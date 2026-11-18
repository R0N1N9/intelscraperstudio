"""User settings, stored as JSON in the app folder (~/.intelscraperstudio by default)."""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path

_lock = threading.Lock()


def app_dir() -> Path:
    d = Path(os.environ.get("ISS_HOME", Path.home() / ".intelscraperstudio"))
    d.mkdir(parents=True, exist_ok=True)
    return d


def _defaults() -> dict:
    videos = Path.home() / "Videos" / "intelscraperstudio"
    return {
        # Where imported footage is copied. Originals on the card are never changed.
        "library_root": str(videos / "Library"),
        # Second copy for safety (e.g. another drive). Empty string = no backup copy.
        "backup_root": "",
        # Re-read every copy after writing to confirm it matches the card byte for byte.
        "verify_copies": True,
        # Small 540p preview files for smooth playback in the review screen.
        "make_proxies": True,
        # Music folders that have been imported.
        "music_folders": [],
        # Clips recorded more than this many hours apart start a new shoot.
        "shoot_gap_hours": 6,
        # Start importing as soon as a camera card with new files is plugged in.
        "auto_import": False,
        # Share of a shoot's qualifying photos picked for Portfolio & print (the best ones).
        "portfolio_share": 0.15,
        # Where exported Stock / Portfolio collections go. Empty = <library>/Collections.
        "collections_root": "",
    }


def config_path() -> Path:
    return app_dir() / "config.json"


def load() -> dict:
    with _lock:
        cfg = _defaults()
        p = config_path()
        if p.exists():
            try:
                cfg.update(json.loads(p.read_text(encoding="utf-8")))
            except (json.JSONDecodeError, OSError):
                pass
        return cfg


def save(updates: dict) -> dict:
    cfg = load()
    allowed = set(_defaults())
    cfg.update({k: v for k, v in updates.items() if k in allowed})
    with _lock:
        config_path().write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    return cfg


def cache_dir(kind: str) -> Path:
    """Generated files (thumbnails, proxies) live in the app folder, not the library."""
    d = app_dir() / kind
    d.mkdir(parents=True, exist_ok=True)
    return d
