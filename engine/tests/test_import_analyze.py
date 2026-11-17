from pathlib import Path

from iss import analyze, db, ingest, media


def clips_by_name():
    with db.session() as con:
        return {c["src_name"]: c for c in db.rows(con.execute("SELECT * FROM clips"))}


def test_metadata_from_sidecars(cards):
    sony = media.inspect(cards["sony"] / "PRIVATE/M4ROOT/CLIP/C0001.MP4")
    assert sony["camera"] == "Sony A7C"
    assert sony["color_profile"] == "S-Log3"
    assert abs(sony["lat"] - 36.371) < 0.001 and abs(sony["lon"] + 121.902) < 0.001
    assert sony["recorded_at"].startswith("2026-11-14T18:10")
    dji = media.inspect(cards["dji"] / "DCIM/100MEDIA/DJI_0001.MP4")
    assert dji["camera"] == "DJI Mavic 3"
    assert dji["color_profile"] == "D-Log M"
    assert dji["lat"] == 36.371234  # zero coordinates before GPS fix are skipped
    assert dji["meta"]["iso"] == "100" and dji["meta"]["aperture"] == "f/2.8"


def test_import_copies_verifies_and_skips_duplicates(cards, home):
    res = ingest.import_source(cards["sony"])
    assert res["imported"] == 5  # SUB proxy folder ignored
    c = clips_by_name()
    for name, clip in c.items():
        assert Path(clip["path"]).exists() and Path(clip["backup_path"]).exists()
        assert "Sony A7C" in clip["path"]
        assert Path(clip["path"]).with_name(name.replace(".MP4", "M01.XML")).exists()  # sidecar kept
    # originals untouched
    assert (cards["sony"] / "PRIVATE/M4ROOT/CLIP/C0001.MP4").exists()
    again = ingest.import_source(cards["sony"])
    assert again["imported"] == 0 and again["skipped"] == 5

    res2 = ingest.import_source(cards["dji"])
    assert res2["imported"] == 2
    with db.session() as con:
        shoots = db.rows(con.execute("SELECT * FROM shoots"))
    assert len(shoots) == 1  # same evening, same shoot
    assert abs(shoots[0]["lat"] - 36.371) < 0.01


def test_analysis_scores_make_sense(cards, home):
    ids = ingest.import_source(cards["sony"])["clip_ids"] + ingest.import_source(cards["dji"])["clip_ids"]
    analyze.analyze_many(ids)
    c = clips_by_name()
    for clip in c.values():
        assert clip["status"] == "analyzed", clip["error"]
        assert Path(clip["thumb"]).exists() and Path(clip["proxy"]).exists()
    steady, shaky, blurry, black, dup = (c[f"C000{i}.MP4"] for i in range(1, 6))
    pan = c["DJI_0002.MP4"]
    assert steady["metrics"]["steady"] > shaky["metrics"]["steady"] + 30
    assert steady["metrics"]["focus"] > blurry["metrics"]["focus"] + 30
    assert pan["metrics"]["steady"] >= 80, pan["metrics"]  # smooth move is not shake
    assert black["verdict"] == "reject" and "accident" in " ".join(black["reasons"]).lower()
    assert shaky["verdict"] == "reject" and blurry["verdict"] == "reject"
    assert "shaky" in shaky["reasons"][0].lower()
    assert steady["verdict"] == "keep"
    assert dup["similar_to"] == steady["id"] or steady["similar_to"] == dup["id"]
    assert 0 <= steady["best_start"] < steady["best_end"] <= steady["duration"] + 0.01
