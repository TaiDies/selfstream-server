"""Shared machinery: config, yt-dlp helpers, LRCLIB lyrics and the download queue."""
import asyncio
import json
import os
import re
import shlex
import time
import uuid
from pathlib import Path

import subsonic

MUSIC_DIR = Path(os.environ.get("MUSIC_DIR", "/music"))
NAVIDROME_URL = os.environ.get("NAVIDROME_URL", "http://127.0.0.1:4533")
AUDIO_FORMAT = os.environ.get("AUDIO_FORMAT", "opus")
YTDLP = os.environ.get("YTDLP_BIN", "yt-dlp")
YTDLP_EXTRA = shlex.split(os.environ.get("YTDLP_EXTRA_ARGS", ""))  # e.g. --cookies /data/cookies.txt
YTDLP_UPDATE_HOURS = float(os.environ.get("YTDLP_UPDATE_HOURS", "24"))

REMOTE_ENABLED = os.environ.get("REMOTE_ENABLED", "1") != "0"
REMOTE_RESULTS = int(os.environ.get("REMOTE_RESULTS", "8"))
REMOTE_MAX_SECONDS = int(os.environ.get("REMOTE_MAX_SECONDS", "900"))  # skip mixes/streams longer than this
REMOTE_WAIT_SECONDS = float(os.environ.get("REMOTE_WAIT_SECONDS", "8"))  # how long search3 waits for YouTube
REMOTE_TITLE_PREFIX =os.environ.get("REMOTE_TITLE_PREFIX", "☁ ")  # cloud marks not-yet-downloaded tracks
DOWNLOAD_WAIT_SECONDS = int(os.environ.get("DOWNLOAD_WAIT_SECONDS", "300"))
PROXY_LOG = os.environ.get("PROXY_LOG", "1") != "0"

LRCLIB = "https://lrclib.net/api"
UA = {"User-Agent": "selfstream/0.2 (self-hosted)"}
_VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")

CONTENT_TYPES = {"m4a": "audio/mp4", "mp4": "audio/mp4", "webm": "audio/webm", "opus": "audio/ogg", "mp3": "audio/mpeg"}


def log(msg: str) -> None:
    print(f"[selfstream] {msg}", flush=True)


def watch_url(video_id: str) -> str:
    return f"https://www.youtube.com/watch?v={video_id}"


# ----------------------------------------------------------------- process
async def run(*args: str, timeout: float | None = None) -> tuple[int, str, str]:
    try:
        proc = await asyncio.create_subprocess_exec(
            *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
    except FileNotFoundError:
        return 127, "", f"{args[0]}: not found"
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return 124, "", "timeout"
    return proc.returncode, out.decode(errors="replace"), err.decode(errors="replace")


def _prune(cache: dict, limit: int) -> None:
    while len(cache) > limit:
        cache.pop(next(iter(cache)))


# ----------------------------------------------------------------- search
_search_cache: dict[str, tuple[float, list[dict]]] = {}
_meta: dict[str, dict] = {}  # video id -> {id, title, channel, duration, [artist, track]}
_search_sem = asyncio.Semaphore(2)


async def yt_search(query: str, count: int) -> list[dict]:
    """Search YouTube through yt-dlp. Cached for 10 minutes (clients search as you type)."""
    key = f"{count}:{query.strip().lower()}"
    hit = _search_cache.get(key)
    if hit and time.time() - hit[0] < 600:
        return hit[1]
    async with _search_sem:
        code, out, err = await run(
            YTDLP, *YTDLP_EXTRA, "--no-warnings", "--flat-playlist", "-j",
            f"ytsearch{count * 2}:{query}", timeout=25,
        )
    if code != 0:
        log(f"search failed ({code}): {err.strip()[-200:]}")
        return []
    items: list[dict] = []
    for line in out.splitlines():
        try:
            d = json.loads(line)
        except ValueError:
            continue
        vid, dur = d.get("id"), d.get("duration")
        if not (vid and _VIDEO_ID.match(vid)) or not dur or dur > REMOTE_MAX_SECONDS:
            continue
        if d.get("live_status") in ("is_live", "is_upcoming"):
            continue
        item = {
            "id": vid,
            "title": d.get("title") or "",
            "channel": d.get("channel") or d.get("uploader") or "",
            "duration": int(dur),
        }
        _meta[vid] = {**_meta.get(vid, {}), **item}
        items.append(item)
        if len(items) >= count:
            break
    _search_cache[key] = (time.time(), items)
    _prune(_search_cache, 200)
    _prune(_meta, 3000)
    return items


# ----------------------------------------------------------------- streams
_stream_cache: dict[str, tuple[float, dict]] = {}
_resolve_sem = asyncio.Semaphore(3)


async def resolve_stream(video_id: str, fresh: bool = False) -> dict | None:
    """Direct audio URL (plus the headers it needs) for a video. Cached for 30 minutes."""
    hit = _stream_cache.get(video_id)
    if hit and not fresh and time.time() - hit[0] < 1800:
        return hit[1]
    async with _resolve_sem:
        code, out, err = await run(
            YTDLP, *YTDLP_EXTRA, "--no-warnings", "--no-playlist",
            "-f", "bestaudio[ext=m4a]/bestaudio/best", "-j", watch_url(video_id), timeout=60,
        )
    if code != 0 or not out.strip():
        log(f"resolve failed for {video_id} ({code}): {err.strip()[-300:]}")
        return None
    try:
        d = json.loads(out.strip().splitlines()[-1])
        info = {
            "url": d["url"],
            "headers": d.get("http_headers") or {},
            "ext": d.get("ext") or "m4a",
        }
    except (ValueError, KeyError):
        return None
    meta = {
        "id": video_id,
        "title": d.get("title") or "",
        "channel": d.get("channel") or d.get("uploader") or "",
        "duration": int(d.get("duration") or 0),
    }
    if d.get("artist") and d.get("track"):
        meta["artist"] = str(d["artist"]).split(",")[0].strip()
        meta["track"] = d["track"]
    _meta[video_id] = {**_meta.get(video_id, {}), **meta}
    _stream_cache[video_id] = (time.time(), info)
    _prune(_stream_cache, 200)
    return info


async def get_meta(video_id: str) -> dict | None:
    if video_id not in _meta:
        await resolve_stream(video_id)
    return _meta.get(video_id)


# ------------------------------------------------------------------ lyrics
_lrc_cache: dict[tuple[str, str], str | None] = {}


async def fetch_lrc(artist: str, title: str, duration: int | None) -> str | None:
    """Synced lyrics (LRC text) from LRCLIB, or None."""
    key = (artist.lower(), title.lower())
    if key in _lrc_cache:
        return _lrc_cache[key]
    import httpx  # imported here so the pure-logic tests need no web stack

    result = None
    async with httpx.AsyncClient(timeout=15, headers=UA) as c:
        params = {"artist_name": artist, "track_name": title}
        if duration:
            params["duration"] = str(duration)
        try:
            r = await c.get(f"{LRCLIB}/get", params=params)
            if r.status_code == 200 and r.json().get("syncedLyrics"):
                result = r.json()["syncedLyrics"]
            else:
                r = await c.get(f"{LRCLIB}/search", params={"q": f"{artist} {title}"})
                if r.status_code == 200:
                    result = next((i["syncedLyrics"] for i in r.json() if i.get("syncedLyrics")), None)
        except (httpx.HTTPError, ValueError):
            result = None
    _lrc_cache[key] = result
    _prune(_lrc_cache, 500)
    return result


# --------------------------------------------------------------- downloads
jobs: dict[str, dict] = {}
job_events: dict[str, asyncio.Event] = {}
ingest_by_video: dict[str, str] = {}  # video id -> job id
_download_sem = asyncio.Semaphore(2)


def done_video_ids() -> set[str]:
    return {v for v, j in ingest_by_video.items() if jobs.get(j, {}).get("status") == "done"}


def video_id_from_url(url: str) -> str | None:
    m = re.search(r"(?:v=|youtu\.be/|/shorts/)([A-Za-z0-9_-]{11})", url)
    return m.group(1) if m else None


def start_ingest(url: str, video_id: str | None = None) -> dict:
    """Queue a download. Re-uses an existing job for the same video unless it failed."""
    video_id = video_id or video_id_from_url(url)
    if video_id:
        existing = jobs.get(ingest_by_video.get(video_id, ""))
        if existing and existing["status"] != "error":
            return existing
    job_id = uuid.uuid4().hex[:8]
    jobs[job_id] = {"id": job_id, "url": url, "status": "queued", "created": time.time(), "video_id": video_id}
    job_events[job_id] = asyncio.Event()
    if video_id:
        ingest_by_video[video_id] = job_id
    asyncio.create_task(_ingest_job(job_id, url))
    while len(jobs) > 300:  # drop the oldest finished jobs
        oldest = next((k for k, j in jobs.items() if j["status"] in ("done", "error")), None)
        if oldest is None:
            break
        jobs.pop(oldest)
        job_events.pop(oldest, None)
    return jobs[job_id]


async def _ingest_job(job_id: str, url: str) -> None:
    job = jobs[job_id]
    try:
        async with _download_sem:
            job["status"] = "downloading"
            template = str(MUSIC_DIR / "%(artist,uploader)s" / "%(track,title)s.%(ext)s")
            code, out, err = await run(
                YTDLP, *YTDLP_EXTRA, "--no-warnings", "--no-playlist", "--no-overwrites",
                "-x", "--audio-format", AUDIO_FORMAT, "--audio-quality", "0",
                "--embed-metadata", "--no-simulate",
                "--print", "after_move:%(artist,uploader)s\t%(track,title)s\t%(duration)s\t%(filepath)s",
                "-o", template, url,
            )
            if code != 0:
                tail = err.strip().splitlines()
                job.update(status="error", error=tail[-1] if tail else "yt-dlp failed")
                return
            artist, title, duration, filepath = out.strip().splitlines()[-1].split("\t")
            job.update(artist=artist, title=title, file=filepath)

            job["status"] = "fetching lyrics"
            try:
                dur = int(float(duration))
            except ValueError:
                dur = None
            lrc = await fetch_lrc(artist, title, dur)
            if lrc:
                Path(filepath).with_suffix(".lrc").write_text(lrc, encoding="utf-8")
            job["lyrics"] = "synced" if lrc else "not found"
            job["status"] = "done"  # Navidrome picks the file up on its next scan
    except Exception as exc:  # never leave a job hanging
        job.update(status="error", error=f"{type(exc).__name__}: {exc}")
    finally:
        if job["status"] == "error":
            log(f"download failed for {url}: {job.get('error')}")
        elif job["status"] == "done":
            log(f"download finished: {job.get('file')}")
        job_events[job_id].set()


def song_for(meta: dict) -> dict:
    return subsonic.make_song(meta, REMOTE_TITLE_PREFIX)
