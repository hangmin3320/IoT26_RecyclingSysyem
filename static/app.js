/* app.js — /api/status, /api/history 폴링 후 DOM 을 제자리 갱신 (§12) */
(function () {
  "use strict";

  const cfg = window.STREAM_CFG || {};
  const CLASS_NAMES = window.CLASS_NAMES || [];
  const host = window.location.hostname; // 대시보드 접속 host 와 동일 host 로 스트림 재생

  // ----------------------------------------------------------------
  // 라이브 스트림 (HLS via hls.js, 또는 WebRTC iframe)
  // ----------------------------------------------------------------
  function setStreamMsg(text) {
    const el = document.getElementById("stream-msg");
    if (!el) return;
    if (text) { el.textContent = text; el.classList.remove("hidden"); }
    else { el.classList.add("hidden"); }
  }

  function initStream() {
    const video = document.getElementById("live");
    const mode = cfg.mode || "auto";
    const hlsUrl = `http://${host}:${cfg.hlsPort}/${cfg.path}/index.m3u8`;

    // WebRTC 모드: MediaMTX WHEP 페이지를 iframe 으로 임베드
    if (mode === "webrtc") {
      const wrap = video.parentElement;
      const iframe = document.createElement("iframe");
      iframe.src = `http://${host}:${cfg.webrtcPort}/${cfg.path}`;
      iframe.style.cssText = "width:100%;height:100%;border:0;";
      iframe.allow = "autoplay";
      video.replaceWith(iframe);
      setStreamMsg(null);
      return;
    }

    // HLS (기본)
    if (window.Hls && window.Hls.isSupported()) {
      const hls = new Hls({ lowLatencyMode: true, liveSyncDuration: 2 });
      hls.loadSource(hlsUrl);
      hls.attachMedia(video);
      hls.on(Hls.Events.MANIFEST_PARSED, () => { setStreamMsg(null); video.play().catch(() => {}); });
      hls.on(Hls.Events.ERROR, (evt, data) => {
        if (data.fatal) {
          setStreamMsg("스트림 대기 중… (MediaMTX 실행 확인)");
          // 잠시 후 재시도
          setTimeout(() => { try { hls.loadSource(hlsUrl); } catch (e) {} }, 4000);
        }
      });
    } else if (video.canPlayType("application/vnd.apple.mpegurl")) {
      // Safari 등 네이티브 HLS
      video.src = hlsUrl;
      video.addEventListener("loadedmetadata", () => setStreamMsg(null));
    } else {
      setStreamMsg("이 브라우저는 HLS 를 지원하지 않습니다.");
    }
  }

  // ----------------------------------------------------------------
  // 상태 폴링
  // ----------------------------------------------------------------
  function fmt(v, digits) {
    if (v === null || v === undefined || Number.isNaN(v)) return "--";
    return Number(v).toFixed(digits === undefined ? 1 : digits);
  }

  function setBadge(id, up) {
    const el = document.getElementById(id);
    if (!el) return;
    el.classList.toggle("up", !!up);
    el.classList.toggle("down", !up);
  }

  async function pollStatus() {
    try {
      const r = await fetch("/api/status", { cache: "no-store" });
      const d = await r.json();

      // 시스템 상태
      const sp = document.getElementById("system-status");
      const ok = d.system_status === "ok";
      sp.textContent = ok ? "● 작동 중 (OK)" : d.system_status;
      sp.classList.toggle("ok", ok);
      sp.classList.toggle("bad", !ok);

      // 센서
      const s = d.sensors || {};
      document.getElementById("temp").textContent = fmt(s.temperature_c, 1);
      document.getElementById("hum").textContent = fmt(s.humidity_pct, 0);
      document.getElementById("dist").textContent = fmt(s.distance_cm, 0);
      setBadge("badge-dht", s.dht_connected);
      setBadge("badge-ultrasonic", s.ultrasonic_connected);
      setBadge("badge-camera", s.camera_connected);
      setBadge("badge-lcd", s.lcd_connected);
      document.getElementById("status-ts").textContent = d.timestamp || "--";

      // 최근 감지
      const det = d.last_detection;
      if (det) {
        const img = document.getElementById("det-img");
        const noimg = document.getElementById("det-noimg");
        if (det.image) {
          const newSrc = `/captures/${det.image}`;
          if (img.getAttribute("src") !== newSrc) img.src = newSrc;
          img.style.display = "block";
          noimg.style.display = "none";
        }
        document.getElementById("det-label").textContent = det.label || "--";
        document.getElementById("det-conf").textContent =
          det.confidence != null ? (det.confidence * 100).toFixed(1) + "%" : "--";
        document.getElementById("det-ts").textContent = det.timestamp || "--";
        const t = det.temperature_c != null ? fmt(det.temperature_c, 1) + "°C" : "--";
        const h = det.humidity_pct != null ? fmt(det.humidity_pct, 0) + "%" : "--";
        document.getElementById("det-env").textContent = `${t} · ${h}`;
      }

      // 카운트
      const counts = d.counts || {};
      const total = counts.total || 0;
      let maxVal = 1;
      CLASS_NAMES.forEach((c) => { if ((counts[c] || 0) > maxVal) maxVal = counts[c]; });
      CLASS_NAMES.forEach((c) => {
        const cv = counts[c] || 0;
        const el = document.getElementById("count-" + c);
        if (el) el.textContent = cv;
        const bar = document.getElementById("bar-" + c);
        if (bar) bar.style.width = Math.round((cv / maxVal) * 100) + "%";
      });
      document.getElementById("count-total").textContent = total;
    } catch (e) {
      const sp = document.getElementById("system-status");
      sp.textContent = "● 서버 연결 끊김";
      sp.classList.add("bad");
      sp.classList.remove("ok");
    }
  }

  // ----------------------------------------------------------------
  // 이력 폴링 (덜 자주)
  // ----------------------------------------------------------------
  async function pollHistory() {
    try {
      const r = await fetch("/api/history?limit=20", { cache: "no-store" });
      const d = await r.json();
      const body = document.getElementById("history-body");
      const rows = d.detections || [];
      if (!rows.length) {
        body.innerHTML = '<tr><td colspan="6" class="empty">이력 없음</td></tr>';
        return;
      }
      body.innerHTML = rows
        .map((x) => {
          const tagCls = x.label === "others" ? "tag others" : "tag";
          const conf = x.confidence != null ? (x.confidence * 100).toFixed(0) + "%" : "--";
          const temp = x.temperature_c != null ? Number(x.temperature_c).toFixed(1) + "°C" : "--";
          const hum = x.humidity_pct != null ? Number(x.humidity_pct).toFixed(0) + "%" : "--";
          const imgLink = x.image
            ? `<a href="/captures/${x.image}" target="_blank">보기</a>`
            : "--";
          return `<tr>
            <td>${x.timestamp || "--"}</td>
            <td><span class="${tagCls}">${x.label}</span></td>
            <td>${conf}</td>
            <td>${temp}</td>
            <td>${hum}</td>
            <td>${imgLink}</td>
          </tr>`;
        })
        .join("");
    } catch (e) {
      /* 무시 */
    }
  }

  // ----------------------------------------------------------------
  // MOCK 테스트 버튼
  // ----------------------------------------------------------------
  function initMock() {
    const btn = document.getElementById("mock-trigger");
    if (!btn) return;
    btn.addEventListener("click", async () => {
      btn.disabled = true;
      try {
        await fetch("/api/mock/trigger", { method: "POST" });
      } catch (e) {}
      setTimeout(() => { btn.disabled = false; pollStatus(); pollHistory(); }, 1500);
    });
  }

  // ----------------------------------------------------------------
  // 시작
  // ----------------------------------------------------------------
  initStream();
  initMock();
  pollStatus();
  pollHistory();
  setInterval(pollStatus, 1500);
  setInterval(pollHistory, 5000);
})();
