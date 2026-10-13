"""selfstream: ingest service + transparent Subsonic proxy in front of Navidrome.

- /selfstream            small web UI (search YouTube, paste a URL, see jobs)
- /selfstream/api/*      search, ingest, jobs
- everything else        reverse-proxied to Navidrome (Subsonic/OpenSubsonic API, web UI)
"""
import asyncio
import json
import os
import re
import time
import uuid
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel
from starlette.background import BackgroundTask

MUSIC_DIR = Path(os.environ.get("MUSIC_DIR", "/music"))
NAVIDROME_URL = os.environ.get("NAVIDROME_URL", "http://127.0.0.1:4533")
AUDIO_FORMAT = os.environ.get("AUDIO_FORMAT", "opus")
YTDLP_UPDATE_HOURS = float(os.environ.get("YTDLP_UPDATE_HOURS", "24"))
LRCLIB = "https://lrclib.net/api"
UA = {"User-Agent": "selfstream/0.1 (self-hosted)"}

app = FastAPI(title="selfstream")
jobs: dict[str, dict] = {}
download_lock = asyncio.Semaphore(2)  # at most 2 parallel downloads


# ---------------------------------------------------------------- helpers
def safe_name(s: str) -> str:
    return re.sub(r'[\\/:*?"<>|]+', "_", s).strip(" .") or "unknown"


async def run(*args: str) -> tuple[int, str, str]:
    proc = await asyncio.create_subprocess_exec(
        *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    out, err = await proc.communicate()
    return proc.returncode, out.decode(errors="replace"), err.decode(errors="replace")


async def fetch_lrc(artist: str, title: str, duration: int | None) -> str | None:
    """Return synced lyrics (LRC text) from LRCLIB, or None."""
    async with httpx.AsyncClient(timeout=15, headers=UA) as c:
        params = {"artist_name": artist, "track_name": title}
        if duration:
            params["duration"] = str(duration)
        try:
            r = await c.get(f"{LRCLIB}/get", params=params)
            if r.status_code == 200 and r.json().get("syncedLyrics"):
                return r.json()["syncedLyrics"]
            r = await c.get(f"{LRCLIB}/search", params={"q": f"{artist} {title}"})
            if r.status_code == 200:
                for item in r.json():
                    if item.get("syncedLyrics"):
                        return item["syncedLyrics"]
        except httpx.HTTPError:
            pass
    return None


# ------------------------------------------------------------------- jobs
async def ingest_job(job_id: str, url: str):
    job = jobs[job_id]
    async with download_lock:
        job["status"] = "downloading"
        template = str(MUSIC_DIR / "%(artist,uploader)s" / "%(track,title)s.%(ext)s")
        code, out, err = await run(
            "yt-dlp", "--no-playlist", "-x", "--audio-format", AUDIO_FORMAT,
            "--audio-quality", "0", "--embed-metadata", "--no-simulate",
            "--print", "after_move:%(artist,uploader)s\t%(track,title)s\t%(duration)s\t%(filepath)s",
            "-o", template, url,
        )
        if code != 0:
            job.update(status="error", error=err.strip().splitlines()[-1] if err.strip() else "yt-dlp failed")
            return
        line = out.strip().splitlines()[-1]
        artist, title, duration, filepath = line.split("\t")
        job.update(artist=artist, title=title, file=filepath)

        job["status"] = "fetching lyrics"
        try:
            dur = int(float(duration))
        except ValueError:
            dur = None
        lrc = await fetch_lrc(artist, title, dur)
        if lrc:
            Path(filepath).with_suffix(".lrc").write_text(lrc, encoding="utf-8")
            job["lyrics"] = "synced"
        else:
            job["lyrics"] = "not found"
        job["status"] = "done"  # Navidrome picks the file up on its next scan


class IngestRequest(BaseModel):
    url: str


@app.post("/selfstream/api/ingest")
async def ingest(req: IngestRequest):
    if not re.match(r"^https?://", req.url):
        raise HTTPException(400, "url must start with http(s)://")
    job_id = uuid.uuid4().hex[:8]
    jobs[job_id] = {"id": job_id, "url": req.url, "status": "queued", "created": time.time()}
    asyncio.create_task(ingest_job(job_id, req.url))
    return jobs[job_id]


@app.get("/selfstream/api/jobs")
async def list_jobs():
    return sorted(jobs.values(), key=lambda j: j["created"], reverse=True)[:50]


@app.get("/selfstream/api/search")
async def search(q: str):
    code, out, err = await run(
        "yt-dlp", "--flat-playlist", "-j", f"ytsearch8:{q}"
    )
    if code != 0:
        raise HTTPException(502, err.strip()[-300:])
    results = []
    for line in out.splitlines():
        d = json.loads(line)
        results.append({
            "title": d.get("title"),
            "channel": d.get("channel") or d.get("uploader"),
            "duration": d.get("duration"),
            "url": d.get("url") or f"https://www.youtube.com/watch?v={d.get('id')}",
        })
    return results


# ---------------------------------------------------------- yt-dlp updater
async def ytdlp_updater():
    while True:
        await run("yt-dlp", "-U")
        await asyncio.sleep(YTDLP_UPDATE_HOURS * 3600)


@app.on_event("startup")
async def startup():
    MUSIC_DIR.mkdir(parents=True, exist_ok=True)
    asyncio.create_task(ytdlp_updater())


# ------------------------------------------------------------------- UI
UI = """<!doctype html><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>selfstream</title>
<style>
 body{font:16px system-ui;max-width:720px;margin:2rem auto;padding:0 1rem;background:#111;color:#eee}
 input{padding:.6rem;width:70%;border-radius:6px;border:1px solid #444;background:#222;color:#eee}
 button{padding:.6rem 1rem;border-radius:6px;border:0;background:#1db954;color:#000;cursor:pointer}
 .r{display:flex;justify-content:space-between;gap:1rem;padding:.5rem 0;border-bottom:1px solid #333}
 small{color:#999} a{color:#1db954}
</style>
<h2>selfstream</h2>
<p><a href="/">Open Navidrome</a></p>
<p><input id=q placeholder="Search YouTube or paste a URL"> <button onclick="go()">Go</button></p>
<div id=res></div><h3>Jobs</h3><div id=jobs></div>
<script>
const $=id=>document.getElementById(id);
async function go(){
  const q=$('q').value.trim(); if(!q)return;
  if(/^https?:\\/\\//.test(q)){return add(q)}
  $('res').textContent='Searching...';
  const r=await fetch('/selfstream/api/search?q='+encodeURIComponent(q));
  if(!r.ok){$('res').textContent='Search failed';return}
  const items=await r.json();
  $('res').innerHTML=items.map(i=>`<div class=r><span>${i.title}<br><small>${i.channel||''}</small></span>
   <button onclick="add('${i.url}')">Download</button></div>`).join('');
}
async function add(url){
  await fetch('/selfstream/api/ingest',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({url})});
  refresh();
}
async function refresh(){
  const js=await (await fetch('/selfstream/api/jobs')).json();
  $('jobs').innerHTML=js.map(j=>`<div class=r><span>${j.title?j.artist+' - '+j.title:j.url}<br><small>${j.error||''}</small></span>
   <small>${j.status}${j.lyrics?' · lyrics: '+j.lyrics:''}</small></div>`).join('');
}
setInterval(refresh,3000);refresh();
</script>"""


@app.get("/selfstream", response_class=HTMLResponse)
async def ui():
    return UI


# ------------------------------------------------------- Navidrome proxy
client = httpx.AsyncClient(base_url=NAVIDROME_URL, timeout=None)
# Hop-by-hop headers must not be forwarded. Content-Encoding and Content-Length ARE forwarded
# on responses, because the body is streamed raw (still compressed, exact byte length), which
# also keeps Range/seek behaviour for audio intact.
HOP_REQ = {"host", "connection", "keep-alive", "transfer-encoding", "te", "upgrade", "content-length"}
HOP_RESP = {"connection", "keep-alive", "transfer-encoding", "te", "upgrade"}


@app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"])
async def proxy(path: str, request: Request):
    upstream = client.build_request(
        request.method,
        "/" + path,
        params=request.query_params.multi_items(),  # keeps repeated params (id=1&id=2)
        headers=[(k, v) for k, v in request.headers.items() if k.lower() not in HOP_REQ],
        content=await request.body(),
    )
    resp = await client.send(upstream, stream=True)
    out = StreamingResponse(
        resp.aiter_raw(),
        status_code=resp.status_code,
        background=BackgroundTask(resp.aclose),  # release the upstream connection when done
    )
    # copy headers verbatim, including repeated ones such as Set-Cookie
    out.raw_headers = [
        (k.lower().encode("latin-1"), v.encode("latin-1"))
        for k, v in resp.headers.multi_items()
        if k.lower() not in HOP_RESP
    ]
    return out
