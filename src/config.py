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

# Optional: also download SoundCloud likes (the "user" in soundcloud.com/user).
SOUNDCLOUD_USER = os.environ.get("SOUNDCLOUD_USER", "")

SLSKD_URL = os.environ.get("SLSKD_URL", "http://localhost:5030")
SLSKD_API_KEY = _require("SLSKD_API_KEY")

STATE_DB_PATH = os.environ.get("STATE_DB_PATH", str(BASE_DIR / "data" / "state.db"))
DOWNLOAD_DIR = Path(os.environ.get("DOWNLOAD_DIR", str(BASE_DIR / "data" / "downloads")))
# Optional: every finished track is also copied here (e.g. your rekordbox collection).
COPY_TO_DIR = Path(os.environ["COPY_TO_DIR"]) if os.environ.get("COPY_TO_DIR") else None

PREFERRED_FORMATS = [f.strip() for f in os.environ.get("PREFERRED_FORMATS", "flac,mp3").split(",")]
MIN_BITRATE = int(os.environ.get("MIN_BITRATE", "256"))
SEARCH_TIMEOUT_SECONDS = int(os.environ.get("SEARCH_TIMEOUT_SECONDS", "20"))
DOWNLOAD_TIMEOUT_SECONDS = int(os.environ.get("DOWNLOAD_TIMEOUT_SECONDS", "600"))
# Genre classification (see config/genres.example.yaml). Both keys are
# optional: Discogs is queried anyway (more slowly without a token), and
# Last.fm only with a key.
GENRES_CONFIG_PATH = os.environ.get(
    "GENRES_CONFIG_PATH", str(BASE_DIR / "config" / "genres.yaml")
)
DISCOGS_TOKEN = os.environ.get("DISCOGS_TOKEN", "")
# Copy each download into its genre subfolder of COPY_TO_DIR when the
# classifier is confident (unclear ones go to COPY_TO_DIR itself).
SORT_BY_GENRE = os.environ.get("SORT_BY_GENRE", "true").lower() not in ("0", "false", "no")
LASTFM_API_KEY = os.environ.get("LASTFM_API_KEY", "")

# rekordbox: after each run, sync the collection with the genre folders
# (see src/rekordbox.py). Only while rekordbox is closed.
REKORDBOX_SYNC = os.environ.get("REKORDBOX_SYNC", "false").lower() in ("1", "true", "yes")
REKORDBOX_PLAYLIST_FOLDER = os.environ.get("REKORDBOX_PLAYLIST_FOLDER", "my_music")

# Tracks that weren't found or failed to download are retried on later runs
# until they've been tried this many times.
MAX_ATTEMPTS = int(os.environ.get("MAX_ATTEMPTS", "5"))
