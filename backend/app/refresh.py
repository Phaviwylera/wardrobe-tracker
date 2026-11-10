"""Scheduled price refresh. Run by the Render cron job:  python -m app.refresh

Order: watch-listed products first, then everything seen in recent searches, oldest check first.
"""
from __future__ import annotations

import asyncio
import logging
import os
import smtplib
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage

from sqlalchemy import false, or_, select

from .config import get_settings
from .db import SessionLocal, init_db
from .models import Product, WatchItem
from .services.brightdata import BrightData
from .services.direct import Direct
from .services.pipeline import scrape_product
from .services.pricing import apply_page_data, record_price

log = logging.getLogger("refresh")


def _due_products(limit: int) -> list[tuple[str, str]]:
    cutoff_recent = datetime.now(timezone.utc) - timedelta(days=45)
    stale = datetime.now(timezone.utc) - timedelta(hours=5)
    with SessionLocal() as db:
        watched = set(db.scalars(select(WatchItem.product_id)).all())
        rows = db.execute(
            select(Product.id, Product.url, Product.last_checked)
            .where(Product.tracked.is_(True))
            .where(or_(Product.id.in_(watched) if watched else false(), Product.first_seen >= cutoff_recent))
            .where(or_(Product.last_checked.is_(None), Product.last_checked < stale))
        ).all()
    rows.sort(key=lambda r: (r.id not in watched, r.last_checked or datetime.min.replace(tzinfo=timezone.utc)))
    return [(r.id, r.url) for r in rows[:limit]]


async def refresh(limit: int | None = None, product_ids: list[str] | None = None) -> dict:
    s = get_settings()
    limit = limit or int(os.getenv("REFRESH_LIMIT", "300"))
    if product_ids:
        with SessionLocal() as db:
            due = [(p.id, p.url) for p in db.scalars(select(Product).where(Product.id.in_(product_ids)))]
    else:
        due = _due_products(limit)
    bd = BrightData(s) if s.brightdata_unlocker_zone and s.brightdata_api_key else None
    direct = Direct(per_host=1)  # slow and polite: one request at a time per store
    ok = failed = 0
    try:
        for i in range(0, len(due), 20):
            batch = due[i:i + 20]
            pages = await asyncio.gather(*[scrape_product(url, direct, bd) for _, url in batch])
            await asyncio.sleep(2)
            with SessionLocal() as db:
                for (pid, _), page in zip(batch, pages):
                    p = db.get(Product, pid)
                    p.last_checked = datetime.now(timezone.utc)
                    if page and page.price:
                        apply_page_data(p, page)
                        record_price(db, p, page.price, mrp=page.mrp, in_stock=page.in_stock, source="page")
                        ok += 1
                    else:
                        failed += 1
                db.commit()
    finally:
        await direct.aclose()
        if bd:
            await bd.aclose()
    alerts = send_alerts()
    return {"checked": len(due), "ok": ok, "failed": failed, "alerts": alerts}


def send_alerts() -> int:
    s = get_settings()
    sent = 0
    with SessionLocal() as db:
        items = db.scalars(select(WatchItem).where(WatchItem.target_price.is_not(None))).all()
        for w in items:
            p = w.product
            if p.current_price is None or p.current_price > w.target_price:
                continue
            if w.last_alerted_price is not None and abs(w.last_alerted_price - p.current_price) < 0.01:
                continue
            if w.email and s.smtp_host:
                try:
                    _email(w.email, p)
                    sent += 1
                except Exception as e:
                    log.warning("alert mail failed: %s", e)
                    continue
            w.last_alerted_price = p.current_price
        db.commit()
    return sent


def _email(to: str, p: Product) -> None:
    s = get_settings()
    msg = EmailMessage()
    msg["Subject"] = f"Price drop: {p.title or 'your item'} is now ₹{p.current_price:,.0f}"
    msg["From"] = s.alert_from or s.smtp_user
    msg["To"] = to
    msg.set_content(
        f"{p.title}\n{p.store_name}: ₹{p.current_price:,.0f}"
        + (f" (MRP ₹{p.mrp:,.0f})" if p.mrp else "")
        + f"\n\n{p.url}\n\nTrack it: {s.frontend_origin}/product/{p.id}\n"
    )
    with smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=30) as smtp:
        smtp.starttls()
        if s.smtp_user:
            smtp.login(s.smtp_user, s.smtp_password)
        smtp.send_message(msg)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    init_db()
    print(asyncio.run(refresh()))
