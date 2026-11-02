@echo off
setlocal
set "DBA_TOOLKIT_HOME=%~dp0"
set "PYTHONUTF8=1"
pushd "%~dp0"
"%~dp0runtime\python.exe" -I "%~dp0app\portable_entry.py" %*
set "DBA_EXIT=%ERRORLEVEL%"
popd
exit /b %DBA_EXIT%
