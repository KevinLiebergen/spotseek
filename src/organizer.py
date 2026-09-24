import re
import shutil
from pathlib import Path

from . import config

_INVALID_CHARS = re.compile(r'[<>:"/\\|?*]')


def _sanitize(name: str) -> str:
    return _INVALID_CHARS.sub("_", name).strip()


def _unique_path(target_dir: Path, artist: str, title: str, suffix: str) -> Path:
    target_path = target_dir / _sanitize(f"{artist} - {title}{suffix}")
    counter = 2
    while target_path.exists():
        target_path = target_dir / _sanitize(f"{artist} - {title} ({counter}){suffix}")
        counter += 1
    return target_path


def tidy_download(downloaded_path: Path, artist: str, title: str) -> Path:
    """Moves the file from slskd's per-folder subdirectory to the top of
    DOWNLOAD_DIR as "Artist - Title.ext", so new tracks are easy to review
    and file by hand."""
    target_dir = config.DOWNLOAD_DIR
    target_path = _unique_path(target_dir, artist, title, downloaded_path.suffix)

    shutil.move(str(downloaded_path), str(target_path))

    # slskd creates one subfolder per download; drop it once it's empty.
    if downloaded_path.parent != target_dir:
        try:
            downloaded_path.parent.rmdir()
        except OSError:
            pass

    return target_path


def copy_to(path: Path, target_dir: Path, artist: str, title: str) -> Path:
    target_dir.mkdir(parents=True, exist_ok=True)
    target_path = _unique_path(target_dir, artist, title, path.suffix)
    shutil.copy2(str(path), str(target_path))
    return target_path
