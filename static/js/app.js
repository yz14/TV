/* ============================================================
 * OldTV frontend
 * ------------------------------------------------------------
 * 职责:
 *   1) /api/channels -> 分组频道（含多个备用 plays[]）
 *   2) 网格 UI + 分类 tab + 搜索
 *   3) 点击 -> 复古电视机内播放
 *   4) 失败时按 plays 顺序自动切源；UI 显示 "源 i/N" + 手动切换按钮
 *   5) "验证" 按钮调用 /api/test_channel 进行后端探活
 *   6) 换台：在当前分组(或搜索结果)中切换上/下一个频道；显示频道号
 *   7) 多套复古电视机主题切换（PANDA / CHANGHONG / TRINITRON / CLASSIC）
 * ============================================================ */

(function () {
  "use strict";

  const $ = (sel) => document.querySelector(sel);
  const $$ = (sel) => Array.from(document.querySelectorAll(sel));

  // ----- top / grid 元素 -----
  const tabsEl    = $("#tabs");
  const gridEl    = $("#grid");
  const searchEl  = $("#search");
  const refreshEl = $("#refreshBtn");

  // ----- player 元素 -----
  const playerEl   = $("#player");
  const screenEl   = document.querySelector(".tv-screen");
  const videoEl    = $("#video");
  const nowNameEl  = $("#nowName");
  // 默认 OSD 渲染目标重置为 "--"（防止所有侧栏初始状态不一致）
  if (nowNameEl) nowNameEl.textContent = "--";
  const closeEl    = $("#closePlayer");
  const exitEl     = $("#exitPlayer");
  const reloadEl   = $("#reloadStream");
  const prevSrcEl  = $("#prevSrc");
  const nextSrcEl  = $("#nextSrc");
  const prevChEl   = $("#prevCh");
  const nextChEl   = $("#nextCh");
  const chNumEl    = $("#chNum");
  const srcInfoEl  = $("#srcInfo");
  const verifyEl   = $("#verifyBtn");
  const msgEl      = $("#streamMsg");

  /** @type {{groups: Record<string, Array<{name,group,logo,plays:string[]}>>, total:number}} */
  let data = { groups: {}, total: 0 };
  let activeGroup = null;
  let hls = null;
  let currentChannel = null;
  let currentSrcIdx = 0;
  let autoFallbackTried = 0;
  let playingListener = null;

  // 换台所需：当前播放队列 + 在队列中的索引
  // 队列 = 当前活动 tab 的频道列表 (有搜索时是搜索结果)
  let currentPlaylist = [];
  let currentChannelIdx = -1;

  // -------------------------------------------------------------
  // 主题切换
  // -------------------------------------------------------------
  const THEME_KEY = "oldtv.theme";
  const VALID_THEMES = ["classic", "panda", "changhong", "trinitron"];

  function applyTheme(name) {
    if (!VALID_THEMES.includes(name)) name = "panda";
    document.body.setAttribute("data-tv-theme", name);
    $$(".theme-btn").forEach(btn => {
      btn.classList.toggle("active", btn.dataset.theme === name);
    });
    try { localStorage.setItem(THEME_KEY, name); } catch (e) {}
  }

  function initTheme() {
    let saved = "panda";
    try { saved = localStorage.getItem(THEME_KEY) || "panda"; } catch (e) {}
    applyTheme(saved);
    $$(".theme-btn").forEach(btn => {
      btn.addEventListener("click", () => applyTheme(btn.dataset.theme));
    });
  }

  // -------------------------------------------------------------
  // CRT 特效切换引擎
  // -------------------------------------------------------------
  const FX_KEY = "oldtv.fx";
  const VALID_FX = ["none", "crt", "scanlines", "phosphor", "snow", "vcr"];

  function applyFx(name) {
    if (!VALID_FX.includes(name)) name = "crt";
    document.body.setAttribute("data-tv-fx", name);
    $$(".fx-btn").forEach(btn => {
      btn.classList.toggle("active", btn.dataset.fx === name);
    });
    try { localStorage.setItem(FX_KEY, name); } catch (e) {}
  }

  function initFx() {
    let saved = "crt";
    try { saved = localStorage.getItem(FX_KEY) || "crt"; } catch (e) {}
    applyFx(saved);
    $$(".fx-btn").forEach(btn => {
      btn.addEventListener("click", () => applyFx(btn.dataset.fx));
    });
  }

  // -------------------------------------------------------------
  // 视频宽高比自适应：让画面正好嵌入电视屏幕，无内部黑边
  // -------------------------------------------------------------
  const DEFAULT_ASPECT_W = 4;
  const DEFAULT_ASPECT_H = 3;

  function setScreenAspect(w, h) {
    if (!screenEl) return;
    const aw = Number(w) > 0 ? Number(w) : DEFAULT_ASPECT_W;
    const ah = Number(h) > 0 ? Number(h) : DEFAULT_ASPECT_H;
    screenEl.style.setProperty("--vid-w", String(aw));
    screenEl.style.setProperty("--vid-h", String(ah));
  }

  function resetScreenAspect() {
    setScreenAspect(DEFAULT_ASPECT_W, DEFAULT_ASPECT_H);
  }

  function onVideoMetadata() {
    const w = videoEl.videoWidth, h = videoEl.videoHeight;
    if (w > 0 && h > 0) {
      setScreenAspect(w, h);
      console.debug("[Player] video aspect:", w, "x", h, `(${(w/h).toFixed(3)})`);
    }
  }
  videoEl.addEventListener("loadedmetadata", onVideoMetadata);
  videoEl.addEventListener("resize", onVideoMetadata);

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

  /** 计算当前 UI 显示的频道列表（用作换台队列） */
  function computeVisibleList() {
    const q = searchEl.value.trim().toLowerCase();
    if (q) {
      const list = [];
      for (const g of Object.keys(data.groups)) {
        for (const ch of data.groups[g]) {
          if (ch.name.toLowerCase().includes(q)) list.push(ch);
        }
      }
      return list;
    }
    return (data.groups[activeGroup] || []).slice();
  }

  function renderGrid() {
    const list = computeVisibleList();

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
    if (!msgEl) return;
    msgEl.textContent = text;
    msgEl.classList.add("show");
    if (autohide) {
      clearTimeout(showMsg._t);
      showMsg._t = setTimeout(() => msgEl.classList.remove("show"), 5000);
    }
  }
  function hideMsg() { if (msgEl) msgEl.classList.remove("show"); }

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

  /** 进入播放 (从频道卡片点击进入) */
  function play(ch) {
    // 记录当前换台队列
    currentPlaylist = computeVisibleList();
    currentChannelIdx = currentPlaylist.findIndex(c => c === ch || c.name === ch.name);
    if (currentChannelIdx < 0) {
      // 兜底：直接放入队列
      currentPlaylist = [ch];
      currentChannelIdx = 0;
    }
    playerEl.classList.remove("hidden");
    document.body.style.overflow = "hidden";
    bootChannel(ch);
  }

  /** 切到 currentPlaylist 中某频道并开播 */
  function bootChannel(ch) {
    currentChannel = ch;
    currentSrcIdx = 0;
    autoFallbackTried = 0;
    nowNameEl.textContent = ch.name;
    updateChDisplay();
    updateChBtnState();
    startStream();
  }

  function updateChDisplay() {
    if (!chNumEl) return;
    // 显示 1-based 频道号，取自当前 playlist 中的位置
    if (currentChannelIdx < 0 || !currentPlaylist.length) {
      chNumEl.textContent = "--";
      return;
    }
    const n = currentChannelIdx + 1;
    chNumEl.textContent = (n < 10 ? "0" : "") + String(n);
  }

  function updateChBtnState() {
    if (!prevChEl || !nextChEl) return;
    const has = currentPlaylist.length > 1;
    prevChEl.disabled = !has;
    nextChEl.disabled = !has;
  }

  function updateSrcInfo() {
    if (!currentChannel) {
      srcInfoEl.textContent = "--";
      return;
    }
    srcInfoEl.textContent = `${currentSrcIdx + 1}/${currentChannel.plays.length}`;
    if (nowNameEl) {
      nowNameEl.title = currentChannel.name || "";
    }
    prevSrcEl.disabled = currentSrcIdx <= 0;
    nextSrcEl.disabled = currentSrcIdx >= currentChannel.plays.length - 1;
  }

  /** OSD 已迁出屏幕、始终可见于侧栏；保留函数 stub 不破坏调用点 */
  function flashOSD(/* ms */) { /* no-op: OSD 现居于侧栏，始终显示 */ }

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
    // 切源时暂时重置屏幕为默认 4:3，等新流加载后再根据真实宽高比调整
    resetScreenAspect();
    showMsg(`正在连接源 ${currentSrcIdx + 1}/${currentChannel.plays.length}…`);

    playingListener = function () {
      playingListener = null;
      if (videoEl.videoWidth === 0 && videoEl.videoHeight === 0) {
        console.warn("[Player] audio-only stream detected (videoWidth=0), fallback");
        tryNextOrFail({ type: "media", details: "audio-only" });
        return;
      }
      hideMsg();
      autoFallbackTried = 0;
    };
    videoEl.addEventListener("playing", playingListener, { once: true });

    if (window.Hls && Hls.isSupported()) {
      hls = new Hls({
        lowLatencyMode: true,
        maxBufferLength: 30,
        manifestLoadingTimeOut: 8000,
        manifestLoadingMaxRetry: 1,
        levelLoadingTimeOut: 8000,
        fragLoadingTimeOut: 15000,
      });
      hls.loadSource(src);
      hls.attachMedia(videoEl);

      hls.on(Hls.Events.MANIFEST_PARSED, () => {
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

  /** 换台：在当前 playlist 中循环切换 */
  function switchChannel(delta) {
    if (!currentPlaylist.length) return;
    const n = currentPlaylist.length;
    currentChannelIdx = ((currentChannelIdx + delta) % n + n) % n;
    bootChannel(currentPlaylist[currentChannelIdx]);
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
    document.body.style.overflow = "";
    currentChannel = null;
    currentSrcIdx = 0;
    autoFallbackTried = 0;
    currentPlaylist = [];
    currentChannelIdx = -1;
    if (chNumEl)   chNumEl.textContent   = "--";
    if (nowNameEl) nowNameEl.textContent = "--";
    if (srcInfoEl) srcInfoEl.textContent = "--";
    resetScreenAspect();
    hideMsg();
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
  if (exitEl) exitEl.addEventListener("click", closePlayer);
  reloadEl.addEventListener("click", () => { autoFallbackTried = 0; startStream(); });
  prevSrcEl.addEventListener("click", () => switchSrc(-1));
  nextSrcEl.addEventListener("click", () => switchSrc(+1));
  if (prevChEl) prevChEl.addEventListener("click", () => switchChannel(-1));
  if (nextChEl) nextChEl.addEventListener("click", () => switchChannel(+1));
  verifyEl.addEventListener("click", verifyChannel);

  document.addEventListener("keydown", (e) => {
    if (playerEl.classList.contains("hidden")) return;
    // 在 input 中不拦截
    const tag = (e.target && e.target.tagName) || "";
    if (tag === "INPUT" || tag === "TEXTAREA") return;

    if (e.key === "Escape") {
      closePlayer();
    } else if (e.key === "ArrowRight" && e.altKey) {
      switchSrc(+1);
    } else if (e.key === "ArrowLeft"  && e.altKey) {
      switchSrc(-1);
    } else if (e.key === "ArrowUp" || (e.key === "ArrowRight" && !e.altKey)) {
      e.preventDefault();
      switchChannel(+1);
    } else if (e.key === "ArrowDown" || (e.key === "ArrowLeft" && !e.altKey)) {
      e.preventDefault();
      switchChannel(-1);
    }
  });

  // -------------------------------------------------------------
  initTheme();
  initFx();
  resetScreenAspect();
  loadChannels(false);
})();
