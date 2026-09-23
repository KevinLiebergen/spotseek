# spotseek

When the Windows machine running rekordbox boots up, it checks your new
Spotify likes, searches for and downloads them over Soulseek, tries to
infer the genre, and drops the file into the matching genre subfolder
inside your rekordbox collection. rekordbox itself is NOT touched
automatically: you open the app when you're about to prep a session and
analyze the folders with new tracks.

## Architecture

1. **slskd**: a headless Soulseek client with a REST API. Replaces
   Nicotine+ so search/download can be automated.
2. **src/main.py**: the orchestrator. Compares your likes against a
   local record (`data/state.db`) to figure out which ones are new,
   searches each one on slskd, downloads the best result, infers a
   genre, and moves the file.
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
- (Optional) A Last.fm API key, free, for the genre fallback:
  https://www.last.fm/api/account/create
- Python 3.11+ installed on the Windows machine.

## Installation

```bat
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt

copy .env.example .env
copy config\genre_mapping.example.yaml config\genre_mapping.yaml
```

Edit `.env` with your credentials and the real path to your rekordbox
collection (`REKORDBOX_ROOT`), and `config\genre_mapping.yaml` with the
Spotify-genre -> your-folder-name mapping.

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
   Windows service (natively supported).

## Known limitations / things to verify on the real Windows box

- **slskd API**: `src/slskd_client.py` is written against the
  documented v0 API endpoints, but this can vary by installed version.
  Before trusting it, compare it against your instance's Swagger
  (`http://localhost:5030/swagger`) and adjust field names/endpoints if
  needed. This can't be tested without a real slskd instance.
- **Genre classification**: based on the genres Spotify assigns to the
  artist (not the track) plus an optional fallback to Last.fm tags,
  passed through your own mapping in `config/genre_mapping.yaml`.
  Anything that doesn't match lands in the `Unclassified` folder —
  check it occasionally and extend the mapping.
- **rekordbox**: import and analysis are intentionally not automated
  (rekordbox has no official API for that, and writing to its database
  directly is fragile). The file just ends up in the right folder so
  that when you open rekordbox, you select that folder and hit
  analyze.
