"""End-to-end: upload -> Lens (faked) -> verify -> price -> API, against SQLite."""
import asyncio
import base64
import os
import tempfile

os.environ["DATABASE_URL"] = f"sqlite:///{tempfile.mkdtemp()}/t.db"
os.environ["BRIGHTDATA_API_KEY"] = "test"
os.environ["BRIGHTDATA_SERP_ZONE"] = "serp"
os.environ["BRIGHTDATA_UNLOCKER_ZONE"] = "unlocker"
os.environ["PUBLIC_BASE_URL"] = "https://api.example.com"

from fastapi.testclient import TestClient  # noqa: E402

from app.db import init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.services import pipeline  # noqa: E402
from app.services.brightdata import BrightData, BrightDataError  # noqa: E402
from tests.test_units import _bytes, _garment  # noqa: E402

SAME = "data:image/jpeg;base64," + base64.b64encode(_bytes(_garment().resize((300, 400)))).decode()
RECOLOUR = "data:image/jpeg;base64," + base64.b64encode(_bytes(_garment(colour=(20, 60, 200)))).decode()
OTHER = "data:image/jpeg;base64," + base64.b64encode(_bytes(_garment(colour=(40, 160, 70), seed=7))).decode()

PAGE = """<script type="application/ld+json">{"@type":"Product","name":"%s","brand":"Libas",
"image":"%s","offers":{"price":"%s","priceCurrency":"INR","availability":"InStock"}}</script>"""


class FakeBD(BrightData):
    def __init__(self):
        super().__init__()
        self.calls = []

    async def lens_by_url(self, image_url, tab):
        self.calls.append(("lens", tab, image_url))
        if tab == "exact_matches":
            return {"exact_matches": [
                {"title": "Libas Red Printed Kurta", "link": "https://www.myntra.com/kurtas/libas/red/11112222/buy",
                 "thumbnail": SAME, "source": "Myntra", "price": "₹1,299"},
                {"title": "Red kurta pin", "link": "https://in.pinterest.com/pin/9/", "thumbnail": SAME},
            ]}
        if tab == "products":
            return {"products": [
                {"title": "Libas Kurta", "link": "https://www.ajio.com/p/460999001_red", "thumbnail": SAME,
                 "price": "₹1,099"},
                {"title": "Blue version", "link": "https://www.ajio.com/p/460999002_blue", "thumbnail": RECOLOUR},
                {"title": "Category", "link": "https://www.myntra.com/women-kurtas", "thumbnail": SAME},
            ]}
        return {"visual_matches": [
            {"title": "Green kurta", "link": "https://www.libas.in/products/green", "thumbnail": OTHER},
        ]}

    async def fetch_html(self, url):
        self.calls.append(("page", url))
        if "11112222" in url:
            return PAGE % ("Libas Red Printed Kurta", "https://cdn/x.jpg", "1249")
        if "460999001" in url:
            raise BrightDataError("blocked")
        return PAGE % ("Other", "https://cdn/y.jpg", "999")


def test_full_flow(monkeypatch):
    init_db()
    fake = FakeBD()
    real_run = pipeline.run_search

    async def run(search_id):
        await real_run(search_id, bd=fake)

    monkeypatch.setattr("app.main.run_search", run)
    c = TestClient(app)
    h = {"X-Client-Id": "client-1234abcd"}

    r = c.post("/api/searches", files={"file": ("q.jpg", _bytes(_garment()), "image/jpeg")}, headers=h)
    assert r.status_code == 202, r.text
    sid = r.json()["id"]
    s = c.get(f"/api/searches/{sid}").json()
    assert s["status"] == "done", s
    res = s["results"]
    by_url = {x["product"]["url"]: x for x in res}

    # Pinterest + category page dropped; Lens fetched our public image URL
    assert not any("pinterest" in u or "women-kurtas" in u for u in by_url)
    assert any(cl[0] == "lens" and cl[2].startswith("https://api.example.com/api/images/") for cl in fake.calls)

    myntra = by_url["https://www.myntra.com/kurtas/libas/red/11112222/buy"]
    assert myntra["match_level"] == "exact" and myntra["rank"] == 0
    assert myntra["product"]["current_price"] == 1249 and myntra["product"]["live"] is True

    ajio = by_url["https://www.ajio.com/p/460999001_red"]
    assert ajio["match_level"] == "exact"
    assert ajio["product"]["current_price"] == 1099 and ajio["product"]["live"] is False  # SERP price fallback

    assert by_url["https://www.ajio.com/p/460999002_blue"]["match_level"] != "exact"
    assert "https://www.libas.in/products/green" not in by_url or \
        by_url["https://www.libas.in/products/green"]["match_level"] == "similar"

    # Product detail + other stores + watchlist
    pid = myntra["product"]["id"]
    d = c.get(f"/api/products/{pid}", headers=h).json()
    assert d["stats"]["count"] == 1 and d["history"][0]["price"] == 1249
    assert any(o["store_key"] == "ajio" for o in d["other_stores"])

    assert c.put(f"/api/watchlist/{pid}", json={"target_price": 1300}, headers=h).json()["ok"]
    wl = c.get("/api/watchlist", headers=h).json()
    assert wl[0]["hit"] is True

    assert len(c.get("/api/searches", headers=h).json()) == 1
    assert c.get(f"/api/images/{s['image_id']}.jpg").headers["content-type"] == "image/jpeg"
    assert c.post("/api/searches", files={"file": ("q.jpg", b"notanimage", "image/jpeg")}, headers=h).status_code == 415
    assert c.post("/api/searches", files={"file": ("q.jpg", b"x", "image/jpeg")}).status_code == 400


def test_price_history_dedupe_and_stats():
    from datetime import datetime, timedelta, timezone

    from app.db import SessionLocal
    from app.models import PricePoint
    from app.services.pricing import get_or_create_product, price_stats, record_price

    init_db()
    with SessionLocal() as db:
        p = get_or_create_product(db, "https://www.myntra.com/a/b/c/99998888/buy")
        assert record_price(db, p, 1500) is not None
        assert record_price(db, p, 1500) is None  # same price within window
        db.flush()
        base = datetime.now(timezone.utc)
        for i, price in enumerate([1600, 1400, 1550, 1200]):
            db.add(PricePoint(product_id=p.id, price=price, source="page",
                              captured_at=base + timedelta(days=i + 1)))
        db.flush()
        db.refresh(p)
        st = price_stats(p.prices)
        assert st["lowest"] == 1200 and st["current"] == 1200 and st["verdict"] == "lowest"
        assert st["change"] == -350


def test_refresh_job_records_new_price_and_alert(monkeypatch):
    from app import refresh as job
    from app.db import SessionLocal
    from app.models import Product, WatchItem
    from app.services.pricing import get_or_create_product

    init_db()
    with SessionLocal() as db:
        p = get_or_create_product(db, "https://www.ajio.com/p/470000111_pink")
        p.current_price = 2000
        db.add(WatchItem(client_id="client-refresh1", product_id=p.id, target_price=1500))
        db.commit()
        pid = p.id

    async def fake_scrape(bd, url):
        from app.services.extract import ProductData
        return ProductData(title="Pink kurta", price=1450, mrp=2999, in_stock=True)

    monkeypatch.setattr(job, "scrape_product", fake_scrape)
    out = asyncio.run(job.refresh(product_ids=[pid]))
    assert out["ok"] == 1
    with SessionLocal() as db:
        p = db.get(Product, pid)
        assert p.current_price == 1450 and p.prices[-1].price == 1450
        w = db.query(WatchItem).filter_by(product_id=pid).one()
        assert w.last_alerted_price == 1450  # alert marked (no SMTP configured, so no mail)
