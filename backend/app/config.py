"""Runtime configuration, read from environment variables (or a .env file)."""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # --- Core ---
    database_url: str = "sqlite:///./styletrail.db"
    public_base_url: str = "http://localhost:8000"  # where Google Lens can fetch uploaded images
    frontend_origin: str = "http://localhost:3000"
    admin_token: str = ""  # protects /api/admin/* (cron refresh trigger)

    # --- Bright Data ---
    brightdata_api_key: str = ""
    brightdata_serp_zone: str = ""        # SERP API zone (Google Lens + Google Shopping)
    brightdata_unlocker_zone: str = ""    # Web Unlocker zone (product pages)
    # Optional raw proxy credentials for the SERP zone. Lets us POST the image bytes
    # straight to Lens instead of making Lens fetch a public URL (needed for localhost dev).
    brightdata_customer_id: str = ""
    brightdata_serp_zone_password: str = ""
    # --- Lens (reverse image search); keys may also live in AWS SSM Parameter Store /styletrail/ ---
    serpapi_key: str = ""                 # SerpApi Google Lens
    serpapi_lens_types: str = "exact_matches,visual_matches"
    serpapi_shopping_queries: int = 2     # Google Shopping text searches per photo (Myntra/AJIO/Nykaa feeds)
    serpapi_resolve_max: int = 3          # matched Shopping products whose store link is looked up
    google_vision_key: str = ""           # Google Cloud Vision WEB_DETECTION
    country: str = "in"
    language: str = "en"

    # --- Optional vision captioner (Claude) to turn the photo into shopping queries ---
    llm_provider: str = "none"  # none | anthropic | bedrock (Amazon Nova or Claude through the IAM role)
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-haiku-5-5"
    bedrock_model: str = ""   # vision model that reads the photo, e.g. us.meta.llama4-maverick-17b-instruct-v1:0
    bedrock_region: str = ""  # region of bedrock_model (defaults to aws_region)
    # Judges that confirm "same product"; comma list of model@region. A listing is exact only when
    # every judge that answered says same (and at least two answered, if two are configured).
    verify_models: str = ""
    aws_region: str = "ap-south-1"

    # --- Tuning ---
    max_candidates: int = 110         # listings compared with the photo per search
    max_product_pages: int = 30       # product pages scraped for live price per search
    http_concurrency: int = 8
    request_timeout_s: float = 60.0
    exact_threshold: float = 0.80
    close_threshold: float = 0.62

    # --- Alerts (optional e-mail on price drop) ---
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    alert_from: str = ""

    def model_post_init(self, _ctx) -> None:
        # Values pasted into dashboards often carry spaces, newlines or quotes.
        for f in ("brightdata_api_key", "brightdata_serp_zone", "brightdata_unlocker_zone",
                  "brightdata_customer_id", "brightdata_serp_zone_password", "anthropic_api_key", "admin_token",
                  "serpapi_key", "google_vision_key"):
            v = getattr(self, f) or ""
            setattr(self, f, v.strip().strip('"').strip("'").strip())
        # On Render, web services get RENDER_EXTERNAL_URL automatically; use it so Lens can fetch uploads.
        import os
        ext = os.getenv("RENDER_EXTERNAL_URL")
        if ext and ("localhost" in self.public_base_url or not self.public_base_url):
            self.public_base_url = ext

    @property
    def brightdata_ready(self) -> bool:
        return bool(self.brightdata_api_key and self.brightdata_serp_zone)

    @property
    def sqlalchemy_url(self) -> str:
        url = (self.database_url or "").strip()
        if not url:
            # No database configured yet: temporary SQLite (lost on restart). Set DATABASE_URL to keep history.
            url = "sqlite:////tmp/styletrail.db"
        # Render hands out postgres:// URLs; SQLAlchemy + psycopg3 wants postgresql+psycopg://
        if url.startswith("postgres://"):
            url = "postgresql+psycopg://" + url[len("postgres://"):]
        elif url.startswith("postgresql://"):
            url = "postgresql+psycopg://" + url[len("postgresql://"):]
        return url


@lru_cache
def get_settings() -> Settings:
    return Settings()
