import logging
import sys
from pathlib import Path

from . import config, genre, organizer, slskd_client, spotify_client, state

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(Path(config.STATE_DB_PATH).parent / "spotseek.log", encoding="utf-8"),
    ],
)
log = logging.getLogger("spotseek")


def process_track(sp, track: dict) -> None:
    title = track["name"]
    artist = track["artists"][0]["name"]
    artist_id = track["artists"][0]["id"]
    spotify_id = track["id"]

    log.info("New like: %s - %s", artist, title)

    query = f"{artist} {title}"
    responses = slskd_client.search(query)
    username, file_info = slskd_client.pick_best_file(responses)

    if not file_info:
        log.warning("No valid results on Soulseek for: %s", query)
        state.mark_processed(spotify_id, title, artist, "not_found")
        return

    slskd_client.enqueue_download(username, file_info)
    ok = slskd_client.wait_for_download(username, file_info["filename"])

    if not ok:
        log.warning("Download failed or timed out: %s", file_info["filename"])
        state.mark_processed(spotify_id, title, artist, "download_failed")
        return

    downloaded_path = slskd_client.downloaded_file_path(file_info["filename"])
    if not downloaded_path.exists():
        log.warning("Downloaded file not found at %s", downloaded_path)
        state.mark_processed(spotify_id, title, artist, "file_not_found")
        return

    spotify_genres = spotify_client.get_artist_genres(sp, artist_id)
    genre_folder = genre.resolve_genre_folder(spotify_genres, artist, title)
    final_path = organizer.move_to_genre_folder(downloaded_path, genre_folder, artist, title)

    log.info("Classified as '%s' -> %s", genre_folder, final_path)
    state.mark_processed(spotify_id, title, artist, f"ok:{genre_folder}")


def main() -> None:
    config.DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

    sp = spotify_client.get_client()
    seen_ids = state.get_seen_ids()

    new_tracks = list(spotify_client.iter_new_liked_tracks(sp, seen_ids))
    if not new_tracks:
        log.info("No new tracks.")
        return

    log.info("%d new track(s) found.", len(new_tracks))
    for track in reversed(new_tracks):  # oldest to newest
        try:
            process_track(sp, track)
        except Exception:
            log.exception("Error processing '%s'", track.get("name"))


if __name__ == "__main__":
    main()
