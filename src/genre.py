"""Guesses which collection folder (see config/genres.yaml) a downloaded
track belongs to, by combining several weak signals:

- the genre tag inside the file (often set by the store it was bought from),
  ignoring junk such as URLs or a list of every genre on a compilation;
- the styles Discogs gives for matching releases (DISCOGS_TOKEN recommended);
- Last.fm's top tags for the track, or for the artist when the track has
  none, which is usually the case (needs LASTFM_API_KEY); they also hint at
  the language ("spanish rap", "british");
- where you already filed other tracks by the same artist;
- the BPM tag, to pick between folders sharing a genre and to rule out
  folders whose tempo range is clearly off.

Each signal adds points to the folders its genre maps to. A folder is only
returned when it clearly beats the rest; otherwise the track is left for
you to file by hand.
"""
import json
import re
import time
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import mutagen
import requests
import yaml

from . import config, slskd_client

# Genres too broad to tell folders apart: they count half.
# "edm" too: Last.fm tags whole artists (Fred again.., Calvin Harris) with it.
GENERIC_GENRES = {"house", "electronic", "electro", "dance", "club", "pop", "latin", "edm"}

# Language hints, for folders split by language (spanish: true/false).
_SPANISH_COUNTRIES = {
    "spain", "mexico", "argentina", "colombia", "chile", "peru", "venezuela",
    "puerto rico", "cuba", "dominican republic", "uruguay", "ecuador", "bolivia",
    "paraguay", "guatemala", "costa rica", "panama", "honduras", "el salvador", "nicaragua",
}
_SPANISH_WORDS = {
    "el", "la", "los", "las", "de", "del", "que", "mi", "tu", "te", "con", "por",
    "para", "una", "un", "y", "lo", "se", "yo", "sin", "mas", "como", "amor",
    "calle", "barrio", "vida", "corazon", "noche", "flow",
}
_ENGLISH_WORDS = {
    "the", "you", "my", "i", "and", "of", "your", "it", "is", "on", "we", "love",
    "night", "life", "all", "get", "up", "don't", "can't", "ain't",
}
_SPANISH_TAGS = {
    "spain", "spanish", "espanol", "spanish rap", "rap espanol", "spanish hip hop",
    "hip hop espanol", "trap espanol", "latin", "latino", "latin trap", "trap latino",
    "reggaeton", "puerto rico", "argentina", "mexico", "colombia", "chile",
}
_NON_SPANISH_TAGS = {
    "british", "uk", "english", "american", "usa", "us", "west coast", "east coast",
    "west coast rap", "east coast rap", "french", "german", "italian", "dutch",
}
_SPANISH_LETTERS = re.compile(r"[ñáéíóúü¿¡]", re.IGNORECASE)
MIN_SCORE = 2.0  # points the winning folder needs...
MIN_MARGIN = 1.5  # ...and how many times the runner-up's score it must have
BPM_TOLERANCE = 3
_JUNK_TAG = re.compile(r"https?:|www\.|\.com\b|;;|\bunknown\b|\bother\b", re.IGNORECASE)
_TAG_SEPARATORS = re.compile(r"\s*[,;|]\s*")
_FEATURING = re.compile(r"\s*[(\[]?\s*\b(feat\.?|ft\.?|featuring)\s.*$", re.IGNORECASE)
_MAIN_ARTIST = re.compile(r"\s*(,|&|\bx\b|\bfeat\.?|\bft\.?|\bvs\.?)\s*", re.IGNORECASE)


@dataclass
class Classification:
    folder: str | None  # None: not confident enough
    scores: dict[str, float] = field(default_factory=dict)
    evidence: list[str] = field(default_factory=list)
    bpm: float | None = None


def _normalize(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return " ".join(text.lower().replace("’", "'").split())


def load_folders(path: str | None = None) -> dict[str, dict]:
    path = path or config.GENRES_CONFIG_PATH
    try:
        with open(path, encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
    except FileNotFoundError:
        raise RuntimeError(
            f"{path} does not exist. Copy config/genres.example.yaml to "
            "config/genres.yaml and adapt it to your folders."
        )
    return {
        folder: {
            "genres": {_normalize(g) for g in (rules or {}).get("genres", [])},
            "bpm": _bpm_ranges((rules or {}).get("bpm")),
            "spanish": (rules or {}).get("spanish"),
        }
        for folder, rules in raw.items()
    }


def _bpm_ranges(value) -> list[tuple[float, float]]:
    """[min, max] or a list of them ([[60, 115], [135, 180]]) -> list of ranges."""
    if not value:
        return []
    if isinstance(value[0], (int, float)):
        value = [value]
    return [(float(low), float(high)) for low, high in value]


def guess_spanish(
    artist: str, title: str, countries: list[str], tags: list[str] = ()
) -> bool | None:
    """True if the track looks Spanish-language, False if it looks like
    another language, None if it can't be told. `tags` are Last.fm tags,
    which often name the scene ("spanish rap", "british")."""
    score = 0
    for tag in tags:
        tag = _normalize(tag)
        if tag in _SPANISH_TAGS:
            score += 2
        elif tag in _NON_SPANISH_TAGS:
            score -= 2
    known = [c.lower() for c in countries if c and c.lower() not in ("unknown", "europe", "worldwide")]
    if known:
        spanish_share = sum(c in _SPANISH_COUNTRIES for c in known) / len(known)
        score += 2 if spanish_share > 0.5 else -2 if spanish_share == 0 else 0

    text = f"{artist} {title}"
    score += len(_SPANISH_LETTERS.findall(text))
    words = re.findall(r"[\w']+", _normalize(text))
    score += sum(w in _SPANISH_WORDS for w in words)
    score -= sum(w in _ENGLISH_WORDS for w in words)
    return True if score > 0 else False if score < 0 else None


def _folders_for(genre: str, folders: dict) -> tuple[str | None, list[str]]:
    """The folder genre that best matches `genre` (exact, or the longest one
    contained in it as whole words) and every folder listing it."""
    padded = f" {genre} "
    best_key, best_folders = None, []
    for folder, rules in folders.items():
        for key in rules["genres"]:
            if key == genre or f" {key} " in padded:
                if best_key is None or len(key) > len(best_key):
                    best_key, best_folders = key, [folder]
                elif key == best_key:
                    best_folders.append(folder)
    return best_key, best_folders


# --- signals ---------------------------------------------------------------


def read_tags(path: Path) -> tuple[list[str], float | None]:
    """(genres, bpm) from the file's tags. A tag listing many genres is a
    compilation dumping its whole catalogue, not this track's genre."""
    try:
        audio = mutagen.File(path, easy=True)
    except Exception:
        return [], None
    if not audio:
        return [], None

    genres = []
    for value in audio.get("genre") or []:
        if not _JUNK_TAG.search(value):
            genres += [p for p in _TAG_SEPARATORS.split(value) if p]
    if len(genres) > 3:  # in one value or spread over several
        genres = []

    bpm = None
    for value in audio.get("bpm") or []:
        try:
            bpm = float(value) or None
        except ValueError:
            pass
    return genres, bpm


def _clean_for_lookup(artist: str, title: str) -> tuple[str, str]:
    artist = _MAIN_ARTIST.split(artist, 1)[0].strip()
    title = slskd_client.VERSION_SUFFIX.sub("", _FEATURING.sub("", title)).strip()
    return artist, title


_last_discogs_call = 0.0
_DISCOGS_CACHE_PATH = Path(config.STATE_DB_PATH).parent / "discogs-cache.json"
_discogs_cache: dict | None = None


def discogs_releases(artist: str, title: str) -> list[dict]:
    """Style, genre and country of the top Discogs releases containing the
    track, best matches first. Answers are cached on disk."""
    global _discogs_cache
    artist, title = _clean_for_lookup(artist, title)
    if _discogs_cache is None:
        try:
            _discogs_cache = json.loads(_DISCOGS_CACHE_PATH.read_text(encoding="utf-8"))
        except (FileNotFoundError, ValueError):
            _discogs_cache = {}

    key = f"{artist.lower()}|{title.lower()}"
    # Entries cached before titles were stored can't be checked: fetch again.
    if key not in _discogs_cache or any("title" not in r for r in _discogs_cache[key]):
        results = _discogs_search(artist, title)
        if results is None:  # request failed: don't cache, try again next time
            return []
        _discogs_cache[key] = [
            {
                "style": r.get("style", []),
                "genre": r.get("genre", []),
                "country": r.get("country", ""),
                "title": r.get("title", ""),
            }
            for r in results
        ]
        _DISCOGS_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _DISCOGS_CACHE_PATH.write_text(json.dumps(_discogs_cache), encoding="utf-8")
    return [r for r in _discogs_cache[key] if _same_artist(artist, r["title"])]


def _same_artist(artist: str, release_title: str) -> bool:
    """Whether a Discogs result ("Artist - Release") is by this artist, or a
    compilation. Otherwise it's another song with the same name (a rock
    band's "Rapido" for a reggaeton "Rapido")."""
    release_artist = _normalize(release_title.split(" - ", 1)[0])
    wanted = _normalize(artist)
    return bool(wanted) and (
        wanted in release_artist or release_artist in wanted or release_artist.startswith("various")
    )


def discogs_styles(releases: list[dict]) -> dict[str, float]:
    """Style -> share (0-1) among the releases, weighting the best matches more."""
    weights = [1.0, 0.8, 0.6, 0.4, 0.2]
    shares: dict[str, float] = defaultdict(float)
    total = 0.0
    for weight, release in zip(weights, releases):
        total += weight
        for style in set(release["style"] + release["genre"]):
            shares[style] += weight
    return {style: share / total for style, share in shares.items()} if total else {}


def _discogs_search(artist: str, title: str) -> list[dict] | None:
    """Top releases containing the track ([] if none), or None on errors."""
    global _last_discogs_call

    headers = {"User-Agent": "spotseek/1.0 +https://github.com/kevinliebergen/spotseek"}
    if config.DISCOGS_TOKEN:
        headers["Authorization"] = f"Discogs token={config.DISCOGS_TOKEN}"
    # 60 requests per minute with a token, 25 without (the docs say search
    # needs one, but it currently answers anonymous requests too).
    min_interval = 1.1 if config.DISCOGS_TOKEN else 2.5

    for params in (
        {"artist": artist, "track": title},
        {"q": f"{artist} {title}"},
    ):
        time.sleep(max(0.0, min_interval - (time.time() - _last_discogs_call)))
        _last_discogs_call = time.time()
        try:
            resp = requests.get(
                "https://api.discogs.com/database/search",
                params={**params, "type": "release", "per_page": 5},
                headers=headers,
                timeout=15,
            )
            resp.raise_for_status()
            results = resp.json().get("results", [])
        except (requests.RequestException, ValueError):
            return None
        if results:
            return results
    return []


_LASTFM_CACHE_PATH = Path(config.STATE_DB_PATH).parent / "lastfm-cache.json"
_lastfm_cache: dict | None = None


def _lastfm_call(cache_key: str, **params) -> list[dict] | None:
    """Top tags for a Last.fm call, cached on disk; None on errors."""
    global _lastfm_cache
    if _lastfm_cache is None:
        try:
            _lastfm_cache = json.loads(_LASTFM_CACHE_PATH.read_text(encoding="utf-8"))
        except (FileNotFoundError, ValueError):
            _lastfm_cache = {}
    if cache_key not in _lastfm_cache:
        try:
            resp = requests.get(
                "https://ws.audioscrobbler.com/2.0/",
                params={**params, "api_key": config.LASTFM_API_KEY, "format": "json", "autocorrect": 1},
                timeout=10,
            )
            resp.raise_for_status()
            tags = resp.json().get("toptags", {}).get("tag", [])
        except (requests.RequestException, ValueError):
            return None
        _lastfm_cache[cache_key] = [
            {"name": t["name"], "count": int(t.get("count", 0))} for t in tags[:8]
        ]
        _LASTFM_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _LASTFM_CACHE_PATH.write_text(json.dumps(_lastfm_cache), encoding="utf-8")
    return _lastfm_cache[cache_key]


def lastfm_tags(artist: str, title: str) -> tuple[dict[str, float], str]:
    """(tag -> relevance 0-1, "track" or "artist"). Last.fm currently returns
    no tags for most tracks, so the artist's tags are the fallback."""
    if not config.LASTFM_API_KEY:
        return {}, ""
    artist, title = _clean_for_lookup(artist, title)
    for level, cache_key, params in (
        ("track", f"track:{artist.lower()}|{title.lower()}",
         {"method": "track.gettoptags", "artist": artist, "track": title}),
        ("artist", f"artist:{artist.lower()}", {"method": "artist.gettoptags", "artist": artist}),
    ):
        tags = _lastfm_call(cache_key, **params) or []
        relevant = {t["name"]: t["count"] / 100 for t in tags if t["count"] >= 20}
        if relevant:
            return relevant, level
    return {}, ""


_library: dict | None = None


def _artists(artist: str) -> set[str]:
    return {_normalize(a) for a in _MAIN_ARTIST.split(artist) if a and not _MAIN_ARTIST.fullmatch(a)} - {""}


def _load_library() -> dict:
    """artist -> {folder: count} and file -> (artists, folder), from the tracks
    already filed in the genre subfolders of COPY_TO_DIR."""
    by_artist: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    by_file: dict[str, tuple[set[str], str]] = {}
    if config.COPY_TO_DIR and Path(config.COPY_TO_DIR).is_dir():
        for subfolder in Path(config.COPY_TO_DIR).iterdir():
            if not subfolder.is_dir():
                continue
            for path in subfolder.iterdir():
                try:
                    audio = mutagen.File(path, easy=True)
                except Exception:
                    continue
                if not audio or not audio.get("artist"):
                    continue
                artists = _artists(audio["artist"][0])
                by_file[str(path)] = (artists, subfolder.name)
                for a in artists:
                    by_artist[a][subfolder.name] += 1
    return {"by_artist": by_artist, "by_file": by_file}


def library_folders(artist: str, exclude: Path | None = None) -> dict[str, float]:
    """Folder -> share of this artist's tracks you filed there, weighted down
    when there are only one or two of them. `exclude` leaves a file out (to
    evaluate the classifier on tracks already in the library)."""
    global _library
    if _library is None:
        _library = _load_library()
    own = _library["by_file"].get(str(exclude)) if exclude else None

    counts: dict[str, int] = defaultdict(int)
    for a in _artists(artist):
        for folder, n in _library["by_artist"].get(a, {}).items():
            counts[folder] += n
        if own and a in own[0]:
            counts[own[1]] -= 1
    counts = {f: n for f, n in counts.items() if n > 0}
    total = sum(counts.values())
    if not total:
        return {}
    confidence = min(1.0, total / 3)
    return {f: confidence * n / total for f, n in counts.items()}


# --- scoring ---------------------------------------------------------------


def classify(path: Path, artist: str, title: str, folders: dict | None = None) -> Classification:
    folders = folders or load_folders()
    scores: dict[str, float] = defaultdict(float)
    evidence = []
    tag_genres, bpm = read_tags(path)
    releases = discogs_releases(artist, title)
    lastfm, lastfm_level = lastfm_tags(artist, title)
    spanish = guess_spanish(
        artist, title, [r.get("country", "") for r in releases], list(lastfm)
    )

    def tempo_fits(folder: str, tolerance: float = 0) -> bool:
        return any(
            low - tolerance <= bpm <= high + tolerance for low, high in folders[folder]["bpm"]
        )

    def add(source: str, genre: str, weight: float) -> None:
        key, targets = _folders_for(_normalize(genre), folders)
        if not targets:
            return
        if bpm and len(targets) > 1:
            # A genre shared by several folders ("techno"): tempo decides...
            targets = [f for f in targets if tempo_fits(f)] or targets
        if spanish is not None and len(targets) > 1:
            # ...or language ("rap": HIP HOP or RAP ESPAÑOL). Unknown: a tie.
            targets = [f for f in targets if folders[f]["spanish"] in (None, spanish)] or targets
        # A folder only for one language never takes a track known to be in
        # another, and a Spanish-only folder needs the track to look Spanish
        # (so English "rock" doesn't land in FIESTA ESPAÑOLA).
        targets = [
            f for f in targets
            if not (folders[f]["spanish"] is True and spanish is not True and len(targets) == 1)
            and not (spanish is not None and folders[f]["spanish"] is not None
                     and folders[f]["spanish"] != spanish)
        ]
        if not targets:
            return
        if key in GENERIC_GENRES:
            weight /= 2
        for folder in targets:
            scores[folder] += weight
        evidence.append(f"{source}: {genre} ({weight:.1f})")

    for genre in tag_genres:
        add("tag", genre, 2.0)
    # For a remix or edit Discogs usually finds the original song, which may
    # be another genre entirely (a hip hop classic remixed as tech house).
    is_version = bool(slskd_client._remixer_words(slskd_client.VERSION_SUFFIX.sub("", title)))
    discogs_weight = 1.0 if is_version else 3.0
    for style, share in discogs_styles(releases).items():
        add("discogs", style, discogs_weight * share)

    # Where you usually file this artist.
    for folder, share in library_folders(artist, exclude=path).items():
        if folder in folders:
            scores[folder] += 3.0 * share
            evidence.append(f"library: artist in {folder} ({3.0 * share:.1f})")
    # Artist tags describe the artist's usual genre, not this track: weaker,
    # and generic ones ("house") are skipped since they can't tell apart the
    # styles that artist moves between. They still count for the language.
    lastfm_weight = {"track": 1.5, "artist": 0.6}.get(lastfm_level, 0.0)
    for tag, relevance in lastfm.items():
        if lastfm_level == "artist" and _normalize(tag) in GENERIC_GENRES:
            continue
        add(f"lastfm {lastfm_level}", tag, lastfm_weight * relevance)

    if spanish is not None:
        evidence.append("language: " + ("spanish" if spanish else "not spanish"))
    if bpm:
        # Tempo only rules folders out; it never adds points on its own.
        evidence.append(f"bpm: {bpm:g}")
        for folder in list(scores):
            if folders[folder]["bpm"] and not tempo_fits(folder, BPM_TOLERANCE):
                scores[folder] *= 0.25

    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    folder = None
    if ranked and ranked[0][1] >= MIN_SCORE:
        runner_up = ranked[1][1] if len(ranked) > 1 else 0.0
        if runner_up == 0 or ranked[0][1] >= MIN_MARGIN * runner_up:
            folder = ranked[0][0]

    return Classification(
        folder=folder,
        scores={f: round(s, 2) for f, s in ranked},
        evidence=evidence,
        bpm=bpm,
    )
