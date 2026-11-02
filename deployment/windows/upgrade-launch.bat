@echo off
setlocal
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0deployment\windows\update-bootstrap.ps1" -InstallRoot "%~dp0"
set "UPGRADE_EXIT=%ERRORLEVEL%"
if not "%UPGRADE_EXIT%"=="0" echo Upgrade did not complete. Review the safe error message above; user data was not changed.
pause
exit /b %UPGRADE_EXIT%
