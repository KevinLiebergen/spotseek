"""Moves the tracks you marked SI in data/propuestas.txt to their proposed
folder, and marks those lines HECHO. Lines with NO or ? are left alone.

Usage:
    python scripts/apply_proposals.py [--dry-run]

Every move is appended to data/moves-log.txt ("from -> to"), so it can be
undone. Nothing is overwritten: if the destination already has a file with
that name, the track is skipped.
"""
import shutil
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import config  # noqa: E402

PROPOSALS_PATH = Path(config.STATE_DB_PATH).parent / "propuestas.txt"
LOG_PATH = Path(config.STATE_DB_PATH).parent / "moves-log.txt"


def main() -> None:
    dry_run = "--dry-run" in sys.argv
    root = Path(config.COPY_TO_DIR)
    lines = PROPOSALS_PATH.read_text(encoding="utf-8-sig").splitlines(keepends=True)

    moved = skipped = 0
    log = []
    for i, line in enumerate(lines):
        parts = [p.strip() for p in line.split("|")]
        if line.startswith("#") or len(parts) < 5 or parts[0].upper() != "SI":
            continue
        proposed = parts[1].split("->")[1].strip()
        source = root / parts[-1]
        target = root / proposed / source.name

        if not source.is_file():
            print(f"skip (not found): {source}")
            skipped += 1
            continue
        if not (root / proposed).is_dir():
            print(f"skip (no folder {proposed}): {source.name}")
            skipped += 1
            continue
        if target.exists():
            print(f"skip (already in {proposed}): {source.name}")
            skipped += 1
            continue

        print(f"{'would move' if dry_run else 'move'}: {parts[2]}  ->  {proposed}")
        if not dry_run:
            shutil.move(str(source), str(target))
            log.append(f"{datetime.now():%Y-%m-%d %H:%M} | {source} -> {target}\n")
            new_relative = target.relative_to(root)
            lines[i] = "HECHO | " + " | ".join(parts[1:-1]) + f" | {new_relative}\n"
        moved += 1

    if not dry_run and moved:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.writelines(log)
        PROPOSALS_PATH.write_text("".join(lines), encoding="utf-8-sig")
    print(f"\n{moved} {'to move' if dry_run else 'moved'}, {skipped} skipped")


if __name__ == "__main__":
    main()
