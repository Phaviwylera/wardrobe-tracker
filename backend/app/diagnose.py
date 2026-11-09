"""Check your Bright Data setup against real pages before deploying.

  python -m app.diagnose lens  https://example.com/some-dress.jpg   # raw Lens JSON + parsed listings
  python -m app.diagnose page  https://www.myntra.com/.../12345678/buy  # what the price extractor reads
  python -m app.diagnose direct "pink anarkali kurta"                 # keyless store searches
  python -m app.diagnose claude  https://example.com/some-dress.jpg   # Claude's description of a photo
"""
from __future__ import annotations

import asyncio
import json
import sys

from .services.brightdata import BrightData
from .services.extract import extract_product, looks_blocked
from .services.serp_parse import extract_listings
from .services.stores import is_product_url, store_info


async def lens(image_url: str) -> None:
    bd = BrightData()
    try:
        for tab in ("exact_matches", "products", "visual_matches"):
            doc = await bd.lens_by_url(image_url, tab)
            with open(f"lens_{tab}.json", "w") as f:
                json.dump(doc, f, indent=2, ensure_ascii=False)
            ls = extract_listings(doc, f"lens:{tab}")
            print(f"\n== {tab}: {len(ls)} listings (raw saved to lens_{tab}.json)")
            for li in ls[:15]:
                keep = "keep" if is_product_url(li.url) else "drop"
                print(f"  [{keep}] {store_info(li.url)[1]:<14} {li.price or '':>8}  {li.title[:60]}")
    finally:
        await bd.aclose()


async def page(url: str) -> None:
    bd = BrightData()
    try:
        html = await bd.fetch_html(url)
    finally:
        await bd.aclose()
    print(f"{len(html):,} bytes, blocked={looks_blocked(html)}")
    d = extract_product(html, url)
    print(json.dumps(d.__dict__, indent=2, ensure_ascii=False, default=str))


async def direct(q: str) -> None:
    from .services.direct import BLOCKED_STORES, SHOPIFY_STORES, Direct
    d = Direct()
    try:
        jobs = {"amazon": d.amazon(q), "flipkart": d.flipkart(q), "tatacliq": d.tatacliq(q)}
        jobs.update({f"bing:{s}": d.bing_images(q, s) for s in BLOCKED_STORES})
        jobs.update({f"shopify:{h}": d.shopify(h, q) for h in SHOPIFY_STORES})
        res = await asyncio.gather(*jobs.values(), return_exceptions=True)
        report = {}
        for name, r in zip(jobs, res):
            if isinstance(r, Exception):
                report[name] = {"error": str(r)[:200]}
                continue
            report[name] = {"n": len(r), "priced": sum(1 for x in r if x.price), "imaged": sum(1 for x in r if x.image_url),
                            "sample": [{"title": x.title[:70], "price": x.price, "mrp": x.extra.get("mrp"),
                                        "url": x.url[:110], "img": (x.image_url or "")[:90]} for x in r[:2]]}
        sample = next((x for r in res if isinstance(r, list) for x in r if x.channel == "store"), None)
        if sample:
            html = await d.fetch_page(sample.url)
            from .services.extract import extract_product
            pd = extract_product(html, sample.url) if html else None
            report["_page_check"] = {"url": sample.url, "fetched": bool(html),
                                     "price": pd.price if pd else None, "mrp": pd.mrp if pd else None,
                                     "sizes": len(pd.sizes) if pd else None, "sources": pd.sources if pd else None}
        report["_errors"] = d.errors
        print(json.dumps(report, ensure_ascii=False))
    finally:
        await d.aclose()


async def claude(image_url: str) -> None:
    from .services import captioner
    from .services.direct import Direct
    d = Direct()
    try:
        data = await d.fetch_image(image_url)
    finally:
        await d.aclose()
    print(json.dumps(await captioner.describe(data) if data else {"error": "image fetch failed"}, ensure_ascii=False))


if __name__ == "__main__":
    modes = {"lens": lens, "page": page, "direct": direct, "claude": claude}
    if len(sys.argv) != 3 or sys.argv[1] not in modes:
        print(__doc__)
        sys.exit(1)
    asyncio.run(modes[sys.argv[1]](sys.argv[2]))
