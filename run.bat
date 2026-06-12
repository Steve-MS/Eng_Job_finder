@echo off
REM Mech-PM-Finder weekly runner
REM Activates the project venv and runs the CLI.
REM Usage: run.bat [--source <name>]
call "%~dp0.venv\Scripts\activate.bat"
python -m mechpm.cli run-all %*
