# spotseek

Turns the tracks you like on Spotify and SoundCloud into files in your DJ
collection, sorted by genre and ready in rekordbox. When the Windows
machine running rekordbox logs in, spotseek:

1. reads your new Spotify likes (and optionally SoundCloud likes);
2. searches for each one on Soulseek, through slskd, and downloads the best
   file (original/extended mixes, lossless or high bitrate);
3. works out its genre and copies it into the matching folder of your
   collection, e.g. `my_music\[01] TECH HOUSE`, or leaves it at the top of
   the collection for you to file when it isn't sure;
4. adds it to the rekordbox playlist with the same name as that folder
   (rekordbox's auto analysis then analyzes it when you open it);
5. tells you how it went with a Windows notification and a summary file.

Nothing is ever downloaded from Spotify or SoundCloud: they're only used
as lists of what you like.

## How it fits together

- **[slskd](https://github.com/slskd/slskd)**: a headless Soulseek client
  with a REST API, so searching and downloading can be automated.
- **`src/main.py`**: the orchestrator. It compares your likes with a local
  record (`data/state.db`) to find the new ones, then searches, downloads,
  classifies and files each of them, and finally syncs rekordbox.
- **`src/genre.py`**: the genre classifier, configured per folder in
  `config/genres.yaml`.
- **`src/rekordbox.py`**: keeps rekordbox's collection and genre playlists
  in line with your folders, through
  [pyrekordbox](https://github.com/dylanljones/pyrekordbox).
- **Windows Task Scheduler**: starts slskd at logon, and `run_windows.bat`
  (spotseek) 30 seconds later.

## Setup

Prerequisites, on the Windows machine:

- **Python 3.11+**.
- **slskd** running with an API key (`web.authentication.api_keys` in
  `slskd.yml`) and its downloads folder set (`directories.downloads`).
  There's a native Windows binary; no Docker needed.
- **A Spotify app** registered at https://developer.spotify.com/dashboard,
  for `SPOTIFY_CLIENT_ID` and `SPOTIFY_CLIENT_SECRET`, with the redirect
  URI you set in `.env` (default `http://127.0.0.1:8888/callback`).
- Optional, both free, both improve genre classification: a Discogs token
  (https://www.discogs.com/settings/developers, "Generate new token") and a
  Last.fm API key (https://www.last.fm/api/account/create).

Install:

```bat
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
copy .env.example .env
copy config\genres.example.yaml config\genres.yaml
```

Fill in `.env` (every setting is explained in `.env.example`) and adapt
`config/genres.yaml` to your own genre folders. Then authorize Spotify
once; the token is kept in `data/` and refreshes itself:

```bat
.venv\Scripts\python scripts\authorize_spotify.py
```

Always use `.venv\Scripts\python` when running things by hand: the
system Python doesn't have the dependencies.

## Running it

Manually, with slskd running:

```bat
.venv\Scripts\python -m src.main
```

The **first run downloads nothing**: it only records the likes you already
have, so that from then on only new ones are downloaded. Each source
(Spotify, SoundCloud) has its own first run. To also get your N most
recent likes on that first run:

```bat
.venv\Scripts\python -m src.main --backfill 20
```

To run it automatically, create two tasks in Task Scheduler for your user,
both "At log on" and run hidden: one starting `slskd.exe` (with
`--app-dir` if its config lives elsewhere) and one starting
`run_windows.bat` with a 30-second delay and no short time limit, since a
run with many tracks can take a while.

### Knowing how it went

- A **Windows notification** when a run starts (if there's anything to do)
  and when it ends, e.g. "5 descargadas (4 en su carpeta, 1 sin
  clasificar), 3 no encontradas, rekordbox al día". `NOTIFY=false` turns
  them off.
- **`data/ultimo-resumen.txt`**: the last run's tracks, grouped by outcome,
  with the folder each download went to.
- **`data/spotseek.log`**: everything, including why each track went where
  it did. It rotates at 5 MB. Errors raised before logging starts go to
  `data/task-output.log`.

## Sources

- **Spotify**: your liked songs.
- **SoundCloud** (optional, `SOUNDCLOUD_USER` = the `user` in
  `soundcloud.com/user`): your public likes, read with yt-dlp, no API key
  needed. `run_windows.bat` upgrades yt-dlp before each run because
  SoundCloud changes often. SoundCloud titles have no artist field, so
  "Artist - Title" is split, track numbers ("9. ") and "[Free DL]"-style
  noise are stripped, and the uploader is used when there's no artist in
  the title. Liked playlists and anything longer than 15 minutes (DJ sets,
  radio shows) are skipped. Many SoundCloud edits and bootlegs only exist
  there, so expect more "not found" than with Spotify.

Likes you already have anywhere in your collection (same main artist and
title) aren't downloaded again. A radio edit or extended mix counts as the
same track; a remix you don't have yet doesn't. `SKIP_DUPLICATES=false`
turns this off.

## Downloading

Soulseek only returns files whose path contains every word of the query,
so when the exact "Artist Title" search finds nothing, simpler versions
are tried: without "(feat. ...)" or "- Radio Edit"-style suffixes, without
non-version parentheses, and without punctuation. Searches still running
at the timeout are stopped, since slskd only returns responses once a
search is complete.

Among the results, spotseek prefers users with a free upload slot, then
files named "Extended Mix" / "Original Mix", files whose length is known,
`PREFERRED_FORMATS`, bitrate and a short queue. It skips:

- files shorter than the track (a different edit) or longer than 15
  minutes (a DJ mix). Longer files are fine: Spotify often only has the
  radio edit;
- files with a known bitrate below `MIN_BITRATE` (FLAC excepted);
- a different version: a remix the title doesn't mention ("Wings [Krakota
  Remix]" for "Wings"), or, for a remix or edit, a file that doesn't name
  its author ("La Linea (Original Mix)" for "La Linea (Toth Edit)").

Files from up to 3 different users are tried, best first. A user is
skipped (and the transfer cancelled) when slskd can't queue the download,
the user rejects it, it fails, it doesn't finish within
`DOWNLOAD_TIMEOUT_SECONDS`, or more than `MAX_QUEUE_POSITION` downloads are
queued ahead of ours.

Tracks that aren't found or fail are retried on later runs, up to
`MAX_ATTEMPTS` times (default 5); Soulseek availability changes with who
is online. A track that fails because of the network is retried once
after 30 seconds. If slskd isn't logged in to Soulseek (e.g. stuck in
"Disconnecting" after the PC slept), spotseek waits 90 seconds, then
restarts it once through its scheduled task (`SLSKD_RESTART_TASK`); if it's
still not connected, the run stops without marking the remaining tracks.

Each finished track stays in slskd's downloads folder (`DOWNLOAD_DIR`) as
`Artist - Title.ext`, and a copy goes into your collection (`COPY_TO_DIR`).

## Genre sorting

With `COPY_TO_DIR` and `config/genres.yaml` in place, each copy goes into
its genre subfolder when the classifier is confident, and to the top of
`COPY_TO_DIR` when it isn't. `SORT_BY_GENRE=false` turns it off. Each folder
in `config/genres.yaml` lists the genre names that point to it, and
optionally a BPM range and a language (see the comments in
`config/genres.example.yaml`). The classifier combines:

- the genre and BPM tags inside the file;
- the names of the Soulseek folders the file came from (e.g. "Beatport -
  Top 100 Deep House"), useful for releases no catalogue knows yet;
- Discogs styles of releases by the same artist (`DISCOGS_TOKEN`), weighted
  down for remixes, where Discogs usually finds the original song;
- Last.fm tags of the track or, usually, of its artist (`LASTFM_API_KEY`);
- the folder where you already filed other tracks by that artist;
- a Spanish-language guess, for folders split by language (e.g. hip hop and
  Spanish rap).

BPM picks between folders that share a genre (plain "techno" goes to peak
time or hard techno by tempo) and rules out folders whose tempo is clearly
off; it never decides on its own. Discogs and Last.fm answers are cached
in `data/`.

To check the classifier against a collection you already sorted:

```bat
.venv\Scripts\python scripts\genre_report.py --evaluate
.venv\Scripts\python scripts\genre_report.py --evaluate --proposals
.venv\Scripts\python scripts\apply_proposals.py
.venv\Scripts\python scripts\sort_loose.py --dry-run
```

`--evaluate` compares its proposals with the folders your tracks are in;
`--proposals` writes the disagreements to `data/propuestas.txt` for you to
mark SI/NO, and `apply_proposals.py` moves the SI ones. `sort_loose.py`
files the tracks lying at the top of `COPY_TO_DIR`. Every move is logged
in `data/moves-log.txt`.

## rekordbox sync

With `REKORDBOX_SYNC=true`, at the end of each run (also runs with nothing
new, to pick up tracks you filed by hand) and only while rekordbox is
closed, spotseek updates rekordbox's database, after backing it up to
`data/rekordbox-backups`:

- tracks whose file moved within `COPY_TO_DIR` are relocated (by unique
  file name), keeping their cues and analysis;
- every genre folder gets a playlist with the same name inside the
  `REKORDBOX_PLAYLIST_FOLDER` playlist folder;
- each track is put in the playlist of its folder and taken out of the
  other genre playlists;
- files in the genre folders that aren't in the collection yet are added.

Only rekordbox can analyze tracks (beatgrid, waveform): turn on its auto
analysis (Preferences > Analysis) and it analyzes the new ones when you
open it. To preview or run the sync by hand:

```bat
.venv\Scripts\python scripts\rekordbox_sync.py --dry-run
.venv\Scripts\python scripts\rekordbox_sync.py
```

## Known limitations

- **slskd API**: `src/slskd_client.py` was checked against slskd 0.26.0.
  If you upgrade slskd, compare it with your instance's Swagger
  (`http://localhost:5030/swagger`, needs `feature.swagger: true`).
- **slskd listen port**: on some Windows machines the default port (50300)
  falls inside a range reserved by Hyper-V/WSL and slskd exits with
  `ListenException`. Check with
  `netsh interface ipv4 show excludedportrange protocol=tcp` and set
  `soulseek.listen_port` to a free one (e.g. 2234). Allow slskd through the
  Windows firewall on your private network; forwarding that port on the
  router is optional but lets more users send you files.
- **rekordbox database**: written through pyrekordbox, tested by its
  authors up to rekordbox 7.0.9 (this setup runs 7.2.18). Keep backups.
- **Windows only** for the scheduled tasks, notifications, slskd restarts
  and rekordbox sync; the downloading and sorting itself is plain Python.
