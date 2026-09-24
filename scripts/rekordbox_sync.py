"""Syncs the rekordbox collection with the genre folders (see
src/rekordbox.py). rekordbox must be closed.

Usage:
    python scripts/rekordbox_sync.py [--dry-run] [--db PATH]

--dry-run only prints what would change. --db works on another copy of
master.db instead of rekordbox's own (no backup is made then).
"""
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import genre, rekordbox  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(message)s")
for noisy in ("pyrekordbox", "pyrekordbox.db6.database"):
    logging.getLogger(noisy).setLevel(logging.ERROR)


def main() -> None:
    args = sys.argv[1:]
    dry_run = "--dry-run" in args
    db_path = args[args.index("--db") + 1] if "--db" in args else None
    if not dry_run and db_path is None and rekordbox.is_running():
        sys.exit("rekordbox is open: close it and run this again.")

    plan = rekordbox.sync(list(genre.load_folders()), db_path=db_path, dry_run=dry_run)
    print(f"\n{'would' if dry_run else 'done'}: {plan.summary()}")
    for playlist, name in plan.rename:
        print(f"  rename playlist {playlist.Name!r} -> {name!r}")
    for name in plan.create:
        print(f"  create playlist {name!r}")
    for text in plan.ambiguous:
        print(f"  not relocated, {text}")
    moves = {}
    for folder, _ in plan.add_to_playlist:
        moves[folder] = moves.get(folder, 0) + 1
    for folder, n in sorted(moves.items()):
        print(f"  add {n:4} tracks to {folder}")
    new = {}
    for folder, _ in plan.new_tracks:
        new[folder] = new.get(folder, 0) + 1
    for folder, n in sorted(new.items()):
        print(f"  new  {n:4} tracks into the collection, in {folder}")


if __name__ == "__main__":
    main()
