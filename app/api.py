"""selfstream's own API for the app: /selfstream/api/v1/*

Sign-in is Navidrome's: the app sends the same Subsonic credentials (u + t/s, or u + p) as query parameters, and
selfstream checks them with a ping. Everything is stored per Navidrome user, so a second device sees the same data.
"""
import asyncio
import json
import re
import time

from starlette.requests import Request
from starlette.responses import JSONResponse, Response

import core
import discover
import screens
import follows
import remote
import store as store_mod
import subsonic

PREFIX = "selfstream/api/v1/"
VERSION = "0.3"
FEATURES = ["playlists", "history", "stats", "discover", "follows", "state", "remote-playlists", "genres", "suggest", "like-download", "share"]


def features() -> list[str]:
    """What this server offers right now. Discord presence needs an application ID in DISCORD_CLIENT_ID."""
    return FEATURES + (["presence"] if core.DISCORD_CLIENT_ID else [])

st: "store_mod.Store | None" = None
_discover_lock = asyncio.Lock()


def err(status: int, message: str) -> JSONResponse:
    return JSONResponse({"error": message}, status_code=status)


class Ctx:
    def __init__(self, request: Request, client, params, user: str | None):
        self.request, self.client, self.params, self.user = request, client, params, user

    def arg(self, key: str, default: str = "") -> str:
        return remote.first(self.params, key, default) or default

    async def body(self) -> dict:
        try:
            data = await self.request.json()
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}


# ------------------------------------------------------------------ handlers
async def ping(c: Ctx):
    return JSONResponse({"selfstream": True, "version": VERSION, "features": features(), "authenticated": c.user is not None,
                         "user": c.user})


async def list_playlists(c: Ctx):
    return JSONResponse({"playlists": st.playlists(c.user)})


async def create_playlist(c: Ctx):
    b = await c.body()
    name = str(b.get("name") or "").strip()
    if not name:
        return err(400, "name is required")
    songs = [s for s in b.get("songs") or [] if isinstance(s, dict)]
    return JSONResponse(st.create_playlist(c.user, name, str(b.get("description") or ""), songs=songs), status_code=201)


async def get_playlist(c: Ctx, pid: str):
    p = st.playlist(c.user, pid)
    return JSONResponse(p) if p else err(404, "no such playlist")


async def patch_playlist(c: Ctx, pid: str):
    b = await c.body()
    if not st.update_playlist(c.user, pid, b.get("name"), b.get("description")):
        return err(404, "no such playlist")
    return JSONResponse(st.playlist(c.user, pid))


async def delete_playlist(c: Ctx, pid: str):
    return JSONResponse({"ok": True}) if st.delete_playlist(c.user, pid) else err(404, "no such playlist")


async def add_songs(c: Ctx, pid: str):
    b = await c.body()
    songs = [s for s in b.get("songs") or [] if isinstance(s, dict)]
    n = st.add_songs(c.user, pid, songs)
    if n < 0:
        return err(404, "no such playlist")
    if songs and n == 0:  # nothing new: say so instead of letting the app report an add
        return err(409, "Already in this playlist" if len(songs) == 1 else "All of these are already in this playlist")
    return JSONResponse({"added": n, "playlist": st.playlist(c.user, pid)})


async def remove_song(c: Ctx, pid: str, item: str):
    return JSONResponse({"ok": True}) if st.remove_item(c.user, pid, int(item)) else err(404, "no such item")


async def order_playlist(c: Ctx, pid: str):
    b = await c.body()
    ids = [int(i) for i in b.get("itemIds") or [] if str(i).isdigit()]
    return JSONResponse({"ok": True}) if st.reorder(c.user, pid, ids) else err(404, "no such playlist")


async def post_play(c: Ctx):
    b = await c.body()
    song = b.get("song")
    if not isinstance(song, dict):
        return err(400, "song is required")
    res = st.record_play(c.user, song, float(b.get("seconds") or 0), b.get("duration"), b.get("ts"))
    return JSONResponse(res, status_code=200 if res["ok"] else 400)


async def post_plays(c: Ctx):
    """Several listens at once: the app sends what it collected while offline."""
    b = await c.body()
    n = 0
    for p in (b.get("plays") or [])[:500]:
        if isinstance(p, dict) and isinstance(p.get("song"), dict):
            n += st.record_play(c.user, p["song"], float(p.get("seconds") or 0), p.get("duration"), p.get("ts"))["ok"]
    return JSONResponse({"recorded": n})


async def get_history(c: Ctx):
    return JSONResponse({"history": st.history(c.user, min(int(c.arg("limit", "50")), 500), int(c.arg("offset", "0")))})


async def get_stats(c: Ctx):
    return JSONResponse(st.stats(c.user, c.arg("range", "month"), int(c.arg("tz", "0") or 0)))


async def _build_discover(c: Ctx, variant: int) -> dict:
    return await discover.build(st, c.user, core.radio_for, core.find_video, core.latest_tracks, time.time(), variant)


async def get_discover(c: Ctx):
    now = time.time()
    week = discover.week_id(now)
    var = st.get_state(c.user, "discover-variant")
    variant = (var or {}).get("value", {}).get("n", 0) if (var or {}).get("value", {}).get("week") == week else 0
    if c.arg("refresh") == "1":
        variant += 1
        st.set_state(c.user, "discover-variant", {"week": week, "n": variant})
    key = f"discover:{week}:{variant}"
    cached = st.get_state(c.user, key)
    if cached and not cached["value"].get("needsHistory"):
        return JSONResponse(cached["value"])
    async with _discover_lock:
        try:
            result = await asyncio.wait_for(_build_discover(c, variant), 90)
        except asyncio.TimeoutError:
            return err(504, "Discover took too long, try again")
    if result["songs"]:
        st.set_state(c.user, key, result)
    return JSONResponse(result)


async def list_follows(c: Ctx):
    return JSONResponse({"follows": st.follows(c.user)})


async def post_follow(c: Ctx):
    b = await c.body()
    kind, rid = str(b.get("kind") or ""), str(b.get("id") or "")
    if kind not in follows.KINDS or not rid:
        return err(400, "kind must be album, artist or playlist, and id is required")
    if not (subsonic.parse_album_id(rid) or subsonic.parse_artist_id(rid) or rid.startswith("yp-")):
        return err(400, "only YouTube Music albums (yb-), artists (ya-) and playlists (yp-) can be followed")
    f = st.follow(c.user, kind, rid, str(b.get("name") or "")[:200], str(b.get("cover") or ""), str(b.get("artist") or ""))
    return JSONResponse(f, status_code=201)


async def delete_follow(c: Ctx, fid: str):
    ok = st.unfollow(c.user, fid, delete_playlist=c.arg("deletePlaylist") == "1")
    return JSONResponse({"ok": True}) if ok else err(404, "no such follow")


async def retry_follow(c: Ctx, fid: str):
    f = st.get_follow(c.user, fid)
    if not f:
        return err(404, "no such follow")
    st.update_follow(fid, status="queued")
    return JSONResponse(st.get_follow(c.user, fid))


async def search_playlists(c: Ctx):
    q = c.arg("q").strip()
    return JSONResponse({"playlists": await core.remote_playlists(q, 8) if len(q) >= 2 else []})


async def presence(c: Ctx):
    title, artist = c.arg("title").strip(), c.arg("artist").strip()
    if not title:
        return err(400, "title is required")
    return JSONResponse(await core.presence_for(c.arg("id").strip(), title, artist, c.arg("album").strip()))


async def share(c: Ctx):
    title, artist = c.arg("title").strip(), c.arg("artist").strip()
    if not title:
        return err(400, "title is required")
    r = await core.share_for(c.arg("id").strip(), title, artist)
    if not r["url"]:
        return err(404, "no link found for this song")
    return JSONResponse(r)


async def suggest(c: Ctx):
    q = c.arg("q").strip()
    return JSONResponse({"suggestions": await core.suggestions(q) if len(q) >= 2 else []})


async def remote_playlist(c: Ctx, pid: str):
    p = await core.remote_playlist(pid)
    if not p:
        return err(404, "playlist not found")
    return JSONResponse({"id": f"yp-{p['id']}", "cloud": True, "name": p["title"], "author": p["author"], "cover": p["cover"],
                         "songs": [subsonic.make_song(t) for t in p["tracks"]]})


async def home(c: Ctx):
    week = st.stats(c.user, "week", int(c.arg("tz", "0") or 0))
    return JSONResponse({
        "recent": st.recent_tracks(c.user, 20),
        "mostPlayed": [t["song"] for t in week["topTracks"][:10]],
        "weekHours": week["totalHours"],
        "streak": week["currentStreak"],
        "playlists": st.playlists(c.user)[:10],
        "follows": st.follows(c.user)[:10],
    })


async def get_manifest(c: Ctx):
    return JSONResponse(screens.manifest(features()))


async def get_screen(c: Ctx, sid: str):
    tz = int(c.arg("tz", "0") or 0)
    if sid == "home":
        week = st.stats(c.user, "week", tz)
        return JSONResponse(screens.home(st.recent_tracks(c.user, 20), [t["song"] for t in week["topTracks"][:10]],
                                         week["totalHours"], week["currentStreak"], st.playlists(c.user), set(features())))
    if sid == "discover":
        resp = await get_discover(c)
        if resp.status_code != 200:
            return resp
        return JSONResponse(screens.discover(json.loads(resp.body), stats="stats" in features()))
    if sid == "activity":
        rng = c.arg("range", "month")
        return JSONResponse(screens.activity(st.stats(c.user, rng, tz), rng if rng in ("week", "month", "year", "all") else "month"))
    return err(404, "no such screen")


STATE_KEY = re.compile(r"^[a-z0-9_-]{1,40}$")


async def get_state(c: Ctx, key: str):
    if not STATE_KEY.match(key):
        return err(400, "bad key")
    s = st.get_state(c.user, key)
    return JSONResponse(s or {"value": None, "updated": 0})


async def put_state(c: Ctx, key: str):
    if not STATE_KEY.match(key):
        return err(400, "bad key")
    b = await c.body()
    raw = json.dumps(b.get("value"))
    if len(raw) > 200_000:
        return err(413, "too large")
    return JSONResponse({"updated": st.set_state(c.user, key, b.get("value"))})


ROUTES = [
    ("GET", r"ping", ping, False),
    ("GET", r"playlists", list_playlists, True),
    ("POST", r"playlists", create_playlist, True),
    ("GET", r"playlists/([\w-]+)", get_playlist, True),
    ("PATCH", r"playlists/([\w-]+)", patch_playlist, True),
    ("DELETE", r"playlists/([\w-]+)", delete_playlist, True),
    ("POST", r"playlists/([\w-]+)/songs", add_songs, True),
    ("DELETE", r"playlists/([\w-]+)/songs/(\d+)", remove_song, True),
    ("PUT", r"playlists/([\w-]+)/order", order_playlist, True),
    ("POST", r"plays", post_play, True),
    ("POST", r"plays/batch", post_plays, True),
    ("GET", r"history", get_history, True),
    ("GET", r"stats", get_stats, True),
    ("GET", r"discover", get_discover, True),
    ("GET", r"follows", list_follows, True),
    ("POST", r"follows", post_follow, True),
    ("DELETE", r"follows/([\w-]+)", delete_follow, True),
    ("POST", r"follows/([\w-]+)/retry", retry_follow, True),
    ("GET", r"suggest", suggest, True),
    ("GET", r"presence", presence, True),
    ("GET", r"share", share, True),
    ("GET", r"manifest", get_manifest, True),
    ("GET", r"screens/([a-z0-9_-]+)", get_screen, True),
    ("GET", r"remote/playlists", search_playlists, True),
    ("GET", r"remote/playlists/([\w-]+)", remote_playlist, True),
    ("GET", r"home", home, True),
    ("GET", r"state/([\w-]+)", get_state, True),
    ("PUT", r"state/([\w-]+)", put_state, True),
]


async def handle(request: Request, path: str, client) -> Response | None:
    """The v1 API, or None when the path is not ours."""
    if not path.startswith(PREFIX):
        return None
    if not core.EXTENSION_API:
        return err(404, "unknown endpoint")
    if st is None:
        return err(503, "database not ready")
    sub = path[len(PREFIX):].strip("/")
    params = list(request.query_params.multi_items())
    method = request.method
    if method == "OPTIONS":
        return Response(status_code=204)
    matched = [(fn, m.groups(), needs) for meth, pat, fn, needs in ROUTES
               for m in [re.fullmatch(pat, sub)] if m and meth == method]
    if not matched:
        return err(404 if not any(re.fullmatch(p, sub) for _, p, _, _ in ROUTES) else 405, "unknown endpoint")
    fn, groups, needs_auth = matched[0]
    user = None
    if any(k in ("u", "apiKey") for k, _ in params) and await remote.authenticated(client, params):
        user = remote.first(params, "u") or "apikey"
    if needs_auth and user is None:
        return err(401, "Wrong username or password")
    try:
        return await fn(Ctx(request, client, params, user), *groups)
    except Exception as exc:  # a bug here must never take the music proxy down
        core.log(f"api {method} /{sub}: {type(exc).__name__}: {exc}")
        return err(500, f"{type(exc).__name__}: {exc}")
