"""Comment tags inside audio files.

Downloads often carry a comment saying where they came from ("Purchased at
Beatport.com", "https://djsoundtop.com", "For Promotional Use Only!"), which
rekordbox then shows in its Comments column.
"""
import logging
from pathlib import Path

import mutagen
from mutagen.id3 import ID3

log = logging.getLogger(__name__)

_VORBIS_KEYS = ("comment", "description")
_MP4_KEYS = ("©cmt",)


def _comment_keys(tags) -> list:
    if isinstance(tags, ID3):
        return [k for k in tags.keys() if k.startswith("COMM") or k.lower().startswith("txxx:comment")]
    return [k for k in tags.keys() if str(k).lower() in _VORBIS_KEYS or k in _MP4_KEYS]


def comments(path: Path) -> list[str]:
    """The comment texts in the file's tags."""
    audio = mutagen.File(path)
    if not audio or not audio.tags:
        return []
    texts = []
    for key in _comment_keys(audio.tags):
        value = audio.tags[key]
        value = getattr(value, "text", value)
        texts += [str(v) for v in (value if isinstance(value, list) else [value]) if str(v).strip()]
    return texts


def strip_comments(path: Path) -> list[str]:
    """Removes every comment tag from the file and returns the removed texts.
    Never raises: a file mutagen can't read or save is left as it is."""
    try:
        audio = mutagen.File(path)
        if not audio or not audio.tags:
            return []
        keys = _comment_keys(audio.tags)
        if not keys:
            return []
        removed = comments(path)
        for key in keys:
            del audio.tags[key]
        audio.save()
        return removed
    except Exception as e:  # noqa: BLE001
        log.warning("Couldn't remove the comments of %s: %s", path.name, e)
        return []
