"""Endpoints for the StyleTrail browser extension.

The extension runs in the shopper's own browser while they browse a store normally. It sends us
what that page already shows them:
  /api/ext/match    – product tiles from a listing grid (link, title, price, photo bytes); we compare
                      each photo with the search photo and add matches to that search's results
  /api/ext/observe  – one product page's price, stock and sizes, added to that product's price history
The server never requests store pages itself through this path.
"""
from __future__ import annotations

import asyncio
import base64
import logging
from collections import OrderedDict
from urllib.parse import urlsplit

import httpx

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import get_db
from .models import QueryImage, Search, SearchResult
from .services import matcher
from .services.extract import extract_product
from .services.pipeline import Candidate, _identical
from .services.pricing import apply_page_data, get_or_create_product, record_price, save_thumb
from .services.serp_parse import Listing, parse_price
from .services.stores import canonical_url, is_product_url, store_for

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/ext", tags=["extension"])

_QF: "OrderedDict[str, matcher.Features]" = OrderedDict()  # search photo features, small LRU


def _query_features(db: Session, search_id: str) -> matcher.Features:
    if search_id in _QF:
        _QF.move_to_end(search_id)
        return _QF[search_id]
    s = db.get(Search, search_id)
    if not s:
        raise HTTPException(404, "Search not found")
    img = db.get(QueryImage, s.image_id)
    f = matcher.features(img.data)
    _QF[search_id] = f
    while len(_QF) > 64:
        _QF.popitem(last=False)
    return f


def _store_page(url: str) -> bool:
    return store_for(url).key != "other" and is_product_url(url)


class Tile(BaseModel):
    url: str = Field(max_length=2000)
    title: str | None = Field(default=None, max_length=400)
    price: str | float | None = None
    mrp: str | float | None = None
    image_b64: str | None = Field(default=None, max_length=900_000)
    image_url: str | None = Field(default=None, max_length=2000)  # from the phone Finder: we fetch the photo


class MatchIn(BaseModel):
    search_id: str = Field(max_length=64)
    tiles: list[Tile] = Field(max_length=40)


# Image CDNs of the stores the Finder runs on. Only these are fetched, and only the photo itself.
IMAGE_HOSTS = ("assets.myntassets.com", "assets.ajio.com", "images.meesho.com", "images-static.nykaa.com",
               "adn-static1.nykaa.com")


async def _tile_images(tiles: list[Tile]) -> dict[int, bytes]:
    out: dict[int, bytes] = {}
    need = [(i, t.image_url) for i, t in enumerate(tiles) if not t.image_b64 and t.image_url
            and urlsplit(t.image_url).scheme == "https" and (urlsplit(t.image_url).hostname or "") in IMAGE_HOSTS]
    if not need:
        return out
    async with httpx.AsyncClient(timeout=12, follow_redirects=False, headers={"User-Agent": "Mozilla/5.0 StyleTrail"}) as c:
        async def one(i: int, u: str) -> None:
            try:
                r = await c.get(u)
                if r.status_code == 200 and r.headers.get("content-type", "").startswith("image/") and len(r.content) < 3_000_000:
                    out[i] = r.content
            except httpx.HTTPError:
                pass
        await asyncio.gather(*[one(i, u) for i, u in need])
    return out


def _score_tiles(qf: matcher.Features, tiles: list[Tile], fetched: dict[int, bytes]) -> list[tuple[Tile, bytes, matcher.MatchScore, bool]]:
    out = []
    for i, t in enumerate(tiles):
        if not _store_page(t.url):
            continue
        try:
            raw = fetched[i] if i in fetched else base64.b64decode((t.image_b64 or "").split(",", 1)[-1])
            if not raw:
                continue
            f = matcher.features(raw)
        except Exception:
            continue
        sc = matcher.compare(qf, f, "lens:exact_matches")
        c = Candidate(canon=canonical_url(t.url), listing=Listing(url=t.url))
        c.score = sc
        same = _identical(c)
        out.append((t, raw, sc, same))
    return out


@router.post("/match")
async def match(body: MatchIn, db: Session = Depends(get_db)) -> dict:
    qf = await asyncio.to_thread(_query_features, db, body.search_id)
    fetched = await _tile_images(body.tiles)
    scored = await asyncio.to_thread(_score_tiles, qf, body.tiles, fetched)
    found = []
    for t, raw, sc, same in scored:
        level = "exact" if same else sc.level
        score = 0.995 if same else sc.score
        if level not in ("exact", "close"):
            continue
        p = get_or_create_product(db, t.url, title=t.title)
        if t.title and not p.title:
            p.title = t.title
        save_thumb(db, p.id, raw)
        price, _ = parse_price(t.price) if t.price is not None else (None, None)
        mrp, _ = parse_price(t.mrp) if t.mrp is not None else (None, None)
        if price:
            record_price(db, p, price, mrp=mrp, source="extension")
        r = db.scalar(select(SearchResult).where(SearchResult.search_id == body.search_id,
                                                 SearchResult.product_id == p.id))
        signals = {**sc.signals, "identical_photo": same, "via": "extension"}
        if r is None:
            db.add(SearchResult(search_id=body.search_id, product_id=p.id, rank=-1 if same else 999,
                                score=score, match_level=level, signals=signals, sources=["extension"]))
        elif score > r.score:
            r.score, r.match_level, r.signals = score, level, signals
            r.rank = min(r.rank, -1 if same else r.rank)
            r.sources = sorted(set(r.sources or []) | {"extension"})
        found.append({"url": t.url, "product_id": p.id, "level": level, "score": round(score, 3)})
    db.commit()
    return {"checked": len(scored), "matches": found}


class ObserveIn(BaseModel):
    url: str = Field(max_length=2000)
    state_script: str | None = Field(default=None, max_length=3_000_000)  # window.__myx / JSON-LD text
    title: str | None = Field(default=None, max_length=400)
    brand: str | None = Field(default=None, max_length=200)
    price: str | float | None = None
    mrp: str | float | None = None
    in_stock: bool | None = None
    image_url: str | None = Field(default=None, max_length=2000)


@router.post("/observe")
def observe(body: ObserveIn, db: Session = Depends(get_db)) -> dict:
    if not _store_page(body.url):
        raise HTTPException(400, "Not a product page of a store we track")
    d = extract_product(f"<html><head>{body.state_script or ''}</head></html>", body.url) if body.state_script else None
    price = d.price if d and d.price else (parse_price(body.price)[0] if body.price is not None else None)
    mrp = d.mrp if d and d.mrp else (parse_price(body.mrp)[0] if body.mrp is not None else None)
    stock = d.in_stock if d and d.in_stock is not None else body.in_stock
    p = get_or_create_product(db, body.url, title=(d.title if d else None) or body.title,
                              image_url=(d.image_url if d else None) or body.image_url)
    if d:
        apply_page_data(p, d)
    else:
        p.title = p.title or body.title
        p.brand = p.brand or body.brand
    if stock is not None:
        p.in_stock = stock
    pt = record_price(db, p, price, mrp=mrp, in_stock=stock, source="extension")
    db.commit()
    return {"product_id": p.id, "price": price, "mrp": mrp, "in_stock": stock, "recorded": pt is not None}


class LinkIn(BaseModel):
    search_id: str = Field(max_length=64)
    url: str = Field(max_length=2000)


@router.post("/add-link")
async def add_link(body: LinkIn, db: Session = Depends(get_db)) -> dict:
    """The shopper found the piece themselves (e.g. shared from the Myntra app) and pastes its link."""
    url = body.url.strip()
    m = __import__("re").search(r"https?://\S+", url)  # shared text often has words around the link
    url = m.group(0).rstrip(").,") if m else url
    if not db.get(Search, body.search_id):
        raise HTTPException(404, "Search not found")
    if not _store_page(url):
        raise HTTPException(400, "That isn't a product link from a store we track. Copy the link of the product page itself.")
    p = get_or_create_product(db, url)
    r = db.scalar(select(SearchResult).where(SearchResult.search_id == body.search_id, SearchResult.product_id == p.id))
    signals = {"user_confirmed": True, "identical_photo": True}
    if r is None:
        db.add(SearchResult(search_id=body.search_id, product_id=p.id, rank=-2, score=1.0, match_level="exact",
                            signals=signals, sources=["you"]))
    else:
        r.rank, r.score, r.match_level, r.signals = -2, 1.0, "exact", {**(r.signals or {}), **signals}
        r.sources = sorted(set(r.sources or []) | {"you"})
    db.commit()
    priced = False
    if not p.title or not p.prices:
        from .main import _scrape  # stores that serve us directly; blocked stores fill in via Finder/extension visits
        page = await _scrape(p.url)
        if page and page.price:
            apply_page_data(p, page)
            record_price(db, p, page.price, mrp=page.mrp, in_stock=page.in_stock, source="page")
            priced = True
        if not p.title:
            p.title = _title_from_url(p.url)
        db.commit()
    return {"product_id": p.id, "priced": priced}


def _title_from_url(url: str) -> str:
    parts = [x for x in urlsplit(url).path.split("/") if x and not x.isdigit() and x not in ("buy", "p", "dp")]
    slug = max(parts, key=len) if parts else ""
    return slug.replace("-", " ").replace("+", " ").strip().title()[:200] or "Product"
