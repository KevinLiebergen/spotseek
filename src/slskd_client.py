"""Minimal client for the slskd HTTP API (https://github.com/slskd/slskd).

Checked against slskd 0.26.0. If you upgrade slskd and something breaks,
compare against the Swagger UI of your instance
(http://localhost:5030/swagger, needs `feature.swagger: true`).
"""
import logging
import os
import re
import subprocess
import time
from pathlib import Path, PureWindowsPath
from urllib.parse import quote

import requests

from . import config

log = logging.getLogger("spotseek")

# Spotify often only has the radio edit while Soulseek has the original or
# extended mix, so longer files are fine. Shorter ones are a different edit,
# and anything past the cap is usually a full DJ mix.
DURATION_TOLERANCE_SECONDS = 5
MAX_LENGTH_SECONDS = 15 * 60
PREFERRED_VERSIONS = ("extended mix", "original mix", "extended")
# Words naming a different version of a track (a remix of it, an edit...).
_VERSION_TAGS = re.compile(
    r"\b(remix|rmx|bootleg|edit|rework|flip|vip|dub|mashup|acapella|instrumental)\b",
    re.IGNORECASE,
)
_NOT_REMIXER_WORDS = {
    "remix", "rmx", "bootleg", "edit", "rework", "flip", "vip", "dub", "mashup",
    "acapella", "instrumental", "mix", "extended", "original", "radio", "club",
    "the", "and", "feat", "ft",
}
# "- Radio Edit", "(Original Mix)"...: the same track as the original, just
# shorter or longer, so they don't count as a different version.
VERSION_SUFFIX = re.compile(
    r"\s*(-\s*|[(\[]\s*)(radio edit|radio mix|edit|original mix|extended mix|extended)\s*[)\]]?\s*$",
    re.IGNORECASE,
)


class SlskdError(RuntimeError):
    pass


def _headers() -> dict:
    return {"X-API-Key": config.SLSKD_API_KEY}


def _base() -> str:
    return config.SLSKD_URL.rstrip("/")


def _get(path: str):
    resp = requests.get(f"{_base()}/api/v0{path}", headers=_headers(), timeout=15)
    resp.raise_for_status()
    return resp.json()


_restarted = False


def _wait_logged_in(timeout: int) -> tuple[bool, str]:
    deadline = time.time() + timeout
    state = "unreachable"
    while True:
        try:
            state = _get("/application")["server"]["state"]
            if "LoggedIn" in state:
                return True, state
        except requests.RequestException:
            state = "unreachable"
        if time.time() >= deadline:
            return False, state
        time.sleep(5)


def restart() -> None:
    """Kills slskd and starts it again through its scheduled task."""
    subprocess.run(["taskkill", "/IM", "slskd.exe", "/F"], capture_output=True)
    time.sleep(3)
    subprocess.run(
        ["schtasks", "/Run", "/TN", config.SLSKD_RESTART_TASK], capture_output=True, check=True
    )


def wait_until_ready(timeout: int = 180) -> None:
    """Blocks until slskd is logged in to the Soulseek server. Right after
    Windows logon slskd may still be starting up or connecting. If it
    doesn't get there (e.g. stuck in "Disconnecting" after the PC slept),
    restarts it once per run through its scheduled task."""
    global _restarted
    can_restart = bool(config.SLSKD_RESTART_TASK) and os.name == "nt" and not _restarted
    ready, state = _wait_logged_in(90 if can_restart else timeout)
    if ready:
        return
    if can_restart:
        _restarted = True
        log.warning("slskd not logged in (%s): restarting it", state)
        try:
            restart()
        except (OSError, subprocess.CalledProcessError) as e:
            raise SlskdError(f"slskd is not logged in ({state}) and couldn't be restarted: {e}")
        ready, state = _wait_logged_in(timeout)
        if ready:
            log.info("slskd is back after the restart")
            return
    raise SlskdError(f"slskd is not logged in to Soulseek (last state: {state})")


def search(query: str, timeout: int | None = None) -> list[dict]:
    timeout = timeout or config.SEARCH_TIMEOUT_SECONDS

    resp = requests.post(
        f"{_base()}/api/v0/searches",
        headers=_headers(),
        json={"searchText": query},
        timeout=15,
    )
    if resp.status_code == 409:
        # slskd lost its Soulseek connection (e.g. after the PC slept).
        raise SlskdError(f"slskd can't search right now: {resp.text}")
    resp.raise_for_status()
    search_id = resp.json()["id"]

    if not _wait_for_search(search_id, timeout):
        # Popular tracks keep getting responses past the timeout, and slskd
        # only returns them once the search is complete: stop it first.
        requests.put(f"{_base()}/api/v0/searches/{search_id}", headers=_headers(), timeout=15)
        _wait_for_search(search_id, 15)

    return _get(f"/searches/{search_id}/responses")


def _wait_for_search(search_id: str, timeout: int) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _get(f"/searches/{search_id}").get("isComplete"):
            return True
        time.sleep(1)
    return False


def _format_rank(filename: str) -> int:
    for rank, fmt in enumerate(config.PREFERRED_FORMATS):
        if filename.endswith(f".{fmt}"):
            return len(config.PREFERRED_FORMATS) - rank
    return -1


def _duration_matches(file_info: dict, duration_seconds: float | None) -> bool:
    length = file_info.get("length")
    if not length:
        return True
    if length > MAX_LENGTH_SECONDS:
        return False
    return not duration_seconds or length >= duration_seconds - DURATION_TOLERANCE_SECONDS


def _bitrate(file_info: dict) -> int:
    """kbps as reported, or estimated from size and length when missing (0 if unknown)."""
    if file_info.get("bitRate"):
        return file_info["bitRate"]
    size, length = file_info.get("size"), file_info.get("length")
    if size and length:
        return int(size * 8 / length / 1000)
    return 0


def _remixer_words(title: str) -> set[str]:
    """Words naming who made the version: "Toth" in "(Toth Edit)", "tony
    romera" in "- Tony Romera Remix"."""
    segments = re.findall(r"[(\[]([^)\]]*)[)\]]", title)
    if " - " in title:
        segments.append(title.split(" - ", 1)[1])
    words = set()
    for segment in segments:
        if _VERSION_TAGS.search(segment):
            words |= set(re.findall(r"\w+", segment.lower())) - _NOT_REMIXER_WORDS
    return words


def _is_other_version(file_name: str, title: str) -> bool:
    """True when the file looks like a different version than the track:
    "Wings [Krakota Remix]" for "Wings", or "La Linea (Original Mix)" for
    "La Linea (Toth Edit)". Plain radio edits count as the original."""
    title = VERSION_SUFFIX.sub("", title)
    file_tags = {t.lower() for t in _VERSION_TAGS.findall(file_name)}
    remixers = _remixer_words(title)
    if remixers:
        return not remixers <= set(re.findall(r"\w+", file_name.lower()))
    title_tags = {t.lower() for t in _VERSION_TAGS.findall(title)}
    if title_tags:
        return not title_tags <= file_tags
    return bool(file_tags)


def rank_files(
    responses: list[dict], duration_seconds: float | None = None, title: str = ""
) -> list[tuple[str, dict]]:
    """(username, file) of every acceptable file across all search
    responses, best first. Prefers users with a
    free upload slot (so the download starts right away), then extended or
    original mixes, then files whose length could be checked, then the
    preferred format, then bitrate and a short queue. Discards files that
    look like a different version of the track (a remix the title doesn't
    mention, or the original when the title is a remix), files shorter than
    the track (a different edit), and files with a known bitrate below
    MIN_BITRATE."""
    candidates = []
    for response in responses:
        username = response.get("username")
        for file_info in response.get("files", []):
            if file_info.get("isLocked"):
                continue
            filename = file_info.get("filename", "").lower()
            bitrate = _bitrate(file_info)
            if bitrate and bitrate < config.MIN_BITRATE and not filename.endswith(".flac"):
                continue
            if not _duration_matches(file_info, duration_seconds):
                continue
            file_name = PureWindowsPath(filename).name
            if _is_other_version(file_name, title):
                continue
            score = (
                bool(response.get("hasFreeUploadSlot")),
                any(v in file_name for v in PREFERRED_VERSIONS),
                bool(file_info.get("length")),  # duration verified
                _format_rank(filename),
                bitrate,
                -(response.get("queueLength") or 0),
                response.get("uploadSpeed") or 0,
            )
            candidates.append((score, username, file_info))

    candidates.sort(key=lambda c: c[0], reverse=True)
    return [(username, file_info) for _, username, file_info in candidates]


def pick_best_file(responses: list[dict], duration_seconds: float | None = None, title: str = ""):
    """(username, file) of the best candidate, or (None, None)."""
    ranked = rank_files(responses, duration_seconds, title)
    return ranked[0] if ranked else (None, None)


def top_candidates(ranked: list[tuple[str, dict]], limit: int) -> list[tuple[str, dict]]:
    """The best file of each of the first `limit` distinct users: if one
    user fails, trying another file of theirs rarely helps."""
    picked, users = [], set()
    for username, file_info in ranked:
        if username not in users:
            users.add(username)
            picked.append((username, file_info))
            if len(picked) == limit:
                break
    return picked


def cancel_download(username: str, transfer_id: str | None) -> None:
    """Best effort: removes a queued or failed transfer, so a download we
    gave up on doesn't complete later on its own."""
    if not transfer_id:
        return
    try:
        requests.delete(
            f"{_base()}/api/v0/transfers/downloads/{quote(username, safe='')}/{transfer_id}",
            headers=_headers(),
            params={"remove": "true"},
            timeout=15,
        )
    except requests.RequestException:
        pass


def enqueue_download(username: str, file_info: dict) -> str | None:
    """Queues the download and returns the transfer id when slskd reports it."""
    payload = [{"filename": file_info["filename"], "size": file_info.get("size", 0)}]
    resp = requests.post(
        f"{_base()}/api/v0/transfers/downloads/{quote(username, safe='')}",
        headers=_headers(),
        json=payload,
        timeout=30,
    )
    resp.raise_for_status()
    try:
        body = resp.json()
    except ValueError:
        return None
    if isinstance(body, dict):
        for transfer in body.get("enqueued", []):
            if transfer.get("filename") == file_info["filename"]:
                return transfer.get("id")
    return None


def _find_transfer(username: str, filename: str, transfer_id: str | None) -> dict | None:
    user = quote(username, safe="")
    try:
        if transfer_id:
            return _get(f"/transfers/downloads/{user}/{transfer_id}")
        data = _get(f"/transfers/downloads/{user}")
    except requests.HTTPError as e:
        if e.response is not None and e.response.status_code == 404:
            return None  # not registered yet
        raise

    for user_entry in data if isinstance(data, list) else [data]:
        for directory in user_entry.get("directories", []):
            for transfer in directory.get("files", []):
                if transfer.get("filename") == filename:
                    return transfer
    return None


def wait_for_download(
    username: str, filename: str, transfer_id: str | None = None, timeout: int | None = None
) -> tuple[bool, str]:
    """Returns (succeeded, last state). slskd states are flag combinations
    such as "Completed, Succeeded" or "Completed, Errored". Gives up early
    when the other user has more than MAX_QUEUE_POSITION downloads ahead of
    ours: it would never start in time."""
    timeout = timeout or config.DOWNLOAD_TIMEOUT_SECONDS
    deadline = time.time() + timeout
    state = "not found"
    next_position_check = 0.0

    while time.time() < deadline:
        transfer = _find_transfer(username, filename, transfer_id)
        if transfer:
            state = transfer.get("state", "")
            if "Succeeded" in state:
                return True, state
            if "Completed" in state:
                return False, state
            if "Queued, Remotely" in state and time.time() >= next_position_check:
                next_position_check = time.time() + QUEUE_CHECK_INTERVAL
                position = queue_position(username, transfer_id or transfer.get("id"))
                if position is not None and position > config.MAX_QUEUE_POSITION:
                    return False, f"queued at position {position}"
        time.sleep(2)

    return False, f"timed out ({state})"


QUEUE_CHECK_INTERVAL = 30  # seconds between queue position checks


def queue_position(username: str, transfer_id: str | None) -> int | None:
    """Our place in the other user's upload queue, or None if unknown."""
    if not transfer_id:
        return None
    try:
        resp = requests.get(
            f"{_base()}/api/v0/transfers/downloads/{quote(username, safe='')}/{transfer_id}/position",
            headers=_headers(),
            timeout=30,  # slskd asks the other user, which can take a while
        )
        resp.raise_for_status()
        return int(resp.json())
    except (requests.RequestException, ValueError, TypeError):
        return None


def find_downloaded_file(filename: str, started_at: float) -> Path | None:
    """Locates the finished file inside slskd's downloads folder. slskd puts
    it in a subfolder named after the remote folder, and may append a suffix
    to the name if a file with that name already exists."""
    remote_name = PureWindowsPath(filename).name
    stem, suffix = Path(remote_name).stem, Path(remote_name).suffix

    matches = [
        p
        for p in config.DOWNLOAD_DIR.rglob(f"*{suffix}")
        if p.name.startswith(stem) and p.stat().st_mtime >= started_at - 5
    ]
    return max(matches, key=lambda p: p.stat().st_mtime, default=None)
