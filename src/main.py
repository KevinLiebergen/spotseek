import argparse
import logging
import sys
import time
from pathlib import Path

from . import config, organizer, slskd_client, spotify_client, state

LOG_DIR = Path(config.STATE_DB_PATH).parent
LOG_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(LOG_DIR / "spotseek.log", encoding="utf-8"),
    ],
)
log = logging.getLogger("spotseek")


def process_track(track: dict) -> None:
    title = track["name"]
    artist = track["artists"][0]["name"]
    spotify_id = track["id"]
    duration_seconds = (track.get("duration_ms") or 0) / 1000 or None

    log.info("New like: %s - %s", artist, title)

    query = f"{artist} {title}"
    responses = slskd_client.search(query)
    username, file_info = slskd_client.pick_best_file(responses, duration_seconds)

    if not file_info:
        log.warning("No valid results on Soulseek for: %s", query)
        state.mark_processed(spotify_id, title, artist, "not_found")
        return

    started_at = time.time()
    transfer_id = slskd_client.enqueue_download(username, file_info)
    ok, transfer_state = slskd_client.wait_for_download(
        username, file_info["filename"], transfer_id
    )

    if not ok:
        log.warning("Download failed (%s): %s", transfer_state, file_info["filename"])
        state.mark_processed(spotify_id, title, artist, "download_failed")
        return

    downloaded_path = slskd_client.find_downloaded_file(file_info["filename"], started_at)
    if not downloaded_path:
        log.warning("Downloaded file not found under %s", config.DOWNLOAD_DIR)
        state.mark_processed(spotify_id, title, artist, "file_not_found")
        return

    final_path = organizer.tidy_download(downloaded_path, artist, title)

    log.info("Downloaded -> %s", final_path)
    state.mark_processed(spotify_id, title, artist, "ok")


def _baseline(tracks: list[dict]) -> None:
    state.mark_many(
        [(t["id"], t["name"], t["artists"][0]["name"], "baseline") for t in tracks]
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Download new Spotify likes via Soulseek.")
    parser.add_argument(
        "--backfill",
        type=int,
        default=0,
        metavar="N",
        help="first run only: also download your N most recent likes "
        "(by default the first run just records existing likes without downloading)",
    )
    args = parser.parse_args()

    config.DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

    sp = spotify_client.get_client()
    seen_ids = state.get_seen_ids()
    new_tracks = list(spotify_client.iter_new_liked_tracks(sp, seen_ids))

    if not seen_ids:
        # First run: without this, the whole liked library would be downloaded.
        to_process, to_skip = new_tracks[: args.backfill], new_tracks[args.backfill :]
        _baseline(to_skip)
        log.info(
            "First run: recorded %d existing like(s) without downloading them. "
            "From now on only new likes are downloaded.",
            len(to_skip),
        )
        new_tracks = to_process
    elif args.backfill:
        log.warning("--backfill only applies to the first run; ignoring it.")

    if not new_tracks:
        log.info("No new tracks.")
        return

    log.info("%d new track(s) found.", len(new_tracks))
    slskd_client.wait_until_ready()

    for track in reversed(new_tracks):  # oldest to newest
        try:
            process_track(track)
        except Exception:
            log.exception("Error processing '%s'", track.get("name"))


if __name__ == "__main__":
    main()
