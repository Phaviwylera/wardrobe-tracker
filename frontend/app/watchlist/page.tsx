"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import Spark from "@/components/Spark";
import { api, money, type WatchRow } from "@/lib/api";

export default function Watchlist() {
  const [rows, setRows] = useState<WatchRow[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [url, setUrl] = useState("");
  const [adding, setAdding] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);

  const load = () => api<WatchRow[]>("/api/watchlist").then(setRows).catch((e) => setErr(e.message));
  useEffect(() => { load(); }, []);

  async function add(e: React.FormEvent) {
    e.preventDefault();
    setAdding(true);
    setMsg(null);
    try {
      await api("/api/products/track", { method: "POST", body: JSON.stringify({ url }) });
      setUrl("");
      setMsg("Added. Its price is now tracked.");
      load();
    } catch (er) {
      setMsg((er as Error).message);
    } finally {
      setAdding(false);
    }
  }

  return (
    <div className="split" style={{ gap: 36 }}>
      <div className="split" style={{ gap: 14 }}>
        <h1>Watchlist</h1>
        <p className="muted">Pieces you're tracking. Prices are rechecked every few hours; set a target on any item to be told when it drops.</p>
      </div>

      <form onSubmit={add} className="watch-form" style={{ gridTemplateColumns: "1fr auto", maxWidth: 760 }}>
        <div className="field">
          <label htmlFor="url">Track a product link</label>
          <input id="url" className="input" type="url" required placeholder="https://www.myntra.com/kurtas/…/buy"
            value={url} onChange={(e) => setUrl(e.target.value)} />
        </div>
        <button className="btn" disabled={adding}>{adding ? "Reading the page…" : "Track price"}</button>
      </form>
      {msg && <p className="small" role="status">{msg}</p>}

      {err && <p className="notice">{err}</p>}
      {rows && rows.length === 0 && (
        <div className="empty">
          <h2>Nothing tracked yet</h2>
          <p className="muted">Search with a photo and open any result's price history, or paste a product link above.</p>
          <div><Link className="btn" href="/">Search with a photo</Link></div>
        </div>
      )}
      {rows && rows.length > 0 && (
        <div className="rows">
          {rows.map((w) => {
            const p = w.product;
            return (
              <article className="row" key={p.id}>
                {p.image_url ? (
                  // eslint-disable-next-line @next/next/no-img-element
                  <img className="row-img" src={p.image_url} alt="" loading="lazy" referrerPolicy="no-referrer" />
                ) : <div className="row-img" />}
                <div className="row-main">
                  <div className="row-store">
                    <span>{p.store_name}</span>
                    {w.hit && <span className="best">Target reached</span>}
                    {p.in_stock === false && <span className="oos small">Out of stock</span>}
                  </div>
                  <Link className="row-title" href={`/product/?id=${p.id}`}>{p.title ?? p.domain}</Link>
                  <div className="row-meta">
                    {w.target_price ? <span>Target {money(w.target_price)}</span> : <span>No target set</span>}
                    {w.lowest != null && <span>Lowest seen {money(w.lowest)}</span>}
                  </div>
                </div>
                <div className="row-side">
                  <div className="price num">{money(p.current_price, p.currency)}</div>
                  <Spark points={w.spark} />
                  <div className="row-actions">
                    <Link className="btn quiet small" href={`/product/?id=${p.id}`}>Price history</Link>
                  </div>
                </div>
              </article>
            );
          })}
        </div>
      )}
    </div>
  );
}
