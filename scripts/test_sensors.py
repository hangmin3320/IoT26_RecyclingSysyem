#!/usr/bin/env python3
"""
test_sensors.py — DHT(온습도) + HC-SR04(초음파) 라이브 점검.

사용법:
    python scripts/test_sensors.py            # 기본 핀(config.py)으로 반복 측정
    python scripts/test_sensors.py --scan-dht # DHT 가 안 잡힐 때 후보 GPIO 핀 스캔

DHT 가 GPIO4 에서 안 잡히면:
  - 배선(데이터/VCC/GND)과 풀업 저항(보통 10k) 확인
  - 가장 안정적인 방법: dht11 디바이스트리 오버레이 (README §트러블슈팅 참고)
"""
import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
import sensors  # noqa: E402


def scan_dht():
    """후보 GPIO 핀을 하나씩(별도 시도) 점검해 DHT 가 응답하는 핀을 찾는다."""
    import board
    import adafruit_dht

    print("DHT 핀 스캔 (각 핀 1회 시도)...")
    for p in [4, 17, 18, 22, 27, 5, 6, 26, 23, 24, 25, 16, 20, 21]:
        pin = getattr(board, f"D{p}", None)
        if pin is None:
            continue
        try:
            dev = adafruit_dht.DHT22(pin, use_pulseio=False)
            t, h = dev.temperature, dev.humidity
            dev.exit()
            if t is not None:
                print(f"  GPIO{p:<2}: OK  t={t}C h={h}%  <-- 이 핀을 config.DHT_PIN 으로!")
            else:
                print(f"  GPIO{p:<2}: 응답 없음(None)")
        except Exception as e:
            print(f"  GPIO{p:<2}: {e}")
        time.sleep(2.1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scan-dht", action="store_true", help="DHT 후보 핀 스캔")
    ap.add_argument("--count", type=int, default=15, help="반복 횟수")
    args = ap.parse_args()

    if args.scan_dht:
        scan_dht()
        return

    print(f"설정: DHT=GPIO{config.DHT_PIN}({config.DHT_SENSOR_TYPE}), "
          f"US TRIG=GPIO{config.ULTRASONIC_TRIG_PIN} ECHO=GPIO{config.ULTRASONIC_ECHO_PIN}")
    print(f"감지 임계 거리: {config.DETECTION_DISTANCE_CM} cm\n")

    dht = sensors.DHTSensor()
    ultra = sensors.UltrasonicSensor()
    ok_dht = 0
    try:
        for i in range(args.count):
            line = f"[{i+1:>2}/{args.count}] "
            try:
                t, h = dht.read()
                line += f"DHT: {t:>5.1f}C {h:>5.1f}%   "
                ok_dht += 1
            except Exception as e:
                line += f"DHT: ERR ({str(e)[:30]})   "
            try:
                d = ultra.read_cm()
                near = "  << 물체 감지!" if d <= config.DETECTION_DISTANCE_CM else ""
                line += f"DIST: {d:>6.1f}cm{near}"
            except Exception as e:
                line += f"DIST: ERR ({str(e)[:30]})"
            print(line)
            time.sleep(1.0)
    except KeyboardInterrupt:
        pass
    finally:
        dht.close()
        ultra.close()

    print(f"\nDHT 성공 {ok_dht}/{args.count}.")
    if ok_dht == 0:
        print("DHT 응답 없음 -> `--scan-dht` 로 핀 확인 또는 배선/오버레이 점검 (README 참고).")


if __name__ == "__main__":
    main()
