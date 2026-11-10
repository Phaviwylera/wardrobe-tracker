// Talks to the StyleTrail server and fetches tile photos (the page itself can't, because of CORS).
const DEFAULT_SERVER = "https://d33xenhft9gbmu.cloudfront.net";

async function server() {
  const { server } = await chrome.storage.local.get("server");
  return (server || DEFAULT_SERVER).replace(/\/+$/, "");
}

async function toBase64(url) {
  const r = await fetch(url, { credentials: "omit" });
  if (!r.ok) throw new Error("image " + r.status);
  const buf = new Uint8Array(await r.arrayBuffer());
  let bin = "";
  for (let i = 0; i < buf.length; i += 0x8000) bin += String.fromCharCode.apply(null, buf.subarray(i, i + 0x8000));
  return btoa(bin);
}

async function post(path, body) {
  const r = await fetch((await server()) + path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.detail || "server " + r.status);
  return data;
}

chrome.runtime.onMessage.addListener((msg, _sender, reply) => {
  (async () => {
    try {
      if (msg.type === "match") {
        const tiles = [];
        for (const t of msg.tiles) {
          try {
            tiles.push({ url: t.url, title: t.title, price: t.price, mrp: t.mrp, image_b64: await toBase64(t.img) });
          } catch (_) { /* skip tiles whose photo didn't load */ }
        }
        if (!tiles.length) return reply({ ok: true, checked: 0, matches: [] });
        reply({ ok: true, ...(await post("/api/ext/match", { search_id: msg.searchId, tiles })) });
      } else if (msg.type === "observe") {
        reply({ ok: true, ...(await post("/api/ext/observe", msg.page)) });
      } else if (msg.type === "server") {
        reply({ ok: true, server: await server() });
      }
    } catch (e) {
      reply({ ok: false, error: String(e.message || e) });
    }
  })();
  return true; // async reply
});
