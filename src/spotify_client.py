import spotipy
from spotipy.oauth2 import SpotifyOAuth

from . import config


def get_oauth_manager() -> SpotifyOAuth:
    return SpotifyOAuth(
        client_id=config.SPOTIFY_CLIENT_ID,
        client_secret=config.SPOTIFY_CLIENT_SECRET,
        redirect_uri=config.SPOTIFY_REDIRECT_URI,
        scope=config.SPOTIFY_SCOPE,
        cache_path=config.SPOTIFY_TOKEN_CACHE,
        open_browser=False,
    )


def get_client() -> spotipy.Spotify:
    return spotipy.Spotify(auth_manager=get_oauth_manager())


def iter_new_liked_tracks(sp: spotipy.Spotify, seen_ids: set, page_size: int = 50):
    """Walks liked tracks newest-first, stopping as soon as it hits one
    already processed in a previous run."""
    offset = 0
    while True:
        page = sp.current_user_saved_tracks(limit=page_size, offset=offset)
        items = page.get("items", [])
        if not items:
            return
        for item in items:
            track = item["track"]
            if track["id"] in seen_ids:
                return
            yield track
        offset += page_size
