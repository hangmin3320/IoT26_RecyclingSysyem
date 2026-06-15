#!/usr/bin/env python3
"""
test_camera.py — 카메라 점검.

사용법:
    python scripts/test_camera.py                 # 스냅샷 1장 저장
    python scripts/test_camera.py --stream 15     # 15초간 MediaMTX 로 라이브 publish 테스트

스트림 테스트 전 MediaMTX 를 먼저 실행:
    ./mediamtx/mediamtx ./mediamtx/mediamtx.yml
그리고 브라우저에서 http://<pi-ip>:8888/cam 확인.
"""
import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
import camera_stream  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stream", type=int, default=0,
                    help="N초간 MediaMTX 로 라이브 스트림 테스트")
    ap.add_argument("--out", default="captures/_camtest.jpg", help="스냅샷 저장 경로")
    args = ap.parse_args()

    import cv2

    cam = camera_stream.CameraStream()
    print(f"카메라 사용 가능: {cam.available} (mock={cam.mock})")

    # 스냅샷
    try:
        frame = cam.capture_still()
        os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
        cv2.imwrite(args.out, frame)
        print(f"스냅샷 저장: {args.out}  shape={frame.shape}")
    except Exception as e:
        print(f"스냅샷 실패: {e}")

    # 스트림
    if args.stream > 0:
        print(f"MediaMTX 로 {args.stream}초간 publish... (rtsp: {config.MEDIAMTX_RTSP_URL})")
        cam.start_stream()
        time.sleep(1.5)
        print(f"streaming={cam.streaming}")
        print(f"브라우저 확인: http://<pi-ip>:{config.MEDIAMTX_HLS_PORT}/{config.MEDIAMTX_PATH}")
        try:
            time.sleep(args.stream)
        except KeyboardInterrupt:
            pass

    cam.close()
    print("완료.")


if __name__ == "__main__":
    main()
