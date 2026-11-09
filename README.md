# StyleTrail

Upload a photo of an outfit and see every Indian store selling that exact piece, with live prices,
price history and drop alerts. Built for women's ethnic and western wear on Myntra, AJIO, Nykaa Fashion,
Flipkart, Amazon.in, Meesho, Tata CLiQ and ~30 brand stores.

## How a search works (no third-party API keys)

1. **Read the photo**: a vision model on Amazon Bedrock (Llama 4 Maverick by default) describes the
   garment and writes six shopping queries, including the colour names Indian stores use loosely.
2. **Search stores directly**: Amazon.in, Flipkart and Tata CLiQ search pages/APIs, Shopify brand
   stores (Libas, Berrylush, The Label Life, Okhai, …), Bing Images for Myntra / AJIO / Nykaa Fashion /
   Meesho (which block servers), and Yandex reverse image search for the rest of the web.
3. **Verify**: every listing photo is compared with yours (perceptual hash, ORB keypoints, colour), then
   two independent judges (Llama 4 Maverick and Amazon Nova Pro) compare the top 32 one by one.
   *Exact* needs both to say "same"; one "same" gives *Very close*; otherwise *Similar style*.
4. **Price**: listing prices, plus product pages / APIs for MRP, sizes and stock
   (Amazon, Flipkart, Tata CLiQ product API, Shopify, JSON-LD).
5. **Track**: every price is stored; a cron job rechecks watched and recent products every 6 hours and
   e-mails when a watch-list target is hit.

Bright Data (Google Lens) is still supported as an optional extra: set `BRIGHTDATA_API_KEY` and
`BRIGHTDATA_SERP_ZONE` and its results are merged in.

## Production on AWS (ap-south-1, Mumbai)

| Piece | What |
|---|---|
| App server | EC2 `c7i-flex.large` (free-tier eligible) named `styletrail`, Amazon Linux 2023, Docker |
| HTTPS | CloudFront distribution in front of the server; the server only accepts CloudFront traffic |
| Database | SQLite on the server's encrypted EBS volume (`/var/lib/styletrail`) |
| AI | Bedrock via the instance role `styletrail-ec2-role` (no API keys) |
| Price refresh | `/etc/cron.d/styletrail`, every 6 hours |
| Management | AWS Systems Manager (no SSH port open) |

Settings live in `/etc/styletrail.env` on the server. To deploy the latest `main`:

```bash
sudo /opt/styletrail/deploy.sh      # run through SSM Session Manager or Run Command
```

To use Claude instead of Llama/Nova (better descriptions), accept the Anthropic model terms once in the
Bedrock console (Model access), then set in `/etc/styletrail.env`:

```
BEDROCK_MODEL=global.anthropic.claude-sonnet-4-5-20250929-v1:0
BEDROCK_REGION=ap-south-1
VERIFY_MODELS=global.anthropic.claude-sonnet-4-5-20250929-v1:0@ap-south-1,apac.amazon.nova-pro-v1:0@ap-south-1
```

and rerun `deploy.sh`.

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
# production-style: STATIC_EXPORT=1 npx next build, then STATIC_DIR=../frontend/out uvicorn …
```

SQLite is used when `DATABASE_URL` is unset; production uses Postgres.

## Other hosting

`render.yaml` and the `Dockerfile` also run on Render (set `LLM_PROVIDER=bedrock` with AWS credentials,
or Bright Data keys). AWS is the reference deployment.

## Cost per search (roughly)

About 1 description call and up to 64 short comparison calls on Bedrock (Llama 4 Maverick + Nova Pro),
a few US cents per search. Store searches are plain web requests and cost nothing. Lower
`SEARCHES_PER_HOUR` or the verifier pool (`CLAUDE_CHECK` in `pipeline.py`) to spend less.

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
Dockerfile             builds website + API into one image
render.yaml            Render Blueprint (free plan)
.github/workflows/     6-hourly price refresh
```
