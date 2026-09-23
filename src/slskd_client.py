"""Minimal client for the slskd HTTP API (https://github.com/slskd/slskd).

Checked against slskd 0.26.0. If you upgrade slskd and something breaks,
compare against the Swagger UI of your instance
(http://localhost:5030/swagger, needs `feature.swagger: true`).
"""
import time
from pathlib import Path, PureWindowsPath
from urllib.parse import quote

import requests

from . import config

# Spotify often only has the radio edit while Soulseek has the original or
# extended mix, so longer files are fine. Shorter ones are a different edit,
# and anything past the cap is usually a full DJ mix.
DURATION_TOLERANCE_SECONDS = 5
MAX_LENGTH_SECONDS = 15 * 60
PREFERRED_VERSIONS = ("extended mix", "original mix", "extended")


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


def wait_until_ready(timeout: int = 180) -> None:
    """Blocks until slskd is logged in to the Soulseek server. Right after
    Windows logon slskd may still be starting up or connecting."""
    deadline = time.time() + timeout
    last_state = "unreachable"
    while time.time() < deadline:
        try:
            last_state = _get("/application")["server"]["state"]
            if "LoggedIn" in last_state:
                return
        except requests.RequestException:
            last_state = "unreachable"
        time.sleep(5)
    raise SlskdError(f"slskd is not logged in to Soulseek (last state: {last_state})")


def search(query: str, timeout: int | None = None) -> list[dict]:
    timeout = timeout or config.SEARCH_TIMEOUT_SECONDS

    resp = requests.post(
        f"{_base()}/api/v0/searches",
        headers=_headers(),
        json={"searchText": query},
        timeout=15,
    )
    resp.raise_for_status()
    search_id = resp.json()["id"]

    deadline = time.time() + timeout
    while time.time() < deadline:
        if _get(f"/searches/{search_id}").get("isComplete"):
            break
        time.sleep(1)

    return _get(f"/searches/{search_id}/responses")


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


def pick_best_file(responses: list[dict], duration_seconds: float | None = None):
    """Picks the best file across all search responses. Prefers users with a
    free upload slot (so the download starts right away), then extended or
    original mixes, then files whose length could be checked, then the
    preferred format, then bitrate and a short queue. Files shorter than the
    Spotify duration (a different edit) or with a known bitrate below
    MIN_BITRATE are discarded."""
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
            score = (
                bool(response.get("hasFreeUploadSlot")),
                any(v in PureWindowsPath(filename).name for v in PREFERRED_VERSIONS),
                bool(file_info.get("length")),  # duration verified
                _format_rank(filename),
                bitrate,
                -(response.get("queueLength") or 0),
                response.get("uploadSpeed") or 0,
            )
            candidates.append((score, username, file_info))

    if not candidates:
        return None, None

    candidates.sort(key=lambda c: c[0], reverse=True)
    _, username, file_info = candidates[0]
    return username, file_info


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
    such as "Completed, Succeeded" or "Completed, Errored"."""
    timeout = timeout or config.DOWNLOAD_TIMEOUT_SECONDS
    deadline = time.time() + timeout
    state = "not found"

    while time.time() < deadline:
        transfer = _find_transfer(username, filename, transfer_id)
        if transfer:
            state = transfer.get("state", "")
            if "Succeeded" in state:
                return True, state
            if "Completed" in state:
                return False, state
        time.sleep(2)

    return False, f"timed out ({state})"


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
