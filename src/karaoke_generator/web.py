from __future__ import annotations

import hashlib
import shutil
import tempfile
import threading
import uuid
from pathlib import Path
from typing import Any

from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from . import __version__
from .config import load_config
from .pipeline import generate
from .studio_web import router as studio_router


app = FastAPI(title="VocalCreator", version=__version__)
STATIC_ROOT = Path(__file__).with_name("static")
app.mount("/static", StaticFiles(directory=STATIC_ROOT), name="static")
app.include_router(studio_router)
JOBS_ROOT = Path(tempfile.gettempdir()) / "karaoke-creator-jobs"
JOBS_ROOT.mkdir(parents=True, exist_ok=True)
JOBS: dict[str, dict[str, Any]] = {}
JOBS_LOCK = threading.Lock()

FORM = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>Karaoke Creator</title><style>
body{margin:0;background:#080b1a;color:#f7f7fb;font:16px system-ui;display:grid;place-items:center;min-height:100vh}
main{width:min(720px,90vw);background:#151a36;padding:36px;border-radius:24px;box-shadow:0 24px 70px #0008}
h1{margin-top:0;font-size:38px}label{display:block;margin:18px 0 8px;color:#c8cbe0}
input,select,button{width:100%;box-sizing:border-box;padding:13px;border-radius:10px;border:1px solid #40476f;background:#0f1430;color:white}
input[type=checkbox]{width:auto;margin:0 9px 0 0}.check{display:flex;align-items:center;color:#f7f7fb}
input[type=range]{padding:0}.setting-help{display:block;margin-top:7px}.setting-value{float:right;color:#ffd43b}
button{margin-top:24px;background:#ffd43b;color:#171717;border:0;font-weight:800;cursor:pointer}
button:disabled{cursor:wait;opacity:.65}small{color:#9fa5c5}.ok{color:#70e5b1}a{color:#ffd43b}
#progress-panel{margin-top:28px;padding:20px;border:1px solid #343d68;border-radius:16px;background:#0d122a}
#progress-panel[hidden]{display:none}.progress-head{display:flex;justify-content:space-between;gap:18px;margin-bottom:12px}
.progress-track{height:14px;overflow:hidden;border-radius:999px;background:#252c52;box-shadow:inset 0 1px 4px #0008}
#progress-bar{position:relative;width:0;height:100%;border-radius:inherit;background:linear-gradient(90deg,#ffd43b,#ff8a3d);transition:width .35s ease}
#progress-bar:after{content:"";position:absolute;inset:0;background:linear-gradient(100deg,transparent 25%,#fff7 50%,transparent 75%);animation:shimmer 1.3s infinite}
#progress-detail{min-height:1.5em;margin:12px 0 0;color:#aeb5d8}.error{color:#ff8f9b!important}
#result{margin-top:18px}#result video{width:100%;margin-top:16px;border-radius:12px;background:#000}
.diagnostics{margin:14px 0;padding:12px;border-radius:10px;background:#111735;color:#c8cbe0}details{margin-top:8px}summary{cursor:pointer;color:#ffd43b}
@keyframes shimmer{from{transform:translateX(-100%)}to{transform:translateX(100%)}}
</style></head><body><main><h1>Karaoke Creator</h1>
<p>Exact lyrics in. Word-highlighted MP4 out.</p>
<form id="generate-form" action="/generate" method="post" enctype="multipart/form-data">
<label>Audio</label><input name="audio" type="file" accept="audio/*" required>
<label>Lyrics (UTF-8 TXT)</label><input name="lyrics" type="file" accept=".txt,text/plain" required>
<label>Language</label><input name="language" value="auto" list="languages" aria-describedby="language-help">
<datalist id="languages"><option value="auto"><option value="English"><option value="Russian"><option value="en"><option value="ru"></datalist>
<small id="language-help">Use auto, a language name, or an ISO code.</small>
<label>Timing engine</label><select name="backend"><option value="whisperx" selected>WhisperX — precise word alignment</option><option value="faster-whisper">faster-whisper — faster</option></select>
<small class="setting-help">WhisperX downloads one extra phoneme model per language on first use.</small>
<label>Recognition model</label><select name="model"><option value="small" selected>Small — recommended locally</option><option value="medium">Medium — slower, more accurate text</option><option value="large-v3">Large v3 — slowest</option></select>
<label class="check"><input name="vad_filter" type="checkbox" value="true" checked>Keep soft vocal activity detection</label>
<small class="setting-help">Uncheck only if quiet sung words are being missed.</small>
<label for="timing-offset">Highlight offset <output id="offset-value" class="setting-value">0 ms</output></label>
<input id="timing-offset" name="timing_offset_ms" type="range" min="-1000" max="1000" step="25" value="0">
<small class="setting-help">Negative values highlight earlier. Start with 0 ms.</small>
<label>Audio output</label><select name="audio_mode"><option value="instrumental">Instrumental</option><option value="original">Original</option></select>
<button id="generate-button">Generate karaoke</button></form>
<section id="progress-panel" aria-live="polite" hidden>
<div class="progress-head"><strong id="progress-stage">Uploading files</strong><span id="progress-percent">0%</span></div>
<div class="progress-track" role="progressbar" aria-label="Karaoke generation progress" aria-valuemin="0" aria-valuemax="100" aria-valuenow="0"><div id="progress-bar"></div></div>
<p id="progress-detail">The five pipeline stages report real completion.</p><div id="result"></div>
</section><p><small>Files stay on this computer.</small></p></main>
<script>
const form=document.querySelector('#generate-form');
const button=document.querySelector('#generate-button');
const panel=document.querySelector('#progress-panel');
const stage=document.querySelector('#progress-stage');
const percent=document.querySelector('#progress-percent');
const detail=document.querySelector('#progress-detail');
const bar=document.querySelector('#progress-bar');
const track=document.querySelector('.progress-track');
const result=document.querySelector('#result');
const offset=document.querySelector('#timing-offset');
const offsetValue=document.querySelector('#offset-value');
const delay=ms=>new Promise(resolve=>setTimeout(resolve,ms));
const showOffset=()=>{const value=Number(offset.value);offsetValue.textContent=`${value<0?'−':value>0?'+':''}${Math.abs(value)} ms`;};
offset.addEventListener('input',showOffset);showOffset();

function updateProgress(value,label){
  const safe=Math.max(0,Math.min(100,Number(value)||0));
  bar.style.width=`${safe}%`; percent.textContent=`${safe}%`;
  track.setAttribute('aria-valuenow',String(safe)); stage.textContent=label;
}

async function showArtifacts(artifacts){
  result.replaceChildren();
  const heading=document.createElement('strong'); heading.className='ok'; heading.textContent='Karaoke ready';
  const list=document.createElement('ul');
  for(const [name,url] of Object.entries(artifacts)){
    const item=document.createElement('li'); const link=document.createElement('a');
    link.href=url; link.textContent=`Download ${name}`; item.append(link); list.append(item);
  }
  const diagnostics=document.createElement('div'); diagnostics.className='diagnostics';
  try{
    const [alignmentResponse,cleanupResponse]=await Promise.all([
      fetch(artifacts['alignment.json'],{cache:'no-store'}),
      fetch(artifacts['lyrics_cleanup.json'],{cache:'no-store'}),
    ]);
    const alignment=await alignmentResponse.json(); const cleanup=await cleanupResponse.json();
    const quality=alignment.quality||{}; const ratio=Math.round((quality.aligned_ratio||0)*1000)/10;
    const summary=document.createElement('div');
    summary.textContent=`Сопоставление текста: ${quality.directly_aligned||0}/${quality.total_words||0} words (${ratio}%), ${quality.interpolated||0} interpolated. Removed ${cleanup.removed_count||0} metadata lines.`;
    diagnostics.append(summary);
    const timing=alignment.diagnostics||{}; const sources=timing.timing_sources||{};
    const timingInfo=document.createElement('div');
    const refiner=timing.refinement?.model?.name||'не используется';
    timingInfo.textContent=`ASR: ${timing.asr?.model?.name||'неизвестно'}; уточнитель: ${refiner}; сдвиг: ${timing.effective_timing_offset_ms??'неизвестно'} мс. По аудио уточнено: ${sources.refined||0}; ASR: ${sources.asr||0}; приблизительное разделение: ${sources.approximate_split||0}; интерполяция: ${sources.interpolated||0}; ручные правки: ${sources.manual||0}; коррекции: ${sources.corrected||0}. Точность таймингов без эталонной разметки не измерена.`;
    diagnostics.append(timingInfo);
    if(cleanup.removed_lines?.length){
      const details=document.createElement('details'); const label=document.createElement('summary');
      label.textContent='Show removed lyrics lines'; const removedList=document.createElement('ul');
      for(const removed of cleanup.removed_lines){const item=document.createElement('li');item.textContent=`Line ${removed.line_number}: ${removed.text}`;removedList.append(item);}
      details.append(label,removedList); diagnostics.append(details);
    }
  }catch(error){diagnostics.textContent='Diagnostic reports are available from the download links.';}
  const video=document.createElement('video'); video.controls=true; video.src=artifacts['karaoke.mp4'];
  result.append(heading,diagnostics,list,video);
}

form.addEventListener('submit',async event=>{
  event.preventDefault(); panel.hidden=false; result.replaceChildren(); detail.className='';
  button.disabled=true; button.textContent='Generating…'; updateProgress(0,'Uploading files');
  try{
    const response=await fetch('/api/jobs',{method:'POST',body:new FormData(form)});
    const created=await response.json();
    if(!response.ok) throw new Error(created.detail||'Could not start generation');
    while(true){
      const statusResponse=await fetch(created.status_url,{cache:'no-store'});
      const job=await statusResponse.json();
      if(!statusResponse.ok) throw new Error(job.detail||'Could not read job status');
      updateProgress(job.progress,job.stage); detail.textContent=job.detail||'';
      if(job.status==='complete'){await showArtifacts(job.artifacts);break;}
      if(job.status==='failed') throw new Error(job.error||'Generation failed');
      await delay(700);
    }
  }catch(error){
    stage.textContent='Generation failed'; detail.className='error'; detail.textContent=error.message;
  }finally{button.disabled=false;button.textContent='Generate karaoke';}
});
</script></body></html>"""


@app.get("/karaoke", response_class=HTMLResponse)
def karaoke_index() -> str:
    return FORM


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    html = (STATIC_ROOT / "studio.html").read_text(encoding="utf-8")
    for name in ("studio.js", "studio.css"):
        version = hashlib.sha256((STATIC_ROOT / name).read_bytes()).hexdigest()[:16]
        html = html.replace(f'"/static/{name}"', f'"/static/{name}?v={version}"')
    return html


def _set_job(job_id: str, **changes: Any) -> None:
    with JOBS_LOCK:
        job = JOBS[job_id]
        if "progress" in changes:
            changes["progress"] = max(job["progress"], min(100, int(changes["progress"])))
        job.update(changes)


def _job_snapshot(job_id: str) -> dict[str, Any]:
    if not job_id.isalnum():
        raise HTTPException(404)
    with JOBS_LOCK:
        if job_id not in JOBS:
            raise HTTPException(404)
        return dict(JOBS[job_id])


def _normalize_language(language: str) -> str:
    value = language.strip().lower()
    aliases = {"english": "en", "russian": "ru"}
    value = aliases.get(value, value)
    if value == "auto" or (value.isalpha() and 2 <= len(value) <= 3):
        return value
    raise HTTPException(400, "Language must be auto, a language name, or a 2–3 letter ISO code")


def _normalize_generation_options(
    backend: str,
    model: str,
    timing_offset_ms: int,
) -> tuple[str, str, int]:
    if backend not in {"faster-whisper", "whisperx"}:
        raise HTTPException(400, "Invalid timing engine")
    if model not in {"small", "medium", "large-v3"}:
        raise HTTPException(400, "Invalid recognition model")
    if not -1000 <= timing_offset_ms <= 1000:
        raise HTTPException(400, "Timing offset must be between -1000 and 1000 ms")
    return backend, model, timing_offset_ms


def _prepare_job(
    audio: UploadFile,
    lyrics: UploadFile,
    language: str,
    audio_mode: str,
    backend: str,
    model: str,
    timing_offset_ms: int,
) -> tuple[str, Path, Path, str, str, str, int]:
    if audio_mode not in {"original", "instrumental"}:
        raise HTTPException(400, "Invalid audio mode")
    normalized_language = _normalize_language(language)
    backend, model, timing_offset_ms = _normalize_generation_options(
        backend, model, timing_offset_ms
    )
    job_id = uuid.uuid4().hex
    job_dir = JOBS_ROOT / job_id
    job_dir.mkdir(parents=True)
    audio_suffix = Path(audio.filename or "audio.wav").suffix.lower() or ".wav"
    if audio_suffix not in {".mp3", ".wav", ".flac", ".m4a", ".aac", ".ogg"}:
        raise HTTPException(400, "Unsupported audio format")
    audio_path = job_dir / f"audio{audio_suffix}"
    lyrics_path = job_dir / "lyrics.txt"
    with audio_path.open("wb") as handle:
        shutil.copyfileobj(audio.file, handle)
    with lyrics_path.open("wb") as handle:
        shutil.copyfileobj(lyrics.file, handle)
    with JOBS_LOCK:
        JOBS[job_id] = {
            "id": job_id,
            "status": "queued",
            "progress": 0,
            "stage": "Queued",
            "detail": "Waiting for a worker",
            "artifacts": {},
        }
    return (
        job_id,
        audio_path,
        lyrics_path,
        normalized_language,
        backend,
        model,
        timing_offset_ms,
    )


def _run_job(
    job_id: str,
    audio_path: Path,
    lyrics_path: Path,
    language: str,
    audio_mode: str,
    backend: str = "whisperx",
    model: str = "small",
    vad_filter: bool = True,
    timing_offset_ms: int = 0,
) -> None:
    try:
        config = load_config()
        config["alignment"]["language"] = language
        config["alignment"]["backend"] = backend
        config["alignment"]["model"] = model
        config["alignment"]["vad_filter"] = vad_filter
        config["karaoke"]["timing_offset_ms"] = timing_offset_ms
        config["output"]["audio_mode"] = audio_mode
        _set_job(job_id, status="running", stage="Preparing audio", detail="Generation started")

        def report(progress: int, label: str) -> None:
            _set_job(job_id, progress=progress, stage=label, detail=f"Stage progress: {progress}%")

        generate(audio_path, lyrics_path, audio_path.parent / "result", config, progress_callback=report)
    except Exception as exc:
        _set_job(job_id, status="failed", stage="Generation failed", detail="", error=str(exc))
        return
    artifacts = {
        name: f"/jobs/{job_id}/{name}"
        for name in (
            "karaoke.mp4",
            "karaoke.ass",
            "alignment.json",
            "processed_lyrics.txt",
            "lyrics_cleanup.json",
        )
    }
    _set_job(
        job_id,
        status="complete",
        progress=100,
        stage="Karaoke ready",
        detail="Generation complete",
        artifacts=artifacts,
    )


@app.post("/api/jobs", status_code=202)
def start_job(
    background_tasks: BackgroundTasks,
    audio: UploadFile = File(...),
    lyrics: UploadFile = File(...),
    language: str = Form("auto"),
    audio_mode: str = Form("instrumental"),
    backend: str = Form("whisperx"),
    model: str = Form("small"),
    vad_filter: bool = Form(False),
    timing_offset_ms: int = Form(0),
) -> dict[str, str]:
    prepared = _prepare_job(
        audio, lyrics, language, audio_mode, backend, model, timing_offset_ms
    )
    job_id, audio_path, lyrics_path, language, backend, model, timing_offset_ms = prepared
    background_tasks.add_task(
        _run_job,
        job_id,
        audio_path,
        lyrics_path,
        language,
        audio_mode,
        backend,
        model,
        vad_filter,
        timing_offset_ms,
    )
    return {"job_id": job_id, "status_url": f"/api/jobs/{job_id}"}


@app.get("/api/jobs/{job_id}")
def job_status(job_id: str) -> dict[str, Any]:
    return _job_snapshot(job_id)


@app.post("/generate", response_class=HTMLResponse)
def generate_job(
    audio: UploadFile = File(...),
    lyrics: UploadFile = File(...),
    language: str = Form("auto"),
    audio_mode: str = Form("instrumental"),
    backend: str = Form("whisperx"),
    model: str = Form("small"),
    vad_filter: bool = Form(False),
    timing_offset_ms: int = Form(0),
) -> str:
    prepared = _prepare_job(
        audio, lyrics, language, audio_mode, backend, model, timing_offset_ms
    )
    job_id, audio_path, lyrics_path, language, backend, model, timing_offset_ms = prepared
    _run_job(
        job_id,
        audio_path,
        lyrics_path,
        language,
        audio_mode,
        backend,
        model,
        vad_filter,
        timing_offset_ms,
    )
    job = _job_snapshot(job_id)
    if job["status"] == "failed":
        raise HTTPException(500, job["error"])
    return f"""<html><body style="background:#080b1a;color:white;font:18px system-ui;padding:3rem">
<h1 class="ok">Karaoke ready</h1><ul>
<li><a href="/jobs/{job_id}/karaoke.mp4">Download karaoke.mp4</a></li>
<li><a href="/jobs/{job_id}/karaoke.ass">Download karaoke.ass</a></li>
<li><a href="/jobs/{job_id}/alignment.json">Download alignment.json</a></li>
</ul><a href="/">Create another</a></body></html>"""


@app.get("/jobs/{job_id}/{filename}")
def download(job_id: str, filename: str) -> FileResponse:
    if not job_id.isalnum() or filename not in {
        "karaoke.mp4",
        "karaoke.ass",
        "alignment.json",
        "processed_lyrics.txt",
        "lyrics_cleanup.json",
    }:
        raise HTTPException(404)
    path = JOBS_ROOT / job_id / "result" / filename
    if not path.is_file():
        raise HTTPException(404)
    return FileResponse(path, filename=filename)
