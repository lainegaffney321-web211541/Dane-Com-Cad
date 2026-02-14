@echo off
setlocal
cd /d "%~dp0"

set "PYTHON=venv\Scripts\python.exe"
if not exist "%PYTHON%" (
  echo [ERROR] venv Python not found at %PYTHON%
  echo Create it first: py -3 -m venv venv
  exit /b 1
)

echo Starting Danecom CAD stack from %CD%
start "Danecom CAD API 8787" cmd /k "cd /d "%CD%" && "%PYTHON%" -m uvicorn cad_extract_api:app --host 127.0.0.1 --port 8787"
start "Danecom Monitor" cmd /k "cd /d "%CD%" && "%PYTHON%" -u openmhz_sheriff_monitor.py"
start "Danecom Dashboard 8501" cmd /k "cd /d "%CD%" && "%PYTHON%" -m streamlit run dashboard_sheriff.py --server.port 8501 --server.address 127.0.0.1"

echo.
echo API:       http://127.0.0.1:8787/health
echo Dashboard: http://127.0.0.1:8501
echo.
echo Use RESTART_CAD.bat to restart services.
endlocal
