"""Finding camera cards and importing footage into the library.

Safety rules:
  * Files on the card are only ever read, never moved, renamed or deleted.
  * Every copy is hashed while writing and (by default) re-read to verify it.
  * Already-imported files are skipped, so importing the same card twice is harmless.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import string
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from . import config, db, media

CHUNK = 8 * 1024 * 1024
SKIP_DIRS = {"SUB", "THMBNL", "$RECYCLE.BIN", "SYSTEM VOLUME INFORMATION", "MISC", "AVF_INFO"}

Progress = Callable[[int, int, str], None]


def _noop(done: int, total: int, msg: str) -> None:
    pass


# ---------------------------------------------------------------- sources

def _volume_roots() -> list[Path]:
    if sys.platform.startswith("win"):
        return [Path(f"{d}:\\") for d in string.ascii_uppercase if d not in "AB" and os.path.exists(f"{d}:\\")]
    roots: list[Path] = []
    for base in ("/Volumes", "/media", "/run/media", "/mnt"):
        b = Path(base)
        if not b.is_dir():
            continue
        for p in b.iterdir():
            roots.append(p)
            if p.is_dir() and base in ("/media", "/run/media"):
                roots.extend(c for c in p.iterdir() if c.is_dir())
    return roots


def _volume_label(root: Path) -> str:
    if sys.platform.startswith("win"):
        try:
            import ctypes
            buf = ctypes.create_unicode_buffer(261)
            ctypes.windll.kernel32.GetVolumeInformationW(str(root), buf, 261, None, None, None, None, 0)
            if buf.value:
                return buf.value
        except Exception:
            pass
    return root.name or str(root)


def is_camera_card(root: Path) -> bool:
    try:
        return (root / "DCIM").is_dir() or (root / "PRIVATE" / "M4ROOT").is_dir()
    except OSError:
        return False


def scan_media(root: Path) -> list[Path]:
    found: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d.upper() not in SKIP_DIRS and not d.startswith(".")]
        for f in filenames:
            if f.startswith("."):
                continue
            ext = Path(f).suffix.lower()
            if ext in media.VIDEO_EXT or ext in media.PHOTO_EXT:
                found.append(Path(dirpath) / f)
    return sorted(found)


def describe_source(root: Path) -> dict:
    files = scan_media(root)
    with db.session() as con:
        known = {(r["src_name"], r["src_size"]) for r in con.execute("SELECT src_name, src_size FROM clips")}
    sizes = [(f, f.stat().st_size) for f in files]
    new = [(f, s) for f, s in sizes if (f.name, s) not in known]
    names = [f.name.upper() for f in files]
    if (root / "PRIVATE" / "M4ROOT").is_dir():
        guess = "Sony camera"
        xml = next((media.sony_sidecar(f) for f in files if media.sony_sidecar(f)), None)
        if xml:
            guess = media.parse_sony_xml(xml).get("camera", guess)
    elif any(n.startswith("DJI_") for n in names):
        guess = "DJI drone"
    else:
        guess = "Camera card"
    return {
        "path": str(root), "label": _volume_label(root), "camera": guess,
        "files": len(files), "videos": sum(1 for f in files if f.suffix.lower() in media.VIDEO_EXT),
        "bytes": sum(s for _, s in sizes), "new_files": len(new), "new_bytes": sum(s for _, s in new),
    }


def find_sources() -> list[dict]:
    out = []
    for r in _volume_roots():
        if is_camera_card(r):
            try:
                out.append(describe_source(r))
            except OSError:
                continue
    return out


# ---------------------------------------------------------------- copying

def _hash_file(p: Path) -> str:
    h = hashlib.blake2b(digest_size=16)
    with open(p, "rb") as f:
        while chunk := f.read(CHUNK):
            h.update(chunk)
    return h.hexdigest()


def _copy_hashed(src: Path, dests: list[Path]) -> str:
    """Copy src to every dest in one read pass, returning the source hash."""
    h = hashlib.blake2b(digest_size=16)
    tmp = [d.with_name(d.name + ".part") for d in dests]
    for d in dests:
        d.parent.mkdir(parents=True, exist_ok=True)
    outs = [open(t, "wb") for t in tmp]
    try:
        with open(src, "rb") as f:
            while chunk := f.read(CHUNK):
                h.update(chunk)
                for o in outs:
                    o.write(chunk)
    finally:
        for o in outs:
            o.close()
    for t, d in zip(tmp, dests):
        os.replace(t, d)
        shutil.copystat(src, d)
    return h.hexdigest()


def _free_name(dest: Path) -> Path:
    if not dest.exists():
        return dest
    i = 2
    while True:
        cand = dest.with_name(f"{dest.stem}_{i}{dest.suffix}")
        if not cand.exists():
            return cand
        i += 1


# ---------------------------------------------------------------- shoots

def to_dt(s: str | None) -> datetime:
    if not s:
        return datetime.now(timezone.utc)
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.astimezone()  # treat as local time
    return dt.astimezone(timezone.utc)


def _assign_shoot(con, rec: str, lat, lon, gap_h: float) -> int:
    t = to_dt(rec)
    gap = timedelta(hours=gap_h)
    for s in con.execute("SELECT * FROM shoots ORDER BY started_at DESC"):
        if to_dt(s["started_at"]) - gap <= t <= to_dt(s["ended_at"]) + gap:
            new_start = min(to_dt(s["started_at"]), t)
            new_end = max(to_dt(s["ended_at"]), t)
            con.execute("UPDATE shoots SET started_at=?, ended_at=?, lat=COALESCE(lat, ?), lon=COALESCE(lon, ?) WHERE id=?",
                        (new_start.isoformat(), new_end.isoformat(), lat, lon, s["id"]))
            return s["id"]
    local = to_dt(rec).astimezone()
    cur = con.execute("INSERT INTO shoots(name, started_at, ended_at, lat, lon) VALUES (?,?,?,?,?)",
                      (f"Shoot · {local:%b} {local.day}, {local.year}", t.isoformat(), t.isoformat(), lat, lon))
    return cur.lastrowid


def _camera_folder(camera: str) -> str:
    return "".join(c for c in camera if c.isalnum() or c in " -_").strip() or "Camera"


# ---------------------------------------------------------------- import

def import_source(source: str | Path, progress: Progress = _noop) -> dict:
    """Copy new media from a card or folder into the library. Returns a summary and new clip ids."""
    cfg = config.load()
    root = Path(source)
    if not root.is_dir():
        raise FileNotFoundError(f"Folder not found: {root}")
    lib = Path(cfg["library_root"])
    backup = Path(cfg["backup_root"]) if cfg.get("backup_root") else None
    lib.mkdir(parents=True, exist_ok=True)

    files = scan_media(root)
    progress(0, len(files), f"Found {len(files)} media files")
    with db.session() as con:
        known = {(r["src_name"], r["src_size"]) for r in con.execute("SELECT src_name, src_size FROM clips")}
    todo = [f for f in files if (f.name, f.stat().st_size) not in known]
    skipped = len(files) - len(todo)

    # Read metadata first so clips can be grouped into shoots by time.
    infos = []
    for i, f in enumerate(todo):
        progress(i, len(todo), f"Reading {f.name}")
        infos.append((f, media.inspect(f)))
    # RAW+JPEG pairs (DSC01234.ARW + DSC01234.JPG) share one capture time, so they land in the same shoot.
    pairs: dict[tuple[str, str], list[dict]] = {}
    for f, info in infos:
        if info["kind"] == "photo":
            pairs.setdefault((str(f.parent), f.stem.upper()), []).append(info)
    for group in pairs.values():
        dated = [i for i in group if not i["meta"].get("time_from_file")]
        if len(group) > 1 and dated:
            for i in group:
                if i["meta"].get("time_from_file"):
                    i["recorded_at"] = dated[0]["recorded_at"]
                    i.setdefault("lat", dated[0].get("lat"))
                    i.setdefault("lon", dated[0].get("lon"))
    infos.sort(key=lambda x: to_dt(x[1]["recorded_at"]))

    new_ids: list[int] = []
    copied_bytes = 0
    for i, (f, info) in enumerate(infos):
        progress(i, len(infos), f"Copying {f.name} ({i + 1} of {len(infos)})")
        size = f.stat().st_size
        with db.session() as con:
            shoot_id = _assign_shoot(con, info["recorded_at"], info.get("lat"), info.get("lon"), cfg["shoot_gap_hours"])
            shoot = con.execute("SELECT started_at FROM shoots WHERE id=?", (shoot_id,)).fetchone()
        day = to_dt(shoot["started_at"]).astimezone()
        rel = Path(f"{day:%Y}") / f"{day:%Y-%m-%d}" / _camera_folder(info["camera"])
        dest = _free_name(lib / rel / f.name)
        dests = [dest] + ([_free_name(backup / rel / f.name)] if backup else [])
        digest = _copy_hashed(f, dests)

        with db.session() as con:
            if con.execute("SELECT 1 FROM clips WHERE hash=?", (digest,)).fetchone():
                for d in dests:  # identical file already in the library under another name
                    d.unlink(missing_ok=True)
                skipped += 1
                continue
        if cfg.get("verify_copies", True):
            for d in dests:
                if _hash_file(d) != digest:
                    for x in dests:
                        x.unlink(missing_ok=True)
                    raise IOError(f"Copy of {f.name} did not match the original. Nothing was changed on the card.")
        for sc in info.get("sidecars", []):
            for d in dests:
                shutil.copy2(sc, d.with_name(d.stem + sc.name[len(f.stem):]))
        copied_bytes += size
        with db.session() as con:
            cur = con.execute(
                """INSERT INTO clips(shoot_id, hash, src_name, src_size, path, backup_path, kind, camera,
                   color_profile, duration, width, height, fps, codec, bit_depth, recorded_at, lat, lon, meta, status,
                   megapixels)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (shoot_id, digest, f.name, size, str(dest), str(dests[1]) if backup else None, info["kind"],
                 info["camera"], info.get("color_profile"), info.get("duration"), info.get("width"),
                 info.get("height"), info.get("fps"), info.get("codec"), info.get("bit_depth"),
                 info["recorded_at"], info.get("lat"), info.get("lon"), db.dumps(info.get("meta", {})),
                 "imported", info.get("megapixels")))
            new_ids.append(cur.lastrowid)
    with db.session() as con:  # a shoot created only for duplicates would be empty
        con.execute("DELETE FROM shoots WHERE id NOT IN (SELECT DISTINCT shoot_id FROM clips WHERE shoot_id IS NOT NULL)")
    progress(len(infos), len(infos), f"Imported {len(new_ids)} files")
    return {"imported": len(new_ids), "skipped": skipped, "bytes": copied_bytes, "clip_ids": new_ids}
