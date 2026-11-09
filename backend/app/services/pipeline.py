"""Photo -> verified product links with prices.

  1. describe   Claude (Bedrock) reads the photo: garment, colours, details, search queries
  2. discover   direct store searches (Amazon.in, Flipkart, Tata CLiQ, Shopify brands, Bing
                Images for Myntra/AJIO/Nykaa/Meesho); plus Google Lens when Bright Data is set
  3. filter     keep single-product pages on shops, collapse duplicates by canonical URL
  4. verify     compare every listing photo with the shopper's photo (matcher.py), then ask
                Claude to confirm the closest ones are the same product
  5. price      listing prices; product pages for sizes and stock where the store allows it
  6. persist    products, a price-history point each, ranked search results
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
from .direct import Direct
from .extract import ProductData, extract_product, looks_blocked
from .pricing import apply_page_data, get_or_create_product, record_price, save_thumb
from .serp_parse import Listing, extract_listings
from .stores import canonical_url, is_non_shop, is_product_url, shopping_site_filter, store_tier

log = logging.getLogger(__name__)
LENS_TABS = ("exact_matches", "products", "visual_matches")
CHANNEL_RANK = {"lens:exact_matches": 0, "lens:products": 1, "store": 2, "shopping": 2,
                "web": 3, "organic": 3, "lens:visual_matches": 4}
MIN_SCORE = 0.30
CLAUDE_CHECK = 32  # candidates the vision model double-checks per search


@dataclass
class Candidate:
    canon: str
    listing: Listing
    channels: set[str] = field(default_factory=set)
    images: list[str] = field(default_factory=list)
    score: matcher.MatchScore | None = None
    page: ProductData | None = None
    image_bytes: bytes | None = None

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


async def _serp_text(bd: BrightData, queries: list[str]) -> list[Listing]:
    jobs = [bd.google_shopping(q) for q in queries[:3]]
    if queries:
        jobs.append(bd.google_organic(f"{queries[0]} ({shopping_site_filter()})"))
    out: list[Listing] = []
    for i, res in enumerate(await asyncio.gather(*jobs, return_exceptions=True)):
        if isinstance(res, Exception):
            log.warning("serp text search failed: %s", res)
            continue
        out += extract_listings(res, "organic" if i == len(jobs) - 1 else "shopping")
    return out


def _collect(listings: list[Listing], limit: int) -> list[Candidate]:
    by_canon: dict[str, Candidate] = {}
    for li in listings:
        if is_non_shop(li.url):
            continue
        trusted = li.channel in ("store", "shopping") and li.price
        if not is_product_url(li.url) and not trusted:
            continue
        canon = canonical_url(li.url)
        c = by_canon.get(canon)
        if c is None:
            c = by_canon[canon] = Candidate(canon=canon, listing=li)
        c.channels.add(li.channel)
        better = CHANNEL_RANK.get(li.channel, 9) < CHANNEL_RANK.get(c.listing.channel, 9)
        if li.image_url and li.image_url not in c.images:
            c.images.insert(0, li.image_url) if better else c.images.append(li.image_url)
        price, currency, extra = c.listing.price, c.listing.currency, c.listing.extra
        if better:
            c.listing = li
        if c.listing.price is None and (li.price or price):
            c.listing.price = li.price or price
            c.listing.currency = li.currency if li.price else currency
            if not c.listing.extra.get("mrp") and extra.get("mrp"):
                c.listing.extra["mrp"] = extra["mrp"]
    cands = sorted(by_canon.values(),
                   key=lambda c: (CHANNEL_RANK.get(c.best_channel, 9), store_tier(c.canon), c.listing.position))
    return cands[:limit]


# --------------------------------------------------------------------------- verification
async def _image(direct: Direct, bd: BrightData | None, url: str) -> bytes | None:
    data = await direct.fetch_image(url)
    if data is None and bd is not None:
        data = await bd.fetch_image(url)
    return data


async def _load(direct: Direct, bd: BrightData | None, c: Candidate) -> matcher.Features | None:
    for u in c.images[:2]:
        data = await _image(direct, bd, u)
        if not data:
            continue
        try:
            f = await asyncio.to_thread(matcher.features, data)
            c.image_bytes = data
            return f
        except Exception:
            continue
    return None


async def _verify(direct: Direct, bd: BrightData | None, s: Settings, image: bytes,
                  qf: matcher.Features, cands: list[Candidate]) -> bool:
    feats = await asyncio.gather(*[_load(direct, bd, c) for c in cands])
    for c, f in zip(cands, feats):
        c.score = matcher.compare(qf, f, c.best_channel, s.exact_threshold, s.close_threshold)

    if not captioner.enabled(s):
        return False
    # Who gets the (slower) AI check: best photo matches plus each store's top search hits,
    # because lifestyle photos often score low on pixel similarity even for the right item.
    def priority(c: Candidate) -> float:
        bonus = 0.15 if c.listing.position <= 3 else (0.08 if c.listing.position <= 8 else 0.0)
        return c.score.score + bonus
    pool = sorted([c for c in cands if c.image_bytes], key=priority, reverse=True)[:CLAUDE_CHECK]
    verdicts = await captioner.verify(image, [c.image_bytes for c in pool], s)
    for i, c in enumerate(pool):
        if i not in verdicts:
            continue
        verdict, conf = verdicts[i]
        sig = dict(c.score.signals)
        sig["ai"] = {"verdict": verdict, "confidence": round(conf, 2)}
        score = c.score.score
        if verdict == "same" and conf >= 0.7:
            score = max(score, 0.80 + 0.18 * conf)
        elif verdict in ("same", "close"):
            score = max(min(score, s.exact_threshold - 0.02), s.close_threshold + 0.04)
        elif verdict == "similar":
            score = min(max(score, 0.42 + 0.1 * conf), s.close_threshold - 0.02)
        else:
            score = min(score, 0.2)
        score = round(min(score, 0.99), 3)
        c.score = matcher.MatchScore(score, matcher._level(score, s.exact_threshold, s.close_threshold), sig)
    return bool(verdicts)


# --------------------------------------------------------------------------- pricing
async def scrape_product(url: str, direct: Direct | None = None, bd: BrightData | None = None) -> ProductData | None:
    own = direct is None
    direct = direct or Direct()
    try:
        if "tatacliq.com" in url:
            li = await direct.tatacliq_product(url)
            if not li or not li.price:
                return None
            return ProductData(title=li.title or None, price=li.price, mrp=li.extra.get("mrp"), currency="INR",
                               in_stock=li.in_stock, image_url=li.image_url, sizes=li.extra.get("sizes") or [],
                               images=[li.image_url] if li.image_url else [], sources=["tatacliq_api"])
        html = await direct.fetch_page(url)
        if (html is None or looks_blocked(html)) and bd is not None and bd.s.brightdata_unlocker_zone:
            try:
                html = await bd.fetch_html(url)
            except BrightDataError as e:
                log.info("unlocker fetch failed %s: %s", url[:100], e)
                html = None
        if not html or (looks_blocked(html) and "price" not in html.lower()):
            return None
        return await asyncio.to_thread(extract_product, html, url)
    except Exception as e:
        log.warning("product page failed %s: %s", url[:100], e)
        return None
    finally:
        if own:
            await direct.aclose()


async def _price(direct: Direct, bd: BrightData | None, cands: list[Candidate]) -> None:
    pages = await asyncio.gather(*[scrape_product(c.listing.url, direct, bd) for c in cands])
    for c, page in zip(cands, pages):
        c.page = page


# --------------------------------------------------------------------------- persist
def _persist(search_id: str, cands: list[Candidate]) -> dict:
    kept = priced = 0
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
            if not p.image_url and c.images:
                p.image_url = c.images[0]
            if c.image_bytes:
                save_thumb(db, p.id, c.image_bytes)
            if li.in_stock is not None and p.in_stock is None:
                p.in_stock = li.in_stock
            if c.page and c.page.price:
                record_price(db, p, c.page.price, mrp=c.page.mrp, in_stock=c.page.in_stock, source="page")
                priced += 1
            elif li.price and li.currency in (None, "INR"):
                record_price(db, p, li.price, mrp=li.extra.get("mrp"), in_stock=li.in_stock,
                             source="store" if li.channel == "store" else ("shopping" if li.channel == "shopping" else "lens"))
                p.scrape_ok = li.channel == "store"
                priced += 1
            db.add(SearchResult(search_id=search_id, product_id=p.id, rank=rank,
                                score=c.score.score, match_level=c.score.level,
                                signals=c.score.signals, sources=sorted(c.channels)))
            kept += 1
        db.commit()
    return {"results": kept, "priced": priced}


# --------------------------------------------------------------------------- entry
async def run_search(search_id: str, settings: Settings | None = None,
                     bd: BrightData | None = None, direct: Direct | None = None) -> None:
    s = settings or get_settings()
    own_bd = bd is None and s.brightdata_ready
    bd = bd or (BrightData(s) if s.brightdata_ready else None)
    own_direct = direct is None
    direct = direct or Direct()
    t0 = time.monotonic()
    try:
        with SessionLocal() as db:
            search = db.get(Search, search_id)
            img = db.get(QueryImage, search.image_id)
            image, image_id = img.data, img.id

        if bd is None and not captioner.enabled(s):
            raise RuntimeError("No search method is configured. Set LLM_PROVIDER=bedrock (recommended) "
                               "or Bright Data credentials.")

        _update(search_id, status="searching", stage="Reading the photo", progress=0.08)
        qf_task = asyncio.create_task(asyncio.to_thread(matcher.features, image))
        caption_task = asyncio.create_task(captioner.describe(image, s))
        lens_task = asyncio.gather(*[_lens(bd, s, image_id, image, t) for t in LENS_TABS]) if bd else None

        attrs = await caption_task
        listings: list[Listing] = []
        if attrs and attrs.get("queries"):
            _update(search_id, attributes=attrs, progress=0.2,
                    stage="Searching Amazon, Flipkart, Tata CLiQ, Myntra, AJIO and brand stores")
            public = s.public_base_url.rstrip("/")
            img_url = None if ("localhost" in public or "127.0.0.1" in public) else f"{public}/api/images/{image_id}.jpg"
            jobs = [direct.search_all(attrs["queries"], img_url)]
            if bd:
                jobs.append(_serp_text(bd, attrs["queries"]))
            for res in await asyncio.gather(*jobs, return_exceptions=True):
                if isinstance(res, Exception):
                    log.warning("search source failed: %s", res)
                else:
                    listings += res
        elif bd is None:
            raise RuntimeError("Couldn't read the photo. Try a clearer picture where the garment fills the frame.")
        if lens_task:
            listings += [li for group in await lens_task for li in group]

        cands = _collect(listings, s.max_candidates)
        if not cands:
            _update(search_id, status="done", stage="No shops found for this photo", progress=1.0,
                    finished_at=datetime.now(timezone.utc),
                    stats={"listings": len(listings), "candidates": 0, "results": 0,
                           "seconds": round(time.monotonic() - t0, 1), "errors": direct.errors})
            return

        _update(search_id, status="verifying", progress=0.45,
                stage=f"Comparing {len(cands)} listings with your photo")
        qf = await qf_task
        checked = await _verify(direct, bd, s, image, qf, cands)

        best = sorted(cands, key=lambda c: -(c.score.score if c.score else 0))
        to_price = [c for c in best if c.score and c.score.level in ("exact", "close")][: s.max_product_pages]
        _update(search_id, status="pricing", progress=0.75, stage="Checking sizes, stock and prices")
        await _price(direct, bd, to_price)

        stats = _persist(search_id, cands)
        stats.update(listings=len(listings), candidates=len(cands), seconds=round(time.monotonic() - t0, 1),
                     captioned=bool(attrs), claude_checked=checked, errors=direct.errors)
        _update(search_id, status="done", stage="Done", progress=1.0, stats=stats,
                finished_at=datetime.now(timezone.utc))
    except Exception as e:
        log.exception("search %s failed", search_id)
        _update(search_id, status="failed", stage="Failed", error=str(e)[:500],
                finished_at=datetime.now(timezone.utc))
    finally:
        if own_bd and bd:
            await bd.aclose()
        if own_direct:
            await direct.aclose()
