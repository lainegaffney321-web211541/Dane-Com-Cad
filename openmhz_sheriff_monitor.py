# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from curl_cffi import requests
from faster_whisper import WhisperModel

DB_PATH = Path(__file__).resolve().parent / "danecom_cad.db"
OPENMHZ_URL = "https://api.openmhz.com/dane_com/calls?limit=200"
EXTRACT_URL = "http://127.0.0.1:8787/extract"
TARGET_TALKGROUP = 13001
POLL_SECONDS = 8
CLEANUP_HOURS = 48

HEADERS = {
    "accept": "application/json, text/plain, */*",
    "accept-language": "en-US,en;q=0.9",
    "cache-control": "no-cache",
    "pragma": "no-cache",
    "referer": "https://openmhz.com/",
    "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_call_time(call: Dict[str, Any]) -> Tuple[int, str]:
    epoch = int(call.get("time") or call.get("timestamp") or time.time())
    iso = datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat()
    return epoch, iso


def connect_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=30, isolation_level=None)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA busy_timeout=10000;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    return conn


def column_exists(conn: sqlite3.Connection, table: str, column: str) -> bool:
    rows = conn.execute(f"PRAGMA table_info({table});").fetchall()
    return any(row[1] == column for row in rows)


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS calls (
            call_id TEXT PRIMARY KEY,
            talkgroup_num INTEGER,
            time_iso TEXT,
            time_epoch INTEGER,
            duration_sec REAL,
            audio_url TEXT,
            raw_json TEXT
        );
        """
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS cad (
            call_id TEXT PRIMARY KEY,
            talkgroup_num INTEGER,
            time_iso TEXT,
            time_epoch INTEGER,
            duration_sec REAL,
            audio_url TEXT,
            status TEXT,
            error TEXT,
            priority_code TEXT,
            call_type TEXT,
            location_text TEXT,
            city TEXT,
            address TEXT,
            cross_streets TEXT,
            roads_json TEXT,
            units_json TEXT,
            callsigns_json TEXT,
            ten_codes_json TEXT,
            summary TEXT,
            transcript TEXT,
            updated_at TEXT
        );
        """
    )

    required_cols = {
        "talkgroup_num": "INTEGER",
        "time_iso": "TEXT",
        "time_epoch": "INTEGER",
        "duration_sec": "REAL",
        "audio_url": "TEXT",
        "status": "TEXT",
        "error": "TEXT",
        "priority_code": "TEXT",
        "call_type": "TEXT",
        "location_text": "TEXT",
        "city": "TEXT",
        "address": "TEXT",
        "cross_streets": "TEXT",
        "roads_json": "TEXT",
        "units_json": "TEXT",
        "callsigns_json": "TEXT",
        "ten_codes_json": "TEXT",
        "summary": "TEXT",
        "transcript": "TEXT",
        "updated_at": "TEXT",
    }

    for col, col_type in required_cols.items():
        if not column_exists(conn, "cad", col):
            conn.execute(f"ALTER TABLE cad ADD COLUMN {col} {col_type};")


def db_execute_retry(conn: sqlite3.Connection, sql: str, params: Tuple[Any, ...] = (), retries: int = 5) -> bool:
    for attempt in range(retries):
        try:
            conn.execute(sql, params)
            return True
        except sqlite3.OperationalError as exc:
            if "locked" in str(exc).lower():
                time.sleep(0.35 * (attempt + 1))
                continue
            raise
    return False


def fetch_calls() -> List[Dict[str, Any]]:
    resp = requests.get(OPENMHZ_URL, headers=HEADERS, impersonate="chrome110", timeout=30)
    resp.raise_for_status()
    payload = resp.json()
    if isinstance(payload, dict):
        calls = payload.get("calls") or payload.get("data") or []
    else:
        calls = payload
    if not isinstance(calls, list):
        return []
    return calls


def get_call_id(call: Dict[str, Any]) -> str:
    return str(call.get("id") or call.get("_id") or f"{call.get('time','')}-{call.get('url','')}")


def download_audio(url: str) -> Optional[str]:
    if not url:
        return None
    try:
        r = requests.get(url, headers=HEADERS, impersonate="chrome110", timeout=45)
        r.raise_for_status()
    except Exception:
        return None

    with tempfile.NamedTemporaryFile(delete=False, suffix=".mp3") as tmp:
        tmp.write(r.content)
        return tmp.name


def transcribe_file(model: WhisperModel, audio_path: str) -> str:
    try:
        segments, _ = model.transcribe(audio_path, beam_size=1, language="en")
        text = " ".join(seg.text.strip() for seg in segments if seg.text.strip())
        return " ".join(text.split())
    except Exception:
        return ""


def call_extractor(transcript: str) -> Dict[str, Any]:
    payload = {"transcript": transcript}
    try:
        resp = requests.post(EXTRACT_URL, json=payload, timeout=15)
        if resp.status_code == 200:
            data = resp.json()
            if isinstance(data, dict):
                return data
    except Exception:
        pass
    return {
        "provider": "local_rules",
        "call_type": "General",
        "priority": "ROUTINE",
        "address": "",
        "cross_streets": "",
        "location_text": "",
        "roads": [],
        "ten_codes": [],
        "units": [],
        "callsigns": [],
        "summary": "General",
    }


def upsert_queued(conn: sqlite3.Connection, call: Dict[str, Any], call_id: str, epoch: int, iso: str) -> bool:
    raw = json.dumps(call, ensure_ascii=False)
    duration = float(call.get("duration") or 0)
    tg = int(call.get("talkgroup_num") or call.get("talkgroup") or 0)
    audio_url = call.get("url") or ""

    inserted_calls = db_execute_retry(
        conn,
        """
        INSERT OR IGNORE INTO calls(call_id, talkgroup_num, time_iso, time_epoch, duration_sec, audio_url, raw_json)
        VALUES(?,?,?,?,?,?,?)
        """,
        (call_id, tg, iso, epoch, duration, audio_url, raw),
    )

    if not inserted_calls:
        return False

    inserted_cad = db_execute_retry(
        conn,
        """
        INSERT INTO cad(
            call_id, talkgroup_num, time_iso, time_epoch, duration_sec, audio_url,
            status, error, updated_at
        ) VALUES(?,?,?,?,?,?,?,?,?)
        ON CONFLICT(call_id) DO UPDATE SET
            talkgroup_num=excluded.talkgroup_num,
            time_iso=excluded.time_iso,
            time_epoch=excluded.time_epoch,
            duration_sec=excluded.duration_sec,
            audio_url=excluded.audio_url,
            status='QUEUED',
            updated_at=excluded.updated_at
        """,
        (call_id, tg, iso, epoch, duration, audio_url, "QUEUED", "", now_iso()),
    )
    return inserted_cad


def set_status(conn: sqlite3.Connection, call_id: str, status: str, error: str = "") -> bool:
    return db_execute_retry(
        conn,
        "UPDATE cad SET status=?, error=?, updated_at=? WHERE call_id=?",
        (status, error, now_iso(), call_id),
    )


def finalize_call(conn: sqlite3.Connection, call_id: str, extract: Dict[str, Any], transcript: str) -> bool:
    return db_execute_retry(
        conn,
        """
        UPDATE cad SET
            status='DONE',
            error='',
            priority_code=?,
            call_type=?,
            location_text=?,
            address=?,
            cross_streets=?,
            roads_json=?,
            ten_codes_json=?,
            units_json=?,
            callsigns_json=?,
            transcript=?,
            summary=?,
            updated_at=?
        WHERE call_id=?
        """,
        (
            str(extract.get("priority") or "ROUTINE"),
            str(extract.get("call_type") or "General"),
            str(extract.get("location_text") or ""),
            str(extract.get("address") or ""),
            str(extract.get("cross_streets") or ""),
            json.dumps(extract.get("roads") or [], ensure_ascii=False),
            json.dumps(extract.get("ten_codes") or [], ensure_ascii=False),
            json.dumps(extract.get("units") or [], ensure_ascii=False),
            json.dumps(extract.get("callsigns") or [], ensure_ascii=False),
            transcript,
            str(extract.get("summary") or extract.get("call_type") or "General"),
            now_iso(),
            call_id,
        ),
    )


def cleanup_old(conn: sqlite3.Connection) -> bool:
    cutoff = int((datetime.now(timezone.utc) - timedelta(hours=CLEANUP_HOURS)).timestamp())
    try:
        conn.execute(
            """
            DELETE FROM cad
            WHERE rowid IN (
                SELECT rowid FROM cad
                WHERE time_epoch < ?
                LIMIT 200
            )
            """,
            (cutoff,),
        )
        conn.execute(
            """
            DELETE FROM calls
            WHERE rowid IN (
                SELECT rowid FROM calls
                WHERE time_epoch < ?
                LIMIT 200
            )
            """,
            (cutoff,),
        )
        return True
    except sqlite3.OperationalError as exc:
        if "locked" in str(exc).lower():
            return False
        raise


def process_loop() -> None:
    conn = connect_db()
    ensure_schema(conn)

    model = WhisperModel("medium.en", device="cpu", compute_type="float32")

    seen_done: set[str] = set()

    while True:
        processed = 0
        skipped = 0
        errors = 0

        try:
            calls = fetch_calls()
        except Exception as exc:
            print(f"[{now_iso()}] heartbeat processed=0 skipped=0 errors=1 fetch_error={exc}", flush=True)
            time.sleep(POLL_SECONDS)
            continue

        for call in calls:
            tg = int(call.get("talkgroup_num") or call.get("talkgroup") or 0)
            if tg != TARGET_TALKGROUP:
                skipped += 1
                continue

            call_id = get_call_id(call)
            if call_id in seen_done:
                skipped += 1
                continue

            epoch, iso = parse_call_time(call)
            if not upsert_queued(conn, call, call_id, epoch, iso):
                errors += 1
                continue

            if not set_status(conn, call_id, "PROCESSING"):
                errors += 1
                continue

            audio_url = call.get("url") or ""
            audio_path = download_audio(audio_url)
            transcript = ""
            if audio_path:
                transcript = transcribe_file(model, audio_path)
                try:
                    os.remove(audio_path)
                except OSError:
                    pass

            extract = call_extractor(transcript)
            ok = finalize_call(conn, call_id, extract, transcript)
            if ok:
                processed += 1
                seen_done.add(call_id)
            else:
                set_status(conn, call_id, "ERROR", "database is locked")
                errors += 1

        if not cleanup_old(conn):
            print(f"[{now_iso()}] cleanup skipped due to database lock", flush=True)

        print(
            f"[{now_iso()}] heartbeat processed={processed} skipped={skipped} errors={errors} total_polled={len(calls)}",
            flush=True,
        )
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    process_loop()
