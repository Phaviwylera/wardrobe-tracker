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

    glob = '<a m="' + json.dumps({"purl": "https://www.myntraglobal.com/products/all-about-you-women-green-solid-shirt-style-top-16931780",
                                  "murl": "https://assets.myntassets.com/a.jpg", "t": "Top"}).replace('"', "&quot;") + '"></a>'
    g = parse_bing_images(glob, "myntra.com")
    assert g and g[0].url == "https://www.myntra.com/16931780"
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
        out = {}
        for i, b in enumerate(cands):  # judge by which picture it is, not by position
            out[i] = ("different", 0.9) if b == recolour_b else ("similar", 0.7)
        return out

    same, recolour, other = _uri(_garment().resize((300, 400))), _uri(_garment(colour=(20, 60, 200))), _uri(_garment(colour=(40, 160, 70), seed=7))
    recolour_b = base64.b64decode(recolour.split(",", 1)[1])

    async def search_all(self, queries, image_url=None, store_queries=None):
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
    assert amz["match_level"] == "exact" and amz["signals"]["identical_photo"] is True and amz["rank"] == 0
    assert amz["product"]["image_url"].endswith("/thumb.jpg")
    assert c.get(amz["product"]["image_url"]).headers["content-type"] == "image/jpeg"
    assert amz["product"]["current_price"] == 899 and amz["product"]["mrp"] == 2499 and amz["product"]["discount_pct"] == 64
    assert "https://www.flipkart.com/x/p/itm1?pid=KT1" not in by  # judged different: dropped
    assert by["https://www.myntra.com/kurtas/libas/a/24567890/buy"]["match_level"] == "similar"
    assert s["attributes"]["garment"] == "anarkali kurta" and verified["n"] == 2  # identical photo skips the AI check


def test_tatacliq_pdp():
    from app.services.direct import parse_tatacliq_pdp
    d = {"brandName": "Biba", "productName": "Biba Pink Anarkali", "allOOStock": False,
         "winningSellerPrice": {"doubleValue": 4121.0}, "mrpPrice": {"commaFormattedValue": "₹5,495.00"},
         "variantOptions": [{"sizelink": {"size": "M", "isAvailable": True}}, {"sizelink": {"size": "XL", "isAvailable": False}}],
         "galleryImagesList": [{"galleryImages": [{"key": "product", "value": "//img.tatacliq.com/a.jpg"}]}]}
    li = parse_tatacliq_pdp(d, "https://www.tatacliq.com/x/p-mp000000030341986")
    assert (li.price, li.extra["mrp"], li.in_stock, li.title) == (4121, 5495, True, "Biba Pink Anarkali")
    assert li.extra["sizes"] == [{"label": "M", "available": True}, {"label": "XL", "available": False}]
    assert li.image_url == "https://img.tatacliq.com/a.jpg"


def test_savana_from_bing():
    page = '<a m="' + json.dumps({"purl": "https://www.savana.com/details/mint-tie-neck-top-12345678",
                                  "murl": "https://img101.savana.com/a.jpg", "t": "Mint Tie Neck Top - Savana"}).replace('"', "&quot;") + '"></a>'
    g = parse_bing_images(page, "savana.com")
    assert g and g[0].title == "Mint Tie Neck Top" and g[0].url.startswith("https://www.savana.com/details/")


def test_lens_parsers():
    from app.services.lens import parse_serpapi, parse_vision
    doc = {"exact_matches": [{"position": 1, "title": "Atelier Rebel Tie-Up Neck Bell Sleeve Top", "source": "Myntra",
                              "link": "https://www.myntra.com/tops/atelier+rebel/x/47750458/buy", "price": "₹1,299",
                              "extracted_price": 1299.0, "thumbnail": "https://serpapi.com/t.jpg"}],
           "visual_matches": [{"title": "Similar", "link": "https://www.ajio.com/p/4601", "thumbnail": "https://x/y.jpg"}]}
    got = parse_serpapi(doc)
    assert got[0].channel == "lens:exact_matches" and got[0].price == 1299 and "47750458" in got[0].url
    assert got[1].channel == "lens:visual_matches"
    v = {"responses": [{"webDetection": {"pagesWithMatchingImages": [
        {"url": "https://www.myntra.com/47750458", "pageTitle": "Buy <b>Atelier Rebel</b> Top",
         "fullMatchingImages": [{"url": "https://assets.myntassets.com/a.jpg"}]},
        {"url": "https://www.savana.com/details/x-1", "pageTitle": "Top", "partialMatchingImages": [{"url": "https://img101.savana.com/b.jpg"}]}]}}]}
    pv = parse_vision(v)
    assert pv[0].channel == "lens:exact_matches" and pv[0].title == "Buy Atelier Rebel Top"
    assert pv[1].channel == "lens:products" and pv[1].image_url.endswith("b.jpg")


def test_extension_match_and_observe():
    from fastapi.testclient import TestClient

    from app.db import SessionLocal, init_db
    from app.main import app
    from app.models import QueryImage, Search

    init_db()
    with SessionLocal() as db:
        qi = QueryImage(data=_bytes(_garment()), width=480, height=640)
        db.add(qi); db.flush()
        s = Search(image_id=qi.id, client_id="client-ext", status="done")
        db.add(s); db.commit()
        sid = s.id
    c = TestClient(app)
    same = base64.b64encode(_bytes(_garment().resize((300, 400)))).decode()
    other = base64.b64encode(_bytes(_garment(colour=(40, 160, 70), seed=7))).decode()
    r = c.post("/api/ext/match", json={"search_id": sid, "tiles": [
        {"url": "https://www.myntra.com/tops/atelier+rebel/x/47750458/buy", "title": "Atelier Rebel Top",
         "price": "Rs. 1299", "mrp": "Rs. 2599", "image_b64": same},
        {"url": "https://www.myntra.com/tops/x/y/12345678/buy", "title": "Other", "image_b64": other},
        {"url": "https://www.myntra.com/women-tops", "title": "grid", "image_b64": same}]}).json()
    assert r["checked"] == 2 and [m["level"] for m in r["matches"]] == ["exact"]
    res = c.get(f"/api/searches/{sid}").json()["results"]
    assert "47750458" in res[0]["product"]["url"] and res[0]["match_level"] == "exact"
    assert res[0]["product"]["current_price"] == 1299
    state = '<script>window.__myx = {"pdpData": {"name": "Tie-Up Neck Top", "brand": {"name": "Atelier Rebel"}, "price": {"mrp": 2599, "discounted": 1199}, "sizes": [{"label": "S", "available": true}]}};</script>'
    o = c.post("/api/ext/observe", json={"url": "https://www.myntra.com/tops/atelier+rebel/x/47750458/buy", "state_script": state}).json()
    assert o["price"] == 1199 and o["in_stock"] is True and o["product_id"] == res[0]["product"]["id"]
    assert c.post("/api/ext/observe", json={"url": "https://example.com/x", "price": 1}).status_code == 400


def test_finder_image_urls_and_add_link(monkeypatch):
    from fastapi.testclient import TestClient

    from app import extension_api
    from app.db import SessionLocal, init_db
    from app.main import app
    from app.models import QueryImage, Search

    init_db()
    with SessionLocal() as db:
        qi = QueryImage(data=_bytes(_garment()), width=480, height=640)
        db.add(qi); db.flush()
        s = Search(image_id=qi.id, client_id="client-ext2", status="done")
        db.add(s); db.commit()
        sid = s.id
    pics = {"https://assets.myntassets.com/a/47750458/x.jpg": _bytes(_garment().resize((240, 320)))}

    async def fake_images(tiles):
        return {i: pics[t.image_url] for i, t in enumerate(tiles) if t.image_url in pics}
    monkeypatch.setattr(extension_api, "_tile_images", fake_images)
    c = TestClient(app)
    r = c.post("/api/ext/match", json={"search_id": sid, "tiles": [
        {"url": "https://www.myntra.com/tops/a/b/47750458/buy", "image_url": "https://assets.myntassets.com/a/47750458/x.jpg"},
        {"url": "https://www.myntra.com/tops/a/b/11111111/buy", "image_url": "https://evil.example/x.jpg"}]}).json()
    assert [m["level"] for m in r["matches"]] == ["exact"]

    async def no_scrape(url):
        return None
    monkeypatch.setattr("app.main._scrape", no_scrape)
    shared = "Check out this Atelier Rebel Top on Myntra! https://www.myntra.com/tops/atelier+rebel/atelier-rebel-tie-up-neck-bell-sleeve-top/47750458/buy"
    r = c.post("/api/ext/add-link", json={"search_id": sid, "url": shared})
    assert r.status_code == 200, r.text
    res = c.get(f"/api/searches/{sid}").json()["results"]
    assert "47750458" in res[0]["product"]["url"] and res[0]["sources"] == ["extension", "you"] and res[0]["match_level"] == "exact"
    assert c.post("/api/ext/add-link", json={"search_id": sid, "url": "https://www.myntra.com/women-tops"}).status_code == 400


def test_google_shopping_feed_and_resolve():
    import asyncio

    from app.config import Settings
    from app.services import lens, matcher
    from app.services.pipeline import Candidate, _collect, _resolve_shopping
    doc = {"shopping_results": [
        {"position": 1, "title": "ATELIER REBEL Tie-Up Neck Bell Sleeve Top'\"'by Myntra", "source": "Myntra", "price": "₹1,299",
         "extracted_price": 1299, "extracted_old_price": 2599, "product_id": "111", "immersive_product_page_token": "tokA",
         "thumbnail": "https://encrypted-tbn0.gstatic.com/a"},
        {"position": 2, "title": "Other Top by AJIO", "source": "AJIO.com", "price": "₹700", "extracted_price": 700,
         "product_id": "222", "immersive_product_page_token": "tokB", "thumbnail": "https://encrypted-tbn0.gstatic.com/b"},
        {"position": 3, "title": "x", "source": "ubuy.co.in", "price": "₹1", "extracted_price": 1, "product_id": "3",
         "immersive_product_page_token": "t"}]}
    ls = lens.parse_shopping(doc)
    assert [l.source for l in ls] == ["Myntra", "AJIO"] and ls[0].title == "ATELIER REBEL Tie-Up Neck Bell Sleeve Top"
    assert ls[0].url == "https://www.myntra.com/gshop/p111" and ls[0].extra["mrp"] == 2599
    cands = _collect(ls, 20)
    assert len(cands) == 2
    cands[0].score = matcher.MatchScore(0.95, "exact", {}); cands[1].score = matcher.MatchScore(0.4, "similar", {})
    imm = {"product_results": {"stores": [
        {"name": "Myntra", "link": "https://www.myntra.com/tops/atelier+rebel/x/47750458/buy?srsltid=abc", "extracted_price": 1199},
        {"name": "Nykaa Fashion", "link": "https://www.nykaafashion.com/x/p/18119651", "extracted_price": 1250},
        {"name": "Blog", "link": "https://example.com/post"}]}}
    assert [d["store"] for d in lens.parse_immersive(imm)] in (["myntra", "nykaafashion"], ["nykaafashion", "myntra"])

    async def fake(s, token):
        assert token == "tokA"
        return lens.parse_immersive(imm)
    orig, lens.resolve = lens.resolve, fake
    try:
        asyncio.run(_resolve_shopping(Settings(), cands))
    finally:
        lens.resolve = orig
    got = {c.canon: c for c in cands}
    assert set(got) == {"https://www.myntra.com/47750458", "https://www.nykaafashion.com/x/p/18119651"}
    assert got["https://www.myntra.com/47750458"].listing.price == 1199 and got["https://www.myntra.com/47750458"].score.level == "exact"
