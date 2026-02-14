@echo off
setlocal
cd /d "%~dp0"

echo Restarting Danecom CAD services...
for %%P in (8787 8501) do (
  for /f "tokens=5" %%A in ('netstat -ano ^| findstr :%%P ^| findstr LISTENING') do (
    taskkill /PID %%A /F >nul 2>&1
  )
)

taskkill /FI "WINDOWTITLE eq Danecom CAD API 8787" /F >nul 2>&1
taskkill /FI "WINDOWTITLE eq Danecom Monitor" /F >nul 2>&1
taskkill /FI "WINDOWTITLE eq Danecom Dashboard 8501" /F >nul 2>&1

call START_CAD.bat
endlocal
