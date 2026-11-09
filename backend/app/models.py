import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def _uuid() -> str:
    return uuid.uuid4().hex


def _now() -> datetime:
    return datetime.now(timezone.utc)


class QueryImage(Base):
    """A photo a shopper uploaded (stored normalised as JPEG, max 1280px)."""

    __tablename__ = "query_images"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    data: Mapped[bytes] = mapped_column(LargeBinary)
    width: Mapped[int] = mapped_column(Integer)
    height: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Search(Base):
    __tablename__ = "searches"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    image_id: Mapped[str] = mapped_column(ForeignKey("query_images.id"))
    client_id: Mapped[str | None] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(20), default="queued")
    stage: Mapped[str] = mapped_column(String(120), default="Queued")
    progress: Mapped[float] = mapped_column(Float, default=0.0)
    error: Mapped[str | None] = mapped_column(Text)
    attributes: Mapped[dict | None] = mapped_column(JSON)  # garment description from the captioner
    stats: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    results: Mapped[list["SearchResult"]] = relationship(
        back_populates="search", cascade="all, delete-orphan", order_by="SearchResult.rank"
    )


class Product(Base):
    """One product page on one store. Price history hangs off this."""

    __tablename__ = "products"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    canonical_url: Mapped[str] = mapped_column(String(1024), unique=True, index=True)
    url: Mapped[str] = mapped_column(String(2048))
    store_key: Mapped[str] = mapped_column(String(40), index=True)
    store_name: Mapped[str] = mapped_column(String(80))
    domain: Mapped[str] = mapped_column(String(255))
    title: Mapped[str | None] = mapped_column(String(512))
    brand: Mapped[str | None] = mapped_column(String(200))
    image_url: Mapped[str | None] = mapped_column(String(2048))
    currency: Mapped[str] = mapped_column(String(8), default="INR")
    current_price: Mapped[float | None] = mapped_column(Float)
    mrp: Mapped[float | None] = mapped_column(Float)
    in_stock: Mapped[bool | None] = mapped_column(Boolean)
    sizes: Mapped[list | None] = mapped_column(JSON)
    rating: Mapped[float | None] = mapped_column(Float)
    rating_count: Mapped[int | None] = mapped_column(Integer)
    scrape_ok: Mapped[bool] = mapped_column(Boolean, default=False)
    first_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    last_checked: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    tracked: Mapped[bool] = mapped_column(Boolean, default=True)

    prices: Mapped[list["PricePoint"]] = relationship(
        back_populates="product", cascade="all, delete-orphan", order_by="PricePoint.captured_at"
    )


class PricePoint(Base):
    __tablename__ = "price_points"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    product_id: Mapped[str] = mapped_column(ForeignKey("products.id"), index=True)
    price: Mapped[float] = mapped_column(Float)
    mrp: Mapped[float | None] = mapped_column(Float)
    in_stock: Mapped[bool | None] = mapped_column(Boolean)
    source: Mapped[str] = mapped_column(String(20), default="page")  # page | lens | shopping
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)

    product: Mapped[Product] = relationship(back_populates="prices")


class SearchResult(Base):
    __tablename__ = "search_results"
    __table_args__ = (UniqueConstraint("search_id", "product_id"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    search_id: Mapped[str] = mapped_column(ForeignKey("searches.id"), index=True)
    product_id: Mapped[str] = mapped_column(ForeignKey("products.id"))
    rank: Mapped[int] = mapped_column(Integer, default=0)
    score: Mapped[float] = mapped_column(Float)
    match_level: Mapped[str] = mapped_column(String(12))  # exact | close | similar
    signals: Mapped[dict | None] = mapped_column(JSON)
    sources: Mapped[list | None] = mapped_column(JSON)  # which discovery channels found it

    search: Mapped[Search] = relationship(back_populates="results")
    product: Mapped[Product] = relationship()


class ProductThumb(Base):
    """Small JPEG of the listing photo, served by us because stores block hotlinking."""

    __tablename__ = "product_thumbs"
    product_id: Mapped[str] = mapped_column(ForeignKey("products.id"), primary_key=True)
    data: Mapped[bytes] = mapped_column(LargeBinary)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class WatchItem(Base):
    __tablename__ = "watch_items"
    __table_args__ = (UniqueConstraint("client_id", "product_id"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    client_id: Mapped[str] = mapped_column(String(64), index=True)
    product_id: Mapped[str] = mapped_column(ForeignKey("products.id"))
    target_price: Mapped[float | None] = mapped_column(Float)
    email: Mapped[str | None] = mapped_column(String(320))
    last_alerted_price: Mapped[float | None] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    product: Mapped[Product] = relationship()
