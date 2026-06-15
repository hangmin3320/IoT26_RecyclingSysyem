"""
sensors.py — 하드웨어 센서/표시장치 모듈.

담당 하드웨어 (§4, §7 모듈 경계 규칙: GPIO/I2C 는 이 파일과 camera_stream.py 에만):
  - DHT22/DHT11  온습도 (1-wire GPIO, adafruit_dht)
  - HC-SR04      초음파 거리 (gpiozero DistanceSensor + lgpio 핀 팩토리)
  - I2C LCD      PCF8574 16x2 (RPLCD)

설계 원칙:
  - 각 센서는 클래스로 캡슐화. read 계열 메서드는 하드웨어 실패 시 예외를 올리고,
    app.py 의 루프가 try/except 로 잡아 *_connected 플래그를 내린다 (§9).
  - MOCK_MODE 이면 GPIO/I2C 를 전혀 건드리지 않고 그럴듯한 값을 생성한다.
  - print 금지, logging 사용 (§14).

Pi 5 주의 (§17): gpiozero 는 lgpio 핀 팩토리를 강제한다. RPi.GPIO 사용 금지.
"""

import logging
import os
import random
import time
from typing import Optional, Tuple

import config

logger = logging.getLogger(__name__)

# Pi 5: lgpio 핀 팩토리를 명시적으로 강제 (gpiozero import 보다 먼저).
os.environ.setdefault("GPIOZERO_PIN_FACTORY", "lgpio")


# ===========================================================================
# DHT22 / DHT11 온습도
# ===========================================================================
class DHTSensor:
    """
    DHT 온습도 센서. read() -> (temperature_c, humidity_pct).

    백엔드 (자동 선택):
      1) iio       — 커널 dht11 디바이스트리 오버레이가 로드된 경우 (/sys/bus/iio).
                     Pi 5 에서 가장 안정적인 방법 (§17). 오버레이 활성화 방법은 README 참고.
      2) adafruit  — adafruit_dht bit-bang. Pi 5 에서 타이밍이 불안정할 수 있음(§17).

    안정성 장치:
      - DHT 는 ~2s 간격으로만 새로 읽을 수 있으므로 DHT_READ_INTERVAL_S 로 스로틀링하고
        그 사이에는 마지막 양호값을 돌려준다.
      - 일시적 체크섬 오류는 최근 양호값으로 흡수(연속 실패가 tolerance 를 넘기 전까지).
        연속 실패가 누적되면(센서 미연결/고장) 예외를 올려 app.py 가 dht_connected=False 로 표시.
    """

    _FAIL_TOLERANCE = 3  # 이만큼 연속 실패하면 '끊김'으로 간주

    def __init__(self) -> None:
        self.mock = config.MOCK_MODE
        self._device = None
        self._backend = "mock" if self.mock else None
        self._iio_path: Optional[str] = None
        self._last_good: Tuple[Optional[float], Optional[float]] = (None, None)
        self._last_attempt = 0.0
        self._consec_fail = 0
        if not self.mock:
            self._select_backend()

    # --- 백엔드 선택 ---
    def _select_backend(self) -> None:
        iio = self._find_iio_device()
        if iio:
            self._backend = "iio"
            self._iio_path = iio
            logger.info("DHT backend: kernel iio (%s)", iio)
        else:
            self._backend = "adafruit"
            logger.info(
                "DHT backend: adafruit bit-bang on GPIO%d (%s). "
                "Pi 5 timing can be flaky; consider the dht11 overlay (see README).",
                config.DHT_PIN,
                config.DHT_SENSOR_TYPE,
            )

    @staticmethod
    def _find_iio_device() -> Optional[str]:
        """dht11 오버레이가 만든 iio 디바이스 경로를 찾는다(없으면 None)."""
        base = "/sys/bus/iio/devices"
        if not os.path.isdir(base):
            return None
        for name in sorted(os.listdir(base)):
            path = os.path.join(base, name)
            if os.path.exists(os.path.join(path, "in_temp_input")) and os.path.exists(
                os.path.join(path, "in_humidityrelative_input")
            ):
                return path
        return None

    def _init_adafruit(self) -> None:
        import board
        import adafruit_dht

        pin = getattr(board, f"D{config.DHT_PIN}")
        cls = adafruit_dht.DHT11 if config.DHT_SENSOR_TYPE == "DHT11" else adafruit_dht.DHT22
        self._device = cls(pin, use_pulseio=False)

    # --- 1회 실제 읽기 ---
    def _read_once(self) -> Tuple[float, float]:
        if self._backend == "iio":
            with open(os.path.join(self._iio_path, "in_temp_input")) as f:
                t = float(f.read().strip()) / 1000.0
            with open(os.path.join(self._iio_path, "in_humidityrelative_input")) as f:
                h = float(f.read().strip()) / 1000.0
            return round(t, 1), round(h, 1)

        # adafruit
        if self._device is None:
            self._init_adafruit()
        t = self._device.temperature
        h = self._device.humidity
        if t is None or h is None:
            raise RuntimeError("DHT returned None")
        return round(float(t), 1), round(float(h), 1)

    def read(self) -> Tuple[float, float]:
        """(temperature_c, humidity_pct) 반환. 끊김 상태면 예외."""
        if self.mock:
            t = round(random.uniform(22.0, 28.0), 1)
            h = round(random.uniform(40.0, 60.0), 1)
            self._last_good = (t, h)
            return t, h

        now = time.monotonic()
        # 스로틀: 최근에 시도했으면 마지막 양호값 재사용
        if (now - self._last_attempt) < config.DHT_READ_INTERVAL_S:
            if self._last_good[0] is not None:
                return self._last_good  # type: ignore[return-value]
        self._last_attempt = now

        try:
            t, h = self._read_once()
            self._last_good = (t, h)
            self._consec_fail = 0
            return t, h
        except Exception as e:
            self._consec_fail += 1
            # adafruit 백엔드는 디바이스를 재생성하면 회복되는 경우가 있음
            if self._backend == "adafruit":
                self._safe_deinit()
            # 일시적 실패는 최근 양호값으로 흡수
            if self._last_good[0] is not None and self._consec_fail <= self._FAIL_TOLERANCE:
                return self._last_good  # type: ignore[return-value]
            raise RuntimeError(
                f"DHT read failed ({self._backend}, {self._consec_fail} consecutive): {e}"
            )

    def _safe_deinit(self) -> None:
        try:
            if self._device is not None:
                self._device.exit()
        except Exception:
            pass
        self._device = None

    def close(self) -> None:
        if not self.mock:
            self._safe_deinit()


# ===========================================================================
# HC-SR04 초음파 거리
# ===========================================================================
class UltrasonicSensor:
    """HC-SR04 초음파 거리 센서. read_cm() -> 거리(cm)."""

    def __init__(self) -> None:
        self.mock = config.MOCK_MODE
        self._sensor = None
        # mock 시 물체가 가끔 다가오는 상태를 흉내내기 위한 내부 상태
        self._mock_force_near = False
        if not self.mock:
            self._init_device()

    def _init_device(self) -> None:
        import warnings as _w

        from gpiozero import DistanceSensor

        # Pi 5 에서 흔한(그리고 정상인) 경고 두 종을 억제해 로그 스팸을 막는다:
        #  - PWMSoftwareFallback: 소프트웨어 PWM 사용 안내
        #  - DistanceSensorNoEcho: 측정 범위 밖이면 매 폴링마다 발생
        try:
            from gpiozero.exc import DistanceSensorNoEcho, PWMSoftwareFallback

            _w.filterwarnings("ignore", category=PWMSoftwareFallback)
            _w.filterwarnings("ignore", category=DistanceSensorNoEcho)
        except Exception:
            pass

        max_m = config.ULTRASONIC_MAX_DISTANCE_CM / 100.0
        self._sensor = DistanceSensor(
            echo=config.ULTRASONIC_ECHO_PIN,
            trigger=config.ULTRASONIC_TRIG_PIN,
            max_distance=max_m,
        )
        logger.info(
            "Ultrasonic initialized: TRIG=GPIO%d ECHO=GPIO%d (max %.0fcm)",
            config.ULTRASONIC_TRIG_PIN,
            config.ULTRASONIC_ECHO_PIN,
            config.ULTRASONIC_MAX_DISTANCE_CM,
        )

    def read_cm(self) -> float:
        """현재 거리(cm) 반환. 실패 시 예외."""
        if self.mock:
            if self._mock_force_near:
                self._mock_force_near = False
                return round(random.uniform(5.0, config.DETECTION_DISTANCE_CM - 2), 1)
            # 평소엔 멀리 있다가 가끔(약 10%) 가까워짐
            if random.random() < 0.1:
                return round(random.uniform(5.0, config.DETECTION_DISTANCE_CM - 1), 1)
            return round(random.uniform(config.DETECTION_DISTANCE_CM + 10, 120.0), 1)

        if self._sensor is None:
            self._init_device()
        # gpiozero distance 는 미터 단위 (0.0 ~ max_distance)
        return round(self._sensor.distance * 100.0, 1)

    def trigger_mock_near(self) -> None:
        """MOCK_MODE 에서 다음 read_cm() 을 '가까움'으로 강제 (테스트 감지용)."""
        self._mock_force_near = True

    def close(self) -> None:
        try:
            if self._sensor is not None:
                self._sensor.close()
        except Exception:
            pass
        self._sensor = None


# ===========================================================================
# I2C 캐릭터 LCD (PCF8574)
# ===========================================================================
class LCDDisplay:
    """16x2 (혹은 20x4) I2C LCD. show(line1, line2) 로 두 줄 출력."""

    def __init__(self) -> None:
        self.mock = config.MOCK_MODE
        self._lcd = None
        self._last_lines = ("", "")
        if not self.mock:
            self._init_device()

    def _init_device(self) -> None:
        from RPLCD.i2c import CharLCD

        self._lcd = CharLCD(
            i2c_expander="PCF8574",
            address=config.LCD_I2C_ADDR,
            port=config.LCD_I2C_PORT,
            cols=config.LCD_COLS,
            rows=config.LCD_ROWS,
            auto_linebreaks=False,
        )
        self._lcd.clear()
        logger.info(
            "LCD initialized: 0x%02x on i2c-%d (%dx%d)",
            config.LCD_I2C_ADDR,
            config.LCD_I2C_PORT,
            config.LCD_COLS,
            config.LCD_ROWS,
        )

    def show(self, line1: str = "", line2: str = "") -> None:
        """두 줄을 출력한다. 컬럼 폭에 맞춰 자르고 우측을 공백으로 채운다. 실패 시 예외."""
        l1 = (line1 or "")[: config.LCD_COLS].ljust(config.LCD_COLS)
        l2 = (line2 or "")[: config.LCD_COLS].ljust(config.LCD_COLS)

        if self.mock:
            # 화면이 자주 같으면 로그 스팸 방지
            if (l1, l2) != self._last_lines:
                logger.info("LCD(mock) | %s | %s", l1.strip(), l2.strip())
            self._last_lines = (l1, l2)
            return

        if self._lcd is None:
            self._init_device()
        # 변화가 없으면 굳이 다시 쓰지 않음 (깜빡임/I2C 부하 방지)
        if (l1, l2) == self._last_lines:
            return
        self._lcd.cursor_pos = (0, 0)
        self._lcd.write_string(l1)
        if config.LCD_ROWS > 1:
            self._lcd.cursor_pos = (1, 0)
            self._lcd.write_string(l2)
        self._last_lines = (l1, l2)

    def clear(self) -> None:
        try:
            if self._lcd is not None:
                self._lcd.clear()
            self._last_lines = ("", "")
        except Exception:
            pass

    def close(self) -> None:
        try:
            if self._lcd is not None:
                self._lcd.clear()
                self._lcd.close(clear=True)
        except Exception:
            pass
        self._lcd = None


# 모듈 단독 실행: 간단 라이브 리드 (scripts/test_sensors.py 가 더 자세함)
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    dht = DHTSensor()
    us = UltrasonicSensor()
    try:
        for _ in range(5):
            try:
                t, h = dht.read()
                print(f"DHT  : {t} C, {h} %")
            except Exception as e:
                print("DHT  : ERROR", e)
            try:
                d = us.read_cm()
                print(f"DIST : {d} cm")
            except Exception as e:
                print("DIST : ERROR", e)
            time.sleep(1.0)
    finally:
        dht.close()
        us.close()
