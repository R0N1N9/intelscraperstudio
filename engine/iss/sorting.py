"""Sorting a shoot into collections:

  Stock               technically clean photos and clips that stock agencies accept
  Portfolio & print   the shoot's standout photos: very sharp, clean, high resolution, strong impact

Every decision carries a short reason, and you can override any of them.
"""
from __future__ import annotations

import csv
import math
import os
import re
import shutil
from pathlib import Path
from typing import Callable

from . import config, db, media

COLLECTIONS = {"stock": "Stock", "portfolio": "Portfolio & print"}
Progress = Callable[[int, int, str], None]


def _noop(done: int, total: int, msg: str) -> None:
    pass


# ---------------------------------------------------------------- RAW + JPEG pairs

def pair_companions(shoot_id: int) -> int:
    """A camera shooting RAW+JPEG writes DSC01234.ARW and DSC01234.JPG. Treat them as one photo."""
    with db.session() as con:
        ps = db.rows(con.execute("SELECT id, path, src_name, companion_of FROM clips WHERE shoot_id=? AND kind='photo'",
                                 (shoot_id,)))
        by_stem: dict[tuple[str, str], list[dict]] = {}
        for p in ps:
            by_stem.setdefault((str(Path(p["path"]).parent), Path(p["src_name"]).stem.upper()), []).append(p)
        n = 0
        for group in by_stem.values():
            raws = [p for p in group if Path(p["src_name"]).suffix.lower() in media.RAW_EXT]
            others = [p for p in group if p not in raws]
            if raws and others:
                for o in others:
                    if o["companion_of"] != raws[0]["id"]:
                        con.execute("UPDATE clips SET companion_of=?, status='analyzed' WHERE id=?", (raws[0]["id"], o["id"]))
                        n += 1
        return n


# ---------------------------------------------------------------- rules

def print_size(w: int | None, h: int | None) -> dict | None:
    if not w or not h:
        return None
    return {"in_300": [round(w / 300, 1), round(h / 300, 1)], "in_240": [round(w / 240, 1), round(h / 240, 1)],
            "cm_300": [round(w / 300 * 2.54), round(h / 300 * 2.54)]}


def _fmt_print(p: dict) -> str:
    a, b = p["in_300"]
    c, d = p["in_240"]
    return f"Prints sharp up to {a:g}×{b:g} in at 300 dpi ({c:g}×{d:g} in at 240 dpi)"


def photo_rules(c: dict) -> tuple[bool, str, bool, str]:
    m = c["metrics"] or {}
    mp = c.get("megapixels") or 0
    stock_fail = []
    if c["verdict"] == "reject":
        stock_fail.append("rejected on quality")
    if c.get("similar_to"):
        stock_fail.append("a better frame of this burst was picked")
    if m.get("focus", 0) < 75:
        stock_fail.append("not sharp enough (agencies check at 100%)")
    if m.get("light", 0) < 70:
        stock_fail.append("exposure needs work")
    if m.get("noise", 0) < 60:
        stock_fail.append("too noisy")
    if mp and mp < 4:
        stock_fail.append("under 4 MP")
    t = m.get("tilt_deg")
    if t is not None and 0.8 <= abs(t) <= 12:
        stock_fail.append(f"horizon tilted {abs(t):.1f}°, straighten it first")
    stock = not stock_fail
    stock_note = (f"Clean and sharp at {mp:g} MP. Ready for stock" if mp else "Clean and sharp. Ready for stock") \
        if stock else "Not for stock: " + "; ".join(stock_fail)

    port_fail = []
    if c["verdict"] == "reject" or c.get("similar_to"):
        port_fail.append("not the best frame" if c.get("similar_to") else "rejected on quality")
    if m.get("focus", 0) < 80:
        port_fail.append("subject not critically sharp")
    if m.get("light", 0) < 75:
        port_fail.append("exposure not strong enough")
    if m.get("noise", 0) < 55:
        port_fail.append("too noisy to print large")
    if m.get("impact", 0) < 55:
        port_fail.append("low visual impact")
    if mp and mp < 12:
        port_fail.append("under 12 MP for large prints")
    if t is not None and 0.8 <= abs(t) <= 12:
        port_fail.append(f"horizon tilted {abs(t):.1f}°")
    candidate = not port_fail
    port_note = "Candidate" if candidate else "Not portfolio: " + "; ".join(port_fail)
    return stock, stock_note, candidate, port_note


def video_rules(c: dict) -> tuple[bool, str]:
    m = c["metrics"] or {}
    fail = []
    if c["verdict"] == "reject":
        fail.append("rejected on quality")
    if c.get("similar_to"):
        fail.append("a better take of this shot was picked")
    if m.get("steady", 0) < 80:
        fail.append("not steady enough")
    if m.get("focus", 0) < 70:
        fail.append("not sharp enough")
    if m.get("light", 0) < 70:
        fail.append("exposure needs work")
    if (c.get("height") or 0) < 1080 and (c.get("width") or 0) < 1920:
        fail.append("below 1080p")
    if (c.get("duration") or 0) < 5:
        fail.append("shorter than 5 s")
    if fail:
        return False, "Not for stock: " + "; ".join(fail)
    tips = []
    if (c.get("duration") or 0) > 60:
        tips.append("trim to the best 5–60 s (best moment is marked)")
    if media.is_log(c.get("color_profile")):
        tips.append(f"grade from {c['color_profile']} before submitting")
    res = "4K" if (c.get("width") or 0) >= 3840 else "HD"
    return True, f"Steady, sharp {res} clip. Ready for stock" + (f": {'; '.join(tips)}" if tips else "")


def classify_shoot(shoot_id: int) -> dict:
    """Recompute Stock / Portfolio for every analyzed item in a shoot."""
    share = float(config.load().get("portfolio_share", 0.15))
    with db.session() as con:
        items = db.rows(con.execute("SELECT * FROM clips WHERE shoot_id=? AND status='analyzed' AND companion_of IS NULL "
                                    "AND metrics IS NOT NULL", (shoot_id,)))
        photos = [c for c in items if c["kind"] == "photo"]
        cands = []
        out = {}
        for c in items:
            if c["kind"] == "photo":
                stock, sn, cand, pn = photo_rules(c)
                uses = {"stock": stock, "portfolio": False, "notes": {"stock": sn, "portfolio": pn},
                        "print": print_size(c.get("width"), c.get("height"))}
                if cand:
                    cands.append((c, uses))
            else:
                stock, sn = video_rules(c)
                uses = {"stock": stock, "portfolio": None, "notes": {"stock": sn, "portfolio": "Portfolio picks are photos"}}
            out[c["id"]] = uses
        # Portfolio is selective: only the strongest candidates of the shoot.
        quota = max(1, math.ceil(share * len(photos))) if photos else 0
        cands.sort(key=lambda cu: -(0.6 * (cu[0]["metrics"] or {}).get("impact", 0) + 0.4 * (cu[0]["score"] or 0)))
        for i, (c, uses) in enumerate(cands):
            if i < quota:
                uses["portfolio"] = True
                p = uses.get("print")
                uses["notes"]["portfolio"] = ("One of this shoot's strongest frames. " + (_fmt_print(p) if p else "")).strip()
            else:
                uses["notes"]["portfolio"] = "Strong, but not among this shoot's top picks"
        for cid, uses in out.items():
            con.execute("UPDATE clips SET uses=? WHERE id=?", (db.dumps(uses), cid))
    return {"stock": sum(1 for u in out.values() if u["stock"]), "portfolio": sum(1 for u in out.values() if u["portfolio"])}


def effective(c: dict) -> dict:
    """The app's call, with your overrides applied."""
    uses = c.get("uses") or {}
    mine = c.get("user_uses") or {}
    return {k: (mine[k] if k in mine else bool(uses.get(k))) for k in COLLECTIONS}


# ---------------------------------------------------------------- export

def _safe(name: str) -> str:
    return re.sub(r'[<>:"/\\|?*]+', "-", name).strip(" .") or "Shoot"


def _place(src: Path, dest: Path) -> str:
    """Hard-link when on the same drive (no extra space), otherwise copy."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        return "exists"
    try:
        os.link(src, dest)
        return "linked"
    except OSError:
        shutil.copy2(src, dest)
        return "copied"


def collections_root() -> Path:
    cfg = config.load()
    return Path(cfg.get("collections_root") or Path(cfg["library_root"]) / "Collections")


def export_collection(name: str, shoot_id: int | None = None, progress: Progress = _noop) -> dict:
    if name not in COLLECTIONS:
        raise ValueError(f"Unknown collection: {name}")
    with db.session() as con:
        q = "SELECT c.*, s.name AS shoot_name FROM clips c JOIN shoots s ON s.id=c.shoot_id WHERE c.companion_of IS NULL"
        args: tuple = ()
        if shoot_id:
            q += " AND c.shoot_id=?"
            args = (shoot_id,)
        items = [c for c in db.rows(con.execute(q + " ORDER BY c.recorded_at", args)) if effective(c)[name]]
        companions = {}
        for r in db.rows(con.execute("SELECT companion_of, path FROM clips WHERE companion_of IS NOT NULL")):
            companions.setdefault(r["companion_of"], []).append(r["path"])
    root = collections_root() / COLLECTIONS[name].replace(" & ", " and ")
    written = {"linked": 0, "copied": 0, "exists": 0}
    rows = []
    for i, c in enumerate(items):
        progress(i, len(items), f"Collecting {c['src_name']}")
        folder = root / _safe(c["shoot_name"])
        for path in [c["path"], *companions.get(c["id"], [])]:
            if Path(path).exists():
                written[_place(Path(path), folder / Path(path).name)] += 1
        rows.append(c)
    if rows:
        for shoot_name in {c["shoot_name"] for c in rows}:
            folder = root / _safe(shoot_name)
            mine = [c for c in rows if c["shoot_name"] == shoot_name]
            with open(folder / "details.csv", "w", newline="", encoding="utf-8") as f:
                w = csv.writer(f)
                w.writerow(["Filename", "Type", "Camera", "Recorded", "Latitude", "Longitude", "Width", "Height",
                            "Duration (s)", "Score", "Why"])
                for c in mine:
                    note = ((c.get("uses") or {}).get("notes") or {}).get(name, "")
                    if (c.get("user_uses") or {}).get(name) is True:
                        note = "Your pick" + (f" (app's note: {note})" if note and not (c.get("uses") or {}).get(name) else "")
                    w.writerow([c["src_name"], c["kind"], c["camera"], c["recorded_at"], c["lat"], c["lon"], c["width"],
                                c["height"], c["duration"], c["score"], note])
            if name == "stock":  # the column layout stock agencies' CSV uploaders expect
                with open(folder / "stock_metadata.csv", "w", newline="", encoding="utf-8") as f:
                    w = csv.writer(f)
                    w.writerow(["Filename", "Title", "Keywords", "Category", "Releases"])
                    for c in mine:
                        w.writerow([c["src_name"], "", "", "", ""])
    progress(len(items), len(items), f"Collected {len(items)} items")
    return {"collection": name, "items": len(items), "folder": str(root), **written}
