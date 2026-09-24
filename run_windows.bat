@echo off
REM Entry point for the Windows scheduled task.
cd /d "%~dp0"
call .venv\Scripts\activate.bat
REM Runs hidden: errors go to data\task-output.log, the rest to data\spotseek.log.
if not exist data mkdir data
REM Keep the error log small: past 1 MB, set it aside (replacing the previous one).
if exist data\task-output.log for %%F in (data\task-output.log) do if %%~zF GTR 1000000 move /y "%%F" data\task-output.old.log > nul
set PYTHONIOENCODING=utf-8
REM SoundCloud changes often and old yt-dlp versions stop reading the likes:
REM update it first. Without network this fails quickly and the run goes on.
python -m pip install --upgrade --quiet --disable-pip-version-check --retries 1 --timeout 10 yt-dlp > nul 2>> data\task-output.log
python -m src.main > nul 2>> data\task-output.log
