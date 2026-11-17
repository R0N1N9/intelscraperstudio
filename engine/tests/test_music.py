from conftest import synth_song

from iss import db, music


def test_tempo_sections_and_vibe(tmp_path, home):
    folder = tmp_path / "Music" / "Edit songs"
    folder.mkdir(parents=True)
    synth_song(folder / "drive.mp3", bpm=120, seconds=64)
    synth_song(folder / "slow.wav", bpm=84, seconds=48)
    (folder / "notes.txt").write_text("not music")

    res = music.import_folder(folder)
    assert res == {"songs": 2, "analyzed": 2, "errors": 0, "unchanged": 0}
    with db.session() as con:
        t = {r["path"].rsplit("/", 1)[-1]: r for r in db.rows(con.execute("SELECT * FROM tracks"))}

    drive = t["drive.mp3"]
    assert abs(drive["bpm"] - 120) < 3, drive["bpm"]
    assert abs(t["slow.wav"]["bpm"] - 84) < 3, t["slow.wav"]["bpm"]
    beats = drive["beats"]
    gaps = [b - a for a, b in zip(beats, beats[1:])]
    assert abs(sorted(gaps)[len(gaps) // 2] - 0.5) < 0.03  # beats every half second
    assert abs(drive["duration"] - 64) < 1
    secs = drive["sections"]
    assert secs[0]["start"] == 0 and abs(secs[-1]["end"] - drive["duration"]) < 1
    assert all(b["start"] == a["end"] for a, b in zip(secs, secs[1:]))
    # quiet first half, loud second half: the louder sections come later
    assert secs[-1]["energy"] > secs[0]["energy"] or any(s["name"] in ("Chorus", "Drop") for s in secs[1:])
    assert drive["vibe"] in music.VIBES and set(drive["vibe_scores"]) == set(music.VIBES)
    assert len(drive["energy"]) >= 60

    again = music.import_folder(folder)
    assert again["unchanged"] == 2 and again["analyzed"] == 0


def test_slow_and_fast_tempos_not_doubled_or_halved(tmp_path):
    for bpm in (72, 140):
        synth_song(tmp_path / f"{bpm}.wav", bpm=bpm, seconds=40)
        got = music.analyze_track(str(tmp_path / f"{bpm}.wav"))["bpm"]
        assert abs(got - bpm) < 5, (bpm, got)


def test_change_octave():
    bpm, beats = music.change_octave(140, [0, 0.43, 0.86, 1.29], "half")
    assert bpm == 70 and beats == [0, 0.86]
    bpm, beats = music.change_octave(70, [0, 0.86], "double")
    assert bpm == 140 and beats == [0, 0.43, 0.86]
