"""selfstream: ingest service + Subsonic proxy in front of Navidrome.

- /selfstream            small web UI (search YouTube, paste a URL, see jobs)
- /selfstream/api/*      search, ingest, jobs
- /rest/* (Subsonic)     Navidrome's API, plus YouTube results/streams for 'yt-' ids (see remote.py)
- everything else        reverse-proxied to Navidrome unchanged
"""
import asyncio
import json
import os
import re
import time

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse, StreamingResponse
from pydantic import BaseModel
from starlette.background import BackgroundTask

import admin
import api
import core
import discover  # noqa: F401
import enrich
import follows
import remote
import store

app = FastAPI(title="selfstream")

if core.CORS_ALLOW_ORIGIN:
    from starlette.middleware.cors import CORSMiddleware

    app.add_middleware(
        CORSMiddleware,
        allow_origins=[o.strip() for o in core.CORS_ALLOW_ORIGIN.split(",") if o.strip()],
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["*"],
    )


# ------------------------------------------------------------- API + UI
class IngestRequest(BaseModel):
    url: str


def _guard(request: Request, write: bool = False) -> None:
    """The web page and its API are for Navidrome administrators: the session of the admin page is required."""
    problem = admin.guard_problem(request, write)
    if problem:
        raise HTTPException(*problem)


@app.post("/selfstream/api/ingest")
async def ingest(req: IngestRequest, request: Request):
    _guard(request, write=True)
    if not core.is_youtube_url(req.url):
        raise HTTPException(400, "only YouTube links can be downloaded here")
    return core.public_job(core.start_ingest(req.url))


@app.get("/selfstream/api/jobs")
async def list_jobs(request: Request):
    _guard(request)
    return [core.public_job(j) for j in sorted(core.jobs.values(), key=lambda j: j["created"], reverse=True)[:50]]


@app.get("/selfstream/api/search")
async def search(q: str, request: Request):
    _guard(request)
    items = await core.yt_search(q, core.REMOTE_RESULTS)
    return [{**i, "url": core.watch_url(i["id"])} for i in items]


_diagnose_cache: tuple[float, dict] | None = None


@app.get("/selfstream/api/diagnose")
async def diagnose(request: Request):
    """Self-test: versions, plus one real YouTube lookup with yt-dlp in verbose mode.

    Open http://<host>:8080/selfstream/api/diagnose in a browser (signed in to /selfstream/admin) when streaming/downloads fail.
    The result is cached for a minute so reloading the page doesn't hammer YouTube.
    """
    _guard(request)
    global _diagnose_cache
    now = time.time()
    if _diagnose_cache and now - _diagnose_cache[0] < 60:
        return _diagnose_cache[1]
    _, ytdlp_v, _ = await core.run(core.YTDLP, "--version", timeout=20)
    _, deno_v, deno_err = await core.run("deno", "--version", timeout=20)
    code, out, err = await core.run(
        core.YTDLP, *core.YTDLP_EXTRA, "-v", "--no-playlist",
        "-f", "bestaudio[ext=m4a]/bestaudio/best", "-g", core.watch_url("dQw4w9WgXcQ"), timeout=90,
    )
    result = {
        "yt-dlp": ytdlp_v.strip() or "not found",
        "deno": (deno_v.splitlines() or [deno_err.strip() or "not found"])[0],
        "lookup_ok": code == 0 and out.strip().startswith("http"),
        "lookup_exit_code": code,
        "lookup_log_tail": err.strip().splitlines()[-30:],
        "search_source": core.SEARCH_SOURCE,
        "ytmusic_search": await core.ytmusic_probe(),
        "liked_cloud_tracks": len(core.starred_remote),
        "state_file_writable": os.access(core.STATE_FILE.parent, os.W_OK),
        "music_dir_writable": os.access(core.MUSIC_DIR, os.W_OK),
        "navidrome_url": core.NAVIDROME_URL,
    }
    _diagnose_cache = (now, result)
    return result


async def _ytdlp_updater():
    while True:
        await core.run(core.YTDLP, "-U")
        await asyncio.sleep(core.YTDLP_UPDATE_HOURS * 3600)


@app.on_event("startup")
async def startup():
    core.MUSIC_DIR.mkdir(parents=True, exist_ok=True)
    core.load_state()  # liked cloud tracks survive restarts
    admin.load_overrides()  # settings changed on the admin page win over the env file
    api.st = store.Store(str(core.DB_FILE))
    admin.store = api.st
    core.spawn(_ytdlp_updater())
    core.spawn(follows.worker(api.st))
    core.spawn(enrich.worker(api.st, client))


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
<p><a href="/">Open Navidrome</a> · <a href="/selfstream/admin">Admin</a></p>
<p><small>In a Subsonic app, search finds YouTube tracks too (marked with a cloud). Play them to stream,
heart them to download into the library.</small></p>
<p><input id=q placeholder="Search YouTube or paste a URL"> <button onclick="go()">Go</button></p>
<div id=res></div><h3>Jobs</h3><div id=jobs></div>
<script>
const $=id=>document.getElementById(id);
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const H={'x-selfstream-admin':'1'};
async function jf(url,opt={}){
  const r=await fetch(url,{credentials:'same-origin',...opt,headers:{...H,...(opt.headers||{})}});
  if(r.status===401){location.href='/selfstream/admin?next=/selfstream'}
  return r;
}
async function go(){
  const q=$('q').value.trim(); if(!q)return;
  if(/^https?:\\/\\//.test(q)){return add(q)}
  $('res').textContent='Searching...';
  const r=await jf('/selfstream/api/search?q='+encodeURIComponent(q));
  if(!r.ok){$('res').textContent='Search failed';return}
  const items=await r.json();
  $('res').innerHTML=items.map(i=>`<div class=r><span>${esc(i.title)}<br><small>${esc(i.channel)}</small></span>
   <button data-url="${esc(i.url)}" onclick="add(this.dataset.url)">Download</button></div>`).join('');
}
async function add(url){
  const r=await jf('/selfstream/api/ingest',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({url})});
  if(!r.ok){let m='Could not add';try{m=(await r.json()).detail||m}catch(e){}alert(m)}
  refresh();
}
async function refresh(){
  const rr=await jf('/selfstream/api/jobs');if(!rr.ok)return;const js=await rr.json();
  $('jobs').innerHTML=js.map(j=>`<div class=r><span>${esc(j.title?j.artist+' - '+j.title:j.url)}<br><small>${esc(j.error||'')}</small></span>
   <small>${esc(j.status)}${j.lyrics?' · lyrics: '+esc(j.lyrics):''}</small></div>`).join('');
}
setInterval(refresh,3000);refresh();
</script>"""


@app.get("/selfstream", response_class=HTMLResponse)
async def ui(request: Request):
    if admin.session_user(request) is None:
        return RedirectResponse("/selfstream/admin?next=/selfstream", status_code=302)
    return HTMLResponse(UI, headers={"Cache-Control": "no-store", "X-Frame-Options": "DENY", "Referrer-Policy": "no-referrer"})


# ------------------------------------------------------- Navidrome proxy
client = httpx.AsyncClient(base_url=core.NAVIDROME_URL, timeout=httpx.Timeout(15.0, read=120.0), follow_redirects=False)

# Hop-by-hop headers must not be forwarded. Content-Encoding and Content-Length ARE forwarded
# on responses, because the body is streamed raw (still compressed, exact byte length), which
# also keeps Range/seek behaviour for audio intact.
HOP_REQ = {"host", "connection", "keep-alive", "transfer-encoding", "te", "upgrade", "content-length"}
HOP_RESP = {"connection", "keep-alive", "transfer-encoding", "te", "upgrade"}


async def _really_renamed(request: Request, path: str, client, new_name: str) -> bool:
    auth = {k: v for k, v in request.headers.items() if k.lower() in ("x-nd-authorization", "authorization", "cookie")}
    r = await client.get("/" + path, headers=auth)
    if r.status_code == 200:
        return r.json().get("userName", "") == new_name
    return r.status_code == 401  # the token of the old name no longer works: it was renamed


async def _renamed_from(request: Request, path: str, client) -> str:
    """For Navidrome's 'edit user' call that carries a new userName: the current name (else '')."""
    if request.method != "PUT" or not re.fullmatch(r"api/user/[\w-]+", path):
        return ""
    try:
        new = (json.loads(await request.body()) or {}).get("userName", "")
        if not new:
            return ""
        auth = {k: v for k, v in request.headers.items() if k.lower() in ("x-nd-authorization", "authorization", "cookie")}
        r = await client.get("/" + path, headers=auth)
        old = r.json().get("userName", "") if r.status_code == 200 else ""
        return old if old != new else ""
    except Exception:
        return ""


@app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"])
async def proxy(path: str, request: Request):
    if core.PROXY_LOG and path.startswith("rest/"):
        core.log(f"{request.method} /{path} {remote.sanitized(list(request.query_params.multi_items()))}")

    logging_on = core.PROXY_LOG and path.startswith("rest/")
    handled = await admin.handle(request, path, client)  # the admin page (Navidrome administrators only)
    if handled is None:
        handled = await api.handle(request, path, client)  # the app's own API (playlists, stats, ...)
    if handled is None:
        handled = await remote.handle(request, path, client)  # YouTube results/streams for 'yt-' ids
    if handled is not None:
        if logging_on:
            core.log(f"  -> {handled.status_code} {handled.headers.get('content-type', '')} (handled by selfstream)")
        return handled

    upstream = client.build_request(
        request.method,
        "/" + path,
        params=request.query_params.multi_items(),  # keeps repeated params (id=1&id=2)
        headers=[(k, v) for k, v in request.headers.items() if k.lower() not in HOP_REQ],
        content=await request.body(),
    )
    old_name = await _renamed_from(request, path, client)
    try:
        resp = await client.send(upstream, stream=True)
    except httpx.HTTPError as exc:
        core.log(f"  -> Navidrome unreachable: {type(exc).__name__}: {exc}")
        return PlainTextResponse("Navidrome is not reachable", status_code=502)
    if old_name and resp.status_code < 300:
        try:
            new_name = (json.loads(await request.body()) or {}).get("userName", "")
            # Navidrome ignores a new userName from accounts that are not administrators: only move the saved data
            # when the name really changed (the old login token stops working after a real rename).
            if api.st is not None and new_name and new_name != old_name and await _really_renamed(request, path, client, new_name):
                n = api.st.rename_user(old_name, new_name)
                core.log(f"account renamed {old_name} -> {new_name}: moved {n} rows of playlists/history")
            elif new_name != old_name:
                core.log(f"account {old_name}: Navidrome kept the login name, saved data stays")
        except Exception as exc:
            core.log(f"rename migration failed: {type(exc).__name__}: {exc}")
    if logging_on:
        core.log(f"  -> {resp.status_code} {resp.headers.get('content-type', '')} "
                 f"len={resp.headers.get('content-length', '?')} (Navidrome)")
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
