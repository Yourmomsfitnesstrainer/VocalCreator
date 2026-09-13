"""Serve an isolated real-browser conformance page; never changes user jobs.

Run: .venv/bin/python scripts/check_studio_browser.py --port 8082
Then open http://127.0.0.1:8082/__checks and press the check button.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8082)
    parser.add_argument("--output", type=Path, default=ROOT / "output/v2-qa")
    parser.add_argument("--data-dir", type=Path, help="Separate persistent library for this development server")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    os.environ["VOCAL_CREATOR_DATA_DIR"] = str(args.data_dir.resolve() if args.data_dir else args.output.resolve() / "browser-data")
    from fastapi import Body
    from fastapi.responses import HTMLResponse, FileResponse
    from karaoke_generator.web import app, index

    @app.get("/__checks", response_class=HTMLResponse)
    def checks():
        return index().replace("</body>", '<script src="/__checks.js" defer></script></body>')

    @app.get("/__checks.js")
    def checks_script():
        return FileResponse(ROOT / "tests/browser_checks.js", media_type="application/javascript", headers={"Cache-Control": "no-store"})

    @app.post("/__checks/report")
    def report(payload: dict = Body(...)):
        (args.output / "browser-report.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return {"saved": True}

    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
