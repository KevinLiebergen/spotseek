"""Fallback downloader for tracks not found, or not downloadable, on
Soulseek: with yt-dlp (no API key needed, same library already used to
read SoundCloud likes), either downloads a track straight from its own
page (its SoundCloud URL) or, when there is none, searches YouTube for
"<artist> <title>" and downloads a match.

The audio is never re-encoded (that only loses quality). SoundCloud and
YouTube serve it in fragments, though: with ffmpeg on PATH yt-dlp puts
them back into a regular .m4a, without re-encoding; without ffmpeg the
result would be a fragmented MP4 that DJ software and players may not
read, so then only SoundCloud's plain MP3 is used and YouTube is skipped.
These files are usually 128-160 kbps, below what Soulseek downloads are
held to, so main.py marks them in the file name ("[SC]" / "[YT]").
"""
import shutil
import logging
import re
from pathlib import Path

import yt_dlp

from . import config, genre, slskd_client

log = logging.getLogger("spotseek")

_TMP_DIR = config.DOWNLOAD_DIR / ".youtube-tmp"
_SEARCH_RESULTS = 5
# YouTube durations often include an intro/outro Soulseek files don't, so
# this is looser than slskd_client.DURATION_TOLERANCE_SECONDS.
_DURATION_TOLERANCE_SECONDS = 20
# Formats rekordbox plays, best first; Opus/WebM (YouTube's default) isn't one.
_FORMAT_WITH_FFMPEG = "bestaudio[ext=m4a]/bestaudio[ext=mp3]/bestaudio[acodec^=mp4a]"
# Without ffmpeg only files served whole over plain HTTP are regular files.
_FORMAT_WITHOUT_FFMPEG = "bestaudio[ext=mp3][protocol^=http]"
_PLAYABLE = {".m4a", ".mp3", ".aac", ".mp4"}


def has_ffmpeg() -> bool:
    return shutil.which("ffmpeg") is not None

_NOT_SLUG = re.compile(r"[^A-Za-z0-9]+")
_BASE_TITLE = re.compile(r"\s*([(\[]| - ).*$")


def _download_audio(url: str, filename_stem: str) -> Path | None:
    """Downloads a single URL as audio into a temp subfolder of
    DOWNLOAD_DIR (organizer.tidy_download moves it to the top, same as a
    Soulseek download). None if the download failed; whatever it left
    behind is removed."""
    _TMP_DIR.mkdir(parents=True, exist_ok=True)
    ydl_opts = {
        "format": _FORMAT_WITH_FFMPEG if has_ffmpeg() else _FORMAT_WITHOUT_FFMPEG,
        "outtmpl": str(_TMP_DIR / f"{filename_stem}.%(ext)s"),
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,  # the task runs hidden: the progress bar only fills the error log
    }
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
    except yt_dlp.utils.DownloadError as e:
        log.warning("yt-dlp couldn't download %s: %s", url, str(e)[:200])
        _cleanup(filename_stem)
        return None

    path = next((p for p in _TMP_DIR.glob(f"{filename_stem}.*") if p.suffix.lower() in _PLAYABLE), None)
    if not path:
        log.warning("yt-dlp got no format rekordbox plays for %s", url)
        _cleanup(filename_stem)
    return path


def _cleanup(filename_stem: str) -> None:
    for leftover in _TMP_DIR.glob(f"{filename_stem}.*"):
        try:
            leftover.unlink()
        except OSError:
            pass


def download_from_source(url: str) -> Path | None:
    """Downloads a track straight from its own page instead of searching
    for it, e.g. a SoundCloud like's URL: many SoundCloud edits and
    bootlegs are exclusive to it and wouldn't turn up on a YouTube search
    either. None if disabled or the download failed."""
    if not config.YTDLP_FALLBACK:
        return None

    stem = _NOT_SLUG.sub("-", url).strip("-")[-80:] or "track"
    downloaded_path = _download_audio(url, stem)
    if downloaded_path:
        log.info("Downloaded straight from its own page: %s", url)
    return downloaded_path


def _title_words(title: str) -> set[str]:
    """Words of a track title that a video about it should contain: its
    base (no "(feat. ...)", "(Extended Mix)" or " - Remix" part) and, for a
    remix or edit, who made it."""
    words = set(genre._normalize(_BASE_TITLE.sub("", title)).split())
    return {w for w in words | slskd_client._remixer_words(title) if len(w) > 1}


def _pick_entry(entries: list[dict], title: str, duration_seconds: float | None) -> dict | None:
    """The search result that is this track: its title contains the track's
    title words (not another song of the same artist), its length is close
    to the track's when that's known, and it isn't a full DJ set. Closest
    length wins."""
    wanted = _title_words(title)
    candidates = []
    for entry in entries:
        if not entry:
            continue
        length = entry.get("duration") or 0
        if length > slskd_client.MAX_LENGTH_SECONDS:
            continue
        if duration_seconds and length and abs(length - duration_seconds) > _DURATION_TOLERANCE_SECONDS:
            continue
        video_words = set(genre._normalize(entry.get("title") or "").split())
        if not wanted <= video_words:
            continue
        candidates.append(entry)
    if not candidates:
        return None
    if not duration_seconds:
        return candidates[0]
    return min(candidates, key=lambda e: abs((e.get("duration") or 0) - duration_seconds))


def download(artist: str, title: str, duration_seconds: float | None = None) -> Path | None:
    """Searches YouTube for "<artist> <title>" and downloads the matching
    result as audio. None if disabled, nothing matched, or the download
    failed."""
    if not config.YTDLP_FALLBACK:
        return None
    if not has_ffmpeg():
        # YouTube only serves fragmented audio, which needs ffmpeg to become a
        # regular file.
        log.warning("Skipping YouTube for %s - %s: install ffmpeg to enable it", artist, title)
        return None

    query = f"{artist} {title}"
    try:
        with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "extract_flat": True}) as ydl:
            results = ydl.extract_info(f"ytsearch{_SEARCH_RESULTS}:{query}", download=False)
    except yt_dlp.utils.DownloadError:
        log.warning("yt-dlp search failed for: %s", query)
        return None

    entry = _pick_entry(results.get("entries") or [], title, duration_seconds)
    if not entry:
        log.warning("No YouTube result matches: %s", query)
        return None

    downloaded_path = _download_audio(f"https://www.youtube.com/watch?v={entry['id']}", entry["id"])
    if downloaded_path:
        log.info("Found on YouTube: %s", entry.get("title") or query)
    return downloaded_path
