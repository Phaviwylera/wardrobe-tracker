"""Keyless discovery: search Indian stores directly from the server.

Works without Bright Data or any other paid API. Each searcher returns `Listing`s
(product URL, title, image, price) for one text query:

  amazon      amazon.in search results page
  flipkart    flipkart.com search results page
  tatacliq    Tata CLiQ's public search JSON
  shopify     Shopify brand stores' /search/suggest.json (Libas, Berrylush, …)
  bing        Bing Images with site: filters, to reach stores that block servers
              (Myntra, AJIO, Nykaa Fashion, Meesho); gives page + photo, no price

Product pages are refreshed later with `fetch_page`, which reads the page directly
and falls back to Bright Data's Web Unlocker only when one is configured.
"""
from __future__ import annotations

import asyncio
import html as htmlmod
import json
import logging
import re
from collections import defaultdict
from urllib.parse import quote, quote_plus, urljoin, urlsplit

import httpx
from selectolax.lexbor import LexborHTMLParser as HTMLParser

from .serp_parse import Listing, parse_price
from .stores import is_product_url, myntra_global_to_india, store_for

log = logging.getLogger(__name__)

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/130.0.0.0 Safari/537.36")
HEADERS = {"User-Agent": UA, "Accept-Language": "en-IN,en;q=0.9",
           "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8"}

# Shopify-based Indian women's wear brands with a public search endpoint.
SHOPIFY_STORES = [
    "www.libas.in", "www.berrylush.com", "thelabellife.com", "www.okhai.org",
    "www.globaldesi.in", "www.luluandsky.com", "www.jaypore.com", "www.houseofindya.com",
]
# Single-page-app stores whose product page has no server-rendered price; refreshed via their API.
SPA_STORES = ("tatacliq.com",)
# Stores that refuse server requests; found through Bing Images instead.
BLOCKED_STORES = ["myntra.com", "ajio.com", "nykaafashion.com", "meesho.com"]
# Single-page-app stores with no public search page; also found through Bing Images.
INDEXED_STORES = ["savana.com"]
STORE_WORD = {"myntra.com": "myntra", "ajio.com": "ajio", "nykaafashion.com": "nykaa fashion", "meesho.com": "meesho", "savana.com": "savana"}
DIRECT_BLOCKED = tuple(BLOCKED_STORES)


class Direct:
    def __init__(self, timeout: float = 25.0, per_host: int = 3):
        self.client = httpx.AsyncClient(headers=HEADERS, timeout=timeout, follow_redirects=True,
                                        http2=False)
        self._host_sem: dict[str, asyncio.Semaphore] = defaultdict(lambda: asyncio.Semaphore(per_host))
        # Images come from CDNs: more parallelism, short timeout so one slow host can't stall a search.
        self.img_client = httpx.AsyncClient(headers=HEADERS, timeout=httpx.Timeout(10.0, connect=5.0),
                                            follow_redirects=True)
        self._img_sem: dict[str, asyncio.Semaphore] = defaultdict(lambda: asyncio.Semaphore(8))
        self.errors: dict[str, str] = {}

    async def aclose(self) -> None:
        await self.client.aclose()
        await self.img_client.aclose()

    async def get(self, url: str, **headers) -> httpx.Response | None:
        host = urlsplit(url).hostname or ""
        async with self._host_sem[host]:
            for attempt in range(3):
                try:
                    r = await self.client.get(url, headers=headers or None)
                    if r.status_code in (429, 503) and attempt < 2:
                        await asyncio.sleep(3 * (attempt + 1))
                        continue
                    return r
                except httpx.HTTPError as e:
                    self.errors[host] = str(e)[:120]
                    if attempt == 0:
                        await asyncio.sleep(1)
        return None

    # ------------------------------------------------------------------ searchers
    async def amazon(self, q: str) -> list[Listing]:
        r = await self.get(f"https://www.amazon.in/s?k={quote_plus(q)}")
        if not r or r.status_code != 200:
            self._fail("amazon", r)
            return []
        return parse_amazon(r.text)

    async def flipkart(self, q: str) -> list[Listing]:
        r = await self.get(f"https://www.flipkart.com/search?q={quote_plus(q)}")
        if not r or r.status_code != 200:
            self._fail("flipkart", r)
            return []
        return parse_flipkart(r.text)

    async def tatacliq(self, q: str) -> list[Listing]:
        url = ("https://searchbff.tatacliq.com/products/mpl/search?searchText=" + quote(q) +
               "%3Arelevance&isKeywordRedirect=true&isKeywordRedirectEnabled=true&channel=WEB"
               "&isMDE=true&isTextSearch=false&isFilter=false&page=0&isSuggested=false&isPwa=true"
               "&pageSize=24&typeID=all")
        r = await self.get(url, Accept="application/json")
        if not r or r.status_code != 200:
            self._fail("tatacliq", r)
            return []
        try:
            return parse_tatacliq(r.json())
        except ValueError:
            return []

    async def shopify(self, host: str, q: str) -> list[Listing]:
        url = (f"https://{host}/search/suggest.json?q={quote(q)}"
               "&resources[type]=product&resources[limit]=10&resources[options][unavailable_products]=last")
        r = await self.get(url, Accept="application/json")
        if not r or r.status_code != 200 or "json" not in r.headers.get("content-type", ""):
            return []
        try:
            return parse_shopify(r.json(), host)
        except ValueError:
            return []

    async def bing_images(self, q: str, site: str) -> list[Listing]:
        # Bing ignores site: for server requests; naming the store in the query works.
        url = (f"https://www.bing.com/images/search?q={quote_plus(q + ' ' + STORE_WORD.get(site, site))}"
               "&form=HDRSC2&first=1&cc=IN&setlang=en-IN")
        r = await self.get(url)
        if not r or r.status_code != 200:
            self._fail("bing", r)
            return []
        return parse_bing_images(r.text, site)

    async def tatacliq_product(self, url: str) -> Listing | None:
        """Current price, stock and sizes for one Tata CLiQ product (public product-details API)."""
        m = re.search(r"/p-(mp\d+)", url, re.I)
        if not m:
            return None
        code = m.group(1).upper()
        r = await self.get(f"https://www.tatacliq.com/marketplacewebservices/v2/mpl/products/productDetails/{code}"
                           "?isPwa=true&isMDE=true", Accept="application/json")
        if not r or r.status_code != 200:
            return None
        try:
            return parse_tatacliq_pdp(r.json(), url)
        except ValueError:
            return None

    def _fail(self, name: str, r: httpx.Response | None) -> None:
        self.errors[name] = "no response" if r is None else f"HTTP {r.status_code}"

    async def yandex_similar(self, image_url: str) -> list[Listing]:
        """Yandex reverse image search: pages with the same or similar photo, anywhere on the web."""
        r = await self.get("https://yandex.com/images/search?rpt=imageview&cbir_page=sites&url=" + quote(image_url, safe=""))
        if not r or r.status_code != 200:
            self._fail("yandex", r)
            return []
        return parse_yandex(r.text)

    async def search_all(self, queries: list[str], image_url: str | None = None,
                         store_queries: list[str] | None = None) -> list[Listing]:
        store_queries = store_queries or []
        jobs: list = []
        marketplace_qs = queries[:4] + store_queries[:1]
        for q in marketplace_qs:
            jobs += [self.amazon(q), self.flipkart(q), self.tatacliq(q)]
        for q in queries[:3]:
            jobs += [self.shopify(h, q) for h in SHOPIFY_STORES]
        # Myntra & AJIO are found through Bing; their own title wording finds the exact listing best.
        bing_qs = store_queries[:3] + queries[:1]
        for site in BLOCKED_STORES + INDEXED_STORES:
            for q in (bing_qs if site in ("myntra.com", "ajio.com") else bing_qs[:2]):
                jobs.append(self.bing_images(q, site))
        if image_url:
            jobs.append(self.yandex_similar(image_url))
        out: list[Listing] = []
        for res in await asyncio.gather(*jobs, return_exceptions=True):
            if isinstance(res, Exception):
                log.warning("direct search failed: %s", res)
                continue
            out += res
        return out

    # ------------------------------------------------------------------ pages / images
    async def fetch_page(self, url: str) -> str | None:
        host = urlsplit(url).hostname or ""
        if any(host.endswith(d) for d in DIRECT_BLOCKED + SPA_STORES):
            return None
        r = await self.get(url)
        if r is None or r.status_code != 200:
            return None
        return r.text

    async def fetch_image(self, url: str) -> bytes | None:
        host = urlsplit(url).hostname or ""
        if not host:
            return None
        try:
            async with self._img_sem[host]:
                r = await asyncio.wait_for(self.img_client.get(
                    url, headers={"Accept": "image/avif,image/webp,image/*,*/*;q=0.8",
                                  "Referer": f"https://{host}/"}), 12)
        except Exception:
            return None
        if r.status_code == 200 and r.headers.get("content-type", "").startswith("image") and len(r.content) > 1000:
            return r.content
        return None


# ---------------------------------------------------------------------- parsers
def _abs(base: str, href: str) -> str:
    return urljoin(base, htmlmod.unescape(href))


def parse_amazon(page: str) -> list[Listing]:
    tree = HTMLParser(page)
    out: list[Listing] = []
    for card in tree.css('div[data-component-type="s-search-result"][data-asin]'):
        asin = card.attributes.get("data-asin") or ""
        if not re.fullmatch(r"[A-Z0-9]{10}", asin):
            continue
        if card.css_first(".puis-sponsored-label-text, .s-sponsored-label-info-icon"):
            sponsored = True
        else:
            sponsored = False
        img = card.css_first("img.s-image")
        title = ""
        h2 = card.css_first("h2")
        if h2:
            title = h2.attributes.get("aria-label") or h2.text(strip=True)
        brand_node = card.css_first("h2 + div h2, .a-row.a-size-base-plus, span.a-size-base-plus.a-color-base")
        brand = brand_node.text(strip=True) if brand_node else ""
        alt = img.attributes.get("alt") if img else ""
        title = re.sub(r"^\s*Sponsored\s+Ad\s*[-–]\s*", "", title or "", flags=re.I)
        if alt and (not title or len(title) < 25 or len(alt) > len(title)):
            title = alt
        if brand and title and not title.lower().startswith(brand.lower()):
            title = f"{brand} {title}"
        title = re.sub(r"\s*Sponsored\s+Ad\s*[-–]\s*", " ", title or "", flags=re.I).strip()
        price_node = card.css_first("span.a-price:not(.a-text-price) span.a-offscreen")
        mrp_node = card.css_first("span.a-price.a-text-price span.a-offscreen")
        price = parse_price(price_node.text(strip=True))[0] if price_node else None
        mrp = parse_price(mrp_node.text(strip=True))[0] if mrp_node else None
        out.append(Listing(
            url=f"https://www.amazon.in/dp/{asin}", title=title[:300],
            image_url=img.attributes.get("src") if img else None,
            price=price, currency="INR", source="Amazon.in", channel="store",
            position=len(out) + 1, extra={"mrp": mrp, "sponsored": sponsored},
        ))
    return out[:24]


def parse_flipkart(page: str) -> list[Listing]:
    tree = HTMLParser(page)
    out: list[Listing] = []
    seen: set[str] = set()
    for card in tree.css("div[data-id]"):
        pid = card.attributes.get("data-id") or ""
        link = card.css_first('a[href*="/p/itm"]')
        if not link or not pid or pid in seen:
            continue
        seen.add(pid)
        href = _abs("https://www.flipkart.com", link.attributes.get("href") or "")
        parts = urlsplit(href)
        url = f"https://www.flipkart.com{parts.path}?pid={pid}"
        img = None
        for im in card.css("img"):
            src = im.attributes.get("src") or ""
            if "rukminim" in src or "flixcart.com/image" in src:
                img = re.sub(r"/image/\d+/\d+/", "/image/612/612/", src)
                break
        title_a = card.css_first("a[title]")
        title = title_a.attributes.get("title") if title_a else (link.text(strip=True) or "")
        brand = ""
        for d in card.css("div"):
            t = d.text(strip=True)
            leaf = not any(ch.tag in ("div", "a", "img") for ch in d.iter())
            if t and len(t) < 40 and leaf and "₹" not in t and "off" not in t.lower():
                brand = t
                break
        if brand and title and not title.lower().startswith(brand.lower()):
            title = f"{brand} {title}"
        amounts = []
        for node in card.css("div, span"):
            if any(ch.tag in ("div", "span") for ch in node.iter()):
                continue
            t = node.text(strip=True)
            m = re.fullmatch(r"₹\s?([\d,]+(?:\.\d+)?)", t)
            if m:
                amounts.append(parse_price(m.group(1))[0])
        amounts = [a for a in amounts if a]
        price = amounts[0] if amounts else None
        mrp = amounts[1] if len(amounts) > 1 and amounts[1] > (price or 0) else None
        out.append(Listing(url=url, title=(title or "")[:300], image_url=img, price=price,
                           currency="INR", source="Flipkart", channel="store",
                           position=len(out) + 1, extra={"mrp": mrp}))
    return out[:24]


def parse_tatacliq(doc: dict) -> list[Listing]:
    out: list[Listing] = []
    for it in doc.get("searchresult") or []:
        web = it.get("webURL") or ""
        if not web:
            continue
        url = _abs("https://www.tatacliq.com", web)
        img = it.get("imageURL") or ""
        if img.startswith("//"):
            img = "https:" + img
        price = it.get("price") or {}
        sp = (price.get("sellingPrice") or {}).get("doubleValue") or (price.get("sellingPrice") or {}).get("value")
        mrp = (price.get("mrpPrice") or {}).get("doubleValue") or (price.get("mrpPrice") or {}).get("value")
        title = " ".join(x for x in (it.get("brandname"), it.get("productname")) if x)
        if it.get("brandname") and (it.get("productname") or "").lower().startswith(it["brandname"].lower()):
            title = it["productname"]
        out.append(Listing(url=url, title=title[:300], image_url=img or None,
                           price=parse_price(sp)[0], currency="INR", source="Tata CLiQ", channel="store",
                           position=len(out) + 1,
                           in_stock=bool(it.get("inStockFlag")) if it.get("inStockFlag") is not None else None,
                           extra={"mrp": parse_price(mrp)[0]}))
    return out


def parse_shopify(doc: dict, host: str) -> list[Listing]:
    products = (((doc.get("resources") or {}).get("results") or {}).get("products")) or []
    out: list[Listing] = []
    for p in products:
        url = p.get("url") or ""
        if not url:
            continue
        url = _abs(f"https://{host}", url.split("?")[0])
        img = p.get("image") or (p.get("featured_image") or {}).get("url")
        if isinstance(img, str) and img.startswith("//"):
            img = "https:" + img
        price = parse_price(p.get("price") or p.get("price_min"))[0]
        mrp = parse_price(p.get("compare_at_price_max") or p.get("compare_at_price_min"))[0]
        out.append(Listing(url=url, title=(p.get("title") or "")[:300], image_url=img,
                           price=price, currency="INR", source=p.get("vendor") or host, channel="store",
                           position=len(out) + 1,
                           in_stock=p.get("available") if isinstance(p.get("available"), bool) else None,
                           extra={"mrp": mrp if mrp and price and mrp > price else None}))
    return out


def parse_bing_images(page: str, site: str) -> list[Listing]:
    out: list[Listing] = []
    seen: set[str] = set()
    for m in re.finditer(r'\bm="(\{[^"]*?\})"', page):
        try:
            d = json.loads(htmlmod.unescape(m.group(1)))
        except ValueError:
            continue
        purl, murl = d.get("purl") or "", d.get("murl") or ""
        purl = myntra_global_to_india(purl)
        host = urlsplit(purl).hostname or ""
        if not host.endswith(site) or purl in seen or not is_product_url(purl):
            continue
        seen.add(purl)
        title = htmlmod.unescape(d.get("t") or "").replace("", "").replace("", "")
        title = re.sub(r"\s*[|\-–]\s*(Myntra|AJIO|Nykaa Fashion|Meesho|Savana).*$", "", title, flags=re.I)
        turl = d.get("turl")
        out.append(Listing(url=purl, title=title[:300], image_url=murl or turl,
                           price=None, source=site, channel="web", position=len(out) + 1,
                           extra={"alt_images": [turl] if murl and turl else []}))
    return out[:30]


def parse_yandex(page: str) -> list[Listing]:
    """Read the "sites" list (pages showing the same or similar image) from Yandex's page state."""
    out: list[Listing] = []
    seen: set[str] = set()
    for blob in re.findall(r'data-state="(\{.*?\})"', page):
        text = htmlmod.unescape(blob)
        if '"sites"' not in text:
            continue
        try:
            state = json.loads(text)
        except ValueError:
            continue
        stack = [state]
        while stack:
            cur = stack.pop()
            if isinstance(cur, dict):
                sites = cur.get("sites")
                if isinstance(sites, list):
                    for site in sites:
                        if not isinstance(site, dict):
                            continue
                        url = site.get("url") or ""
                        url = myntra_global_to_india(re.sub(r"[?&]utm_[^&]+", "", url).rstrip("?&"))
                        # only known shops: the open web returns many junk "product" pages
                        if (not url.startswith("http") or url in seen or not is_product_url(url)
                                or store_for(url).key == "other"):
                            continue
                        seen.add(url)
                        img = (site.get("originalImage") or {}).get("url") or (site.get("thumb") or {}).get("url")
                        if img and img.startswith("//"):
                            img = "https:" + img
                        out.append(Listing(url=url, title=(site.get("title") or "")[:300], image_url=img,
                                           price=None, source=site.get("domain"), channel="web",
                                           position=len(out) + 1))
                stack.extend(v for v in cur.values() if isinstance(v, (dict, list)))
            elif isinstance(cur, list):
                stack.extend(cur)
    return out[:30]


def parse_tatacliq_pdp(d: dict, url: str) -> Listing | None:
    def amount(node):
        if not isinstance(node, dict):
            return None
        return parse_price(node.get("doubleValue") or node.get("value") or node.get("commaFormattedValue"))[0]

    price = amount(d.get("winningSellerPrice")) or amount(d.get("sellingPrice"))
    if not price:
        return None
    mrp = amount(d.get("mrpPrice")) or amount(d.get("mrp"))
    sizes = []
    for v in d.get("variantOptions") or []:
        size = (v.get("sizelink") or {}) if isinstance(v, dict) else {}
        if size.get("size"):
            sizes.append({"label": size["size"], "available": bool(size.get("isAvailable", True))})
    img = None
    for g in d.get("galleryImagesList") or []:
        for k in g.get("galleryImages") or []:
            if k.get("key") in ("product", "superZoom", "zoom") and k.get("value"):
                img = k["value"]
                break
        if img:
            break
    if img and img.startswith("//"):
        img = "https:" + img
    title = " ".join(x for x in (d.get("brandName"), d.get("productName")) if x)
    if d.get("brandName") and (d.get("productName") or "").lower().startswith(d["brandName"].lower()):
        title = d["productName"]
    return Listing(url=url, title=title[:300], image_url=img, price=price, currency="INR", source="Tata CLiQ",
                   channel="store", in_stock=not bool(d.get("allOOStock")),
                   extra={"mrp": mrp if mrp and mrp > price else None, "sizes": sizes})
