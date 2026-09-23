"""Run ONCE to generate the cached Spotify token.

Usage:
    python scripts/authorize_spotify.py

It prints a URL to open in your browser. Log in, authorize the app, and
paste the full URL you get redirected to back here (even if the browser
shows a "can't connect" error, the address bar already has the code we
need).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import config, spotify_client  # noqa: E402


def main() -> None:
    Path(config.SPOTIFY_TOKEN_CACHE).parent.mkdir(parents=True, exist_ok=True)
    auth_manager = spotify_client.get_oauth_manager()

    auth_url = auth_manager.get_authorize_url()
    print("Open this URL, log in, and authorize the app:\n")
    print(auth_url)

    redirected_url = input("\nPaste the full URL you were redirected to: ").strip()
    code = auth_manager.parse_response_code(redirected_url)
    auth_manager.get_access_token(code, as_dict=False)

    print("\nToken saved successfully to:", config.SPOTIFY_TOKEN_CACHE)


if __name__ == "__main__":
    main()
