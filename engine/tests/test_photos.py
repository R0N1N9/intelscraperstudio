from pathlib import Path

from iss import analyze, db, ingest, media, sorting


def items():
    with db.session() as con:
        return {c["src_name"]: c for c in db.rows(con.execute("SELECT * FROM clips"))}


def test_photo_metadata(photo_card):
    info = media.inspect(photo_card / "DCIM/100MSDCF/DSC00001.JPG")
    assert info["kind"] == "photo" and info["camera"] == "Sony A7C"
    assert info["recorded_at"] == "2026-11-14T18:40:00-08:00"
    assert info["megapixels"] == 15.4 and abs(info["lat"] - 36.371) < 0.001
    assert info["meta"]["aperture"] == "f/8.0" and info["meta"]["shutter"] == "1/250" and info["meta"]["iso"] == "100"
    raw = media.inspect(photo_card / "DCIM/101MEDIA/DJI_0100.DNG")
    assert raw["camera"] == "DJI Mavic 3" and raw["width"] == 2400 and raw["meta"]["raw"]


def test_photos_sorted_into_stock_and_portfolio(photo_card, home):
    res = ingest.import_source(photo_card)
    assert res["imported"] == 10
    analyze.analyze_many(res["clip_ids"])
    p = items()
    for name, c in p.items():
        assert c["status"] == "analyzed", (name, c["error"])

    # RAW + JPEG pair counts as one photo; the RAW (decoded through LibRaw) is the one analyzed
    assert p["DJI_0100.JPG"]["companion_of"] == p["DJI_0100.DNG"]["id"]
    assert p["DJI_0100.DNG"]["score"] is not None and Path(p["DJI_0100.DNG"]["thumb"]).exists()

    hero, burst2, burst3 = p["DSC00001.JPG"], p["DSC00002.JPG"], p["DSC00003.JPG"]
    blurry, tilted, dark, noisy, plain = (p[f"DSC0000{i}.JPG"] for i in range(4, 9))

    assert hero["verdict"] == "keep"
    assert burst2["similar_to"] == hero["id"] and burst3["similar_to"] == hero["id"]  # best frame of the burst wins
    assert blurry["verdict"] == "reject" and "soft" in blurry["reasons"][0].lower()
    assert "underexposed" in " ".join(dark["reasons"]).lower()
    assert "noisy" in " ".join(noisy["reasons"]).lower() and noisy["verdict"] != "keep"
    assert "tilted" in " ".join(tilted["reasons"]).lower()

    use = {n: sorting.effective(c) for n, c in p.items() if not c["companion_of"]}
    assert use["DSC00001.JPG"] == {"stock": True, "portfolio": True}
    assert use["DSC00008.JPG"]["stock"] and not use["DSC00008.JPG"]["portfolio"]  # clean but flat: stock, not art
    for n in ("DSC00002.JPG", "DSC00003.JPG", "DSC00004.JPG", "DSC00005.JPG", "DSC00006.JPG", "DSC00007.JPG"):
        assert not use[n]["stock"], (n, p[n]["uses"])
    assert "horizon tilted" in tilted["uses"]["notes"]["stock"]
    assert "Prints sharp up to 16×10.7 in at 300 dpi" in hero["uses"]["notes"]["portfolio"]
    assert not use["DJI_0100.DNG"]["portfolio"]  # 3.8 MP is too small for large prints


def test_overrides_and_export(photo_card, cards, home):
    from conftest import make_clip
    ids = ingest.import_source(photo_card)["clip_ids"]
    hd = cards["sony"].parent / "HD_CARD" / "PRIVATE" / "M4ROOT" / "CLIP"
    import cv2
    from conftest import landscape
    still = hd.parents[2] / "hd_scene.png"
    hd.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(still), landscape(w=1920, h=1080, seed=11))
    make_clip(hd / "C0101.MP4", str(still), None, d=6)  # tripod shot of a still scene, 1080p, 6 s
    ids += ingest.import_source(hd.parents[2])["clip_ids"]
    analyze.analyze_many(ids)
    p = items()
    clip = p["C0101.MP4"]
    assert clip["width"] == 1920 and clip["uses"]["portfolio"] is None
    assert clip["uses"]["stock"], clip["uses"]
    assert clip["uses"]["notes"]["stock"].startswith("Steady, sharp HD clip")
    assert not p["DSC00005.JPG"]["uses"]["portfolio"]  # tilted horizon: fix it first

    # you can overrule the app either way
    with db.session() as con:
        con.execute("UPDATE clips SET user_uses=? WHERE src_name='DSC00008.JPG'", (db.dumps({"portfolio": True}),))
        con.execute("UPDATE clips SET user_uses=? WHERE src_name='DSC00001.JPG'", (db.dumps({"stock": False}),))
    p = items()
    assert sorting.effective(p["DSC00008.JPG"]) == {"stock": True, "portfolio": True}
    assert sorting.effective(p["DSC00001.JPG"])["stock"] is False

    r = sorting.export_collection("portfolio")
    folder = Path(r["folder"])
    files = {f.name for f in folder.rglob("*") if f.is_file()}
    assert {"DSC00001.JPG", "DSC00008.JPG", "details.csv"} <= files
    sheet = next(folder.rglob("details.csv")).read_text(encoding="utf-8")
    assert "DSC00008.JPG" in sheet and "Your pick" in sheet
    r = sorting.export_collection("stock")
    stock_files = {f.name for f in Path(r["folder"]).rglob("*") if f.is_file()}
    assert "DSC00001.JPG" not in stock_files and "DSC00008.JPG" in stock_files and "stock_metadata.csv" in stock_files
    again = sorting.export_collection("stock")
    assert again["exists"] == again["items"] and again["linked"] == again["copied"] == 0  # re-export is harmless
