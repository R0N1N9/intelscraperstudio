"""Command line.

  python -m iss                      start the app (opens your browser)
  python -m iss import <folder>      import a card or folder of footage
  python -m iss music <folder>       import and analyze a music folder
"""
from __future__ import annotations

import argparse
import threading
import webbrowser


def _print_progress(done: int, total: int, msg: str) -> None:
    print(f"  [{done}/{total}] {msg}", flush=True)


def main() -> None:
    ap = argparse.ArgumentParser(prog="intelscraperstudio")
    sub = ap.add_subparsers(dest="cmd")
    s = sub.add_parser("serve", help="start the app (default)")
    s.add_argument("--port", type=int, default=8765)
    s.add_argument("--no-browser", action="store_true")
    sub.add_parser("import", help="import footage").add_argument("folder")
    sub.add_parser("music", help="import a music folder").add_argument("folder")
    args = ap.parse_args()

    from . import analyze, db, ingest, music
    db.init()
    if args.cmd == "import":
        res = ingest.import_source(args.folder, _print_progress)
        analyze.analyze_many(res["clip_ids"], _print_progress)
        print(f"Imported {res['imported']} files, skipped {res['skipped']} already in the library.")
        return
    if args.cmd == "music":
        print(music.import_folder(args.folder, _print_progress))
        return

    import uvicorn
    port = getattr(args, "port", 8765)
    url = f"http://127.0.0.1:{port}"
    if not getattr(args, "no_browser", False):
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    print(f"intelscraperstudio is running at {url}  (close this window to stop it)")
    uvicorn.run("iss.server:app", host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    main()
