from __future__ import annotations

import hashlib
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from . import __version__
from .studio_web import router as studio_router
from .manual_web import router as manual_router
from .manual_cycles_web import router as manual_cycles_router
from .syllable_score_web import router as syllable_score_router

app = FastAPI(title="VocalCreator", version=__version__)
STATIC_ROOT = Path(__file__).with_name("static")
app.mount("/static", StaticFiles(directory=STATIC_ROOT), name="static")
app.include_router(studio_router)
app.include_router(manual_router)
app.include_router(manual_cycles_router)
app.include_router(syllable_score_router)


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    html = (STATIC_ROOT / "studio.html").read_text(encoding="utf-8")
    for name in ("studio.js", "studio.css", "manual-model.js", "manual-editor.js", "manual-editor.css"):
        version = hashlib.sha256((STATIC_ROOT / name).read_bytes()).hexdigest()[:16]
        html = html.replace(f'"/static/{name}"', f'"/static/{name}?v={version}"')
    return html
