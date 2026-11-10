"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import ResultRow from "@/components/ResultRow";
import { api, imageUrl, type MatchLevel, type Result, type SearchState } from "@/lib/api";

type Sort = "match" | "price" | "discount";
const GROUPS: { level: MatchLevel; title: string; note: string }[] = [
  { level: "exact", title: "The same piece", note: "Print, cut and colour match your photo." },
  { level: "close", title: "Very close", note: "Likely the same design; check colour or details." },
  { level: "similar", title: "Similar styles", note: "Not the same item, but close in look." },
];

export default function SearchView({ id }: { id: string }) {
  const [s, setS] = useState<SearchState | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [sort, setSort] = useState<Sort>("match");
  const [stores, setStores] = useState<Set<string>>(new Set());
  const [inStock, setInStock] = useState(false);
  const [maxPrice, setMaxPrice] = useState<string>("");
  const [showSimilar, setShowSimilar] = useState(true);
  const [filtersOpen, setFiltersOpen] = useState(false);

  useEffect(() => {
    let stop = false;
    let timer: ReturnType<typeof setTimeout>;
    const tick = async () => {
      try {
        const next = await api<SearchState>(`/api/searches/${id}`);
        if (stop) return;
        setS(next);
        if (next.status !== "done" && next.status !== "failed") timer = setTimeout(tick, 1500);
      } catch (e) {
        if (!stop) setErr((e as Error).message);
      }
    };
    tick();
    return () => { stop = true; clearTimeout(timer); };
  }, [id]);

  const storeNames = useMemo(
    () => Array.from(new Set((s?.results ?? []).map((r) => r.product.store_name))).sort(),
    [s?.results],
  );

  const visible = useMemo(() => {
    let rs = s?.results ?? [];
    if (stores.size) rs = rs.filter((r) => stores.has(r.product.store_name));
    if (inStock) rs = rs.filter((r) => r.product.in_stock !== false);
    const max = Number(maxPrice);
    if (maxPrice && max > 0) rs = rs.filter((r) => r.product.current_price == null || r.product.current_price <= max);
    const by: Record<Sort, (a: Result, b: Result) => number> = {
      match: (a, b) => b.score - a.score,
      price: (a, b) => (a.product.current_price ?? Infinity) - (b.product.current_price ?? Infinity),
      discount: (a, b) => (b.product.discount_pct ?? -1) - (a.product.discount_pct ?? -1),
    };
    return [...rs].sort(by[sort]);
  }, [s?.results, stores, inStock, maxPrice, sort]);

  const cheapestExact = useMemo(() => {
    const ex = (s?.results ?? []).filter((r) => r.match_level === "exact" && r.product.current_price != null && r.product.in_stock !== false);
    return ex.length > 1 ? ex.reduce((a, b) => (a.product.current_price! <= b.product.current_price! ? a : b)).product.id : null;
  }, [s?.results]);

  if (err) return <div className="empty"><h2>Couldn't load this search</h2><p className="notice">{err}</p><Link className="btn" href="/">Start a new search</Link></div>;
  if (!s) return <div className="progress"><div className="stage">Loading…</div></div>;

  const running = s.status !== "done" && s.status !== "failed";
  const a = s.attributes;
  const toggleStore = (name: string) => setStores((prev) => {
    const n = new Set(prev);
    if (n.has(name)) n.delete(name); else n.add(name);
    return n;
  });

  return (
    <div className="search-layout">
      <aside className="aside">
        <div>
          <span className="tag-string" aria-hidden="true" />
          <div className="tag query-photo">
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img src={imageUrl(s.image_id)} alt="Your photo" />
          </div>
        </div>
        <div className="split">
          {a && (
            <div className="filters">
              <h3>{a.garment ? a.garment[0].toUpperCase() + a.garment.slice(1) : "Your outfit"}</h3>
              <div className="chips">
                {[...(a.colours ?? []), a.pattern, a.fabric, ...(a.details ?? []).slice(0, 3)]
                  .filter(Boolean).map((t) => <span className="chip" key={String(t)}>{t}</span>)}
              </div>
            </div>
          )}
          {s.status === "done" && s.results.length > 0 && (
            <button className="btn quiet small filter-toggle" aria-expanded={filtersOpen} onClick={() => setFiltersOpen((o) => !o)}>
              {filtersOpen ? "Hide filters" : "Sort and filter"}
            </button>
          )}
          {s.status === "done" && s.results.length > 0 && (
            <div className={`filters filter-panel${filtersOpen ? " open" : ""}`}>
              <fieldset>
                <legend>Sort by</legend>
                <select className="input" value={sort} onChange={(e) => setSort(e.target.value as Sort)}>
                  <option value="match">Best match</option>
                  <option value="price">Lowest price</option>
                  <option value="discount">Biggest discount</option>
                </select>
              </fieldset>
              <fieldset>
                <legend>Stores</legend>
                <div className="chips">
                  {storeNames.map((n) => (
                    <button key={n} className="chip" aria-pressed={stores.has(n)} onClick={() => toggleStore(n)}>{n}</button>
                  ))}
                </div>
              </fieldset>
              <div className="field">
                <label htmlFor="max">Up to (₹)</label>
                <input id="max" className="input num" inputMode="numeric" placeholder="Any price"
                  value={maxPrice} onChange={(e) => setMaxPrice(e.target.value.replace(/\D/g, ""))} />
              </div>
              <label className="check"><input type="checkbox" checked={inStock} onChange={(e) => setInStock(e.target.checked)} /> In stock only</label>
              <label className="check"><input type="checkbox" checked={showSimilar} onChange={(e) => setShowSimilar(e.target.checked)} /> Show similar styles</label>
            </div>
          )}
        </div>
      </aside>

      <section aria-live="polite">
        {running && (
          <div className="progress">
            <div className="stage">{s.stage}…</div>
            <div className="thread" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(s.progress * 100)}>
              <span style={{ width: `${Math.max(6, s.progress * 100)}%` }} />
            </div>
            <p className="muted">This usually takes 30 to 90 seconds while we open each store's page for live prices.</p>
          </div>
        )}

        {s.status === "failed" && (
          <div className="empty">
            <h2>This search didn't finish</h2>
            <p className="notice">{s.error ?? "Something went wrong on our side."}</p>
            <div><Link className="btn" href="/">Try another photo</Link></div>
          </div>
        )}

        {s.status === "done" && !!s.stats?.spotted?.length && (
          <div className="spotted">
            <h2>Spotted on</h2>
            <p className="muted small">
              Google Lens saw your exact photo on these store pages. The item's own page isn't searchable yet, so
              open one and look for this piece in the grid.
            </p>
            <ul>
              {s.stats.spotted.map((p) => (
                <li key={p.url}>
                  <a href={p.url} target="_blank" rel="noopener noreferrer">
                    <span className="store">{p.store}</span>
                    <span className="title">{p.title}</span>
                    <span aria-hidden="true" className="arrow">↗</span>
                  </a>
                </li>
              ))}
            </ul>
          </div>
        )}

        {s.status === "done" && s.results.length === 0 && (
          <div className="empty">
            <h2>No shops found for this photo</h2>
            <p className="muted">
              Lens couldn't find this piece on a store we can read. Try a clearer photo with the garment filling most of
              the frame, or crop to just the piece you want.
            </p>
            <div><Link className="btn" href="/">Try another photo</Link></div>
          </div>
        )}

        {s.status === "done" && s.results.length > 0 && (
          <>
            {s.stats && (
              <p className="muted small" style={{ marginBottom: 24 }}>
                Checked {s.stats.candidates} listings and kept {s.stats.results}
                {s.stats.seconds ? ` in ${Math.round(s.stats.seconds)} seconds` : ""}.
              </p>
            )}
            {sort === "match"
              ? GROUPS.filter((g) => showSimilar || g.level !== "similar").map((g) => {
                  const rs = visible.filter((r) => r.match_level === g.level);
                  if (!rs.length) return null;
                  return (
                    <div className="group" key={g.level}>
                      <div className="group-head">
                        <h2>{g.title}</h2>
                        <p>{g.level === "exact" ? `${rs.length} ${rs.length === 1 ? "store" : "stores"}. ` : ""}{g.note}</p>
                      </div>
                      <div className="rows">
                        {rs.map((r) => <ResultRow key={r.product.id} r={r} cheapest={r.product.id === cheapestExact} />)}
                      </div>
                    </div>
                  );
                })
              : (
                <div className="rows">
                  {visible.filter((r) => showSimilar || r.match_level !== "similar")
                    .map((r) => <ResultRow key={r.product.id} r={r} cheapest={r.product.id === cheapestExact} />)}
                </div>
              )}
            {visible.length === 0 && (
              <div className="empty">
                <p>No listings match these filters.</p>
                <div><button className="btn quiet" onClick={() => { setStores(new Set()); setInStock(false); setMaxPrice(""); }}>Clear filters</button></div>
              </div>
            )}
          </>
        )}
      </section>
    </div>
  );
}
