# -*- coding: utf-8 -*-
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from fastapi import FastAPI, Request

VERSION = "1.4-no422"

app = FastAPI(title="Danecom CAD Extractor", version=VERSION)

TRANSCRIPT_KEYS = ["transcript", "text", "transcription", "raw_transcription", "input"]

STREET_SUFFIXES = (
    "st",
    "street",
    "ave",
    "avenue",
    "blvd",
    "boulevard",
    "rd",
    "road",
    "dr",
    "drive",
    "ln",
    "lane",
    "ct",
    "court",
    "hwy",
    "highway",
    "pkwy",
    "parkway",
    "trl",
    "trail",
    "way",
    "cir",
    "circle",
)

CALL_TYPE_RULES: List[Tuple[str, List[str]]] = [
    ("Pursuit", ["pursuit", "flee", "fled", "elude", "eluding", "chase", "running from"]),
    ("Domestic", ["domestic", "family trouble"]),
    ("Crash", ["crash", "accident", "mvc", "rollover", "vehicle into"]),
    ("Fire", ["fire", "smoke", "structure fire", "working fire"]),
    ("Shooting", ["shooting", "shots fired", "gunfire"]),
    ("Alarm", ["alarm", "burg alarm", "fire alarm"]),
    ("Burglary", ["burglary", "break-in", "b and e"]),
    ("Traffic Stop", ["traffic stop", "vehicle stop"]),
    ("Medical", ["medical", "ems", "unconscious", "overdose"]),
    ("Suspicious", ["suspicious", "prowler", "unknown trouble"]),
]

PRIORITY_WORDS = {
    "ECHO": ["officer down", "active shooter", "critical", "immediate life threat"],
    "DELTA": ["pursuit", "flee", "elude", "shots fired", "weapon", "armed", "high speed"],
    "CHARLIE": ["injury", "domestic", "burglary in progress", "assault"],
    "BRAVO": ["alarm", "suspicious", "fight", "disturbance"],
    "ALPHA": ["traffic stop", "welfare check", "property damage"],
}


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return " ".join(value.replace("\n", " ").split())
    return " ".join(str(value).split())


def _extract_transcript(payload: Any) -> str:
    if isinstance(payload, dict):
        for key in TRANSCRIPT_KEYS:
            if key in payload:
                text = _clean_text(payload.get(key))
                if text:
                    return text
        # fallback: any nested likely field
        for value in payload.values():
            if isinstance(value, str) and value.strip():
                return _clean_text(value)
    elif isinstance(payload, str):
        return _clean_text(payload)
    return ""


def _extract_ten_codes(text: str) -> List[str]:
    codes = {f"10-{m}" for m in re.findall(r"\b10[-\s]?(\d{2})\b", text, re.IGNORECASE)}
    return sorted(codes)


def _extract_intersection(text: str) -> str:
    patterns = [
        r"\b([A-Z][A-Za-z]+(?:\s[A-Z][A-Za-z]+){0,2})\s+at\s+([A-Z][A-Za-z]+(?:\s[A-Z][A-Za-z]+){0,2})\b",
        r"\b([A-Z][A-Za-z]+(?:\s[A-Z][A-Za-z]+){0,2})\s+and\s+([A-Z][A-Za-z]+(?:\s[A-Z][A-Za-z]+){0,2})\b",
    ]
    for pat in patterns:
        match = re.search(pat, text)
        if match:
            return f"{match.group(1)} & {match.group(2)}"
    return ""


def _extract_address(text: str) -> str:
    pattern = (
        r"\b(\d{3,6})\s+([A-Z][A-Za-z0-9'\-]+(?:\s[A-Z][A-Za-z0-9'\-]+){0,4})\s+"
        r"(" + "|".join(STREET_SUFFIXES) + r")\b"
    )
    match = re.search(pattern, text, re.IGNORECASE)
    if not match:
        return ""
    return f"{match.group(1)} {match.group(2)} {match.group(3)}"


def _extract_roads(text: str) -> List[str]:
    roads: set[str] = set()

    belt_mineral = re.findall(r"\b(beltline|mineral\s+point)\b", text, re.IGNORECASE)
    for r in belt_mineral:
        roads.add(" ".join(w.capitalize() for w in r.split()))

    generic_patterns = [
        r"\b([A-Z][A-Za-z0-9'\-]+(?:\s[A-Z][A-Za-z0-9'\-]+){0,3}\s(?:Road|Rd|Street|St|Avenue|Ave|Lane|Ln|Drive|Dr|Boulevard|Blvd|Highway|Hwy|Parkway|Pkwy|Trail|Trl))\b",
        r"\b(Highway\s\d+[A-Z]?)\b",
        r"\b(Hwy\s\d+[A-Z]?)\b",
        r"\b([A-Z][A-Za-z0-9'\-]+\sRoad)\b",
    ]
    for pat in generic_patterns:
        for match in re.findall(pat, text):
            roads.add(match.strip())

    return sorted(roads)


def _extract_plate_numbers(text: str) -> set[str]:
    plates: set[str] = set()
    for m in re.finditer(r"\bplate(?:\s+of)?\s+([A-Z0-9\-]{2,10})\b", text, re.IGNORECASE):
        token = m.group(1)
        digits = re.findall(r"\d{3,6}", token)
        for d in digits:
            plates.add(d)
    return plates


def _extract_units(text: str, address: str) -> Tuple[List[str], List[str]]:
    address_num = re.match(r"\b(\d{3,6})\b", address)
    blocked_numbers = set()
    if address_num:
        blocked_numbers.add(address_num.group(1))

    blocked_numbers.update(_extract_plate_numbers(text))

    blocked_spans: List[Tuple[int, int]] = []
    for m in re.finditer(r"\b\d{3,6}\s+[A-Za-z0-9\-\s]{1,30}\b(?:" + "|".join(STREET_SUFFIXES) + r")\b", text, re.IGNORECASE):
        blocked_spans.append((m.start(), m.end()))

    units: set[str] = set()

    for m in re.finditer(r"\b([1-9]\d{3})\b", text):
        num = m.group(1)
        if num in blocked_numbers:
            continue
        if any(start <= m.start() <= end for start, end in blocked_spans):
            continue
        units.add(num)

    callsigns = set(units)

    # Capture alpha-numeric callsigns like CAR12, A21, MED5
    for m in re.finditer(r"\b([A-Z]{1,4}[\-\s]?\d{1,3})\b", text, re.IGNORECASE):
        token = m.group(1).upper().replace(" ", "")
        if token.startswith("10"):
            continue
        callsigns.add(token)

    return sorted(units), sorted(callsigns)


def _classify_call_type(text: str) -> str:
    lowered = text.lower()
    for call_type, keywords in CALL_TYPE_RULES:
        if any(keyword in lowered for keyword in keywords):
            return call_type
    return "General"


def _classify_priority(text: str, call_type: str) -> str:
    lowered = text.lower()
    priority = "ROUTINE"
    for level in ["ECHO", "DELTA", "CHARLIE", "BRAVO", "ALPHA"]:
        if any(keyword in lowered for keyword in PRIORITY_WORDS[level]):
            priority = level
            break

    if call_type == "Pursuit" and priority not in {"ECHO", "DELTA"}:
        priority = "DELTA"

    return priority


def extract_fields(transcript: str) -> Dict[str, Any]:
    text = _clean_text(transcript)
    address = _extract_address(text)
    cross = _extract_intersection(text)
    roads = _extract_roads(text)
    ten_codes = _extract_ten_codes(text)
    units, callsigns = _extract_units(text, address)
    call_type = _classify_call_type(text)
    priority = _classify_priority(text, call_type)

    location_text = ""
    if address and cross:
        location_text = f"{address} ({cross})"
    elif address:
        location_text = address
    elif cross:
        location_text = cross
    elif roads:
        location_text = roads[0]

    return {
        "provider": "local_rules",
        "call_type": call_type,
        "priority": priority,
        "address": address,
        "cross_streets": cross,
        "location_text": location_text,
        "roads": roads,
        "ten_codes": ten_codes,
        "units": units,
        "callsigns": callsigns,
        "summary": call_type,
    }


@app.get("/health")
def health() -> Dict[str, bool]:
    return {"ok": True}


@app.get("/version")
def version() -> Dict[str, str]:
    return {"version": VERSION}


@app.post("/extract")
async def extract(request: Request) -> Dict[str, Any]:
    payload: Any = {}
    try:
        payload = await request.json()
    except Exception:
        try:
            body = await request.body()
            payload = body.decode("utf-8", errors="ignore")
        except Exception:
            payload = {}

    transcript = _extract_transcript(payload)
    result = extract_fields(transcript)
    return result
