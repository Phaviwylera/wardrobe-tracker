"""Schema-tolerant extraction of product listings from Bright Data SERP JSON.

Bright Data's parsed Lens / Shopping JSON varies by tab and changes over time, so rather
than hard-coding one shape we walk the whole document and pick out every object that
looks like a listing (has an http link and a title), then normalise its fields.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterator
from urllib.parse import parse_qs, unquote, urlsplit

LINK_KEYS = ("link", "url", "product_link", "href", "page_url", "source_link")
TITLE_KEYS = ("title", "name", "product_title", "text")
IMAGE_KEYS = ("thumbnail", "image", "image_url", "img", "original", "thumbnail_url", "image_link")
PRICE_KEYS = ("price", "extracted_price", "price_str", "current_price", "sale_price", "displayed_price")
SOURCE_KEYS = ("source", "shop", "store", "merchant", "seller", "domain", "display_link")


@dataclass
class Listing:
    url: str
    title: str = ""
    image_url: str | None = None
    price: float | None = None
    currency: str | None = None
    source: str | None = None
    channel: str = ""              # lens:exact_matches | lens:products | shopping | organic …
    position: int = 0
    in_stock: bool | None = None
    extra: dict = field(default_factory=dict)


_CURRENCY = {"₹": "INR", "rs": "INR", "inr": "INR", "$": "USD", "usd": "USD", "€": "EUR", "£": "GBP"}
_NUM = re.compile(r"(\d{1,3}(?:[,\s]\d{2,3})+|\d+)(?:\.(\d{1,2}))?")


def parse_price(value: Any) -> tuple[float | None, str | None]:
    """'₹1,299.00' -> (1299.0, 'INR'). Handles Indian digit grouping (1,23,456)."""
    if value is None:
        return None, None
    if isinstance(value, (int, float)):
        return (float(value) if value > 0 else None), None
    if isinstance(value, dict):
        for k in ("extracted_value", "value", "amount", "price", "current"):
            if k in value:
                p, c = parse_price(value[k])
                return p, c or _currency_of(str(value.get("currency", "")))
        return None, None
    text = str(value).strip()
    if not text:
        return None, None
    cur = _currency_of(text)
    m = _NUM.search(text.replace(" ", " "))
    if not m:
        return None, cur
    whole = re.sub(r"[,\s]", "", m.group(1))
    try:
        num = float(whole + ("." + m.group(2) if m.group(2) else ""))
    except ValueError:
        return None, cur
    return (num if num > 0 else None), cur


def _currency_of(text: str) -> str | None:
    t = text.lower()
    for sym, code in _CURRENCY.items():
        if sym in t:
            return code
    return None


def unwrap_redirect(url: str) -> str:
    """Google sometimes wraps outbound links (/url?q=…, /aclk?…&adurl=…)."""
    parts = urlsplit(url)
    if parts.hostname and "google." in parts.hostname:
        q = parse_qs(parts.query)
        for k in ("q", "url", "adurl", "u"):
            if k in q and q[k][0].startswith("http"):
                return unquote(q[k][0])
    return url


def _first(d: dict, keys: tuple[str, ...]) -> Any:
    for k in keys:
        v = d.get(k)
        if v not in (None, "", [], {}):
            return v
    return None


def _walk(node: Any, path: tuple[str, ...] = ()) -> Iterator[tuple[dict, tuple[str, ...]]]:
    if isinstance(node, dict):
        yield node, path
        for k, v in node.items():
            yield from _walk(v, path + (str(k),))
    elif isinstance(node, list):
        for v in node:
            yield from _walk(v, path)


def extract_listings(doc: dict, channel: str) -> list[Listing]:
    out: list[Listing] = []
    seen: set[str] = set()
    for obj, path in _walk(doc):
        link = _first(obj, LINK_KEYS)
        title = _first(obj, TITLE_KEYS)
        if not isinstance(link, str) or not link.startswith("http") or not isinstance(title, str):
            continue
        link = unwrap_redirect(link)
        host = urlsplit(link).hostname or ""
        if "google." in host or "gstatic." in host or "googleusercontent." in host:
            continue
        if link in seen:
            continue
        seen.add(link)

        image = _first(obj, IMAGE_KEYS)
        if isinstance(image, dict):
            image = _first(image, ("link", "url", "src"))
        if isinstance(image, list):
            image = image[0] if image and isinstance(image[0], str) else None
        price, currency = parse_price(_first(obj, PRICE_KEYS))
        source = _first(obj, SOURCE_KEYS)
        if isinstance(source, dict):
            source = _first(source, ("name", "title", "link"))
        stock = obj.get("in_stock")
        if stock is None and isinstance(obj.get("availability"), str):
            stock = "out" not in obj["availability"].lower()

        pos = obj.get("rank") or obj.get("position")
        out.append(Listing(
            url=link,
            title=title.strip()[:500],
            image_url=image if isinstance(image, str) and image.startswith(("http", "data:image")) else None,
            price=price,
            currency=currency,
            source=source if isinstance(source, str) else None,
            channel=channel,
            position=int(pos) if str(pos or "").isdigit() else len(out) + 1,
            in_stock=stock if isinstance(stock, bool) else None,
            extra={"path": "/".join(path[-3:])},
        ))
    return out
