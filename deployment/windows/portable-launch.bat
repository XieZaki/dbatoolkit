@echo off
setlocal
set "DBA_TOOLKIT_HOME=%~dp0"
set "PYTHONUTF8=1"
pushd "%~dp0"
"%~dp0runtime\python.exe" -I "%~dp0deployment\windows\startup_check.py"
if errorlevel 1 goto :failed
"%~dp0runtime\python.exe" -I "%~dp0app\portable_entry.py" %*
set "DBA_EXIT=%ERRORLEVEL%"
popd
if "%~1"=="" pause
exit /b %DBA_EXIT%
:failed
set "DBA_EXIT=%ERRORLEVEL%"
popd
echo Portable runtime check failed. Review the message above and docs\DRIVER_SETUP.md.
pause
exit /b %DBA_EXIT%
