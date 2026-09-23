import re

import requests
import yaml

from . import config


def _load_mapping() -> dict:
    path = config.GENRE_MAPPING_PATH
    try:
        with open(path, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except FileNotFoundError:
        raise RuntimeError(
            f"{path} does not exist. Copy config/genre_mapping.example.yaml to "
            "config/genre_mapping.yaml and adjust it to your folders."
        )


def _normalize(tag: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", tag.lower()).strip()


def map_tags_to_folder(tags: list[str], mapping: dict) -> str | None:
    normalized_keys = {_normalize(k): v for k, v in mapping.items()}

    for tag in tags:
        norm = _normalize(tag)
        if norm in normalized_keys:
            return normalized_keys[norm]

    for tag in tags:
        norm = _normalize(tag)
        for key, folder in normalized_keys.items():
            if key in norm or norm in key:
                return folder

    return None


def lastfm_tags(artist: str, track: str) -> list[str]:
    if not config.LASTFM_API_KEY:
        return []
    try:
        resp = requests.get(
            "https://ws.audioscrobbler.com/2.0/",
            params={
                "method": "track.gettoptags",
                "artist": artist,
                "track": track,
                "api_key": config.LASTFM_API_KEY,
                "format": "json",
            },
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        return [t["name"] for t in data.get("toptags", {}).get("tag", [])]
    except requests.RequestException:
        return []


def resolve_genre_folder(spotify_genres: list[str], artist: str, track: str) -> str:
    mapping = _load_mapping()

    folder = map_tags_to_folder(spotify_genres, mapping)
    if folder:
        return folder

    folder = map_tags_to_folder(lastfm_tags(artist, track), mapping)
    if folder:
        return folder

    return config.UNCLASSIFIED_FOLDER
