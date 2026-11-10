// StyleTrail on Myntra: runs only on pages you open yourself.
(() => {
  const ACTIVE_HOURS = 6;
  const sent = new Set();
  const matched = new Map(); // product id -> level
  let searchId = null, checked = 0, timer = null, lastObserved = "";

  const idOf = (href) => (href.match(/\/(\d{6,})(?:\/buy)?\/?(?:[?#].*)?$/) || [])[1];
  const isProductPage = () => /\/\d{6,}(\/buy)?\/?$/.test(location.pathname);

  async function loadSearch() {
    const m = location.hash.match(/styletrail=([a-f0-9]{16,64})/i);
    if (m) {
      await chrome.storage.local.set({ active: { id: m[1], at: Date.now() } });
      history.replaceState(null, "", location.pathname + location.search);
    }
    const { active, paused } = await chrome.storage.local.get(["active", "paused"]);
    if (paused || !active) return null;
    if (Date.now() - active.at > ACTIVE_HOURS * 3600e3) return null;
    return active.id;
  }

  // ---------- floating panel ----------
  let panel;
  function render(note) {
    if (!searchId) return;
    if (!panel) {
      panel = document.createElement("div");
      panel.className = "stt-panel";
      document.body.appendChild(panel);
    }
    const exact = [...matched.values()].filter((l) => l === "exact").length;
    const close = [...matched.values()].filter((l) => l === "close").length;
    panel.innerHTML = "";
    const h = document.createElement("strong");
    h.textContent = "StyleTrail";
    const p = document.createElement("span");
    p.textContent = note || (exact ? `Found your piece (${exact} exact${close ? `, ${close} close` : ""})`
      : `Checked ${checked} products${close ? `, ${close} close` : ""}. Keep scrolling…`);
    panel.append(h, p);
    if (exact) {
      const b = document.createElement("button");
      b.textContent = "Show me";
      b.onclick = () => document.querySelector(".stt-hit-exact")?.scrollIntoView({ behavior: "smooth", block: "center" });
      panel.append(b);
    }
    chrome.runtime.sendMessage({ type: "server" }, (r) => {
      if (!r?.server) return;
      const a = document.createElement("a");
      a.href = `${r.server}/search/?id=${searchId}`;
      a.target = "_blank";
      a.rel = "noopener";
      a.textContent = "Results";
      panel.append(a);
    });
  }

  // ---------- grid tiles ----------
  function tiles() {
    const out = [];
    for (const a of document.querySelectorAll('a[href*="/buy"], a[href]')) {
      const href = a.href;
      const id = idOf(new URL(href, location.href).pathname);
      if (!id || sent.has(id)) continue;
      const tile = a.closest("li") || a;
      const img = tile.querySelector("img");
      const src = img && (img.currentSrc || img.src);
      if (!src || !/^https:\/\/assets\.myntassets\.com\//.test(src)) continue; // not loaded yet
      const text = (sel) => tile.querySelector(sel)?.textContent?.trim() || null;
      out.push({
        id, el: tile,
        url: `https://www.myntra.com/${id}`,
        title: [text(".product-brand"), text(".product-product")].filter(Boolean).join(" ") || img.alt || null,
        price: text(".product-discountedPrice") || text(".product-price"),
        mrp: text(".product-strike"),
        img: src.replace(/w_\d+/, "w_480").replace(/h_\d+,?/, ""),
      });
    }
    return out;
  }

  function scan() {
    if (!searchId) return;
    const batch = tiles().slice(0, 12);
    if (!batch.length) return;
    batch.forEach((t) => sent.add(t.id));
    chrome.runtime.sendMessage(
      { type: "match", searchId, tiles: batch.map(({ el, id, ...t }) => t) },
      (r) => {
        if (!r?.ok) { render(r?.error ? `Couldn't check: ${r.error}` : undefined); return; }
        checked += r.checked;
        for (const m of r.matches) {
          const id = idOf(m.url);
          matched.set(id, m.level);
          const t = batch.find((b) => b.id === id);
          if (t) {
            t.el.classList.add("stt-hit", `stt-hit-${m.level}`);
            t.el.setAttribute("data-stt", m.level === "exact" ? "Your piece" : "Close match");
          }
        }
        render();
        if (tiles().length) schedule(300);
      }
    );
  }

  function schedule(ms = 1200) {
    clearTimeout(timer);
    timer = setTimeout(() => (isProductPage() ? observeProduct() : scan()), ms);
  }

  // ---------- product page: price into history ----------
  function observeProduct() {
    const id = idOf(location.pathname);
    if (!id || lastObserved === id) return;
    const state = [...document.scripts].find((s) => s.textContent.includes("window.__myx"));
    const stateFresh = state && state.textContent.includes(`"id":${id}`);
    const ld = [...document.querySelectorAll('script[type="application/ld+json"]')].map((s) => s.outerHTML).join("");
    const text = (sel) => document.querySelector(sel)?.textContent?.trim() || null;
    const page = {
      url: `https://www.myntra.com/${id}`,
      state_script: stateFresh ? state.outerHTML : (ld || null),
      title: text(".pdp-name"),
      brand: text(".pdp-title"),
      price: text(".pdp-price strong") || text(".pdp-price"),
      mrp: text(".pdp-mrp s"),
      in_stock: document.querySelector(".size-buttons-out-of-stock, .pdp-out-of-stock") ? false : null,
    };
    if (!page.price && !page.state_script) return schedule(1500); // still rendering
    lastObserved = id;
    chrome.runtime.sendMessage({ type: "observe", page }, () => {});
  }

  (async () => {
    searchId = await loadSearch();
    let href = location.href;
    new MutationObserver(() => {
      if (location.href !== href) { href = location.href; lastObserved = ""; }
      if (searchId || isProductPage()) schedule();
    }).observe(document.body, { childList: true, subtree: true });
    window.addEventListener("scroll", () => searchId && schedule(800), { passive: true });
    if (searchId) render("Looking for your piece…");
    schedule(600);
  })();
})();
