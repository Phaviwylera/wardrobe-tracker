import Link from "next/link";
import Spark from "./Spark";
import { LEVEL_LABEL, money, type Result } from "@/lib/api";

export default function ResultRow({ r, cheapest }: { r: Result; cheapest: boolean }) {
  const p = r.product;
  const atLowest = r.lowest != null && p.current_price != null && r.spark.length > 2 && p.current_price <= r.lowest;
  return (
    <article className="row">
      {p.image_url ? (
        // eslint-disable-next-line @next/next/no-img-element
        <img className="row-img" src={p.image_url} alt="" loading="lazy" referrerPolicy="no-referrer" />
      ) : (
        <div className="row-img" aria-hidden="true" />
      )}
      <div className="row-main">
        <div className="row-store">
          <span>{p.store_name}</span>
          <span className={`stamp ${r.match_level}`}>{LEVEL_LABEL[r.match_level]}</span>
          {cheapest && <span className="best">Lowest price</span>}
        </div>
        <Link href={`/product/?id=${p.id}`} className="row-title">
          {p.brand && !p.title?.toLowerCase().startsWith(p.brand.toLowerCase()) ? `${p.brand} ` : ""}
          {p.title ?? p.domain}
        </Link>
        <div className="row-meta">
          {p.in_stock === false && <span className="oos">Out of stock</span>}
          {p.rating && <span>★ {p.rating.toFixed(1)}{p.rating_count ? ` (${p.rating_count.toLocaleString("en-IN")})` : ""}</span>}
          {p.sizes && p.sizes.length > 0 && (
            <span>Sizes {p.sizes.filter((s) => s.available !== false).map((s) => s.label).slice(0, 6).join(", ") || "none left"}</span>
          )}
          {!p.live && p.current_price != null && <span>Price from search listing</span>}
          {atLowest && <span style={{ color: "var(--leaf)" }}>At its lowest</span>}
        </div>
      </div>
      <div className="row-side">
        <div>
          <div className="price num">{money(p.current_price, p.currency)}</div>
          {p.mrp && p.discount_pct ? (
            <div className="small">
              <span className="mrp num">{money(p.mrp)}</span> <span className="off">{p.discount_pct}% off</span>
            </div>
          ) : null}
        </div>
        <Spark points={r.spark} />
        <div className="row-actions">
          <Link className="btn quiet small" href={`/product/?id=${p.id}`}>Price history</Link>
          <a className="btn small" href={p.url} target="_blank" rel="noopener noreferrer nofollow">
            Open on {p.store_name.split(" ")[0]}
          </a>
        </div>
      </div>
    </article>
  );
}
