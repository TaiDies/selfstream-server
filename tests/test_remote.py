"""Handler tests: a fake Navidrome, a fake YouTube CDN and a fake yt-dlp script, no network."""
import asyncio
import json
import os
import stat
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from types import SimpleNamespace

TMP = tempfile.mkdtemp(prefix="selfstream-test-")
MUSIC = os.path.join(TMP, "music")
os.makedirs(MUSIC)
FAKE_YTDLP = os.path.join(TMP, "yt-dlp")
with open(FAKE_YTDLP, "w") as f:
    f.write(r"""#!/bin/bash
args="$*"
case "$args" in
  *ZZZZZZZZZZZ*) echo "ERROR: Video unavailable" >&2; exit 1 ;;
  *ytsearch*)
    echo '{"id":"AAAAAAAAAAA","title":"Rick Astley - Never Gonna Give You Up (Official Video)","channel":"Rick Astley","duration":213}'
    echo '{"id":"BBBBBBBBBBB","title":"Two hour mix","channel":"DJ","duration":7200}'
    echo '{"id":"CCCCCCCCCCC","title":"Live stream","channel":"X","duration":100,"live_status":"is_live"}'
    ;;
  *--print*)
    mkdir -p "$MUSIC_DIR/Rick Astley"
    file="$MUSIC_DIR/Rick Astley/Never Gonna Give You Up.opus"
    echo audio-bytes > "$file"
    printf 'Rick Astley\tNever Gonna Give You Up\t213\t%s\n' "$file"
    ;;
  *" -j "*)
    echo '{"url":"https://cdn.fake/audio.m4a","http_headers":{"User-Agent":"fake-ua"},"ext":"m4a","title":"Rick Astley - Never Gonna Give You Up","channel":"Rick Astley","duration":213}'
    ;;
  *) exit 1 ;;
esac
""")
os.chmod(FAKE_YTDLP, os.stat(FAKE_YTDLP).st_mode | stat.S_IEXEC)

STATE = os.path.join(TMP, "state", "selfstream-state.json")
os.environ.update(MUSIC_DIR=MUSIC, PROXY_LOG="0", YTDLP_BIN=FAKE_YTDLP, YTDLP_EXTRA_ARGS="", REMOTE_WAIT_SECONDS="5",
                  SEARCH_SOURCE="youtube", STATE_FILE=STATE)
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

from starlette.applications import Starlette  # noqa: E402
from starlette.responses import Response  # noqa: E402
from starlette.routing import Route  # noqa: E402

import core  # noqa: E402
import remote  # noqa: E402

NS = "{http://subsonic.org/restapi}"
AUDIO = bytes(range(256)) * 4
AUTH = "u=admin&t=good&s=salt&v=1.16.1&c=test"


class FakeHeaders:
    def __init__(self, items):
        self._items = list(items)

    def items(self):
        return self._items

    multi_items = items

    def get(self, key, default=None):
        return next((v for k, v in self._items if k.lower() == key.lower()), default)

    def __getitem__(self, key):
        v = self.get(key)
        if v is None:
            raise KeyError(key)
        return v


class FakeResp:
    def __init__(self, status=200, content=b"", headers=()):
        self.status_code, self.content, self.headers, self.closed = status, content, FakeHeaders(headers), False

    async def aiter_raw(self):
        for i in range(0, len(self.content), 64):
            yield self.content[i:i + 64]

    async def aclose(self):
        self.closed = True


class FakeClient:
    """Stands in for the httpx client: Navidrome, the YouTube CDN and thumbnails."""

    def __init__(self):
        self.calls = []
        self.local_songs = [{"id": "loc1", "title": "Some Local Song", "artist": "Local Artist"}]
        self.local_starred = [{"id": "loc5", "title": "Liked Local", "artist": "Local Artist", "starred": "2026-01-01T00:00:00Z"}]
        self.library_after_scan = []  # songs that appear in search once startScan was called
        self.scanned = False
        self.cdn_fail_first = 0
        self.cdn_redirect = False
        self.search_status = "ok"

    def build_request(self, method, url, params=None, headers=None, content=None):
        return SimpleNamespace(method=method, url=url, params=list(params or []), headers=dict(headers or {}))

    async def send(self, req, stream=False, follow_redirects=False):
        req.follow_redirects = follow_redirects
        self.calls.append(req)
        p = dict(req.params)
        req = SimpleNamespace(**{**vars(req), "url": req.url.removesuffix(".view")})  # Navidrome accepts '.view' too
        if req.url == "/rest/startScan":
            self.scanned = True
            return FakeResp(200, b'{"subsonic-response":{"status":"ok"}}', [("Content-Type", "application/json")])
        if req.url in ("/rest/getStarred2", "/rest/getStarred"):
            key = "starred2" if req.url.endswith("2") else "starred"
            if p.get("f") == "json":
                body = {"subsonic-response": {"status": "ok", "version": "1.16.1", key: {"song": self.local_starred}}}
                return FakeResp(200, json.dumps(body).encode(), [("Content-Type", "application/json")])
            songs = "".join(f'<song id="{s["id"]}" title="{s["title"]}" artist="{s["artist"]}"/>' for s in self.local_starred)
            xml = (f'<?xml version="1.0"?><subsonic-response xmlns="http://subsonic.org/restapi" status="ok" '
                   f'version="1.16.1"><{key}>{songs}</{key}></subsonic-response>')
            return FakeResp(200, xml.encode(), [("Content-Type", "text/xml")])
        if req.url.startswith("https://lh3.fake/"):
            return FakeResp(200 if req.url.endswith("=w544-h544-l90-rj") else 404, b"SQUARE")
        if req.url == "/rest/ping":
            ok = p.get("u") == "admin" and p.get("t") == "good"
            body = {"subsonic-response": {"status": "ok" if ok else "failed", "version": "1.16.1"}}
            return FakeResp(200, json.dumps(body).encode())
        if req.url.startswith("/rest/search3"):
            local = self.local_songs + (self.library_after_scan if self.scanned else [])
            if p.get("f") == "json":
                body = {"subsonic-response": {"status": self.search_status, "version": "1.16.1",
                                              "searchResult3": {"song": local}}}
                return FakeResp(200, json.dumps(body).encode(), [("Content-Type", "application/json")])
            songs = "".join(f'<song id="{s["id"]}" title="{s["title"]}" artist="{s["artist"]}"/>' for s in local)
            xml = (f'<?xml version="1.0"?><subsonic-response xmlns="http://subsonic.org/restapi" '
                   f'status="{self.search_status}" version="1.16.1"><searchResult3>{songs}</searchResult3></subsonic-response>')
            return FakeResp(200, xml.encode(), [("Content-Type", "text/xml")])
        if req.url == "/rest/star":
            return FakeResp(200, b'{"subsonic-response":{"status":"ok"}}', [("Content-Type", "application/json")])
        if req.url == "https://cdn.fake/audio.m4a":
            if self.cdn_redirect:  # the real client would follow this; the fake just shows what a raw 302 looks like
                return FakeResp(302, b"", [("Location", "https://cdn2.fake/audio.m4a")])
            if self.cdn_fail_first > 0:
                self.cdn_fail_first -= 1
                return FakeResp(403)
            rng = req.headers.get("Range", "")
            start, _, end = rng.removeprefix("bytes=").partition("-")
            a = int(start or 0)
            b = int(end) if end else len(AUDIO) - 1
            return FakeResp(206, AUDIO[a:b + 1], [("Content-Length", str(b - a + 1)),
                                                  ("Content-Range", f"bytes {a}-{b}/{len(AUDIO)}")])
        if req.url.startswith("https://i.ytimg.com/vi/"):
            return FakeResp(200 if "hqdefault" in req.url else 404, b"JPEG")
        return FakeResp(404)


async def asgi(app, method="GET", path="/rest/search3", query="", headers=None, body=b""):
    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": method, "scheme": "http",
             "path": path, "raw_path": path.encode(), "query_string": query.encode(),
             "headers": [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()],
             "server": ("test", 80), "client": ("c", 1)}
    sent = False

    async def receive():
        nonlocal sent
        if not sent:
            sent = True
            return {"type": "http.request", "body": body, "more_body": False}
        await asyncio.sleep(3600)

    out = {"status": None, "headers": {}, "body": b""}

    async def send(msg):
        if msg["type"] == "http.response.start":
            out["status"] = msg["status"]
            out["headers"] = {k.decode().lower(): v.decode() for k, v in msg["headers"]}
        elif msg["type"] == "http.response.body":
            out["body"] += msg.get("body", b"")

    await app(scope, receive, send)
    return out


class RemoteTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        for cache in (core._search_cache, core._meta, core._stream_cache, core._lrc_cache, core.jobs,
                      core.job_events, core.ingest_by_video, remote._auth_ok, remote._cover_cache,
                      core.starred_remote):
            cache.clear()
        if os.path.exists(STATE):
            os.remove(STATE)
        core.SEARCH_SOURCE = "youtube"
        core.FINALIZE_DELAYS = (0.01, 0.01, 0.01)
        self.client = FakeClient()

        async def fake_lrc(artist, title, duration):
            return "[00:01.00]Never gonna\n[00:02.50]give you up" if title == "Never Gonna Give You Up" else None

        core.fetch_lrc = fake_lrc

        async def endpoint(request):
            res = await remote.handle(request, request.path_params["path"], self.client)
            return res if res is not None else Response("PASSTHROUGH")

        self.app = Starlette(routes=[Route("/{path:path}", endpoint, methods=["GET", "POST", "HEAD"])])

    async def asyncTearDown(self):
        for task in list(remote._background):
            task.cancel()
        await asyncio.gather(*remote._background, return_exceptions=True)

    async def get(self, path, query="", **kw):
        return await asgi(self.app, path=path, query=query, **kw)

    def songs_json(self, res):
        return json.loads(res["body"])["subsonic-response"]["searchResult3"]["song"]

    # ----------------------------------------------------------------- search
    async def test_search_json_merges_remote_after_local(self):
        res = await self.get("/rest/search3", f"{AUTH}&f=json&query=never+gonna")
        songs = self.songs_json(res)
        self.assertEqual([s["id"] for s in songs], ["loc1", "yt-AAAAAAAAAAA"])  # long mix + live stream filtered out
        remote_song = songs[1]
        self.assertEqual((remote_song["title"], remote_song["artist"], remote_song["album"]),
                         ("☁ Never Gonna Give You Up", "Rick Astley", "YouTube"))
        self.assertEqual(remote_song["duration"], 213)

    async def test_search_xml_default_format(self):
        res = await self.get("/rest/search3.view", f"{AUTH}&query=never+gonna")
        root = ET.fromstring(res["body"])
        ids = [s.get("id") for s in root.find(f"{NS}searchResult3")]
        self.assertEqual(ids, ["loc1", "yt-AAAAAAAAAAA"])
        self.assertIn("xml", res["headers"]["content-type"])

    async def test_search_skips_library_sync_and_pagination(self):
        for query in ('query=', 'query=%22%22', 'query=ab', 'query=never+gonna&songOffset=20', 'query=never+gonna&songCount=0'):
            res = await self.get("/rest/search3", f"{AUTH}&f=json&{query}")
            self.assertEqual(res["body"], b"PASSTHROUGH", query)

    async def test_search_leaves_failed_upstream_alone(self):
        self.client.search_status = "failed"
        res = await self.get("/rest/search3", "u=bad&t=bad&f=json&query=never+gonna")
        self.assertNotIn(b"yt-", res["body"])
        self.assertEqual(json.loads(res["body"])["subsonic-response"]["status"], "failed")

    async def test_search_dedupes_against_library(self):
        self.client.local_songs = [{"id": "loc9", "title": "Never Gonna Give You Up", "artist": "Rick Astley"}]
        res = await self.get("/rest/search3", f"{AUTH}&f=json&query=never+gonna")
        self.assertEqual([s["id"] for s in self.songs_json(res)], ["loc9"])

    async def test_search_survives_ytdlp_failure(self):
        core.YTDLP = "/nonexistent/yt-dlp"
        try:
            res = await self.get("/rest/search3", f"{AUTH}&f=json&query=never+gonna")
        finally:
            core.YTDLP = FAKE_YTDLP
        self.assertEqual([s["id"] for s in self.songs_json(res)], ["loc1"])

    # ----------------------------------------------------------------- stream
    async def test_stream_range_request_is_206(self):
        res = await self.get("/rest/stream", f"{AUTH}&id=yt-AAAAAAAAAAA", headers={"Range": "bytes=10-19"})
        self.assertEqual(res["status"], 206)
        self.assertEqual(res["body"], AUDIO[10:20])
        self.assertEqual(res["headers"]["content-range"], f"bytes 10-19/{len(AUDIO)}")
        self.assertEqual(res["headers"]["accept-ranges"], "bytes")
        self.assertEqual(res["headers"]["content-type"], "audio/mp4")
        self.assertEqual(self.client.calls[-1].headers["User-Agent"], "fake-ua")  # yt-dlp's headers are forwarded

    async def test_stream_without_range_is_plain_200(self):
        res = await self.get("/rest/stream.view", f"{AUTH}&id=yt-AAAAAAAAAAA")
        self.assertEqual(res["status"], 200)
        self.assertEqual(res["body"], AUDIO)
        self.assertNotIn("content-range", res["headers"])
        self.assertEqual(self.client.calls[-1].headers["Range"], "bytes=0-")

    async def test_stream_retries_once_when_url_expired(self):
        self.client.cdn_fail_first = 1
        res = await self.get("/rest/stream", f"{AUTH}&id=yt-AAAAAAAAAAA")
        self.assertEqual((res["status"], res["body"]), (200, AUDIO))
        self.assertEqual(sum(c.url == "https://cdn.fake/audio.m4a" for c in self.client.calls), 2)

    async def test_stream_gives_up_after_second_failure(self):
        self.client.cdn_fail_first = 5
        res = await self.get("/rest/stream", f"{AUTH}&id=yt-AAAAAAAAAAA")
        self.assertEqual(res["status"], 502)

    async def test_remote_ids_require_valid_credentials(self):
        for q in ("u=admin&t=wrong&s=x&v=1&c=t&f=json&id=yt-AAAAAAAAAAA", "f=json&id=yt-AAAAAAAAAAA"):
            res = await self.get("/rest/stream", q)
            self.assertEqual(json.loads(res["body"])["subsonic-response"]["error"]["code"], 40, q)

    async def test_local_ids_are_not_touched(self):
        for ep in ("stream", "getCoverArt", "getSong", "star", "getLyricsBySongId"):
            res = await self.get(f"/rest/{ep}", f"{AUTH}&id=12345")
            self.assertEqual(res["body"], b"PASSTHROUGH", ep)
        self.assertEqual((await self.get("/rest/getAlbum", f"{AUTH}&id=yt-AAAAAAAAAAA"))["body"], b"PASSTHROUGH")

    # ------------------------------------------------- cover / song / lyrics
    async def test_cover_art(self):
        res = await self.get("/rest/getCoverArt", f"{AUTH}&id=yt-AAAAAAAAAAA&size=300")
        self.assertEqual((res["status"], res["body"], res["headers"]["content-type"]), (200, b"JPEG", "image/jpeg"))

    async def test_get_song(self):
        res = await self.get("/rest/getSong", f"{AUTH}&f=json&id=yt-AAAAAAAAAAA")
        song = json.loads(res["body"])["subsonic-response"]["song"]
        self.assertEqual((song["id"], song["duration"], song["artist"]), ("yt-AAAAAAAAAAA", 213, "Rick Astley"))

    async def test_lyrics_json_and_xml(self):
        res = await self.get("/rest/getLyricsBySongId", f"{AUTH}&f=json&id=yt-AAAAAAAAAAA")
        sl = json.loads(res["body"])["subsonic-response"]["lyricsList"]["structuredLyrics"][0]
        self.assertEqual([l["start"] for l in sl["line"]], [1000, 2500])
        res = await self.get("/rest/getLyricsBySongId", f"{AUTH}&id=yt-AAAAAAAAAAA")
        line = ET.fromstring(res["body"]).find(f"{NS}lyricsList/{NS}structuredLyrics/{NS}line")
        self.assertEqual((line.get("start"), line.text), ("1000", "Never gonna"))

    # --------------------------------------------------------- star / download
    async def wait_job(self, video_id="AAAAAAAAAAA"):
        job = core.jobs[core.ingest_by_video[video_id]]
        await asyncio.wait_for(core.job_events[job["id"]].wait(), 10)
        return job

    async def test_star_downloads_into_library_and_hides_remote_copy(self):
        res = await self.get("/rest/star", f"{AUTH}&f=json&id=yt-AAAAAAAAAAA")
        self.assertEqual(json.loads(res["body"])["subsonic-response"]["status"], "ok")
        job = await self.wait_job()
        self.assertEqual((job["status"], job["lyrics"]), ("done", "synced"))
        self.assertTrue(os.path.isfile(os.path.join(MUSIC, "Rick Astley", "Never Gonna Give You Up.opus")))
        lrc = os.path.join(MUSIC, "Rick Astley", "Never Gonna Give You Up.lrc")
        with open(lrc, encoding="utf-8") as fh:
            self.assertIn("give you up", fh.read())
        # starring twice does not start a second download, and the remote copy disappears from search
        await self.get("/rest/star", f"{AUTH}&id=yt-AAAAAAAAAAA")
        self.assertEqual(len(core.jobs), 1)
        res = await self.get("/rest/search3", f"{AUTH}&f=json&query=never+gonna")
        self.assertEqual([s["id"] for s in self.songs_json(res)], ["loc1"])

    async def test_star_with_mixed_ids_forwards_only_local(self):
        await self.get("/rest/star", f"{AUTH}&f=json&id=loc7&id=yt-AAAAAAAAAAA")
        forwarded = [c for c in self.client.calls if c.url == "/rest/star"]
        self.assertEqual(len(forwarded), 1)
        self.assertEqual([v for k, v in forwarded[0].params if k == "id"], ["loc7"])
        await self.wait_job()

    async def test_unstar_and_scrobble_remote_are_noops(self):
        for ep in ("unstar", "scrobble"):
            res = await self.get(f"/rest/{ep}", f"{AUTH}&f=json&id=yt-AAAAAAAAAAA")
            self.assertEqual(json.loads(res["body"])["subsonic-response"]["status"], "ok", ep)
        self.assertFalse(core.jobs)
        self.assertFalse([c for c in self.client.calls if c.url in ("/rest/unstar", "/rest/scrobble")])

    async def test_download_endpoint_serves_library_copy(self):
        res = await self.get("/rest/download", f"{AUTH}&id=yt-AAAAAAAAAAA")
        self.assertEqual((res["status"], res["body"]), (200, b"audio-bytes\n"))

    async def test_post_form_params(self):
        res = await asgi(self.app, "POST", "/rest/stream", "", {"Content-Type": "application/x-www-form-urlencoded"},
                         f"{AUTH}&id=yt-AAAAAAAAAAA".encode())
        self.assertEqual((res["status"], res["body"]), (200, AUDIO))

    # --------------------------------------------------- redirects (real bug)
    async def test_stream_follows_redirects_from_youtube(self):
        res = await self.get("/rest/stream", f"{AUTH}&id=yt-AAAAAAAAAAA")
        self.assertEqual(res["status"], 200)
        cdn_calls = [c for c in self.client.calls if c.url == "https://cdn.fake/audio.m4a"]
        self.assertTrue(cdn_calls and all(c.follow_redirects for c in cdn_calls))

    async def test_stream_never_passes_a_redirect_to_the_player(self):
        self.client.cdn_redirect = True  # a client that did not follow would have forwarded this bare 302
        res = await self.get("/rest/stream", f"{AUTH}&id=yt-AAAAAAAAAAA")
        self.assertEqual(res["status"], 502)
        self.assertNotIn("location", res["headers"])

    # --------------------------------------------------------- YouTube Music
    YTM = [
        {"category": "Songs", "resultType": "song", "title": "Never Gonna Give You Up", "videoId": "AAAAAAAAAAA",
         "artists": [{"name": "Rick Astley", "id": "UC1"}], "album": {"name": "Whenever You Need Somebody", "id": "M1"},
         "duration": "3:33", "duration_seconds": 213,
         "thumbnails": [{"url": "https://lh3.fake/cover=w60-h60-l90-rj"}, {"url": "https://lh3.fake/cover=w226-h226-l90-rj"}]},
        {"resultType": "song", "title": "Two hour thing", "videoId": "BBBBBBBBBBB", "artists": [], "duration_seconds": 7200},
        {"resultType": "song", "title": "No id", "artists": [{"name": "X"}], "duration": "1:00"},
        {"resultType": "song", "title": "Only text duration", "videoId": "DDDDDDDDDDD",
         "artists": [{"name": "A"}, {"name": "B"}], "duration": "1:02:03"},
    ]

    async def test_ytmusic_search_gives_real_song_metadata_and_square_cover(self):
        core.SEARCH_SOURCE = "ytmusic"
        core._ytmusic_search_sync = lambda q, limit: self.YTM
        res = await self.get("/rest/search3", f"{AUTH}&f=json&query=never+gonna")
        songs = self.songs_json(res)
        self.assertEqual([s["id"] for s in songs], ["loc1", "yt-AAAAAAAAAAA"])  # long, id-less and 1h02 results dropped
        song = songs[1]
        self.assertEqual((song["artist"], song["album"], song["title"]),
                         ("Rick Astley", "Whenever You Need Somebody", "☁ Never Gonna Give You Up"))
        cover = await self.get("/rest/getCoverArt", f"{AUTH}&id=yt-AAAAAAAAAAA")
        self.assertEqual(cover["body"], b"SQUARE")  # the album art, asked for at 544 px, not the video thumbnail

    async def test_ytmusic_multiple_artists_and_duration_text(self):
        core.SEARCH_SOURCE = "ytmusic"
        core.REMOTE_MAX_SECONDS = 4000
        try:
            core._ytmusic_search_sync = lambda q, limit: self.YTM
            items = await core.yt_search("anything", 8)
        finally:
            core.REMOTE_MAX_SECONDS = 900
        by_id = {i["id"]: i for i in items}
        self.assertEqual(by_id["DDDDDDDDDDD"]["artist"], "A, B")
        self.assertEqual(by_id["DDDDDDDDDDD"]["duration"], 3723)

    async def test_ytmusic_failure_falls_back_to_plain_youtube(self):
        core.SEARCH_SOURCE = "ytmusic"

        def boom(q, limit):
            raise RuntimeError("blocked")

        core._ytmusic_search_sync = boom
        res = await self.get("/rest/search3", f"{AUTH}&f=json&query=never+gonna")
        songs = self.songs_json(res)
        self.assertEqual([s["id"] for s in songs], ["loc1", "yt-AAAAAAAAAAA"])
        self.assertEqual(songs[1]["album"], "YouTube")  # came from the yt-dlp fallback

    async def test_ytmusic_empty_result_falls_back_and_empty_results_are_not_cached(self):
        core.SEARCH_SOURCE = "ytmusic"
        core._ytmusic_search_sync = lambda q, limit: []
        self.assertEqual([i["id"] for i in await core.yt_search("never gonna", 8)], ["AAAAAAAAAAA"])
        core.YTDLP = "/nonexistent/yt-dlp"
        try:
            self.assertEqual(await core.yt_search("other query", 8), [])
        finally:
            core.YTDLP = FAKE_YTDLP
        self.assertNotIn("ytmusic:8:other query", core._search_cache)

    # -------------------------------------------------------------- likes
    async def star(self, vid="yt-AAAAAAAAAAA", extra=""):
        return await self.get("/rest/star", f"{AUTH}&f=json&id={vid}{extra}")

    async def finish_background(self):
        await asyncio.wait_for(asyncio.gather(*remote._background), 10)

    async def test_star_remembers_the_like_across_restarts(self):
        core.FINALIZE_DELAYS = (3600,)  # keep the follow-up from running in this test
        res = await self.star()
        self.assertEqual(json.loads(res["body"])["subsonic-response"]["status"], "ok")
        self.assertIn("AAAAAAAAAAA", core.starred_remote)
        with open(STATE, encoding="utf-8") as fh:
            saved = json.load(fh)
        self.assertEqual(saved["starred"]["AAAAAAAAAAA"]["meta"]["id"], "AAAAAAAAAAA")
        core.starred_remote.clear()
        core._meta.clear()
        core.load_state()  # what the app does when it starts
        self.assertIn("AAAAAAAAAAA", core.starred_remote)
        self.assertEqual(core.get_meta.__name__, "get_meta")
        self.assertEqual(core._meta["AAAAAAAAAAA"]["id"], "AAAAAAAAAAA")

    async def test_liked_cloud_tracks_appear_in_getstarred2_json_and_xml(self):
        core.FINALIZE_DELAYS = (3600,)
        await self.star()
        res = await self.get("/rest/getStarred2", f"{AUTH}&f=json")
        songs = json.loads(res["body"])["subsonic-response"]["starred2"]["song"]
        self.assertEqual([s["id"] for s in songs], ["loc5", "yt-AAAAAAAAAAA"])
        self.assertTrue(songs[1]["starred"].endswith("Z"))
        res = await self.get("/rest/getStarred.view", AUTH)  # XML, the older call
        root = ET.fromstring(res["body"])
        self.assertEqual([s.get("id") for s in root.find(f"{NS}starred")], ["loc5", "yt-AAAAAAAAAAA"])

    async def test_getstarred_is_untouched_when_nothing_is_liked(self):
        res = await self.get("/rest/getStarred2", f"{AUTH}&f=json")
        self.assertEqual(res["body"], b"PASSTHROUGH")

    async def test_liked_flag_shows_in_search_results(self):
        await core.yt_search("never gonna", 8)  # makes the track known, as a search would
        core.star_remote("AAAAAAAAAAA")  # liked, but not downloaded (a finished download hides the cloud copy)
        core._search_cache.clear()
        res = await self.get("/rest/search3", f"{AUTH}&f=json&query=never+gonna")
        remote_song = self.songs_json(res)[-1]
        self.assertEqual(remote_song["id"], "yt-AAAAAAAAAAA")
        self.assertIn("starred", remote_song)

    async def test_unstar_forgets_the_like(self):
        core.FINALIZE_DELAYS = (3600,)
        await self.star()
        await self.get("/rest/unstar", f"{AUTH}&f=json&id=yt-AAAAAAAAAAA")
        self.assertNotIn("AAAAAAAAAAA", core.starred_remote)
        with open(STATE, encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["starred"], {})

    async def test_finalize_hearts_the_library_copy_and_drops_the_cloud_entry(self):
        self.client.library_after_scan = [{"id": "lib42", "title": "Never Gonna Give You Up", "artist": "Rick Astley"}]
        await self.star()
        await self.wait_job()
        await self.finish_background()
        self.assertEqual(core.starred_remote, {})  # the like now lives on the real library song
        calls = {c.url: c for c in self.client.calls}
        self.assertIn("/rest/startScan", calls)
        star_calls = [c for c in self.client.calls if c.url == "/rest/star"]
        self.assertEqual(len(star_calls), 1)
        sent = dict(star_calls[0].params)
        self.assertEqual((sent["id"], sent["u"], sent["t"]), ("lib42", "admin", "good"))  # the person's own login

    async def test_finalize_gives_up_but_the_like_stays(self):
        await self.star()  # nothing ever shows up in the library
        await self.wait_job()
        await self.finish_background()
        self.assertIn("AAAAAAAAAAA", core.starred_remote)
        self.assertFalse([c for c in self.client.calls if c.url == "/rest/star"])

    async def test_finalize_stops_when_unstarred_meanwhile(self):
        self.client.library_after_scan = [{"id": "lib42", "title": "Never Gonna Give You Up", "artist": "Rick Astley"}]
        core.FINALIZE_DELAYS = (0.3,)
        await self.star()
        await self.wait_job()
        await self.get("/rest/unstar", f"{AUTH}&f=json&id=yt-AAAAAAAAAAA")
        await self.finish_background()
        self.assertFalse([c for c in self.client.calls if c.url == "/rest/star"])

    async def test_star_of_unknown_track_is_an_error_and_not_remembered(self):
        res = await self.star("yt-ZZZZZZZZZZZ")
        self.assertEqual(json.loads(res["body"])["subsonic-response"]["error"]["code"], 70)
        self.assertEqual(core.starred_remote, {})
        self.assertFalse(core.jobs)

    async def test_disabled_flag(self):
        core.REMOTE_ENABLED = False
        try:
            res = await self.get("/rest/search3", f"{AUTH}&f=json&query=never+gonna")
        finally:
            core.REMOTE_ENABLED = True
        self.assertEqual(res["body"], b"PASSTHROUGH")


class IngestCommand(unittest.IsolatedAsyncioTestCase):
    """The yt-dlp command line built for downloads (audio only, embedded cover, safe fallback)."""

    async def run_ingest(self, results):
        calls = []

        async def fake_run(*args, timeout=None):
            calls.append(list(args))
            return results[len(calls) - 1]

        async def no_lyrics(*args):
            return None

        real, real_lrc = core.run, core.fetch_lrc
        core.run, core.fetch_lrc = fake_run, no_lyrics
        try:
            job = core.start_ingest("https://www.youtube.com/watch?v=QQQQQQQQQQQ")
            await asyncio.wait_for(core.job_events[job["id"]].wait(), 10)
        finally:
            core.run, core.fetch_lrc = real, real_lrc
            core.ingest_by_video.clear()
        return calls, core.jobs[job["id"]]

    async def test_audio_only_with_embedded_cover(self):
        ok = (0, "A\tT\t10\t" + os.path.join(MUSIC, "A", "T.opus") + "\n", "")
        calls, job = await self.run_ingest([ok])
        self.assertEqual(len(calls), 1)
        args = calls[0]
        self.assertEqual(args[args.index("-f") + 1], "bestaudio[ext=m4a]/bestaudio/best")
        self.assertIn("--embed-thumbnail", args)
        self.assertEqual(job["status"], "done")

    async def test_falls_back_without_cover_when_embedding_fails(self):
        ok = (0, "A\tT\t10\t" + os.path.join(MUSIC, "A", "T2.opus") + "\n", "")
        calls, job = await self.run_ingest([(1, "", "ERROR: Postprocessing: mutagen missing"), ok])
        self.assertEqual(len(calls), 2)
        self.assertIn("--embed-thumbnail", calls[0])
        self.assertNotIn("--embed-thumbnail", calls[1])
        self.assertEqual(job["status"], "done")

    async def test_real_failure_is_reported(self):
        calls, job = await self.run_ingest([(1, "", "ERROR: boom"), (1, "", "ERROR: boom")])
        self.assertEqual((job["status"], job["error"]), ("error", "ERROR: boom"))


if __name__ == "__main__":
    unittest.main()
