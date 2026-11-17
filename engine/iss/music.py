"""Music library: tempo, beats, energy, song sections and an estimated vibe for every song.

The editor (step 2) uses this to cut on beats, hold shots in quiet sections, cut faster
in choruses and drops, and pick songs that match the chosen vibe.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Callable

import librosa
import numpy as np

from . import config, db, media

AUDIO_EXT = {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".aif", ".aiff", ".opus", ".wma"}
VIBES = ["cinematic", "energetic", "chill", "dreamy", "nostalgic", "adventure"]
SR = 22050
HOP = 512
MAX_SECONDS = 720

Progress = Callable[[int, int, str], None]


def _noop(done: int, total: int, msg: str) -> None:
    pass


def decode(path: str) -> np.ndarray:
    raw = media.run([media.tool("ffmpeg"), "-v", "error", "-i", path, "-vn", "-ac", "1", "-ar", str(SR),
                     "-t", str(MAX_SECONDS), "-f", "f32le", "-"], timeout=300, binary=True)
    y = np.frombuffer(raw, np.float32)
    if y.size < SR * 2:
        raise ValueError("Song is too short or could not be decoded.")
    return y


def _g(x: float, c: float, w: float) -> float:
    return float(np.exp(-((x - c) / w) ** 2))


def _fold_tempo(t: float) -> float:
    while t < 60:
        t *= 2
    while t > 185:
        t /= 2
    return t


def resolve_octave(tempo: float, onset_rate: float) -> float:
    """Beat trackers often lock onto double or half the felt tempo.
    A song really at the tracked tempo has its notes subdividing the beat (hi-hats, eighths);
    a slow song tracked at double speed has only about one note per tracked beat."""
    per_beat = onset_rate / (tempo / 60)
    if tempo > 124 and per_beat < 1.25:
        return tempo / 2
    if tempo < 75 and per_beat > 3.5:
        return tempo * 2
    return tempo


def change_octave(bpm: float, beats: list[float], how: str) -> tuple[float, list[float]]:
    """Halve or double the tempo and its beat grid (user correction)."""
    if how == "half":
        return round(bpm / 2, 1), beats[::2]
    mids = [round((a + b) / 2, 3) for a, b in zip(beats, beats[1:])]
    return round(bpm * 2, 1), sorted(beats + mids)


def sections_for(y: np.ndarray, beats: np.ndarray, energy_frames: np.ndarray, duration: float, tempo: float,
                 perc: float) -> list[dict]:
    """Split the song into sections and name them by energy (Intro, Verse, Chorus, Drop, Bridge, Outro)."""
    if len(beats) < 16:
        return [{"name": "Full song", "start": 0.0, "end": round(duration, 2), "energy": 0.5}]
    chroma = librosa.feature.chroma_cqt(y=y, sr=SR, hop_length=HOP)
    mfcc = librosa.feature.mfcc(y=y, sr=SR, hop_length=HOP, n_mfcc=13)
    n = min(chroma.shape[1], mfcc.shape[1], len(energy_frames))
    feats = np.vstack([chroma[:, :n], mfcc[:, :n] / 40.0, 4 * energy_frames[None, :n]])
    bars = beats[::4]  # cut points on bar lines
    bars = bars[bars < n]
    if len(bars) < 4:
        bars = beats[beats < n]
    synced = librosa.util.sync(feats, list(bars), aggregate=np.median)
    k = int(np.clip(round(duration / 24), 3, min(10, synced.shape[1] - 1)))
    bounds = librosa.segment.agglomerative(synced, k)
    edge_frames = [0] + [int(bars[b - 1]) if 0 < b <= len(bars) else 0 for b in bounds[1:]]
    times = sorted({round(float(librosa.frames_to_time(f, sr=SR, hop_length=HOP)), 2) for f in edge_frames})
    times = [t for t in times if 0 <= t < duration - 4] + [round(duration, 2)]
    if times[0] != 0:
        times = [0.0] + times
    merged = [times[0]]
    for t in times[1:-1]:  # drop sections shorter than 8 s
        if t - merged[-1] >= 8:
            merged.append(t)
    if times[-1] - merged[-1] < 8 and len(merged) > 1:
        merged.pop()
    merged.append(times[-1])

    sec_t = librosa.frames_to_time(np.arange(len(energy_frames)), sr=SR, hop_length=HOP)
    secs = []
    for a, b in zip(merged[:-1], merged[1:]):
        m = (sec_t >= a) & (sec_t < b)
        secs.append({"start": a, "end": b, "energy": round(float(energy_frames[m].mean()) if m.any() else 0.0, 3)})
    es = np.array([s["energy"] for s in secs])
    hi_q, lo_q, med = np.quantile(es, 0.7), np.quantile(es, 0.35), np.median(es)
    for i, s in enumerate(secs):
        e = s["energy"]
        prev_e = secs[i - 1]["energy"] if i else e
        nxt = secs[i + 1]["energy"] if i + 1 < len(secs) else e
        if i == 0 and e <= med:
            name = "Intro"
        elif i == len(secs) - 1 and e <= med:
            name = "Outro"
        elif e >= hi_q:
            name = "Drop" if (tempo >= 110 and perc > 0.45 and e - prev_e > 0.2) else "Chorus"
        elif nxt >= hi_q and nxt - e > 0.12:
            name = "Build"
        elif e <= lo_q and i > len(secs) / 2:
            name = "Bridge"
        else:
            name = "Verse"
        s["name"] = name
    return secs


def vibe_scores(f: dict) -> dict:
    T = f["tempo"]
    L = float(np.clip((f["loudness_db"] + 30) / 20, 0, 1))
    D = float(np.clip(f["dynamics"] / 0.6, 0, 1))
    B = float(np.clip((f["brightness_hz"] - 1000) / 3000, 0, 1))
    P = float(np.clip((f["percussive"] - 0.15) / 0.5, 0, 1))
    O = float(np.clip(f["onset_rate"] / 5, 0, 1))
    raw = {
        "energetic": .30 * _g(T, 128, 22) + .25 * L + .25 * P + .20 * O,
        "chill": .30 * _g(T, 86, 14) + .25 * (1 - L) + .20 * (1 - D) + .25 * (1 - B),
        "dreamy": .30 * _g(T, 72, 16) + .30 * (1 - P) + .20 * (1 - O) + .20 * (1 - L),
        "cinematic": .35 * D + .20 * _g(T, 92, 25) + .20 * L + .25 * (1 - O),
        "nostalgic": .30 * _g(T, 96, 14) + .25 * (1 - B) + .25 * _g(P, .5, .3) + .20 * (1 - D),
        "adventure": .30 * _g(T, 110, 14) + .25 * L + .25 * B + .20 * _g(P, .55, .3),
    }
    return {k: round(v, 3) for k, v in raw.items()}


def analyze_track(path: str) -> dict:
    y = decode(path)
    probe = media.ffprobe(Path(path))
    try:
        duration = float(probe["format"]["duration"])
    except (KeyError, TypeError, ValueError):
        duration = len(y) / SR
    analyzed = len(y) / SR

    onset_env = librosa.onset.onset_strength(y=y, sr=SR, hop_length=HOP)
    tempo, beats = librosa.beat.beat_track(onset_envelope=onset_env, sr=SR, hop_length=HOP)
    tempo = _fold_tempo(float(np.atleast_1d(tempo)[0]) or 100.0)
    onsets = librosa.onset.onset_detect(onset_envelope=onset_env, sr=SR, hop_length=HOP)
    felt = resolve_octave(tempo, len(onsets) / (len(y) / SR))
    if abs(felt - tempo) > 1:  # re-track beats at the corrected tempo
        tempo, beats = librosa.beat.beat_track(onset_envelope=onset_env, sr=SR, hop_length=HOP, start_bpm=felt,
                                               tightness=400)
        tempo = float(np.atleast_1d(tempo)[0])
        if abs(tempo - felt) > felt * 0.2:  # tracker still insists on the other octave: thin or fill its grid
            beats = beats[::2] if tempo > felt else np.sort(np.concatenate([beats, (beats[:-1] + beats[1:]) // 2]))
        tempo = felt
    beat_times = librosa.frames_to_time(beats, sr=SR, hop_length=HOP)

    rms = librosa.feature.rms(y=y, hop_length=HOP)[0]
    ref = np.percentile(rms, 95) or 1.0
    energy_frames = np.clip(rms / ref, 0, 1)
    fps = SR / HOP
    per_sec = [round(float(energy_frames[int(i * fps):int((i + 1) * fps)].mean()), 3)
               for i in range(int(analyzed)) if int(i * fps) < len(energy_frames)]

    mid = slice(int(len(y) * 0.3), int(len(y) * 0.3) + SR * 45)  # 45 s from the middle
    excerpt = y[mid] if y[mid].size > SR * 5 else y
    harm, perc = librosa.effects.hpss(excerpt)
    perc_ratio = float(np.sqrt(np.mean(perc ** 2)) / (np.sqrt(np.mean(excerpt ** 2)) + 1e-9))
    feats = {
        "tempo": round(tempo, 1),
        "loudness_db": round(float(20 * np.log10(np.sqrt(np.mean(y ** 2)) + 1e-9)), 1),
        "dynamics": round(float(np.percentile(energy_frames, 90) - np.percentile(energy_frames, 10)), 3),
        "brightness_hz": round(float(np.mean(librosa.feature.spectral_centroid(y=excerpt, sr=SR))), 0),
        "percussive": round(perc_ratio, 3),
        "onset_rate": round(len(onsets) / analyzed, 2),
    }
    secs = sections_for(y, beats, energy_frames, analyzed, tempo, perc_ratio)
    if duration > analyzed + 1:  # very long file: only the first part was analyzed
        secs[-1]["end"] = round(duration, 2)
    scores = vibe_scores(feats)
    return {
        "duration": round(duration, 2), "bpm": round(tempo, 1),
        "beats": [round(float(t), 3) for t in beat_times], "energy": per_sec, "sections": secs,
        "vibe": max(scores, key=scores.get), "vibe_scores": scores, "features": feats,
    }


def _title(path: Path) -> str:
    try:
        tags = {k.lower(): v for k, v in (media.ffprobe(path).get("format", {}).get("tags") or {}).items()}
    except Exception:
        tags = {}
    t = tags.get("title")
    a = tags.get("artist")
    return f"{t} · {a}" if t and a else t or path.stem


def scan_folder(folder: str | Path) -> list[Path]:
    out = []
    for dirpath, dirnames, filenames in os.walk(folder):
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        out += [Path(dirpath) / f for f in filenames if Path(f).suffix.lower() in AUDIO_EXT and not f.startswith(".")]
    return sorted(out)


def import_folder(folder: str | Path, progress: Progress = _noop) -> dict:
    folder = Path(folder)
    if not folder.is_dir():
        raise FileNotFoundError(f"Folder not found: {folder}")
    files = scan_folder(folder)
    progress(0, len(files), f"Found {len(files)} songs")
    todo = []
    with db.session() as con:
        for f in files:
            mtime = f.stat().st_mtime
            r = con.execute("SELECT id, mtime, status FROM tracks WHERE path=?", (str(f),)).fetchone()
            if r and r["mtime"] == mtime and r["status"] == "analyzed":
                continue
            if r:
                con.execute("UPDATE tracks SET mtime=?, status='new' WHERE id=?", (mtime, r["id"]))
                todo.append((r["id"], f))
            else:
                cur = con.execute("INSERT INTO tracks(path, folder, title, mtime) VALUES (?,?,?,?)",
                                  (str(f), str(folder), _title(f), mtime))
                todo.append((cur.lastrowid, f))
    cfg = config.load()
    if str(folder) not in cfg["music_folders"]:
        config.save({"music_folders": cfg["music_folders"] + [str(folder)]})
    done = errors = 0
    for i, (tid, f) in enumerate(todo):
        progress(i, len(todo), f"Listening to {f.name} ({i + 1} of {len(todo)})")
        try:
            a = analyze_track(str(f))
            with db.session() as con:
                con.execute("""UPDATE tracks SET duration=?, bpm=?, beats=?, energy=?, sections=?, vibe_auto=?,
                               vibe=COALESCE(vibe, ?), vibe_scores=?, features=?, status='analyzed', error=NULL
                               WHERE id=?""",
                            (a["duration"], a["bpm"], db.dumps(a["beats"]), db.dumps(a["energy"]),
                             db.dumps(a["sections"]), a["vibe"], a["vibe"], db.dumps(a["vibe_scores"]),
                             db.dumps(a["features"]), tid))
            done += 1
        except Exception as e:
            errors += 1
            with db.session() as con:
                con.execute("UPDATE tracks SET status='error', error=? WHERE id=?", (str(e)[:300], tid))
    progress(len(todo), len(todo), f"Analyzed {done} songs")
    return {"songs": len(files), "analyzed": done, "errors": errors, "unchanged": len(files) - len(todo)}
