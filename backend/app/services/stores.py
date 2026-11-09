"""Registry of Indian (and India-shipping) fashion stores.

Each store knows how to recognise one of its *product* pages (so category/listing
pages that Lens sometimes returns are dropped) and how to canonicalise the URL, so the
same product reached through different tracking links collapses to one price history.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit


@dataclass(frozen=True)
class Store:
    key: str
    name: str
    domains: tuple[str, ...]
    product_re: str | None = None          # regex on the URL path(+query); None = accept any path
    keep_params: tuple[str, ...] = ()       # query params that identify the product
    tier: int = 2                           # 1 = major marketplace, 2 = brand/boutique
    canon: str | None = None                # special canonicaliser name
    extra: dict = field(default_factory=dict)


STORES: list[Store] = [
    Store("myntra", "Myntra", ("myntra.com",), r"/\d{5,}(/buy)?/?$", tier=1, canon="myntra"),
    Store("ajio", "AJIO", ("ajio.com",), r"/p/[\w-]+", tier=1),
    Store("flipkart", "Flipkart", ("flipkart.com",), r"/p/itm", keep_params=("pid",), tier=1),
    Store("amazon", "Amazon.in", ("amazon.in",), r"/(dp|gp/product)/[A-Z0-9]{10}", tier=1, canon="amazon"),
    Store("nykaafashion", "Nykaa Fashion", ("nykaafashion.com",), r"/p/\d+", tier=1),
    Store("meesho", "Meesho", ("meesho.com",), r"/p/\w+", tier=1),
    Store("tatacliq", "Tata CLiQ", ("tatacliq.com", "luxury.tatacliq.com"), r"/p-mp\w+", tier=1),
    Store("shoppersstop", "Shoppers Stop", ("shoppersstop.com",), r"/p-\w+|/[\w-]+/p/\w+", tier=1),
    Store("lifestyle", "Lifestyle", ("lifestylestores.com",), r"/p/[\w-]+"),
    Store("maxfashion", "Max Fashion", ("maxfashion.in",), r"/p/[\w-]+"),
    Store("westside", "Westside", ("westside.com",), r"/products/"),
    Store("pantaloons", "Pantaloons", ("pantaloons.com",), r"/p/|/products/"),
    Store("libas", "Libas", ("libas.in",), r"/products/"),
    Store("biba", "BIBA", ("biba.in",), r"\.html$|/products/"),
    Store("wforwoman", "W for Woman", ("wforwoman.com",), r"/products/|\.html$"),
    Store("aurelia", "Aurelia", ("shopaurelia.com",), r"/products/|\.html$"),
    Store("fabindia", "Fabindia", ("fabindia.com",), r"/[\w-]+-\d+$|/p/"),
    Store("houseofindya", "Indya", ("houseofindya.com",), r"/[\w-]+/\d+/|/products/"),
    Store("jaypore", "Jaypore", ("jaypore.com",), r"/[\w-]+-p\d+|/products/"),
    Store("faballey", "FabAlley", ("faballey.com",), r"/products/|/[\w-]+-\d+"),
    Store("urbanic", "Urbanic", ("urbanic.com",), r"/details/|/product/"),
    Store("zara", "Zara India", ("zara.com",), r"/in/.+-p\d+\.html"),
    Store("hm", "H&M India", ("hm.com", "www2.hm.com"), r"/en_in/productpage\.\d+"),
    Store("uniqlo", "UNIQLO India", ("uniqlo.com",), r"/in/.*/products/"),
    Store("mango", "Mango India", ("mango.com", "shop.mango.com"), r"/in/"),
    Store("bewakoof", "Bewakoof", ("bewakoof.com",), r"/p/"),
    Store("snitch", "Snitch", ("snitch.co.in", "snitch.com"), r"/products/"),
    Store("berrylush", "Berrylush", ("berrylush.com",), r"/products/"),
    Store("thelabellife", "The Label Life", ("thelabellife.com",), r"/products/"),
    Store("lulusky", "Lulu & Sky", ("luluandsky.com",), r"/products/|\.html$"),
    Store("kalki", "Kalki Fashion", ("kalkifashion.com",), r"\.html$|/products/"),
    Store("taneira", "Taneira", ("taneira.com",), r"/products/|\.html$|/p/"),
    Store("okhai", "Okhai", ("okhai.org",), r"/products/"),
    Store("globaldesi", "Global Desi", ("globaldesi.in",), r"/products/|\.html$"),
    Store("andfashion", "AND", ("andindia.com",), r"/products/|\.html$"),
    Store("chumbak", "Chumbak", ("chumbak.com",), r"/products/|/[\w-]+/p/"),
    Store("shein", "SHEIN India", ("sheinindia.in", "shein.in"), r"/p/|-p-\d+"),
    Store("savana", "Savana", ("savana.com",), r"/details/|/product/"),
    Store("forever21", "Forever 21 India", ("forever21.in",), r"/products/|/p/"),
    Store("etsy", "Etsy", ("etsy.com",), r"/listing/\d+", tier=2),
]

# Domains that are never shops: social, image hosts, blogs, search engines.
NON_SHOP_DOMAINS = {
    "pinterest.com", "pinterest.in", "in.pinterest.com", "instagram.com", "facebook.com",
    "youtube.com", "twitter.com", "x.com", "reddit.com", "tumblr.com", "quora.com",
    "google.com", "lens.google.com", "wikipedia.org", "medium.com", "blogspot.com",
    "wordpress.com", "tiktok.com", "threads.net", "lookbook.nu", "weheartit.com",
    "dreamstime.com", "shutterstock.com", "freepik.com", "alamy.com", "gettyimages.com",
}

_DOMAIN_INDEX: dict[str, Store] = {d: s for s in STORES for d in s.domains}
OTHER = Store("other", "Other store", ())


def registrable_host(url: str) -> str:
    host = (urlsplit(url).hostname or "").lower()
    for prefix in ("www.", "m.", "www2.", "shop.", "in."):
        if host.startswith(prefix) and host.count(".") >= 2:
            host = host[len(prefix):]
    return host


def store_for(url: str) -> Store:
    host = registrable_host(url)
    full = (urlsplit(url).hostname or "").lower()
    for candidate in (full, host):
        while candidate:
            if candidate in _DOMAIN_INDEX:
                return _DOMAIN_INDEX[candidate]
            if "." not in candidate:
                break
            candidate = candidate.split(".", 1)[1]
    return OTHER


def is_non_shop(url: str) -> bool:
    host = registrable_host(url)
    parts = host.split(".")
    return any(".".join(parts[i:]) in NON_SHOP_DOMAINS for i in range(len(parts)))


def is_product_url(url: str) -> bool:
    """True if this looks like a single product page (not a listing / search / blog)."""
    if is_non_shop(url):
        return False
    store = store_for(url)
    parts = urlsplit(url)
    target = parts.path + ("?" + parts.query if parts.query else "")
    if store.product_re:
        return re.search(store.product_re, target) is not None
    # Unknown store: accept common product-page shapes, reject obvious listings.
    if re.search(r"/(search|category|categories|collections/?$|blog|tag|tags)(/|$|\?)", parts.path, re.I):
        return False
    return bool(re.search(r"/products?/|/p/|/dp/|/item/|/listing/|-p-?\d+|\.html$|/\d{5,}", target, re.I))


def myntra_global_to_india(url: str) -> str:
    """myntraglobal.com/products/<slug>-<styleid> is the same catalogue item as myntra.com/<styleid>."""
    m = re.match(r"https?://(?:www\.)?myntraglobal\.com/products/[^?#]*?-(\d{5,})/?(?:[?#].*)?$", url.strip())
    return f"https://www.myntra.com/{m.group(1)}" if m else url


def canonical_url(url: str) -> str:
    url = myntra_global_to_india(url)
    parts = urlsplit(url.strip())
    store = store_for(url)
    host = (parts.hostname or "").lower()
    if host.startswith("m.") and store.key not in ("other",):
        host = "www." + host[2:]
    path = re.sub(r"/{2,}", "/", parts.path).rstrip("/") or "/"

    if store.canon == "myntra":
        m = re.search(r"/(\d{5,})(?:/buy)?/?$", parts.path)
        if m:
            return f"https://www.myntra.com/{m.group(1)}"

    if store.canon == "amazon":
        m = re.search(r"/(?:dp|gp/product)/([A-Z0-9]{10})", parts.path)
        if m:
            return f"https://www.amazon.in/dp/{m.group(1)}"

    q = parse_qs(parts.query)
    kept = {k: q[k][0] for k in store.keep_params if k in q}
    query = urlencode(sorted(kept.items()))
    return urlunsplit(("https", host, path, query, ""))


def store_info(url: str) -> tuple[str, str, str]:
    """(store_key, store_name, display_domain)."""
    s = store_for(url)
    host = registrable_host(url)
    if s.key == "other":
        name = host.split(".")[0].replace("-", " ").title() if host else "Store"
        return "other", name, host
    return s.key, s.name, host


def store_tier(url: str) -> int:
    return store_for(url).tier if store_for(url).key != "other" else 3


def shopping_site_filter() -> str:
    """`site:` OR-clause used to keep Google Shopping/organic text search on Indian stores."""
    tier1 = [s.domains[0] for s in STORES if s.tier == 1]
    return " OR ".join(f"site:{d}" for d in tier1)
