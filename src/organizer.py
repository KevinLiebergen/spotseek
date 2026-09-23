import re
import shutil
from pathlib import Path

from . import config

_INVALID_CHARS = re.compile(r'[<>:"/\\|?*]')


def _sanitize(name: str) -> str:
    return _INVALID_CHARS.sub("_", name).strip()


def move_to_genre_folder(downloaded_path: Path, genre_folder: str, artist: str, title: str) -> Path:
    target_dir = config.REKORDBOX_ROOT / genre_folder
    target_dir.mkdir(parents=True, exist_ok=True)

    target_name = _sanitize(f"{artist} - {title}{downloaded_path.suffix}")
    target_path = target_dir / target_name

    shutil.move(str(downloaded_path), str(target_path))
    return target_path
