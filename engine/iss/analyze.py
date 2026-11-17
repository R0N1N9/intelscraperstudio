"""Scoring clips: focus, steadiness, light, best moment, near-duplicates.

Frames are decoded small (320 px wide, grayscale) through FFmpeg, so a 4K clip costs
about as much to analyze as a thumbnail strip. On Windows with an NVIDIA GPU, FFmpeg
decodes with hardware acceleration and proxies are encoded with NVENC.
"""
from __future__ import annotations

import functools
import os
import sys
from pathlib import Path
from typing import Callable

import cv2
import numpy as np

from . import config, db, media

W = 320
Progress = Callable[[int, int, str], None]


def _noop(done: int, total: int, msg: str) -> None:
    pass


# ---------------------------------------------------------------- decoding

def _hwaccel() -> list[str]:
    return ["-hwaccel", "auto"] if sys.platform.startswith("win") else []


def read_frames(path: str, width: int, height: int, duration: float) -> tuple[np.ndarray, float]:
    """Gray frames at W px wide. Returns (frames[N,h,w], sample_fps)."""
    fps = 8.0 if duration <= 90 else max(1.0, 720.0 / max(duration, 1))
    h = max(2, int(round(height * W / max(width, 1) / 2)) * 2) if width and height else 180
    cmd = [media.tool("ffmpeg"), "-v", "error", *_hwaccel(), "-i", path, "-an",
           "-vf", f"fps={fps:.4f},scale={W}:{h}:flags=area", "-f", "rawvideo", "-pix_fmt", "gray", "-"]
    raw = media.run(cmd, timeout=max(120, duration * 4), binary=True)
    n = len(raw) // (W * h)
    frames = np.frombuffer(raw[: n * W * h], np.uint8).reshape(n, h, W)
    return frames, fps


# ---------------------------------------------------------------- metrics

def _norm_log(v: np.ndarray | float, lo: float, hi: float):
    return np.clip((np.log(np.maximum(v, 1e-6)) - np.log(lo)) / (np.log(hi) - np.log(lo)), 0, 1)


def _moving_avg(x: np.ndarray, k: int) -> np.ndarray:
    if k <= 1 or len(x) < 2:
        return x.copy()
    pad = k // 2
    xp = np.pad(x, ((pad, pad), (0, 0)) if x.ndim == 2 else (pad, pad), mode="edge")
    ker = np.ones(k) / k
    if x.ndim == 1:
        return np.convolve(xp, ker, mode="valid")[: len(x)]
    return np.stack([np.convolve(xp[:, j], ker, mode="valid")[: len(x)] for j in range(x.shape[1])], 1)


def measure(frames: np.ndarray, fps: float, log_profile: bool, duration: float) -> dict:
    n = len(frames)
    if n == 0:
        raise ValueError("No frames could be decoded from this clip.")
    f32 = frames.astype(np.float32)
    # Light smoothing first so sensor noise doesn't read as detail.
    sharp = np.array([cv2.Laplacian(cv2.GaussianBlur(f, (3, 3), 0), cv2.CV_32F).var() for f in f32])
    mean = f32.mean(axis=(1, 2)) / 255
    std = f32.std(axis=(1, 2)) / 255
    hi = (frames >= 250).mean(axis=(1, 2))
    lo = (frames <= 6).mean(axis=(1, 2))

    # Camera motion between samples. Smooth motion (pans, drone moves) is fine; jitter is shake.
    vel = np.zeros((n, 2), np.float32)
    conf = np.zeros(n, np.float32)
    if n > 1:
        win = cv2.createHanningWindow((frames.shape[2], frames.shape[1]), cv2.CV_32F)
        for i in range(1, n):
            (dx, dy), r = cv2.phaseCorrelate(f32[i - 1], f32[i], win)
            vel[i] = (dx, dy)
            conf[i] = r
        vel[0] = vel[1]
        conf[0] = conf[1]
    reliable = conf > 0.03
    vel[~reliable] = 0
    k = max(3, int(round(fps * 0.75)) | 1)
    smooth = _moving_avg(vel, k)
    jit = np.linalg.norm(vel - smooth, axis=1)
    jit_local = _moving_avg(jit, k)
    rms_jit = float(np.sqrt(np.mean(jit ** 2))) if n > 2 else 0.0
    move = float(np.median(np.linalg.norm(smooth, axis=1)))

    # Log footage is flat by design: lower contrast, brighter shadows. Judge it on its own terms.
    s_lo, s_hi = (2.0, 25.0) if log_profile else (3.0, 40.0)
    e_lo, e_hi = (0.22, 0.62) if log_profile else (0.16, 0.74)

    focus_frames = _norm_log(sharp, s_lo, s_hi)
    focus = float(100 * _norm_log(np.percentile(sharp, 80), s_lo, s_hi))
    steady = float(100 * np.clip(1 - (rms_jit - 0.5) / 3.0, 0, 1))
    m = float(np.median(mean))
    pen = 0.0
    if m < e_lo:
        pen += (e_lo - m) / e_lo * 120
    if m > e_hi:
        pen += (m - e_hi) / (1 - e_hi) * 120
    clip_hi = float(np.mean(hi))
    clip_lo = float(np.mean(lo))
    pen += max(0.0, clip_hi - 0.02) * 400
    if not log_profile:
        pen += max(0.0, clip_lo - 0.15) * 200
    light = float(np.clip(100 - pen, 0, 100))

    blocked = float(np.mean((mean < 0.07) & (std < 0.05))) > 0.6
    expo_frames = np.clip(1 - np.maximum(0, e_lo - mean) / e_lo - np.maximum(0, mean - e_hi) / (1 - e_hi), 0, 1)
    q = focus_frames * np.clip(1 - jit_local / 3.5, 0, 1) * expo_frames

    # Best moment: the window with the highest average quality.
    win_s = duration if duration < 3 else float(np.clip(duration * 0.35, 3, 8))
    ls = max(1, min(n, int(round(win_s * fps))))
    c = np.concatenate([[0], np.cumsum(q)])
    sums = c[ls:] - c[:-ls]
    i0 = int(np.argmax(sums)) if len(sums) else 0
    best_start = round(i0 / fps, 2)
    best_end = round(min(duration, best_start + ls / fps), 2)
    mid = min(n - 1, i0 + ls // 2)

    return {
        "focus": round(focus), "steady": round(steady), "light": round(light),
        "jitter_px": round(rms_jit, 2), "movement_px": round(move, 2),
        "brightness": round(m, 3), "clipped_highlights": round(clip_hi, 4), "crushed_shadows": round(clip_lo, 4),
        "blocked": blocked, "best_start": best_start, "best_end": best_end, "best_frame": mid,
        "sample_fps": round(fps, 2), "frames": n,
        "quality_curve": [round(float(x), 3) for x in _resample(q, 60)],
    }


def _resample(x: np.ndarray, n: int) -> np.ndarray:
    if len(x) <= n:
        return x
    idx = np.linspace(0, len(x), n + 1).astype(int)
    return np.array([x[a:b].mean() for a, b in zip(idx[:-1], idx[1:])])


def judge(m: dict, duration: float) -> tuple[int, str, list[str]]:
    score = 0.42 * m["focus"] + 0.38 * m["steady"] + 0.20 * m["light"]
    # Great exposure can't rescue a clip that's unusably shaky or out of focus.
    score = min(score, 30 + 0.8 * min(m["focus"], m["steady"]))
    bad: list[str] = []
    if m["blocked"]:
        score = min(score, 8)
        bad.append("Lens looks blocked or pointed at the ground. Likely an accidental recording")
    if duration < 1.5:
        score = min(score, 15)
        bad.append(f"Very short clip ({duration:.1f} s), likely started by accident")
    elif duration < 3:
        score -= 10
        bad.append(f"Short clip ({duration:.1f} s)")
    if m["blocked"]:
        return int(round(max(0.0, score))), "reject", bad[:3]
    if m["steady"] < 35:
        bad.append("Very shaky. Too much to stabilize cleanly")
    elif m["steady"] < 60:
        bad.append("Some handheld shake (stabilizing should fix it)")
    if m["focus"] < 35:
        bad.append("Soft or missed focus")
    elif m["focus"] < 55:
        bad.append("Slightly soft")
    if m["clipped_highlights"] > 0.06:
        bad.append("Bright highlights are clipped")
    elif m["light"] < 60:
        bad.append("Underexposed" if m["brightness"] < 0.3 else "Overexposed")
    score = int(round(float(np.clip(score, 0, 100))))

    good: list[str] = []
    if m["steady"] >= 85 and m["movement_px"] > 1.0:
        good.append("Smooth, steady camera move")
    elif m["steady"] >= 85:
        good.append("Rock steady")
    if m["focus"] >= 80:
        good.append("Sharp focus")
    if m["light"] >= 85:
        good.append("Good exposure")

    verdict = "keep" if score >= 70 else "reject" if score <= 42 else "review"
    reasons = (good + bad) if verdict == "keep" else bad if (verdict == "reject" and bad) else (bad + good)
    return score, verdict, reasons[:3] or ["Nothing stands out either way"]


def dhash(frame: np.ndarray) -> str:
    small = cv2.resize(frame, (9, 8), interpolation=cv2.INTER_AREA)
    bits = (small[:, 1:] > small[:, :-1]).flatten()
    return f"{int(''.join('1' if b else '0' for b in bits), 2):016x}"


def hamming(a: str, b: str) -> int:
    return bin(int(a, 16) ^ int(b, 16)).count("1")


# ---------------------------------------------------------------- outputs

LOG_PREVIEW = "eq=contrast=1.45:brightness=-0.05:saturation=1.6"


def make_thumb(path: str, t: float, out: Path, log_profile: bool) -> None:
    vf = "scale=640:-2" + ("," + LOG_PREVIEW if log_profile else "")
    media.run([media.tool("ffmpeg"), "-v", "error", "-y", "-ss", f"{t:.2f}", "-i", path,
               "-frames:v", "1", "-vf", vf, "-q:v", "3", str(out)], timeout=60)


@functools.lru_cache(maxsize=1)
def h264_encoder() -> list[str]:
    """NVENC when an NVIDIA GPU is usable, otherwise fast CPU x264."""
    ff = media.tool("ffmpeg")
    try:
        media.run([ff, "-v", "error", "-f", "lavfi", "-i", "color=c=black:s=256x144:d=0.2",
                   "-c:v", "h264_nvenc", "-f", "null", "-"], timeout=30)
        return ["-c:v", "h264_nvenc", "-preset", "p4", "-cq", "28"]
    except Exception:
        return ["-c:v", "libx264", "-preset", "veryfast", "-crf", "26"]


def make_proxy(path: str, out: Path, log_profile: bool) -> None:
    vf = "scale=-2:540" + ("," + LOG_PREVIEW if log_profile else "")
    tmp = out.with_suffix(".part.mp4")
    media.run([media.tool("ffmpeg"), "-v", "error", "-y", *_hwaccel(), "-i", path, "-vf", vf,
               *h264_encoder(), "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k",
               "-movflags", "+faststart", str(tmp)], timeout=3600)
    os.replace(tmp, out)


# ---------------------------------------------------------------- driver

def analyze_clip(clip_id: int) -> dict:
    with db.session() as con:
        c = db.row(con.execute("SELECT * FROM clips WHERE id=?", (clip_id,)).fetchone())
        con.execute("UPDATE clips SET status='analyzing', error=NULL WHERE id=?", (clip_id,))
    try:
        dur = c["duration"] or 0.0
        log_p = media.is_log(c["color_profile"])
        frames, fps = read_frames(c["path"], c["width"] or 0, c["height"] or 0, dur)
        if not dur:
            dur = len(frames) / fps
        m = measure(frames, fps, log_p, dur)
        score, verdict, reasons = judge(m, dur)
        thumb = config.cache_dir("thumbs") / f"{clip_id}.jpg"
        make_thumb(c["path"], (m["best_start"] + m["best_end"]) / 2, thumb, log_p)
        proxy = None
        if config.load().get("make_proxies", True):
            proxy = config.cache_dir("proxies") / f"{clip_id}.mp4"
            make_proxy(c["path"], proxy, log_p)
        dh = dhash(frames[m.pop("best_frame")])
        with db.session() as con:
            con.execute("""UPDATE clips SET status='analyzed', score=?, verdict=?, reasons=?, metrics=?,
                           best_start=?, best_end=?, thumb=?, proxy=?, dhash=?, duration=? WHERE id=?""",
                        (score, verdict, db.dumps(reasons), db.dumps(m), m["best_start"], m["best_end"],
                         str(thumb), str(proxy) if proxy else None, dh, dur, clip_id))
        return {"id": clip_id, "score": score, "verdict": verdict}
    except Exception as e:
        with db.session() as con:
            con.execute("UPDATE clips SET status='error', error=? WHERE id=?", (str(e)[:500], clip_id))
        return {"id": clip_id, "error": str(e)}


def group_similar(shoot_id: int, max_bits: int = 8, max_gap_s: float = 900) -> int:
    """Mark near-identical takes so only the best of each group is kept automatically."""
    from .ingest import to_dt
    with db.session() as con:
        cs = db.rows(con.execute("SELECT id, src_name, dhash, score, verdict, reasons, recorded_at, camera FROM clips "
                                 "WHERE shoot_id=? AND status='analyzed' AND kind='video' AND dhash IS NOT NULL",
                                 (shoot_id,)))
        parent = {c["id"]: c["id"] for c in cs}

        def find(x):
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        for i, a in enumerate(cs):
            for b in cs[i + 1:]:
                if a["camera"] != b["camera"]:
                    continue
                if abs((to_dt(a["recorded_at"]) - to_dt(b["recorded_at"])).total_seconds()) > max_gap_s:
                    continue
                if hamming(a["dhash"], b["dhash"]) <= max_bits:
                    parent[find(a["id"])] = find(b["id"])
        groups: dict[int, list[dict]] = {}
        for c in cs:
            groups.setdefault(find(c["id"]), []).append(c)
        marked = 0
        for g in groups.values():
            if len(g) < 2:
                continue
            g.sort(key=lambda c: -(c["score"] or 0))
            best = g[0]
            for c in g[1:]:
                note = f"Very similar to {best['src_name']}, which scored higher"
                reasons = [r for r in (c["reasons"] or []) if not r.startswith("Very similar to")]
                verdict = "review" if c["verdict"] == "keep" else c["verdict"]
                new_reasons = reasons[:2] + [note] if verdict == "reject" else [note] + reasons[:2]
                con.execute("UPDATE clips SET similar_to=?, verdict=?, reasons=? WHERE id=?",
                            (best["id"], verdict, db.dumps(new_reasons), c["id"]))
                marked += 1
        return marked


def analyze_many(ids: list[int], progress: Progress = _noop) -> list[dict]:
    out = []
    for i, cid in enumerate(ids):
        with db.session() as con:
            r = con.execute("SELECT src_name, kind FROM clips WHERE id=?", (cid,)).fetchone()
        if not r or r["kind"] != "video":
            continue
        progress(i, len(ids), f"Analyzing {r['src_name']} ({i + 1} of {len(ids)})")
        out.append(analyze_clip(cid))
    with db.session() as con:
        shoots = {r[0] for r in con.execute(
            f"SELECT DISTINCT shoot_id FROM clips WHERE id IN ({','.join('?' * len(ids))})", ids)} if ids else set()
    for s in shoots:
        group_similar(s)
    progress(len(ids), len(ids), "Analysis done")
    return out
