import re
import shutil
from pathlib import Path

from . import config

_INVALID_CHARS = re.compile(r'[<>:"/\\|?*]')


def _sanitize(name: str) -> str:
    return _INVALID_CHARS.sub("_", name).strip()


def tidy_download(downloaded_path: Path, artist: str, title: str) -> Path:
    """Moves the file from slskd's per-folder subdirectory to the top of
    DOWNLOAD_DIR as "Artist - Title.ext", so new tracks are easy to review
    and file by hand."""
    target_dir = config.DOWNLOAD_DIR
    suffix = downloaded_path.suffix
    target_path = target_dir / _sanitize(f"{artist} - {title}{suffix}")
    counter = 2
    while target_path.exists():
        target_path = target_dir / _sanitize(f"{artist} - {title} ({counter}){suffix}")
        counter += 1

    shutil.move(str(downloaded_path), str(target_path))

    # slskd creates one subfolder per download; drop it once it's empty.
    if downloaded_path.parent != target_dir:
        try:
            downloaded_path.parent.rmdir()
        except OSError:
            pass

    return target_path
