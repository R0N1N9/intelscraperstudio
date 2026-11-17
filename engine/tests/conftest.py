"""Synthetic camera cards and music for tests.

The clips are generated with FFmpeg so each one has a known "right answer":
steady, shaky, blurry, smooth pan, an accidental black clip, and a near-duplicate take.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

SONY_XML = """<?xml version="1.0" encoding="UTF-8"?>
<NonRealTimeMeta xmlns="urn:schemas-professionalDisc:nonRealTimeMeta:ver.2.20" lastUpdate="2026-11-14T18:12:03-08:00">
  <Duration value="150"/>
  <CreationDate value="{date}"/>
  <VideoFormat><VideoFrame videoCodec="AVC_3840_2160_HP@L51" captureFps="29.97p" formatFps="29.97p"/></VideoFormat>
  <Device manufacturer="Sony" modelName="ILCE-7C" serialNo="0000000"/>
  <AcquisitionRecord>
    <Group name="CameraUnitMetadataSet">
      <Item name="CaptureGammaEquation" value="s-log3-cine"/>
      <Item name="CaptureColorPrimaries" value="S-Gamut3.Cine"/>
    </Group>
    <Group name="ExifGPS">
      <Item name="LatitudeRef" value="N"/><Item name="Latitude" value="36:22:15.600"/>
      <Item name="LongitudeRef" value="W"/><Item name="Longitude" value="121:54:07.200"/>
    </Group>
  </AcquisitionRecord>
</NonRealTimeMeta>
"""

DJI_SRT = """1
00:00:00,000 --> 00:00:00,033
<font size="28">FrameCnt: 1, DiffTime: 33ms
{date}
[iso: 100] [shutter: 1/100.0] [fnum: 2.8] [ev: 0] [color_md : dlog_m] [focal_len: 24.00] [latitude: 0.000000] [longitude: 0.000000] [rel_alt: 0.000 abs_alt: 40.1] </font>

2
00:00:00,033 --> 00:00:00,066
<font size="28">FrameCnt: 2, DiffTime: 33ms
{date}
[iso: 100] [shutter: 1/100.0] [fnum: 2.8] [ev: 0] [color_md : dlog_m] [focal_len: 24.00] [latitude: 36.371234] [longitude: -121.902345] [rel_alt: 84.000 abs_alt: 124.1] </font>
"""

BASE = "testsrc2=s=960x540:r=30:d={d}"


def ff(args: list[str]) -> None:
    subprocess.run(["ffmpeg", "-v", "error", "-y", *args], check=True)


def make_clip(out: Path, src: str, vf: str | None = None, d: float = 5) -> None:
    """src is a still image path (a static scene filmed for d seconds) or a lavfi source."""
    out.parent.mkdir(parents=True, exist_ok=True)
    if Path(src).exists():
        args = ["-loop", "1", "-framerate", "30", "-t", str(d), "-i", src]
    else:
        args = ["-f", "lavfi", "-i", src]
    args += ["-f", "lavfi", "-i", f"sine=f=440:d={d}"]
    noise = "noise=alls=6:allf=t"  # a little sensor noise so frames are never pixel-identical
    args += ["-vf", f"{vf},{noise}" if vf else noise]
    ff(args + ["-shortest", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "18", "-pix_fmt", "yuv420p",
               "-c:a", "aac", str(out)])


@pytest.fixture(scope="session")
def cards(tmp_path_factory) -> dict:
    root = tmp_path_factory.mktemp("cards")
    still = str(root / "scene.png")
    ff(["-f", "lavfi", "-i", "testsrc2=s=960x540", "-frames:v", "1", still])
    sony = root / "SONY_CARD"
    clip = sony / "PRIVATE" / "M4ROOT" / "CLIP"
    crop = "crop=640:360:{x}:{y}"
    make_clip(clip / "C0001.MP4", still, crop.format(x=160, y=90))                                  # steady
    make_clip(clip / "C0002.MP4", still,
              crop.format(x="160+40*(random(1)-0.5)", y="90+30*(random(2)-0.5)"))                    # shaky
    make_clip(clip / "C0003.MP4", still, crop.format(x=160, y=90) + ",gblur=sigma=9")                # blurry
    make_clip(clip / "C0004.MP4", "color=c=black:s=640x360:r=30:d=1.2", d=1.2)                      # accidental
    make_clip(clip / "C0005.MP4", still, crop.format(x=162, y=90) + ",eq=brightness=0.02")          # near-dup of C0001
    for i, minute in zip(range(1, 6), (10, 11, 12, 13, 14)):
        (clip / f"C000{i}M01.XML").write_text(SONY_XML.format(date=f"2026-11-14T18:{minute}:00-08:00"))
    # Sony proxy folder must be ignored
    make_clip(sony / "PRIVATE" / "M4ROOT" / "SUB" / "C0001S03.MP4", "color=c=gray:s=320x180:r=30:d=1", d=1)

    dji = root / "DJI_CARD"
    d = dji / "DCIM" / "100MEDIA"
    make_clip(d / "DJI_0001.MP4", still, crop.format(x=100, y=60) + ",hflip")
    make_clip(d / "DJI_0002.MP4", still, crop.format(x="10+t*50", y=90))                 # smooth pan
    for i, minute in ((1, 25), (2, 27)):
        (d / f"DJI_000{i}.SRT").write_text(DJI_SRT.format(date=f"2026-11-14 18:{minute}:11.123"))
    return {"sony": sony, "dji": dji}


@pytest.fixture()
def home(tmp_path, monkeypatch):
    # DJI drones write local clock time without a timezone; the PC's timezone resolves it.
    import time
    monkeypatch.setenv("TZ", "America/Los_Angeles")
    time.tzset()
    monkeypatch.setenv("ISS_HOME", str(tmp_path / "app"))
    from iss import config, db
    config.save({"library_root": str(tmp_path / "Library"), "backup_root": str(tmp_path / "Backup"),
                 "make_proxies": True})
    db.init()
    return tmp_path


def synth_song(path: Path, bpm: float = 120, seconds: float = 64, sr: int = 22050) -> None:
    """Kick + hat pattern with a quiet first half and a loud second half."""
    t = np.arange(int(seconds * sr)) / sr
    y = np.zeros_like(t)
    beat = 60 / bpm
    rng = np.random.default_rng(0)
    for k in range(int(seconds / beat)):
        s = int(k * beat * sr)
        n = int(0.12 * sr)
        env = np.exp(-np.linspace(0, 8, n))
        loud = 1.0 if k * beat > seconds / 2 else 0.35
        seg = slice(s, min(s + n, len(y)))
        m = seg.stop - seg.start
        y[seg] += loud * 0.9 * np.sin(2 * np.pi * np.linspace(150, 45, m) * np.arange(m) / sr) * env[:m]
        h = int((k + 0.5) * beat * sr)
        hn = min(int(0.03 * sr), len(y) - h)
        if hn > 0:
            y[h:h + hn] += loud * 0.25 * rng.standard_normal(hn) * np.exp(-np.linspace(0, 6, hn))
    pad = 0.15 * np.sin(2 * np.pi * 220 * t) * (0.4 + 0.6 * (t > seconds / 2))
    y = (y + pad) / 1.6
    raw = path.with_suffix(".f32")
    y.astype(np.float32).tofile(raw)
    ff(["-f", "f32le", "-ar", str(sr), "-ac", "1", "-i", str(raw), str(path)])
    raw.unlink()
