import time

from conftest import synth_song
from fastapi.testclient import TestClient


def wait(client, job_id, timeout=120):
    t0 = time.time()
    while time.time() - t0 < timeout:
        j = client.get(f"/api/jobs/{job_id}").json()
        if j["status"] in ("done", "error"):
            return j
        time.sleep(0.3)
    raise TimeoutError(job_id)


def test_full_flow_through_api(cards, home, tmp_path):
    from iss.server import app
    with TestClient(app) as client:
        st = client.get("/api/status").json()
        assert st["ffmpeg"] and st["counts"]["clips"] == 0

        assert client.get("/api/fs").json()["dirs"]
        listing = client.get("/api/fs", params={"path": str(cards["sony"] / "PRIVATE/M4ROOT/CLIP")}).json()
        assert listing["video_files"] == 5

        assert client.post("/api/import", json={"path": "/does/not/exist"}).status_code == 400
        j = wait(client, client.post("/api/import", json={"path": str(cards["sony"])}).json()["id"])
        assert j["status"] == "done", j["error"]
        assert j["result"]["imported"] == 5

        shoots = client.get("/api/shoots").json()
        assert len(shoots) == 1
        s = shoots[0]
        assert s["clips"] == 5 and s["keep"] >= 1 and s["reject"] >= 2 and s["cover"]
        assert client.patch(f"/api/shoots/{s['id']}", json={"name": "Big Sur Coast"}).json()["ok"]

        clips = client.get(f"/api/shoots/{s['id']}/clips").json()
        shaky = next(c for c in clips if c["src_name"] == "C0002.MP4")
        assert shaky["final"] == "reject"
        client.post(f"/api/clips/{shaky['id']}/verdict", json={"verdict": "keep"})
        again = {c["id"]: c for c in client.get(f"/api/shoots/{s['id']}/clips").json()}
        assert again[shaky["id"]]["final"] == "keep" and again[shaky["id"]]["verdict"] == "reject"
        assert client.post(f"/api/clips/{shaky['id']}/verdict", json={"verdict": "maybe"}).status_code == 400

        r = client.get(f"/api/clips/{shaky['id']}/thumb")
        assert r.status_code == 200 and r.content[:2] == b"\xff\xd8"
        r = client.get(f"/api/clips/{shaky['id']}/video", headers={"Range": "bytes=0-99"})
        assert r.status_code in (200, 206) and len(r.content) >= 100

        music_dir = tmp_path / "songs"
        music_dir.mkdir()
        synth_song(music_dir / "a.mp3", bpm=120, seconds=40)
        j = wait(client, client.post("/api/music/import", json={"path": str(music_dir)}).json()["id"])
        assert j["status"] == "done" and j["result"]["analyzed"] == 1
        t = client.get("/api/music").json()[0]
        assert t["status"] == "analyzed" and t["sections"]
        assert client.patch(f"/api/music/{t['id']}", json={"vibe": "dreamy"}).json()["ok"]
        assert client.get("/api/music").json()[0]["vibe"] == "dreamy"
        assert client.patch(f"/api/music/{t['id']}", json={"vibe": "loud"}).status_code == 400
        before = t["bpm"]
        assert client.patch(f"/api/music/{t['id']}", json={"tempo": "half"}).json()["ok"]
        assert abs(client.get("/api/music").json()[0]["bpm"] - before / 2) < 0.2
        assert client.get(f"/api/music/{t['id']}/audio").status_code == 200

        assert client.get("/").status_code == 200  # UI served


def test_sorting_overrides_export_and_auto_import(photo_card, home, monkeypatch):
    from iss import config, ingest
    from iss.server import app

    # simulate plugging in a card while auto-import is on
    config.save({"auto_import": True})
    real_describe = ingest.describe_source
    monkeypatch.setattr(ingest, "find_sources", lambda: [real_describe(photo_card)])  # card stays plugged in
    with TestClient(app) as client:
        before = {j["id"] for j in client.get("/api/jobs").json()}
        new = lambda: [j for j in client.get("/api/jobs").json() if j["id"] not in before]  # noqa: E731
        t0 = time.time()
        while time.time() - t0 < 30 and not new():
            time.sleep(0.3)
        jobs = new()
        assert jobs and jobs[0]["kind"] == "import", "auto-import did not start"
        j = wait(client, jobs[0]["id"])
        assert j["status"] == "done", j["error"]
        time.sleep(6)  # one more watcher cycle with the card still plugged in
        assert len(new()) == 1  # the watcher doesn't start a second import of the same card

        shoot = client.get("/api/shoots").json()[0]
        assert shoot["photos"] == 9 and shoot["portfolio"] >= 1 and shoot["stock"] >= 2
        items = {c["src_name"]: c for c in client.get(f"/api/shoots/{shoot['id']}/clips").json()}
        assert "DJI_0100.JPG" not in items and items["DJI_0100.DNG"]["companion"] == "DJI_0100.JPG"
        hero = items["DSC00001.JPG"]
        assert hero["portfolio"] and hero["stock"]

        assert client.post(f"/api/clips/{hero['id']}/uses", json={"collection": "stock", "value": False}).json()["ok"]
        hero = {c["src_name"]: c for c in client.get(f"/api/shoots/{shoot['id']}/clips").json()}["DSC00001.JPG"]
        assert hero["stock"] is False and hero["user_uses"] == {"stock": False}
        client.post(f"/api/clips/{hero['id']}/uses", json={"collection": "stock", "value": None})
        hero = {c["src_name"]: c for c in client.get(f"/api/shoots/{shoot['id']}/clips").json()}["DSC00001.JPG"]
        assert hero["stock"] is True and not hero["user_uses"]
        assert client.post(f"/api/clips/{hero['id']}/uses", json={"collection": "print", "value": True}).status_code == 400

        r = client.get(f"/api/clips/{hero['id']}/preview")
        assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"

        j = wait(client, client.post("/api/export", json={"collection": "portfolio", "shoot_id": shoot["id"]}).json()["id"])
        assert j["status"] == "done" and j["result"]["items"] == shoot["portfolio"]
        cols = client.get("/api/collections").json()
        assert cols["portfolio"]["count"] == shoot["portfolio"] and cols["stock"]["folder"].endswith("Stock")

        # changing the portfolio share re-ranks existing shoots
        client.put("/api/config", json={"portfolio_share": 0.5})
        assert client.get("/api/shoots").json()[0]["portfolio"] >= shoot["portfolio"]
