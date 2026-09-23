# spotseek

When the Windows machine running rekordbox boots up, it checks your new
Spotify likes, searches for and downloads them over Soulseek, and leaves
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
downloads folder (`directories.downloads` in `slskd.yml`).

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

## Which file gets picked

Among all search results, spotseek prefers, in order: users with a free
upload slot, files named "Extended Mix" / "Original Mix", files whose
length is known, then `PREFERRED_FORMATS`, bitrate and a short queue.
Files shorter than the Spotify track (a different edit), longer than 15
minutes (usually a full DJ mix), or with a known bitrate below
`MIN_BITRATE` (FLAC excepted) are skipped. Longer files are accepted on
purpose: Spotify often only has the radio edit.

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
- **Failed tracks aren't retried**: a track marked `not_found` or
  `download_failed` in `data/state.db` stays that way. Delete its row to
  retry it.
- **rekordbox**: import and analysis are intentionally not automated
  (rekordbox has no official API for that, and writing to its database
  directly is fragile). Move the new files into your collection folders
  and analyze them in rekordbox.
