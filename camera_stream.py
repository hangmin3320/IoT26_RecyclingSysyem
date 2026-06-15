"""
camera_stream.py — 카메라 단일 소유 모듈 (GPIO/카메라는 이 파일과 sensors.py 에만, §7).

책임 (§10):
  1) 라이브 스트림: `rpicam-vid → ffmpeg → MediaMTX(RTSP)` 관리형 서브프로세스.
     ffmpeg 가 동시에 1fps JPEG 스냅샷(LIVE_SNAPSHOT_PATH)을 갱신한다.
  2) 온디맨드 스냅샷: capture_still() -> np.ndarray(BGR). 감지 트리거 때만 호출.
     라이브 스냅샷 파일을 읽어 반환(카메라 핸들 충돌 없음, 항상 최신 ~1s).

왜 rpicam-vid + ffmpeg 인가 (§10 fallback):
  - Pi 5 는 H264 하드웨어 인코더가 없어 소프트웨어 인코딩이 필요하다.
  - Picamera2 의 H264Encoder + FfmpegOutput 경로는 장시간 구동 시 프레임 타임스탬프가
    드리프트하며 RTSP publish 가 멈추는(그러나 프로세스는 살아있는) 현상이 있었다.
  - ffmpeg(libx264, 고정 입력 fps)가 인코딩/먹싱을 일관되게 처리해 장시간 안정적이다.

스톨 감지(워치독):
  - 스냅샷 파일 mtime 이 STREAM_STALL_TIMEOUT_S 동안 갱신되지 않으면 스트림이 죽은 것으로 보고
    파이프라인을 재시작한다. "프로세스는 살아있지만 데이터만 멈춘" 경우까지 잡아낸다.

MOCK_MODE 또는 카메라/도구 미존재 시: 합성 이미지를 생성하고 스트리밍은 생략한다.
"""

import logging
import os
import shutil
import signal
import subprocess
import threading
import time
from typing import Optional

import numpy as np

import config

logger = logging.getLogger(__name__)


class CameraStream:
    def __init__(self) -> None:
        self.mock = config.MOCK_MODE
        self.proc: Optional[subprocess.Popen] = None   # rpicam|ffmpeg 파이프라인(프로세스 그룹)
        self.streaming = False
        self._want_stream = False
        self._lock = threading.Lock()
        self._monitor_thread: Optional[threading.Thread] = None
        self._stop_monitor = threading.Event()
        self.available = False
        self.snapshot_path = config.LIVE_SNAPSHOT_PATH

        os.makedirs(os.path.dirname(self.snapshot_path), exist_ok=True)

        if self.mock:
            logger.info("CameraStream in MOCK mode (synthetic frames, no streaming)")
            self.available = True
            return

        # 필수 외부 도구 확인
        self._have_rpicam = shutil.which("rpicam-vid") is not None
        self._have_ffmpeg = shutil.which("ffmpeg") is not None
        if not (self._have_rpicam and self._have_ffmpeg):
            logger.error(
                "rpicam-vid/ffmpeg 누락 (rpicam=%s ffmpeg=%s) — 합성 프레임으로 대체",
                self._have_rpicam,
                self._have_ffmpeg,
            )
            self.mock = True
            self.available = False
        else:
            self.available = True

    # -----------------------------------------------------------------
    # 라이브 스트림 파이프라인
    # -----------------------------------------------------------------
    def _build_cmd(self) -> str:
        w, h = config.STREAM_RESOLUTION
        fps = config.STREAM_FPS
        rtsp = config.MEDIAMTX_RTSP_URL
        snap = self.snapshot_path
        # rpicam-vid: 원본 YUV420 프레임을 stdout 으로 (Pi5엔 HW h264 없음).
        # ffmpeg: libx264(ultrafast,zerolatency)로 인코딩 → RTSP publish,
        #         동시에 1fps 로 최신 JPEG 스냅샷 갱신(-update 1).
        return (
            f"rpicam-vid -t 0 --nopreview --width {w} --height {h} "
            f"--framerate {fps} --codec yuv420 -o - 2>/dev/null | "
            f"ffmpeg -loglevel error -f rawvideo -pix_fmt yuv420p -s {w}x{h} -r {fps} -i - "
            f"-c:v libx264 -preset ultrafast -tune zerolatency -g {fps*2} -pix_fmt yuv420p "
            f"-f rtsp -rtsp_transport tcp {rtsp} "
            f"-map 0:v -vf fps=1 -update 1 -y {snap}"
        )

    def start_stream(self) -> None:
        if not config.ENABLE_STREAM:
            logger.info("streaming disabled (ENABLE_STREAM=False)")
            return
        if self.mock or not self.available:
            logger.info("streaming skipped (mock/no camera)")
            return
        self._want_stream = True
        self._spawn_pipeline()
        if self._monitor_thread is None:
            self._monitor_thread = threading.Thread(
                target=self._monitor_loop, name="stream-monitor", daemon=True
            )
            self._monitor_thread.start()

    def _spawn_pipeline(self) -> None:
        with self._lock:
            if self.streaming and self.proc and self.proc.poll() is None:
                return
            cmd = self._build_cmd()
            try:
                # 자체 세션으로 띄워 종료 시 그룹 전체(rpicam+ffmpeg)를 한 번에 kill.
                self.proc = subprocess.Popen(
                    cmd, shell=True, executable="/bin/bash", start_new_session=True
                )
                self.streaming = True
                logger.info("live stream pipeline started -> %s", config.MEDIAMTX_RTSP_URL)
            except Exception as e:
                self.streaming = False
                logger.error("failed to start stream pipeline: %s", e)

    def _kill_pipeline(self) -> None:
        with self._lock:
            p = self.proc
            self.proc = None
            self.streaming = False
        if p is None:
            return
        try:
            os.killpg(os.getpgid(p.pid), signal.SIGINT)
            try:
                p.wait(timeout=3)
            except subprocess.TimeoutExpired:
                os.killpg(os.getpgid(p.pid), signal.SIGKILL)
                p.wait(timeout=3)
        except Exception as e:
            logger.warning("kill pipeline error: %s", e)
        # 남은 rpicam/ffmpeg 잔여 정리(보수적)
        for name in ("rpicam-vid", "ffmpeg"):
            subprocess.run(["pkill", "-f", name], check=False,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def _snapshot_age(self) -> Optional[float]:
        """스냅샷 파일이 마지막으로 갱신된 뒤 경과 초. 파일 없으면 None."""
        try:
            return time.time() - os.path.getmtime(self.snapshot_path)
        except OSError:
            return None

    def stream_healthy(self) -> bool:
        """스트림이 살아있는지(최근 스냅샷이 갱신 중인지). 대시보드 camera 배지에 사용."""
        if self.mock:
            return True
        if not self._want_stream:
            return self.available  # 스트림 미사용 모드에서는 카메라 가용 여부로
        age = self._snapshot_age()
        return age is not None and age <= max(3.0, config.STREAM_STALL_TIMEOUT_S)

    def _monitor_loop(self) -> None:
        """프로세스 종료 또는 스냅샷 정지(stall)를 감지해 파이프라인을 재시작."""
        backoff = 2.0
        grace = 9.0  # (재)시작 후 첫 스냅샷이 생길 때까지 staleness 판단 보류
        last_spawn = time.monotonic()
        while not self._stop_monitor.is_set():
            self._stop_monitor.wait(3.0)
            if self._stop_monitor.is_set() or not self._want_stream:
                continue

            proc_dead = self.proc is None or self.proc.poll() is not None
            age = self._snapshot_age()
            in_grace = (time.monotonic() - last_spawn) < grace
            # 프로세스가 살아있고 유예가 지났는데도 스냅샷이 오래되면 stall 로 판단
            stalled = (
                not proc_dead
                and not in_grace
                and (age is None or age > config.STREAM_STALL_TIMEOUT_S)
            )

            if proc_dead or stalled:
                logger.warning(
                    "stream unhealthy (proc_dead=%s, snapshot_age=%s) — restarting in %.1fs",
                    proc_dead, None if age is None else round(age, 1), backoff,
                )
                self._kill_pipeline()
                self._stop_monitor.wait(backoff)
                if self._stop_monitor.is_set():
                    break
                self._spawn_pipeline()
                last_spawn = time.monotonic()
                backoff = min(backoff * 1.5, 15.0)
            elif not in_grace:
                backoff = 2.0

    # -----------------------------------------------------------------
    # 스냅샷
    # -----------------------------------------------------------------
    def capture_still(self) -> "np.ndarray":
        """분류용 스냅샷(BGR np.ndarray) 반환. 실패 시 예외."""
        if self.mock:
            return self._synthetic_frame()

        import cv2

        # 1) 스트리밍 중이면 ffmpeg 가 갱신하는 최신 스냅샷 파일을 사용 (카메라 충돌 없음).
        #    스트림 시작 직후/일시적 stall 대비로 최신 스냅샷을 잠깐(최대 ~5s) 기다린다.
        fresh = max(3.0, config.STREAM_STALL_TIMEOUT_S)
        if self._want_stream:
            # 콜드 스타트 시 첫 스냅샷 생성에 ~6-8s 걸릴 수 있어 넉넉히 기다린다.
            deadline = time.time() + 12.0
            while time.time() < deadline:
                age = self._snapshot_age()
                if age is not None and age <= fresh:
                    frame = cv2.imread(self.snapshot_path)
                    if frame is not None:
                        return frame
                time.sleep(0.3)
            logger.warning("no fresh snapshot within wait window, trying fallbacks")

        # 2) 스트림이 없을 때(ENABLE_STREAM=False 등)는 일회성 rpicam-jpeg 캡처
        if not self._want_stream and shutil.which("rpicam-jpeg"):
            tmp = self.snapshot_path + ".oneshot.jpg"
            w, h = config.STREAM_RESOLUTION
            r = subprocess.run(
                ["rpicam-jpeg", "--nopreview", "-t", "600",
                 "--width", str(w), "--height", str(h), "-o", tmp],
                check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            if r.returncode == 0:
                frame = cv2.imread(tmp)
                try:
                    os.remove(tmp)
                except OSError:
                    pass
                if frame is not None:
                    return frame

        raise RuntimeError("capture_still: no fresh snapshot available")

    def _synthetic_frame(self) -> "np.ndarray":
        """MOCK/카메라 없음일 때 합성 프레임 (시간 텍스트 포함)."""
        w, h = config.STREAM_RESOLUTION
        img = np.full((h, w, 3), 40, dtype=np.uint8)
        img[:, :, 0] = (np.linspace(0, 255, w).astype(np.uint8))[None, :]
        try:
            import cv2

            cv2.putText(
                img, time.strftime("MOCK %H:%M:%S"), (30, h // 2),
                cv2.FONT_HERSHEY_SIMPLEX, 2.0, (255, 255, 255), 3,
            )
        except Exception:
            pass
        return img

    # -----------------------------------------------------------------
    # 종료
    # -----------------------------------------------------------------
    def close(self) -> None:
        self._want_stream = False
        self._stop_monitor.set()
        self._kill_pipeline()
        try:
            if os.path.exists(self.snapshot_path):
                os.remove(self.snapshot_path)
        except OSError:
            pass


# 모듈 단독 실행: 스냅샷 1장 저장
if __name__ == "__main__":
    import cv2

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    cam = CameraStream()
    cam.start_stream()
    time.sleep(6)  # 스트림/스냅샷 워밍업
    try:
        frame = cam.capture_still()
        cv2.imwrite("captures/_camtest.jpg", frame)
        print(f"captured {frame.shape} -> captures/_camtest.jpg")
    finally:
        cam.close()
