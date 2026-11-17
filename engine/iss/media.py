"""Reading metadata from camera files.

Sources, best first:
  * Sony XAVC sidecar XML (C0001M01.XML): camera model, gamma (S-Log3), GPS, record date.
  * DJI telemetry subtitles (DJI_0001.SRT): color mode (D-Log M), GPS, ISO, shutter, aperture.
  * ffprobe: codec, resolution, frame rate, bit depth, duration, creation time.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

VIDEO_EXT = {".mp4", ".mov", ".mxf", ".m4v"}
PHOTO_EXT = {".arw", ".dng", ".jpg", ".jpeg", ".heic", ".hif", ".tif", ".tiff"}
SKIP_EXT = {".lrf", ".thm", ".xml", ".srt", ".bim", ".bin", ".dat", ".db", ".ini"}

SONY_MODELS = {
    "ILCE-7C": "Sony A7C", "ILCE-7CM2": "Sony A7C II", "ILCE-7CR": "Sony A7CR",
    "ILCE-7M3": "Sony A7 III", "ILCE-7M4": "Sony A7 IV", "ILCE-7SM3": "Sony A7S III",
    "ILCE-6400": "Sony a6400", "ILCE-6700": "Sony a6700", "ZV-E1": "Sony ZV-E1",
}

SONY_GAMMA = {
    "s-log3-cine": "S-Log3", "s-log3": "S-Log3", "s-log2": "S-Log2",
    "rec2100-hlg": "HLG", "hlg": "HLG", "rec709": "Standard", "rec709-xvycc": "Standard",
    "s-cinetone": "S-Cinetone",
}

DJI_COLOR = {"dlog_m": "D-Log M", "d_log_m": "D-Log M", "dlog": "D-Log", "d_log": "D-Log",
             "hlg": "HLG", "default": "Normal", "normal": "Normal"}


def tool(name: str) -> str:
    found = shutil.which(name)
    if not found:
        raise RuntimeError(
            f"{name} was not found. Install FFmpeg (on Windows: winget install Gyan.FFmpeg) and restart.")
    return found


def run(args: list[str], timeout: float = 120, binary: bool = False):
    kw = {}
    if os.name == "nt":  # don't flash console windows on Windows
        kw["creationflags"] = 0x08000000
    p = subprocess.run(args, capture_output=True, timeout=timeout, **kw)
    if p.returncode != 0:
        raise RuntimeError(p.stderr.decode(errors="replace")[-600:])
    return p.stdout if binary else p.stdout.decode(errors="replace")


def ffprobe(path: Path) -> dict:
    out = run([tool("ffprobe"), "-v", "error", "-print_format", "json",
               "-show_format", "-show_streams", str(path)], timeout=60)
    return json.loads(out or "{}")


def _fps(s: str | None) -> float | None:
    if not s or s in ("0/0",):
        return None
    if "/" in s:
        a, b = s.split("/")
        return round(float(a) / float(b), 3) if float(b) else None
    return float(s)


def _parse_time(s: str | None) -> str | None:
    if not s:
        return None
    s = s.strip().replace("Z", "+00:00")
    for fmt in (None, "%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y:%m:%d %H:%M:%S"):
        try:
            dt = datetime.fromisoformat(s) if fmt is None else datetime.strptime(s, fmt)
            return dt.isoformat(timespec="seconds")
        except ValueError:
            continue
    return None


def _dms(v: str, ref: str | None) -> float | None:
    """'36:22:15.600' + 'N' -> 36.371. Also accepts plain decimals."""
    try:
        if ":" in v:
            d, m, s = (float(x) for x in v.split(":"))
            val = d + m / 60 + s / 3600
        else:
            val = float(v)
    except ValueError:
        return None
    if ref and ref.upper() in ("S", "W"):
        val = -val
    return round(val, 6)


def sony_sidecar(path: Path) -> Path | None:
    for cand in (path.with_name(path.stem + "M01.XML"), path.with_name(path.stem + "M01.xml")):
        if cand.exists():
            return cand
    return None


def dji_sidecar(path: Path) -> Path | None:
    for cand in (path.with_suffix(".SRT"), path.with_suffix(".srt")):
        if cand.exists():
            return cand
    return None


def parse_sony_xml(xml_path: Path) -> dict:
    out: dict = {}
    try:
        root = ET.parse(xml_path).getroot()
    except (ET.ParseError, OSError):
        return out
    items: dict[str, str] = {}
    for el in root.iter():
        tag = el.tag.split("}")[-1]
        if tag == "Device":
            model = el.get("modelName", "")
            out["model_code"] = model
            out["camera"] = SONY_MODELS.get(model, f"{el.get('manufacturer', 'Sony')} {model}".strip())
        elif tag == "CreationDate":
            out["recorded_at"] = _parse_time(el.get("value"))
        elif tag == "VideoFrame":
            out["capture_fps"] = el.get("captureFps")
        elif tag == "Lens" and el.get("modelName"):
            out["lens"] = el.get("modelName")
        elif tag == "Item" and el.get("name"):
            items[el.get("name")] = el.get("value", "")
    gamma = items.get("CaptureGammaEquation", "").lower()
    if gamma:
        out["color_profile"] = SONY_GAMMA.get(gamma, gamma)
    if items.get("CaptureColorPrimaries"):
        out["gamut"] = items["CaptureColorPrimaries"]
    if items.get("Latitude") and items.get("Longitude"):
        out["lat"] = _dms(items["Latitude"], items.get("LatitudeRef"))
        out["lon"] = _dms(items["Longitude"], items.get("LongitudeRef"))
    return out


_SRT_FIELD = re.compile(r"\[\s*([a-z_]+)\s*:\s*([^\]\s]+)")
_SRT_TIME = re.compile(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}(?:\.\d+)?)")
_SRT_GPS_OLD = re.compile(r"GPS\s*\(\s*(-?[\d.]+)\s*,\s*(-?[\d.]+)")


def parse_dji_srt(srt_path: Path, max_bytes: int = 200_000) -> dict:
    out: dict = {}
    try:
        text = srt_path.read_bytes()[:max_bytes].decode(errors="replace")
    except OSError:
        return out
    fields: dict[str, str] = {}
    for k, v in _SRT_FIELD.findall(text):
        if k in ("latitude", "longitude"):
            try:
                if abs(float(v)) < 0.0001:
                    continue  # no GPS fix yet on this frame
            except ValueError:
                continue
        fields.setdefault(k, v)
    t = _SRT_TIME.search(text)
    if t:
        out["recorded_at"] = _parse_time(t.group(1))
    lat, lon = fields.get("latitude"), fields.get("longitude")
    if not lat:
        m = _SRT_GPS_OLD.search(text)
        if m:
            lon, lat = m.group(1), m.group(2)
    try:
        if lat and lon and abs(float(lat)) > 0.0001:
            out["lat"], out["lon"] = round(float(lat), 6), round(float(lon), 6)
    except ValueError:
        pass
    if fields.get("color_md"):
        out["color_profile"] = DJI_COLOR.get(fields["color_md"].lower(), fields["color_md"])
    for src, dst in (("iso", "iso"), ("shutter", "shutter"), ("fnum", "aperture"), ("rel_alt", "altitude_m")):
        if fields.get(src):
            out[dst] = fields[src]
    if out.get("aperture"):
        try:
            f = float(out["aperture"])
            out["aperture"] = f"f/{f / 100:.1f}" if f > 30 else f"f/{f:.1f}"  # older firmware writes 280
        except ValueError:
            pass
    return out


def inspect(path: Path) -> dict:
    """Everything we can learn about a media file without decoding it."""
    path = Path(path)
    ext = path.suffix.lower()
    info: dict = {"kind": "video" if ext in VIDEO_EXT else "photo", "meta": {}}
    try:
        probe = ffprobe(path)
    except Exception as e:  # unreadable or unsupported: still importable as a plain file
        probe = {}
        info["meta"]["probe_error"] = str(e)[:200]
    fmt = probe.get("format", {})
    tags = {k.lower(): v for k, v in (fmt.get("tags") or {}).items()}
    vs = next((s for s in probe.get("streams", []) if s.get("codec_type") == "video"), None)

    if vs:
        info["width"], info["height"] = vs.get("width"), vs.get("height")
        info["codec"] = vs.get("codec_name")
        pix = vs.get("pix_fmt") or ""
        info["bit_depth"] = 10 if "10" in pix else 12 if "12" in pix else 8
        info["fps"] = _fps(vs.get("avg_frame_rate")) or _fps(vs.get("r_frame_rate"))
        if vs.get("color_transfer") == "arib-std-b67":
            info["color_profile"] = "HLG"
        if info["kind"] == "photo":
            info["fps"] = None
    try:
        info["duration"] = round(float(fmt.get("duration")), 3) if info["kind"] == "video" else None
    except (TypeError, ValueError):
        info["duration"] = None
    stream_tags = {k.lower(): v for k, v in ((vs or {}).get("tags") or {}).items()}
    info["recorded_at"] = _parse_time(tags.get("creation_time") or stream_tags.get("creation_time"))

    name = path.name.upper()
    tag_text = " ".join(str(v) for v in tags.values()).upper()
    if name.startswith("DJI_") or "DJI" in tag_text:
        info["camera"] = "DJI Mavic 3" if ("MAVIC" in tag_text or "M3" in tag_text or name.startswith("DJI_")) else "DJI"
        srt = dji_sidecar(path)
        if srt:
            info["sidecars"] = [srt]
            d = parse_dji_srt(srt)
            for k in ("recorded_at", "lat", "lon", "color_profile"):
                if d.get(k) is not None:
                    info[k] = d[k]
            info["meta"].update({k: d[k] for k in ("iso", "shutter", "aperture", "altitude_m") if k in d})
        if not info.get("color_profile") and info.get("bit_depth") == 10:
            info["color_profile"] = "D-Log M (likely)"
    else:
        xml = sony_sidecar(path)
        if xml:
            info["sidecars"] = [xml]
            d = parse_sony_xml(xml)
            for k in ("camera", "recorded_at", "lat", "lon", "color_profile"):
                if d.get(k) is not None:
                    info[k] = d[k]
            info["meta"].update({k: d[k] for k in ("lens", "gamut", "capture_fps", "model_code") if k in d})
        elif re.match(r"^C\d{4}\.MP4$", name) or "SONY" in tag_text:
            info["camera"] = "Sony camera"

    info.setdefault("camera", tags.get("com.apple.quicktime.model") or tags.get("model") or "Unknown camera")
    info.setdefault("color_profile", "Standard")
    if not info.get("recorded_at"):
        info["recorded_at"] = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).astimezone().isoformat(timespec="seconds")
    return info


def is_log(profile: str | None) -> bool:
    return bool(profile) and any(k in profile for k in ("Log", "LOG", "log"))
