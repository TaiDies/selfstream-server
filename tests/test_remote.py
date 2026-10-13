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

os.environ.update(MUSIC_DIR=MUSIC, PROXY_LOG="0", YTDLP_BIN=FAKE_YTDLP, YTDLP_EXTRA_ARGS="", REMOTE_WAIT_SECONDS="5")
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
        self.cdn_fail_first = 0
        self.search_status = "ok"

    def build_request(self, method, url, params=None, headers=None, content=None):
        return SimpleNamespace(method=method, url=url, params=list(params or []), headers=dict(headers or {}))

    async def send(self, req, stream=False):
        self.calls.append(req)
        p = dict(req.params)
        if req.url == "/rest/ping":
            ok = p.get("u") == "admin" and p.get("t") == "good"
            body = {"subsonic-response": {"status": "ok" if ok else "failed", "version": "1.16.1"}}
            return FakeResp(200, json.dumps(body).encode())
        if req.url.startswith("/rest/search3"):
            if p.get("f") == "json":
                body = {"subsonic-response": {"status": self.search_status, "version": "1.16.1",
                                              "searchResult3": {"song": self.local_songs}}}
                return FakeResp(200, json.dumps(body).encode(), [("Content-Type", "application/json")])
            songs = "".join(f'<song id="{s["id"]}" title="{s["title"]}" artist="{s["artist"]}"/>' for s in self.local_songs)
            xml = (f'<?xml version="1.0"?><subsonic-response xmlns="http://subsonic.org/restapi" '
                   f'status="{self.search_status}" version="1.16.1"><searchResult3>{songs}</searchResult3></subsonic-response>')
            return FakeResp(200, xml.encode(), [("Content-Type", "text/xml")])
        if req.url == "/rest/star":
            return FakeResp(200, b'{"subsonic-response":{"status":"ok"}}', [("Content-Type", "application/json")])
        if req.url == "https://cdn.fake/audio.m4a":
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
                      core.job_events, core.ingest_by_video, remote._auth_ok, remote._cover_cache):
            cache.clear()
        self.client = FakeClient()

        async def fake_lrc(artist, title, duration):
            return "[00:01.00]Never gonna\n[00:02.50]give you up" if title == "Never Gonna Give You Up" else None

        core.fetch_lrc = fake_lrc

        async def endpoint(request):
            res = await remote.handle(request, request.path_params["path"], self.client)
            return res if res is not None else Response("PASSTHROUGH")

        self.app = Starlette(routes=[Route("/{path:path}", endpoint, methods=["GET", "POST", "HEAD"])])

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

    async def test_disabled_flag(self):
        core.REMOTE_ENABLED = False
        try:
            res = await self.get("/rest/search3", f"{AUTH}&f=json&query=never+gonna")
        finally:
            core.REMOTE_ENABLED = True
        self.assertEqual(res["body"], b"PASSTHROUGH")


if __name__ == "__main__":
    unittest.main()
