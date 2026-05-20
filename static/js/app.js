/* ============================================================
 * OldTV frontend
 * ------------------------------------------------------------
 * 职责:
 *   1) /api/channels -> 分组频道（含多个备用 plays[]）
 *   2) 网格 UI + 分类 tab + 搜索
 *   3) 点击 -> 全屏 hls.js 播放
 *   4) 失败时按 plays 顺序自动切换下一源；UI 显示 "源 i/N" + 手动切换按钮
 *   5) "验证" 按钮调用 /api/test_channel 进行后端探活
 * ============================================================ */

(function () {
  "use strict";

  const $ = (sel) => document.querySelector(sel);

  // ----- top / grid 元素 -----
  const tabsEl    = $("#tabs");
  const gridEl    = $("#grid");
  const searchEl  = $("#search");
  const refreshEl = $("#refreshBtn");

  // ----- player 元素 -----
  const playerEl   = $("#player");
  const videoEl    = $("#video");
  const nowNameEl  = $("#nowName");
  const closeEl    = $("#closePlayer");
  const reloadEl   = $("#reloadStream");
  const prevSrcEl  = $("#prevSrc");
  const nextSrcEl  = $("#nextSrc");
  const srcInfoEl  = $("#srcInfo");
  const verifyEl   = $("#verifyBtn");
  const msgEl      = $("#streamMsg");

  /** @type {{groups: Record<string, Array<{name,group,logo,plays:string[]}>>, total:number}} */
  let data = { groups: {}, total: 0 };
  let activeGroup = null;
  let hls = null;
  let currentChannel = null;
  let currentSrcIdx = 0;
  let autoFallbackTried = 0;     // 当前频道已尝试过的 fallback 计数，防止死循环
  let playingListener = null;   // 当前 video 元素上挂着的 playing 监听（用于纯音频检测）

  // -------------------------------------------------------------
  // Fetch & render
  // -------------------------------------------------------------
  async function loadChannels(refresh = false) {
    gridEl.innerHTML = '<div class="hint">正在加载频道列表…</div>';
    try {
      const url = refresh ? "/api/channels?refresh=1" : "/api/channels";
      const r = await fetch(url);
      data = await r.json();
      if (!data.groups || data.total === 0) {
        gridEl.innerHTML = '<div class="hint">未能获取到频道列表，请检查网络或稍后重试。</div>';
        return;
      }
      activeGroup = Object.keys(data.groups)[0];
      renderTabs();
      renderGrid();
    } catch (e) {
      console.error(e);
      gridEl.innerHTML = `<div class="hint">加载失败：${e.message}</div>`;
    }
  }

  function renderTabs() {
    tabsEl.innerHTML = "";
    Object.keys(data.groups).forEach((g) => {
      const btn = document.createElement("div");
      btn.className = "tab" + (g === activeGroup ? " active" : "");
      btn.textContent = `${g} (${data.groups[g].length})`;
      btn.addEventListener("click", () => {
        activeGroup = g;
        renderTabs();
        renderGrid();
      });
      tabsEl.appendChild(btn);
    });
  }

  function renderGrid() {
    const q = searchEl.value.trim().toLowerCase();
    let list = data.groups[activeGroup] || [];
    if (q) {
      // 跨分组搜索
      list = [];
      for (const g of Object.keys(data.groups)) {
        for (const ch of data.groups[g]) {
          if (ch.name.toLowerCase().includes(q)) list.push(ch);
        }
      }
    }

    if (!list.length) {
      gridEl.innerHTML = '<div class="hint">没有匹配的频道。</div>';
      return;
    }

    const frag = document.createDocumentFragment();
    list.forEach((ch) => {
      const card = document.createElement("div");
      card.className = "card";
      card.title = `${ch.name} · ${ch.plays.length} 源`;

      if (ch.logo) {
        const img = document.createElement("img");
        img.className = "logo";
        img.src = ch.logo;
        img.alt = "";
        img.loading = "lazy";
        img.onerror = () => img.replaceWith(makePlaceholder(ch.name));
        card.appendChild(img);
      } else {
        card.appendChild(makePlaceholder(ch.name));
      }
      const name = document.createElement("div");
      name.className = "name";
      name.textContent = ch.name;
      card.appendChild(name);

      const badge = document.createElement("div");
      badge.className = "badge";
      badge.textContent = `${ch.plays.length} 源`;
      card.appendChild(badge);

      card.addEventListener("click", () => play(ch));
      frag.appendChild(card);
    });
    gridEl.innerHTML = "";
    gridEl.appendChild(frag);
  }

  function makePlaceholder(name) {
    const div = document.createElement("div");
    div.className = "logo placeholder";
    const ch = (name || "?").replace(/\s+/g, "").charAt(0).toUpperCase();
    div.textContent = ch;
    return div;
  }

  // -------------------------------------------------------------
  // Player
  // -------------------------------------------------------------
  function showMsg(text, autohide = true) {
    msgEl.textContent = text;
    msgEl.classList.add("show");
    if (autohide) {
      clearTimeout(showMsg._t);
      showMsg._t = setTimeout(() => msgEl.classList.remove("show"), 3000);
    }
  }
  function hideMsg() { msgEl.classList.remove("show"); }

  function destroyHls() {
    if (hls) { try { hls.destroy(); } catch (e) {} hls = null; }
    if (playingListener) {
      videoEl.removeEventListener("playing", playingListener);
      playingListener = null;
    }
    try { videoEl.pause(); } catch (e) {}
    videoEl.removeAttribute("src");
    videoEl.load();
  }

  function play(ch) {
    currentChannel = ch;
    currentSrcIdx = 0;
    autoFallbackTried = 0;
    nowNameEl.textContent = ch.name;
    playerEl.classList.remove("hidden");
    enterFullscreen();
    startStream();
  }

  function updateSrcInfo() {
    if (!currentChannel) {
      srcInfoEl.textContent = "";
      return;
    }
    srcInfoEl.textContent = `源 ${currentSrcIdx + 1}/${currentChannel.plays.length}`;
    prevSrcEl.disabled = currentSrcIdx <= 0;
    nextSrcEl.disabled = currentSrcIdx >= currentChannel.plays.length - 1;
  }

  function startStream() {
    if (!currentChannel) return;
    const src = currentChannel.plays[currentSrcIdx];
    if (!src) {
      showMsg("当前频道没有更多可用的源", false);
      return;
    }
    destroyHls();
    hideMsg();
    updateSrcInfo();
    showMsg(`正在连接源 ${currentSrcIdx + 1}/${currentChannel.plays.length}…`);

    // 通用：等到真正开始播放时检查是否为纯音频流（videoWidth/Height === 0）。
    // 不能在 hls.js 的 MANIFEST_PARSED 阶段判断，因为 IPTV 常见的 media playlist
    // 不带 CODECS / RESOLUTION 属性时 level 元数据全为空，会被误判。
    playingListener = function () {
      playingListener = null;
      if (videoEl.videoWidth === 0 && videoEl.videoHeight === 0) {
        console.warn("[Player] audio-only stream detected (videoWidth=0), fallback");
        tryNextOrFail({ type: "media", details: "audio-only" });
        return;
      }
      hideMsg();
      autoFallbackTried = 0;     // 成功后重置
    };
    videoEl.addEventListener("playing", playingListener, { once: true });

    if (window.Hls && Hls.isSupported()) {
      hls = new Hls({
        lowLatencyMode: true,
        maxBufferLength: 30,
        manifestLoadingTimeOut: 8000,    // 8s 内拉不到 manifest 即视为失败
        manifestLoadingMaxRetry: 1,
        levelLoadingTimeOut: 8000,
        fragLoadingTimeOut: 15000,
      });
      hls.loadSource(src);
      hls.attachMedia(videoEl);

      hls.on(Hls.Events.MANIFEST_PARSED, () => {
        // manifest 拿到后启动播放；纯音频/视频的判定交给 playingListener
        videoEl.play().catch(() => {});
      });

      hls.on(Hls.Events.ERROR, (_evt, info) => {
        console.warn("[HLS ERROR]", info);
        if (info.fatal) {
          tryNextOrFail(info);
        }
      });
    } else if (videoEl.canPlayType("application/vnd.apple.mpegurl")) {
      videoEl.src = src;
      const onLoad = () => { videoEl.play().catch(() => {}); };
      const onErr  = () => tryNextOrFail({ type: "media", details: "native" });
      videoEl.addEventListener("loadedmetadata", onLoad, { once: true });
      videoEl.addEventListener("error",          onErr,  { once: true });
    } else {
      showMsg("当前浏览器不支持 HLS 播放", false);
    }
  }

  function tryNextOrFail(errInfo) {
    if (!currentChannel) return;
    autoFallbackTried += 1;
    const total = currentChannel.plays.length;
    if (currentSrcIdx + 1 < total && autoFallbackTried < total) {
      currentSrcIdx += 1;
      showMsg(`源 ${currentSrcIdx}/${total} 失败 (${errInfo.details || errInfo.type})，自动切换到 ${currentSrcIdx + 1}/${total} …`, false);
      // 稍微延后启动，给上一个 hls 销毁留时间
      setTimeout(startStream, 200);
    } else {
      showMsg(`所有 ${total} 个源都无法播放。可能为版权 / 地域限制 / 源失效。`, false);
      destroyHls();
    }
  }

  function switchSrc(delta) {
    if (!currentChannel) return;
    const next = currentSrcIdx + delta;
    if (next < 0 || next >= currentChannel.plays.length) return;
    currentSrcIdx = next;
    autoFallbackTried = 0;
    startStream();
  }

  async function verifyChannel() {
    if (!currentChannel) return;
    showMsg("正在并发探测所有源…", false);
    try {
      const r = await fetch(`/api/test_channel?name=${encodeURIComponent(currentChannel.name)}`);
      const d = await r.json();
      if (d.error) { showMsg("验证失败：" + d.error, false); return; }
      const ok = d.alive;
      const lines = d.results.map((x, i) =>
        `${x.ok ? "✓" : "✗"} 源${i + 1}: ${x.status}`
      ).join("  |  ");
      showMsg(`验证完成：${ok}/${d.total} 可用 | ${lines}`, false);
      // 把可用的源排到前面
      if (ok > 0 && ok < d.total) {
        const aliveIdx = d.results.map((x, i) => x.ok ? i : -1).filter(i => i >= 0);
        if (aliveIdx[0] !== undefined && aliveIdx[0] !== currentSrcIdx) {
          currentSrcIdx = aliveIdx[0];
          autoFallbackTried = 0;
          startStream();
        }
      }
    } catch (e) {
      showMsg("验证失败：" + e.message, false);
    }
  }

  function closePlayer() {
    destroyHls();
    playerEl.classList.add("hidden");
    exitFullscreen();
    currentChannel = null;
    currentSrcIdx = 0;
    autoFallbackTried = 0;
  }

  function enterFullscreen() {
    const el = playerEl;
    const fn = el.requestFullscreen || el.webkitRequestFullscreen || el.msRequestFullscreen;
    if (fn) fn.call(el).catch(() => {});
  }
  function exitFullscreen() {
    if (document.fullscreenElement || document.webkitFullscreenElement) {
      const fn = document.exitFullscreen || document.webkitExitFullscreen;
      if (fn) fn.call(document).catch(() => {});
    }
  }

  // -------------------------------------------------------------
  // Events
  // -------------------------------------------------------------
  searchEl.addEventListener("input", renderGrid);
  refreshEl.addEventListener("click", () => loadChannels(true));
  closeEl.addEventListener("click", closePlayer);
  reloadEl.addEventListener("click", () => { autoFallbackTried = 0; startStream(); });
  prevSrcEl.addEventListener("click", () => switchSrc(-1));
  nextSrcEl.addEventListener("click", () => switchSrc(+1));
  verifyEl.addEventListener("click", verifyChannel);

  document.addEventListener("keydown", (e) => {
    if (playerEl.classList.contains("hidden")) return;
    if (e.key === "Escape")    closePlayer();
    else if (e.key === "ArrowRight" && e.altKey) switchSrc(+1);
    else if (e.key === "ArrowLeft"  && e.altKey) switchSrc(-1);
  });
  document.addEventListener("fullscreenchange", () => {
    if (!document.fullscreenElement && !playerEl.classList.contains("hidden")) {
      closePlayer();
    }
  });

  // -------------------------------------------------------------
  loadChannels(false);
})();
