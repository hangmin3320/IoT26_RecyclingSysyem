"""
storage.py — SQLite 영속 저장소 (누적 카운트 + 감지 이력).

§11 스키마를 따른다. GPIO/카메라를 절대 건드리지 않는 순수 로직+IO 모듈.
스레드 안전: 모든 DB 접근을 모듈 내부 Lock 으로 보호하고, 단일 커넥션을
check_same_thread=False 로 공유한다(백그라운드 감지 스레드 + Flask 요청 스레드).

SD 카드 수명을 위해 감지/카운트가 바뀔 때만 기록한다(센서 폴링마다 X) (§17).
"""

import logging
import os
import sqlite3
import threading
from typing import Optional

import config

logger = logging.getLogger(__name__)

# 단일 공유 커넥션 + 락
_lock = threading.Lock()
_conn: Optional[sqlite3.Connection] = None


def _connect() -> sqlite3.Connection:
    """공유 커넥션을 (필요 시) 생성해 반환한다."""
    global _conn
    if _conn is None:
        os.makedirs(os.path.dirname(config.DB_PATH), exist_ok=True)
        _conn = sqlite3.connect(config.DB_PATH, check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        # 동시성/내구성 절충: WAL 모드로 읽기-쓰기 충돌 완화
        _conn.execute("PRAGMA journal_mode=WAL;")
        _conn.execute("PRAGMA synchronous=NORMAL;")
    return _conn


def init_db() -> None:
    """테이블 생성 + CLASS_NAMES 의 모든 클래스 행을 0 으로 시드한다."""
    with _lock:
        conn = _connect()
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS counts (
                class_name TEXT PRIMARY KEY,
                count INTEGER NOT NULL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS detections (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                label TEXT NOT NULL,
                confidence REAL NOT NULL,
                image_path TEXT NOT NULL,
                temperature_c REAL,
                humidity_pct REAL
            );
            """
        )
        # 모든 클래스에 대해 카운트 행 보장 (없으면 0 으로 생성)
        for name in config.CLASS_NAMES:
            conn.execute(
                "INSERT OR IGNORE INTO counts (class_name, count) VALUES (?, 0)",
                (name,),
            )
        conn.commit()
    logger.info("storage initialized at %s (classes=%s)", config.DB_PATH, config.CLASS_NAMES)


def increment_count(label: str) -> None:
    """주어진 클래스의 누적 카운트를 1 증가시킨다."""
    if label not in config.CLASS_NAMES:
        logger.warning("increment_count: unknown label %r -> 'others'", label)
        label = "others"
    with _lock:
        conn = _connect()
        # 시드가 보장되지만 방어적으로 INSERT OR IGNORE 후 UPDATE
        conn.execute(
            "INSERT OR IGNORE INTO counts (class_name, count) VALUES (?, 0)", (label,)
        )
        conn.execute(
            "UPDATE counts SET count = count + 1 WHERE class_name = ?", (label,)
        )
        conn.commit()


def add_detection(record: dict) -> int:
    """
    감지 1건을 이력에 저장한다.

    record 키: timestamp, label, confidence, image_path, temperature_c, humidity_pct
    반환: 새 행의 id
    """
    with _lock:
        conn = _connect()
        cur = conn.execute(
            """
            INSERT INTO detections
                (timestamp, label, confidence, image_path, temperature_c, humidity_pct)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                record.get("timestamp"),
                record.get("label"),
                float(record.get("confidence", 0.0)),
                record.get("image_path", ""),
                record.get("temperature_c"),
                record.get("humidity_pct"),
            ),
        )
        conn.commit()
        return cur.lastrowid


def get_counts() -> dict:
    """{class_name: count} (CLASS_NAMES 순서 보장)."""
    with _lock:
        conn = _connect()
        rows = conn.execute("SELECT class_name, count FROM counts").fetchall()
    by_name = {r["class_name"]: r["count"] for r in rows}
    # CLASS_NAMES 순서대로, 누락 클래스는 0 으로 채워 반환
    return {name: int(by_name.get(name, 0)) for name in config.CLASS_NAMES}


def get_total() -> int:
    """전체 누적 감지 수."""
    with _lock:
        conn = _connect()
        row = conn.execute("SELECT COALESCE(SUM(count), 0) AS total FROM counts").fetchone()
    return int(row["total"]) if row else 0


def get_recent(limit: int = 20) -> list:
    """최근 감지 이력 (최신순) 리스트[dict]."""
    try:
        limit = max(1, min(int(limit), 200))
    except (TypeError, ValueError):
        limit = 20
    with _lock:
        conn = _connect()
        rows = conn.execute(
            """
            SELECT id, timestamp, label, confidence, image_path, temperature_c, humidity_pct
            FROM detections
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    result = []
    for r in rows:
        result.append(
            {
                "id": r["id"],
                "timestamp": r["timestamp"],
                "label": r["label"],
                "confidence": r["confidence"],
                # 대시보드는 파일명만 필요 (/captures/<filename>)
                "image": os.path.basename(r["image_path"]) if r["image_path"] else None,
                "temperature_c": r["temperature_c"],
                "humidity_pct": r["humidity_pct"],
            }
        )
    return result


def reset_counts() -> None:
    """누적 카운트를 모두 0 으로 초기화한다(이력 detections 는 보존)."""
    with _lock:
        conn = _connect()
        conn.execute("UPDATE counts SET count = 0")
        conn.commit()
    logger.info("counts reset to 0")


def close() -> None:
    """커넥션을 닫는다(종료 시)."""
    global _conn
    with _lock:
        if _conn is not None:
            _conn.close()
            _conn = None


# 모듈 단독 실행 시 간단 self-test
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    init_db()
    print("counts:", get_counts())
    increment_count("bottle")
    add_detection(
        {
            "timestamp": "2026-06-15T10:00:00",
            "label": "bottle",
            "confidence": 0.91,
            "image_path": "captures/test_bottle.jpg",
            "temperature_c": 24.1,
            "humidity_pct": 50.8,
        }
    )
    print("counts:", get_counts())
    print("total:", get_total())
    print("recent:", get_recent(5))
