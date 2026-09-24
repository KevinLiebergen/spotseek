# spotseek

When the Windows machine running rekordbox boots up, it checks your new
Spotify (and optionally SoundCloud) likes, searches for and downloads
them over Soulseek, and leaves
them in one folder as `Artist - Title.ext`. You decide by hand which
rekordbox folder each track goes into; rekordbox itself is not touched.

## Architecture

1. **slskd**: a headless Soulseek client with a REST API. Replaces
   Nicotine+ so search/download can be automated.
2. **src/main.py**: the orchestrator. Compares your likes against a
   local record (`data/state.db`) to figure out which ones are new,
   searches each one on slskd, downloads the best result, and moves it
   to the top of the downloads folder with a clean name.
3. **Windows Task Scheduler**: triggers `run_windows.bat` on logon.

## Prerequisites (to do yourself, on the Windows machine)

- **slskd installed and running**, with an API key configured.
  Repo: https://github.com/slskd/slskd — follow its Windows install
  guide (there's a native binary, no Docker required).
- **A Spotify app registered** at
  https://developer.spotify.com/dashboard to get
  `SPOTIFY_CLIENT_ID` and `SPOTIFY_CLIENT_SECRET`. Set the Redirect URI
  to the same value you use in `.env` (defaults to
  `http://127.0.0.1:8888/callback`).
- Python 3.11+ installed on the Windows machine.

## Installation

```bat
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt

copy .env.example .env
```

Edit `.env` with your credentials and set `DOWNLOAD_DIR` to slskd's
downloads folder (`directories.downloads` in `slskd.yml`). Optionally set
`COPY_TO_DIR` (e.g. your rekordbox collection folder) to also get a copy
of every finished track there.

## Spotify authorization (one time)

```bat
python scripts\authorize_spotify.py
```

Follow the on-screen instructions. This saves a token to
`data/.spotify-token-cache` that auto-refreshes afterwards, so you
shouldn't need to repeat it unless you delete that folder.

## Manual test run

With slskd running:

```bat
python -m src.main
```

Check `data/spotseek.log` to see what it did with each new track.

The **first run doesn't download anything**: it only records your current
likes in `data/state.db`, so that from then on only new likes are
downloaded. To also grab your most recent likes on that first run, pass
`--backfill N`:

```bat
python -m src.main --backfill 20
```

## Genre sorting (optional)

With `COPY_TO_DIR` set and `config/genres.yaml` in place (copy
`config/genres.example.yaml` and adapt it to your folders), each download
is copied into its genre subfolder, e.g. `my_music\[01] TECH HOUSE`, when
the classifier is confident, and to `COPY_TO_DIR` itself when it isn't, for
you to file by hand. `data/spotseek.log` says where each track went and
why. The classifier (`src/genre.py`) combines:

- the genre and BPM tags inside the file;
- Discogs styles of releases by the same artist (`DISCOGS_TOKEN`);
- Last.fm tags of the track or, usually, its artist (`LASTFM_API_KEY`);
- the folder where you already filed other tracks by that artist;
- a Spanish-language guess, for folders split by language.

To check it against your own collection before trusting it:

```bat
python scripts\genre_report.py --evaluate
python scripts\genre_report.py --evaluate --proposals
python scripts\apply_proposals.py
python scripts\sort_loose.py --dry-run
```

`--evaluate` compares its proposals with the folders you already filed
tracks in; `--proposals` also writes the disagreements to
`data/propuestas.txt`, where you mark SI/NO, and `apply_proposals.py`
moves the SI ones. `sort_loose.py` files the tracks lying loose in
`COPY_TO_DIR`. Every move is logged in `data/moves-log.txt`.

## rekordbox sync (optional)

With `REKORDBOX_SYNC=true`, after each run spotseek updates rekordbox's
database (through pyrekordbox), as long as rekordbox is closed:

- tracks whose file moved within `COPY_TO_DIR` are relocated (by unique
  file name), keeping their cues and analysis;
- every genre folder gets a playlist with the same name inside the
  `REKORDBOX_PLAYLIST_FOLDER` playlist folder (renaming an old playlist
  whose tracks mostly moved to that folder, or creating it);
- each track is put in the playlist of its folder and taken out of the
  other genre playlists;
- files in the genre folders that aren't in the collection are added.

`master.db` is backed up to `data/rekordbox-backups` before each write.
To preview or run it by hand:

```bat
python scripts\rekordbox_sync.py --dry-run
python scripts\rekordbox_sync.py
```

## SoundCloud likes (optional)

Set `SOUNDCLOUD_USER` in `.env` (the `user` in `soundcloud.com/user`)
and your SoundCloud likes are handled like Spotify ones: listed with
yt-dlp (no API key; your likes must be public), searched and downloaded
from Soulseek, never from SoundCloud itself. Its first run also just
records your existing likes. Liked playlists are ignored, and anything
longer than 15 minutes (DJ sets, radio shows) is skipped. Expect more
"not found" than with Spotify: many SoundCloud edits and bootlegs only
exist there.

SoundCloud titles have no separate artist field, so spotseek splits
"Artist - Title" and strips "[Free DL]"-style noise. When a title
doesn't follow that pattern, the uploader is used as the artist and the
title alone is also tried.

## Which file gets picked

Soulseek only returns files whose path contains every word of the query,
so if the exact "Artist Title" search finds nothing, spotseek retries
with simpler versions: without "(feat. ...)" or "- Radio Edit"-style
suffixes, without anything in parentheses, and without punctuation.

Among all search results, spotseek prefers, in order: users with a free
upload slot, files named "Extended Mix" / "Original Mix", files whose
length is known, then `PREFERRED_FORMATS`, bitrate and a short queue.
Files shorter than the Spotify track (a different edit), longer than 15
minutes (usually a full DJ mix), or with a known bitrate below
`MIN_BITRATE` (FLAC excepted) are skipped. Longer files are accepted on
purpose: Spotify often only has the radio edit.

Files that look like a different version are skipped too: a remix or
edit the title doesn't mention ("Wings [Krakota Remix]" for "Wings"), or,
when the title is a remix or edit, a file that doesn't name its author
("La Linea (Original Mix)" for "La Linea (Toth Edit)"). Radio edits and
original/extended mixes count as the same track. If only other versions
exist, the track is marked not found and retried later.

## Scheduling on Windows (Task Scheduler)

1. Open "Task Scheduler".
2. Create Basic Task -> Trigger: "When I log on".
3. Action: "Start a program" -> select `run_windows.bat`
   (full path inside this folder).
4. Under "Settings", check "Run task as soon as possible after a
   scheduled start is missed" and remove any short time limit (a
   search + download can take several minutes).
5. If you also want slskd to start automatically, create a similar
   task pointing at the slskd executable, or install slskd as a
   Windows service (natively supported). spotseek waits up to 3 minutes
   for slskd to log in to Soulseek before searching.

## Known limitations / things to verify on the real Windows box

- **slskd API**: `src/slskd_client.py` was checked against slskd 0.26.0.
  If you upgrade slskd, compare it against your instance's Swagger
  (`http://localhost:5030/swagger`, needs `feature.swagger: true`).
- **slskd listen port**: on some Windows machines the default port
  (50300) falls inside a range reserved by Hyper-V/WSL and slskd exits
  with `ListenException`. Check with
  `netsh interface ipv4 show excludedportrange protocol=tcp` and set
  `soulseek.listen_port` to a free one (e.g. 2234).
- **Retries**: tracks that weren't found or failed to download are
  retried on later runs, up to `MAX_ATTEMPTS` times in total (default 5).
  If slskd loses its Soulseek connection mid-run (e.g. the PC went to
  sleep), the run stops without marking the remaining tracks, and they're
  picked up next time. A slskd stuck in "Disconnecting" needs a restart.
- **rekordbox analysis**: tracks are added to the collection, but only
  rekordbox itself can analyze them (beatgrid, waveform). Select the genre
  playlist in rekordbox and Analyze the new ones.
- **rekordbox database**: writing it goes through pyrekordbox, which is
  tested up to rekordbox 7.0.9 (this setup runs 7.2.18). Back up your
  collection before enabling `REKORDBOX_SYNC`.
