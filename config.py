"""
config.py — 단일 설정 소스 (Single source of truth).

이 프로젝트의 모든 핀 번호, 임계값, 모델 경로, 스트림 URL, CLASS_NAMES 등을
이 파일 한 곳에서만 정의한다. 다른 모듈은 절대 이 값들을 재정의하지 말고
`import config` 또는 `from config import ...` 로 가져다 쓴다.

하드웨어 관련 값(핀/주소)은 "예시 기본값"이며, 실제 배선과 다를 수 있으므로
`scripts/test_*.py` 와 `i2cdetect -y 1` 로 확인 후 필요하면 여기서만 수정한다.

값은 대부분 환경변수로 덮어쓸 수 있다 (systemd / 쉘에서 주입 용이).
"""

import os


def _env_bool(name: str, default: bool) -> bool:
    return os.environ.get(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


# 프로젝트 루트 (다른 모듈이 상대경로를 절대경로로 바꿀 때 사용)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# ---------------------------------------------------------------------------
# 실행 모드
# ---------------------------------------------------------------------------
# MOCK_MODE=True 이면 실제 GPIO/I2C/카메라/모델을 건드리지 않고
# 그럴듯한 가짜 값을 만들어 대시보드 전체 파이프라인을 테스트할 수 있다.
MOCK_MODE = _env_bool("MOCK_MODE", False)

# ---------------------------------------------------------------------------
# GPIO 핀 (BCM 번호)  — Pi 5 에서는 gpiozero + lgpio 핀 팩토리 사용 (§17)
# ---------------------------------------------------------------------------
DHT_PIN = _env_int("DHT_PIN", 4)                 # DHT22 DATA 라인
ULTRASONIC_TRIG_PIN = _env_int("ULTRASONIC_TRIG_PIN", 23)   # HC-SR04 TRIG
ULTRASONIC_ECHO_PIN = _env_int("ULTRASONIC_ECHO_PIN", 24)   # HC-SR04 ECHO (분압 회로 권장)

# DHT 센서 종류: "DHT22" 또는 "DHT11"
DHT_SENSOR_TYPE = os.environ.get("DHT_SENSOR_TYPE", "DHT22").upper()

# ---------------------------------------------------------------------------
# I2C LCD (PCF8574 백팩) — i2cdetect -y 1 로 주소 확인됨: 0x27
# ---------------------------------------------------------------------------
LCD_I2C_ADDR = _env_int("LCD_I2C_ADDR", 0x27)
LCD_I2C_PORT = _env_int("LCD_I2C_PORT", 1)
LCD_COLS = _env_int("LCD_COLS", 16)
LCD_ROWS = _env_int("LCD_ROWS", 2)

# ---------------------------------------------------------------------------
# 감지 트리거 (초음파)
# ---------------------------------------------------------------------------
DETECTION_DISTANCE_CM = _env_float("DETECTION_DISTANCE_CM", 15.0)  # 이하이면 "물체 있음"
COOLDOWN_SECONDS = _env_float("COOLDOWN_SECONDS", 5.0)             # 연속 트리거 최소 간격
SENSOR_POLL_INTERVAL_S = _env_float("SENSOR_POLL_INTERVAL_S", 0.5) # 초음파/DHT 폴링 주기
LCD_UPDATE_INTERVAL_S = _env_float("LCD_UPDATE_INTERVAL_S", 2.0)   # LCD 갱신 주기
DHT_READ_INTERVAL_S = _env_float("DHT_READ_INTERVAL_S", 2.0)       # DHT 최소 읽기 간격(센서 제약)
ULTRASONIC_MAX_DISTANCE_CM = _env_float("ULTRASONIC_MAX_DISTANCE_CM", 200.0)  # 측정 상한

# ---------------------------------------------------------------------------
# 카메라 / 스트리밍 (Pi 5는 H264 하드웨어 인코더가 없어 ffmpeg libx264 소프트웨어 인코딩)
# ---------------------------------------------------------------------------
# 라이브 스트림 = 분류 스냅샷 소스 (rpicam-vid → ffmpeg → RTSP + 1fps JPEG 스냅샷).
# 분류는 이 스냅샷 프레임을 사용한다(별도 고해상도 still 스트림 없음).
# Pi 5는 H264 소프트웨어 인코딩이라 해상도가 CPU 부하에 직결된다. 초음파(소프트웨어 타이밍)
# 안정성을 위해 640x480 기본. 더 선명한 화면이 필요하면 (1280,720)으로 올릴 수 있다.
STREAM_RESOLUTION = (640, 480)    # 라이브 피드 & 스냅샷 해상도
STILL_RESOLUTION = STREAM_RESOLUTION  # (호환용 별칭)
STREAM_FPS = _env_int("STREAM_FPS", 15)
CAPTURES_DIR = os.path.join(BASE_DIR, "captures")

# ffmpeg 가 1fps 로 갱신하는 최신 프레임 JPEG. capture_still() 이 이 파일을 읽는다.
LIVE_SNAPSHOT_PATH = os.path.join(BASE_DIR, "data", "live_snapshot.jpg")
# 스냅샷이 이 시간(초) 이상 갱신 안 되면 스트림이 멈춘 것으로 보고 파이프라인 재시작(워치독).
STREAM_STALL_TIMEOUT_S = _env_float("STREAM_STALL_TIMEOUT_S", 6.0)

# ---------------------------------------------------------------------------
# 분류 (Classification)
# ---------------------------------------------------------------------------
# 이 리스트가 전 시스템의 단일 진실 소스. 순서/이름을 어디서도 바꾸지 말 것.
CLASS_NAMES = ["bottle", "can", "paper", "glass", "others"]

# 모델 경로: env(RECYCLER_MODEL_PATH) > 기본값. classifier.py 는 절대 경로를 하드코딩하지 않는다.
# 현재 모델: iotterm.ipynb 로 직접 학습한 YOLO11n-cls 분류기(garbage-classification-v2, 10클래스).
#   원본 클래스: battery/biological/cardboard/clothes/glass/metal/paper/plastic/shoes/trash
#   학습 결과 best.pt 를 models/garbage_yolo11n_cls.pt 로 복사해 자동 로드된다.
#   - 다른 위치면 RECYCLER_MODEL_PATH=/path/to/best.pt 로 지정.
# 모델 원본 클래스명이 우리 5클래스와 다르므로 MODEL_LABEL_MAP 으로 정규화한다(아래).
MODEL_PATH = os.environ.get(
    "RECYCLER_MODEL_PATH", os.path.join(BASE_DIR, "models", "garbage_yolo11n_cls.pt")
)
# 위 경로에 모델이 없을 때만 쓰는 ultralytics 폴백(앱이 죽지 않게 하는 안전망; COCO yolov8n).
MODEL_FALLBACK = os.environ.get("RECYCLER_MODEL_FALLBACK", "yolov8n.pt")

CONF_THRESHOLD = _env_float("CONF_THRESHOLD", 0.30)  # 이하이면 -> "others"

# 라벨 1개 선택 정책:
#  - True  (기본): 검출된 박스 중 '재활용품'(others 가 아닌 bottle/can/paper/glass)을
#                 'others/배경'보다 우선해서 고른다. 재활용품끼리는 confidence 최댓값.
#                 → 배경 박스가 1등을 먹어 실제 물체를 놓치는 문제를 막는다.
#  - False (기존): 전역 최고 confidence 박스 1개를 그대로 사용.
PREFER_RECYCLABLE = _env_bool("PREFER_RECYCLABLE", True)

# 채점 우선 클래스(§3): 한 프레임에서 이 클래스들이 임계값 이상 잡히면 glass 보다 우선 선택한다.
# (현재 모델이 불투명 캔/플라스틱병을 glass 로 자주 오인하므로, metal/plastic 을 약하게라도
#  잡았다면 can/bottle 로 살려주기 위함.) 이 안에서는 confidence 최댓값.
PRIMARY_CLASSES = ["bottle", "can", "paper"]

# 추론 입력 크기: 학습 해상도(iotterm.ipynb 의 imgsz=256)와 맞춰야 정확도가 가장 높다.
# (실측: 256 에서 can=metal 1.00, paper=cardboard 0.92 로 깔끔. 640 으로 키우면
#  분류기 정확도가 오히려 떨어졌다 — 학습 시 본 입력 크기/크롭과 달라지기 때문.)
INFER_IMGSZ = _env_int("INFER_IMGSZ", 256)
TORCH_THREADS = _env_int("TORCH_THREADS", 3)  # 단발 추론이라 코어를 더 써서 지연 단축

# 모델 원본 라벨 -> 프로젝트 5클래스 정규화 매핑 (§5).
# 키는 소문자로 비교한다. 매핑에 없거나 CONF_THRESHOLD 미만이면 "others".
# 직접 학습한 모델의 클래스명에 맞춰 여기에 항목을 추가/수정하면 된다.
# (학습 클래스를 bottle/can/paper/glass 로 그대로 쓰면 아래 동일-이름 항목으로 자동 처리된다.)
MODEL_LABEL_MAP = {
    # bottle 류 (플라스틱/페트병)
    "bottle": "bottle",
    "plastic": "bottle",
    "pet": "bottle",
    "plastic bottle": "bottle",
    "water bottle": "bottle",
    # can 류 (금속캔)
    "can": "can",
    "metal": "can",
    "tin": "can",
    "aluminium": "can",
    "aluminum": "can",
    "soda can": "can",
    # paper 류 (종이/박스)
    "paper": "paper",
    "cardboard": "paper",
    "carton": "paper",
    "newspaper": "paper",
    # glass 류 (유리) — 색상별 유리 클래스명까지 흡수
    "glass": "glass",
    "glass bottle": "glass",
    "brown-glass": "glass",
    "green-glass": "glass",
    "white-glass": "glass",
    # 그 외는 모두 others 로 폴백 (명시하지 않아도 기본이 others)
    "trash": "others",
    "garbage": "others",
    "battery": "others",
    "biological": "others",
    "clothes": "others",
    "shoes": "others",
}

# ---------------------------------------------------------------------------
# 저장소 (SQLite)
# ---------------------------------------------------------------------------
DB_PATH = os.path.join(BASE_DIR, "data", "detections.db")

# ---------------------------------------------------------------------------
# MediaMTX 스트리밍
# ---------------------------------------------------------------------------
# Pi 가 MediaMTX 로 영상을 publish 하는 로컬 RTSP 주소
MEDIAMTX_RTSP_URL = os.environ.get("MEDIAMTX_RTSP_URL", "rtsp://127.0.0.1:8554/cam")
MEDIAMTX_PATH = os.environ.get("MEDIAMTX_PATH", "cam")  # 스트림 경로 이름

# 브라우저가 영상을 재생할 때 쓰는 포트. 실제 host(IP)는 대시보드 접속 host 에서
# 클라이언트(app.js)가 동적으로 가져오므로 여기엔 포트만 둔다.
MEDIAMTX_HLS_PORT = _env_int("MEDIAMTX_HLS_PORT", 8888)
MEDIAMTX_WEBRTC_PORT = _env_int("MEDIAMTX_WEBRTC_PORT", 8889)

# 참고/단독 실행용 전체 URL (실제 IP 로 치환해서 사용). 대시보드는 위 포트만 사용.
_PI_IP = os.environ.get("PI_IP", "127.0.0.1")   # 원격 접속 시 PI_IP 환경변수로 실제 IP 지정
MEDIAMTX_HLS_URL = os.environ.get(
    "MEDIAMTX_HLS_URL", f"http://{_PI_IP}:{MEDIAMTX_HLS_PORT}/{MEDIAMTX_PATH}/index.m3u8"
)
MEDIAMTX_WEBRTC_URL = os.environ.get(
    "MEDIAMTX_WEBRTC_URL", f"http://{_PI_IP}:{MEDIAMTX_WEBRTC_PORT}/{MEDIAMTX_PATH}"
)
# auto | hls | webrtc  (대시보드 재생 방식)
STREAM_MODE = os.environ.get("STREAM_MODE", "auto").lower()

# 라이브 스트림 활성화 여부 (스트림 없이 센서/분류만 돌릴 때 False)
ENABLE_STREAM = _env_bool("ENABLE_STREAM", True)

# ---------------------------------------------------------------------------
# Flask / 안전장치
# ---------------------------------------------------------------------------
FLASK_HOST = os.environ.get("FLASK_HOST", "0.0.0.0")
FLASK_PORT = _env_int("FLASK_PORT", 5000)
# POST /api/reset 에 반드시 필요한 토큰. 운영 시 env 로 바꿀 것.
RESET_TOKEN = os.environ.get("RESET_TOKEN", "change-me")
