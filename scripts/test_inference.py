#!/usr/bin/env python3
"""
test_inference.py — 분류 모델 점검.

사용법:
    python scripts/test_inference.py --image samples/bottle.jpg
    python scripts/test_inference.py --camera          # 카메라로 1장 찍어 분류
    python scripts/test_inference.py --image x.jpg --raw  # 원본 검출 결과까지 출력

CLASS_NAMES = bottle / can / paper / glass / others 로 정규화된 결과를 출력한다.
"""
import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
import classifier  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--image", help="분류할 이미지 경로")
    ap.add_argument("--camera", action="store_true", help="카메라로 한 장 찍어 분류")
    ap.add_argument("--raw", action="store_true", help="모델 원본 검출 결과도 출력")
    args = ap.parse_args()

    if not args.image and not args.camera:
        ap.error("--image 또는 --camera 중 하나는 필요합니다.")

    print(f"모델 경로: {config.MODEL_PATH}")
    print(f"CONF_THRESHOLD: {config.CONF_THRESHOLD},  INFER_IMGSZ: {config.INFER_IMGSZ}")
    t0 = time.time()
    clf = classifier.load()
    print(f"모델 로드: {time.time()-t0:.1f}s  (source={clf.model_source}, task={clf.task})")
    print(f"모델 원본 클래스: {list(clf.names.values())}\n")

    # 입력 준비
    if args.camera:
        import camera_stream
        import cv2

        cam = camera_stream.CameraStream()
        image = cam.capture_still()
        cv2.imwrite("captures/_infertest.jpg", image)
        cam.close()
        print("카메라 캡처 -> captures/_infertest.jpg")
    else:
        image = args.image
        if not os.path.exists(image):
            print(f"이미지 없음: {image}")
            return

    # 분류
    t1 = time.time()
    label, conf = clf.classify(image)
    dt = time.time() - t1
    print(f"\n>>> 결과: label={label}  confidence={conf:.3f}  ({dt:.2f}s)")

    # 원본 검출 결과
    if args.raw and not clf.mock:
        res = clf.model.predict(image, imgsz=config.INFER_IMGSZ, verbose=False)[0]
        if getattr(res, "boxes", None) is not None and len(res.boxes):
            print("\n원본 검출 (raw):")
            for b in res.boxes:
                ci = int(b.cls[0]); cf = float(b.conf[0])
                raw = clf.names.get(ci, ci)
                print(f"  {raw:<14} {cf:.3f} -> {classifier.Classifier._map_label(raw)}")
        else:
            print("\n원본 검출 없음.")


if __name__ == "__main__":
    main()
