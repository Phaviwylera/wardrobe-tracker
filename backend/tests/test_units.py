import io
import json

import numpy as np
from PIL import Image, ImageDraw

from app.services import matcher
from app.services.extract import extract_js_object, extract_product
from app.services.serp_parse import extract_listings, parse_price, unwrap_redirect
from app.services.stores import canonical_url, is_product_url, store_info


# ------------------------------------------------------------------ prices / SERP
def test_parse_price_indian_grouping():
    assert parse_price("₹1,23,456.50") == (123456.5, "INR")
    assert parse_price("Rs. 899") == (899.0, "INR")
    assert parse_price({"extracted_value": 1299, "currency": "INR"}) == (1299.0, "INR")
    assert parse_price("free")[0] is None


def test_extract_listings_nested_and_redirects():
    doc = {"exact_matches": [
        {"rank": 1, "title": "Women Floral Kurta", "link": "https://www.myntra.com/kurtas/libas/x/24567890/buy",
         "source": "Myntra", "price": "₹1,199", "thumbnail": "https://img.example/a.jpg"},
        {"title": "Pin", "link": "https://in.pinterest.com/pin/1"},
    ], "products": {"items": [{"name": "Kurta", "url": "https://www.google.com/url?q=https%3A%2F%2Fwww.ajio.com%2Fp%2F460123",
                                "price": {"value": "₹999"}}]}}
    ls = extract_listings(doc, "lens:exact_matches")
    urls = [l.url for l in ls]
    assert "https://www.ajio.com/p/460123" in urls
    first = ls[0]
    assert first.price == 1199 and first.image_url.endswith("a.jpg") and first.source == "Myntra"
    assert unwrap_redirect("https://www.google.com/aclk?sa=l&adurl=https://x.com/p/1") == "https://x.com/p/1"


# ------------------------------------------------------------------ stores
def test_product_url_detection_and_canonical():
    assert is_product_url("https://www.myntra.com/kurtas/libas/libas-women-kurta/24567890/buy")
    assert not is_product_url("https://www.myntra.com/women-kurtas")
    assert is_product_url("https://www.amazon.in/Libas-Women-Kurta/dp/B0C1234567/ref=sr_1_3?keywords=x")
    assert canonical_url("https://www.amazon.in/Libas-Women/dp/B0C1234567/ref=sr_1?th=1") == \
        "https://www.amazon.in/dp/B0C1234567"
    assert canonical_url("https://www.flipkart.com/x/p/itm123?pid=KTAG1&lid=L&marketplace=FLIPKART") == \
        "https://www.flipkart.com/x/p/itm123?pid=KTAG1"
    assert canonical_url("https://m.myntra.com/a/b/123456/buy/") == "https://www.myntra.com/123456"
    assert not is_product_url("https://in.pinterest.com/pin/123/")
    assert is_product_url("https://somebrand.in/products/red-saree")
    assert store_info("https://www.libas.in/products/x")[1] == "Libas"
    assert store_info("https://www.somebrand.in/products/x")[0] == "other"


# ------------------------------------------------------------------ page extraction
JSONLD_PAGE = """<html><head><meta property="og:image" content="https://cdn/x.jpg">
<script type="application/ld+json">{"@context":"https://schema.org","@graph":[{"@type":"BreadcrumbList"},
{"@type":"Product","name":"Mirror Work Anarkali","brand":{"@type":"Brand","name":"Libas"},
"image":["https://cdn/1.jpg"],"aggregateRating":{"ratingValue":"4.3","reviewCount":"120"},
"offers":[{"@type":"Offer","price":"1899.00","priceCurrency":"INR","availability":"https://schema.org/InStock",
"priceSpecification":{"@type":"UnitPriceSpecification","priceType":"https://schema.org/ListPrice","price":3799}}]}]}
</script></head><body></body></html>"""


def test_extract_jsonld():
    d = extract_product(JSONLD_PAGE, "https://www.libas.in/products/mirror-anarkali")
    assert d.title == "Mirror Work Anarkali" and d.brand == "Libas"
    assert d.price == 1899 and d.mrp == 3799 and d.in_stock is True
    assert d.rating == 4.3 and d.rating_count == 120 and d.image_url == "https://cdn/1.jpg"


def test_extract_myntra_state():
    state = {"pdpData": {"name": "Women Printed Kurta", "brand": {"name": "Anouk"},
             "price": {"mrp": 2499, "discounted": 999},
             "sizes": [{"label": "S", "available": True}, {"label": "M", "available": False}],
             "media": {"albums": [{"images": [{"imageURL": "https://assets.myntassets.com/1.jpg"}]}]},
             "ratings": {"averageRating": 4.1, "totalCount": 52}}}
    html = f"<html><script>window.__myx = {json.dumps(state)};</script></html>"
    d = extract_product(html, "https://www.myntra.com/kurtas/anouk/x/12345678/buy")
    assert (d.price, d.mrp, d.brand, d.in_stock) == (999, 2499, "Anouk", True)
    assert d.sizes[1] == {"label": "M", "available": False}
    assert "myntra" in d.sources


def test_extract_amazon_dom():
    html = """<span id="productTitle"> Women Rayon Kurta </span><a id="bylineInfo">Visit the GoSriKi Store</a>
    <div id="corePriceDisplay_desktop_feature_div"><span class="a-price"><span class="a-offscreen">₹549.00</span></span>
    <span class="a-price a-text-price"><span class="a-offscreen">₹2,099.00</span></span></div>
    <div id="availability"> In stock </div><img id="landingImage" data-old-hires="https://m.media-amazon.com/x.jpg">"""
    d = extract_product(html, "https://www.amazon.in/dp/B0C1234567")
    assert (d.title, d.brand, d.price, d.mrp, d.in_stock) == ("Women Rayon Kurta", "GoSriKi", 549, 2099, True)


def test_js_object_with_braces_in_strings():
    html = 'x; window.__PRELOADED_STATE__ = {"a": "}{ tricky \\" }", "b": {"c": 1}}; more'
    assert extract_js_object(html, "window.__PRELOADED_STATE__") == {"a": '}{ tricky " }', "b": {"c": 1}}


# ------------------------------------------------------------------ matcher
def _garment(colour=(200, 40, 60), seed=0, size=(480, 640)) -> Image.Image:
    rng = np.random.default_rng(seed)
    img = Image.new("RGB", size, (245, 245, 245))
    d = ImageDraw.Draw(img)
    d.polygon([(120, 80), (360, 80), (420, 600), (60, 600)], fill=colour)  # a kurta silhouette
    for _ in range(140):  # a print
        x, y = rng.integers(90, 390), rng.integers(100, 580)
        r = int(rng.integers(4, 14))
        d.ellipse((x - r, y - r, x + r, y + r), fill=tuple(int(v) for v in rng.integers(0, 255, 3)))
    return img


def _bytes(img: Image.Image) -> bytes:
    b = io.BytesIO()
    img.save(b, "JPEG", quality=88)
    return b.getvalue()


def test_matcher_levels():
    q = matcher.features(_bytes(_garment()))
    same_resized = matcher.features(_bytes(_garment().resize((360, 480))))
    cropped = matcher.features(_bytes(_garment().crop((30, 40, 460, 630))))
    other_colour = matcher.features(_bytes(_garment(colour=(30, 60, 200))))
    different = matcher.features(_bytes(_garment(colour=(40, 160, 70), seed=7)))

    s_same = matcher.compare(q, same_resized, "lens:visual_matches")
    s_crop = matcher.compare(q, cropped, "lens:visual_matches")
    s_col = matcher.compare(q, other_colour, "lens:visual_matches")
    s_diff = matcher.compare(q, different, "lens:visual_matches")
    assert s_same.level == "exact", s_same
    assert s_crop.level == "exact", s_crop
    assert s_col.level != "exact", s_col
    assert s_diff.score < s_same.score and s_diff.level != "exact", s_diff
    unverified = matcher.compare(q, None, "lens:exact_matches")
    assert unverified.signals["verified"] is False and unverified.level != "exact"


def test_judge_combination():
    from app.services.captioner import combine
    assert combine([("same", 0.9), ("same", 0.8)], 2) == ("same", 0.8)
    assert combine([("same", 0.9)], 2) == ("close", 0.9)          # second judge didn't answer
    assert combine([("same", 0.9)], 1) == ("same", 0.9)
    assert combine([("same", 0.9), ("similar", 0.8)], 2)[0] == "close"
    assert combine([("same", 0.9), ("different", 0.8)], 2)[0] == "similar"
    assert combine([("different", 0.9), ("different", 0.7)], 2)[0] == "different"
    assert combine([], 2) is None


def test_collect_gives_every_store_a_share():
    from app.services.pipeline import _collect
    from app.services.serp_parse import Listing
    ls = [Listing(url=f"https://www.amazon.in/dp/B0{i:08d}", title="a", price=100, channel="store", position=i + 1)
          for i in range(60)]
    ls += [Listing(url=f"https://www.myntra.com/tops/x/top-{i}/{1690000 + i}/buy", title="m", channel="web", position=i + 1)
           for i in range(10)]
    got = _collect(ls, 20)
    assert sum("myntra" in c.canon for c in got) == 10
    assert got[1].canon.startswith("https://www.myntra.com") or got[0].canon.startswith("https://www.myntra.com")


def test_myntra_urls_collapse():
    from app.services.stores import canonical_url, is_product_url, store_info
    a = canonical_url("https://www.myntraglobal.com/products/all-about-you-women-green-solid-shirt-style-top-16931780")
    b = canonical_url("https://www.myntra.com/tops/all+about+you/all-about-you-women-green-solid-shirt-style-top/16931780/buy")
    assert a == b == "https://www.myntra.com/16931780"
    assert is_product_url("https://www.myntra.com/16931780") and store_info(a)[1] == "Myntra"


def test_identical_photo_survives_screenshot_borders():
    from PIL import ImageOps
    from app.services.pipeline import Candidate, _identical
    from app.services.serp_parse import Listing
    base = _garment()
    shot = ImageOps.expand(base.resize((420, 560)), border=(30, 20, 30, 70), fill=(250, 250, 250))  # screenshot chrome
    q = matcher.features(_bytes(shot))
    same = matcher.compare(q, matcher.features(_bytes(base)), "web")
    recol = matcher.compare(q, matcher.features(_bytes(_garment(colour=(30, 60, 200)))), "web")
    c1 = Candidate(canon="a", listing=Listing(url="a")); c1.score = same
    c2 = Candidate(canon="b", listing=Listing(url="b")); c2.score = recol
    assert _identical(c1), same.signals
    assert not _identical(c2), recol.signals


def test_spotted_pages():
    from app.services.pipeline import _spotted
    from app.services.serp_parse import Listing
    ls = [Listing(url="https://www.myntra.com/bell-sleeved-tops", title="Buy Bell Sleeved Tops for Women Online - Myntra", channel="lens:exact_matches"),
          Listing(url="https://www.myntra.com/bell-sleeved-tops?p=2", title="dup", channel="lens:exact_matches"),
          Listing(url="https://www.myntra.com/tops/x/y/47750458/buy", title="product", channel="lens:exact_matches"),
          Listing(url="https://in.pinterest.com/pin/1", title="pin", channel="lens:exact_matches"),
          Listing(url="https://www.myntra.com/women-tops", title="Women tops", channel="lens:visual_matches")]
    got = _spotted(ls)
    assert got == [{"url": "https://www.myntra.com/bell-sleeved-tops", "title": "Bell Sleeved Tops for Women", "store": "Myntra"}]
