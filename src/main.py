import argparse
import itertools
import logging
import logging.handlers
import re
import socket
import sys
import time
from pathlib import Path

import requests

from . import (
    config,
    duplicates,
    genre,
    organizer,
    slskd_client,
    soundcloud_client,
    spotify_client,
    state,
)

LOG_DIR = Path(config.STATE_DB_PATH).parent
LOG_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        # At most ~20 MB: spotseek.log plus 3 rotated ones of 5 MB.
        logging.handlers.RotatingFileHandler(
            LOG_DIR / "spotseek.log", maxBytes=5_000_000, backupCount=3, encoding="utf-8"
        ),
    ],
)
log = logging.getLogger("spotseek")


# "(feat. X)", "[with X]": the featured artist is often missing from filenames.
_FEATURING = re.compile(r"\s*[(\[]\s*(feat\.?|ft\.?|featuring|with)\s[^)\]]*[)\]]", re.IGNORECASE)
_PARENTHESES = re.compile(r"\s*[(\[]([^)\]]*)[)\]]")
# Parentheses naming a version ("(Toth Edit)", "(Coyu Remix)") are a different
# track than the original, so their words stay in every query.
_VERSION_WORDS = re.compile(r"\b(remix|edit|bootleg|rework|flip|vip|mix|dub|version)\b", re.IGNORECASE)
_PUNCTUATION = re.compile(r"[\"'’`,.!?¿¡:;]")


def _strip_parentheses(text: str) -> str:
    return _PARENTHESES.sub(
        lambda m: f" {m.group(1)}" if _VERSION_WORDS.search(m.group(1)) else "", text
    )


def search_queries(artist: str, title: str, artist_optional: bool = False) -> list[str]:
    """The exact query first, then simpler ones for when Soulseek finds
    nothing: every word in the query has to be in the file path. With
    artist_optional (the "artist" is really the uploader), the title alone
    is tried last."""
    # "- Radio Edit", "(Original Mix)"...: longer versions are accepted anyway.
    cleaned = slskd_client.VERSION_SUFFIX.sub("", _FEATURING.sub("", title))
    # Also drops tags like "(ITA)" from the artist name.
    bare = f"{_PARENTHESES.sub('', artist)} {_strip_parentheses(cleaned)}"
    candidates = [f"{artist} {title}", f"{artist} {cleaned}", bare]
    candidates.append(_PUNCTUATION.sub(" ", candidates[-1]))
    if artist_optional:
        candidates.append(_PUNCTUATION.sub(" ", _strip_parentheses(cleaned)))

    queries = []
    for query in candidates:
        query = " ".join(query.split())
        if query not in queries:
            queries.append(query)
    return queries


# Files from this many different users are tried before giving up on a track.
MAX_CANDIDATES = 3
NETWORK_RETRY_DELAY = 30  # seconds before retrying a track that hit a network error

_NETWORK_ERRORS = (socket.gaierror, TimeoutError, ConnectionError, requests.ConnectionError, requests.Timeout)
_NETWORK_MESSAGES = re.compile(r"getaddrinfo|timed out|connection (reset|aborted|refused)|name resolution", re.I)


def _is_network_error(e: BaseException) -> bool:
    """Whether an error (or one it was raised from) is a network problem.
    yt-dlp wraps them in its own errors, so the message is checked too."""
    seen = set()
    while e is not None and id(e) not in seen:
        seen.add(id(e))
        if isinstance(e, _NETWORK_ERRORS) or _NETWORK_MESSAGES.search(str(e)):
            return True
        e = e.__cause__ or e.__context__
    return False


def _download_first(candidates: list[tuple[str, dict]]) -> tuple[dict, float] | None:
    """Downloads the first candidate that works: (file, time it started).
    A user whose download slskd rejects, fails or doesn't finish in time is
    skipped for the next one."""
    for username, file_info in candidates:
        started_at = time.time()
        try:
            transfer_id = slskd_client.enqueue_download(username, file_info)
        except requests.HTTPError as e:
            # e.g. slskd answers 500 for some user names ("{{{{d(*_*)b}}}}")
            log.warning("slskd couldn't queue the download from %s (%s), trying the next file", username, e)
            continue
        ok, transfer_state = slskd_client.wait_for_download(
            username, file_info["filename"], transfer_id
        )
        if ok:
            return file_info, started_at
        log.warning("Download failed (%s) from %s: %s", transfer_state, username, file_info["filename"])
        slskd_client.cancel_download(username, transfer_id)
    return None


def process_track(track: dict) -> None:
    title = track["name"]
    artist = track["artists"][0]["name"]
    spotify_id = track["id"]
    duration_seconds = (track.get("duration_ms") or 0) / 1000 or None

    log.info("Processing: %s - %s", artist, title)

    if duration_seconds and duration_seconds > slskd_client.MAX_LENGTH_SECONDS:
        # A DJ set or live recording (common among SoundCloud likes), not a track.
        log.warning("Skipping, %d min long: %s - %s", duration_seconds // 60, artist, title)
        state.mark_processed(spotify_id, title, artist, "skipped_long")
        return

    if config.SKIP_DUPLICATES:
        existing = duplicates.find(artist, title)
        if existing:
            log.info("Already in your collection, not downloading: %s", existing)
            state.mark_processed(spotify_id, title, artist, "duplicate")
            return

    queries = search_queries(artist, title, track.get("artist_is_uploader", False))
    ranked = []
    for query in queries:
        slskd_client.wait_until_ready()
        ranked = slskd_client.rank_files(slskd_client.search(query), duration_seconds, title)
        if ranked:
            break

    if not ranked:
        log.warning("No valid results on Soulseek for: %s", " | ".join(queries))
        state.mark_processed(spotify_id, title, artist, "not_found")
        return

    downloaded = _download_first(slskd_client.top_candidates(ranked, MAX_CANDIDATES))
    if not downloaded:
        state.mark_processed(spotify_id, title, artist, "download_failed")
        return
    file_info, started_at = downloaded

    downloaded_path = slskd_client.find_downloaded_file(file_info["filename"], started_at)
    if not downloaded_path:
        log.warning("Downloaded file not found under %s", config.DOWNLOAD_DIR)
        state.mark_processed(spotify_id, title, artist, "file_not_found")
        return

    final_path = organizer.tidy_download(downloaded_path, artist, title)

    log.info("Downloaded -> %s", final_path)
    # Record it before copying so a failed copy doesn't cause a re-download.
    state.mark_processed(spotify_id, title, artist, "ok")

    if config.COPY_TO_DIR:
        folder = _genre_folder(final_path, artist, title)
        target_dir = config.COPY_TO_DIR / folder if folder else config.COPY_TO_DIR
        copy_path = organizer.copy_to(final_path, target_dir, artist, title)
        log.info("Copied -> %s", copy_path)
        duplicates.remember(copy_path, artist, title)
        if folder:
            state.mark_processed(spotify_id, title, artist, f"ok:{folder}")


def _genre_folder(path: Path, artist: str, title: str) -> str | None:
    """The genre subfolder of COPY_TO_DIR to file the track in, or None to
    leave it at the top for you to file by hand."""
    if not config.SORT_BY_GENRE or not Path(config.GENRES_CONFIG_PATH).is_file():
        return None
    try:
        result = genre.classify(path, artist, title)
    except Exception:
        log.exception("Genre classification failed for %s - %s", artist, title)
        return None

    why = "; ".join(result.evidence) or "no genre info"
    if not result.folder:
        top = ", ".join(f"{f} {s}" for f, s in list(result.scores.items())[:2]) or "-"
        log.info("Genre unclear (%s), left for you to file: %s", top, why)
        return None
    if not (config.COPY_TO_DIR / result.folder).is_dir():
        log.warning("Genre folder %s doesn't exist, left for you to file", result.folder)
        return None
    log.info("Genre: %s (%s)", result.folder, why)
    return result.folder


def _baseline(tracks: list[dict]) -> None:
    state.mark_many(
        [(t["id"], t["name"], t["artists"][0]["name"], "baseline") for t in tracks]
    )


def _is_soundcloud(track_id: str) -> bool:
    return track_id.startswith(soundcloud_client.ID_PREFIX)


def _new_likes(source: str, tracks: list[dict], first_run: bool, backfill: int) -> list[dict]:
    if not first_run:
        return tracks
    # First run of this source: without this, every like would be downloaded.
    to_process, to_skip = tracks[:backfill], tracks[backfill:]
    _baseline(to_skip)
    log.info(
        "First %s run: recorded %d existing like(s) without downloading them. "
        "From now on only new likes are downloaded.",
        source,
        len(to_skip),
    )
    return to_process


def _load(sp, track: dict) -> dict:
    """Full track data: SoundCloud likes lists and retries only carry the id."""
    if _is_soundcloud(track["id"]):
        return soundcloud_client.get_track(track["id"])
    if "duration_ms" not in track:
        return sp.track(track["id"])
    return track


def _with_retries(what: str, fn, attempts: int = 6, delay: int = 60):
    """Right after logon the network may not be up yet: retry for a few minutes."""
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except Exception as e:
            if attempt == attempts:
                raise
            log.warning("Failed %s (%s), retrying in %ds", what, e, delay)
            time.sleep(delay)


def main() -> None:
    parser = argparse.ArgumentParser(description="Download new Spotify/SoundCloud likes via Soulseek.")
    parser.add_argument(
        "--backfill",
        type=int,
        default=0,
        metavar="N",
        help="first run of each source only: also download your N most recent likes "
        "(by default the first run just records existing likes without downloading)",
    )
    args = parser.parse_args()

    config.DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

    sp = spotify_client.get_client()
    seen_ids = state.get_seen_ids()

    spotify_first_run = not any(not _is_soundcloud(i) for i in seen_ids)
    new_tracks = _new_likes(
        "Spotify",
        _with_retries(
            "reading Spotify likes",
            lambda: list(spotify_client.iter_new_liked_tracks(sp, seen_ids)),
        ),
        spotify_first_run,
        args.backfill,
    )
    first_runs = [spotify_first_run]

    if config.SOUNDCLOUD_USER:
        soundcloud_first_run = not any(_is_soundcloud(i) for i in seen_ids)
        try:
            soundcloud_tracks = _with_retries(
                "reading SoundCloud likes",
                lambda: list(soundcloud_client.iter_new_liked_tracks(seen_ids)),
            )
        except Exception:
            log.exception("Couldn't read SoundCloud likes of '%s'", config.SOUNDCLOUD_USER)
        else:
            new_tracks += _new_likes(
                "SoundCloud", soundcloud_tracks, soundcloud_first_run, args.backfill
            )
            first_runs.append(soundcloud_first_run)

    if args.backfill and not any(first_runs):
        log.warning("--backfill only applies to the first run; ignoring it.")

    new_ids = {t["id"] for t in new_tracks}
    retry_ids = [i for i in state.get_retryable_ids(config.MAX_ATTEMPTS) if i not in new_ids]

    if not new_tracks and not retry_ids:
        log.info("No new tracks.")
        return

    log.info("%d new track(s), %d to retry.", len(new_tracks), len(retry_ids))
    retries = ({"id": i} for i in retry_ids)

    for track in itertools.chain(reversed(new_tracks), retries):  # new: oldest first
        name = track.get("name") or track["id"]
        for attempt in (1, 2):
            try:
                process_track(_load(sp, track))
            except slskd_client.SlskdError as e:
                # Not the track's fault: stop here and leave the rest for the next run.
                log.error("Stopping: %s", e)
                return
            except Exception as e:
                if attempt == 1 and _is_network_error(e):
                    log.warning("Network error on '%s' (%s), retrying in %ds", name, e, NETWORK_RETRY_DELAY)
                    time.sleep(NETWORK_RETRY_DELAY)
                    continue
                log.exception("Error processing '%s'", name)
            break


def sync_rekordbox() -> None:
    """Brings the rekordbox collection and genre playlists in line with the
    genre folders, if enabled and rekordbox is closed (see src/rekordbox.py)."""
    if not (config.REKORDBOX_SYNC and config.COPY_TO_DIR):
        return
    from . import rekordbox  # needs pyrekordbox, only imported when enabled

    try:
        rekordbox.sync(list(genre.load_folders()))
    except Exception:
        log.exception("rekordbox sync failed; the collection wasn't changed")


if __name__ == "__main__":
    try:
        main()
        # Also after runs with nothing new: it picks up tracks you filed by hand.
        sync_rekordbox()
    except Exception:
        # The scheduled task has no visible console: make sure it's in the log.
        log.exception("Run failed")
        raise
