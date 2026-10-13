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
SEARCH_SOURCE = os.environ.get("SEARCH_SOURCE", "ytmusic")  # 'ytmusic' = songs only, 'youtube' = plain video search
STATE_FILE = Path(os.environ.get("STATE_FILE", "/var/lib/navidrome/selfstream-state.json"))
FINALIZE_DELAYS = (5, 20, 20, 30, 30, 45, 60)  # seconds before each try to heart the library copy of a download
REMOTE_TITLE_PREFIX = os.environ.get("REMOTE_TITLE_PREFIX", "☁ ")  # cloud marks not-yet-downloaded tracks
DOWNLOAD_WAIT_SECONDS = int(os.environ.get("DOWNLOAD_WAIT_SECONDS", "300"))
PROXY_LOG = os.environ.get("PROXY_LOG", "1") != "0"
# artists and albums from YouTube Music in search results and on artist/album pages
REMOTE_BROWSE = os.environ.get("REMOTE_BROWSE", "1") != "0"
REMOTE_ARTISTS = int(os.environ.get("REMOTE_ARTISTS", "3"))
REMOTE_ALBUMS = int(os.environ.get("REMOTE_ALBUMS", "5"))
EMBED_COVER = os.environ.get("EMBED_COVER", "1") != "0"
THUMB_ARGS = (
    ["--embed-thumbnail", "--convert-thumbnails", "jpg", "--ppa",
     "ThumbnailsConvertor+ffmpeg_o:-c:v mjpeg -q:v 2 -vf crop='if(gt(ih,iw),iw,ih)':'if(gt(iw,ih),ih,iw)'"]
    if EMBED_COVER else []
)
# Browser-based Subsonic clients need CORS headers. "*" = any origin (fine on a LAN, auth is per request); empty = off.
CORS_ALLOW_ORIGIN = os.environ.get("CORS_ALLOW_ORIGIN", "*").strip()

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


_ytm = None


def _ytm_client():
    global _ytm
    if _ytm is None:
        from ytmusicapi import YTMusic  # optional dependency; without it we fall back to plain YouTube search

        _ytm = YTMusic()
    return _ytm


def _ytmusic_search_sync(query: str, limit: int) -> list[dict]:
    return _ytm_client().search(query, filter="songs", limit=limit, ignore_spelling=True)


def _duration_seconds(r: dict) -> int | None:
    dur = r.get("duration_seconds")
    if not dur and r.get("duration"):  # "3:33" or "1:02:03"
        try:
            parts = [int(p) for p in str(r["duration"]).split(":")]
            dur = sum(p * 60 ** i for i, p in enumerate(reversed(parts)))
        except ValueError:
            dur = None
    return int(dur) if dur else None


def _from_ytmusic(r: dict) -> dict | None:
    """One ytmusicapi 'song' result -> our search item (or None if unusable)."""
    vid, dur = r.get("videoId"), _duration_seconds(r)
    if not (vid and _VIDEO_ID.match(vid)) or not dur or dur > REMOTE_MAX_SECONDS:
        return None
    artists = [a["name"] for a in (r.get("artists") or []) if a.get("name")]
    title = r.get("title") or ""
    item = {"id": vid, "title": title, "channel": artists[0] if artists else "", "duration": int(dur)}
    if artists and title:
        item["artist"], item["track"] = ", ".join(artists), title
    if (r.get("album") or {}).get("name"):
        item["album"] = r["album"]["name"]
    thumbs = r.get("thumbnails") or []
    if thumbs and thumbs[-1].get("url"):
        item["cover"] = thumbs[-1]["url"]
    return item


async def _ytmusic_items(query: str, count: int) -> list[dict]:
    try:
        async with _search_sem:
            raw = await asyncio.wait_for(asyncio.to_thread(_ytmusic_search_sync, query, count * 2), 20)
    except Exception as exc:
        log(f"YouTube Music search failed ({type(exc).__name__}: {exc}); using plain YouTube search")
        return []
    items: list[dict] = []
    for r in raw or []:
        item = _from_ytmusic(r)
        if item:
            _meta[item["id"]] = {**_meta.get(item["id"], {}), **item}
            items.append(item)
        if len(items) >= count:
            break
    return items


async def ytmusic_probe() -> dict:
    """For the diagnose page: does YouTube Music search work from here?"""
    try:
        raw = await asyncio.wait_for(asyncio.to_thread(_ytmusic_search_sync, "daft punk get lucky", 3), 25)
        return {"ok": bool(raw), "results": len(raw or []), "first": (raw[0].get("title") if raw else None)}
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


async def yt_search(query: str, count: int) -> list[dict]:
    """Songs for a search query. Cached for 10 minutes (clients search as you type)."""
    key = f"{SEARCH_SOURCE}:{count}:{query.strip().lower()}"
    hit = _search_cache.get(key)
    if hit and time.time() - hit[0] < 600:
        return hit[1]
    items: list[dict] = []
    if SEARCH_SOURCE == "ytmusic":
        items = await _ytmusic_items(query, count)
    if not items:  # plain YouTube search: the fallback, or the chosen source
        items = await _youtube_items(query, count)
    if items:
        _search_cache[key] = (time.time(), items)
        _prune(_search_cache, 200)
        _prune(_meta, 3000)
    return items


async def _youtube_items(query: str, count: int) -> list[dict]:
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
    return items


# ------------------------------------------------- artists and albums
_BROWSE_ID = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
_art_urls: dict[str, str] = {}  # remote artist/album id ('ya-...', 'yb-...') -> image url
_browse_cache: dict[str, tuple[float, object]] = {}
_browse_sem = asyncio.Semaphore(4)


async def _ytm_call(key: str, fn):
    """Run a (blocking) ytmusicapi call off the event loop. Cached for 30 minutes; None on any failure."""
    hit = _browse_cache.get(key)
    if hit and time.time() - hit[0] < 1800:
        return hit[1]
    try:
        async with _browse_sem:
            result = await asyncio.wait_for(asyncio.to_thread(fn), 25)
    except Exception as exc:
        log(f"YouTube Music lookup failed ({key.split(':')[0]}): {type(exc).__name__}: {exc}")
        return None
    _browse_cache[key] = (time.time(), result)
    _prune(_browse_cache, 300)
    return result


def _thumb(r: dict) -> str:
    thumbs = r.get("thumbnails") or []
    return thumbs[-1].get("url", "") if thumbs else ""


def _valid_id(value) -> str:
    return value if isinstance(value, str) and _BROWSE_ID.match(value) else ""


def _album_item(r: dict, artist: str = "", artist_id: str = "") -> dict | None:
    bid, title = _valid_id(r.get("browseId")), r.get("title") or ""
    if not (bid and title):
        return None
    arts = r.get("artists") or []
    item = {
        "id": bid, "title": title,
        "artist": (arts[0].get("name") if arts else "") or artist,
        "artistId": _valid_id(arts[0].get("id")) if arts else artist_id,
        "cover": _thumb(r),
    }
    year = str(r.get("year") or "")[:4]
    if year.isdigit():
        item["year"] = int(year)
    if item["cover"]:
        _art_urls[subsonic.remote_album_id(bid)] = item["cover"]
    return item


async def remote_artists(query: str, count: int = 3) -> list[dict]:
    raw = await _ytm_call(f"sa:{count}:{query.lower()}", lambda: _ytm_client().search(
        query, filter="artists", limit=count, ignore_spelling=True)) or []
    out = []
    for r in raw[:count]:
        cid, name = _valid_id(r.get("browseId")), r.get("artist") or r.get("title") or ""
        if cid and name:
            item = {"id": cid, "name": name, "cover": _thumb(r)}
            if item["cover"]:
                _art_urls[subsonic.remote_artist_id(cid)] = item["cover"]
            out.append(item)
    return out


async def remote_albums(query: str, count: int = 5) -> list[dict]:
    raw = await _ytm_call(f"sb:{count}:{query.lower()}", lambda: _ytm_client().search(
        query, filter="albums", limit=count, ignore_spelling=True)) or []
    return [i for i in (_album_item(r) for r in raw[:count]) if i]


async def remote_artist(channel_id: str) -> dict | None:
    """An artist's page: {id, name, cover, albums: [album items]} (albums and singles)."""
    d = await _ytm_call(f"ga:{channel_id}", lambda: _ytm_client().get_artist(channel_id))
    if not d:
        return None
    name, cover = d.get("name") or "", _thumb(d)
    albums = []
    for section in ("albums", "singles"):
        for r in (d.get(section) or {}).get("results") or []:
            item = _album_item(r, name, channel_id)
            if item:
                albums.append(item)
    if cover:
        _art_urls[subsonic.remote_artist_id(channel_id)] = cover
    return {"id": channel_id, "name": name, "cover": cover, "albums": albums}


async def remote_album(browse_id: str) -> dict | None:
    """An album with its playable tracks: {id, title, artist, artistId, year, cover, tracks: [meta]}."""
    d = await _ytm_call(f"gb:{browse_id}", lambda: _ytm_client().get_album(browse_id))
    if not d:
        return None
    item = _album_item({**d, "browseId": browse_id})
    if not item:
        return None
    tracks = []
    for n, t in enumerate(d.get("tracks") or [], 1):
        vid, dur = t.get("videoId"), _duration_seconds(t)
        if not (vid and _VIDEO_ID.match(vid)) or not dur or t.get("isAvailable") is False:
            continue
        artists = [a["name"] for a in (t.get("artists") or []) if a.get("name")] or [item["artist"]]
        meta = {"id": vid, "title": t.get("title") or "", "channel": artists[0], "duration": dur,
                "artist": ", ".join(artists), "track": t.get("title") or "", "album": item["title"],
                "cover": item["cover"], "number": n}
        _meta[vid] = {**_meta.get(vid, {}), **meta}
        tracks.append(meta)
    _prune(_meta, 3000)
    return {**item, "tracks": tracks}


async def find_remote_artist(name: str) -> dict | None:
    """The YouTube Music artist with exactly this name (ignoring case and punctuation), or None."""
    want = subsonic.norm(name)
    return next((a for a in await remote_artists(name, 5) if subsonic.norm(a["name"]) == want), None) if want else None


async def find_remote_album(artist: str, album: str) -> dict | None:
    """The YouTube Music album that is this library album, or None."""
    want, who = subsonic.norm(album), subsonic.norm(artist)
    if not want:
        return None
    for a in await remote_albums(f"{artist} {album}".strip(), 5):
        title, by = subsonic.norm(a["title"]), subsonic.norm(a["artist"])
        if (title == want or (min(len(title), len(want)) >= 4 and (want in title or title in want))) \
                and (not who or not by or who in by or by in who):
            return a
    return None


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
            common = [
                YTDLP, *YTDLP_EXTRA, "--no-warnings", "--no-playlist",
                "-f", "bestaudio[ext=m4a]/bestaudio/best",  # audio only: never touch the (often blocked) video streams
                "-x", "--audio-format", AUDIO_FORMAT, "--audio-quality", "0",
                "--embed-metadata", "--no-simulate",
                "--print", "after_move:%(artist,uploader)s\t%(track,title)s\t%(duration)s\t%(filepath)s",
                "-o", template, url,
            ]
            # Cover art embedded in the file (square crop of the thumbnail) so every app shows it.
            # Needs mutagen for opus; if embedding fails for any reason, retry without it rather than lose the song.
            code, out, err = await run(*common, *THUMB_ARGS, "--no-overwrites")
            if code != 0 and THUMB_ARGS:
                log(f"download with cover art failed for {url}, retrying without: {err.strip()[-200:]}")
                code, out, err = await run(*common)
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


# ------------------------------------------------------------ liked tracks
starred_remote: dict[str, dict] = {}  # video id -> {"starred": ISO time, "meta": {...}}


def load_state() -> None:
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    starred_remote.update(data.get("starred") or {})
    for vid, entry in starred_remote.items():
        if (entry.get("meta") or {}).get("id"):
            _meta.setdefault(vid, entry["meta"])


def save_state() -> None:
    try:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = STATE_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps({"starred": starred_remote}), encoding="utf-8")
        tmp.replace(STATE_FILE)
    except OSError as exc:
        log(f"could not save {STATE_FILE}: {exc}")


def star_remote(video_id: str) -> None:
    stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    starred_remote[video_id] = {"starred": stamp, "meta": _meta.get(video_id) or {"id": video_id}}
    save_state()


def unstar_remote(video_id: str) -> None:
    if starred_remote.pop(video_id, None) is not None:
        save_state()


def song_for(meta: dict) -> dict:
    entry = starred_remote.get(meta["id"])
    return subsonic.make_song(meta, REMOTE_TITLE_PREFIX, entry["starred"] if entry else None)


def starred_songs() -> list[dict]:
    """Hearted cloud tracks, newest first, as Subsonic song dicts."""
    entries = sorted(starred_remote.items(), key=lambda kv: kv[1]["starred"], reverse=True)
    return [song_for({**e["meta"], "id": vid}) for vid, e in entries]
