"""Product upsert + price-history recording, shared by search and the scheduled refresh."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import PricePoint, Product
from .extract import ProductData
from .stores import canonical_url, store_info

DEDUPE_WINDOW = timedelta(hours=3)
THUMB_IDS: set[str] = set()  # products whose photo we store (see ProductThumb)


def save_thumb(db: Session, product_id: str, image: bytes) -> None:
    import io

    from PIL import Image

    from ..models import ProductThumb

    try:
        img = Image.open(io.BytesIO(image)).convert("RGB")
        img.thumbnail((480, 640))
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=82, optimize=True)
    except Exception:
        return
    t = db.get(ProductThumb, product_id)
    if t:
        t.data, t.updated_at = buf.getvalue(), datetime.now(timezone.utc)
    else:
        db.add(ProductThumb(product_id=product_id, data=buf.getvalue()))
    THUMB_IDS.add(product_id)


def _as_aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def get_or_create_product(db: Session, url: str, *, title: str | None = None,
                          image_url: str | None = None) -> Product:
    canon = canonical_url(url)
    p = db.scalar(select(Product).where(Product.canonical_url == canon))
    if p:
        return p
    key, name, domain = store_info(url)
    p = Product(canonical_url=canon, url=url, store_key=key, store_name=name, domain=domain,
                title=title, image_url=image_url)
    db.add(p)
    db.flush()
    return p


def apply_page_data(p: Product, d: ProductData) -> None:
    # Keep the listing's own title; page <title> tags are often generic site names.
    if not p.title or ("jsonld" in d.sources or "myntra" in d.sources or "amazon_dom" in d.sources):
        p.title = d.title or p.title
    p.brand = d.brand or p.brand
    p.image_url = d.image_url or p.image_url
    p.currency = d.currency or p.currency or "INR"
    if d.price is not None:
        p.current_price = d.price
        p.mrp = d.mrp
    if d.in_stock is not None:
        p.in_stock = d.in_stock
    if d.sizes:
        p.sizes = d.sizes
    p.rating = d.rating or p.rating
    p.rating_count = d.rating_count or p.rating_count
    p.scrape_ok = d.price is not None
    p.last_checked = datetime.now(timezone.utc)


def record_price(db: Session, p: Product, price: float | None, *, mrp: float | None = None,
                 in_stock: bool | None = None, source: str = "page") -> PricePoint | None:
    """Append a history point unless the same price was already recorded very recently."""
    if price is None or price <= 0:
        return None
    last = db.scalar(select(PricePoint).where(PricePoint.product_id == p.id)
                     .order_by(PricePoint.captured_at.desc()).limit(1))
    now = datetime.now(timezone.utc)
    if last and abs(last.price - price) < 0.01 and now - _as_aware(last.captured_at) < DEDUPE_WINDOW:
        return None
    # A page-scraped price is authoritative; don't let a stale SERP snippet overwrite it.
    if source != "page" and last and last.source == "page" and now - _as_aware(last.captured_at) < timedelta(days=1):
        return None
    pt = PricePoint(product_id=p.id, price=price, mrp=mrp, in_stock=in_stock, source=source,
                    captured_at=now)
    db.add(pt)
    db.flush()
    if p.current_price is None or source == "page":
        p.current_price = price
        if mrp:
            p.mrp = mrp
    return pt


def price_stats(points: list[PricePoint]) -> dict:
    if not points:
        return {"count": 0}
    prices = [pt.price for pt in points]
    current = prices[-1]
    lo, hi = min(prices), max(prices)
    avg = sum(prices) / len(prices)
    first = _as_aware(points[0].captured_at)
    last = _as_aware(points[-1].captured_at)
    # Verdict: where does today's price sit inside the observed range?
    if len(points) < 3 or hi == lo:
        verdict = "tracking"
    elif current <= lo * 1.02:
        verdict = "lowest"
    elif current <= avg * 0.97:
        verdict = "good"
    elif current >= hi * 0.98:
        verdict = "high"
    else:
        verdict = "fair"
    prev_distinct = next((p for p in reversed(prices[:-1]) if abs(p - current) > 0.01), None)
    return {
        "count": len(points), "current": current, "lowest": lo, "highest": hi,
        "average": round(avg, 2), "since": first.isoformat(), "updated": last.isoformat(),
        "change": None if prev_distinct is None else round(current - prev_distinct, 2),
        "verdict": verdict,
    }
