import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent


def _require(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(
            f"Missing environment variable {name}. Copy .env.example to .env and fill it in."
        )
    return value


SPOTIFY_CLIENT_ID = _require("SPOTIFY_CLIENT_ID")
SPOTIFY_CLIENT_SECRET = _require("SPOTIFY_CLIENT_SECRET")
SPOTIFY_REDIRECT_URI = os.environ.get("SPOTIFY_REDIRECT_URI", "http://127.0.0.1:8888/callback")
SPOTIFY_SCOPE = "user-library-read"
SPOTIFY_TOKEN_CACHE = os.environ.get(
    "SPOTIFY_TOKEN_CACHE", str(BASE_DIR / "data" / ".spotify-token-cache")
)

SLSKD_URL = os.environ.get("SLSKD_URL", "http://localhost:5030")
SLSKD_API_KEY = _require("SLSKD_API_KEY")

STATE_DB_PATH = os.environ.get("STATE_DB_PATH", str(BASE_DIR / "data" / "state.db"))
DOWNLOAD_DIR = Path(os.environ.get("DOWNLOAD_DIR", str(BASE_DIR / "data" / "downloads")))

PREFERRED_FORMATS = [f.strip() for f in os.environ.get("PREFERRED_FORMATS", "flac,mp3").split(",")]
MIN_BITRATE = int(os.environ.get("MIN_BITRATE", "256"))
SEARCH_TIMEOUT_SECONDS = int(os.environ.get("SEARCH_TIMEOUT_SECONDS", "20"))
DOWNLOAD_TIMEOUT_SECONDS = int(os.environ.get("DOWNLOAD_TIMEOUT_SECONDS", "600"))
