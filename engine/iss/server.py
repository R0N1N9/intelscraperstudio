"""Local web server: the app's UI talks to the engine through these endpoints.

It listens on 127.0.0.1 only, so nothing is reachable from other computers.
"""
from __future__ import annotations

import os
import shutil
import string
import subprocess
import sys
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import __version__, analyze, config, db, ingest, jobs, media, music, sorting

WEB = Path(__file__).resolve().parents[2] / "web"

_active_imports: set[str] = set()
_leftover: dict[str, int] = {}  # files still "new" after an import (exact duplicates under another name)
_stop = threading.Event()


def _import_job(src: Path) -> dict:
    key = str(src)
    _active_imports.add(key)

    def work(progress):
        try:
            res = ingest.import_source(src, progress)
            res["analysis"] = analyze.analyze_many(res["clip_ids"], progress)
            return res
        finally:
            try:
                _leftover[key] = ingest.describe_source(src)["new_files"]
            except Exception:
                pass
            _active_imports.discard(key)

    return jobs.submit("import", f"Importing {src.name or src}", work)


def _watch_cards() -> None:
    """With auto-import on, a card with new files starts importing as soon as it's plugged in."""
    while not _stop.wait(5):
        try:
            if not config.load().get("auto_import"):
                continue
            for src in ingest.find_sources():
                if src["new_files"] > _leftover.get(src["path"], 0) and src["path"] not in _active_imports:
                    _import_job(Path(src["path"]))
        except Exception:
            pass


@asynccontextmanager
async def _lifespan(_app):
    db.init()
    _stop.clear()
    threading.Thread(target=_watch_cards, daemon=True, name="card-watcher").start()
    yield
    _stop.set()


app = FastAPI(title="intelscraperstudio", version=__version__, lifespan=_lifespan)


def _eff(c: dict) -> str | None:
    return c.get("user_verdict") or c.get("verdict")


# ---------------------------------------------------------------- status & settings

def _gpu() -> dict | None:
    exe = shutil.which("nvidia-smi")
    if not exe:
        return None
    try:
        out = media.run([exe, "--query-gpu=name,utilization.gpu,memory.used,memory.total",
                         "--format=csv,noheader,nounits"], timeout=5).strip().splitlines()[0]
        name, util, used, total = [x.strip() for x in out.split(",")]
        return {"name": name, "util": int(util), "mem_used": int(used), "mem_total": int(total)}
    except Exception:
        return None


@app.get("/api/status")
def status():
    cfg = config.load()
    lib = Path(cfg["library_root"])
    probe = lib if lib.exists() else lib.anchor or Path.home()
    try:
        du = shutil.disk_usage(probe)
        disk = {"used": du.used, "total": du.total, "free": du.free}
    except OSError:
        disk = None
    with db.session() as con:
        counts = dict(con.execute("SELECT COUNT(*) AS clips, COALESCE(SUM(src_size),0) AS bytes FROM clips").fetchone())
        counts["shoots"] = con.execute("SELECT COUNT(*) FROM shoots").fetchone()[0]
        counts["tracks"] = con.execute("SELECT COUNT(*) FROM tracks WHERE status='analyzed'").fetchone()[0]
    return {"version": __version__, "platform": sys.platform, "gpu": _gpu(), "disk": disk, "counts": counts,
            "ffmpeg": bool(shutil.which("ffmpeg") and shutil.which("ffprobe")), "config": cfg}


class Settings(BaseModel):
    library_root: str | None = None
    backup_root: str | None = None
    verify_copies: bool | None = None
    make_proxies: bool | None = None
    shoot_gap_hours: float | None = None
    auto_import: bool | None = None
    portfolio_share: float | None = None
    collections_root: str | None = None


@app.put("/api/config")
def put_config(s: Settings):
    vals = {k: v for k, v in s.model_dump().items() if v is not None}
    if "portfolio_share" in vals:
        vals["portfolio_share"] = min(0.5, max(0.02, vals["portfolio_share"]))
    cfg = config.save(vals)
    if "portfolio_share" in vals:  # re-rank existing shoots with the new share
        with db.session() as con:
            ids = [r[0] for r in con.execute("SELECT id FROM shoots")]
        for sid in ids:
            sorting.classify_shoot(sid)
    return cfg


# ---------------------------------------------------------------- folders

@app.get("/api/fs")
def browse(path: str | None = None):
    """List folders so the UI can offer a folder picker."""
    if not path:
        roots = []
        if sys.platform.startswith("win"):
            roots += [{"name": f"{d}:\\", "path": f"{d}:\\"} for d in string.ascii_uppercase if os.path.exists(f"{d}:\\")]
        else:
            roots.append({"name": "/", "path": "/"})
        home = Path.home()
        for p in (home, home / "Music", home / "Videos", home / "Desktop", home / "Downloads"):
            if p.is_dir():
                roots.append({"name": p.name if p != home else "Home", "path": str(p)})
        return {"path": "", "parent": None, "dirs": roots}
    p = Path(path)
    if not p.is_dir():
        raise HTTPException(404, f"Folder not found: {path}")
    try:
        dirs = sorted((d for d in p.iterdir() if d.is_dir() and not d.name.startswith((".", "$"))),
                      key=lambda d: d.name.lower())
    except PermissionError:
        dirs = []
    audio = sum(1 for f in p.iterdir() if f.is_file() and f.suffix.lower() in music.AUDIO_EXT) if p.is_dir() else 0
    vids = sum(1 for f in p.iterdir() if f.is_file() and f.suffix.lower() in media.VIDEO_EXT) if p.is_dir() else 0
    parent = str(p.parent) if p.parent != p else ""
    return {"path": str(p), "parent": parent, "dirs": [{"name": d.name, "path": str(d)} for d in dirs[:500]],
            "audio_files": audio, "video_files": vids}


@app.get("/api/sources")
def sources():
    return ingest.find_sources()


# ---------------------------------------------------------------- jobs

class PathIn(BaseModel):
    path: str


@app.post("/api/import")
def start_import(body: PathIn):
    src = Path(body.path)
    if not src.is_dir():
        raise HTTPException(400, f"Folder not found: {body.path}")

    if str(src) in _active_imports:
        raise HTTPException(409, "That folder is already being imported.")
    return _import_job(src)


@app.post("/api/music/import")
def start_music(body: PathIn):
    src = Path(body.path)
    if not src.is_dir():
        raise HTTPException(400, f"Folder not found: {body.path}")
    return jobs.submit("music", f"Music · {src.name}", lambda p: music.import_folder(src, p), pool="music")


@app.post("/api/shoots/{sid}/analyze")
def reanalyze(sid: int):
    with db.session() as con:
        ids = [r[0] for r in con.execute("SELECT id FROM clips WHERE shoot_id=? AND kind='video'", (sid,))]
    return jobs.submit("analyze", "Re-analyzing shoot", lambda p: {"analysis": analyze.analyze_many(ids, p)})


@app.get("/api/jobs")
def list_jobs():
    return jobs.recent()


@app.get("/api/jobs/{jid}")
def get_job(jid: str):
    j = jobs.get(jid)
    if not j:
        raise HTTPException(404, "No such job")
    return j


# ---------------------------------------------------------------- shoots & clips

@app.get("/api/shoots")
def list_shoots():
    with db.session() as con:
        shoots = db.rows(con.execute("SELECT * FROM shoots ORDER BY started_at DESC"))
        for s in shoots:
            cs = db.rows(con.execute("SELECT id, kind, camera, duration, score, verdict, user_verdict, status, thumb, "
                                     "uses, user_uses FROM clips WHERE shoot_id=? AND companion_of IS NULL", (s["id"],)))
            vids = [c for c in cs if c["kind"] == "video"]
            s["clips"] = len(vids)
            s["photos"] = len(cs) - len(vids)
            s["cameras"] = sorted({c["camera"] for c in cs})
            s["keep"] = sum(1 for c in cs if _eff(c) == "keep")
            s["review"] = sum(1 for c in cs if _eff(c) == "review")
            s["reject"] = sum(1 for c in cs if _eff(c) == "reject")
            s["pending"] = sum(1 for c in cs if c["status"] in ("imported", "analyzing"))
            s["minutes"] = round(sum(c["duration"] or 0 for c in vids) / 60, 1)
            eff = [sorting.effective(c) for c in cs]
            s["stock"] = sum(1 for e in eff if e["stock"])
            s["portfolio"] = sum(1 for e in eff if e["portfolio"])
            best = max((c for c in cs if c["thumb"]), key=lambda c: (c["score"] or 0) + 0.3 * (1 if c["kind"] == "photo" else 0),
                       default=None)
            s["cover"] = best["id"] if best else None
    return shoots


class ShootIn(BaseModel):
    name: str


@app.patch("/api/shoots/{sid}")
def rename_shoot(sid: int, body: ShootIn):
    with db.session() as con:
        con.execute("UPDATE shoots SET name=? WHERE id=?", (body.name.strip()[:120] or "Untitled shoot", sid))
    return {"ok": True}


@app.get("/api/shoots/{sid}/clips")
def shoot_clips(sid: int):
    with db.session() as con:
        cs = db.rows(con.execute("SELECT * FROM clips WHERE shoot_id=? ORDER BY recorded_at", (sid,)))
    pairs = {c["companion_of"]: c["src_name"] for c in cs if c["companion_of"]}
    out = []
    for c in cs:
        if c["companion_of"]:
            continue
        c["final"] = _eff(c)
        c.update(sorting.effective(c))
        c["companion"] = pairs.get(c["id"])
        c.pop("hash", None)
        out.append(c)
    return out


class VerdictIn(BaseModel):
    verdict: str | None  # keep | reject | None to go back to the engine's call


@app.post("/api/clips/{cid}/verdict")
def set_verdict(cid: int, body: VerdictIn):
    if body.verdict not in ("keep", "reject", None):
        raise HTTPException(400, "verdict must be keep, reject or null")
    with db.session() as con:
        con.execute("UPDATE clips SET user_verdict=? WHERE id=?", (body.verdict, cid))
    return {"ok": True}


class UseIn(BaseModel):
    collection: str          # stock | portfolio
    value: bool | None       # True / False to overrule the app, None to go back to its call


@app.post("/api/clips/{cid}/uses")
def set_use(cid: int, body: UseIn):
    if body.collection not in sorting.COLLECTIONS:
        raise HTTPException(400, "collection must be stock or portfolio")
    with db.session() as con:
        r = db.row(con.execute("SELECT user_uses FROM clips WHERE id=?", (cid,)).fetchone())
        if not r:
            raise HTTPException(404, "No such item")
        mine = r["user_uses"] or {}
        if body.value is None:
            mine.pop(body.collection, None)
        else:
            mine[body.collection] = body.value
        con.execute("UPDATE clips SET user_uses=? WHERE id=?", (db.dumps(mine) if mine else None, cid))
    return {"ok": True}


class ExportIn(BaseModel):
    collection: str
    shoot_id: int | None = None


@app.post("/api/export")
def export(body: ExportIn):
    if body.collection not in sorting.COLLECTIONS:
        raise HTTPException(400, "collection must be stock or portfolio")
    title = f"Exporting {sorting.COLLECTIONS[body.collection]}"
    return jobs.submit("export", title, lambda p: sorting.export_collection(body.collection, body.shoot_id, p))


@app.get("/api/collections")
def collections():
    with db.session() as con:
        cs = db.rows(con.execute("SELECT uses, user_uses FROM clips WHERE companion_of IS NULL AND uses IS NOT NULL"))
    eff = [sorting.effective(c) for c in cs]
    root = sorting.collections_root()
    return {k: {"name": n, "count": sum(1 for e in eff if e[k]), "folder": str(root / n.replace(" & ", " and "))}
            for k, n in sorting.COLLECTIONS.items()}


def _clip_file(cid: int, field: str) -> Path:
    with db.session() as con:
        r = con.execute(f"SELECT {field} FROM clips WHERE id=?", (cid,)).fetchone()
    if not r or not r[0] or not Path(r[0]).exists():
        raise HTTPException(404, "Not available yet")
    return Path(r[0])


@app.get("/api/clips/{cid}/thumb")
def thumb(cid: int):
    return FileResponse(_clip_file(cid, "thumb"), media_type="image/jpeg", headers={"Cache-Control": "max-age=3600"})


@app.get("/api/clips/{cid}/video")
def video(cid: int):
    try:
        return FileResponse(_clip_file(cid, "proxy"), media_type="video/mp4")
    except HTTPException:
        return FileResponse(_clip_file(cid, "path"), media_type="video/mp4")


@app.get("/api/clips/{cid}/preview")
def preview(cid: int):
    """Large JPEG for photos, 540p MP4 for clips."""
    try:
        p = _clip_file(cid, "proxy")
    except HTTPException:
        p = _clip_file(cid, "path")
    return FileResponse(p, media_type="image/jpeg" if p.suffix.lower() in (".jpg", ".jpeg") else "video/mp4")


@app.post("/api/clips/{cid}/reveal")
def reveal(cid: int):
    """Show the original file in Explorer / Finder."""
    p = _clip_file(cid, "path")
    if sys.platform.startswith("win"):
        subprocess.Popen(["explorer", "/select,", str(p)])
    elif sys.platform == "darwin":
        subprocess.Popen(["open", "-R", str(p)])
    else:
        subprocess.Popen(["xdg-open", str(p.parent)])
    return {"ok": True}


# ---------------------------------------------------------------- music

@app.get("/api/music")
def list_tracks():
    with db.session() as con:
        ts = db.rows(con.execute("SELECT id, path, folder, title, duration, bpm, energy, sections, vibe, vibe_auto, "
                                 "vibe_scores, features, status, error FROM tracks ORDER BY title COLLATE NOCASE"))
    return ts


class TrackIn(BaseModel):
    vibe: str | None = None
    tempo: str | None = None  # "half" | "double": fix a tempo read at the wrong octave


@app.patch("/api/music/{tid}")
def update_track(tid: int, body: TrackIn):
    if body.vibe is not None and body.vibe not in music.VIBES:
        raise HTTPException(400, f"vibe must be one of {music.VIBES}")
    if body.tempo not in (None, "half", "double"):
        raise HTTPException(400, "tempo must be half or double")
    with db.session() as con:
        if body.vibe:
            con.execute("UPDATE tracks SET vibe=? WHERE id=?", (body.vibe, tid))
        if body.tempo:
            r = db.row(con.execute("SELECT bpm, beats FROM tracks WHERE id=?", (tid,)).fetchone())
            if not r or not r["bpm"]:
                raise HTTPException(404, "Song not analyzed")
            bpm, beats = music.change_octave(r["bpm"], r["beats"] or [], body.tempo)
            con.execute("UPDATE tracks SET bpm=?, beats=? WHERE id=?", (bpm, db.dumps(beats), tid))
    return {"ok": True}


@app.get("/api/music/{tid}/audio")
def track_audio(tid: int):
    with db.session() as con:
        r = con.execute("SELECT path FROM tracks WHERE id=?", (tid,)).fetchone()
    if not r or not Path(r[0]).exists():
        raise HTTPException(404, "Song file not found")
    return FileResponse(r[0])


@app.exception_handler(RuntimeError)
def _runtime(_, exc: RuntimeError):
    return JSONResponse({"detail": str(exc)}, status_code=500)


if WEB.is_dir():
    app.mount("/", StaticFiles(directory=WEB, html=True), name="web")
