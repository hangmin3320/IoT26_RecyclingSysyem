"""
app.py — 통합 허브: 백그라운드 감지 루프 + Flask 대시보드/API (§9, §12).

구조:
  - 단일 프로세스. 백그라운드 스레드 1개가 센서 루프를 돌리고,
    Flask(threaded=True)가 공유 상태(_state, Lock 보호)에서 대시보드/API 를 서빙한다.
  - 감지(capture→classify→store)는 별도 워커 스레드로 오프로드해 센서/LCD 루프가
    추론 중에도 멈추지 않게 한다. busy 플래그로 동시 감지를 1건으로 제한.
  - 모든 하드웨어 호출은 try/except 로 감싸 *_connected 플래그를 갱신하고, 루프 전체도
    바깥 try/except 로 감싸 한 사이클 오류가 스레드를 죽이지 못하게 한다.

추론/카메라 등 무거운 작업은 절대 Flask 요청 핸들러 안에서 하지 않는다(§18).
"""

import logging
import os
import threading
import time
from datetime import datetime

from flask import (
    Flask,
    abort,
    jsonify,
    render_template,
    request,
    send_from_directory,
)

import config
import storage

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(threadName)s] %(name)s: %(message)s",
)
logger = logging.getLogger("app")

app = Flask(__name__)

# ===========================================================================
# 공유 상태
# ===========================================================================
_lock = threading.Lock()
_state = {
    "timestamp": None,
    "sensors": {
        "temperature_c": None,
        "humidity_pct": None,
        "distance_cm": None,
        "dht_connected": False,
        "ultrasonic_connected": False,
        "camera_connected": False,
        "lcd_connected": False,
    },
    "last_detection": None,
    "system_status": "starting",
}

# 감지 동시성 제어
_busy = threading.Lock()         # 감지 처리 중 여부 (non-blocking acquire 로 busy 판정)
_force_trigger = threading.Event()  # MOCK: /api/mock/trigger 가 셋

# 하드웨어 핸들 (백그라운드 스레드에서 초기화)
_cam = None


# ===========================================================================
# 감지 워커 (capture → classify → save → store → update)
# ===========================================================================
def _run_detection(temperature, humidity):
    """단일 감지 처리. _busy 락을 이미 획득한 상태로 호출된다."""
    import cv2

    import classifier

    try:
        # 1) 스냅샷
        try:
            frame = _cam.capture_still()
            with _lock:
                _state["sensors"]["camera_connected"] = True
        except Exception as e:
            logger.error("capture failed: %s", e)
            with _lock:
                _state["sensors"]["camera_connected"] = False
            return

        # 2) 분류
        try:
            label, conf = classifier.classify(frame)
        except Exception as e:
            logger.error("classify failed: %s", e)
            label, conf = "others", 0.0

        # 3) 이미지 저장 captures/<timestamp>_<label>.jpg
        ts = datetime.now()
        fname = f"{ts.strftime('%Y%m%d_%H%M%S')}_{label}.jpg"
        fpath = os.path.join(config.CAPTURES_DIR, fname)
        try:
            os.makedirs(config.CAPTURES_DIR, exist_ok=True)
            cv2.imwrite(fpath, frame)
        except Exception as e:
            logger.error("imwrite failed: %s", e)

        # 4) 저장소 기록 (감지 시에만 SQLite write → SD 카드 보호)
        record = {
            "timestamp": ts.isoformat(timespec="seconds"),
            "label": label,
            "confidence": float(conf),
            "image_path": fpath,
            "temperature_c": temperature,
            "humidity_pct": humidity,
        }
        try:
            storage.add_detection(record)
            storage.increment_count(label)
        except Exception as e:
            logger.error("storage write failed: %s", e)

        # 5) 공유 상태 갱신
        with _lock:
            _state["last_detection"] = {
                "timestamp": record["timestamp"],
                "label": label,
                "confidence": round(float(conf), 4),
                "image": fname,
                "temperature_c": temperature,
                "humidity_pct": humidity,
            }
        logger.info("DETECTION: %s (%.2f) -> %s", label, conf, fname)
    finally:
        # busy 해제
        try:
            _busy.release()
        except RuntimeError:
            pass


# ===========================================================================
# 백그라운드 센서 루프
# ===========================================================================
def _sensor_loop():
    global _cam
    import sensors
    import camera_stream
    import classifier

    # --- 하드웨어 초기화 (각 단계 실패해도 계속) ---
    dht = sensors.DHTSensor()
    ultra = sensors.UltrasonicSensor()
    lcd = sensors.LCDDisplay()
    _cam = camera_stream.CameraStream()

    # 분류기 미리 로드 (첫 감지 지연 방지). 실패해도 감지 시 재시도.
    try:
        classifier.load()
    except Exception as e:
        logger.error("classifier preload failed: %s", e)

    # 라이브 스트림 시작 (MediaMTX 가 떠 있으면 publish; 아니면 모니터가 재시도)
    try:
        _cam.start_stream()
    except Exception as e:
        logger.error("start_stream failed: %s", e)

    with _lock:
        _state["sensors"]["camera_connected"] = bool(getattr(_cam, "available", False))
        _state["system_status"] = "ok"

    previously_in_range = False
    last_trigger_time = 0.0
    last_lcd_update = 0.0
    last_temp = None
    last_hum = None

    logger.info("sensor loop started (poll=%.2fs)", config.SENSOR_POLL_INTERVAL_S)

    while True:
        cycle_start = time.monotonic()
        try:
            # --- DHT ---
            try:
                t, h = dht.read()
                last_temp, last_hum = t, h
                with _lock:
                    _state["sensors"]["temperature_c"] = t
                    _state["sensors"]["humidity_pct"] = h
                    _state["sensors"]["dht_connected"] = True
            except Exception as e:
                with _lock:
                    _state["sensors"]["dht_connected"] = False
                logger.debug("DHT read failed: %s", e)

            # --- 초음파 ---
            distance = None
            try:
                distance = ultra.read_cm()
                with _lock:
                    _state["sensors"]["distance_cm"] = distance
                    _state["sensors"]["ultrasonic_connected"] = True
            except Exception as e:
                with _lock:
                    _state["sensors"]["ultrasonic_connected"] = False
                logger.debug("ultrasonic read failed: %s", e)

            # --- LCD 주기적 갱신 ---
            now = time.monotonic()
            if (now - last_lcd_update) >= config.LCD_UPDATE_INTERVAL_S:
                try:
                    if last_temp is not None and last_hum is not None:
                        line1 = f"T:{last_temp:.0f}C H:{last_hum:.0f}%"
                    else:
                        line1 = "DHT: --"
                    with _lock:
                        last_det = _state["last_detection"]
                    if last_det:
                        line2 = f"L:{last_det['label']}"[: config.LCD_COLS]
                    elif distance is not None:
                        line2 = f"Dist:{distance:.0f}cm"
                    else:
                        line2 = "Recycler ready"
                    lcd.show(line1, line2)
                    with _lock:
                        _state["sensors"]["lcd_connected"] = True
                except Exception as e:
                    with _lock:
                        _state["sensors"]["lcd_connected"] = False
                    logger.debug("LCD update failed: %s", e)
                last_lcd_update = now

            # --- 카메라/스트림 헬스 반영 (캡처 성공 여부와 무관하게 스트림 상태로 갱신) ---
            try:
                with _lock:
                    _state["sensors"]["camera_connected"] = _cam.stream_healthy()
            except Exception:
                pass

            # --- 감지 트리거 (rising edge + cooldown + busy) ---
            mock_fire = _force_trigger.is_set()
            if mock_fire:
                _force_trigger.clear()

            in_range = distance is not None and distance <= config.DETECTION_DISTANCE_CM
            rising_edge = in_range and not previously_in_range
            cooldown_ok = (time.monotonic() - last_trigger_time) >= config.COOLDOWN_SECONDS

            if (rising_edge and cooldown_ok) or mock_fire:
                # busy 가 아니면(=락 획득 성공) 감지 워커 가동
                if _busy.acquire(blocking=False):
                    last_trigger_time = time.monotonic()
                    logger.info(
                        "trigger fired (distance=%s, mock=%s)", distance, mock_fire
                    )
                    threading.Thread(
                        target=_run_detection,
                        args=(last_temp, last_hum),
                        name="detection",
                        daemon=True,
                    ).start()
                else:
                    logger.debug("trigger skipped: busy")

            previously_in_range = in_range

            with _lock:
                _state["timestamp"] = datetime.now().isoformat(timespec="seconds")

        except Exception:
            # 한 사이클 전체 실패해도 루프는 계속
            logger.exception("sensor loop iteration error")
            time.sleep(0.5)

        # 폴링 주기 유지
        elapsed = time.monotonic() - cycle_start
        time.sleep(max(0.0, config.SENSOR_POLL_INTERVAL_S - elapsed))


# ===========================================================================
# Flask 라우트
# ===========================================================================
@app.route("/")
def index():
    return render_template(
        "index.html",
        class_names=config.CLASS_NAMES,
        stream_mode=config.STREAM_MODE,
        hls_port=config.MEDIAMTX_HLS_PORT,
        webrtc_port=config.MEDIAMTX_WEBRTC_PORT,
        stream_path=config.MEDIAMTX_PATH,
        mock_mode=config.MOCK_MODE,
    )


@app.route("/api/status")
def api_status():
    with _lock:
        sensors_snapshot = dict(_state["sensors"])
        last_detection = _state["last_detection"]
        timestamp = _state["timestamp"]
        system_status = _state["system_status"]
    counts = storage.get_counts()
    counts_out = dict(counts)
    counts_out["total"] = sum(counts.values())
    return jsonify(
        {
            "timestamp": timestamp,
            "sensors": sensors_snapshot,
            "last_detection": last_detection,
            "counts": counts_out,
            "system_status": system_status,
        }
    )


@app.route("/api/history")
def api_history():
    limit = request.args.get("limit", default=20, type=int)
    return jsonify({"detections": storage.get_recent(limit)})


@app.route("/captures/<path:filename>")
def captures(filename):
    # send_from_directory 는 safe_join 으로 경로 탈출을 막는다(추가로 basename 강제).
    safe = os.path.basename(filename)
    if safe != filename or not safe:
        abort(404)
    return send_from_directory(config.CAPTURES_DIR, safe)


@app.route("/api/reset", methods=["POST"])
def api_reset():
    data = request.get_json(silent=True) or {}
    if data.get("token") != config.RESET_TOKEN:
        abort(403)
    storage.reset_counts()
    logger.info("counts reset via API")
    return jsonify({"ok": True, "counts": storage.get_counts()})


@app.route("/api/mock/trigger", methods=["POST"])
def api_mock_trigger():
    if not config.MOCK_MODE:
        abort(404)
    _force_trigger.set()
    return jsonify({"ok": True})


@app.route("/api/health")
def api_health():
    return jsonify({"status": "ok"})


# ===========================================================================
# 진입점
# ===========================================================================
def main():
    storage.init_db()
    # 백그라운드 센서 루프 시작
    t = threading.Thread(target=_sensor_loop, name="sensor-loop", daemon=True)
    t.start()
    logger.info(
        "starting Flask on %s:%d (mock=%s)",
        config.FLASK_HOST,
        config.FLASK_PORT,
        config.MOCK_MODE,
    )
    # use_reloader=False: 리로더가 백그라운드 스레드를 두 번 띄우는 것 방지
    app.run(
        host=config.FLASK_HOST,
        port=config.FLASK_PORT,
        threaded=True,
        use_reloader=False,
    )


if __name__ == "__main__":
    main()
