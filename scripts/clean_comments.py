"""Removes the comments of the tracks already in COPY_TO_DIR: the comment
tags inside the files and the Comments column of rekordbox. [SAMPLER] is
left alone (its comments name the kit each sample comes from).

A comment that is an energy note ("LOW", "MID", "HIGH", "6 LOW", "10 - HIGH")
becomes the track's Energy My Tag in rekordbox, unless it already has one.

Usage:
    python scripts/clean_comments.py [--dry-run]

rekordbox must be closed; master.db is backed up first.
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import config, rekordbox, tags  # noqa: E402

SKIP_FOLDERS = {"[SAMPLER]"}
ENERGY_NOTE = re.compile(r"^\s*(?:\d+\s*-?\s*)?(low|mid|high)\s*$", re.IGNORECASE)


def _skipped(path: Path, root: Path) -> bool:
    parts = path.relative_to(root).parts
    return len(parts) > 1 and parts[0] in SKIP_FOLDERS


def main() -> None:
    dry_run = "--dry-run" in sys.argv
    root = Path(config.COPY_TO_DIR)
    if not dry_run and rekordbox.is_running():
        sys.exit("Close rekordbox first.")

    files = [
        p for p in root.rglob("*")
        if p.suffix.lower() in rekordbox.AUDIO_EXTENSIONS | {".ogg", ".opus"} and not _skipped(p, root)
    ]
    cleaned = 0
    for path in files:
        found = tags.comments(path) if dry_run else tags.strip_comments(path)
        if found:
            cleaned += 1
            print(f"  file  {path.relative_to(root)}: {' / '.join(found)}")

    db = rekordbox.open_db()
    try:
        if not dry_run:
            print("rekordbox backup:", rekordbox.backup(Path(db.db_directory) / "master.db"))
        root_posix = root.as_posix().lower() + "/"
        cleared, energy = 0, []
        for content in db.get_content():
            path = (content.FolderPath or "")
            comment = (content.Commnt or "").strip()
            if not comment or not path.lower().startswith(root_posix):
                continue
            if _skipped(Path(path), root):
                continue
            note = ENERGY_NOTE.match(comment)
            if note:
                level = note.group(1).capitalize()
                current = rekordbox.my_tag(db, content, "Energy")
                if current:
                    energy.append(f"  keeps Energy {current} (comment said {level}): {Path(path).name}")
                else:
                    energy.append(f"  Energy {level}: {Path(path).name}")
                    if not dry_run:
                        rekordbox.set_my_tag(db, content, "Energy", level)
            print(f"  rekordbox  {Path(path).name}: {comment}")
            if not dry_run:
                content.Commnt = ""
            cleared += 1
        if not dry_run:
            db.commit()
    finally:
        db.close()

    verb = "would remove" if dry_run else "removed"
    print(f"\n{verb} the comments of {cleaned} files and {cleared} rekordbox tracks")
    print(f"energy notes ({len(energy)}):")
    print("\n".join(energy))


if __name__ == "__main__":
    main()
