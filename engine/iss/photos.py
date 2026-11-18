"""Scoring photos (RAW and JPEG): subject sharpness, exposure, noise, horizon tilt and visual impact.

RAW files are read through LibRaw (rawpy). Cameras embed a full-size JPEG preview in their RAW
files, so analysis uses that preview when present and only demosaics the RAW when it isn't.
"""
from __future__ import annotations

import math
from pathlib import Path

import cv2
import numpy as np

from . import analyze, config, db, media

PREVIEW_SIDE = 1800
WORK_SIDE = 1024


# ---------------------------------------------------------------- loading

def _resize_long(img: np.ndarray, side: int) -> np.ndarray:
    h, w = img.shape[:2]
    s = side / max(h, w)
    if s >= 1:
        return img
    return cv2.resize(img, (max(1, round(w * s)), max(1, round(h * s))), interpolation=cv2.INTER_AREA)


def _rotate_for_flip(img: np.ndarray, flip: int) -> np.ndarray:
    if flip == 3:
        return cv2.rotate(img, cv2.ROTATE_180)
    if flip == 5:
        return cv2.rotate(img, cv2.ROTATE_90_COUNTERCLOCKWISE)
    if flip == 6:
        return cv2.rotate(img, cv2.ROTATE_90_CLOCKWISE)
    return img


def load_photo(path: str, side: int = PREVIEW_SIDE) -> np.ndarray:
    """Returns an 8-bit BGR image, upright, with its long side at most `side` pixels."""
    p = Path(path)
    ext = p.suffix.lower()
    if ext in media.RAW_EXT:
        import rawpy
        with rawpy.imread(str(p)) as raw:
            flip = raw.sizes.flip
            img = None
            try:
                th = raw.extract_thumb()
                if th.format == rawpy.ThumbFormat.JPEG:
                    img = cv2.imdecode(np.frombuffer(th.data, np.uint8), cv2.IMREAD_COLOR)
                elif th.format == rawpy.ThumbFormat.BITMAP:
                    img = cv2.cvtColor(th.data, cv2.COLOR_RGB2BGR)
                if img is not None and max(img.shape[:2]) < 1000:
                    img = None  # tiny thumbnail only; decode the RAW instead
                if img is not None:
                    img = _rotate_for_flip(img, flip)
            except (rawpy.LibRawNoThumbnailError, rawpy.LibRawUnsupportedThumbnailError, Exception):
                img = None
            if img is None:
                rgb = raw.postprocess(half_size=True, use_camera_wb=True, output_bps=8, no_auto_bright=False)
                img = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)  # postprocess already applies orientation
        return _resize_long(img, side)
    try:
        from PIL import Image, ImageOps
        with Image.open(p) as im:
            im.draft("RGB", (side, side))
            im = ImageOps.exif_transpose(im).convert("RGB")
            im.thumbnail((side, side))
            return cv2.cvtColor(np.asarray(im), cv2.COLOR_RGB2BGR)
    except Exception:
        # HEIC and other formats: let FFmpeg decode
        raw = media.run([media.tool("ffmpeg"), "-v", "error", "-i", str(p), "-frames:v", "1",
                         "-vf", f"scale='min({side},iw)':-2", "-f", "image2pipe", "-vcodec", "png", "-"], binary=True)
        img = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
        if img is None:
            raise ValueError("This photo format could not be decoded.")
        return img


# ---------------------------------------------------------------- measurements

def _tile_sharpness(gray: np.ndarray, cols: int = 6, rows: int = 4) -> np.ndarray:
    g = cv2.GaussianBlur(gray, (3, 3), 0).astype(np.float32)
    lap = cv2.Laplacian(g, cv2.CV_32F)
    h, w = gray.shape
    vals = []
    for j in range(rows):
        for i in range(cols):
            t = lap[j * h // rows:(j + 1) * h // rows, i * w // cols:(i + 1) * w // cols]
            vals.append(float(t.var()))
    return np.array(vals)


def noise_sigma(gray: np.ndarray) -> float:
    """Immerkær's noise estimate (8-bit levels), measured only in the smoothest areas (sky, walls),
    so fine detail like grass or rock texture isn't mistaken for noise."""
    k = np.array([[1, -2, 1], [-2, 4, -2], [1, -2, 1]], np.float32)
    g = gray.astype(np.float32)
    resp = np.abs(cv2.filter2D(g, -1, k))[2:-2, 2:-2]
    smooth = cv2.GaussianBlur(g, (0, 0), 3)
    struct = cv2.blur(np.abs(cv2.Laplacian(smooth, cv2.CV_32F)), (15, 15))[2:-2, 2:-2]
    mid = (gray[2:-2, 2:-2] > 12) & (gray[2:-2, 2:-2] < 243)  # clipped areas hide noise
    cand = struct[mid] if mid.mean() > 0.05 else struct.ravel()
    thr = np.percentile(cand, 25)
    sel = resp[(struct <= thr) & mid] if mid.mean() > 0.05 else resp[struct <= thr]
    if sel.size < 500:
        sel = resp.ravel()
    return float(math.sqrt(math.pi / 2) / 6 * sel.mean())


def horizon_tilt(gray: np.ndarray) -> float | None:
    """Angle (degrees) of the dominant near-horizontal line, or None if there isn't a clear one."""
    h, w = gray.shape
    edges = cv2.Canny(cv2.GaussianBlur(gray, (5, 5), 0), 40, 120)
    lines = cv2.HoughLinesP(edges, 1, np.pi / 720, threshold=80, minLineLength=int(w * 0.3), maxLineGap=12)
    if lines is None:
        return None
    angs, lens = [], []
    for x1, y1, x2, y2 in np.asarray(lines).reshape(-1, 4):
        a = math.degrees(math.atan2(y2 - y1, x2 - x1))
        if a > 90:
            a -= 180
        if a < -90:
            a += 180
        if abs(a) <= 12:
            angs.append(a)
            lens.append(math.hypot(x2 - x1, y2 - y1))
    if not angs or sum(lens) < w * 0.6:
        return None
    order = np.argsort(angs)
    cum = np.cumsum(np.array(lens)[order])
    return round(float(np.array(angs)[order][np.searchsorted(cum, cum[-1] / 2)]), 2)


def saliency(gray: np.ndarray) -> np.ndarray:
    """Spectral-residual saliency map (Hou & Zhang), 0..1, at 128 px wide."""
    small = cv2.resize(gray, (128, max(8, round(128 * gray.shape[0] / gray.shape[1]))), interpolation=cv2.INTER_AREA)
    f = np.fft.fft2(small.astype(np.float32))
    log_amp = np.log(np.abs(f) + 1e-6)
    phase = np.angle(f)
    residual = log_amp - cv2.blur(log_amp, (3, 3))
    sal = np.abs(np.fft.ifft2(np.exp(residual + 1j * phase))) ** 2
    sal = cv2.GaussianBlur(sal.astype(np.float32), (9, 9), 2.5)
    return sal / (sal.max() + 1e-9)


def impact_metrics(img: np.ndarray, gray: np.ndarray) -> dict:
    b, g, r = [c.astype(np.float32) for c in cv2.split(img)]
    rg, yb = r - g, 0.5 * (r + g) - b
    colorful = math.sqrt(rg.std() ** 2 + yb.std() ** 2) + 0.3 * math.sqrt(rg.mean() ** 2 + yb.mean() ** 2)
    contrast = float(gray.std() / 255)
    sal = saliency(gray)
    flat = np.sort(sal.ravel())[::-1]
    top = flat[: max(1, len(flat) // 20)]
    concentration = float(top.sum() / (flat.sum() + 1e-9))  # 0.05 = attention spread evenly
    thr = flat[max(1, len(flat) // 10)]
    ys, xs = np.nonzero(sal >= thr)
    wts = sal[ys, xs]
    cx, cy = float((xs * wts).sum() / wts.sum() / sal.shape[1]), float((ys * wts).sum() / wts.sum() / sal.shape[0])
    d = min(math.hypot(cx - tx, cy - ty) for tx in (1 / 3, 2 / 3) for ty in (1 / 3, 2 / 3))
    c_n = float(np.clip(colorful / 80, 0, 1))
    k_n = float(np.clip(contrast / 0.25, 0, 1))
    s_n = float(np.clip((concentration - 0.06) / 0.25, 0, 1))
    t_n = float(np.clip(1 - d / 0.25, 0, 1))
    return {"colorfulness": round(colorful, 1), "contrast": round(contrast, 3), "attention": round(concentration, 3),
            "subject_x": round(cx, 2), "subject_y": round(cy, 2),
            "impact": round(100 * (0.32 * c_n + 0.23 * k_n + 0.30 * s_n + 0.15 * t_n))}


def measure_photo(img: np.ndarray, iso: float | None) -> dict:
    work = _resize_long(img, WORK_SIDE)
    gray = cv2.cvtColor(work, cv2.COLOR_BGR2GRAY)
    # Stretch brightness first so a dark frame isn't mistaken for a soft one.
    lo, hi = np.percentile(gray, (1, 99))
    stretched = np.clip((gray.astype(np.float32) - lo) * (255 / max(hi - lo, 8)), 0, 255).astype(np.uint8)
    tiles = _tile_sharpness(stretched)
    top = float(np.sort(tiles)[-3:].mean())
    focus = float(100 * analyze._norm_log(top, 6.0, 90.0))

    lum = gray.astype(np.float32) / 255
    m = float(lum.mean())
    clip_hi = float((work.min(axis=2) >= 250).mean())
    clip_lo = float((gray <= 4).mean())
    pen = 0.0
    if m < 0.16:
        pen += (0.16 - m) / 0.16 * 110
    if m > 0.76:
        pen += (m - 0.76) / 0.24 * 110
    pen += max(0.0, clip_hi - 0.01) * 500 + max(0.0, clip_lo - 0.10) * 200
    light = float(np.clip(100 - pen, 0, 100))

    sigma = noise_sigma(gray)
    noise = float(100 * np.clip(1 - (sigma - 1.0) / 4.0, 0, 1))
    if iso:
        noise = min(noise, float(np.clip(100 - 22 * math.log2(max(iso, 100) / 800), 25, 100)))

    return {"focus": round(focus), "light": round(light), "noise": round(noise), "noise_sigma": round(sigma, 2),
            "sharp_tiles": round(top, 1), "brightness": round(m, 3), "clipped_highlights": round(clip_hi, 4),
            "crushed_shadows": round(clip_lo, 4), "tilt_deg": horizon_tilt(gray), **impact_metrics(work, gray)}


def judge_photo(m: dict, iso: float | None) -> tuple[int, str, list[str]]:
    score = 0.45 * m["focus"] + 0.25 * m["light"] + 0.30 * m["noise"]
    score = min(score, 25 + 0.8 * m["focus"], 40 + 0.7 * m["light"], 45 + 0.6 * m["noise"])
    bad: list[str] = []
    if m["focus"] < 40:
        bad.append("Subject is soft or focus was missed")
    elif m["focus"] < 60:
        bad.append("Slightly soft")
    if m["clipped_highlights"] > 0.04:
        bad.append("Bright highlights are clipped")
    if m["light"] < 60:
        bad.append("Underexposed" if m["brightness"] < 0.4 else "Overexposed")
    if m["noise"] < 55:
        bad.append(f"Noisy{f' (ISO {int(iso)})' if iso else ''}")
    t = m.get("tilt_deg")
    if t is not None and 0.8 <= abs(t) <= 12:
        bad.append(f"Horizon tilted {abs(t):.1f}°")
    good: list[str] = []
    if m["focus"] >= 85:
        good.append("Tack-sharp subject")
    if m["noise"] >= 85:
        good.append("Clean, low noise")
    if m["light"] >= 85:
        good.append("Well exposed")
    if m["impact"] >= 65:
        good.append("Strong color and contrast")
    score = int(round(float(np.clip(score, 0, 100))))
    verdict = "keep" if score >= 70 else "reject" if score <= 42 else "review"
    reasons = bad if (verdict == "reject" and bad) else (bad + good)  # flaws first, so they're never hidden
    return score, verdict, reasons[:3] or ["Nothing stands out either way"]


# ---------------------------------------------------------------- driver

def _iso(meta: dict) -> float | None:
    try:
        return float(str(meta.get("iso", "")).split()[0])
    except (ValueError, IndexError):
        return None


def analyze_photo(clip_id: int) -> dict:
    with db.session() as con:
        c = db.row(con.execute("SELECT * FROM clips WHERE id=?", (clip_id,)).fetchone())
        con.execute("UPDATE clips SET status='analyzing', error=NULL WHERE id=?", (clip_id,))
    try:
        img = load_photo(c["path"])
        iso = _iso(c["meta"] or {})
        m = measure_photo(img, iso)
        score, verdict, reasons = judge_photo(m, iso)
        thumb = config.cache_dir("thumbs") / f"{clip_id}.jpg"
        preview = config.cache_dir("proxies") / f"{clip_id}.jpg"
        cv2.imwrite(str(thumb), _resize_long(img, 640), [cv2.IMWRITE_JPEG_QUALITY, 85])
        cv2.imwrite(str(preview), img, [cv2.IMWRITE_JPEG_QUALITY, 90])
        gray = cv2.cvtColor(_resize_long(img, 256), cv2.COLOR_BGR2GRAY)
        with db.session() as con:
            con.execute("""UPDATE clips SET status='analyzed', score=?, verdict=?, reasons=?, metrics=?, thumb=?, proxy=?,
                           dhash=? WHERE id=?""",
                        (score, verdict, db.dumps(reasons), db.dumps(m), str(thumb), str(preview),
                         analyze.dhash(gray), clip_id))
        return {"id": clip_id, "score": score, "verdict": verdict}
    except Exception as e:
        with db.session() as con:
            con.execute("UPDATE clips SET status='error', error=? WHERE id=?", (str(e)[:500], clip_id))
        return {"id": clip_id, "error": str(e)}
