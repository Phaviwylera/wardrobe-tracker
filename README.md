# StyleTrail

Upload a photo of an outfit and see every Indian store selling that exact piece, with live prices,
price history and drop alerts. Built for women's ethnic and western wear on Myntra, AJIO, Nykaa Fashion,
Flipkart, Amazon.in, Meesho, Tata CLiQ and ~30 brand stores.

## How a search works

1. **Discover** – the photo goes to Google Lens through Bright Data's SERP API (exact matches, products
   and visual matches tabs, India locale). Optionally, Claude describes the garment and those queries run
   on Google Shopping and site-filtered Google to catch listings Lens misses.
2. **Filter** – only single-product pages on shops survive (category pages, Pinterest, blogs are dropped);
   tracking links collapse to one canonical URL per product (`app/services/stores.py`).
3. **Verify** – every listing photo is compared with yours (`app/services/matcher.py`): perceptual hashes
   for the identical photo, ORB keypoints + RANSAC for the same print shot differently, and a hue
   histogram so colour variants never count as an exact match. Optional CLIP embeddings.
4. **Price** – the best candidates' pages are fetched through Web Unlocker and parsed
   (`app/services/extract.py`): Myntra / AJIO / Nykaa embedded state, Amazon DOM, Shopify, JSON-LD,
   OpenGraph, then a guarded regex. Gives price, MRP, sizes in stock and ratings.
5. **Track** – every price lands in `price_points`. A Render cron rechecks watched and recent products
   every 6 hours and e-mails when a watch-list target is hit.

Features: crop to one garment, paste screenshots, exact / very close / similar groups, lowest-price
badge, filters by store, budget and stock, price chart with lowest / average / highest and a
"good time to buy" verdict, same piece at other stores, watch list with target prices, and tracking any
pasted product link.

## Bright Data setup

Create two zones in the Bright Data control panel:

| Zone | Product | Used for |
|------|---------|----------|
| e.g. `serp_api1` | SERP API | Google Lens and Google Shopping |
| e.g. `web_unlocker1` | Web Unlocker | Product pages (Myntra, AJIO, Amazon…) |

Then set `BRIGHTDATA_API_KEY`, `BRIGHTDATA_SERP_ZONE`, `BRIGHTDATA_UNLOCKER_ZONE`.

Google Lens fetches your uploaded photo from `PUBLIC_BASE_URL/api/images/<id>.jpg`, so it must be
publicly reachable. On Render that is automatic. For local development either use a tunnel
(`cloudflared tunnel --url localhost:8000`, then set `PUBLIC_BASE_URL`) or set
`BRIGHTDATA_CUSTOMER_ID` + `BRIGHTDATA_SERP_ZONE_PASSWORD` so the image bytes are uploaded to Lens
through the proxy instead.

Before the first real search, check what Lens and the extractor return with your zones:

```bash
cd backend
python -m app.diagnose lens https://<a public image of a kurta>.jpg
python -m app.diagnose page https://www.myntra.com/<a product>/buy
```

## Run locally

```bash
# API
cd backend
cp .env.example .env            # fill in the Bright Data values
pip install -r requirements-dev.txt
uvicorn app.main:app --reload    # http://localhost:8000
pytest                          # unit + end-to-end tests (Bright Data faked)

# Web
cd frontend
npm install
npm run dev                     # http://localhost:3000, proxies /api to :8000
```

SQLite is used when `DATABASE_URL` is unset; production uses Postgres.

## Deploy on Render

`render.yaml` creates a project with Postgres, the FastAPI service, the Next.js site and the
6-hourly price-refresh cron, all in Singapore (closest region to India).

1. Push this repo to GitHub.
2. Render dashboard → New → Blueprint → pick the repo.
3. Enter the Bright Data values when asked (both for `styletrail-api` and the cron job).
   `ANTHROPIC_API_KEY` can be left empty.
4. Open the `styletrail-web` URL.

Optional settings on the API service:

- `LLM_PROVIDER=anthropic` + `ANTHROPIC_API_KEY`, or `LLM_PROVIDER=bedrock` + `BEDROCK_MODEL` +
  AWS credentials, for garment descriptions and text search.
- `ENABLE_CLIP=1` after adding `open_clip-torch` and `torch` to requirements (needs ~2 GB RAM).
- `SMTP_HOST`, `SMTP_USER`, `SMTP_PASSWORD`, `ALERT_FROM` for e-mail price alerts.
- `SEARCHES_PER_HOUR` (default 30 per browser) to cap Bright Data spend.

## Cost per search (roughly)

3 Lens requests + up to 4 text searches (SERP API) and up to 30 product pages (Web Unlocker).
Lower `MAX_PRODUCT_PAGES` to spend less per search.

## Layout

```
backend/app/
  main.py              API routes
  refresh.py           cron entry point (price refresh + alerts)
  diagnose.py          checks Bright Data responses on real pages
  services/
    pipeline.py        search orchestration
    brightdata.py      SERP / Lens / Web Unlocker client with retries
    serp_parse.py      schema-tolerant listing extraction from SERP JSON
    matcher.py         photo-vs-listing verification
    extract.py         product page parsing
    stores.py          store registry, product-URL rules, canonical URLs
    pricing.py         price history and stats
    captioner.py       optional Claude garment description
frontend/              Next.js app (search, results, product history, watch list)
render.yaml            Render Blueprint
```
