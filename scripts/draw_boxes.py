#!/usr/bin/env python3
"""
draw_boxes.py — 모델이 검출하는 *모든* bounding box 를 이미지에 그려서 눈으로 직접 확인.

각 박스에 [원본클래스 conf -> 우리라벨] 을 표시하고, 최종 선택된 라벨(classify 결과)을
상단에 크게 표시한다. conf 임계값을 낮춰(기본 0.05) 모델이 약하게라도 본 것까지 다 보여준다.

사용법:
    # 저장된 캡처 1장에 박스 그리기
    python scripts/draw_boxes.py --image captures/20260615_234750_glass.jpg

    # captures/ 의 모든 jpg 에 박스 그려서 captures/boxed/ 에 저장 (일괄)
    python scripts/draw_boxes.py --all

    # 현재 라이브 스냅샷(앱/스트림 실행 중)에 박스 그리기
    python scripts/draw_boxes.py --snapshot

    # 약한 검출까지 더 보고 싶으면 임계값 낮추기
    python scripts/draw_boxes.py --image x.jpg --conf 0.01

결과 파일: 입력옆에 <이름>_boxed.jpg (또는 --all 이면 captures/boxed/).
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
import classifier  # noqa: E402

# 우리 라벨별 색상 (BGR)
_COLORS = {
    "bottle": (0, 200, 0),     # 초록
    "can": (255, 100, 0),      # 파랑
    "paper": (0, 200, 255),    # 노랑
    "glass": (255, 255, 0),    # 청록
    "others": (150, 150, 150), # 회색
}


def annotate(clf, img, conf_th):
    """img(np.ndarray BGR)에 모든 박스를 그리고, (그려진 img, 검출목록, 최종라벨) 반환."""
    import cv2

    res = clf.model.predict(img, imgsz=config.INFER_IMGSZ, conf=conf_th, verbose=False)[0]
    out = img.copy()
    dets = []

    boxes = getattr(res, "boxes", None)
    if boxes is not None and len(boxes) > 0:
        xyxy = boxes.xyxy.cpu().numpy()
        confs = boxes.conf.cpu().numpy()
        clss = boxes.cls.cpu().numpy().astype(int)
        # confidence 내림차순으로 그려서 강한 박스가 위에 오게
        order = confs.argsort()[::-1]
        for i in order:
            x1, y1, x2, y2 = [int(v) for v in xyxy[i]]
            cf = float(confs[i])
            raw = clf.names.get(int(clss[i]), str(int(clss[i])))
            mapped = classifier.Classifier._map_label(raw)
            dets.append((raw, cf, mapped))
            color = _COLORS.get(mapped, (200, 200, 200))
            cv2.rectangle(out, (x1, y1), (x2, y2), color, 2)
            label = f"{raw} {cf:.2f}->{mapped}"
            (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1)
            ytxt = max(0, y1 - 6)
            cv2.rectangle(out, (x1, ytxt - th - 4), (x1 + tw + 4, ytxt + 2), color, -1)
            cv2.putText(out, label, (x1 + 2, ytxt - 2),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 1, cv2.LINE_AA)
    else:
        # 분류(classify) 모델이거나 검출 0개
        probs = getattr(res, "probs", None)
        if probs is not None:
            try:
                top5 = [int(i) for i in probs.top5]
                data = probs.data.cpu().numpy()
                for r, idx in enumerate(top5):
                    nm = clf.names.get(idx, str(idx))
                    cf = float(data[idx])
                    dets.append((nm, cf, classifier.Classifier._map_label(nm)))
                    cv2.putText(out, f"{nm} {cf:.2f}->{classifier.Classifier._map_label(nm)}",
                                (10, 60 + r * 24), cv2.FONT_HERSHEY_SIMPLEX, 0.6,
                                (0, 255, 0), 2, cv2.LINE_AA)
            except Exception:
                pass

    # 최종 선택 라벨(현재 선택 정책 적용 결과)
    final_label, final_conf = clf.classify(img)
    banner = f"FINAL: {final_label} ({final_conf:.2f})  [thr={config.CONF_THRESHOLD}, prefer={config.PREFER_RECYCLABLE}]"
    cv2.rectangle(out, (0, 0), (out.shape[1], 28), (0, 0, 0), -1)
    cv2.putText(out, banner, (8, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.6,
                _COLORS.get(final_label, (255, 255, 255)), 2, cv2.LINE_AA)
    return out, dets, (final_label, final_conf)


def process_one(clf, path, conf_th, out_path):
    import cv2

    img = cv2.imread(path)
    if img is None:
        print(f"  ! 이미지 못 읽음: {path}")
        return
    out, dets, final = annotate(clf, img, conf_th)
    cv2.imwrite(out_path, out)
    print(f"\n[{os.path.basename(path)}] -> {out_path}")
    print(f"  최종 선택: {final[0]} ({final[1]:.3f})")
    if dets:
        print("  검출(내림차순):")
        for raw, cf, mapped in dets[:12]:
            print(f"    {raw:<14} {cf:.3f} -> {mapped}")
    else:
        print("  검출 없음")


def main():
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--image", help="박스 그릴 이미지 1장")
    g.add_argument("--all", action="store_true", help="captures/ 전체 일괄 처리")
    g.add_argument("--snapshot", action="store_true", help="현재 라이브 스냅샷에 박스")
    ap.add_argument("--conf", type=float, default=0.05, help="표시 최소 confidence (기본 0.05)")
    args = ap.parse_args()

    print(f"모델: {os.path.basename(config.MODEL_PATH)}  | imgsz={config.INFER_IMGSZ} | conf표시>={args.conf}")
    clf = classifier.load()
    print(f"모델 클래스: {list(clf.names.values())}")

    if args.image:
        base, ext = os.path.splitext(args.image)
        process_one(clf, args.image, args.conf, f"{base}_boxed{ext}")

    elif args.snapshot:
        snap = config.LIVE_SNAPSHOT_PATH
        if not os.path.exists(snap):
            print(f"라이브 스냅샷 없음: {snap} (앱/스트림을 먼저 실행하세요)")
            return
        process_one(clf, snap, args.conf, os.path.join(config.CAPTURES_DIR, "_snapshot_boxed.jpg"))

    elif args.all:
        import glob
        outdir = os.path.join(config.CAPTURES_DIR, "boxed")
        os.makedirs(outdir, exist_ok=True)
        files = sorted(f for f in glob.glob(os.path.join(config.CAPTURES_DIR, "*.jpg"))
                       if "_boxed" not in f and "/boxed/" not in f)
        if not files:
            print("captures/ 에 jpg 가 없습니다.")
            return
        for p in files:
            process_one(clf, p, args.conf, os.path.join(outdir, os.path.basename(p)))
        print(f"\n완료: {len(files)}장 -> {outdir}/")


if __name__ == "__main__":
    main()
