// StyleTrail Finder: a bookmark that runs on a store page in the shopper's own browser (phone or computer).
// It reads the product tiles the page is showing, asks StyleTrail (through /bridge.html) which ones match the
// search photo, and highlights them. On a product page it saves the shown price to the price history.
// Written without // comments because it becomes a single-line javascript: URL.
const SOURCE = String.raw`
(function () {
  if (window.__stt) { window.__stt.panel.style.display = "flex"; window.__stt.scan(); return; }
  var O = "__ORIGIN__";
  var sent = {}, hits = {}, checked = 0, ready = false, queue = [], peer = null, frame = null, popup = null;
  var sid = null, started = Date.now(), lastObserved = "";
  var m = location.hash.match(/styletrail=([a-f0-9]{16,64})/i);
  try {
    if (m) localStorage.setItem("styletrail:sid", m[1]);
    sid = m ? m[1] : localStorage.getItem("styletrail:sid");
  } catch (e) { sid = m ? m[1] : null; }

  var css = document.createElement("style");
  css.textContent = ".stt-hit{position:relative;outline:3px solid #c8215f!important;outline-offset:2px;border-radius:4px}" +
    ".stt-hit-close{outline-color:#1e2350!important;outline-style:dashed!important}" +
    ".stt-hit:before{content:attr(data-stt);position:absolute;z-index:5;top:8px;left:8px;background:#c8215f;color:#fff;font:600 12px/1 system-ui,sans-serif;padding:6px 8px;border-radius:3px}" +
    ".stt-hit-close:before{background:#1e2350}" +
    ".stt-panel{position:fixed;z-index:2147483000;left:12px;right:12px;bottom:12px;max-width:360px;margin-left:auto;display:flex;flex-wrap:wrap;gap:6px 10px;align-items:center;background:#fbf8f3;color:#1e2350;border:1.5px solid #1e2350;border-radius:8px;padding:10px 12px;font:14px/1.35 system-ui,sans-serif;box-shadow:0 6px 24px rgba(30,35,80,.2)}" +
    ".stt-panel strong{color:#c8215f}.stt-panel span{flex:1 1 100%}" +
    ".stt-panel button,.stt-panel a{font:600 13px system-ui,sans-serif;color:#fbf8f3;background:#1e2350;border:0;border-radius:5px;padding:8px 12px;text-decoration:none;cursor:pointer}" +
    ".stt-panel .q{background:transparent;color:#1e2350;box-shadow:inset 0 0 0 1.5px #1e2350}";
  document.head.appendChild(css);
  var panel = document.createElement("div");
  panel.className = "stt-panel";
  document.body.appendChild(panel);

  function btn(label, fn, quiet) {
    var b = document.createElement("button"); b.textContent = label; b.onclick = fn; if (quiet) b.className = "q"; return b;
  }
  function say(text, extra) {
    panel.innerHTML = "";
    var h = document.createElement("strong"); h.textContent = "StyleTrail";
    var s = document.createElement("span"); s.textContent = text;
    panel.append(h, s);
    (extra || []).forEach(function (x) { panel.append(x); });
    var exact = document.querySelector(".stt-hit-exact");
    if (exact) panel.append(btn("Show me", function () { exact.scrollIntoView({ behavior: "smooth", block: "center" }); }));
    if (sid) { var a = document.createElement("a"); a.href = O + "/search/?id=" + sid; a.target = "_blank"; a.textContent = "Results"; a.className = "q"; panel.append(a); }
    panel.append(btn("Close", function () { panel.style.display = "none"; }, true));
  }
  function status() {
    var e = 0, c = 0;
    for (var k in hits) { if (hits[k] === "exact") e++; else c++; }
    if (e) say("Found your piece: " + e + " exact" + (c ? ", " + c + " close" : "") + ".");
    else say("Checked " + checked + " products" + (c ? ", " + c + " close" : "") + ". Keep scrolling…");
  }

  function idOf(path) { var r = path.match(/\/(\d{6,})(?:\/buy)?\/?$/); return r && r[1]; }
  function productPage() { return /\/\d{6,}(\/buy)?\/?$/.test(location.pathname); }

  function post(msg) { if (ready && peer) peer.postMessage(msg, O); else queue.push(msg); }
  window.addEventListener("message", function (e) {
    if (e.origin !== O) return;
    var d = e.data || {};
    if (d.type === "stt-ready") {
      ready = true; peer = e.source; clearTimeout(fallback);
      queue.splice(0).forEach(post);
      if (productPage()) say("Saving this price to StyleTrail…"); else if (sid) say("Looking for your piece…");
      tick();
    } else if (d.type === "stt-result") {
      sid = d.sid || sid; checked += d.checked;
      d.matches.forEach(function (x) { var id = idOf(new URL(x.url).pathname); hits[id] = x.level; mark(id, x.level); });
      status();
    } else if (d.type === "stt-observed") {
      say(d.price ? "Saved ₹" + Math.round(d.price) + " to this product's price history." : "Couldn't read the price on this page.");
    } else if (d.type === "stt-error") {
      say(d.error);
    }
  });

  frame = document.createElement("iframe");
  frame.src = O + "/bridge.html" + (sid ? "?sid=" + sid : "");
  frame.style.cssText = "position:fixed;width:1px;height:1px;opacity:0;border:0;right:0;bottom:0;pointer-events:none";
  document.body.appendChild(frame);
  var fallback = setTimeout(function () {
    frame.remove();
    say("This page blocks the quiet connection. Tap Connect: StyleTrail opens in a new tab, then come back here.",
      [btn("Connect", function () { popup = window.open(O + "/bridge.html" + (sid ? "?sid=" + sid : ""), "styletrail-bridge"); })]);
  }, 4000);
  say("Connecting…");

  function tiles() {
    var out = [], links = document.querySelectorAll("a[href]");
    for (var i = 0; i < links.length && out.length < 12; i++) {
      var a = links[i], id;
      try { id = idOf(new URL(a.href, location.href).pathname); } catch (e) { id = null; }
      if (!id || sent[id]) continue;
      var tile = a.closest("li") || a;
      var img = tile.querySelector("img");
      var src = img && (img.currentSrc || img.src);
      if (!src || !/^https:\/\/assets\.myntassets\.com\//.test(src)) continue;
      var r = tile.getBoundingClientRect();
      if (r.bottom < -400 || r.top > innerHeight + 1200) continue;
      var t = function (sel) { var n = tile.querySelector(sel); return n ? n.textContent.trim() : null; };
      out.push({
        id: id, url: "https://www.myntra.com/" + id,
        title: [t(".product-brand"), t(".product-product")].filter(Boolean).join(" ") || img.alt || null,
        price: t(".product-discountedPrice") || t(".product-price"), mrp: t(".product-strike"),
        image_url: src.replace(/w_\d+/, "w_480").replace(/h_\d+,?/, "")
      });
    }
    return out;
  }
  function mark(id, level) {
    var as = document.querySelectorAll('a[href*="' + id + '"]');
    for (var i = 0; i < as.length; i++) {
      var el = as[i].closest("li") || as[i];
      el.classList.add("stt-hit", "stt-hit-" + level);
      el.setAttribute("data-stt", level === "exact" ? "Your piece" : "Close match");
    }
  }
  function observe() {
    var id = idOf(location.pathname);
    if (!id || lastObserved === id) return;
    var state = null, ss = document.scripts;
    for (var i = 0; i < ss.length; i++) if (ss[i].textContent.indexOf("window.__myx") >= 0 && ss[i].textContent.indexOf('"id":' + id) >= 0) state = ss[i].outerHTML;
    var q = function (sel) { var n = document.querySelector(sel); return n ? n.textContent.trim() : null; };
    var page = { url: "https://www.myntra.com/" + id, state_script: state, title: q(".pdp-name"), brand: q(".pdp-title"),
      price: q(".pdp-price strong") || q(".pdp-price"), mrp: q(".pdp-mrp s") };
    if (!page.price && !state) return;
    lastObserved = id;
    post({ type: "stt-observe", page: page });
  }
  function scan() {
    if (productPage()) return observe();
    if (!sid) { say("Open a Myntra link from your StyleTrail results first (the Spotted on list), then tap Finder again."); return; }
    var batch = tiles();
    if (!batch.length) return;
    batch.forEach(function (t) { sent[t.id] = 1; });
    post({ type: "stt-tiles", sid: sid, tiles: batch.map(function (t) { return { url: t.url, title: t.title, price: t.price, mrp: t.mrp, image_url: t.image_url }; }) });
  }
  function tick() { if (Date.now() - started < 20 * 60e3) { scan(); setTimeout(tick, 1500); } }
  window.__stt = { panel: panel, scan: scan };
})();
`;

export function finderBookmarklet(origin: string): string {
  const code = SOURCE.replace("__ORIGIN__", origin)
    .split("\n").map((l) => l.trim()).filter(Boolean).join(" ");
  return "javascript:" + encodeURIComponent(code);
}
