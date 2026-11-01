from __future__ import annotations

import asyncio
import logging
import os
import time
from contextlib import asynccontextmanager
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone

from fastapi import BackgroundTasks, Depends, FastAPI, File, Form, Header, HTTPException, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import refresh as refresh_job
from .config import get_settings
from .db import get_db, init_db
from .models import PricePoint, Product, QueryImage, Search, SearchResult, WatchItem
from .services import matcher
from .services.brightdata import BrightData
from .services.pipeline import run_search, scrape_product
from .services.pricing import apply_page_data, get_or_create_product, price_stats, record_price
from .services.stores import STORES, is_product_url, store_for

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
settings = get_settings()


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    # Searches interrupted by a restart/deploy would otherwise spin forever in the UI.
    from .db import SessionLocal
    with SessionLocal() as db:
        db.query(Search).filter(Search.status.in_(("queued", "searching", "verifying", "pricing"))).update(
            {"status": "failed", "stage": "Interrupted", "error": "The server restarted during this search. Please run it again."},
            synchronize_session=False)
        db.commit()
    yield


app = FastAPI(title="StyleTrail API", version="1.0.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.frontend_origin.split(",") if o.strip()] + ["http://localhost:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)
MAX_UPLOAD = 12 * 1024 * 1024
_recent: dict[str, deque] = defaultdict(deque)


def _client(x_client_id: str | None = Header(default=None)) -> str:
    cid = (x_client_id or "").strip()
    if not (8 <= len(cid) <= 64) or not cid.replace("-", "").isalnum():
        raise HTTPException(400, "X-Client-Id header required")
    return cid


def _rate_limit(cid: str, per_hour: int = int(os.getenv("SEARCHES_PER_HOUR", "30"))) -> None:
    q = _recent[cid]
    now = time.time()
    while q and now - q[0] > 3600:
        q.popleft()
    if len(q) >= per_hour:
        raise HTTPException(429, "Search limit reached for this hour. Try again later.")
    q.append(now)


# ------------------------------------------------------------------------- schemas
class ProductOut(BaseModel):
    id: str
    url: str
    store_key: str
    store_name: str
    domain: str
    title: str | None
    brand: str | None
    image_url: str | None
    currency: str
    current_price: float | None
    mrp: float | None
    discount_pct: int | None
    in_stock: bool | None
    sizes: list | None
    rating: float | None
    rating_count: int | None
    last_checked: datetime | None
    live: bool


class ResultOut(BaseModel):
    rank: int
    score: float
    match_level: str
    signals: dict | None
    sources: list | None
    product: ProductOut
    spark: list[float]
    lowest: float | None


class WatchIn(BaseModel):
    target_price: float | None = Field(default=None, gt=0)
    email: str | None = Field(default=None, max_length=320)


class TrackIn(BaseModel):
    url: str = Field(max_length=2048)


def product_out(p: Product) -> ProductOut:
    disc = None
    if p.mrp and p.current_price and p.mrp > p.current_price:
        disc = round(100 * (p.mrp - p.current_price) / p.mrp)
    return ProductOut(
        id=p.id, url=p.url, store_key=p.store_key, store_name=p.store_name, domain=p.domain,
        title=p.title, brand=p.brand, image_url=p.image_url, currency=p.currency,
        current_price=p.current_price, mrp=p.mrp, discount_pct=disc, in_stock=p.in_stock,
        sizes=p.sizes, rating=p.rating, rating_count=p.rating_count, last_checked=p.last_checked,
        live=p.scrape_ok,
    )


def _spark(db: Session, product_ids: list[str]) -> dict[str, list[float]]:
    if not product_ids:
        return {}
    since = datetime.now(timezone.utc) - timedelta(days=90)
    rows = db.execute(select(PricePoint.product_id, PricePoint.price)
                      .where(PricePoint.product_id.in_(product_ids), PricePoint.captured_at >= since)
                      .order_by(PricePoint.captured_at)).all()
    out: dict[str, list[float]] = defaultdict(list)
    for pid, price in rows:
        out[pid].append(price)
    return out


# ------------------------------------------------------------------------- routes
def _key_shape(k: str) -> dict:
    """Describe a key's shape without revealing it, to diagnose bad pastes."""
    import re
    return {
        "length": len(k),
        "uuid_like": bool(re.fullmatch(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}", k)),
        "hex64_like": bool(re.fullmatch(r"[0-9a-fA-F]{64}", k)),
        "dashes": k.count("-"),
        "whitespace_inside": bool(re.search(r"\s", k)),
        "non_hex_chars": len(re.findall(r"[^0-9a-fA-F-]", k)),
    }


@app.get("/api/brightdata-check")
async def brightdata_check() -> dict:
    """Asks Bright Data whether the configured key is accepted (status code only)."""
    import httpx
    if not settings.brightdata_api_key:
        return {"configured": False}
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.get("https://api.brightdata.com/zone/get_active_zones",
                        headers={"Authorization": f"Bearer {settings.brightdata_api_key}"})
    zones = []
    if r.status_code == 200:
        try:
            zones = [z.get("name") for z in r.json()]
        except Exception:
            pass
    return {"configured": True, "status": r.status_code, "zones": zones,
            "serp_zone_found": settings.brightdata_serp_zone in zones}


@app.get("/api/health")
def health() -> dict:
    return {
        "ok": True,
        "brightdata": settings.brightdata_ready,
        # Shape only (never the value), to spot mis-pasted keys.
        "key_shape": _key_shape(settings.brightdata_api_key),
        "serp_zone": settings.brightdata_serp_zone or None,
        "unlocker": bool(settings.brightdata_unlocker_zone),
        "captioner": settings.llm_provider if settings.llm_provider != "none" else None,
        "clip": os.getenv("ENABLE_CLIP") == "1",
        "stores": len(STORES),
    }


@app.get("/api/stores")
def stores() -> list[dict]:
    return [{"key": s.key, "name": s.name, "domain": s.domains[0], "tier": s.tier} for s in STORES]


@app.post("/api/searches", status_code=202)
async def create_search(
    background: BackgroundTasks,
    file: UploadFile = File(...),
    crop_x: float | None = Form(default=None), crop_y: float | None = Form(default=None),
    crop_w: float | None = Form(default=None), crop_h: float | None = Form(default=None),
    cid: str = Depends(_client), db: Session = Depends(get_db),
) -> dict:
    raw = await file.read(MAX_UPLOAD + 1)
    if len(raw) > MAX_UPLOAD:
        raise HTTPException(413, "Image too large (max 12 MB)")
    _rate_limit(cid)
    try:
        if None not in (crop_x, crop_y, crop_w, crop_h) and 0 < crop_w <= 1 and 0 < crop_h <= 1:
            raw = await asyncio.to_thread(matcher.crop_image, raw, (crop_x, crop_y, crop_w, crop_h))
        data, w, h = await asyncio.to_thread(matcher.normalise_upload, raw)
    except Exception:
        raise HTTPException(415, "Couldn't read that image. Use JPG, PNG or WEBP.")
    img = QueryImage(data=data, width=w, height=h)
    db.add(img)
    db.flush()
    s = Search(image_id=img.id, client_id=cid)
    db.add(s)
    db.commit()
    background.add_task(run_search, s.id)
    return {"id": s.id}


@app.get("/api/searches")
def list_searches(cid: str = Depends(_client), db: Session = Depends(get_db)) -> list[dict]:
    rows = db.scalars(select(Search).where(Search.client_id == cid)
                      .order_by(Search.created_at.desc()).limit(30)).all()
    counts = dict(db.execute(select(SearchResult.search_id, func.count())
                             .where(SearchResult.search_id.in_([r.id for r in rows]))
                             .group_by(SearchResult.search_id)).all()) if rows else {}
    return [{"id": r.id, "image_id": r.image_id, "status": r.status, "created_at": r.created_at,
             "garment": (r.attributes or {}).get("garment"), "results": counts.get(r.id, 0)} for r in rows]


@app.get("/api/searches/{search_id}")
def get_search(search_id: str, db: Session = Depends(get_db)) -> dict:
    s = db.get(Search, search_id)
    if not s:
        raise HTTPException(404, "Search not found")
    results = s.results if s.status == "done" else []
    spark = _spark(db, [r.product_id for r in results])
    return {
        "id": s.id, "image_id": s.image_id, "status": s.status, "stage": s.stage,
        "progress": s.progress, "error": s.error, "attributes": s.attributes, "stats": s.stats,
        "created_at": s.created_at, "finished_at": s.finished_at,
        "results": [ResultOut(
            rank=r.rank, score=r.score, match_level=r.match_level, signals=r.signals, sources=r.sources,
            product=product_out(r.product), spark=spark.get(r.product_id, [])[-30:],
            lowest=min(spark[r.product_id]) if spark.get(r.product_id) else None,
        ) for r in results],
    }


@app.get("/api/images/{image_id}.jpg")
def image(image_id: str, db: Session = Depends(get_db)) -> Response:
    img = db.get(QueryImage, image_id)
    if not img:
        raise HTTPException(404)
    return Response(img.data, media_type="image/jpeg", headers={"Cache-Control": "public, max-age=86400"})


@app.get("/api/products/{product_id}")
def get_product(product_id: str, x_client_id: str | None = Header(default=None),
                db: Session = Depends(get_db)) -> dict:
    p = db.get(Product, product_id)
    if not p:
        raise HTTPException(404, "Product not found")
    points = p.prices
    # Same item at other stores: products that appeared as exact/close matches alongside this one.
    sibling_searches = select(SearchResult.search_id).where(
        SearchResult.product_id == p.id, SearchResult.match_level.in_(("exact", "close")))
    others = db.scalars(
        select(Product).join(SearchResult, SearchResult.product_id == Product.id)
        .where(SearchResult.search_id.in_(sibling_searches), SearchResult.match_level == "exact",
               Product.id != p.id).distinct().limit(12)
    ).all()
    watch = db.scalar(select(WatchItem).where(WatchItem.client_id == (x_client_id or ""),
                                              WatchItem.product_id == p.id)) if x_client_id else None
    return {
        "product": product_out(p),
        "history": [{"t": pt.captured_at, "price": pt.price, "mrp": pt.mrp, "in_stock": pt.in_stock,
                     "source": pt.source} for pt in points],
        "stats": price_stats(points),
        "other_stores": sorted([product_out(o) for o in others],
                               key=lambda o: (o.current_price is None, o.current_price or 0)),
        "watch": None if not watch else {"target_price": watch.target_price, "email": watch.email},
    }


@app.post("/api/products/{product_id}/refresh")
async def refresh_product(product_id: str, db: Session = Depends(get_db)) -> dict:
    p = db.get(Product, product_id)
    if not p:
        raise HTTPException(404)
    last = p.last_checked if p.last_checked is None or p.last_checked.tzinfo else p.last_checked.replace(tzinfo=timezone.utc)
    if last and datetime.now(timezone.utc) - last < timedelta(minutes=10):
        return {"refreshed": False, "reason": "checked in the last 10 minutes"}
    bd = BrightData(settings)
    try:
        page = await scrape_product(bd, p.url)
    finally:
        await bd.aclose()
    p.last_checked = datetime.now(timezone.utc)
    if page and page.price:
        apply_page_data(p, page)
        record_price(db, p, page.price, mrp=page.mrp, in_stock=page.in_stock, source="page")
    db.commit()
    return {"refreshed": bool(page and page.price)}


@app.post("/api/products/track")
async def track_url(body: TrackIn, cid: str = Depends(_client), db: Session = Depends(get_db)) -> dict:
    url = body.url.strip()
    if not url.startswith(("http://", "https://")):
        raise HTTPException(422, "Paste a full product link (https://…)")
    if not is_product_url(url) and store_for(url).key != "other":
        raise HTTPException(422, "That link looks like a listing page, not a single product.")
    _rate_limit(cid)
    bd = BrightData(settings)
    try:
        page = await scrape_product(bd, url)
    finally:
        await bd.aclose()
    if not page or not page.price:
        raise HTTPException(502, "Couldn't read a price from that page right now.")
    p = get_or_create_product(db, url, title=page.title, image_url=page.image_url)
    apply_page_data(p, page)
    record_price(db, p, page.price, mrp=page.mrp, in_stock=page.in_stock, source="page")
    if not db.scalar(select(WatchItem).where(WatchItem.client_id == cid, WatchItem.product_id == p.id)):
        db.add(WatchItem(client_id=cid, product_id=p.id))
    db.commit()
    return {"id": p.id}


@app.get("/api/watchlist")
def watchlist(cid: str = Depends(_client), db: Session = Depends(get_db)) -> list[dict]:
    items = db.scalars(select(WatchItem).where(WatchItem.client_id == cid)
                       .order_by(WatchItem.created_at.desc())).all()
    spark = _spark(db, [w.product_id for w in items])
    out = []
    for w in items:
        pts = spark.get(w.product_id, [])
        out.append({
            "product": product_out(w.product), "target_price": w.target_price, "email": w.email,
            "spark": pts[-30:], "lowest": min(pts) if pts else None,
            "hit": bool(w.target_price and w.product.current_price and w.product.current_price <= w.target_price),
            "added": w.created_at,
        })
    return out


@app.put("/api/watchlist/{product_id}")
def watch(product_id: str, body: WatchIn, cid: str = Depends(_client), db: Session = Depends(get_db)) -> dict:
    if not db.get(Product, product_id):
        raise HTTPException(404)
    w = db.scalar(select(WatchItem).where(WatchItem.client_id == cid, WatchItem.product_id == product_id))
    if not w:
        w = WatchItem(client_id=cid, product_id=product_id)
        db.add(w)
    w.target_price = body.target_price
    w.email = (body.email or "").strip() or None
    w.last_alerted_price = None
    db.commit()
    return {"ok": True}


@app.delete("/api/watchlist/{product_id}")
def unwatch(product_id: str, cid: str = Depends(_client), db: Session = Depends(get_db)) -> dict:
    w = db.scalar(select(WatchItem).where(WatchItem.client_id == cid, WatchItem.product_id == product_id))
    if w:
        db.delete(w)
        db.commit()
    return {"ok": True}


@app.post("/api/admin/refresh")
async def admin_refresh(authorization: str | None = Header(default=None)) -> dict:
    if not settings.admin_token or authorization != f"Bearer {settings.admin_token}":
        raise HTTPException(401)
    return await refresh_job.refresh()


# ------------------------------------------------------------------------- website
# In production the Next.js static export is copied into STATIC_DIR and served from this same
# service, so the site and /api share one origin (and one free Render instance).
_static = os.getenv("STATIC_DIR", "")
if _static and os.path.isdir(_static):
    from fastapi.staticfiles import StaticFiles

    app.mount("/", StaticFiles(directory=_static, html=True), name="web")
