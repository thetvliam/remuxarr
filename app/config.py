from pydantic_settings import BaseSettings
from pathlib import Path


class Settings(BaseSettings):
    APP_NAME: str = "Remuxarr"
    DEBUG: bool = False

    # Server
    # HOST/PORT deliberately absent. They existed here and nothing read them:
    # the container runs uvicorn with the address on the command line, and
    # docker-compose maps the port. A settings field nobody consumes reads as
    # a supported override, so changing REMUXARR_PORT looked like it should
    # work and silently did nothing.

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

    # Recycle bin — where revert sidecars live. A dedicated volume rather
    # than TEMP_DIR (wiped on restart, so it cannot hold a retention window)
    # and rather than a path next to the media (a sidecar sitting in the
    # library is one MEDIA_EXTENSIONS change away from being scanned and
    # processed as if it were a real file).
    #
    # Deliberately NOT mkdir'd at import the way TEMP_DIR is below. If the
    # volume is not mounted, creating it would silently succeed inside the
    # container's own writable layer: sidecars would survive restarts,
    # convince the user their retention window works, and then disappear on
    # the next image pull — precisely when they matter. app/core/recycle.py
    # treats "the directory is not there" as "the feature is not configured"
    # and says so, instead of manufacturing a directory to write into.
    RECYCLE_DIR: str = "/recycle"

    # Webhook debounce — how long to wait after the last trigger before
    # processing (handles rapid season-pack renames from Sonarr).
    WEBHOOK_DEBOUNCE_SECONDS: float = 10.0

    class Config:
        env_prefix = "REMUXARR_"
        env_file = ".env"


settings = Settings()

# Ensure temp dir exists at import time
Path(settings.TEMP_DIR).mkdir(parents=True, exist_ok=True)
