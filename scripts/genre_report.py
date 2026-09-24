"""Proposes a genre folder for every track in a folder, WITHOUT moving
anything, so you can check how accurate the classification is.

Usage:
    python scripts/genre_report.py [folder]
    python scripts/genre_report.py --evaluate [--proposals]

The folder defaults to DOWNLOAD_DIR. With --evaluate, every track already
filed in a genre subfolder of COPY_TO_DIR is classified and the proposal is
compared with the folder you put it in. --proposals also writes the tracks
it would move to data/propuestas.txt, for you to accept or reject.
"""
import sys
from collections import Counter
from pathlib import Path

import mutagen

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import config, genre  # noqa: E402

AUDIO_EXTENSIONS = {".mp3", ".flac", ".wav", ".aiff", ".aif", ".m4a"}


def artist_and_title(path: Path) -> tuple[str, str]:
    """From the tags if present, else from an "Artist - Title" file name."""
    try:
        audio = mutagen.File(path, easy=True)
    except Exception:
        audio = None
    if audio and audio.get("artist") and audio.get("title"):
        return audio["artist"][0], audio["title"][0]
    artist, _, title = path.stem.partition(" - ")
    return artist, title or artist


def audio_files(folder: Path) -> list[Path]:
    return sorted(p for p in folder.iterdir() if p.suffix.lower() in AUDIO_EXTENSIONS)


def report(folder: Path, folders: dict) -> None:
    files = audio_files(folder)
    print(f"{len(files)} tracks in {folder}\n")
    classified = 0
    for path in files:
        artist, title = artist_and_title(path)
        result = genre.classify(path, artist, title, folders)
        classified += bool(result.folder)
        top = ", ".join(f"{f} {s}" for f, s in list(result.scores.items())[:3]) or "-"
        print(f"{result.folder or '?':28} | {artist} - {title}"[:110])
        print(f"{'':28} |   scores: {top}")
        print(f"{'':28} |   why: {'; '.join(result.evidence) or 'no genre info'}")
    print(f"\n{classified}/{len(files)} would be moved; the rest stay for you to file by hand.")


PROPOSALS_PATH = Path(config.STATE_DB_PATH).parent / "propuestas.txt"
PROPOSALS_HEADER = """\
# Canciones de tu coleccion que el clasificador pondria en otra carpeta.
#
# Escribe SI al principio de la linea para moverla a la carpeta propuesta,
# o NO para dejarla donde esta. Las lineas con ? no se tocan.
# Guarda el archivo y avisa: solo se mueven las lineas con SI.
#
# decision | tu carpeta -> propuesta | cancion | por que | archivo
"""


def evaluate(folders: dict, write_proposals: bool = False) -> None:
    right, wrong, unsure = Counter(), Counter(), Counter()
    mistakes = []
    proposals = []
    for actual in folders:
        subfolder = Path(config.COPY_TO_DIR) / actual
        if not subfolder.is_dir():
            continue
        for path in audio_files(subfolder):
            artist, title = artist_and_title(path)
            result = genre.classify(path, artist, title, folders)
            proposed = result.folder
            if proposed is None:
                unsure[actual] += 1
            elif proposed == actual:
                right[actual] += 1
            else:
                wrong[actual] += 1
                mistakes.append((actual, proposed, f"{artist} - {title}"))
                why = "; ".join(e for e in result.evidence if not e.startswith("library"))
                proposals.append((proposed, actual, f"{artist} - {title}", why, path))

    print(f"{'folder':28} {'right':>5} {'wrong':>5} {'unsure':>6}")
    for actual in folders:
        if right[actual] + wrong[actual] + unsure[actual]:
            print(f"{actual:28} {right[actual]:5} {wrong[actual]:5} {unsure[actual]:6}")
    r, w, u = sum(right.values()), sum(wrong.values()), sum(unsure.values())
    print(f"\n{r + w + u} tracks: {r} right, {w} wrong, {u} left unclassified")
    if r + w:
        print(f"precision when it does move a track: {r / (r + w):.0%}")
    print("\nwrong (your folder -> proposed):")
    for actual, proposed, name in mistakes:
        print(f"  {actual} -> {proposed} | {name}"[:120])

    if write_proposals:
        lines = [PROPOSALS_HEADER]
        for proposed, actual, name, why, path in sorted(proposals, key=lambda p: (p[0], p[1], p[2])):
            relative = path.relative_to(config.COPY_TO_DIR)
            name, why = name.replace("|", "/"), why.replace("|", "/")
            lines.append(f"?  | {actual} -> {proposed} | {name} | {why} | {relative}\n")
        PROPOSALS_PATH.write_text("".join(lines), encoding="utf-8-sig")
        print(f"\n{len(proposals)} proposals written to {PROPOSALS_PATH}")


def main() -> None:
    folders = genre.load_folders()
    sources = "tags, Discogs" + ("" if config.DISCOGS_TOKEN else " (no token, slower)")
    sources += ", Last.fm" if config.LASTFM_API_KEY else ""
    print(f"sources: {sources}")
    if sys.argv[1:2] == ["--evaluate"]:
        evaluate(folders, write_proposals="--proposals" in sys.argv)
    else:
        report(Path(sys.argv[1]) if len(sys.argv) > 1 else config.DOWNLOAD_DIR, folders)


if __name__ == "__main__":
    main()
