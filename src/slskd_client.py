"""Minimal client for the slskd HTTP API (https://github.com/slskd/slskd).

IMPORTANT: the field names and endpoints below match the slskd v0 API as
documented at the time this was written. slskd's API changes between
versions, so before relying on this module open the Swagger UI of your
instance (usually http://localhost:5030/swagger) and compare the
/api/v0/searches and /api/v0/transfers/downloads endpoints against what's
here. This part needs to be verified and adjusted on the Windows machine
with slskd actually running; it can't be tested from here.
"""
import time
from pathlib import Path

import requests

from . import config


class SlskdError(RuntimeError):
    pass


def _headers() -> dict:
    return {"X-API-Key": config.SLSKD_API_KEY}


def _base() -> str:
    return config.SLSKD_URL.rstrip("/")


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
        status_resp = requests.get(
            f"{_base()}/api/v0/searches/{search_id}", headers=_headers(), timeout=15
        )
        status_resp.raise_for_status()
        if status_resp.json().get("isComplete"):
            break
        time.sleep(1)

    results_resp = requests.get(
        f"{_base()}/api/v0/searches/{search_id}/responses", headers=_headers(), timeout=15
    )
    results_resp.raise_for_status()
    return results_resp.json()


def _score_file(file_info: dict) -> tuple:
    name = file_info.get("filename", "").lower()
    bitrate = file_info.get("bitRate") or 0
    for rank, fmt in enumerate(config.PREFERRED_FORMATS):
        if name.endswith(f".{fmt}"):
            return (len(config.PREFERRED_FORMATS) - rank, bitrate)
    return (-1, bitrate)


def pick_best_file(responses: list[dict]):
    candidates = []
    for response in responses:
        username = response.get("username")
        for file_info in response.get("files", []):
            filename = file_info.get("filename", "").lower()
            bitrate = file_info.get("bitRate") or 0
            if bitrate < config.MIN_BITRATE and not filename.endswith(".flac"):
                continue
            candidates.append((_score_file(file_info), username, file_info))

    if not candidates:
        return None, None

    candidates.sort(key=lambda c: c[0], reverse=True)
    _, username, file_info = candidates[0]
    return username, file_info


def enqueue_download(username: str, file_info: dict) -> None:
    payload = [{"filename": file_info["filename"], "size": file_info.get("size", 0)}]
    resp = requests.post(
        f"{_base()}/api/v0/transfers/downloads/{username}",
        headers=_headers(),
        json=payload,
        timeout=15,
    )
    resp.raise_for_status()


def wait_for_download(username: str, filename: str, timeout: int | None = None) -> bool:
    timeout = timeout or config.DOWNLOAD_TIMEOUT_SECONDS
    deadline = time.time() + timeout

    while time.time() < deadline:
        resp = requests.get(
            f"{_base()}/api/v0/transfers/downloads/{username}", headers=_headers(), timeout=15
        )
        resp.raise_for_status()
        for directory in resp.json():
            for file_info in directory.get("files", []):
                if file_info.get("filename") == filename:
                    state = file_info.get("state", "")
                    if "Completed" in state:
                        return True
                    if any(bad in state for bad in ("Failed", "Cancelled", "Errored")):
                        return False
        time.sleep(2)

    return False


def downloaded_file_path(filename: str) -> Path:
    return config.DOWNLOAD_DIR / Path(filename).name
