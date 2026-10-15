"""Subsonic endpoints for remote (YouTube) tracks.

`handle()` is called by the proxy before anything is forwarded to Navidrome. It returns a
Response when it dealt with the request, or None to let the plain proxy handle it.

  search3        -> Navidrome's results + YouTube results (ids look like 'yt-<videoId>')
  stream         -> audio piped from YouTube (range requests supported, so seeking works)
  download       -> downloads into the library first, then serves the file
  star           -> 'heart' a remote track = remember the like, and download it into the library
                    (unless ssDownload=0 is sent or LIKE_DOWNLOADS=0)
  getCoverArt / getSong / getLyricsBySongId -> served for remote ids
  getArtist / getAlbum -> remote artists ('ya-...') and albums ('yb-...'), and YouTube Music albums /
                          tracks added to the artists and albums you already have
"""
import asyncio
import json
import time
from pathlib import Path
from urllib.parse import parse_qsl

from starlette.background import BackgroundTask
from starlette.requests import Request
from starlette.responses import FileResponse, Response, StreamingResponse

import core
import subsonic

ENDPOINTS = {"search3", "stream", "download", "getcoverart", "getsong", "getlyricsbysongid", "star", "unstar",
             "scrobble", "getstarred2", "getstarred", "getartist", "getalbum", "getartistinfo2", "getartistinfo"}
AUTH_KEYS = ("u", "p", "t", "s", "apiKey")
HOP = {"host", "connection", "keep-alive", "transfer-encoding", "te", "upgrade", "content-length", "accept-encoding"}
SECRET_KEYS = {"p", "t", "s", "apiKey", "password"}

_auth_ok: dict[tuple, float] = {}
_cover_cache: dict[str, bytes] = {}


# ----------------------------------------------------------------- helpers
def endpoint_name(path: str) -> str | None:
    p = path.strip("/")
    if not p.startswith("rest/"):
        return None
    return p[5:].removesuffix(".view").lower()


def first(params: list[tuple[str, str]], key: str, default: str | None = None) -> str | None:
    return next((v for k, v in params if k == key), default)


def as_int(value: str | None, default: int) -> int:
    try:
        return int(value) if value is not None else default
    except ValueError:
        return default


async def get_params(request: Request) -> list[tuple[str, str]]:
    params = list(request.query_params.multi_items())
    if request.method == "POST" and request.headers.get("content-type", "").startswith("application/x-www-form-urlencoded"):
        params += parse_qsl((await request.body()).decode(errors="replace"), keep_blank_values=True)
    return params


def sanitized(params: list[tuple[str, str]]) -> str:
    return "&".join(f"{k}={'***' if k in SECRET_KEYS else v}" for k, v in params)


def api_response(as_json: bool, payload: dict | None = None, error: tuple[int, str] | None = None) -> Response:
    return Response(
        subsonic.envelope(as_json, payload, error),
        media_type="application/json" if as_json else "text/xml",
    )


def forward_headers(request: Request) -> dict[str, str]:
    return {k: v for k, v in request.headers.items() if k.lower() not in HOP}


def buffered(resp) -> Response:
    """Pass a fully-read upstream response through (body already decoded by httpx)."""
    skip = HOP | {"content-encoding"}
    out = Response(content=resp.content, status_code=resp.status_code)
    out.raw_headers = [
        (k.lower().encode("latin-1"), v.encode("latin-1"))
        for k, v in resp.headers.multi_items()
        if k.lower() not in skip
    ] + [(b"content-length", str(len(resp.content)).encode())]
    return out


async def upstream_get(client, path: str, params, headers=None):
    req = client.build_request("GET", path, params=params, headers=headers or {})
    return await client.send(req)


async def authenticated(client, params: list[tuple[str, str]]) -> bool:
    """Remote ids may only be used by someone Navidrome accepts: ask it with a ping."""
    cred = tuple(sorted((k, v) for k, v in params if k in AUTH_KEYS))
    if not any(k in ("u", "apiKey") for k, _ in cred):
        return False
    now = time.time()
    if _auth_ok.get(cred, 0) > now:
        return True
    resp = await upstream_get(client, "/rest/ping", list(cred) + [("v", subsonic.API_VERSION), ("c", "selfstream"), ("f", "json")])
    try:
        ok = resp.status_code == 200 and json.loads(resp.content)["subsonic-response"]["status"] == "ok"
    except (ValueError, KeyError, TypeError):
        ok = False
    if ok:
        _auth_ok[cred] = now + 300
        core._prune(_auth_ok, 200)
    return ok


# --------------------------------------------------------------- dispatcher
async def handle(request: Request, path: str, client) -> Response | None:
    ep = endpoint_name(path)
    if not core.REMOTE_ENABLED or ep not in ENDPOINTS:
        return None
    params = await get_params(request)
    as_json = subsonic.wants_json(first(params, "f"))

    if ep == "search3":
        return await _search3(request, client, path, params, as_json)
    if ep in ("getstarred2", "getstarred"):
        return await _starred(request, client, path, params, as_json, "starred2" if ep == "getstarred2" else "starred")

    if ep in ("getartist", "getalbum", "getartistinfo2", "getartistinfo"):
        return await _browse(request, client, path, params, as_json, ep) if core.REMOTE_BROWSE else None
    art_id = first(params, "id") or ""
    if ep == "getcoverart" and (subsonic.parse_artist_id(art_id) or subsonic.parse_album_id(art_id)):
        if not await authenticated(client, params):
            return Response(status_code=401)
        return await _art_image(client, art_id)

    remote_ids = [v for k, v in params if k == "id" and subsonic.parse_remote_id(v)]
    if not remote_ids:
        return None  # purely local request: plain proxy
    if not await authenticated(client, params):
        core.log(f"{ep}: login rejected for remote id {remote_ids[0]} (user={first(params, 'u')!r}, "
                 f"sent: {', '.join(sorted(k for k, _ in params if k in AUTH_KEYS)) or 'no credentials'})")
        return api_response(as_json, error=(40, "Wrong username or password"))

    vid = subsonic.parse_remote_id(remote_ids[0])
    if ep in ("stream", "download"):
        return await _stream(request, client, vid, download=(ep == "download"))
    if ep == "getcoverart":
        return await _cover(client, vid)
    if ep == "getsong":
        meta = await core.get_meta(vid)
        if not meta:
            return api_response(as_json, error=(70, "Song not found"))
        return api_response(as_json, {"song": core.song_for(meta)})
    if ep == "getlyricsbysongid":
        return await _lyrics(vid, as_json)
    return await _star_like(request, client, path, ep, params, as_json)  # star / unstar / scrobble


# ----------------------------------------------------------------- search3
def _start(coro) -> asyncio.Task:
    task = asyncio.create_task(coro)
    task.add_done_callback(lambda t: t.cancelled() or t.exception())  # never "exception was never retrieved"
    _background.add(task)
    task.add_done_callback(_background.discard)
    return task


def _result(task: asyncio.Task | None, default):
    if task is not None and task.done() and not task.cancelled() and task.exception() is None:
        return task.result()
    return default


async def _soon(coro, default):
    """The coroutine's result if it finishes within REMOTE_WAIT_SECONDS, else `default`.

    It keeps running (and caches its result) so the next request for the same thing is instant.
    """
    task = _start(coro)
    await asyncio.wait({task}, timeout=core.REMOTE_WAIT_SECONDS)
    return _result(task, default)


async def _search3(request, client, path, params, as_json) -> Response | None:
    query = (first(params, "query", "") or "").strip().strip('"').strip()
    want_songs = as_int(first(params, "songCount"), 20) > 0
    want_artists = core.REMOTE_BROWSE and as_int(first(params, "artistCount"), 20) > 0
    want_albums = core.REMOTE_BROWSE and as_int(first(params, "albumCount"), 20) > 0
    paging = any(as_int(first(params, k), 0) > 0 for k in ("songOffset", "artistOffset", "albumOffset"))
    # empty queries are how clients sync the whole library: never mix YouTube into that
    if len(query) < 3 or paging or not (want_songs or want_artists or want_albums):
        return None

    songs_t = _start(core.yt_search(query, core.REMOTE_RESULTS)) if want_songs else None
    artists_t = _start(core.remote_artists(query, core.REMOTE_ARTISTS)) if want_artists else None
    albums_t = _start(core.remote_albums(query, core.REMOTE_ALBUMS)) if want_albums else None
    tasks = {t for t in (songs_t, artists_t, albums_t) if t}
    try:
        resp = await upstream_get(client, "/" + path.lstrip("/"), params, forward_headers(request))
    except Exception:
        return Response("Navidrome is not reachable", status_code=502)
    # Wait a few seconds for YouTube; if it is slower, answer with local results now. The searches
    # keep running and their results are cached, so the client's next request gets them instantly.
    await asyncio.wait(tasks, timeout=core.REMOTE_WAIT_SECONDS)

    prefix = core.REMOTE_TITLE_PREFIX
    body, added = resp.content, []
    have = core.done_video_ids()
    songs = [core.song_for(m) for m in _result(songs_t, []) if m["id"] not in have]
    if songs:
        merged = subsonic.merge_search3(body, as_json, songs, prefix)
        if merged is None:
            return buffered(resp)  # upstream said no (bad credentials, error): leave it alone
        body = merged
        added.append(f"{len(songs)} song(s)")
    artists = [subsonic.make_artist(a, prefix) for a in _result(artists_t, [])]
    if artists:
        body = subsonic.merge_items(body, as_json, "searchResult3", "artist", artists, subsonic.keep_artist(prefix)) or body
        added.append(f"{len(artists)} artist(s)")
    albums = [subsonic.make_album(a, prefix) for a in _result(albums_t, [])]
    if albums:
        body = subsonic.merge_items(body, as_json, "searchResult3", "album", albums, subsonic.keep_album(prefix)) or body
        added.append(f"{len(albums)} album(s)")
    if not added:
        return buffered(resp)
    core.log(f"search {query!r}: adding YouTube {', '.join(added)}")
    return Response(body, media_type="application/json" if as_json else "text/xml")


# ------------------------------------------------- artist and album pages
def _album_songs(album: dict) -> list[dict]:
    rid = subsonic.remote_album_id(album["id"])
    songs = []
    for meta in album["tracks"]:
        song = core.song_for(meta)
        song.update(albumId=rid, parent=rid, track=meta.get("number", 0), discNumber=1)
        if album.get("year"):
            song["year"] = album["year"]
        if album.get("artistId"):
            song["artistId"] = subsonic.remote_artist_id(album["artistId"])
        songs.append(song)
    return songs


async def _remote_albums_for_artist(name: str) -> list[dict]:
    artist = await core.find_remote_artist(name)
    page = await core.remote_artist(artist["id"]) if artist else None
    return page["albums"] if page else []


async def _remote_songs_for_album(artist: str, album: str) -> list[dict]:
    found = await core.find_remote_album(artist, album)
    full = await core.remote_album(found["id"]) if found else None
    return _album_songs(full) if full else []


async def _browse(request, client, path, params, as_json, ep) -> Response | None:
    rid, prefix = first(params, "id") or "", core.REMOTE_TITLE_PREFIX
    cid, bid = subsonic.parse_artist_id(rid), subsonic.parse_album_id(rid)

    if ep in ("getartistinfo2", "getartistinfo"):
        if not (cid or bid):
            return None
        return api_response(as_json, {"artistInfo2" if ep.endswith("2") else "artistInfo": {}})

    if cid or bid:  # a remote artist or album: build the page from YouTube Music
        if not await authenticated(client, params):
            return api_response(as_json, error=(40, "Wrong username or password"))
        if cid:
            page = await core.remote_artist(cid)
            if not page:
                return api_response(as_json, error=(70, "Artist not found"))
            albums = [subsonic.make_album(a, prefix) for a in page["albums"]]
            return api_response(as_json, {"artist": {**subsonic.make_artist(page, prefix, len(albums)), "album": albums}})
        album = await core.remote_album(bid)
        if not album:
            return api_response(as_json, error=(70, "Album not found"))
        songs = _album_songs(album)
        head = subsonic.make_album(album, prefix, len(songs), sum(s["duration"] for s in songs))
        return api_response(as_json, {"album": {**head, "song": songs}})

    # a library artist or album: Navidrome's page, plus what YouTube Music has that the library lacks
    try:
        resp = await upstream_get(client, "/" + path.lstrip("/"), params, forward_headers(request))
    except Exception:
        return Response("Navidrome is not reachable", status_code=502)
    if ep == "getartist":
        ent = subsonic.entity(resp.content, as_json, "artist")
        if not ent or not ent.get("name"):
            return buffered(resp)
        albums = [subsonic.make_album(a, prefix) for a in await _soon(_remote_albums_for_artist(ent["name"]), [])]
        merged = subsonic.merge_items(resp.content, as_json, "artist", "album", albums,
                                      subsonic.keep_album(prefix, same_artist=False), create=False) if albums else None
    else:
        ent = subsonic.entity(resp.content, as_json, "album")
        name = (ent or {}).get("name") or (ent or {}).get("title")
        if not name:
            return buffered(resp)
        songs = await _soon(_remote_songs_for_album((ent or {}).get("artist", ""), name), [])
        merged = subsonic.merge_items(resp.content, as_json, "album", "song", songs,
                                      subsonic.keep_song(prefix), create=False) if songs else None
    if merged is None:
        return buffered(resp)
    return Response(merged, media_type="application/json" if as_json else "text/xml")


# ------------------------------------------------------------------ stream
async def _stream(request: Request, client, vid: str, download: bool) -> Response:
    local = core.downloaded.get(vid)
    if local and Path(local).is_file():  # already in the library: play that file, no YouTube needed
        return FileResponse(local, media_type=core.CONTENT_TYPES.get(Path(local).suffix.lstrip(".").lower()))
    if download:
        # an explicit client-side download: put it into the library first, then serve that file
        job = core.start_ingest(core.watch_url(vid), vid)
        try:
            await asyncio.wait_for(core.job_events[job["id"]].wait(), core.DOWNLOAD_WAIT_SECONDS)
        except asyncio.TimeoutError:
            pass
        if job["status"] == "done" and Path(job.get("file", "")).is_file():
            return FileResponse(job["file"])
        # fall through: still give the client the audio even if the library copy failed
    return await _pipe(request, client, vid, retry=True)


async def _pipe(request: Request, client, vid: str, retry: bool) -> Response:
    info = await core.resolve_stream(vid)
    if not info:
        return Response("Could not get audio for this track", status_code=502)
    headers = dict(info["headers"])
    client_range = request.headers.get("range")
    headers["Range"] = client_range or "bytes=0-"
    try:
        # YouTube's servers often answer with a redirect to another host: follow it ourselves,
        # otherwise the app would receive a bare 302 it cannot use
        resp = await client.send(client.build_request("GET", info["url"], headers=headers),
                                 stream=True, follow_redirects=True)
    except Exception as exc:
        core.log(f"stream {vid}: request to YouTube failed: {type(exc).__name__}: {exc}")
        return Response("Could not reach YouTube", status_code=502)
    if resp.status_code in (403, 404, 410) and retry:  # signed URL expired: resolve again, once
        await resp.aclose()
        await core.resolve_stream(vid, fresh=True)
        return await _pipe(request, client, vid, retry=False)
    if resp.status_code >= 300:  # anything but a success: never hand an error or redirect to the player
        core.log(f"stream {vid}: YouTube answered {resp.status_code}")
        await resp.aclose()
        return Response(f"YouTube answered {resp.status_code}", status_code=502)

    status = resp.status_code
    out = {"Accept-Ranges": "bytes"}
    if resp.headers.get("content-length"):
        out["Content-Length"] = resp.headers["content-length"]
    if client_range and resp.headers.get("content-range"):
        out["Content-Range"] = resp.headers["content-range"]
    if not client_range and status == 206:
        status = 200  # we asked for bytes=0- ourselves; the client expects a plain 200
    return StreamingResponse(
        resp.aiter_raw(),
        status_code=status,
        headers=out,
        media_type=core.CONTENT_TYPES.get(info.get("ext", "m4a"), "audio/mp4"),
        background=BackgroundTask(resp.aclose),
    )


# ---------------------------------------------------------------- cover art
async def _image(client, key: str, urls: list[str]) -> Response:
    data = _cover_cache.get(key)
    if data is None:
        for url in urls:
            try:
                resp = await client.send(client.build_request("GET", url))
            except Exception:
                continue
            if resp.status_code == 200 and resp.content:
                data = resp.content
                break
        if data is None:
            return Response(status_code=404)
        _cover_cache[key] = data
        core._prune(_cover_cache, 300)
    return Response(data, media_type="image/jpeg", headers={"Cache-Control": "public, max-age=86400"})


async def _cover(client, vid: str) -> Response:
    urls = [f"https://i.ytimg.com/vi/{vid}/{name}.jpg" for name in ("hqdefault", "mqdefault", "default")]
    album_art = (core._meta.get(vid) or {}).get("cover")  # square album cover from YouTube Music
    if album_art:
        urls.insert(0, subsonic.cover_url(album_art))
    return await _image(client, vid, urls)


async def _art_image(client, rid: str) -> Response:
    """Picture of a remote artist or album."""
    if rid not in core._art_urls:  # e.g. after a restart: look the page up again to learn its picture
        album, artist = subsonic.parse_album_id(rid), subsonic.parse_artist_id(rid)
        if album:
            await core.remote_album(album)
        elif artist:
            await core.remote_artist(artist)
    url = core._art_urls.get(rid)
    if not url:
        return Response(status_code=404)
    return await _image(client, rid, [subsonic.cover_url(url)])


# ------------------------------------------------------------------ lyrics
async def _lyrics(vid: str, as_json: bool) -> Response:
    meta = await core.get_meta(vid)
    if not meta:
        return api_response(as_json, subsonic.lyrics_payload("", "", []))
    song = core.song_for(meta)
    artist, title = song["artist"], song["title"].removeprefix(core.REMOTE_TITLE_PREFIX)
    lrc = await core.fetch_lrc(artist, title, meta.get("duration"))
    return api_response(as_json, subsonic.lyrics_payload(artist, title, subsonic.parse_lrc(lrc) if lrc else []))


# --------------------------------------------------- star / unstar / scrobble
async def _star_like(request, client, path, ep, params, as_json) -> Response:
    remote_ids = [subsonic.parse_remote_id(v) for k, v in params if k == "id" and subsonic.parse_remote_id(v)]
    if ep == "star":  # hearting a cloud track: remember the like, and download it into the library
        auth = [(k, v) for k, v in params if k in AUTH_KEYS]
        for vid in remote_ids:
            if not await core.get_meta(vid):
                return api_response(as_json, error=(70, "Could not look up this track"))
            core.star_remote(vid)
            flag = first(params, "ssDownload")
            if not (core.LIKE_DOWNLOADS if flag is None else flag != "0"):
                core.log(f"star: liked {vid} (kept as a cloud track, not downloaded)")
                continue
            core.log(f"star: liked {vid}; downloading it into the library")
            job = core.start_ingest(core.watch_url(vid), vid)
            task = asyncio.create_task(finalize_star(client, vid, job["id"], auth))
            _background.add(task)
            task.add_done_callback(_background.discard)
    elif ep == "unstar":  # un-hearting forgets the like but never deletes a downloaded file
        for vid in remote_ids:
            core.unstar_remote(vid)
    local = [(k, v) for k, v in params if not (k == "id" and subsonic.parse_remote_id(v))]
    if any(k in ("id", "albumId", "artistId") for k, _ in local):  # mixed request: forward the local part
        return buffered(await upstream_get(client, "/" + path.lstrip("/"), local, forward_headers(request)))
    return api_response(as_json)


_background: set[asyncio.Task] = set()  # keeps references so running tasks are not garbage collected


async def finalize_star(client, vid: str, job_id: str, auth: list[tuple[str, str]]) -> None:
    """When the download has reached the library, heart that copy too and drop the cloud entry.

    Uses the login of the person who hearted it; it is only kept in memory for this task.
    """
    try:
        await core.job_events[job_id].wait()
        job = core.jobs.get(job_id) or {}
        if job.get("status") != "done" or vid not in core.starred_remote:
            return
        base = auth + [("v", subsonic.API_VERSION), ("c", "selfstream"), ("f", "json")]
        query = f"{job.get('title', '')} {job.get('artist', '')}".strip()
        for attempt, delay in enumerate(core.FINALIZE_DELAYS):
            await asyncio.sleep(delay)
            if vid not in core.starred_remote:  # un-hearted in the meantime
                return
            if attempt == 0:
                await upstream_get(client, "/rest/startScan", base)  # needs an admin; harmless if refused
            resp = await upstream_get(client, "/rest/search3", base + [
                ("query", query), ("songCount", "20"), ("artistCount", "0"), ("albumCount", "0")])
            match = subsonic.find_local_match(
                subsonic.songs_in(resp.content, True, "searchResult3"), job.get("title", ""), job.get("artist", ""))
            if match:
                await upstream_get(client, "/rest/star", base + [("id", match["id"])])
                core.unstar_remote(vid)
                core.log(f"liked the library copy ({match['id']}); cloud entry {vid} removed")
                return
        core.log(f"{vid} did not show up in the library in time; it stays liked as a cloud track")
    except Exception as exc:
        core.log(f"could not finish the like for {vid}: {type(exc).__name__}: {exc}")


# ------------------------------------------------------------ liked list
async def _starred(request, client, path, params, as_json, key: str) -> Response | None:
    """getStarred2/getStarred: Navidrome's favourites plus the cloud tracks the user hearted."""
    if not core.starred_remote:
        return None
    try:
        resp = await upstream_get(client, "/" + path.lstrip("/"), params, forward_headers(request))
    except Exception:
        return Response("Navidrome is not reachable", status_code=502)
    merged = subsonic.merge_starred(resp.content, as_json, key, core.starred_songs(), core.REMOTE_TITLE_PREFIX)
    if merged is None:
        return buffered(resp)
    return Response(merged, media_type="application/json" if as_json else "text/xml")
