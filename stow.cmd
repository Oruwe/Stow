@echo off
REM Windows launcher so the agent is driven as `stow <command>` from a checkout.
REM Deliberately does not change directory: relative paths must resolve against
REM the caller's working directory, not the repository root.
setlocal
set "PYTHONPATH=%~dp0;%PYTHONPATH%"
python -m app.cli %*
exit /b %errorlevel%
