#!/usr/bin/env python3
"""
test_lcd.py — I2C LCD 점검.

사용법:
    python scripts/test_lcd.py

LCD 가 안 나오면:
  - `i2cdetect -y 1` 로 주소 확인 (보통 0x27 또는 0x3f) 후 config.LCD_I2C_ADDR 수정
  - 백라이트 가변저항(콘트라스트) 조정
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
import sensors  # noqa: E402


def main():
    print(f"LCD: addr=0x{config.LCD_I2C_ADDR:02x}, port={config.LCD_I2C_PORT}, "
          f"{config.LCD_COLS}x{config.LCD_ROWS}")
    lcd = sensors.LCDDisplay()
    try:
        frames = [
            ("Smart Recycler", "LCD test OK"),
            ("Gachon IoT", "Team Project"),
            ("T:24C H:51%", "Dist:42cm"),
            ("Detected:", "bottle 0.91"),
        ]
        for l1, l2 in frames:
            print(f"  -> | {l1:<16} | {l2:<16} |")
            lcd.show(l1, l2)
            time.sleep(1.5)
        lcd.show("Test complete", "Bye!")
        time.sleep(1.0)
        print("LCD 테스트 완료. 화면에 위 문구들이 보였다면 정상.")
    except Exception as e:
        print(f"LCD ERROR: {e}")
        print("i2cdetect -y 1 로 주소 확인 후 config.LCD_I2C_ADDR 수정.")
    finally:
        lcd.close()


if __name__ == "__main__":
    main()
