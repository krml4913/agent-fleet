@echo off
rem agent-fleet Windows shim: runs the sibling "fleet-agent" Python script.
rem Prefers the py launcher (py -3), falls back to python. PYTHONUTF8=1 keeps
rem file and console I/O in UTF-8 instead of the ANSI code page (e.g. cp932).
setlocal
set "PYTHONUTF8=1"
where py >nul 2>nul
if errorlevel 1 goto :use_python
py -3 "%~dp0fleet-agent" %*
exit /b %ERRORLEVEL%
:use_python
python "%~dp0fleet-agent" %*
exit /b %ERRORLEVEL%
