@echo off
setlocal
set "deep="
if /i "%~1"=="-Deep" set "deep=-Deep"
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -ExecutionPolicy Bypass -File "%~dp0Collect-TheHunt-Diagnostics.ps1" %deep%
set "result=%ERRORLEVEL%"
if not "%result%"=="0" echo Diagnostics collection failed.
pause
exit /b %result%
