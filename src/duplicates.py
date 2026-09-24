"""Finds tracks you already have in COPY_TO_DIR, so liking a song you already
own doesn't download it again.

A file counts as the same track when its main artist and base title match
and it isn't a different version: a radio edit, original or extended mix
is the same track, but a remix you don't have yet isn't (see
slskd_client._is_other_version).
"""
import re
from pathlib import Path

import mutagen

from . import config, genre, slskd_client

AUDIO_EXTENSIONS = {".mp3", ".flac", ".wav", ".aiff", ".aif", ".m4a"}
_BASE_TITLE = re.compile(r"\s*([(\[]| - ).*$")

_index: dict[str, list[tuple[str, str, Path]]] | None = None  # base title -> (artists, title, path)


def _base_title(title: str) -> str:
    return genre._normalize(_BASE_TITLE.sub("", title))


def _artist_and_title(path: Path) -> tuple[str, str]:
    try:
        audio = mutagen.File(path, easy=True)
    except Exception:
        audio = None
    if audio and audio.get("title"):
        return (audio.get("artist") or [""])[0], audio["title"][0]
    artist, _, title = path.stem.partition(" - ")
    return (artist, title) if title else ("", path.stem)


def _load_index() -> dict:
    index: dict[str, list] = {}
    root = Path(config.COPY_TO_DIR) if config.COPY_TO_DIR else None
    if not root or not root.is_dir():
        return index
    for path in root.rglob("*"):
        if path.suffix.lower() not in AUDIO_EXTENSIONS or not path.is_file():
            continue
        artist, title = _artist_and_title(path)
        entry = (genre._normalize(f"{artist} {path.stem}"), title, path)
        index.setdefault(_base_title(title), []).append(entry)
    return index


def find(artist: str, title: str) -> Path | None:
    """A file in COPY_TO_DIR that is this track, if any."""
    global _index
    if _index is None:
        _index = _load_index()
    main_artist = genre._normalize(genre._clean_for_lookup(artist, title)[0])
    if not main_artist:
        return None
    for artists, file_title, path in _index.get(_base_title(title), []):
        if f" {main_artist} " in f" {artists} " and not slskd_client._is_other_version(
            f"{file_title} {path.stem}", title
        ):
            return path
    return None


def remember(path: Path, artist: str, title: str) -> None:
    """Adds a file copied during this run to the index."""
    if _index is not None:
        entry = (genre._normalize(f"{artist} {path.stem}"), title, path)
        _index.setdefault(_base_title(title), []).append(entry)
