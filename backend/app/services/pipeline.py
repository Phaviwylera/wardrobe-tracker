"""Photo -> verified product links with live prices.

  1. discover   Google Lens (exact matches, products, visual matches) on the photo, plus
                optional Claude-written text queries on Google Shopping / site-filtered Google
  2. filter     keep single-product pages on shops, collapse duplicates by canonical URL
  3. verify     download every candidate image and score it against the photo (matcher.py)
  4. price      scrape the best candidates' pages through Web Unlocker for live price,
                MRP, sizes and stock; fall back to the SERP price snippet
  5. persist    products, a price-history point each, ranked search results
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

from ..config import Settings, get_settings
from ..db import SessionLocal
from ..models import QueryImage, Search, SearchResult
from . import captioner, matcher
from .brightdata import BrightData, BrightDataError
from .extract import ProductData, extract_product, looks_blocked
from .pricing import apply_page_data, get_or_create_product, record_price
from .serp_parse import Listing, extract_listings
from .stores import canonical_url, is_non_shop, is_product_url, shopping_site_filter, store_tier

log = logging.getLogger(__name__)
LENS_TABS = ("exact_matches", "products", "visual_matches")
CHANNEL_RANK = {"lens:exact_matches": 0, "lens:products": 1, "shopping": 2, "organic": 3,
                "lens:visual_matches": 4}
MIN_SCORE = 0.30


@dataclass
class Candidate:
    canon: str
    listing: Listing
    channels: set[str] = field(default_factory=set)
    images: list[str] = field(default_factory=list)
    score: matcher.MatchScore | None = None
    page: ProductData | None = None

    @property
    def best_channel(self) -> str:
        return min(self.channels, key=lambda c: CHANNEL_RANK.get(c, 9))


def _update(search_id: str, **fields) -> None:
    with SessionLocal() as db:
        s = db.get(Search, search_id)
        if s:
            for k, v in fields.items():
                setattr(s, k, v)
            db.commit()


# --------------------------------------------------------------------------- discovery
async def _lens(bd: BrightData, s: Settings, image_id: str, image: bytes, tab: str) -> list[Listing]:
    public = s.public_base_url.rstrip("/")
    local = "localhost" in public or "127.0.0.1" in public
    try:
        if local and s.brightdata_customer_id:
            doc = await bd.lens_by_upload(image, tab)
        else:
            doc = await bd.lens_by_url(f"{public}/api/images/{image_id}.jpg", tab)
        return extract_listings(doc, f"lens:{tab}")
    except BrightDataError as e:
        log.warning("lens %s failed: %s", tab, e)
        return []


async def _text_search(bd: BrightData, queries: list[str]) -> list[Listing]:
    jobs = []
    for q in queries[:3]:
        jobs.append(bd.google_shopping(q))
    if queries:
        jobs.append(bd.google_organic(f"{queries[0]} ({shopping_site_filter()})"))
    out: list[Listing] = []
    for i, res in enumerate(await asyncio.gather(*jobs, return_exceptions=True)):
        if isinstance(res, Exception):
            log.warning("text search failed: %s", res)
            continue
        out += extract_listings(res, "organic" if i == len(jobs) - 1 else "shopping")
    return out


def _collect(listings: list[Listing], limit: int) -> list[Candidate]:
    by_canon: dict[str, Candidate] = {}
    for li in listings:
        if is_non_shop(li.url):
            continue
        # Shopping results carry a price and point at a merchant page even when the URL
        # shape is unfamiliar; Lens results must look like a product page.
        if not is_product_url(li.url) and not (li.channel == "shopping" and li.price):
            continue
        canon = canonical_url(li.url)
        c = by_canon.get(canon)
        if c is None:
            c = by_canon[canon] = Candidate(canon=canon, listing=li)
        c.channels.add(li.channel)
        better = CHANNEL_RANK.get(li.channel, 9) < CHANNEL_RANK.get(c.listing.channel, 9)
        if li.image_url and li.image_url not in c.images:
            # the more trustworthy channel's thumbnail is compared first
            c.images.insert(0, li.image_url) if better else c.images.append(li.image_url)
        price, currency = c.listing.price, c.listing.currency
        if better:
            c.listing = li
        if c.listing.price is None and (li.price or price):
            c.listing.price = li.price or price
            c.listing.currency = li.currency if li.price else currency
    cands = sorted(by_canon.values(),
                   key=lambda c: (CHANNEL_RANK.get(c.best_channel, 9), store_tier(c.canon), c.listing.position))
    return cands[:limit]


# --------------------------------------------------------------------------- verification
async def _features_for(bd: BrightData, urls: list[str]) -> matcher.Features | None:
    for u in urls[:2]:
        data = await bd.fetch_image(u)
        if data:
            try:
                return await asyncio.to_thread(matcher.features, data)
            except Exception:
                continue
    return None


async def _verify(bd: BrightData, s: Settings, qf: matcher.Features, cands: list[Candidate]) -> None:
    feats = await asyncio.gather(*[_features_for(bd, c.images) for c in cands])
    for c, f in zip(cands, feats):
        c.score = matcher.compare(qf, f, c.best_channel, s.exact_threshold, s.close_threshold)


# --------------------------------------------------------------------------- pricing
async def scrape_product(bd: BrightData, url: str) -> ProductData | None:
    try:
        html = await bd.fetch_html(url)
    except BrightDataError as e:
        log.info("page fetch failed %s: %s", url[:100], e)
        return None
    if looks_blocked(html) and "price" not in html.lower():
        return None
    try:
        return await asyncio.to_thread(extract_product, html, url)
    except Exception as e:
        log.warning("extract failed %s: %s", url[:100], e)
        return None


async def _price(bd: BrightData, s: Settings, qf: matcher.Features, cands: list[Candidate]) -> None:
    pages = await asyncio.gather(*[scrape_product(bd, c.listing.url) for c in cands])
    rescore = []
    for c, page in zip(cands, pages):
        c.page = page
        # If we couldn't verify from the SERP thumbnail, use the product page's own photos.
        if page and page.images and c.score and not c.score.signals.get("verified"):
            rescore.append(c)
    feats = await asyncio.gather(*[_features_for(bd, c.page.images) for c in rescore])
    for c, f in zip(rescore, feats):
        if f is not None:
            c.score = matcher.compare(qf, f, c.best_channel, s.exact_threshold, s.close_threshold)


# --------------------------------------------------------------------------- persist
def _persist(search_id: str, cands: list[Candidate]) -> dict:
    kept = 0
    priced = 0
    with SessionLocal() as db:
        db.query(SearchResult).filter(SearchResult.search_id == search_id).delete()
        ranked = sorted([c for c in cands if c.score and c.score.score >= MIN_SCORE],
                        key=lambda c: -c.score.score)
        for rank, c in enumerate(ranked):
            li = c.listing
            p = get_or_create_product(db, li.url, title=li.title, image_url=(c.images or [None])[0])
            if c.page:
                apply_page_data(p, c.page)
            if not p.title:
                p.title = li.title
            if c.page and c.page.price:
                record_price(db, p, c.page.price, mrp=c.page.mrp, in_stock=c.page.in_stock, source="page")
                priced += 1
            elif li.price and (li.currency in (None, "INR")):
                record_price(db, p, li.price, in_stock=li.in_stock,
                             source="shopping" if li.channel == "shopping" else "lens")
                priced += 1
            db.add(SearchResult(search_id=search_id, product_id=p.id, rank=rank,
                                score=c.score.score, match_level=c.score.level,
                                signals=c.score.signals, sources=sorted(c.channels)))
            kept += 1
        db.commit()
    return {"results": kept, "priced": priced}


# --------------------------------------------------------------------------- entry
async def run_search(search_id: str, settings: Settings | None = None,
                     bd: BrightData | None = None) -> None:
    s = settings or get_settings()
    own_client = bd is None
    bd = bd or BrightData(s)
    t0 = time.monotonic()
    try:
        with SessionLocal() as db:
            search = db.get(Search, search_id)
            img = db.get(QueryImage, search.image_id)
            image, image_id = img.data, img.id

        if not s.brightdata_ready:
            raise RuntimeError("Bright Data is not configured (BRIGHTDATA_API_KEY / BRIGHTDATA_SERP_ZONE).")

        _update(search_id, status="searching", stage="Reading the photo and asking Google Lens", progress=0.08)
        qf_task = asyncio.create_task(asyncio.to_thread(matcher.features, image))
        caption_task = asyncio.create_task(captioner.describe(image, s))
        lens = await asyncio.gather(*[_lens(bd, s, image_id, image, t) for t in LENS_TABS])
        listings = [li for group in lens for li in group]

        attrs = await caption_task
        if attrs:
            _update(search_id, attributes=attrs, stage="Searching Indian stores by description", progress=0.3)
            listings += await _text_search(bd, attrs.get("queries") or [])

        cands = _collect(listings, s.max_candidates)
        if not cands:
            _update(search_id, status="done", stage="No shops found for this photo", progress=1.0,
                    finished_at=datetime.now(timezone.utc),
                    stats={"listings": len(listings), "candidates": 0, "results": 0,
                           "seconds": round(time.monotonic() - t0, 1)})
            return

        _update(search_id, status="verifying", progress=0.45,
                stage=f"Comparing {len(cands)} listings against your photo")
        qf = await qf_task
        await _verify(bd, s, qf, cands)

        best = sorted(cands, key=lambda c: -(c.score.score if c.score else 0))
        to_price = [c for c in best if c.score and c.score.score >= MIN_SCORE][: s.max_product_pages]
        _update(search_id, status="pricing", progress=0.7,
                stage=f"Fetching live prices from {len(to_price)} product pages")
        await _price(bd, s, qf, to_price)

        stats = _persist(search_id, cands)
        stats.update(listings=len(listings), candidates=len(cands), seconds=round(time.monotonic() - t0, 1),
                     captioned=bool(attrs))
        _update(search_id, status="done", stage="Done", progress=1.0, stats=stats,
                finished_at=datetime.now(timezone.utc))
    except Exception as e:
        log.exception("search %s failed", search_id)
        _update(search_id, status="failed", stage="Failed", error=str(e)[:500],
                finished_at=datetime.now(timezone.utc))
    finally:
        if own_client:
            await bd.aclose()
