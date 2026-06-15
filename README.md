# ♻️ AIoT Smart Recycling System

Raspberry Pi 5 엣지 디바이스 기반 **스마트 분리수거 시스템**.
온습도(DHT) → I²C LCD 표시, 초음파(HC-SR04)로 물체 감지 → 카메라 스냅샷 →
AI 분류(**bottle / can / paper / glass / others**) → SQLite 기록/집계,
그리고 **MediaMTX** 라이브 스트림 + **Flask 웹 대시보드**로 실시간 모니터링.

> Gachon Univ. · Introduction to IoT (Prof. Jaehyuk Choi, Spring 2026) · Team Project.
> 제출: **2026-06-16 18:00** / 데모: **2026-06-17**

---

## 1. 시스템 아키텍처

```
                      ┌──────────────────────── Raspberry Pi 5 ────────────────────────┐
                      │                                                                 │
   DHT22 ──GPIO4──────┤ sensors.py ─┐                                                   │
   HC-SR04 ─GPIO23/24─┤             │   ┌── app.py (백그라운드 감지 스레드) ──────────┐  │
   I²C LCD ─0x27──────┤ (온습도/거리/LCD)│   read DHT → LCD/상태                       │  │
                      │             └──▶│   read 초음파 → 트리거(rising edge+cooldown)│  │
   Pi Camera ─CSI─────┤ camera_stream.py│   트리거 시: capture→classify→save→store    │  │
   (ov5647)           │  rpicam-vid│ffmpeg│─▶ classifier.py (ultralytics YOLOv8n)      │  │
                      │   ├─ H264 → RTSP ─┐│   storage.py (SQLite: counts + history)    │  │
                      │   └─ 1fps JPEG ───┼┼─▶ capture_still() 이 최신 스냅샷을 읽음     │  │
                      │      snapshot     ▼▼                          Flask (threaded)  │  │
                      │              MediaMTX ◀── RTSP:8554          /api/status 등 ───┼──┼─▶ 브라우저
                      │            HLS:8888 / WebRTC:8889 ───────────────────────────┘  │   대시보드
                      └─────────────────────────────────────────────────────────────────┘
```

- **단일 프로세스 + 백그라운드 스레드**: `app.py`가 감지 루프 스레드 1개를 돌리고,
  Flask(`threaded=True`)가 공유 상태(Lock 보호)에서 대시보드/API를 서빙한다.
- 무거운 감지(capture→classify)는 **별도 워커 스레드**로 오프로드 → 센서/LCD 루프가 멈추지 않음.
- 모든 하드웨어 호출은 try/except로 감싸 한 장치가 죽어도 시스템 전체는 계속 동작(graceful degradation).
- **스트리밍**: Pi 5는 H264 하드웨어 인코더가 없어 `rpicam-vid`(원본 프레임) → `ffmpeg`(libx264
  소프트웨어 인코딩) → MediaMTX RTSP 로 publish 한다. ffmpeg 가 동시에 1fps JPEG 스냅샷을 갱신하고,
  분류용 `capture_still()` 은 카메라를 다시 열지 않고 그 최신 스냅샷을 읽는다(핸들 충돌 없음).
  스냅샷 mtime 이 멈추면 워치독 스레드가 파이프라인을 자동 재시작한다.

---

## 2. 하드웨어 (BOM & 핀맵)

| 부품 | 모델 | 인터페이스 | 핀/주소 (BCM) |
|---|---|---|---|
| 컴퓨트 | **Raspberry Pi 5 (4GB)** | — | — |
| 온습도 | DHT22 (또는 DHT11) | 1-wire GPIO | DATA = **GPIO4** |
| 초음파 거리 | HC-SR04 | GPIO | TRIG = **GPIO23**, ECHO = **GPIO24** (ECHO는 5V→3.3V 분압 권장) |
| 디스플레이 | 16x2 I²C LCD (PCF8574) | I²C bus 1 | addr **0x27** (`i2cdetect -y 1`로 확인) |
| 카메라 | Pi Camera (CSI, ov5647) | CSI / Picamera2 | — |

> 모든 핀/주소는 `config.py`의 예시 기본값이다. 실제 배선과 다르면 **`config.py`에서만** 수정한다.
> Pi 5는 `gpiozero` + **`lgpio`** 핀 팩토리만 사용한다 (`RPi.GPIO`는 Pi 5에서 동작 안 함, RP1 컨트롤러).

---

## 3. 설치 (Setup)

### 3.1 OS 준비 (1회)
```bash
sudo raspi-config        # Interface: I2C 활성화, Camera 활성화 → 재부팅
i2cdetect -y 1           # LCD 주소 확인 (보통 0x27)
rpicam-hello --timeout 2000   # 카메라 확인
```

### 3.2 시스템 패키지 (apt) — lgpio/smbus2/카메라 도구는 apt로
```bash
sudo apt update
sudo apt install -y python3-lgpio python3-gpiozero i2c-tools python3-smbus2 \
                    ffmpeg rpicam-apps
```
> 스트리밍은 `rpicam-vid`(rpicam-apps, Pi OS 기본 포함) + `ffmpeg`(libx264 소프트웨어 인코딩)을
> 사용한다. Pi 5에는 H264 하드웨어 인코더가 없다. `rpicam-jpeg`(스냅샷 폴백)도 rpicam-apps 에 포함.

### 3.3 파이썬 의존성
이 Pi에는 이미 `torch 2.12.0+cpu`, `ultralytics 8.4.66`, `opencv 4.13`,
`RPLCD`, `adafruit-circuitpython-dht`가 설치돼 있다(확인됨). 부족할 때만 설치:
```bash
# (선택) venv — 반드시 시스템 패키지 상속
python3 -m venv --system-site-packages .venv
source .venv/bin/activate
pip install -r requirements.txt   # 이미 있으면 재설치 불필요 (특히 torch/ultralytics)
```

### 3.4 MediaMTX (ARM64 바이너리)
이미 `mediamtx/`에 v1.19.1 linux_arm64 바이너리와 `mediamtx.yml`이 포함돼 있다. 직접 받으려면:
```bash
cd mediamtx
curl -LO https://github.com/bluenviron/mediamtx/releases/download/v1.19.1/mediamtx_v1.19.1_linux_arm64.tar.gz
tar xzf mediamtx_v1.19.1_linux_arm64.tar.gz mediamtx   # 바이너리만 추출 (yml은 우리 것 유지)
```

### 3.5 AI 모델 — 직접 학습한 YOLO11n-cls 분류기 사용
이 프로젝트는 **직접 학습한 YOLO11n 분류(classification) 모델**을 쓴다. `iotterm.ipynb` 로
Kaggle `garbage-classification-v2`(10클래스)를 학습했고, 결과 가중치(`best.pt`)를
**`models/garbage_yolo11n_cls.pt`** 에 두면 자동 로드된다. (다른 위치면
`RECYCLER_MODEL_PATH=/path/to/best.pt` 로 지정.)

```bash
# ultralytics 로 학습 (iotterm.ipynb 와 동일) — imgsz=256 분류 학습
yolo classify train model=yolo11n-cls.pt data=/path/to/garbage_split epochs=50 imgsz=256

# 학습 결과(best.pt)를 모델 경로로 복사
cp runs/classify/.../weights/best.pt models/garbage_yolo11n_cls.pt
```

**클래스 처리** — 모델 원본 10클래스 → 프로젝트 5클래스 매핑은 `config.MODEL_LABEL_MAP` 가 담당:

| 모델 원본 라벨 | 프로젝트 라벨 |
|---|---|
| `plastic` | `bottle` |
| `metal` | `can` |
| `paper`, `cardboard` | `paper` |
| `glass` | `glass` |
| `battery`, `biological`, `clothes`, `shoes`, `trash` | `others` |

- `CLASS_NAMES`(대시보드/DB 표시 5종)는 그대로 둔다. 라벨 정규화는 `MODEL_LABEL_MAP` 가 담당.
- 추론 입력 크기는 학습 해상도와 맞춰 `INFER_IMGSZ=256` 으로 둔다(640 으로 키우면 분류 정확도 하락).

> `models/garbage_yolo11n_cls.pt` 가 아직 없으면 앱은 죽지 않고 ultralytics 기본 `yolov8n.pt`(COCO)로
> 폴백한다(데모 안전망, `bottle` 정도만 인식). 학습 모델을 넣으면 그걸 우선 사용.

**검출 시각화(디버깅):** 학습한 모델이 무엇을 검출하는지 박스로 직접 확인:
```bash
python scripts/draw_boxes.py --image captures/<파일>.jpg   # 한 장
python scripts/draw_boxes.py --all                          # captures/ 전체 -> captures/boxed/
```

---

## 4. 실행 (Run)

```bash
# 1) MediaMTX (영상 서버) — 별도 터미널
./mediamtx/mediamtx ./mediamtx/mediamtx.yml

# 2) 메인 앱 (Flask 대시보드 + 감지 루프)
RESET_TOKEN=your-secret python3 app.py
```
브라우저에서 **`http://<pi-ip>:5000`** 접속 → 라이브 영상 + 센서/감지/카운트 패널.

### MOCK 모드 (하드웨어 없이 대시보드/파이프라인 테스트)
```bash
MOCK_MODE=True python3 app.py
```
- 센서/카메라/모델을 모두 가짜로 대체. 대시보드에 **"Test Detect"** 버튼이 나타나
  `POST /api/mock/trigger`로 전체 감지 파이프라인을 강제 실행할 수 있다.

---

## 5. 하드웨어 검증 스크립트

```bash
python scripts/test_sensors.py            # DHT + 초음파 라이브 측정
python scripts/test_sensors.py --scan-dht # DHT가 안 잡힐 때 후보 GPIO 핀 스캔
python scripts/test_lcd.py                # LCD 출력 테스트
python scripts/test_camera.py             # 스냅샷 1장
python scripts/test_camera.py --stream 15 # 15초 MediaMTX 스트림 테스트
python scripts/test_inference.py --image samples/can.jpg --raw   # 모델 분류 + 원본 검출
python scripts/test_inference.py --camera # 카메라로 찍어 즉시 분류
```

---

## 6. 자동 실행 (systemd)

```bash
sudo cp systemd/mediamtx.service /etc/systemd/system/
sudo cp systemd/smart-recycler.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now mediamtx.service
sudo systemctl enable --now smart-recycler.service
sudo systemctl status smart-recycler.service
journalctl -u smart-recycler -f      # 로그 확인
```
> `smart-recycler.service`는 기본적으로 `/usr/bin/python3`를 사용한다.
> venv를 쓰면 `ExecStart`를 `/home/pi/recycle/.venv/bin/python`으로 바꾼다.
> 토큰/모드 등은 서비스 파일의 `Environment=`로 주입한다.

---

## 7. 웹 API

| Endpoint | Method | 설명 |
|---|---|---|
| `/` | GET | 대시보드 (HTML) |
| `/api/status` | GET | 현재 센서/최근 감지/누적 카운트/시스템 상태 (JSON) |
| `/api/history?limit=N` | GET | 최근 감지 이력 |
| `/captures/<filename>` | GET | 감지 스냅샷 이미지 (경로 탈출 차단됨) |
| `/api/reset` | POST | 누적 카운트 초기화 — `{"token": "<RESET_TOKEN>"}` 필요, 불일치 시 403 |
| `/api/mock/trigger` | POST | 감지 강제 실행 (**MOCK_MODE에서만**, 아니면 404) |
| `/api/health` | GET | 헬스체크 |

`/api/status` 예시는 [config 섹션](#8-주요-설정-configpy)의 형식을 따른다.

---

## 8. 주요 설정 (`config.py`)

모든 핀/임계값/경로/URL/`CLASS_NAMES`의 **단일 소스**. 대부분 환경변수로 덮어쓸 수 있다.

| 항목 | 기본값 | 환경변수 |
|---|---|---|
| 감지 거리 임계 | `15 cm` | `DETECTION_DISTANCE_CM` |
| 쿨다운 | `5 s` | `COOLDOWN_SECONDS` |
| 폴링 주기 | `0.5 s` | `SENSOR_POLL_INTERVAL_S` |
| 분류 임계 | `0.30` | `CONF_THRESHOLD` |
| 재활용품 우선 선택 | `True` | `PREFER_RECYCLABLE` |
| 채점 우선 클래스 | `bottle,can,paper` | `PRIMARY_CLASSES` |
| 추론 입력 크기 | `256` | `INFER_IMGSZ` |
| torch 스레드 | `3` | `TORCH_THREADS` |
| 모델 경로 | `models/garbage_yolo11n_cls.pt` | `RECYCLER_MODEL_PATH` |
| 리셋 토큰 | `change-me` | `RESET_TOKEN` |
| 스트림 방식 | `auto`(HLS) | `STREAM_MODE` (`hls`/`webrtc`) |
| MOCK 모드 | `False` | `MOCK_MODE` |

`CLASS_NAMES = ["bottle", "can", "paper", "glass", "others"]` — 절대 다른 곳에서 재정의 금지.

---

## 9. AI 모델 & 성능 메모

- 모델: **직접 학습한 YOLO11n-cls 분류 모델**(`models/garbage_yolo11n_cls.pt`, `iotterm.ipynb`).
  Pi 5 CPU 단일샷 추론. 원본 10클래스를 `MODEL_LABEL_MAP` 으로 프로젝트 5클래스에 정규화한다.
  - 오프더셸프 trash 모델들은 빨간 캔/불투명 병을 glass 로 오인하는 한계가 있어, 데모 환경과
    유사한 데이터로 **직접 학습**하는 것이 인식률에 가장 효과적이다.
  - 단발 이벤트 추론(쿨다운 5 s)이라 추론 지연 수 초는 문제없다. `imgsz=256`(학습 해상도와 일치) 권장.
- 정확도 튜닝: `CONF_THRESHOLD`, `MODEL_LABEL_MAP` 조정. 학습 데이터(각 클래스 이미지 수, 배경
  다양성)가 가장 큰 변수. 검출 결과는 `scripts/draw_boxes.py` 로 시각 확인하며 데이터/임계값을 조정.
- 모델 교체: `RECYCLER_MODEL_PATH` 만 바꾸면 된다(코드 수정 불필요).
- **라벨 선택 정책 `PREFER_RECYCLABLE`(기본 True)**: 한 프레임에서 검출된 여러 박스 중
  '재활용품'(bottle/can/paper/glass)을 'others/배경'보다 우선 선택하고, 재활용품끼리는
  confidence 최댓값을 고른다. 배경 오검출 박스가 1등을 먹어 실제 물체를 놓치는 문제를 막는다.
  단, 재활용품이 `CONF_THRESHOLD` 미만으로만 잡히면 여전히 others 이므로, 작게/멀리 찍히는
  데모라면 `CONF_THRESHOLD`도 함께 낮추는 것을 권장(예: 0.30).

---

## 10. 트러블슈팅

**DHT(온습도)가 안 읽힘 / `dht_connected: false`**
- Pi 5의 bit-bang DHT는 타이밍이 까다롭다(`adafruit_dht`가 "sensor not found"를 자주 냄).
- 점검: `python scripts/test_sensors.py --scan-dht`로 응답 핀 확인 → `config.DHT_PIN` 수정.
- 배선(DATA/VCC/GND)과 풀업 저항(10kΩ) 확인.
- **가장 안정적인 방법(권장): 커널 dht11 오버레이.** `/boot/firmware/config.txt`에
  `dtoverlay=dht11,gpiopin=4` 추가 후 재부팅하면 `/sys/bus/iio/...`로 노출되고,
  `sensors.py`가 이를 **자동 감지해 우선 사용**한다(코드 수정 불필요).
- DHT가 없어도 시스템은 정상 동작하며, 대시보드에 온습도만 `--`로 표시된다.

**초음파가 항상 200cm("no echo") / `ultrasonic_connected: false`**
- 측정 범위 밖(빈 공간/천장)을 보면 정상적으로 max(200cm)가 나온다.
- 물체를 15cm 이내에 두면 값이 떨어진다. ECHO 5V→3.3V 분압 배선 확인.
- HC-SR04는 gpiozero **소프트웨어 타이밍**(Pi 5는 HW PWM 폴백)이라 **CPU 부하가 높으면 읽기가
  불안정**해질 수 있다. 스트림 해상도(`STREAM_RESOLUTION`)를 낮추거나(기본 640x480) 불필요한
  부하를 줄이면 안정화된다. 실패해도 앱은 죽지 않고 거리만 `--`로 표시(graceful degradation).

**LCD가 안 보임**: `i2cdetect -y 1`로 주소 확인(0x27/0x3f) → `config.LCD_I2C_ADDR` 수정, 콘트라스트 가변저항 조정.

**스트림이 "대기 중…"에서 안 넘어감**
- MediaMTX가 떠 있는지 확인(`./mediamtx/mediamtx ...` 또는 systemd). 방화벽에서 8888/8889 허용.
- 스트림은 `rpicam-vid → ffmpeg → RTSP` 파이프라인이며, 끊기면 워치독이 자동 재시작한다
  (스냅샷 mtime 기준). `rpicam-apps`/`ffmpeg` 설치 확인. 저지연이 필요하면 `STREAM_MODE=webrtc`.
- `data/live_snapshot.jpg` 가 1초마다 갱신되는지 보면 파이프라인 동작 여부를 알 수 있다.

**메모리/OOM**: `TORCH_THREADS`/`INFER_IMGSZ`/`STREAM_RESOLUTION`을 낮춘다. 4GB Pi에서 앱 단독
실행 시 여유 충분(추론은 이벤트당 1회). 다른 무거운 프로세스와 함께 돌리면 OOM 위험.

---

## 11. 프로젝트 구조

```
config.py          # 단일 설정 소스 (핀/임계/모델/URL/CLASS_NAMES)
app.py             # Flask + 백그라운드 감지 루프 + 모든 라우트
sensors.py         # DHT / HC-SR04 / I²C LCD (+ mock, graceful degradation)
classifier.py      # ultralytics 모델 로드, classify()->(label, conf)
camera_stream.py   # rpicam-vid→ffmpeg→MediaMTX RTSP + 1fps JPEG 스냅샷, 워치독 재시작
storage.py         # SQLite: counts + detections (thread-safe)
templates/index.html, static/{style.css,app.js}   # 대시보드
scripts/test_*.py  # 하드웨어 검증
mediamtx/          # MediaMTX 바이너리 + mediamtx.yml
systemd/           # 자동 실행 유닛
models/            # 가중치
samples/           # 테스트 이미지 (bottle/can/paper)
```
