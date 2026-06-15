"""
classifier.py — 단일샷 이미지 분류 (순수 로직+IO, GPIO 금지 §7).

공개 계약 (§5):
    classify(image) -> (label: str, confidence: float),  label in config.CLASS_NAMES
    - CONF_THRESHOLD 미만이거나 매핑 불가 라벨이면 ("others", confidence).
    - 내부 모델이 검출기(detector)든 분류기(classifier)든 동일 계약을 보장한다.

모델 선택 (§6):
    - config.MODEL_PATH(.pt)가 있으면 그것을 사용 (env RECYCLER_MODEL_PATH 로 덮어쓰기 가능).
    - 없으면 config.MODEL_FALLBACK(기본 yolov8n.pt, COCO)을 ultralytics 가 자동 다운로드.
      COCO 모델은 'bottle' 을 안정적으로 인식하므로 데모 안전망이 된다.
    - torch/ultralytics 가 이미 설치돼 있으면 재설치하지 않는다.

이 파일은 카메라를 직접 열지 않는다 — 항상 외부에서 받은 프레임(np.ndarray)을 분류한다.
"""

import logging
import os
import random
from typing import Optional, Tuple, Union

import numpy as np

import config

logger = logging.getLogger(__name__)

ImageLike = Union[str, "np.ndarray"]


class Classifier:
    """ultralytics YOLO 기반 분류기 (직접 학습한 .pt 로드; 검출/분류 모델 모두 지원)."""

    def __init__(self, model_path: Optional[str] = None) -> None:
        self.mock = config.MOCK_MODE
        self.model = None
        self.task = None          # 'detect' | 'classify'
        self.names: dict = {}     # {idx: name}
        self.model_source = None  # 실제 로드된 경로/이름
        if not self.mock:
            self._load(model_path or config.MODEL_PATH)

    def _load(self, path: str) -> None:
        """학습된 YOLO 가중치(.pt)를 로드. 없으면 ultralytics 폴백."""
        import torch
        from ultralytics import YOLO  # torch 로딩이 무거우므로 지연 import

        # Pi 5(4GB) 메모리/CPU 스파이크 완화를 위한 스레드 제한
        try:
            torch.set_num_threads(max(1, config.TORCH_THREADS))
        except Exception:
            pass

        source = path
        if not os.path.exists(path):
            logger.warning(
                "model not found at %s -> falling back to %s (auto-download)",
                path, config.MODEL_FALLBACK,
            )
            source = config.MODEL_FALLBACK

        logger.info("loading model (ultralytics): %s", source)
        self.model = YOLO(source)
        self.task = getattr(self.model, "task", "detect")
        self.names = dict(self.model.names) if getattr(self.model, "names", None) else {}
        self.model_source = source
        logger.info("model loaded: source=%s task=%s classes=%s",
                    os.path.basename(str(source)), self.task, list(self.names.values()))

    # --- 라벨 정규화 ---
    @staticmethod
    def _map_label(raw_name: str) -> str:
        """모델 원본 라벨 -> 프로젝트 5클래스. 매핑 없으면 'others'."""
        key = str(raw_name).strip().lower()
        mapped = config.MODEL_LABEL_MAP.get(key)
        if mapped is None:
            # 부분일치도 한 번 시도 (예: 'plastic bag' 안에 'plastic')
            for k, v in config.MODEL_LABEL_MAP.items():
                if k in key:
                    mapped = v
                    break
        if mapped in config.CLASS_NAMES:
            return mapped
        return "others"

    def classify(self, image: ImageLike) -> Tuple[str, float]:
        """이미지를 분류해 (label in CLASS_NAMES, confidence) 반환."""
        if self.mock:
            label = random.choice(config.CLASS_NAMES)
            conf = round(random.uniform(0.55, 0.97), 2)
            return label, conf

        if self.model is None:
            self._load(config.MODEL_PATH)

        # ultralytics 는 np.ndarray(BGR), 파일경로 모두 허용.
        results = self.model.predict(image, imgsz=config.INFER_IMGSZ, verbose=False)
        if not results:
            return "others", 0.0
        result = results[0]

        if self.task == "classify":
            return self._from_classify(result)
        return self._from_detect(result)

    @staticmethod
    def _select_label(detections) -> Tuple[str, float]:
        """
        검출/후보 목록에서 라벨 1개를 선택하는 순수 로직(테스트 용이).

        detections: list[(raw_name: str, conf: float)]  — 모델 원본 라벨과 신뢰도.

        정책:
          - config.PREFER_RECYCLABLE 이면: 임계값 이상 박스들 중 '재활용품'
            (others 가 아닌 bottle/can/paper/glass)을 우선하고, 그중 confidence 최댓값을 반환.
            재활용품이 임계값 이상으로 하나도 없으면 'others'.
          - 아니면(기존): 전역 최고 confidence 박스를 사용하되 임계값 미만/미매핑이면 'others'.
        """
        if not detections:
            return "others", 0.0

        # (project_label, conf) 로 정규화
        mapped = [(Classifier._map_label(n), float(c)) for (n, c) in detections]
        # 전역 최고(폴백/기존 모드용)
        best_label, best_conf = max(mapped, key=lambda x: x[1])

        if config.PREFER_RECYCLABLE:
            recyclables = [
                (m, c)
                for (m, c) in mapped
                if m != "others" and c >= config.CONF_THRESHOLD
            ]
            if recyclables:
                # 1순위: 채점 대상(bottle/can/paper)이 임계값 이상으로 잡혔으면 그중 최고 conf.
                #        (모델이 불투명 캔/병을 glass 로 오인해도, metal/plastic 을 약하게라도
                #         잡았으면 can/bottle 로 살린다.)
                primary = [(m, c) for (m, c) in recyclables if m in config.PRIMARY_CLASSES]
                if primary:
                    label, conf = max(primary, key=lambda x: x[1])
                    return label, round(conf, 4)
                # 2순위: 채점 대상이 없으면 나머지 재활용품(glass 등) 중 최고 conf.
                label, conf = max(recyclables, key=lambda x: x[1])
                return label, round(conf, 4)
            # 임계값 이상 재활용품이 없음 → others (참고용으로 전역 최고 conf 표기)
            return "others", round(best_conf, 4)

        # 기존 동작: 전역 최고 1개
        if best_conf < config.CONF_THRESHOLD:
            return "others", round(best_conf, 4)
        return best_label, round(best_conf, 4)

    def _from_detect(self, result) -> Tuple[str, float]:
        """검출 결과의 모든 박스에서 라벨 1개를 선택(_select_label 정책)."""
        boxes = getattr(result, "boxes", None)
        if boxes is None or len(boxes) == 0:
            return "others", 0.0
        confs = boxes.conf.cpu().numpy()
        clss = boxes.cls.cpu().numpy().astype(int)
        detections = [
            (self.names.get(int(clss[i]), str(int(clss[i]))), float(confs[i]))
            for i in range(len(confs))
        ]
        return self._select_label(detections)

    def _from_classify(self, result) -> Tuple[str, float]:
        """분류(softmax) 결과의 클래스 확률에서 _select_label 정책으로 1개 선택."""
        probs = getattr(result, "probs", None)
        if probs is None:
            return "others", 0.0
        # 상위 5개 클래스를 후보로 (재활용품 우선 정책이 의미 있도록)
        try:
            top5 = [int(i) for i in probs.top5]
            data = probs.data.cpu().numpy()
            detections = [(self.names.get(i, str(i)), float(data[i])) for i in top5]
        except Exception:
            # 폴백: top-1 만
            detections = [(self.names.get(int(probs.top1), str(int(probs.top1))),
                           float(probs.top1conf))]
        return self._select_label(detections)


# --- 모듈 싱글톤 (app.py 가 시작 시 1회 load 하고 이후 classify 만 호출) ---
_classifier: Optional[Classifier] = None


def load(model_path: Optional[str] = None) -> Classifier:
    """분류기를 (한 번) 로드해 싱글톤으로 보관."""
    global _classifier
    if _classifier is None:
        _classifier = Classifier(model_path)
    return _classifier


def classify(image: ImageLike) -> Tuple[str, float]:
    """편의 함수: 싱글톤 분류기로 classify."""
    return load().classify(image)


if __name__ == "__main__":
    import argparse

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--image", required=True, help="분류할 이미지 경로")
    args = ap.parse_args()

    label, conf = load().classify(args.image)
    print(f"label={label} confidence={conf}")
