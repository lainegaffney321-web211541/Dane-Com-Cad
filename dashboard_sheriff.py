# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Iterable, List

import pandas as pd
import streamlit as st

DB_PATH = Path(__file__).resolve().parent / "danecom_cad.db"


def connect_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.execute("PRAGMA busy_timeout=10000;")
    return conn


def column_exists(conn: sqlite3.Connection, table: str, column: str) -> bool:
    rows = conn.execute(f"PRAGMA table_info({table});").fetchall()
    return any(row[1] == column for row in rows)


def ensure_schema(conn: sqlite3.Connection) -> None:
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
    required_cols = [
        "talkgroup_num",
        "time_iso",
        "time_epoch",
        "duration_sec",
        "audio_url",
        "status",
        "error",
        "priority_code",
        "call_type",
        "location_text",
        "city",
        "address",
        "cross_streets",
        "roads_json",
        "units_json",
        "callsigns_json",
        "ten_codes_json",
        "summary",
        "transcript",
        "updated_at",
    ]
    for col in required_cols:
        if not column_exists(conn, "cad", col):
            conn.execute(f"ALTER TABLE cad ADD COLUMN {col} TEXT;")


def parse_json_list(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v) for v in value if str(v).strip()]
    s = str(value).strip()
    if not s:
        return []
    try:
        data = json.loads(s)
        if isinstance(data, list):
            return [str(v) for v in data if str(v).strip()]
    except Exception:
        pass
    return [item.strip() for item in s.split(",") if item.strip()]


def list_to_text(items: Iterable[str]) -> str:
    values = [str(i).strip() for i in items if str(i).strip()]
    return ", ".join(values)


def load_calls(limit: int = 150) -> pd.DataFrame:
    conn = connect_db()
    ensure_schema(conn)
    query = """
    SELECT
        call_id,
        time_iso,
        talkgroup_num,
        call_type,
        priority_code,
        address,
        cross_streets,
        ten_codes_json,
        units_json,
        callsigns_json,
        transcript,
        audio_url,
        status
    FROM cad
    ORDER BY time_epoch DESC, updated_at DESC
    LIMIT ?
    """
    df = pd.read_sql_query(query, conn, params=(limit,))
    conn.close()

    if df.empty:
        return df

    df["Time"] = pd.to_datetime(df["time_iso"], errors="coerce").dt.strftime("%Y-%m-%d %H:%M:%S UTC")
    df["Talkgroup"] = df["talkgroup_num"].fillna("").astype(str)
    df["Call Type"] = df["call_type"].fillna("General")
    df["Priority"] = df["priority_code"].fillna("ROUTINE")
    df["Address/Cross"] = (
        df["address"].fillna("").astype(str).str.strip()
        + df["cross_streets"].fillna("").astype(str).apply(lambda s: f" ({s.strip()})" if s and s.strip() else "")
    )

    df["Ten-codes"] = df["ten_codes_json"].apply(lambda x: list_to_text(parse_json_list(x)))
    df["Units/Callsigns"] = df.apply(
        lambda row: list_to_text(parse_json_list(row.get("units_json")))
        + (
            ", " + list_to_text(parse_json_list(row.get("callsigns_json")))
            if list_to_text(parse_json_list(row.get("callsigns_json")))
            else ""
        ),
        axis=1,
    )
    df["Transcript"] = df["transcript"].fillna("")
    return df


def render_audio(url: str, key: str) -> None:
    if url and str(url).strip():
        st.audio(url, format="audio/mp3")
    else:
        st.caption("No audio URL")


def main() -> None:
    st.set_page_config(page_title="Danecom CAD Dashboard", layout="wide")
    st.title("Danecom CAD Dashboard")

    if "last_refresh" not in st.session_state:
        st.session_state["last_refresh"] = time.time()

    df = load_calls(limit=150)
    if df.empty:
        st.info("No CAD data yet. Monitor is likely still collecting calls.")
    else:
        latest = df.iloc[0]
        st.subheader("Latest Call")
        st.markdown(
            f"**{latest['Time']}** | TG {latest['Talkgroup']} | "
            f"**{latest['Call Type']}** ({latest['Priority']})"
        )
        st.write(f"**Address/Cross:** {latest['Address/Cross']}")
        st.write(f"**Ten-codes:** {latest['Ten-codes']}")
        st.write(f"**Units/Callsigns:** {latest['Units/Callsigns']}")
        st.write(f"**Transcript:** {latest['Transcript']}")
        render_audio(str(latest.get("audio_url", "")), key=f"latest-{latest['call_id']}")

        major_df = df[df["Priority"].isin(["ECHO", "DELTA"])].copy()
        st.subheader("Major Calls (ECHO / DELTA)")
        if major_df.empty:
            st.caption("No major calls in current window.")
        else:
            st.dataframe(
                major_df[["Time", "Talkgroup", "Call Type", "Priority", "Address/Cross", "Ten-codes", "Units/Callsigns", "Transcript"]],
                use_container_width=True,
                hide_index=True,
            )

        st.subheader("Recent Calls")
        st.dataframe(
            df[["Time", "Talkgroup", "Call Type", "Priority", "Address/Cross", "Ten-codes", "Units/Callsigns", "Transcript"]],
            use_container_width=True,
            hide_index=True,
        )

        st.subheader("Audio")
        for _, row in df.head(10).iterrows():
            st.markdown(f"**{row['Time']} | {row['Call Type']} | {row['Priority']}**")
            render_audio(str(row.get("audio_url", "")), key=f"audio-{row['call_id']}")

    time.sleep(5)
    st.rerun()


if __name__ == "__main__":
    main()
