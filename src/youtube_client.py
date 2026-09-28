"""Fallback downloader for tracks not found, or not downloadable, on
Soulseek: searches YouTube with yt-dlp (no API key needed, same library
already used to read SoundCloud likes) and downloads the audio. Requires
ffmpeg on PATH for yt-dlp's audio extraction.
"""
import logging
from pathlib import Path

import yt_dlp

from . import config, slskd_client

log = logging.getLogger("spotseek")

_TMP_DIR = config.DOWNLOAD_DIR / ".youtube-tmp"
_SEARCH_RESULTS = 3
# YouTube durations often include an intro/outro Soulseek files don't, so
# this is looser than slskd_client.DURATION_TOLERANCE_SECONDS.
_DURATION_TOLERANCE_SECONDS = 20


def _pick_entry(entries: list[dict], duration_seconds: float | None) -> dict | None:
    """Best of a few search results: closest match to the track's known
    duration, or the first result when it's unknown. Full DJ sets/mixes
    that a loose title match can pull in are discarded."""
    candidates = [e for e in entries if e and (e.get("duration") or 0) <= slskd_client.MAX_LENGTH_SECONDS]
    if not candidates:
        return None
    if not duration_seconds:
        return candidates[0]
    return min(candidates, key=lambda e: abs((e.get("duration") or 0) - duration_seconds))


def download(artist: str, title: str, duration_seconds: float | None = None) -> Path | None:
    """Searches YouTube for "<artist> <title>" and downloads the closest
    match as audio into a temp subfolder of DOWNLOAD_DIR (organizer.
    tidy_download moves it to the top, same as a Soulseek download). Returns
    None if disabled, nothing usable was found, or the download failed."""
    if not config.YTDLP_FALLBACK:
        return None

    query = f"{artist} {title}"
    try:
        with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "extract_flat": True}) as ydl:
            results = ydl.extract_info(f"ytsearch{_SEARCH_RESULTS}:{query}", download=False)
    except yt_dlp.utils.DownloadError:
        log.warning("yt-dlp search failed for: %s", query)
        return None

    entry = _pick_entry(results.get("entries") or [], duration_seconds)
    if not entry:
        log.warning("No usable YouTube result for: %s", query)
        return None

    _TMP_DIR.mkdir(parents=True, exist_ok=True)
    ydl_opts = {
        "format": "bestaudio/best",
        "outtmpl": str(_TMP_DIR / f"{entry['id']}.%(ext)s"),
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "postprocessors": [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": config.YTDLP_AUDIO_FORMAT,
                "preferredquality": config.YTDLP_AUDIO_QUALITY,
            }
        ],
    }
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([f"https://www.youtube.com/watch?v={entry['id']}"])
    except yt_dlp.utils.DownloadError:
        log.warning("yt-dlp download failed for: %s", query)
        return None

    downloaded_path = next(_TMP_DIR.glob(f"{entry['id']}.*"), None)
    if not downloaded_path:
        log.warning("yt-dlp reported success but no file found for: %s", query)
        return None

    log.info("Found on YouTube: %s", entry.get("title") or query)
    return downloaded_path
