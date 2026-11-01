"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import PriceChart from "@/components/PriceChart";
import { ago, api, money, type ProductDetail } from "@/lib/api";

const VERDICT: Record<string, string> = {
  lowest: "Lowest price we've seen",
  good: "Below its usual price",
  fair: "Around its usual price",
  high: "Higher than usual",
  tracking: "Tracking started; history builds over the next days",
};

export default function ProductView({ id }: { id: string }) {
  const [d, setD] = useState<ProductDetail | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [target, setTarget] = useState("");
  const [email, setEmail] = useState("");
  const [saved, setSaved] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);

  const load = useCallback(async () => {
    try {
      const x = await api<ProductDetail>(`/api/products/${id}`);
      setD(x);
      if (x.watch) {
        setTarget(x.watch.target_price ? String(Math.round(x.watch.target_price)) : "");
        setEmail(x.watch.email ?? "");
      }
    } catch (e) {
      setErr((e as Error).message);
    }
  }, [id]);
  useEffect(() => { load(); }, [load]);

  if (err) return <div className="empty"><h2>Couldn't load this product</h2><p className="notice">{err}</p></div>;
  if (!d) return <div className="progress"><div className="stage">Loading…</div></div>;
  const p = d.product, st = d.stats;

  async function saveWatch(e: React.FormEvent) {
    e.preventDefault();
    try {
      await api(`/api/watchlist/${id}`, { method: "PUT", body: JSON.stringify({ target_price: target ? Number(target) : null, email: email || null }) });
      setSaved(target ? `Watching. We'll flag it when it's ${money(Number(target))} or less.` : "Added to your watchlist.");
      load();
    } catch (er) {
      setSaved((er as Error).message);
    }
  }
  async function unwatch() {
    await api(`/api/watchlist/${id}`, { method: "DELETE" });
    setSaved("Removed from your watchlist.");
    setTarget(""); setEmail("");
    load();
  }
  async function refresh() {
    setRefreshing(true);
    try {
      const r = await api<{ refreshed: boolean; reason?: string }>(`/api/products/${id}/refresh`, { method: "POST" });
      setSaved(r.refreshed ? "Price checked just now." : r.reason ? `Already ${r.reason}.` : "The store didn't return a price this time.");
      await load();
    } finally {
      setRefreshing(false);
    }
  }

  return (
    <div className="product">
      <div>
        {p.image_url ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img className="product-img" src={p.image_url} alt={p.title ?? ""} referrerPolicy="no-referrer" />
        ) : <div className="product-img" />}
      </div>

      <div className="split">
        <div className="product-head">
          <p className="muted">{p.store_name}{p.brand ? `, ${p.brand}` : ""}</p>
          <h2>{p.title ?? p.domain}</h2>
        </div>

        <div className="price-tag-wrap">
          <span className="tag-string" aria-hidden="true" />
          <div className="tag price-tag">
            <div className="price-big num">{money(p.current_price, p.currency)}</div>
            {p.mrp && p.discount_pct ? (
              <div><span className="mrp num">MRP {money(p.mrp)}</span> <span className="off">{p.discount_pct}% off</span></div>
            ) : null}
            {st.verdict && <span className={`verdict ${st.verdict}`}>{VERDICT[st.verdict]}</span>}
            {p.in_stock === false && <span className="oos">Out of stock right now</span>}
            <span className="small muted">Checked {ago(p.last_checked ?? st.updated)}</span>
          </div>
        </div>

        <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
          <a className="btn" href={p.url} target="_blank" rel="noopener noreferrer nofollow">Buy on {p.store_name}</a>
          <button className="btn quiet" onClick={refresh} disabled={refreshing}>{refreshing ? "Checking…" : "Check price now"}</button>
        </div>

        {p.sizes && p.sizes.length > 0 && (
          <div className="split" style={{ gap: 8 }}>
            <h3>Sizes</h3>
            <div className="sizes">
              {p.sizes.map((s) => <span key={s.label} className={`size${s.available === false ? " no" : ""}`} title={s.available === false ? "Sold out" : "Available"}>{s.label}</span>)}
            </div>
          </div>
        )}

        <div className="split" style={{ gap: 12 }}>
          <h3>Price history</h3>
          {st.count > 0 && (
            <dl className="stats num">
              <div><dt>Lowest</dt><dd>{money(st.lowest)}</dd></div>
              <div><dt>Average</dt><dd>{money(st.average)}</dd></div>
              <div><dt>Highest</dt><dd>{money(st.highest)}</dd></div>
            </dl>
          )}
          <PriceChart history={d.history} target={d.watch?.target_price} />
          <p className="small muted">
            {st.count} price {st.count === 1 ? "check" : "checks"} since {st.since ? new Date(st.since).toLocaleDateString("en-IN", { day: "numeric", month: "long", year: "numeric" }) : "today"}.
            Rechecked automatically every few hours.
          </p>
        </div>

        <form className="split" style={{ gap: 12 }} onSubmit={saveWatch}>
          <h3>{d.watch ? "You're watching this" : "Get told when it drops"}</h3>
          <div className="watch-form">
            <div className="field">
              <label htmlFor="target">Alert me at (₹)</label>
              <input id="target" className="input num" inputMode="numeric" placeholder={st.lowest ? String(Math.round(st.lowest)) : "Target price"}
                value={target} onChange={(e) => setTarget(e.target.value.replace(/\D/g, ""))} />
            </div>
            <div className="field">
              <label htmlFor="email">E-mail (optional)</label>
              <input id="email" type="email" className="input" placeholder="you@example.com" value={email} onChange={(e) => setEmail(e.target.value)} />
            </div>
            <button className="btn" type="submit">{d.watch ? "Update alert" : "Watch price"}</button>
          </div>
          {d.watch && <div><button type="button" className="btn quiet small" onClick={unwatch}>Stop watching</button></div>}
          {saved && <p className="small" role="status">{saved}</p>}
        </form>

        {d.other_stores.length > 0 && (
          <div className="split" style={{ gap: 4 }}>
            <h3>Same piece at other stores</h3>
            <div className="rows">
              {d.other_stores.map((o) => (
                <div className="row" key={o.id} style={{ gridTemplateColumns: "1fr auto auto" }}>
                  <Link href={`/product/?id=${o.id}`} className="row-store">{o.store_name}{o.in_stock === false ? <span className="oos small">Out of stock</span> : null}</Link>
                  <span className="price num" style={{ fontSize: "1.3rem" }}>{money(o.current_price)}</span>
                  <a className="btn small" href={o.url} target="_blank" rel="noopener noreferrer nofollow">Open</a>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
