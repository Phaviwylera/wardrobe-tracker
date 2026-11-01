"""Thin async client for Bright Data's SERP API and Web Unlocker.

Both go through POST https://api.brightdata.com/request with a Bearer key; the zone
decides which product handles the request.
"""
from __future__ import annotations

import asyncio
import json
import logging
import random
from urllib.parse import quote, urlencode

import httpx

from ..config import Settings, get_settings

log = logging.getLogger(__name__)
API = "https://api.brightdata.com/request"


class BrightDataError(RuntimeError):
    pass


class _Fatal(BrightDataError):
    """Non-retryable error."""


class BrightData:
    def __init__(self, settings: Settings | None = None, client: httpx.AsyncClient | None = None):
        self.s = settings or get_settings()
        self._client = client or httpx.AsyncClient(timeout=self.s.request_timeout_s)
        self._sem = asyncio.Semaphore(self.s.http_concurrency)

    async def aclose(self) -> None:
        await self._client.aclose()

    # ---------------------------------------------------------------- low level
    async def _request(self, zone: str, url: str, *, data_format: str | None = None,
                       country: str | None = None, retries: int = 3) -> httpx.Response:
        if not self.s.brightdata_api_key or not zone:
            raise BrightDataError("Bright Data API key / zone not configured")
        body: dict = {"zone": zone, "url": url, "format": "raw"}
        if country:
            body["country"] = country
        if data_format:
            body["data_format"] = data_format
        headers = {"Authorization": f"Bearer {self.s.brightdata_api_key}"}
        last: Exception | None = None
        for attempt in range(retries):
            try:
                async with self._sem:
                    r = await self._client.post(API, json=body, headers=headers)
                if r.status_code in (429, 500, 502, 503, 504):
                    raise BrightDataError(f"HTTP {r.status_code}: {r.text[:200]}")
                if r.status_code >= 400:
                    # 4xx other than 429 won't improve on retry (bad zone, blocked target, …)
                    raise _Fatal(f"HTTP {r.status_code}: {r.text[:300]}")
                return r
            except _Fatal as e:
                last = e
                break
            except (httpx.TransportError, BrightDataError) as e:
                last = e
                await asyncio.sleep((2 ** attempt) + random.random())
        raise BrightDataError(f"request failed for {url[:120]}: {last}")

    # ---------------------------------------------------------------- SERP
    async def serp_json(self, url: str) -> dict:
        r = await self._request(self.s.brightdata_serp_zone, url)
        try:
            return r.json()
        except json.JSONDecodeError as e:
            raise BrightDataError(f"SERP returned non-JSON for {url[:120]}") from e

    async def lens_by_url(self, image_url: str, tab: str) -> dict:
        params = {"url": image_url, "hl": self.s.language, "gl": self.s.country,
                  "brd_json": 1, "brd_lens": tab}
        return await self.serp_json("https://lens.google.com/uploadbyurl?" + urlencode(params))

    async def lens_by_upload(self, image: bytes, tab: str) -> dict:
        """POST the image straight to Lens through the SERP zone's proxy port.

        Used when the uploaded image isn't reachable from the public internet (local dev).
        """
        s = self.s
        if not (s.brightdata_customer_id and s.brightdata_serp_zone_password):
            raise BrightDataError("proxy credentials not set; cannot upload image bytes to Lens")
        user = f"brd-customer-{s.brightdata_customer_id}-zone-{s.brightdata_serp_zone}"
        proxy = f"http://{user}:{quote(s.brightdata_serp_zone_password)}@brd.superproxy.io:33335"
        target = (f"https://lens.google.com/v3/upload?brd_json=1&brd_lens={tab}"
                  f"&hl={s.language}&gl={s.country}")
        async with httpx.AsyncClient(proxy=proxy, timeout=s.request_timeout_s) as c:
            r = await c.post(target, files={"encoded_image": ("query.jpg", image, "image/jpeg")})
        if r.status_code >= 400:
            raise BrightDataError(f"Lens upload HTTP {r.status_code}: {r.text[:200]}")
        return r.json()

    async def google_shopping(self, query: str) -> dict:
        params = {"q": query, "tbm": "shop", "gl": self.s.country, "hl": self.s.language, "brd_json": 1}
        return await self.serp_json("https://www.google.com/search?" + urlencode(params))

    async def google_organic(self, query: str) -> dict:
        params = {"q": query, "gl": self.s.country, "hl": self.s.language, "brd_json": 1}
        return await self.serp_json("https://www.google.com/search?" + urlencode(params))

    # ---------------------------------------------------------------- Unlocker
    async def fetch_html(self, url: str) -> str:
        if not self.s.brightdata_unlocker_zone:
            # SERP zones only reach search engines; without a Web Unlocker zone we use SERP prices.
            raise BrightDataError("no Web Unlocker zone configured")
        r = await self._request(self.s.brightdata_unlocker_zone, url, country=self.s.country)
        return r.text

    # ---------------------------------------------------------------- images
    async def fetch_image(self, url: str) -> bytes | None:
        """Product thumbnails are on public CDNs; fetch directly (cheap), fall back to Unlocker."""
        if url.startswith("data:image"):
            import base64
            try:
                return base64.b64decode(url.split(",", 1)[1])
            except Exception:
                return None
        try:
            async with self._sem:
                r = await self._client.get(url, headers={"User-Agent": _UA}, follow_redirects=True,
                                           timeout=20)
            if r.status_code == 200 and r.headers.get("content-type", "").startswith("image"):
                return r.content
        except httpx.HTTPError:
            pass
        return None


_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/130.0 Safari/537.36")
