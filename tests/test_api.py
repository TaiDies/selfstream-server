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
import remote  # noqa: E402
import store  # noqa: E402
import subsonic  # noqa: E402

P = "/selfstream/api/v1/"


class YTM(FakeYTM):
    def get_playlist(self, pid, limit=100):
        self.calls.append(("get_playlist", pid))
        return {"title": "Road trip", "author": {"name": "Someone"}, "thumbnails": self._thumbs("pl"),
                "tracks": [{"videoId": "FFFFFFFFFFF", "title": "Song F", "artists": [{"name": "Band"}], "duration_seconds": 100},
                           {"videoId": "GGGGGGGGGGG", "title": "Song G", "artists": [{"name": "Band"}], "duration": "2:00"}]}

    def get_watch_playlist(self, videoId=None, radio=False, limit=25):
        self.calls.append(("radio", videoId))
        t = lambda v, title, artist: {"videoId": v, "title": title, "artists": [{"name": artist}], "length": "3:00",
                                      "thumbnail": self._thumbs(v)}
        return {"tracks": [t(videoId, "same", "same"), t("HHHHHHHHHHH", "New One", "Other Band"), t("IIIIIIIIIII", "Known", "Rick Astley"),
                           t("JJJJJJJJJJJ", "New Two", "Other Band"), t("KKKKKKKKKKK", "New Three", "Other Band")]}

    def search(self, query, filter=None, limit=None, ignore_spelling=False):
        if filter == "playlists":
            return [{"browseId": "VLPLroad00000001", "title": "Road trip", "author": "Someone", "itemCount": "2", "thumbnails": self._thumbs("pl")}]
        if filter == "songs":
            return [{"videoId": "AAAAAAAAAAA", "title": "Never Gonna Give You Up", "artists": [{"name": "Rick Astley"}],
                     "duration_seconds": 213, "thumbnails": self._thumbs("s")}]
        return super().search(query, filter, limit, ignore_spelling)


class ApiTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        for cache in (core._meta, core.jobs, core.job_events, core.ingest_by_video, remote._auth_ok, core._search_cache,
                      core._browse_cache, core.downloaded):
            cache.clear()
        core._ytm = YTM()

        async def no_image(url):
            return None

        core._fetch_image = no_image  # no network in tests

        async def no_lrc(artist, title, duration):
            return None

        core.fetch_lrc = no_lrc
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

    async def test_remote_playlists(self):
        found = (await self.call("GET", "remote/playlists", AUTH + "&q=road"))["json"]["playlists"]
        self.assertEqual(found[0]["id"], "PLroad00000001")
        pl = (await self.call("GET", "remote/playlists/PLroad00000001"))["json"]
        self.assertEqual([s["id"] for s in pl["songs"]], ["yt-FFFFFFFFFFF", "yt-GGGGGGGGGGG"])

    async def test_follow_album_makes_playlist_and_downloads(self):
        r = await self.call("POST", "follows", body={"kind": "album", "id": "yb-MPREb_whenever1", "name": "Whenever"})
        self.assertEqual(r["status"], 201)
        self.assertEqual((await self.call("POST", "follows", body={"kind": "album", "id": "garbage"}))["status"], 400)
        f = r["json"]
        await follows.run(self.st, f)
        f = self.st.get_follow("admin", f["id"])
        self.assertEqual((f["status"], f["total"], f["done"], f["failed"]), ("done", 2, 2, 0))
        pl = self.st.playlist("admin", f["playlist_id"])
        self.assertEqual(pl["name"], "Whenever You Need Somebody")
        self.assertEqual(pl["kind"], "follow")
        self.assertEqual([s["id"] for s in pl["songs"]], ["yt-AAAAAAAAAAA", "yt-DDDDDDDDDDD"])
        await follows.run(self.st, self.st.get_follow("admin", f["id"]))  # again: nothing is duplicated
        self.assertEqual(self.st.playlist("admin", f["playlist_id"])["songCount"], 2)
        self.assertEqual(len((await self.call("GET", "follows"))["json"]["follows"]), 1)
        self.assertEqual((await self.call("DELETE", f"follows/{f['id']}", AUTH + "&deletePlaylist=1"))["status"], 200)
        self.assertIsNone(self.st.playlist("admin", f["playlist_id"]))

    async def test_follow_artist_and_playlist(self):
        a = self.st.follow("admin", "artist", "ya-" + FakeYTM.ARTIST, "Rick Astley")
        await follows.run(self.st, a)
        pl = self.st.playlist("admin", self.st.get_follow("admin", a["id"])["playlist_id"])
        self.assertEqual(pl["songCount"], 2)  # the same two songs on three releases are taken once
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
