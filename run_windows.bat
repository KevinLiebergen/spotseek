@echo off
REM Entry point for the Windows scheduled task.
REM Adjust the path below if the project doesn't live in this folder.
cd /d "%~dp0"
call .venv\Scripts\activate.bat
python -m src.main
