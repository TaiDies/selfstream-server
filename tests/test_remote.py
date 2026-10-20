"""Handler tests: a fake Navidrome, a fake YouTube CDN and a fake yt-dlp script, no network."""
import asyncio
import json
import os
import shutil
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
os.environ.update(MUSIC_DIR=MUSIC, PROXY_LOG="0", YTDLP_BIN=FAKE_YTDLP, YTDLP_EXTRA_ARGS="", REMOTE_WAIT_SECONDS="5", REMOTE_TITLE_PREFIX="☁ ",
                  SEARCH_SOURCE="youtube", STATE_FILE=STATE)
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

from starlette.applications import Starlette  # noqa: E402
from starlette.responses import Response  # noqa: E402
from starlette.routing import Route  # noqa: E402

import core  # noqa: E402
import subsonic  # noqa: E402
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
        self.local_artists = []
        self.local_albums = []

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
                                              "searchResult3": {"song": local, "artist": self.local_artists,
                                                                "album": self.local_albums}}}
                return FakeResp(200, json.dumps(body).encode(), [("Content-Type", "application/json")])
            songs = "".join(f'<song id="{s["id"]}" title="{s["title"]}" artist="{s["artist"]}"/>' for s in local)
            xml = (f'<?xml version="1.0"?><subsonic-response xmlns="http://subsonic.org/restapi" '
                   f'status="{self.search_status}" version="1.16.1"><searchResult3>{songs}</searchResult3></subsonic-response>')
            return FakeResp(200, xml.encode(), [("Content-Type", "text/xml")])
        if req.url in ("/rest/getArtist", "/rest/getAlbum") and p.get("id") in ("lartist1", "lalb1"):
            if req.url.endswith("Artist"):
                key, ent = "artist", {"id": "lartist1", "name": "Rick Astley",
                                      "album": [{"id": "lalb1", "name": "Whenever You Need Somebody", "artist": "Rick Astley"}]}
            else:
                key, ent = "album", {"id": "lalb1", "name": "Whenever You Need Somebody", "artist": "Rick Astley",
                                     "song": [{"id": "ls1", "title": "Never Gonna Give You Up", "artist": "Rick Astley"}]}
            if p.get("f") == "json":
                body = {"subsonic-response": {"status": "ok", "version": "1.16.1", key: ent}}
                return FakeResp(200, json.dumps(body).encode(), [("Content-Type", "application/json")])
            kids = ent.get("album") or ent.get("song")
            tag = "album" if key == "artist" else "song"
            inner = "".join(f'<{tag} id="{k["id"]}" ' + " ".join(f'{a}="{v}"' for a, v in k.items() if a != "id") + "/>" for k in kids)
            xml = (f'<?xml version="1.0"?><subsonic-response xmlns="http://subsonic.org/restapi" status="ok" '
                   f'version="1.16.1"><{key} id="{ent["id"]}" name="{ent["name"]}" artist="Rick Astley">{inner}</{key}></subsonic-response>')
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


class FakeYTM:
    """Stands in for ytmusicapi.YTMusic, with the shapes its results really have."""

    ARTIST = "UCartistRick00000000000"

    def __init__(self):
        self.calls, self.fail = [], False

    def _thumbs(self, tag):
        return [{"url": f"https://lh3.fake/{tag}=w60-h60-l90-rj", "width": 60},
                {"url": f"https://lh3.fake/{tag}=w226-h226-l90-rj", "width": 226}]

    def search(self, query, filter=None, limit=None, ignore_spelling=False):
        self.calls.append((filter, query))
        if self.fail:
            raise RuntimeError("blocked")
        if filter == "artists":
            return [{"category": "Artists", "resultType": "artist", "artist": "Rick Astley",
                     "browseId": self.ARTIST, "thumbnails": self._thumbs("artist")}]
        if filter == "albums":
            return [{"resultType": "album", "title": "Whenever You Need Somebody", "type": "Album", "year": "1987",
                     "artists": [{"name": "Rick Astley", "id": self.ARTIST}], "browseId": "MPREb_whenever1",
                     "thumbnails": self._thumbs("whenever")}]
        return []

    def get_artist(self, channel_id):
        self.calls.append(("get_artist", channel_id))
        if self.fail:
            raise RuntimeError("blocked")
        return {"name": "Rick Astley", "channelId": channel_id, "thumbnails": self._thumbs("artist"),
                "albums": {"browseId": "MPADUCartist", "params": "pp", "results": [
                    {"title": "Whenever You Need Somebody", "year": "1987", "browseId": "MPREb_whenever1", "thumbnails": self._thumbs("whenever")},
                    {"title": "Hold Me in Your Arms", "year": "1988", "browseId": "MPREb_holdme001", "thumbnails": self._thumbs("hold")}]},
                "singles": {"results": [
                    {"title": "Together Forever", "year": "1988", "browseId": "MPREb_together1", "thumbnails": self._thumbs("tog")}]}}

    def get_artist_albums(self, channel_id, params, limit=100):
        self.calls.append(("get_artist_albums", channel_id, params))
        return [{"title": "DAMN.", "year": "2017", "browseId": "MPREb_damn00001", "thumbnails": self._thumbs("damn")},
                {"title": "Whenever You Need Somebody", "year": "1987", "browseId": "MPREb_whenever1", "thumbnails": self._thumbs("whenever")}]

    def get_album(self, browse_id):
        self.calls.append(("get_album", browse_id))
        if self.fail:
            raise RuntimeError("blocked")
        rick = [{"name": "Rick Astley", "id": self.ARTIST}]
        return {"title": "Whenever You Need Somebody", "year": "1987", "artists": rick, "thumbnails": self._thumbs("whenever"),
                "tracks": [
                    {"videoId": "AAAAAAAAAAA", "title": "Never Gonna Give You Up", "artists": rick, "duration_seconds": 213, "isAvailable": True},
                    {"videoId": "DDDDDDDDDDD", "title": "Whenever You Need Somebody", "artists": rick, "duration": "3:20", "isAvailable": True},
                    {"videoId": None, "title": "Not playable", "artists": rick, "duration_seconds": 100, "isAvailable": False},
                    {"videoId": "EEEEEEEEEEE", "title": "Blocked", "artists": rick, "duration_seconds": 100, "isAvailable": False}]}


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
        for f in core.COVER_DIR.glob('*.jpg'):
            f.unlink()
        if os.path.exists(STATE):
            os.remove(STATE)
        core.SEARCH_SOURCE = "youtube"
        core.FINALIZE_DELAYS = (0.01, 0.01, 0.01)
        core._browse_cache.clear()
        core._art_urls.clear()
        core.downloaded.clear()
        self.ytm = core._ytm = FakeYTM()
        core.REMOTE_BROWSE = True
        self.client = FakeClient()

        async def fake_lrc(artist, title, duration):
            return "[00:01.00]Never gonna\n[00:02.50]give you up" if title == "Never Gonna Give You Up" else None

        core.fetch_lrc = fake_lrc

        async def no_unison(video_id, artist, title, duration):
            return None

        core.fetch_unison = no_unison

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
        ids = [s.get("id") for s in root.find(f"{NS}searchResult3").findall(f"{NS}song")]
        self.assertEqual(ids, ["loc1", "yt-AAAAAAAAAAA"])
        self.assertIn("xml", res["headers"]["content-type"])

    async def test_search_skips_library_sync_and_pagination(self):
        for query in ('query=', 'query=%22%22', 'query=ab', 'query=never+gonna&songOffset=20', 'query=never+gonna&songCount=0&artistCount=0&albumCount=0'):
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
        for ep in ("getArtistInfo2", "getArtistInfo", "getTopSongs"):
            self.assertEqual((await self.get(f"/rest/{ep}", f"{AUTH}&id=12345"))["body"], b"PASSTHROUGH", ep)

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

    async def test_word_lyrics_from_unison_win_over_line_lyrics(self):
        ttml = ('<tt xmlns="http://www.w3.org/ns/ttml"><body><div>'
                '<p begin="1.000" end="3.000"><span begin="1.000" end="1.500">Never</span> <span begin="1.500" end="2.000">gon</span>'
                '<span begin="2.000" end="3.000">na</span></p></div></body></tt>')

        async def unison(video_id, artist, title, duration):
            return {"lines": subsonic.parse_ttml(ttml), "word": True}

        core.fetch_unison = unison
        res = await self.get("/rest/getLyricsBySongId", f"{AUTH}&f=json&id=yt-AAAAAAAAAAA")
        sl = json.loads(res["body"])["subsonic-response"]["lyricsList"]["structuredLyrics"][0]
        self.assertEqual(sl["source"], core.UNISON_CREDIT)
        line = sl["line"][0]
        self.assertEqual((line["start"], line["value"]), (1000, "Never gonna"))
        self.assertEqual([(w["start"], w["end"], w["value"]) for w in line["words"]],
                         [(1000, 1500, "Never "), (1500, 2000, "gon"), (2000, 3000, "na")])
        res = await self.get("/rest/getLyricsBySongId", f"{AUTH}&id=yt-AAAAAAAAAAA")  # the XML form must still be valid
        line = ET.fromstring(res["body"]).find(f"{NS}lyricsList/{NS}structuredLyrics/{NS}line")
        self.assertEqual(len(line.findall(f"{NS}words")), 3)

        async def line_only(video_id, artist, title, duration):
            return {"lines": [{"start": 5, "value": "x"}], "word": False}

        core.fetch_unison = line_only  # a line-only result loses to LRCLIB
        res = await self.get("/rest/getLyricsBySongId", f"{AUTH}&f=json&id=yt-AAAAAAAAAAA")
        sl = json.loads(res["body"])["subsonic-response"]["lyricsList"]["structuredLyrics"][0]
        self.assertEqual(sl["line"][0]["value"], "Never gonna")
        self.assertNotIn("source", sl)

    def test_parse_ttml_edge_cases(self):
        self.assertEqual(subsonic.parse_ttml('<!DOCTYPE x [<!ENTITY a "b">]><tt/>'), [])
        self.assertEqual(subsonic.parse_ttml("not xml"), [])
        lines = subsonic.parse_ttml('<tt xmlns="http://www.w3.org/ns/ttml" xmlns:ttm="http://www.w3.org/ns/ttml#metadata"><body>'
                                    '<p begin="1:24.500"><span begin="1:24.500" end="1:25.000">Hi</span>'
                                    '<span ttm:role="x-translation" begin="1:24.500" end="1:25.000">Hola</span></p>'
                                    '<p begin="90.0s">No words here</p></body></tt>')
        self.assertEqual([(l["start"], l["value"], "words" in l) for l in lines], [(84500, "Hi", True), (90000, "No words here", False)])

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

    async def test_star_without_download_only_remembers_the_like(self):
        res = await self.get("/rest/star", f"{AUTH}&f=json&id=yt-AAAAAAAAAAA&ssDownload=0")
        self.assertEqual(json.loads(res["body"])["subsonic-response"]["status"], "ok")
        self.assertIn("AAAAAAAAAAA", core.starred_remote)
        self.assertEqual(len(core.jobs), 0)
        res = await self.get("/rest/getStarred2", f"{AUTH}&f=json")
        self.assertIn("yt-AAAAAAAAAAA", [s["id"] for s in json.loads(res["body"])["subsonic-response"]["starred2"]["song"]])

    async def test_star_never_downloads_when_the_server_does_not_allow_it(self):
        old = core.ALLOW_LIKE_DOWNLOAD
        core.ALLOW_LIKE_DOWNLOAD = False
        try:
            res = await self.get("/rest/star", f"{AUTH}&f=json&id=yt-AAAAAAAAAAA&ssDownload=1")
            self.assertEqual(json.loads(res["body"])["subsonic-response"]["status"], "ok")
            self.assertIn("AAAAAAAAAAA", core.starred_remote)
            self.assertEqual(len(core.jobs), 0)
        finally:
            core.ALLOW_LIKE_DOWNLOAD = old

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
        core._meta.clear()  # as after a restart: the same picture still comes back, not the video thumbnail
        again = await self.get("/rest/getCoverArt", f"{AUTH}&id=yt-AAAAAAAAAAA")
        self.assertEqual(again["body"], b"SQUARE")

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


    # ------------------------------------------------- artists and albums
    async def search_json(self, extra=""):
        res = await self.get("/rest/search3", f"{AUTH}&f=json&query=rick+astley{extra}")
        return json.loads(res["body"])["subsonic-response"]["searchResult3"]

    async def test_search_adds_remote_artists_and_albums(self):
        sr = await self.search_json()
        self.assertEqual([(a["id"], a["name"]) for a in sr["artist"]], [("ya-UCartistRick00000000000", "☁ Rick Astley")])
        album = sr["album"][0]
        self.assertEqual((album["id"], album["name"], album["artist"], album["year"]),
                         ("yb-MPREb_whenever1", "☁ Whenever You Need Somebody", "Rick Astley", 1987))
        self.assertEqual(album["artistId"], "ya-UCartistRick00000000000")

    async def test_search_artist_and_album_dedupe_against_library(self):
        self.client.local_artists = [{"id": "lartist1", "name": "Rick Astley"}]
        self.client.local_albums = [{"id": "lalb1", "name": "Whenever You Need Somebody", "artist": "Rick Astley"}]
        sr = await self.search_json()
        self.assertEqual([a["id"] for a in sr["artist"]], ["lartist1"])
        self.assertEqual([a["id"] for a in sr["album"]], ["lalb1"])

    async def test_search_skips_artists_and_albums_when_not_asked_for(self):
        sr = await self.search_json("&artistCount=0&albumCount=0")
        self.assertEqual((sr["artist"], sr["album"]), ([], []))
        self.assertFalse([c for c in self.ytm.calls if c[0] in ("artists", "albums")])

    async def test_search_artists_and_albums_xml(self):
        res = await self.get("/rest/search3", f"{AUTH}&query=rick+astley")
        sr = ET.fromstring(res["body"]).find(f"{NS}searchResult3")
        self.assertEqual([a.get("id") for a in sr.findall(f"{NS}artist")], ["ya-UCartistRick00000000000"])
        self.assertEqual([a.get("id") for a in sr.findall(f"{NS}album")], ["yb-MPREb_whenever1"])

    async def test_remote_artist_page(self):
        res = await self.get("/rest/getArtist", f"{AUTH}&f=json&id=ya-UCartistRick00000000000")
        artist = json.loads(res["body"])["subsonic-response"]["artist"]
        self.assertEqual((artist["name"], artist["albumCount"]), ("☁ Rick Astley", 4))
        self.assertEqual([a["id"] for a in artist["album"]], ["yb-MPREb_damn00001", "yb-MPREb_holdme001", "yb-MPREb_together1", "yb-MPREb_whenever1"])  # complete list, newest first

    async def test_remote_album_page_lists_playable_tracks(self):
        res = await self.get("/rest/getAlbum", f"{AUTH}&f=json&id=yb-MPREb_whenever1")
        album = json.loads(res["body"])["subsonic-response"]["album"]
        self.assertEqual((album["name"], album["songCount"], album["duration"]), ("☁ Whenever You Need Somebody", 2, 413))
        self.assertEqual([(s["id"], s["track"], s["albumId"]) for s in album["song"]],
                         [("yt-AAAAAAAAAAA", 1, "yb-MPREb_whenever1"), ("yt-DDDDDDDDDDD", 2, "yb-MPREb_whenever1")])
        self.assertEqual(album["song"][1]["album"], "Whenever You Need Somebody")
        self.assertEqual(album["song"][1]["duration"], 200)
        # album tracks are known to the server, so playing or hearting them needs no further lookup
        self.assertEqual(core._meta["DDDDDDDDDDD"]["album"], "Whenever You Need Somebody")
        res = await self.get("/rest/getSong", f"{AUTH}&f=json&id=yt-DDDDDDDDDDD")
        self.assertEqual(json.loads(res["body"])["subsonic-response"]["song"]["title"], "☁ Whenever You Need Somebody")

    async def test_remote_pages_need_a_valid_login(self):
        for pid in ("ya-UCartistRick00000000000", "yb-MPREb_whenever1"):
            res = await self.get("/rest/getArtist" if pid.startswith("ya") else "/rest/getAlbum", f"u=bad&t=bad&f=json&id={pid}")
            self.assertEqual(json.loads(res["body"])["subsonic-response"]["error"]["code"], 40)
        self.assertFalse([c for c in self.ytm.calls if c[0] in ("get_artist", "get_album")])

    async def test_remote_page_errors_when_youtube_music_fails(self):
        self.ytm.fail = True
        res = await self.get("/rest/getAlbum", f"{AUTH}&f=json&id=yb-MPREb_whenever1")
        self.assertEqual(json.loads(res["body"])["subsonic-response"]["error"]["code"], 70)

    async def test_library_artist_page_gets_missing_albums(self):
        res = await self.get("/rest/getArtist", f"{AUTH}&f=json&id=lartist1")
        artist = json.loads(res["body"])["subsonic-response"]["artist"]
        self.assertEqual([a["id"] for a in artist["album"]], ["lalb1", "yb-MPREb_damn00001", "yb-MPREb_holdme001", "yb-MPREb_together1"])
        self.assertEqual(artist["album"][2]["name"], "☁ Hold Me in Your Arms")

    async def test_library_artist_page_xml(self):
        res = await self.get("/rest/getArtist", f"{AUTH}&id=lartist1")
        ids = [a.get("id") for a in ET.fromstring(res["body"]).find(f"{NS}artist").findall(f"{NS}album")]
        self.assertEqual(ids, ["lalb1", "yb-MPREb_damn00001", "yb-MPREb_holdme001", "yb-MPREb_together1"])

    async def test_library_album_page_gets_missing_tracks(self):
        res = await self.get("/rest/getAlbum", f"{AUTH}&f=json&id=lalb1")
        album = json.loads(res["body"])["subsonic-response"]["album"]
        self.assertEqual([s["id"] for s in album["song"]], ["ls1", "yt-DDDDDDDDDDD"])  # the track you have is not repeated
        res = await self.get("/rest/getAlbum", f"{AUTH}&id=lalb1")
        songs = ET.fromstring(res["body"]).find(f"{NS}album").findall(f"{NS}song")
        self.assertEqual([s.get("id") for s in songs], ["ls1", "yt-DDDDDDDDDDD"])

    async def test_library_pages_unchanged_when_youtube_music_fails(self):
        self.ytm.fail = True
        res = await self.get("/rest/getArtist", f"{AUTH}&f=json&id=lartist1")
        self.assertEqual([a["id"] for a in json.loads(res["body"])["subsonic-response"]["artist"]["album"]], ["lalb1"])
        res = await self.get("/rest/getAlbum", f"{AUTH}&f=json&id=lalb1")
        self.assertEqual([s["id"] for s in json.loads(res["body"])["subsonic-response"]["album"]["song"]], ["ls1"])

    async def test_browse_can_be_turned_off(self):
        core.REMOTE_BROWSE = False
        for ep in ("getArtist", "getAlbum", "getArtistInfo2"):
            self.assertEqual((await self.get(f"/rest/{ep}", f"{AUTH}&id=lartist1"))["body"], b"PASSTHROUGH", ep)
        sr = await self.search_json()
        self.assertEqual((sr["artist"], sr["album"]), ([], []))

    async def test_artist_info_for_remote_artist_is_empty_but_valid(self):
        res = await self.get("/rest/getArtistInfo2", f"{AUTH}&f=json&id=ya-UCartistRick00000000000")
        body = json.loads(res["body"])["subsonic-response"]
        self.assertEqual((body["status"], body["artistInfo2"]), ("ok", {}))

    async def test_remote_artist_and_album_pictures(self):
        for pid in ("yb-MPREb_whenever1", "ya-UCartistRick00000000000"):
            core._art_urls.clear()  # as after a restart: the picture is looked up again
            res = await self.get("/rest/getCoverArt", f"{AUTH}&id={pid}&size=300")
            self.assertEqual((res["status"], res["body"]), (200, b"SQUARE"), pid)
        res = await self.get("/rest/getCoverArt", "u=bad&t=bad&id=yb-MPREb_whenever1")
        self.assertEqual(res["status"], 401)


    # ------------------------------------------------- the same song twice
    async def test_library_copy_with_remaster_suffix_hides_the_cloud_copy(self):
        self.client.local_songs = [{"id": "loc9", "title": "Never Gonna Give You Up (Remastered 2015)", "artist": "Rick Astley"}]
        ids = [s["id"] for s in self.songs_json(await self.get("/rest/search3", f"{AUTH}&f=json&query=never+gonna"))]
        self.assertEqual(ids, ["loc9"])

    async def test_live_version_is_not_mistaken_for_the_library_song(self):
        self.client.local_songs = [{"id": "loc9", "title": "Never Gonna Give You Up (Live)", "artist": "Rick Astley"}]
        ids = [s["id"] for s in self.songs_json(await self.get("/rest/search3", f"{AUTH}&f=json&query=never+gonna"))]
        self.assertEqual(ids, ["loc9", "yt-AAAAAAAAAAA"])

    async def test_downloaded_song_stays_hidden_after_a_restart_even_if_tags_differ(self):
        await self.get("/rest/star", f"{AUTH}&f=json&id=yt-AAAAAAAAAAA")
        job = await self.wait_job()
        await self.finish_background()
        self.assertEqual(core.downloaded["AAAAAAAAAAA"], job["file"])
        # "restart": forget everything in memory, reload what was saved
        core.jobs.clear()
        core.ingest_by_video.clear()
        core.downloaded.clear()
        core.starred_remote.clear()
        core.load_state()
        self.client.local_songs = [{"id": "loc9", "title": "Totally different tag", "artist": "Somebody"}]
        ids = [s["id"] for s in self.songs_json(await self.get("/rest/search3", f"{AUTH}&f=json&query=never+gonna"))]
        self.assertEqual(ids, ["loc9"])
        # deleting the file brings the cloud copy back
        os.remove(job["file"])
        ids = [s["id"] for s in self.songs_json(await self.get("/rest/search3", f"{AUTH}&f=json&query=never+gonna"))]
        self.assertEqual(ids, ["loc9", "yt-AAAAAAAAAAA"])


class IngestCommand(unittest.IsolatedAsyncioTestCase):
    """The yt-dlp command line built for downloads (audio only, embedded cover, safe fallback)."""

    def setUp(self):
        core._meta.clear()
        core.ingest_by_video.clear()
        core.downloaded.clear()

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

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "needs ffmpeg")
    def test_cover_crop_argument_really_makes_a_square(self):
        """yt-dlp splits the --ppa value like a shell and hands it to ffmpeg; run exactly that."""
        import shlex
        import subprocess
        ppa = core.THUMB_ARGS[core.THUMB_ARGS.index("--ppa") + 1]
        args = shlex.split(ppa.split(":", 1)[1])
        src, dst = os.path.join(TMP, "thumb-in.jpg"), os.path.join(TMP, "thumb-out.jpg")
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=640x360", "-frames:v", "1", src], check=True)
        done = subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", src, *args, dst], capture_output=True, text=True)
        self.assertEqual(done.returncode, 0, done.stderr)
        size = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=width,height", "-of", "csv=p=0", dst],
                              capture_output=True, text=True).stdout.strip()
        self.assertEqual(size, "360,360")

    # ---- the download uses the names and cover that search showed
    META = {"id": "QQQQQQQQQQQ", "title": "Nirvana - Dumb (Official Music Video)", "artist": "Nirvana", "track": "Dumb",
            "album": "In Utero", "channel": "Nirvana", "duration": 172, "cover": "https://lh3.fake/a=w226-h226-l90-rj"}

    async def run_with_meta(self, meta, results, fetch=b"x" * 1000):
        core._meta.clear()
        if meta:
            core._meta[meta["id"]] = dict(meta)
        fetched = []

        async def fake_fetch(url):
            fetched.append(url)
            return fetch

        real = core._fetch_image
        core._fetch_image = fake_fetch
        try:
            calls, job = await self.run_ingest(results)
        finally:
            core._fetch_image = real
        return calls, job, fetched

    def ok_result(self, name="D.opus"):
        return (0, "Nirvana\tDumb\t172\t" + os.path.join(MUSIC, "Nirvana", name) + "\n", "")

    async def test_download_uses_search_names_and_album_art(self):
        calls, job, fetched = await self.run_with_meta(self.META, [self.ok_result(), (0, "", "")])
        ytdlp, embed = calls
        self.assertIn("~Dumb:~%(track)s", ytdlp)
        self.assertIn("~Nirvana:~%(artist)s", ytdlp)
        self.assertIn("~In Utero:~%(album)s", ytdlp)
        self.assertNotIn("--embed-thumbnail", ytdlp)  # not the video frame
        self.assertEqual(fetched, ["https://lh3.fake/a=w544-h544-l90-rj"])  # the same square art search shows
        self.assertEqual(embed[1:2], [str(core.EMBED_SCRIPT)])
        self.assertEqual(embed[2], os.path.join(MUSIC, "Nirvana", "D.opus"))
        self.assertEqual(job["status"], "done")

    async def test_cover_failure_never_fails_the_download(self):
        for results, fetch in (([self.ok_result("D2.opus"), (1, "", "mutagen missing")], b"x" * 1000),
                               ([self.ok_result("D3.opus")], None)):
            calls, job, _ = await self.run_with_meta(self.META, results, fetch)
            self.assertEqual(job["status"], "done")
            core.ingest_by_video.clear()

    async def test_unknown_track_still_uses_the_video_thumbnail(self):
        calls, job, fetched = await self.run_with_meta(None, [self.ok_result("D4.opus")])
        self.assertEqual(len(calls), 1)
        self.assertIn("--embed-thumbnail", calls[0])
        self.assertFalse([a for a in calls[0] if a.startswith("~")])
        self.assertEqual(fetched, [])

    def test_tag_overrides_escape_awkward_text(self):
        args = core.ytdlp_tag_overrides({"id": "x", "title": "t", "artist": "A: B", "track": "100% (Live) | x"})
        self.assertEqual(args[1::2], ["~A\\: B:~%(artist)s", "~100%% (Live) | x:~%(track)s"])
        self.assertEqual(core.ytdlp_tag_overrides(None), [])
        self.assertEqual(core.ytdlp_tag_overrides({"id": "x"}), [])

    @unittest.skipUnless(shutil.which("yt-dlp"), "needs the real yt-dlp on PATH")
    def test_real_ytdlp_applies_the_overrides(self):
        import subprocess
        info = os.path.join(TMP, "info.json")
        with open(info, "w") as fh:
            json.dump({"id": "abcdefghijk", "title": "Nirvana - Dumb (Official Music Video)", "uploader": "NirvanaVEVO",
                       "ext": "m4a", "extractor": "youtube", "extractor_key": "Youtube", "duration": 172, "_type": "video",
                       "webpage_url": "https://www.youtube.com/watch?v=abcdefghijk",
                       "formats": [{"format_id": "140", "url": "http://x/y", "ext": "m4a", "protocol": "https"}]}, fh)
        for track, artist in (("Dumb", "Nirvana"), ("Smells: 100% (Live) | \"x\"", "A, B"), ("Back\\slash", "~tilde")):
            args = core.ytdlp_tag_overrides({"id": "x", "title": "t", "artist": artist, "track": track, "album": "In Utero"})
            out = subprocess.run(["yt-dlp", "--ignore-config", "--load-info-json", info, "--simulate", "--no-warnings", *args,
                                  "--print", "%(artist)s|%(track)s|%(album)s"], capture_output=True, text=True).stdout.strip()
            self.assertEqual(out, f"{artist}|{track}|In Utero")

    async def test_audio_only_with_embedded_cover(self):
        ok = (0, "A\tT\t10\t" + os.path.join(MUSIC, "A", "T.opus") + "\n", "")
        calls, job = await self.run_ingest([ok])
        self.assertEqual(len(calls), 1)
        args = calls[0]
        self.assertEqual(args[args.index("-f") + 1], "bestaudio[ext=m4a]/bestaudio/best")
        self.assertIn("--embed-thumbnail", args)
        self.assertIn("%(track,title)s:%(meta_title)s", args)  # tags carry the song title, not the video title
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
