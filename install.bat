@echo off
setlocal
cd /d "%~dp0"
rem Started by a double click (cmd /c "this.bat", no arguments): the script waits for Enter at the end, so the window stays readable
set "REMIQORA_WAIT="
if "%~1"=="" if not defined REMIQORA_NO_PAUSE echo %cmdcmdline% | find /i "%~nx0" >nul && set "REMIQORA_WAIT=1"

where git >nul 2>nul
if errorlevel 1 goto prereqs
where uv >nul 2>nul
if errorlevel 1 goto prereqs
py -3.13 --version >nul 2>nul
if errorlevel 1 goto prereqs

call powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup_models.ps1" %*
exit /b %errorlevel%

:prereqs
set "REMIQORA_NO_PAUSE=1"
set "REMIQORA_WAIT="
call "%~dp0setup_prereqs.bat"
echo.
echo If prerequisites were installed, close this window, open a new terminal, and run install.bat again.
pause
exit /b 1
endlocal
