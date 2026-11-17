"""Lesson progress on disk (~/.config/botstudio/lessons.json). Only plain data is stored; node ids are re-resolved from the
canvas when a lesson resumes, so progress survives restarts and edits."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def _path() -> Path:
    if sys.platform.startswith("win"):
        base = Path(os.environ.get("APPDATA", Path.home()))
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    d = base / "botstudio"
    d.mkdir(parents=True, exist_ok=True)
    return d / "lessons.json"


def load() -> dict:
    try:
        return json.loads(_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save(data: dict) -> None:
    try:
        _path().write_text(json.dumps(data, indent=2), encoding="utf-8")
    except OSError:
        pass


def get(lesson_id: str) -> dict:
    return load().get(lesson_id, {"step": 0, "done": False, "ctx": {}})


def put(lesson_id: str, step: int, done: bool, ctx: dict) -> None:
    d = load()
    d[lesson_id] = {"step": step, "done": done or d.get(lesson_id, {}).get("done", False),
                    "ctx": {k: v for k, v in ctx.items() if isinstance(v, (str, int, float, bool))}}
    save(d)


def reset(lesson_id: str) -> None:
    d = load()
    d.pop(lesson_id, None)
    save(d)
