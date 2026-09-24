"""Keeps the rekordbox collection in step with the genre folders of
COPY_TO_DIR, through rekordbox's own database (pyrekordbox):

- relocates tracks whose file moved within COPY_TO_DIR (found by file name,
  only when the name is unique), keeping their cues and analysis;
- gives every genre folder a playlist with the same name inside the
  REKORDBOX_PLAYLIST_FOLDER playlist folder. An old playlist whose name
  matches no folder is renamed after the folder most of its tracks are in
  now; missing ones are created;
- puts each track in the playlist of the folder its file is in, and takes
  it out of the other genre playlists;
- adds the files of the genre folders that aren't in the collection yet.
  rekordbox still has to analyze them: select the playlist and Analyze.

rekordbox must be closed: the database is only written when it isn't
running, after saving a backup of master.db in data/rekordbox-backups.
"""
import logging
import shutil
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import mutagen

from . import config

log = logging.getLogger("spotseek")

AUDIO_EXTENSIONS = {".mp3", ".flac", ".wav", ".aiff", ".aif", ".m4a"}
BACKUP_DIR = Path(config.STATE_DB_PATH).parent / "rekordbox-backups"
BACKUPS_KEPT = 10


@dataclass
class SyncPlan:
    relocate: list[tuple[object, Path]] = field(default_factory=list)  # (content, new path)
    ambiguous: list[str] = field(default_factory=list)
    rename: list[tuple[object, str]] = field(default_factory=list)  # (playlist, new name)
    create: list[str] = field(default_factory=list)
    add_to_playlist: list[tuple[str, object]] = field(default_factory=list)  # (folder, content)
    remove_from_playlist: list[tuple[object, object]] = field(default_factory=list)  # (playlist, song)
    new_tracks: list[tuple[str, Path]] = field(default_factory=list)  # (folder, file)

    def summary(self) -> str:
        return (
            f"relocate {len(self.relocate)} (ambiguous {len(self.ambiguous)}), "
            f"rename {len(self.rename)} playlists, create {len(self.create)}, "
            f"add {len(self.add_to_playlist)} to playlists, remove {len(self.remove_from_playlist)}, "
            f"new tracks {len(self.new_tracks)}"
        )


def is_running() -> bool:
    from pyrekordbox.utils import get_rekordbox_pid

    return bool(get_rekordbox_pid())


def open_db(path: str | None = None):
    from pyrekordbox import Rekordbox6Database

    return Rekordbox6Database(path=path) if path else Rekordbox6Database()


def _norm(path: str | Path) -> str:
    return str(path).replace("\\", "/").lower()


def _genre_folder_of(path: Path, root: Path, genre_folders: set[str]) -> str | None:
    try:
        parts = path.relative_to(root).parts
    except ValueError:
        return None
    return parts[0] if len(parts) > 1 and parts[0] in genre_folders else None


def plan_sync(db, genre_folders: list[str]) -> SyncPlan:
    root = Path(config.COPY_TO_DIR)
    folders = set(genre_folders)
    plan = SyncPlan()

    files_by_name: dict[str, list[Path]] = defaultdict(list)
    for p in root.rglob("*"):
        if p.suffix.lower() in AUDIO_EXTENSIONS and p.is_file():
            files_by_name[p.name.lower()].append(p)

    # 1. Relocate tracks whose file is gone but exists, uniquely named, in root.
    contents = list(db.get_content())
    current_path = {}  # content ID -> Path it will have
    for c in contents:
        path = c.FolderPath or ""
        if path.startswith("spotify:") or not path:
            continue  # streaming entries, not files
        if Path(path).is_file():
            current_path[c.ID] = Path(path)
            continue
        matches = files_by_name.get((c.FileNameL or Path(path).name).lower(), [])
        if len(matches) == 1:
            plan.relocate.append((c, matches[0]))
            current_path[c.ID] = matches[0]
        elif len(matches) > 1:
            plan.ambiguous.append(f"{c.FileNameL}: {len(matches)} files with that name")

    # 2. One playlist per genre folder inside the playlist folder.
    parent = db.get_playlist(Name=config.REKORDBOX_PLAYLIST_FOLDER, Attribute=1).first()
    if parent is None:
        raise RuntimeError(f"No playlist folder named {config.REKORDBOX_PLAYLIST_FOLDER!r} in rekordbox")
    playlists = [p for p in db.get_playlist() if p.ParentID == parent.ID and p.Attribute == 0]
    by_name = {p.Name: p for p in playlists}

    claimed = set(by_name) & folders
    unmatched = sorted(
        (p for p in playlists if p.Name not in folders), key=lambda p: len(p.Songs), reverse=True
    )
    for p in unmatched:
        where = Counter(
            _genre_folder_of(current_path[s.ContentID], root, folders)
            for s in p.Songs
            if s.ContentID in current_path
        )
        where.pop(None, None)
        if not where:
            continue
        folder, count = where.most_common(1)[0]
        if folder not in claimed and count / max(1, len(p.Songs)) >= 0.5:
            plan.rename.append((p, folder))
            claimed.add(folder)
            by_name[folder] = p
    plan.create = [f for f in genre_folders if f not in by_name]

    # 3. Membership: each track in the playlist of its folder, only there.
    known_paths = {_norm(p): cid for cid, p in current_path.items()}
    for folder in genre_folders:
        playlist = by_name.get(folder)
        in_playlist = {s.ContentID for s in playlist.Songs} if playlist else set()
        for cid, path in current_path.items():
            if _genre_folder_of(path, root, folders) == folder and cid not in in_playlist:
                plan.add_to_playlist.append((folder, next(c for c in contents if c.ID == cid)))
        if playlist:
            for song in playlist.Songs:
                actual = _genre_folder_of(current_path.get(song.ContentID, Path()), root, folders)
                if actual and actual != folder:
                    plan.remove_from_playlist.append((playlist, song))

    # 4. Files in genre folders that rekordbox doesn't know yet.
    for folder in genre_folders:
        for p in (root / folder).rglob("*"):
            if p.suffix.lower() in AUDIO_EXTENSIONS and p.is_file() and _norm(p) not in known_paths:
                plan.new_tracks.append((folder, p))
    return plan


def _tags(path: Path) -> tuple[str, str]:
    try:
        audio = mutagen.File(path, easy=True)
    except Exception:
        audio = None
    title = (audio.get("title") or [None])[0] if audio else None
    artist = (audio.get("artist") or [None])[0] if audio else None
    if not title:
        artist_part, _, title_part = path.stem.partition(" - ")
        title, artist = (title_part or path.stem), (artist or (artist_part if title_part else None))
    return title, artist or ""


def apply_sync(db, plan: SyncPlan, genre_folders: list[str]) -> None:
    parent = db.get_playlist(Name=config.REKORDBOX_PLAYLIST_FOLDER, Attribute=1).first()

    for content, new_path in plan.relocate:
        content.FolderPath = new_path.as_posix()
    for playlist, name in plan.rename:
        db.rename_playlist(playlist, name)
    for name in plan.create:
        db.create_playlist(name, parent=parent)
    db.flush()

    playlists = {
        p.Name: p for p in db.get_playlist() if p.ParentID == parent.ID and p.Attribute == 0
    }
    for playlist, song in plan.remove_from_playlist:
        db.remove_from_playlist(playlist, song)
    for folder, content in plan.add_to_playlist:
        db.add_to_playlist(playlists[folder], content)

    artists = {}
    for folder, path in plan.new_tracks:
        title, artist_name = _tags(path)
        kwargs = {"Title": title}
        if artist_name:
            artist = artists.get(artist_name) or db.get_artist(Name=artist_name).first()
            artist = artist or db.add_artist(artist_name)
            artists[artist_name] = artist
            kwargs["ArtistID"] = artist.ID
        content = db.add_content(path, **kwargs)
        content.FolderPath = path.as_posix()  # rekordbox stores forward slashes
        db.add_to_playlist(playlists[folder], content)


def backup(db_path: Path) -> Path:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    target = BACKUP_DIR / f"master-{datetime.now():%Y%m%d-%H%M%S}.db"
    shutil.copy2(db_path, target)
    for old in sorted(BACKUP_DIR.glob("master-*.db"))[:-BACKUPS_KEPT]:
        old.unlink()
    return target


def sync(genre_folders: list[str], db_path: str | None = None, dry_run: bool = False) -> SyncPlan | None:
    """Plans and (unless dry_run) applies the sync. Returns None when
    rekordbox is running, since its database can't be written then."""
    if not dry_run and db_path is None and is_running():
        log.info("rekordbox is open: its collection will be synced next time it's closed")
        return None
    db = open_db(db_path)
    try:
        plan = plan_sync(db, genre_folders)
        log.info("rekordbox sync: %s", plan.summary())
        if dry_run:
            return plan
        real_path = Path(db_path) if db_path else Path(db.db_directory) / "master.db"
        if db_path is None:
            log.info("rekordbox backup: %s", backup(real_path))
        apply_sync(db, plan, genre_folders)
        db.commit()
        return plan
    finally:
        db.close()
