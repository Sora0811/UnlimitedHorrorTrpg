/* Unlimited Horror TRPG 規則書入口網站
 *
 * 狀態表示方式（單一策略）：
 *   入口網址  index.html?page=<規則頁面相對路徑>#<頁內錨點>
 *   例：index.html?page=規則/本書說明/簡介/#section2
 * 沒有 page 參數時載入 DEFAULT_PAGE。
 */
(() => {
  'use strict';

  /* ===== 設定 ===== */
  const SITE_TITLE = 'Unlimited Horror TRPG';
  const DEFAULT_PAGE = '規則/本書說明/簡介/';   // 預設閱讀頁面（相對於 index.html 所在資料夾）
  const PAGE_PARAM = 'page';
  const AI_MIN = 0.2;
  const AI_MAX = 0.4;
  const AI_DEFAULT = 0.25;

  /* ===== 元素 ===== */
  const $ = (sel) => document.querySelector(sel);
  const body = document.body;
  const navFrame = $('#nav-frame');
  const readerFrame = $('#reader-frame');
  const navBtn = $('#nav-toggle');
  const aiBtn = $('#ai-toggle');
  const aiClose = $('#ai-close');
  const contentArea = $('#content-area');
  const resizer = $('#ai-resizer');
  const backdrop = $('#backdrop');
  const notice = $('#notice');
  const searchForm = $('#search-form');
  const searchInput = $('#search-input');
  const searchPanel = $('#search-panel');
  const searchMessage = $('#search-message');

  const desktopMQ = window.matchMedia('(min-width: 1025px)');
  const isDesktop = () => desktopMQ.matches;

  /* 入口網站所在資料夾；GitHub Pages 子路徑（/repo/）也適用 */
  const BASE = new URL('.', location.href);

  /* ===== 路徑工具 ===== */

  /**
   * 相對路徑是否為合法的規則頁面。
   * 現在的條目是「資料夾 + index.html」，網址形如 資源/血統/人類/禍神血脈/
   * 因此同時接受目錄式網址與傳統 .htm 檔名。
   */
  function isValidPage(rel) {
    if (!rel || rel.startsWith('/') || rel.includes('\\')) return false;
    const segs = rel.split('/').filter((s) => s !== '');
    if (segs.length === 0) return false;
    if (segs.some((seg) => seg === '.' || seg === '..')) return false;

    // 目錄式網址：結尾斜線，或整段不含副檔名
    const isDirStyle = rel.endsWith('/') || !/\.[a-z0-9]+$/i.test(rel);
    if (isDirStyle) {
      // 排除入口網站自己
      return !(segs.length === 1 && segs[0] === 'index.html');
    }

    // 檔案式網址（保留舊連結相容）
    if (!/\.html?$/i.test(rel)) return false;
    if (/^index\.html?$/i.test(rel) && segs.length === 1) return false;
    return true;
  }

  /** 絕對 URL → 規則頁面相對路徑（已解碼）；不屬於本站規則頁面則回傳 null */
  function toPage(url) {
    if (url.origin !== BASE.origin || !url.pathname.startsWith(BASE.pathname)) return null;
    let rel;
    try {
      rel = decodeURIComponent(url.pathname.slice(BASE.pathname.length));
    } catch (_) {
      return null;
    }
    // 入口網站本身不是規則頁面
    if (rel === '' || /^index\.html?$/i.test(rel)) return null;
    return isValidPage(rel) ? rel : null;
  }

  /** 規則頁面相對路徑 → 絕對 URL（逐段編碼，中文檔名安全） */
  function pageUrl(page, hash) {
    const url = new URL(page.split('/').map(encodeURIComponent).join('/'), BASE);
    if (hash) url.hash = hash;
    return url.href;
  }

  /** 入口網站自己的網址 */
  function portalUrl(page, hash) {
    const q = '?' + PAGE_PARAM + '=' + page.split('/').map(encodeURIComponent).join('/');
    return location.pathname + q + (hash ? '#' + hash : '');
  }

  /** 由目前網址解析出要顯示的頁面 */
  function readLocation() {
    const p = new URLSearchParams(location.search).get(PAGE_PARAM);
    const hash = location.hash.slice(1);
    if (p && isValidPage(p)) return { page: p, hash };
    return { page: DEFAULT_PAGE, hash: p ? '' : hash };
  }

  /* ===== 訊息列 ===== */

  function showNotice(msg) {
    notice.textContent = msg;
    notice.hidden = false;
  }

  /* ===== 閱讀頁面狀態與歷史紀錄 ===== */

  let cur = readLocation();

  function historyCall(method, state, url) {
    try {
      history[method](state, '', url);
    } catch (_) {
      /* file:// 等環境可能不允許，略過 */
    }
  }

  /** 載入閱讀區。使用 location.replace，避免 iframe 自己多寫一筆瀏覽歷史。 */
  function loadReader(page, hash) {
    const url = pageUrl(page, hash);
    try {
      readerFrame.contentWindow.location.replace(url);
    } catch (_) {
      readerFrame.src = url;
    }
  }

  /** mode: 'push' 新增歷史紀錄；'replace' 取代目前紀錄 */
  function navigate(page, hash, mode) {
    const same = page === cur.page && (hash || '') === (cur.hash || '');
    cur = { page, hash: hash || '' };
    historyCall(same || mode === 'replace' ? 'replaceState' : 'pushState', cur, portalUrl(page, hash));
    loadReader(page, hash);
  }

  window.addEventListener('popstate', () => {
    cur = readLocation();
    loadReader(cur.page, cur.hash);
  });

  /* ===== 頁內錨點 ===== */

  function scrollReaderToHash(hash) {
    try {
      const doc = readerFrame.contentDocument;
      const id = decodeURIComponent(hash);
      const el = doc.getElementById(id) || doc.getElementsByName(id)[0];
      if (el) el.scrollIntoView();
      else if (!id || id.toLowerCase() === 'top') readerFrame.contentWindow.scrollTo(0, 0);
    } catch (_) { /* 略過 */ }
  }

  /* ===== 連結接管（導航 iframe 與閱讀 iframe 共用） ===== */

  const hooked = new WeakSet();

  function onLinkClick(e, source) {
    if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    const a = e.target && e.target.closest ? e.target.closest('a[href]') : null;
    if (!a) return;

    const raw = (a.getAttribute('href') || '').trim();
    if (!raw || raw === '#' || /^javascript:/i.test(raw)) return; // 交給原本的 script 處理

    let url;
    try {
      url = new URL(a.href);
    } catch (_) {
      return;
    }
    if (url.protocol !== 'http:' && url.protocol !== 'https:' && url.protocol !== 'file:') return; // mailto: 等

    const page = toPage(url);

    if (page === null) {
      // 外部連結：以新分頁開啟，不載入入口網站的 iframe 裡
      if (url.origin !== BASE.origin && url.protocol !== 'file:') {
        e.preventDefault();
        window.open(url.href, '_blank', 'noopener');
      }
      return; // 本站的圖片、PDF 等非 HTML 檔：維持原行為
    }

    e.preventDefault();
    const hash = url.hash.slice(1);

    if (page === cur.page && hash) {
      // 同頁錨點：只捲動，不重新載入、不新增歷史
      scrollReaderToHash(hash);
      cur = { page, hash };
      historyCall('replaceState', cur, portalUrl(page, hash));
    } else {
      navigate(page, hash, 'push');
    }

    if (source === 'nav' && !isDesktop()) closeOverlays();
  }

  function hookFrame(frame, source) {
    let doc = null;
    try {
      doc = frame.contentDocument;
    } catch (_) { /* 跨來源 */ }

    if (!doc) {
      showNotice(
        location.protocol === 'file:'
          ? '目前以檔案方式開啟，瀏覽器會阻擋入口網站與規則頁面互通。請改用本機伺服器（例如 python -m http.server）或部署到 GitHub Pages。'
          : '無法存取目錄或閱讀區的內容，導航轉送可能無法運作。'
      );
      return null;
    }
    if (!doc.location || doc.location.href === 'about:blank') return null;

    if (!hooked.has(doc)) {
      hooked.add(doc);
      doc.addEventListener('click', (e) => onLinkClick(e, source), true);
    }
    return doc;
  }

  navFrame.addEventListener('load', () => {
    const doc = hookFrame(navFrame, 'nav');
    if (doc && doc.querySelector('frameset')) {
      showNotice('left.htm 是 frameset 結構，入口網站無法接管其中的連結。');
    }
  });

  readerFrame.addEventListener('load', () => {
    const doc = hookFrame(readerFrame, 'reader');
    if (!doc) return;

    let loc;
    try {
      loc = new URL(readerFrame.contentWindow.location.href);
    } catch (_) {
      return;
    }

    const page = toPage(loc);
    if (page) {
      // 若閱讀頁是被規則頁面自己的 script 導向別頁，同步入口網址
      if (page !== cur.page) {
        cur = { page, hash: loc.hash.slice(1) };
        historyCall('pushState', cur, portalUrl(cur.page, cur.hash));
      }
    }

    const t = (doc.title || '').trim();
    document.title = t ? t + ' - ' + SITE_TITLE : SITE_TITLE;
  });

  /* ===== 目錄與 AI 面板開關 ===== */

  function setNav(open) {
    body.dataset.nav = open ? 'open' : 'closed';
    navBtn.setAttribute('aria-expanded', String(open));
  }

  function setAi(open) {
    body.dataset.ai = open ? 'open' : 'closed';
    aiBtn.setAttribute('aria-expanded', String(open));
  }

  function closeOverlays() {
    if (!isDesktop()) {
      setNav(false);
      setAi(false);
    }
  }

  navBtn.addEventListener('click', () => {
    const open = body.dataset.nav !== 'open';
    setNav(open);
    if (open && !isDesktop()) setAi(false);
  });

  aiBtn.addEventListener('click', () => {
    const open = body.dataset.ai !== 'open';
    setAi(open);
    if (open && !isDesktop()) setNav(false);
  });

  aiClose.addEventListener('click', () => setAi(false));
  backdrop.addEventListener('click', closeOverlays);

  desktopMQ.addEventListener('change', () => {
    setNav(isDesktop());      // 桌面預設展開；其他尺寸預設收起
    if (!isDesktop()) setAi(false);
  });

  /* ===== AI 面板寬度調整（桌面） ===== */

  let aiFrac = AI_DEFAULT;

  function setAiFrac(frac) {
    aiFrac = Math.min(AI_MAX, Math.max(AI_MIN, frac));
    contentArea.style.setProperty('--ai-frac', String(aiFrac));
    resizer.setAttribute('aria-valuenow', String(Math.round(aiFrac * 100)));
  }

  resizer.addEventListener('pointerdown', (e) => {
    if (!isDesktop()) return;
    resizer.setPointerCapture(e.pointerId);
    body.classList.add('dragging');
    e.preventDefault();
  });

  resizer.addEventListener('pointermove', (e) => {
    if (!resizer.hasPointerCapture(e.pointerId)) return;
    const rect = contentArea.getBoundingClientRect();
    setAiFrac((rect.right - e.clientX) / rect.width);
  });

  const endDrag = (e) => {
    if (resizer.hasPointerCapture(e.pointerId)) resizer.releasePointerCapture(e.pointerId);
    body.classList.remove('dragging');
  };
  resizer.addEventListener('pointerup', endDrag);
  resizer.addEventListener('pointercancel', endDrag);

  resizer.addEventListener('keydown', (e) => {
    if (e.key === 'ArrowLeft') { setAiFrac(aiFrac + 0.01); e.preventDefault(); }
    if (e.key === 'ArrowRight') { setAiFrac(aiFrac - 0.01); e.preventDefault(); }
  });

  /* ===== 搜尋（只有介面，尚未實作） ===== */

  searchForm.addEventListener('submit', (e) => {
    e.preventDefault();
    searchMessage.textContent = '搜尋功能尚未開放。';
    searchPanel.hidden = false;
  });

  document.addEventListener('click', (e) => {
    if (!searchPanel.hidden && !searchForm.contains(e.target)) searchPanel.hidden = true;
  });

  document.addEventListener('keydown', (e) => {
    if (e.key !== 'Escape') return;
    if (!searchPanel.hidden) {
      searchPanel.hidden = true;
    } else {
      closeOverlays();
    }
  });

  /* ===== 初始化 ===== */

  setAiFrac(AI_DEFAULT);
  setNav(isDesktop());
  setAi(false);

  historyCall('replaceState', cur, location.search || location.hash ? portalUrl(cur.page, cur.hash) : location.pathname);
  loadReader(cur.page, cur.hash);

  // 導航 iframe 可能在 script 執行前就已載入完成
  try {
    if (navFrame.contentDocument && navFrame.contentDocument.readyState === 'complete') {
      hookFrame(navFrame, 'nav');
    }
  } catch (_) {
    hookFrame(navFrame, 'nav');
  }
})();
