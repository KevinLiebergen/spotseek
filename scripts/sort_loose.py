"""Files the tracks lying loose in COPY_TO_DIR into their genre subfolder,
when the classifier is confident. Unclear ones stay where they are.

Usage:
    python scripts/sort_loose.py [--dry-run]

Every move is appended to data/moves-log.txt ("from -> to").
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from genre_report import artist_and_title, audio_files  # noqa: E402

from src import config, genre, organizer  # noqa: E402


def main() -> None:
    dry_run = "--dry-run" in sys.argv
    root = Path(config.COPY_TO_DIR)
    folders = genre.load_folders()

    moved, unsure = [], []
    for path in audio_files(root):
        artist, title = artist_and_title(path)
        result = genre.classify(path, artist, title, folders)
        top = ", ".join(f"{f} {s}" for f, s in list(result.scores.items())[:2]) or "no genre info"
        if not result.folder:
            unsure.append(f"  ?  {artist} - {title}  [{top}]")
            continue
        if dry_run:
            moved.append(f"  {result.folder:30} {artist} - {title}  [{top}]")
        else:
            target = organizer.move_to_genre(path, result.folder)
            moved.append(f"  {result.folder:30} {target.name}")

    print(f"{'would move' if dry_run else 'moved'} ({len(moved)}):")
    print("\n".join(sorted(moved)))
    print(f"\nleft in {root} ({len(unsure)}):")
    print("\n".join(unsure))


if __name__ == "__main__":
    main()
