"""The app API (/selfstream/api/v1): HTTP-level tests with the same fakes as test_remote."""
import json
import os
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(__file__))
from test_remote import AUTH, FakeClient, FakeYTM, TMP, asgi  # noqa: E402  (also sets the test environment)

from starlette.applications import Starlette  # noqa: E402
from starlette.responses import Response  # noqa: E402
from starlette.routing import Route  # noqa: E402

import api  # noqa: E402
import core  # noqa: E402
import discover  # noqa: E402
import follows  # noqa: E402
import screens  # noqa: E402
import remote  # noqa: E402
import spotify  # noqa: E402
import store  # noqa: E402
import subsonic  # noqa: E402

P = "/selfstream/api/v1/"


class YTM(FakeYTM):
    def get_playlist(self, pid, limit=100):
        self.calls.append(("get_playlist", pid))
        return {"title": "Road trip", "author": {"name": "Someone"}, "thumbnails": self._thumbs("pl"),
                "tracks": [{"videoId": "FFFFFFFFFFF", "title": "Song F", "artists": [{"name": "Band"}], "duration_seconds": 100},
                           {"videoId": "GGGGGGGGGGG", "title": "Song G", "artists": [{"name": "Band"}], "duration": "2:00"}]}

    def get_search_suggestions(self, q):
        return [q + " live", q + " lyrics"]

    def get_watch_playlist(self, videoId=None, radio=False, limit=25):
        self.calls.append(("radio", videoId))
        t = lambda v, title, artist: {"videoId": v, "title": title, "artists": [{"name": artist}], "length": "3:00",
                                      "thumbnail": self._thumbs(v)}
        return {"tracks": [t(videoId, "same", "same"), t("HHHHHHHHHHH", "New One", "Other Band"), t("IIIIIIIIIII", "Known", "Rick Astley"),
                           t("JJJJJJJJJJJ", "New Two", "Other Band"), t("KKKKKKKKKKK", "New Three", "Other Band")]}

    def search(self, query, filter=None, limit=None, ignore_spelling=False):
        if filter == "playlists":
            return [{"browseId": "VLPLroad00000001", "title": "Road trip", "author": "Someone", "itemCount": "2", "thumbnails": self._thumbs("pl")}]
        if filter == "songs" and "Blocked" in query:
            return [{"videoId": "LLLLLLLLLLL", "title": "Blocked", "artists": [{"name": "Rick Astley"}], "duration_seconds": 150, "thumbnails": self._thumbs("b")}]
        if filter == "songs":
            return [{"videoId": "AAAAAAAAAAA", "title": "Never Gonna Give You Up", "artists": [{"name": "Rick Astley"}],
                     "duration_seconds": 213, "thumbnails": self._thumbs("s")}]
        return super().search(query, filter, limit, ignore_spelling)


class ApiTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        for cache in (core._meta, core.jobs, core.job_events, core.ingest_by_video, remote._auth_ok, core._search_cache,
                      core._browse_cache, core.downloaded, core._spotify_jobs, core._spotify_pages):
            cache.clear()
        for f in core.COVER_DIR.glob('*.jpg'):
            f.unlink()
        core._ytm = YTM()
        core.FOLLOW_DOWNLOADS = True  # the tests below check the download path; one test checks it off

        async def no_image(url):
            return None

        core._fetch_image = no_image  # no network in tests

        async def no_lrc(artist, title, duration):
            return None

        core.fetch_lrc = no_lrc

        async def no_unison(video_id, artist, title, duration):
            return None

        core.fetch_unison = no_unison
        self.client = FakeClient()
        api.st = store.Store(":memory:")
        self.st = api.st

        async def endpoint(request):
            res = await api.handle(request, request.path_params["path"], self.client)
            return res if res is not None else Response("PASSTHROUGH")

        self.app = Starlette(routes=[Route("/{path:path}", endpoint, methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"])])

    async def call(self, method, path, query=AUTH, body=None):
        r = await asgi(self.app, method, P + path, query, headers={"content-type": "application/json"},
                       body=json.dumps(body).encode() if body is not None else b"")
        try:
            r["json"] = json.loads(r["body"])
        except ValueError:
            r["json"] = None
        return r

    async def test_auth(self):
        self.assertEqual((await self.call("GET", "playlists", ""))["status"], 401)
        self.assertEqual((await self.call("GET", "playlists", "u=admin&t=bad&s=x"))["status"], 401)
        ping = (await self.call("GET", "ping", ""))["json"]
        self.assertTrue(ping["selfstream"])
        self.assertFalse(ping["authenticated"])
        self.assertTrue((await self.call("GET", "ping"))["json"]["authenticated"])
        self.assertEqual((await self.call("GET", "nope"))["status"], 404)
        self.assertEqual((await self.call("DELETE", "ping"))["status"], 405)

    async def test_non_api_paths_are_not_handled(self):
        r = await asgi(self.app, "GET", "/rest/ping", AUTH)
        self.assertEqual(r["body"], b"PASSTHROUGH")

    async def test_playlist_flow(self):
        song = subsonic.make_song({"id": "AAAAAAAAAAA", "title": "Never Gonna Give You Up", "channel": "Rick Astley", "duration": 213})
        r = await self.call("POST", "playlists", body={"name": "Mine", "songs": [song]})
        self.assertEqual(r["status"], 201)
        pid = r["json"]["id"]
        self.assertEqual(r["json"]["songCount"], 1)
        r = await self.call("POST", f"playlists/{pid}/songs", body={"songs": [{"id": "42", "title": "Local", "artist": "A"}]})
        self.assertEqual(r["json"]["added"], 1)
        full = (await self.call("GET", f"playlists/{pid}"))["json"]
        self.assertEqual([s["id"] for s in full["songs"]], ["yt-AAAAAAAAAAA", "42"])
        first, second = (s["playlistItemId"] for s in full["songs"])
        await self.call("PUT", f"playlists/{pid}/order", body={"itemIds": [second, first]})
        self.assertEqual([s["id"] for s in (await self.call("GET", f"playlists/{pid}"))["json"]["songs"]], ["42", "yt-AAAAAAAAAAA"])
        await self.call("PATCH", f"playlists/{pid}", body={"name": "Renamed"})
        self.assertEqual((await self.call("GET", "playlists"))["json"]["playlists"][0]["name"], "Renamed")
        self.assertEqual((await self.call("DELETE", f"playlists/{pid}/songs/{first}"))["status"], 200)
        self.assertEqual((await self.call("POST", "playlists", body={"name": " "}))["status"], 400)
        self.assertEqual((await self.call("DELETE", f"playlists/{pid}"))["status"], 200)
        self.assertEqual((await self.call("GET", f"playlists/{pid}"))["status"], 404)

    async def test_adding_a_song_that_is_already_there_is_refused(self):
        local = {"id": "42", "title": "Local", "artist": "A"}
        pid = (await self.call("POST", "playlists", body={"name": "Mine", "songs": [local]}))["json"]["id"]
        r = await self.call("POST", f"playlists/{pid}/songs", body={"songs": [local]})
        self.assertEqual(r["status"], 409)
        self.assertEqual(r["json"]["error"], "Already in this playlist")
        # a mix keeps the new ones and is not an error
        r = await self.call("POST", f"playlists/{pid}/songs", body={"songs": [local, {"id": "43", "title": "New", "artist": "A"}]})
        self.assertEqual((r["status"], r["json"]["added"]), (200, 1))
        r = await self.call("POST", f"playlists/{pid}/songs", body={"songs": [local, {"id": "43", "title": "New", "artist": "A"}]})
        self.assertEqual(r["status"], 409)
        self.assertEqual(len((await self.call("GET", f"playlists/{pid}"))["json"]["songs"]), 2)

    async def test_android_test_build_is_offered_from_the_app_folder(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            old = os.environ.get("APP_DIR")
            os.environ["APP_DIR"] = d
            try:
                self.assertEqual((await self.call("GET", "app/android", "u=nobody&p=x"))["status"], 401)
                self.assertEqual((await self.call("GET", "app/android", AUTH))["status"], 404)
                self.assertNotIn("app-update", api.features())
                for name in ("selfstream-0.3.0+1-9.apk", "selfstream-0.3.0+1-10.apk", "notes.txt", "selfstream-x.apk"):
                    with open(os.path.join(d, name), "wb") as f:
                        f.write(b"apk-" + name.encode())
                r = (await self.call("GET", "app/android", AUTH))["json"]
                self.assertEqual((r["build"], r["version"], r["name"]), (10, "0.3.0", "selfstream-0.3.0+1-10.apk"))
                self.assertIn("app-update", api.features())
                dl = await self.call("GET", "app/android/download", AUTH)
                self.assertEqual(dl["status"], 200)
                self.assertEqual(dl["body"], b"apk-selfstream-0.3.0+1-10.apk")
            finally:
                if old is None:
                    os.environ.pop("APP_DIR", None)
                else:
                    os.environ["APP_DIR"] = old

    async def test_plays_and_stats(self):
        song = {"id": "1", "title": "Dumb", "artist": "Nirvana", "album": "In Utero", "duration": 150, "genre": "Grunge"}
        now = time.time()
        for n in range(2):
            self.assertTrue((await self.call("POST", "plays", body={"song": song, "seconds": 150, "ts": now - 100 * (n + 1)}))["json"]["counted"])
        r = await self.call("POST", "plays/batch", body={"plays": [{"song": song, "seconds": 3, "ts": now - 5}, {"nonsense": 1}]})
        self.assertEqual(r["json"]["recorded"], 1)
        self.assertEqual((await self.call("POST", "plays", body={"seconds": 3}))["status"], 400)
        st = (await self.call("GET", "stats", AUTH + "&range=week&tz=60"))["json"]
        self.assertEqual(st["plays"], 2)
        self.assertEqual(st["topTracks"][0]["song"]["title"], "Dumb")
        self.assertEqual(st["topGenres"][0]["name"], "Grunge")
        self.assertEqual(len((await self.call("GET", "history"))["json"]["history"]), 3)
        h = (await self.call("GET", "home"))["json"]
        self.assertEqual(h["recent"][0]["title"], "Dumb")

    async def test_state_roundtrip(self):
        self.assertIsNone((await self.call("GET", "state/queue"))["json"]["value"])
        await self.call("PUT", "state/queue", body={"value": {"ids": ["a"], "index": 0}})
        self.assertEqual((await self.call("GET", "state/queue"))["json"]["value"]["ids"], ["a"])
        self.assertEqual((await self.call("GET", "state/BAD KEY"))["status"], 404)

    async def test_album_tracks_without_playable_id_are_found_by_search(self):
        core.SEARCH_SOURCE = "ytmusic"
        try:
            album = await core.remote_album("MPREb_whenever1")
        finally:
            core.SEARCH_SOURCE = "youtube"
        self.assertEqual([t["id"] for t in album["tracks"]], ["AAAAAAAAAAA", "DDDDDDDDDDD", "LLLLLLLLLLL"])
        self.assertEqual(album["tracks"][2]["number"], 4)  # keeps its place in the album

    async def test_duplicate_editions_of_an_album_are_listed_once(self):
        class Twice(YTM):
            def get_artist(self, cid):
                d = super().get_artist(cid)
                d["albums"]["results"].append({"title": "Whenever You Need Somebody", "year": "1987", "browseId": "MPREb_clean0001", "thumbnails": []})
                return d
        core._ytm = Twice()
        page = await core.remote_artist(FakeYTM.ARTIST)
        self.assertEqual([a["title"] for a in page["albums"]].count("Whenever You Need Somebody"), 1)

    async def test_presence_for_a_youtube_song_has_cover_and_link(self):
        core._meta["AAAAAAAAAAA"] = {"id": "AAAAAAAAAAA", "title": "Alright", "cover": "https://lh3.googleusercontent.com/x=w226-h226-l90-rj"}
        r = (await self.call("GET", "presence", AUTH + "&id=yt-AAAAAAAAAAA&title=Alright&artist=Kendrick+Lamar"))["json"]
        self.assertEqual((r["details"], r["state"]), ("Alright", "Kendrick Lamar"))
        self.assertIn("w512-h512", r["largeImage"])
        self.assertEqual(r["buttons"][0], {"label": "Open", "url": "https://www.youtube.com/watch?v=AAAAAAAAAAA"})
        self.assertEqual((await self.call("GET", "presence", AUTH))["status"], 400)

    async def test_share_and_presence_use_the_same_link(self):
        q = AUTH + "&id=yt-AAAAAAAAAAA&title=Alright&artist=Kendrick+Lamar"
        shared = (await self.call("GET", "share", q))["json"]
        self.assertEqual(shared["url"], (await self.call("GET", "presence", q))["json"]["buttons"][0]["url"])
        self.assertEqual(shared["url"], "https://www.youtube.com/watch?v=AAAAAAAAAAA")
        self.assertEqual((await self.call("GET", "share", AUTH))["status"], 400)

    async def test_extension_api_can_be_switched_off(self):
        old = core.EXTENSION_API
        try:
            core.EXTENSION_API = False
            self.assertEqual((await self.call("GET", "ping"))["status"], 404)
            self.assertEqual((await self.call("GET", "manifest"))["status"], 404)
        finally:
            core.EXTENSION_API = old

    async def test_presence_feature_needs_a_discord_application_id(self):
        old = core.DISCORD_CLIENT_ID
        try:
            core.DISCORD_CLIENT_ID = ""
            self.assertNotIn("presence", (await self.call("GET", "manifest"))["json"]["features"])
            core.DISCORD_CLIENT_ID = "123456789"
            self.assertIn("presence", (await self.call("GET", "manifest"))["json"]["features"])
            self.assertIn("presence", (await self.call("GET", "ping"))["json"]["features"])
        finally:
            core.DISCORD_CLIENT_ID = old

    async def test_manifest_lists_tabs_and_features(self):
        m = (await self.call("GET", "manifest"))["json"]
        self.assertEqual([t["id"] for t in m["tabs"]], ["home", "search", "library", "discover"])  # Activity is a button inside Discover
        self.assertIn("follows", m["features"])
        self.assertEqual((await self.call("GET", "manifest", "u=nobody&p=x"))["status"], 401)

    async def test_screens_are_sections_the_app_can_draw(self):
        for sid in ("home", "discover", "activity"):
            r = (await self.call("GET", f"screens/{sid}", AUTH))["json"]
            self.assertIn("sections", r)
            for sec in r["sections"]:
                self.assertIn(sec["type"], screens.SECTION_TYPES, sid)
        disc = (await self.call("GET", "screens/discover", AUTH))["json"]
        button = [a for a in disc["actions"] if a["action"].get("screen") == "activity"]
        self.assertEqual(button[0]["action"]["type"], "screen")
        self.assertTrue(any(x["type"] == "link" and x["action"].get("screen") == "activity" for x in disc["sections"]))
        act = (await self.call("GET", "screens/activity", AUTH + "&range=week"))["json"]
        self.assertEqual(act["sections"][0]["selected"], "week")
        self.assertEqual((await self.call("GET", "screens/nope", AUTH))["status"], 404)

    async def test_suggest(self):
        r = (await self.call("GET", "suggest", AUTH + "&q=kendrick"))["json"]
        self.assertEqual(r["suggestions"], ["kendrick live", "kendrick lyrics"])
        self.assertEqual((await self.call("GET", "suggest", AUTH + "&q=k"))["json"]["suggestions"], [])

    async def test_remote_playlists(self):
        found = (await self.call("GET", "remote/playlists", AUTH + "&q=road"))["json"]["playlists"]
        self.assertEqual(found[0]["id"], "PLroad00000001")
        pl = (await self.call("GET", "remote/playlists/PLroad00000001"))["json"]
        self.assertEqual([s["id"] for s in pl["songs"]], ["yt-FFFFFFFFFFF", "yt-GGGGGGGGGGG"])

    async def test_remote_playlists_from_a_pasted_link(self):
        for link in ("https://music.youtube.com/playlist?list=PLroad00000001",
                     "https://www.youtube.com/watch?v=abc&list=PLroad00000001&index=2",
                     "music.youtube.com/playlist?list=PLroad00000001&si=xyz"):
            found = (await self.call("GET", "remote/playlists", AUTH + "&q=" + link.replace("&", "%26").replace("?", "%3F").replace("=", "%3D")))["json"]["playlists"]
            self.assertEqual([f["id"] for f in found], ["PLroad00000001"], link)
            self.assertEqual(found[0]["title"], "Road trip")
        self.assertEqual(core.playlist_id_from_link("https://music.youtube.com/playlist?list=VLPLabcdefghij"), "PLabcdefghij")
        self.assertEqual(core.playlist_id_from_link("road trip"), "")

    async def test_spotify_playlist_link(self):
        data = {"props": {"pageProps": {"state": {"data": {"entity": {
            "title": "Road mix", "subtitle": "Someone", "coverArt": {"sources": [{"url": "https://i.scdn.co/small"}, {"url": "https://i.scdn.co/big"}]},
            "trackList": [
                {"uri": "spotify:track:1", "title": "Never Gonna Give You Up", "subtitle": "Rick\u00a0Astley", "duration": 213000},
                {"uri": "spotify:track:2", "title": "A Song Nobody Has", "subtitle": "Nobody", "duration": 180000},
                {"uri": "spotify:track:3", "title": "Never Gonna Give You Up", "subtitle": "Rick Astley", "duration": 213000}]}}}}}}
        html = '<html><script id="__NEXT_DATA__" type="application/json">' + json.dumps(data) + "</script></html>"

        async def fake_fetch(pid):
            self.assertEqual(pid, "37i9dQZF1DXcBWIGoYBM5M")
            return spotify.parse_embed(html)

        real, spotify.fetch = spotify.fetch, fake_fetch
        try:
            link = "https%3A%2F%2Fopen.spotify.com%2Fplaylist%2F37i9dQZF1DXcBWIGoYBM5M%3Fsi%3Dabc"
            found = (await self.call("GET", "remote/playlists", AUTH + "&q=" + link))["json"]["playlists"]
            self.assertEqual(found[0]["id"], "spotify-37i9dQZF1DXcBWIGoYBM5M")
            self.assertEqual(found[0]["title"], "Road mix")
            pl = (await self.call("GET", "remote/playlists/spotify-37i9dQZF1DXcBWIGoYBM5M"))["json"]
            self.assertEqual([x["id"] for x in pl["songs"]], ["yt-AAAAAAAAAAA"])  # the unmatched song is left out, the duplicate taken once
            self.assertEqual(pl["id"], "yp-spotify-37i9dQZF1DXcBWIGoYBM5M")
            r = await self.call("POST", "follows", body={"kind": "playlist", "id": pl["id"], "name": pl["name"]})
            self.assertEqual(r["status"], 201)
        finally:
            spotify.fetch = real
        self.assertEqual(spotify.link_id("spotify:playlist:37i9dQZF1DXcBWIGoYBM5M"), "37i9dQZF1DXcBWIGoYBM5M")
        self.assertEqual(spotify.link_id("https://open.spotify.com/intl-de/playlist/37i9dQZF1DXcBWIGoYBM5M"), "37i9dQZF1DXcBWIGoYBM5M")
        self.assertEqual(spotify.link_id("https://open.spotify.com/track/abc"), "")
        self.assertIsNone(spotify.parse_embed("<html>nothing</html>"))

    async def test_spotify_short_link(self):
        self.assertTrue(spotify._SHORT.search("look https://spotify.link/AbC123xyz now"))
        self.assertTrue(spotify._SHORT.search("https://open.spotify.com/playlist/x") is None)
        try:
            import httpx
        except ImportError:
            self.skipTest("httpx is not installed here")

        def handler(request):
            if request.url.host == "spotify.link":
                return httpx.Response(302, headers={"location": "https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M?si=abc"})
            return httpx.Response(200, text="<html></html>")

        real = httpx.AsyncClient
        httpx.AsyncClient = lambda **kw: real(transport=httpx.MockTransport(handler), **kw)
        try:
            self.assertEqual(await spotify.resolve_short("see https://spotify.link/AbC123xyz now"), "37i9dQZF1DXcBWIGoYBM5M")
            self.assertEqual(await spotify.resolve_short("https://example.com/x"), "")
        finally:
            httpx.AsyncClient = real

    async def test_follow_album_makes_playlist_and_downloads(self):
        r = await self.call("POST", "follows", body={"kind": "album", "id": "yb-MPREb_whenever1", "name": "Whenever"})
        self.assertEqual(r["status"], 201)
        self.assertEqual((await self.call("POST", "follows", body={"kind": "album", "id": "garbage"}))["status"], 400)
        f = r["json"]
        await follows.run(self.st, f)
        f = self.st.get_follow("admin", f["id"])
        self.assertEqual((f["status"], f["total"], f["done"], f["failed"]), ("done", 3, 3, 0))
        pl = self.st.playlist("admin", f["playlist_id"])
        self.assertEqual(pl["name"], "Whenever You Need Somebody")
        self.assertEqual(pl["kind"], "follow")
        self.assertEqual([s["id"] for s in pl["songs"]], ["yt-AAAAAAAAAAA", "yt-DDDDDDDDDDD", "yt-LLLLLLLLLLL"])
        await follows.run(self.st, self.st.get_follow("admin", f["id"]))  # again: nothing is duplicated
        self.assertEqual(self.st.playlist("admin", f["playlist_id"])["songCount"], 3)
        self.assertEqual(len((await self.call("GET", "follows"))["json"]["follows"]), 1)
        self.assertEqual((await self.call("DELETE", f"follows/{f['id']}", AUTH + "&deletePlaylist=1"))["status"], 200)
        self.assertIsNone(self.st.playlist("admin", f["playlist_id"]))

    async def test_follow_without_downloads_only_makes_the_playlist(self):
        core.FOLLOW_DOWNLOADS = False
        f = self.st.follow("admin", "album", "yb-MPREb_whenever1", "Whenever")
        await follows.run(self.st, f)
        f = self.st.get_follow("admin", f["id"])
        self.assertEqual((f["status"], f["total"], f["done"]), ("done", 3, 0))
        self.assertEqual(self.st.playlist("admin", f["playlist_id"])["songCount"], 3)
        self.assertEqual(len(core.jobs), 0)

    async def test_follow_artist_and_playlist(self):
        a = self.st.follow("admin", "artist", "ya-" + FakeYTM.ARTIST, "Rick Astley")
        await follows.run(self.st, a)
        pl = self.st.playlist("admin", self.st.get_follow("admin", a["id"])["playlist_id"])
        self.assertEqual(pl["songCount"], 3)  # the same songs on several releases are taken once
        p = self.st.follow("admin", "playlist", "yp-PLroad00000001", "Road trip")
        await follows.run(self.st, p)
        self.assertEqual(self.st.get_follow("admin", p["id"])["total"], 2)

    async def test_follow_failure_is_reported(self):
        core._ytm.fail = True
        f = self.st.follow("admin", "album", "yb-MPREb_whenever1", "x")
        await follows.run(self.st, f)
        self.assertEqual(self.st.get_follow("admin", f["id"])["status"], "error")
        self.assertEqual((await self.call("POST", f"follows/{f['id']}/retry"))["json"]["status"], "queued")

    async def test_discover(self):
        self.assertTrue((await self.call("GET", "discover"))["json"]["needsHistory"])
        now = time.time()
        rick = {"id": "yt-AAAAAAAAAAA", "title": "Never Gonna Give You Up", "artist": "Rick Astley", "duration": 213}
        known = {"id": "yt-IIIIIIIIIII", "title": "Known", "artist": "Rick Astley", "duration": 180}
        for n in range(3):
            self.st.record_play("admin", rick, 213, 213, ts=now - n * 3600)
        self.st.record_play("admin", known, 180, 180, ts=now - 10)
        r = (await self.call("GET", "discover"))["json"]
        titles = [s["title"] for s in r["songs"]]
        self.assertIn("New One", titles)
        self.assertNotIn("Known", titles)  # already played
        self.assertNotIn("Never Gonna Give You Up", titles)
        self.assertFalse(r["needsHistory"])
        self.assertTrue(all(s["id"].startswith("yt-") and s["reason"] for s in r["songs"]))
        again = (await self.call("GET", "discover"))["json"]
        self.assertEqual([s["id"] for s in again["songs"]], [s["id"] for s in r["songs"]])  # stable within the week
        calls = len([c for c in core._ytm.calls if c[0] == "radio"])
        await self.call("GET", "discover")
        self.assertEqual(len([c for c in core._ytm.calls if c[0] == "radio"]), calls)  # cached, no new lookups

    async def test_stream_prefers_the_library_copy(self):
        path = os.path.join(TMP, "music", "x.opus")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as fh:
            fh.write(b"LOCALFILE" * 100)
        core.downloaded["AAAAAAAAAAA"] = path

        async def endpoint(request):
            return await remote.handle(request, request.path_params["path"], self.client) or Response("PASS")

        app = Starlette(routes=[Route("/{path:path}", endpoint, methods=["GET"])])
        r = await asgi(app, "GET", "/rest/stream", AUTH + "&id=yt-AAAAAAAAAAA")
        self.assertEqual(r["body"], b"LOCALFILE" * 100)
        self.assertFalse(any("cdn.fake" in str(c.url) for c in self.client.calls))


class PureTests(unittest.TestCase):
    def test_limit_per_artist(self):
        ranked = [{"akey": "a", "n": i} for i in range(5)] + [{"akey": "b", "n": 9}]
        out = discover.limit_per_artist(ranked, 10, 2)
        self.assertEqual([e["akey"] for e in out], ["a", "a", "b"])

    def test_week_id(self):
        self.assertRegex(discover.week_id(time.time()), r"^\d{4}-W\d\d$")


if __name__ == "__main__":
    unittest.main()
