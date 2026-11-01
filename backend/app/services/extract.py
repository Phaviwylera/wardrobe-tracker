"""Product-page parsing: price, MRP, stock, sizes, images.

Layered so one broken selector never loses the price:
  1. store-specific embedded state (Myntra __myx, AJIO / Nykaa __PRELOADED_STATE__,
     Amazon DOM, Shopify meta)  – most precise
  2. schema.org JSON-LD Product – present on most stores
  3. OpenGraph / product:* meta tags
  4. a guarded ₹ regex near the word "price" – last resort
Earlier layers win field-by-field; later layers only fill gaps.
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from typing import Any

from selectolax.lexbor import LexborHTMLParser as HTMLParser

from .serp_parse import parse_price
from .stores import store_for


@dataclass
class ProductData:
    title: str | None = None
    brand: str | None = None
    price: float | None = None
    mrp: float | None = None
    currency: str | None = None
    in_stock: bool | None = None
    image_url: str | None = None
    images: list[str] = field(default_factory=list)
    sizes: list[dict] = field(default_factory=list)   # [{"label": "M", "available": True}]
    rating: float | None = None
    rating_count: int | None = None
    sources: list[str] = field(default_factory=list)  # which layers contributed

    def merge(self, other: "ProductData", tag: str) -> None:
        contributed = False
        for k, v in asdict(other).items():
            if k == "sources":
                continue
            cur = getattr(self, k)
            if (cur in (None, [], "")) and v not in (None, [], ""):
                setattr(self, k, v)
                contributed = True
        if contributed:
            self.sources.append(tag)

    def finalise(self) -> "ProductData":
        if self.price and self.mrp and self.mrp < self.price:
            self.mrp = None  # bad MRP parse; never show a negative discount
        if self.image_url is None and self.images:
            self.image_url = self.images[0]
        if self.currency is None and self.price is not None:
            self.currency = "INR"
        if self.title:
            self.title = re.sub(r"\s+", " ", self.title).strip()[:500]
        return self


# --------------------------------------------------------------------------- helpers
def extract_js_object(html: str, marker: str) -> Any | None:
    """Return the JSON object assigned after `marker` (e.g. 'window.__myx ='), brace-matched."""
    i = html.find(marker)
    if i < 0:
        return None
    start = html.find("{", i)
    if start < 0:
        return None
    depth, in_str, esc = 0, False, False
    quote = ""
    for j in range(start, min(len(html), start + 3_000_000)):
        ch = html[j]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == quote:
                in_str = False
            continue
        if ch in ('"', "'"):
            in_str, quote = True, ch
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                blob = html[start:j + 1]
                try:
                    return json.loads(blob)
                except json.JSONDecodeError:
                    try:  # some stores emit `undefined`
                        return json.loads(re.sub(r"\bundefined\b", "null", blob))
                    except json.JSONDecodeError:
                        return None
    return None


def _dig(d: Any, *path: Any) -> Any:
    for p in path:
        if isinstance(d, dict):
            d = d.get(p)
        elif isinstance(d, list) and isinstance(p, int) and -len(d) <= p < len(d):
            d = d[p]
        else:
            return None
    return d


def _num(v: Any) -> float | None:
    return parse_price(v)[0]


def _stock_from(value: Any) -> bool | None:
    if value is None:
        return None
    s = str(value).lower()
    if any(w in s for w in ("outofstock", "out of stock", "soldout", "sold out", "discontinued", "unavailable")):
        return False
    if any(w in s for w in ("instock", "in stock", "limitedavailability", "preorder", "available")):
        return True
    return None


# --------------------------------------------------------------------------- layers
def from_jsonld(tree: HTMLParser) -> ProductData:
    pd = ProductData()
    for node in tree.css('script[type="application/ld+json"]'):
        try:
            data = json.loads(node.text(strip=True) or "null")
        except json.JSONDecodeError:
            continue
        stack = [data]
        while stack:
            cur = stack.pop()
            if isinstance(cur, list):
                stack.extend(cur)
                continue
            if not isinstance(cur, dict):
                continue
            if "@graph" in cur:
                stack.extend(cur["@graph"] if isinstance(cur["@graph"], list) else [cur["@graph"]])
            t = cur.get("@type")
            types = t if isinstance(t, list) else [t]
            if not any(x in ("Product", "ProductGroup", "IndividualProduct") for x in types):
                continue
            one = ProductData(title=cur.get("name"))
            brand = cur.get("brand")
            one.brand = brand.get("name") if isinstance(brand, dict) else brand if isinstance(brand, str) else None
            img = cur.get("image")
            if isinstance(img, str):
                one.images = [img]
            elif isinstance(img, list):
                one.images = [i if isinstance(i, str) else i.get("url") for i in img
                              if isinstance(i, (str, dict))][:8]
            elif isinstance(img, dict):
                one.images = [img.get("url")] if img.get("url") else []
            offers = cur.get("offers")
            if not offers and isinstance(cur.get("hasVariant"), list):
                offers = [v.get("offers") for v in cur["hasVariant"] if isinstance(v, dict)]
            offer_list = offers if isinstance(offers, list) else [offers] if offers else []
            prices, stock = [], []
            for o in offer_list:
                if isinstance(o, list):
                    offer_list.extend(o)
                    continue
                if not isinstance(o, dict):
                    continue
                p = _num(o.get("price")) or _num(o.get("lowPrice"))
                if p:
                    prices.append(p)
                spec = o.get("priceSpecification")
                specs = spec if isinstance(spec, list) else [spec] if spec else []
                for sp in specs:
                    if isinstance(sp, dict) and "ListPrice" in str(sp.get("priceType", "")):
                        one.mrp = _num(sp.get("price"))
                one.currency = one.currency or o.get("priceCurrency")
                st = _stock_from(o.get("availability"))
                if st is not None:
                    stock.append(st)
            if prices:
                one.price = min(prices)
            if stock:
                one.in_stock = any(stock)
            agg = cur.get("aggregateRating")
            if isinstance(agg, dict):
                one.rating = _num(agg.get("ratingValue"))
                rc = _num(agg.get("reviewCount") or agg.get("ratingCount"))
                one.rating_count = int(rc) if rc else None
            pd.merge(one, "jsonld")
    return pd


def from_meta(tree: HTMLParser) -> ProductData:
    def meta(*names: str) -> str | None:
        for n in names:
            node = tree.css_first(f'meta[property="{n}"]') or tree.css_first(f'meta[name="{n}"]') \
                or tree.css_first(f'meta[itemprop="{n}"]')
            if node and node.attributes.get("content"):
                return node.attributes["content"]
        return None

    pd = ProductData(
        title=meta("og:title", "twitter:title"),
        brand=meta("product:brand", "og:brand", "brand"),
        price=_num(meta("product:price:amount", "og:price:amount", "product:sale_price:amount", "price")),
        mrp=_num(meta("product:original_price:amount", "og:original_price:amount")),
        currency=meta("product:price:currency", "og:price:currency", "priceCurrency"),
        in_stock=_stock_from(meta("product:availability", "og:availability", "availability")),
    )
    img = meta("og:image:secure_url", "og:image", "twitter:image")
    if img:
        pd.images = [img]
    return pd


def from_myntra(html: str) -> ProductData:
    d = extract_js_object(html, "window.__myx")
    p = _dig(d, "pdpData")
    if not isinstance(p, dict):
        return ProductData()
    images = [im.get("imageURL") or im.get("src") for im in (_dig(p, "media", "albums", 0, "images") or [])
              if isinstance(im, dict)]
    sizes = [{"label": s.get("label"), "available": bool(s.get("available"))}
             for s in p.get("sizes") or [] if isinstance(s, dict) and s.get("label")]
    price = _dig(p, "price", "discounted") or _dig(p, "price", "mrp")
    return ProductData(
        title=p.get("name"), brand=_dig(p, "brand", "name"),
        price=_num(price), mrp=_num(_dig(p, "price", "mrp")), currency="INR",
        in_stock=any(s["available"] for s in sizes) if sizes else None,
        images=[i for i in images if i][:8], sizes=sizes,
        rating=_num(_dig(p, "ratings", "averageRating")),
        rating_count=int(_dig(p, "ratings", "totalCount") or 0) or None,
    )


def from_preloaded_state(html: str) -> ProductData:
    """AJIO and Nykaa Fashion ship a Redux store as window.__PRELOADED_STATE__."""
    d = extract_js_object(html, "window.__PRELOADED_STATE__")
    if not isinstance(d, dict):
        return ProductData()
    # AJIO
    p = _dig(d, "product", "productDetails")
    if isinstance(p, dict) and p.get("name"):
        imgs = [i.get("url") for i in p.get("images") or [] if isinstance(i, dict) and i.get("url")]
        sizes = []
        for opt in _dig(p, "variantOptions") or []:
            label = next((q.get("value") for q in opt.get("variantOptionQualifiers") or []
                          if q.get("qualifier") == "size"), None)
            if label:
                sizes.append({"label": label, "available": _dig(opt, "stock", "stockLevelStatus") != "outOfStock"})
        return ProductData(
            title=p.get("name"), brand=p.get("brandName") or _dig(p, "fnlColorVariantData", "brandName"),
            price=_num(_dig(p, "price", "value")), mrp=_num(_dig(p, "wasPriceData", "value")),
            currency="INR", in_stock=_stock_from(_dig(p, "stock", "stockLevelStatus")),
            images=imgs[:8], sizes=sizes,
        )
    # Nykaa Fashion
    p = _dig(d, "details", "skuData", "product") or _dig(d, "productPage", "product")
    if isinstance(p, dict) and (p.get("title") or p.get("name")):
        return ProductData(
            title=p.get("title") or p.get("name"), brand=p.get("brandName") or p.get("brand_name"),
            price=_num(p.get("discountedPrice") or p.get("price")), mrp=_num(p.get("mrp")),
            currency="INR", in_stock=p.get("inStock") if isinstance(p.get("inStock"), bool) else None,
        )
    return ProductData()


def from_amazon(tree: HTMLParser) -> ProductData:
    def text(sel: str) -> str | None:
        n = tree.css_first(sel)
        return n.text(strip=True) if n else None

    price = text("#corePriceDisplay_desktop_feature_div .a-price .a-offscreen") \
        or text("#corePrice_feature_div .a-offscreen") or text(".priceToPay .a-offscreen") \
        or text("#priceblock_dealprice") or text("#priceblock_ourprice")
    mrp = text("#corePriceDisplay_desktop_feature_div .a-text-price .a-offscreen") \
        or text(".basisPrice .a-offscreen")
    avail = text("#availability")
    img = tree.css_first("#landingImage")
    image = None
    if img:
        image = img.attributes.get("data-old-hires") or img.attributes.get("src")
    sizes = [{"label": n.text(strip=True), "available": "unavailable" not in (n.attributes.get("class") or "")}
             for n in tree.css("#native_dropdown_selected_size_name option")[1:] if n.text(strip=True)]
    rating = text("#acrPopover .a-icon-alt")
    count = text("#acrCustomerReviewText")
    return ProductData(
        title=text("#productTitle"),
        brand=(text("#bylineInfo") or "").replace("Visit the", "").replace("Store", "").replace("Brand:", "").strip() or None,
        price=_num(price), mrp=_num(mrp), currency="INR",
        in_stock=_stock_from(avail), images=[image] if image else [], sizes=sizes,
        rating=_num(rating.split(" ")[0]) if rating else None,
        rating_count=int(_num(count) or 0) or None,
    )


def from_shopify(html: str) -> ProductData:
    """Shopify brand stores (Libas, Snitch, Berrylush, …) expose ShopifyAnalytics.meta."""
    d = extract_js_object(html, "var meta =")
    p = _dig(d, "product")
    if not isinstance(p, dict) or not p.get("variants"):
        return ProductData()
    variants = [v for v in p["variants"] if isinstance(v, dict)]
    prices = [v.get("price") for v in variants if isinstance(v.get("price"), (int, float))]
    sizes = [{"label": v.get("public_title") or v.get("name"), "available": None} for v in variants
             if v.get("public_title")]
    return ProductData(
        brand=p.get("vendor"),
        price=min(prices) / 100 if prices else None,  # Shopify analytics prices are in paise
        sizes=sizes,
    )


_PRICE_NEAR = re.compile(r"(?:price|offer|deal|now)[^₹<]{0,80}₹\s?([\d,]+(?:\.\d{1,2})?)", re.I)


def from_regex(html: str) -> ProductData:
    m = _PRICE_NEAR.search(html)
    return ProductData(price=_num(m.group(1)), currency="INR") if m else ProductData()


# --------------------------------------------------------------------------- entry
def extract_product(html: str, url: str) -> ProductData:
    tree = HTMLParser(html)
    store = store_for(url).key
    pd = ProductData()
    if store == "myntra":
        pd.merge(from_myntra(html), "myntra")
    elif store in ("ajio", "nykaafashion"):
        pd.merge(from_preloaded_state(html), "preloaded_state")
    elif store == "amazon":
        pd.merge(from_amazon(tree), "amazon_dom")
    pd.merge(from_jsonld(tree), "jsonld")
    if "ShopifyAnalytics" in html:
        pd.merge(from_shopify(html), "shopify")
    pd.merge(from_meta(tree), "meta")
    if pd.price is None:
        pd.merge(from_regex(html), "regex")
    if not pd.title:
        t = tree.css_first("title")
        if t:
            pd.title = t.text(strip=True)
    return pd.finalise()


def looks_blocked(html: str) -> bool:
    low = html[:20000].lower()
    return any(s in low for s in ("captcha", "access denied", "are you a robot", "request blocked",
                                  "enable javascript and cookies"))
