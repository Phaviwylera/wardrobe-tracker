"""Keyless path: store parsers + an end-to-end search with Claude and stores faked."""
import base64
import json

from app.services.direct import (parse_amazon, parse_bing_images, parse_flipkart, parse_shopify,
                                 parse_tatacliq)
from tests.test_units import _bytes, _garment

AMAZON = """<div role="listitem" data-asin="B0DKXZGWWY" data-component-type="s-search-result">
<a class="a-link-normal" href="/GoSriKi-Anarkali/dp/B0DKXZGWWY/ref=sr_1_1"><img class="s-image"
 src="https://m.media-amazon.com/images/I/71uq.jpg" alt="GoSriKi Women's Rayon Anarkali Kurta"/></a>
<h2 aria-label="Sponsored Ad - Women's Rayon Viscose Embroidered Anarkali Kurta"><span>Women's Rayon</span></h2>
<span class="a-price"><span class="a-offscreen">₹699</span></span>
<span class="a-price a-text-price" data-a-strike="true"><span class="a-offscreen">₹2,999</span></span>
</div>
<div data-asin="" data-component-type="s-search-result"></div>"""

FLIPKART = """<div data-id="KTAH75YMWFZ2XPXS"><a class="CIaYa1" href="/aaradhiya-women-anarkali/p/itmedb46b17074e8?pid=KTAH75YMWFZ2XPXS&amp;lid=X&amp;srno=s_1_1">
<img class="MZeksS" src="https://rukminim2.flixcart.com/image/312/312/xif0q/kurta/a.jpeg?q=70"/></a>
<div><div class="Fo1I0b">Aaradhiya Traders</div><a class="atJtCj" title="Embroidered Viscose Anarkali Kurta"
 href="/aaradhiya-women-anarkali/p/itmedb46b17074e8?pid=KTAH75YMWFZ2XPXS">x</a>
<div><div>₹475</div><div>₹2,199</div><span>78% off</span></div></div></div>"""

BING = ('<a class="iusc" m="' + json.dumps({
    "purl": "https://www.myntra.com/kurtas/libas/libas-women-pink-anarkali/24567890/buy",
    "murl": "https://assets.myntassets.com/x.jpg", "t": "Libas Women Pink Anarkali | Myntra"}).replace('"', "&quot;") +
    '"></a><a class="iusc" m="' + json.dumps({"purl": "https://www.myntra.com/women-kurtas", "murl": "x"}).replace('"', "&quot;") + '"></a>')


def test_yandex_parser():
    from app.services.direct import parse_yandex
    state = {"x": {"sites": [
        {"url": "https://www.biba.in/pink-anarkali/SKD8812.html?utm_source=yandexsmartcamera", "title": "Pink Anarkali",
         "domain": "biba.in", "originalImage": {"url": "https://www.biba.in/a.jpg"}},
        {"url": "https://in.pinterest.com/pin/123/", "title": "pin", "domain": "pinterest.com"}]}}
    page = '<div data-state="' + json.dumps(state).replace('"', "&quot;") + '"></div>'
    got = parse_yandex(page)
    assert [g.url for g in got] == ["https://www.biba.in/pink-anarkali/SKD8812.html"]
    assert got[0].image_url == "https://www.biba.in/a.jpg" and got[0].channel == "web"


def test_parsers():
    a = parse_amazon(AMAZON)
    assert len(a) == 1 and a[0].url == "https://www.amazon.in/dp/B0DKXZGWWY"
    assert a[0].price == 699 and a[0].extra["mrp"] == 2999 and "Anarkali" in a[0].title
    assert "Sponsored" not in a[0].title

    f = parse_flipkart(FLIPKART)
    assert f[0].url == "https://www.flipkart.com/aaradhiya-women-anarkali/p/itmedb46b17074e8?pid=KTAH75YMWFZ2XPXS"
    assert (f[0].price, f[0].extra["mrp"]) == (475, 2199)
    assert "/image/612/612/" in f[0].image_url and f[0].title.startswith("Aaradhiya Traders")

    t = parse_tatacliq({"searchresult": [{"brandname": "Biba", "productname": "Biba Pink Anarkali",
                                          "webURL": "/biba-pink/p-mp000000030341986", "imageURL": "//img.tatacliq.com/a.jpeg",
                                          "price": {"sellingPrice": {"doubleValue": 1999.0}, "mrpPrice": {"doubleValue": 3999.0}},
                                          "inStockFlag": True}]})
    assert t[0].url == "https://www.tatacliq.com/biba-pink/p-mp000000030341986"
    assert (t[0].price, t[0].extra["mrp"], t[0].image_url, t[0].title) == (1999, 3999, "https://img.tatacliq.com/a.jpeg", "Biba Pink Anarkali")

    sh = parse_shopify({"resources": {"results": {"products": [
        {"title": "Rose Anarkali", "url": "/products/rose-anarkali?_pos=1", "image": "//cdn.shopify.com/a.jpg",
         "price": "1890.00", "compare_at_price_max": "2799.00", "available": True, "vendor": "Libas"}]}}}, "www.libas.in")
    assert sh[0].url == "https://www.libas.in/products/rose-anarkali" and sh[0].price == 1890 and sh[0].extra["mrp"] == 2799

    b = parse_bing_images(BING, "myntra.com")
    assert len(b) == 1 and b[0].title == "Libas Women Pink Anarkali" and b[0].price is None


def _uri(img) -> str:
    return "data:image/jpeg;base64," + base64.b64encode(_bytes(img)).decode()


def test_keyless_search_flow(monkeypatch):
    from fastapi.testclient import TestClient

    from app.db import init_db
    from app.main import app, settings
    from app.services import captioner, direct as direct_mod, pipeline
    from app.services.serp_parse import Listing

    init_db()
    monkeypatch.setattr(settings, "brightdata_api_key", "")
    monkeypatch.setattr(settings, "llm_provider", "bedrock")
    monkeypatch.setattr(settings, "bedrock_model", "test-model")

    async def describe(image, s=None):
        return {"garment": "anarkali kurta", "queries": ["pink printed anarkali kurta", "pink anarkali"]}

    verified = {}

    async def verify(image, cands, s=None):
        verified["n"] = len(cands)
        # listing 0 = same photo (Claude agrees), listing 1 = recolour (Claude says different)
        return {0: ("same", 0.95), 1: ("different", 0.9), 2: ("similar", 0.7)}

    same, recolour, other = _uri(_garment().resize((300, 400))), _uri(_garment(colour=(20, 60, 200))), _uri(_garment(colour=(40, 160, 70), seed=7))

    async def search_all(self, queries, image_url=None):
        return [
            Listing(url="https://www.amazon.in/dp/B0AAAAAAAA", title="Pink Anarkali", image_url=same, price=899,
                    currency="INR", channel="store", extra={"mrp": 2499}),
            Listing(url="https://www.flipkart.com/x/p/itm1?pid=KT1", title="Blue Anarkali", image_url=recolour,
                    price=799, currency="INR", channel="store"),
            Listing(url="https://www.myntra.com/kurtas/libas/a/24567890/buy", title="Libas Pink", image_url=other,
                    channel="web"),
        ]

    async def fetch_page(self, url):
        return None

    async def fetch_image(self, url):
        return base64.b64decode(url.split(",", 1)[1]) if url.startswith("data:") else None

    monkeypatch.setattr(captioner, "describe", describe)
    monkeypatch.setattr(captioner, "verify", verify)
    monkeypatch.setattr(direct_mod.Direct, "search_all", search_all)
    monkeypatch.setattr(direct_mod.Direct, "fetch_page", fetch_page)
    monkeypatch.setattr(direct_mod.Direct, "fetch_image", fetch_image)

    real_run = pipeline.run_search

    async def run(search_id):
        await real_run(search_id)

    monkeypatch.setattr("app.main.run_search", run)
    c = TestClient(app)
    h = {"X-Client-Id": "client-keyless1"}
    sid = c.post("/api/searches", files={"file": ("q.jpg", _bytes(_garment()), "image/jpeg")}, headers=h).json()["id"]
    s = c.get(f"/api/searches/{sid}").json()
    assert s["status"] == "done", s
    by = {r["product"]["url"]: r for r in s["results"]}
    amz = by["https://www.amazon.in/dp/B0AAAAAAAA"]
    assert amz["match_level"] == "exact" and amz["signals"]["ai"]["verdict"] == "same"
    assert amz["product"]["image_url"].endswith("/thumb.jpg")
    assert c.get(amz["product"]["image_url"]).headers["content-type"] == "image/jpeg"
    assert amz["product"]["current_price"] == 899 and amz["product"]["mrp"] == 2499 and amz["product"]["discount_pct"] == 64
    assert "https://www.flipkart.com/x/p/itm1?pid=KT1" not in by  # judged different: dropped
    assert by["https://www.myntra.com/kurtas/libas/a/24567890/buy"]["match_level"] == "similar"
    assert s["attributes"]["garment"] == "anarkali kurta" and verified["n"] == 3
