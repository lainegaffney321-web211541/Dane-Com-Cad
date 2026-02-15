# Danecom CAD (Windows)

This repo runs a local Danecom CAD pipeline:

- **Extractor API** (FastAPI): `127.0.0.1:8787`
- **Monitor** (OpenMHz -> Whisper -> Extractor -> SQLite)
- **Dashboard** (Streamlit): `127.0.0.1:8501`

No OpenAI key is required. Local extraction is default.

## 1) Setup on Windows

From repo root in Command Prompt or PowerShell:

```bat
py -3 -m venv venv
venv\Scripts\python.exe -m pip install --upgrade pip
venv\Scripts\python.exe -m pip install -r requirements.txt
```

## 2) One-click start

```bat
START_CAD.bat
```

This opens 3 windows:
- API server on `8787`
- monitor process
- Streamlit dashboard on `8501`

## 3) Manual start commands

API:

```bat
venv\Scripts\python.exe -m uvicorn cad_extract_api:app --host 127.0.0.1 --port 8787
```

Health/version checks:

```bat
curl http://127.0.0.1:8787/health
curl http://127.0.0.1:8787/version
```

Monitor:

```bat
venv\Scripts\python.exe -u openmhz_sheriff_monitor.py
```

Dashboard:

```bat
venv\Scripts\python.exe -m streamlit run dashboard_sheriff.py --server.port 8501 --server.address 127.0.0.1
```

## 4) Restart all services

```bat
RESTART_CAD.bat
```

This kills listeners on ports `8787` and `8501` and relaunches `START_CAD.bat`.

## 5) Extraction API test (pursuit example)

Use this curl command:

```bat
curl -X POST http://127.0.0.1:8787/extract ^
  -H "Content-Type: application/json" ^
  -d "{\"transcript\":\"Units are in pursuit westbound on beltline approaching mineral point. Plate of ABC1234. 4605 Larson Beach Road was called in earlier. 10-80 in progress, unit 3124 primary.\"}"
```

Expected behavior:
- `call_type` => `Pursuit`
- `priority` => `DELTA` (or higher)
- `roads` includes `Beltline` and `Mineral Point`
- `ten_codes` includes `10-80`
- `units` includes `3124`
- `units` does **not** include address number `4605`
- `units` does **not** include plate digits from `ABC1234`

## 6) Troubleshooting

### Database is locked
- Monitor enables WAL and retries writes.
- If lock persists, monitor skips that write and continues polling.
- Restart monitor if another tool is holding long write transactions.

### Ports already in use
- Run `RESTART_CAD.bat`.
- Or manually free ports:
  - `netstat -ano | findstr :8787`
  - `netstat -ano | findstr :8501`
  - `taskkill /PID <pid> /F`

### Dashboard shows no rows
- Wait for monitor to process talkgroup `13001` calls.
- Confirm monitor heartbeat logs in its console.
- Confirm API health endpoint is reachable.

### Unicode issues on Windows
- Python source files are UTF-8 and include coding headers.
- Keep files saved as UTF-8.
