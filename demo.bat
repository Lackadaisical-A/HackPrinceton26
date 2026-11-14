@echo off
rem PAUL console: build and open the production site on this Windows laptop.
rem Double-click this file, or run it from cmd/PowerShell. Needs Node 22 (https://nodejs.org).
setlocal
cd /d "%~dp0frontend"

where node >nul 2>nul
if errorlevel 1 (
  echo Node.js is not installed. Install Node 22 LTS from https://nodejs.org and run this again.
  pause
  exit /b 1
)

if not exist node_modules (
  echo Installing dependencies...
  call npm ci || goto :fail
)

echo Building and serving at http://localhost:4173 ...
call npm run demo || goto :fail
exit /b 0

:fail
echo.
echo Something went wrong. See the messages above.
pause
exit /b 1
