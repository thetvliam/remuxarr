from pydantic_settings import BaseSettings
from pathlib import Path


class Settings(BaseSettings):
    APP_NAME: str = "Remuxarr"
    DEBUG: bool = False

    # Server
    HOST: str = "0.0.0.0"
    PORT: int = 8000

    # Database — lives in /config so it survives container restarts
    DATABASE_PATH: str = "/config/remuxarr.db"

    # FFmpeg binaries (already on PATH inside the container)
    FFMPEG_PATH: str = "ffmpeg"
    FFPROBE_PATH: str = "ffprobe"

    # Worker
    # Note: worker concurrency (max concurrent jobs) is deliberately NOT
    # here — it's a runtime setting, read exclusively from the
    # database-backed app settings (Settings > Worker in the web UI, see
    # app/database/session.py), not an environment variable. A
    # MAX_CONCURRENT_JOBS field used to live here but was never actually
    # read by anything — confirmed via a full codebase search before
    # removing it.
    TEMP_DIR: str = "/tmp/remuxarr"

    # Webhook debounce — how long to wait after the last trigger before
    # processing (handles rapid season-pack renames from Sonarr).
    WEBHOOK_DEBOUNCE_SECONDS: float = 10.0

    class Config:
        env_prefix = "REMUXARR_"
        env_file = ".env"


settings = Settings()

# Ensure temp dir exists at import time
Path(settings.TEMP_DIR).mkdir(parents=True, exist_ok=True)
