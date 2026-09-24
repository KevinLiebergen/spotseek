@echo off
REM Entry point for the Windows scheduled task.
cd /d "%~dp0"
call .venv\Scripts\activate.bat
REM Runs hidden: errors go to data\task-output.log, the rest to data\spotseek.log.
if not exist data mkdir data
set PYTHONIOENCODING=utf-8
python -m src.main > nul 2>> data\task-output.log
