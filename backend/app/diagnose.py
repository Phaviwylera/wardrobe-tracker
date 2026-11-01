"""Check your Bright Data setup against real pages before deploying.

  python -m app.diagnose lens  https://example.com/some-dress.jpg   # raw Lens JSON + parsed listings
  python -m app.diagnose page  https://www.myntra.com/.../12345678/buy  # what the price extractor reads
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


if __name__ == "__main__":
    if len(sys.argv) != 3 or sys.argv[1] not in ("lens", "page"):
        print(__doc__)
        sys.exit(1)
    asyncio.run(lens(sys.argv[2]) if sys.argv[1] == "lens" else page(sys.argv[2]))
