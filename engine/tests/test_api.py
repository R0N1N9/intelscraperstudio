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
