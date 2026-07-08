"""First-party My Day usage telemetry — click + view events, user-attributed.

Purpose: give an audit (Ram) the data to see what people actually click on My
Day, so we can hide/simplify what's unused and stop overwhelming the user.

Privacy-scoped for an internal, authenticated tool:
  - We record only a stable control label (from ``data-track``) and the view /
    filter STATE (view, tier, project, list, person) plus the page path.
  - We NEVER record form values, keystrokes, cursor coordinates, or third-party
    fingerprints. The user is already known from the session, so nothing here
    performs anonymous identification.

Storage: append-only JSONL per user per UTC day under
``OUTPUT_DIR/usage_events/<user>/<YYYY-MM-DD>.jsonl`` — writes are cheap and never
clobber prior events. ``aggregate()`` rolls a window into the numbers the usage
report shows. All inputs are sanitized; recording never raises (telemetry must
not break the page).
"""
from __future__ import annotations

import datetime as dt
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any, Iterator

from config.settings import OUTPUT_DIR

EVENTS_DIR = OUTPUT_DIR / "usage_events"
ALLOWED_TYPES = {"view", "click"}
MAX_EVENTS_PER_BATCH = 50
_LABEL_RE = re.compile(r"[^A-Za-z0-9._:/\- ]")


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (s or "anon").lower()).strip("_") or "anon"


def _clean(value: Any, limit: int) -> str:
    return _LABEL_RE.sub("", str(value or ""))[:limit]


def _user_file(user_key: str, day: str) -> Path:
    d = EVENTS_DIR / _slug(user_key)
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{day}.jsonl"


def record_events(user_key: str, events: Any, *, now: dt.datetime | None = None) -> int:
    """Append a sanitized client batch (server stamps user + timestamp).

    Returns the number of events written. Never raises — bad input is dropped.
    """
    if not isinstance(events, list):
        return 0
    now = now or dt.datetime.now(dt.timezone.utc)
    ts = now.isoformat()
    rows: list[dict] = []
    for ev in events[:MAX_EVENTS_PER_BATCH]:
        if not isinstance(ev, dict):
            continue
        etype = ev.get("type")
        if etype not in ALLOWED_TYPES:
            continue
        rows.append({
            "ts": ts,
            "user": _clean(user_key, 200),
            "type": etype,
            "label": _clean(ev.get("label"), 80),
            "view": _clean(ev.get("view"), 20),
            "tier": _clean(ev.get("tier"), 20),
            "project": _clean(ev.get("project"), 40),
            "list": _clean(ev.get("list"), 40),
            "person": _clean(ev.get("person"), 60),
            "path": _clean(ev.get("path"), 120),
        })
    if not rows:
        return 0
    try:
        path = _user_file(user_key, now.date().isoformat())
        with open(path, "a", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
    except OSError:
        return 0
    return len(rows)


def _iter_events(since_days: int, now: dt.datetime | None = None) -> Iterator[dict]:
    now = now or dt.datetime.now(dt.timezone.utc)
    cutoff = (now - dt.timedelta(days=since_days)).date()
    if not EVENTS_DIR.exists():
        return
    for user_dir in EVENTS_DIR.iterdir():
        if not user_dir.is_dir():
            continue
        for f in user_dir.glob("*.jsonl"):
            try:
                day = dt.date.fromisoformat(f.stem)
            except ValueError:
                continue
            if day < cutoff:
                continue
            try:
                lines = f.read_text(encoding="utf-8").splitlines()
            except OSError:
                continue
            for line in lines:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    continue


def aggregate(since_days: int = 14, now: dt.datetime | None = None) -> dict:
    """Roll events into the audit numbers: totals, unique users, view + tier
    distribution, whether project/list/person filters get used at all, the top
    clicked controls, and per-day volume."""
    total = 0
    users: set[str] = set()
    views: Counter = Counter()
    tiers: Counter = Counter()
    clicks: Counter = Counter()
    per_day: Counter = Counter()
    filter_use = {"project": 0, "list": 0, "person": 0}
    for ev in _iter_events(since_days, now=now):
        total += 1
        if ev.get("user"):
            users.add(ev["user"])
        per_day[(ev.get("ts") or "")[:10]] += 1
        if ev.get("type") == "view":
            views[ev.get("view") or "briefing"] += 1
            if ev.get("tier"):
                tiers[ev["tier"]] += 1
            for key in filter_use:
                if ev.get(key):
                    filter_use[key] += 1
        elif ev.get("type") == "click" and ev.get("label"):
            clicks[ev["label"]] += 1
    return {
        "since_days": since_days,
        "total_events": total,
        "unique_users": len(users),
        "views": dict(views.most_common()),
        "tiers": dict(tiers.most_common()),
        "top_controls": dict(clicks.most_common(30)),
        "filter_use": filter_use,
        "per_day": dict(sorted(per_day.items())),
    }
