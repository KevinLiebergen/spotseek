"""Reads SoundCloud likes with yt-dlp (no API key needed; the likes must be
public). Tracks are returned in the same shape as Spotify tracks so the
rest of the pipeline can treat them alike; their ids are prefixed with
"sc:" so they never collide with Spotify ids in the state database."""
import re

import yt_dlp

from . import config

ID_PREFIX = "sc:"

_YDL_OPTIONS = {"quiet": True, "no_warnings": True}

# "[Free DL]", "(FREE DOWNLOAD)", "FREE DOWNLOAD: X - Y", "X - Y - FREE DOWNLOAD":
# download-gate noise, never in filenames.
_FREE_DOWNLOAD = [
    re.compile(r"\s*[(\[][^)\]]*\bfree\b[^)\]]*[)\]]", re.IGNORECASE),
    re.compile(r"^\s*free\s*(download|dl)\s*[:|-]\s*", re.IGNORECASE),
    re.compile(r"\s*[:|-]\s*free\s*(download|dl)\s*$", re.IGNORECASE),
]


# "9. ", "03 - ", "12) ": a track number from an album upload. Every word of a
# Soulseek query has to be in the file path, so it would make the search
# fail. A separator and a space are required, so "2Pac" or "50 Cent" stay.
_TRACK_NUMBER = re.compile(r"^\s*\d{1,3}\s*[.)\-–]\s+")


def _likes_url() -> str:
    return f"https://soundcloud.com/{config.SOUNDCLOUD_USER}/likes"


def _split_title(title: str) -> tuple[str | None, str]:
    """SoundCloud has no separate artist field: "Artist - Title" is just a
    convention. Returns (None, title) when the title doesn't follow it."""
    for pattern in _FREE_DOWNLOAD:
        title = pattern.sub("", title)
    title = _TRACK_NUMBER.sub("", title)
    if "|" in title:  # "ROOKIE #4 | TEN CUIDADO (EDIT)": series name first
        title = title.rsplit("|", 1)[1]
    title = " ".join(title.split())
    if " - " in title:
        artist, name = title.split(" - ", 1)
        return artist.strip(), name.strip()
    return None, title


def _to_track(info: dict) -> dict:
    artist, name = _split_title(info.get("title") or "")
    return {
        "id": ID_PREFIX + str(info["id"]),
        "name": name,
        # Fall back to the uploader, which is often a label or a channel
        # rather than the artist, so searches may also try the title alone.
        "artists": [{"name": artist or info.get("uploader") or ""}],
        "artist_is_uploader": artist is None,
        "duration_ms": int((info.get("duration") or 0) * 1000),
    }


def get_track(track_id: str) -> dict:
    sc_id = track_id.removeprefix(ID_PREFIX)
    with yt_dlp.YoutubeDL(_YDL_OPTIONS) as ydl:
        info = ydl.extract_info(
            f"https://api.soundcloud.com/tracks/{sc_id}", download=False, process=False
        )
    return _to_track(info)


def iter_new_liked_tracks(seen_ids: set):
    """Walks likes newest-first, stopping at the first one already seen.
    The list only has titles: use get_track() before searching, to get the
    uploader and duration."""
    with yt_dlp.YoutubeDL({**_YDL_OPTIONS, "extract_flat": True}) as ydl:
        likes = ydl.extract_info(_likes_url(), download=False)
    for entry in likes.get("entries") or []:
        if "/sets/" in entry.get("url", ""):
            continue  # a liked playlist, not a track
        track = _to_track(entry)
        if track["id"] in seen_ids:
            return
        yield track
