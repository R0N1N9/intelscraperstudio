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


# ---------------------------------------------------------------- photos

def landscape(w: int = 4800, h: int = 3200, seed: int = 1, vivid: bool = True) -> np.ndarray:
    """A detailed synthetic landscape (BGR): sky, sun, textured mountains, grassy foreground."""
    import cv2
    rng = np.random.default_rng(seed)
    yy = np.linspace(0, 1, h)[:, None]
    img = np.zeros((h, w, 3), np.float32)
    sky_top, sky_bot = (np.array([150, 90, 40]), np.array([90, 170, 250])) if vivid else (np.array([150, 150, 150]), np.array([175, 175, 170]))
    horizon = int(h * 0.55)
    img[:horizon] = sky_top + (sky_bot - sky_top) * (yy[:horizon] / 0.55)[..., None].squeeze(-1)[..., None]
    if vivid:
        cv2.circle(img, (int(w * 0.68), int(h * 0.36)), int(h * 0.06), (190, 235, 255), -1, cv2.LINE_AA)
    xs = np.arange(w)
    ridge = horizon - (h * 0.18 * (0.5 + 0.5 * np.sin(xs / w * 7 + seed))).astype(int) - rng.integers(0, 12, w)
    for x in range(w):
        img[ridge[x]:horizon, x] = (70, 60, 55) if vivid else (110, 110, 110)
    ground = (40, 110, 70) if vivid else (120, 120, 115)
    img[horizon:] = ground
    tex = rng.normal(0, 1, (h // 4, w // 4)).astype(np.float32)
    tex = cv2.resize(tex, (w, h), interpolation=cv2.INTER_NEAREST)
    detail = rng.normal(0, 1, (h, w)).astype(np.float32)
    mask = np.zeros((h, w), np.float32)
    mask[horizon - int(h * 0.2):] = 1
    img += ((tex * 18 + detail * 6) * mask)[..., None]
    for _ in range(400):  # rocks and grass blades
        x, y = int(rng.integers(0, w)), int(rng.integers(horizon, h))
        cv2.line(img, (x, y), (x + int(rng.integers(-6, 6)), y - int(rng.integers(20, 60))), (30, 80, 40) if vivid else (90, 90, 90), 3)
    return np.clip(img, 0, 255).astype(np.uint8)


def save_photo(path: Path, bgr: np.ndarray, when: str, iso: int = 100, model: str = "ILCE-7C", make: str = "SONY") -> None:
    from PIL import Image
    path.parent.mkdir(parents=True, exist_ok=True)
    im = Image.fromarray(bgr[..., ::-1])
    exif = Image.Exif()
    exif[0x010F] = make
    exif[0x0110] = model
    e = exif.get_ifd(0x8769)
    e[0x9003] = when
    e[0x9011] = "-08:00"
    e[0x8827] = iso
    from PIL.TiffImagePlugin import IFDRational
    e[0x829D] = IFDRational(8, 1)  # cameras store these as fractions
    e[0x829A] = IFDRational(1, 250)
    e[0xA434] = "FE 24-105mm F4 G OSS"
    e[0x920A] = IFDRational(35, 1)
    g = exif.get_ifd(0x8825)
    g[1], g[2], g[3], g[4] = "N", (36.0, 22.0, 15.6), "W", (121.0, 54.0, 7.2)
    im.save(path, quality=92, exif=exif.tobytes())


def save_dng(path: Path, rgb01: np.ndarray, model: str = "L2D-20c") -> None:
    from pidng.core import RAW2DNG, DNGTags, Tag
    from pidng.defs import (CalibrationIlluminant, CFAPattern, DNGVersion, Orientation, PhotometricInterpretation,
                            PreviewColorSpace)
    h, w = rgb01.shape[:2]
    bayer = np.zeros((h, w), np.uint16)
    bayer[0::2, 0::2] = rgb01[0::2, 0::2, 0] * 4000
    bayer[0::2, 1::2] = rgb01[0::2, 1::2, 1] * 4000
    bayer[1::2, 0::2] = rgb01[1::2, 0::2, 1] * 4000
    bayer[1::2, 1::2] = rgb01[1::2, 1::2, 2] * 4000
    t = DNGTags()
    ident = [[10000, 10000], [0, 10000], [0, 10000], [0, 10000], [10000, 10000], [0, 10000], [0, 10000], [0, 10000], [10000, 10000]]
    for k, v in [(Tag.ImageWidth, w), (Tag.ImageLength, h), (Tag.TileWidth, w), (Tag.TileLength, h),
                 (Tag.Orientation, Orientation.Horizontal), (Tag.PhotometricInterpretation, PhotometricInterpretation.Color_Filter_Array),
                 (Tag.SamplesPerPixel, 1), (Tag.BitsPerSample, 12), (Tag.CFARepeatPatternDim, [2, 2]), (Tag.CFAPattern, CFAPattern.RGGB),
                 (Tag.BlackLevel, 0), (Tag.WhiteLevel, 4095), (Tag.ColorMatrix1, ident), (Tag.CalibrationIlluminant1, CalibrationIlluminant.D65),
                 (Tag.AsShotNeutral, [[1, 1], [1, 1], [1, 1]]), (Tag.Make, "DJI"), (Tag.Model, model),
                 (Tag.DNGVersion, DNGVersion.V1_4), (Tag.DNGBackwardVersion, DNGVersion.V1_2), (Tag.PreviewColorSpace, PreviewColorSpace.sRGB)]:
        t.set(k, v)
    path.parent.mkdir(parents=True, exist_ok=True)
    r = RAW2DNG()
    r.options(t, path=str(path.parent), compress=False)
    r.convert(bayer, filename=path.stem)
    produced = path.parent / (path.stem + ".dng")
    if produced != path:
        produced.rename(path)


@pytest.fixture(scope="session")
def photo_card(tmp_path_factory) -> Path:
    import cv2
    root = tmp_path_factory.mktemp("photos") / "A7C_PHOTOS"
    d = root / "DCIM" / "100MSDCF"
    hero = landscape(seed=1)
    save_photo(d / "DSC00001.JPG", hero, "2026:11:14 18:40:00")
    for i, dx in ((2, 4), (3, 8)):  # burst: tiny shifts, a second apart, slightly softer
        m = np.float32([[1, 0, dx], [0, 1, 0]])
        save_photo(d / f"DSC0000{i}.JPG", cv2.GaussianBlur(cv2.warpAffine(hero, m, (4800, 3200), borderMode=cv2.BORDER_REFLECT), (3, 3), 0.8),
                   f"2026:11:14 18:40:0{i - 1}")
    save_photo(d / "DSC00004.JPG", cv2.GaussianBlur(landscape(seed=4), (0, 0), 9), "2026:11:14 18:45:00")
    tilt = landscape(seed=5)
    rot = cv2.warpAffine(tilt, cv2.getRotationMatrix2D((2400, 1600), 4, 1.12), (4800, 3200), borderMode=cv2.BORDER_REFLECT)
    save_photo(d / "DSC00005.JPG", rot, "2026:11:14 18:50:00")
    save_photo(d / "DSC00006.JPG", (landscape(seed=6).astype(np.float32) * 0.13).astype(np.uint8), "2026:11:14 18:55:00")
    noisy = landscape(seed=7).astype(np.float32) + np.random.default_rng(7).normal(0, 28, (3200, 4800, 1))
    save_photo(d / "DSC00007.JPG", np.clip(noisy, 0, 255).astype(np.uint8), "2026:11:14 19:00:00", iso=12800)
    save_photo(d / "DSC00008.JPG", landscape(seed=8, vivid=False), "2026:11:14 19:05:00")
    # RAW + JPEG pair from the drone (DNG is decoded through LibRaw)
    dji = root / "DCIM" / "101MEDIA"
    small = landscape(w=2400, h=1600, seed=9)
    save_dng(dji / "DJI_0100.DNG", small[..., ::-1].astype(np.float32) / 255)
    save_photo(dji / "DJI_0100.JPG", small, "2026:11:14 19:10:00", model="L2D-20c", make="DJI")
    return root
