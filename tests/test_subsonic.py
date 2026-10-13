import json
import os
import sys
import unittest
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import subsonic as sx  # noqa: E402

NS = "{http://subsonic.org/restapi}"


class IdsAndTitles(unittest.TestCase):
    def test_parse_remote_id(self):
        self.assertEqual(sx.parse_remote_id("yt-dQw4w9WgXcQ"), "dQw4w9WgXcQ")
        for bad in (None, "", "123", "yt-short", "yt-dQw4w9WgXcQ!", "xt-dQw4w9WgXcQ", "yt-dQw4w9WgXcQQ"):
            self.assertIsNone(sx.parse_remote_id(bad), bad)

    def test_split_title(self):
        self.assertEqual(sx.split_title("Rick Astley - Never Gonna Give You Up (Official Video)", "VEVO"),
                         ("Rick Astley", "Never Gonna Give You Up"))
        self.assertEqual(sx.split_title("Song Name [Lyrics]", "Some Artist - Topic"), ("Some Artist", "Song Name"))
        self.assertEqual(sx.split_title("A - B - C", ""), ("A", "B - C"))
        self.assertEqual(sx.split_title("", ""), ("Unknown", "Unknown"))

    def test_make_song(self):
        s = sx.make_song({"id": "AAAAAAAAAAA", "title": "X - Y", "channel": "c", "duration": 100}, "☁ ")
        self.assertEqual((s["id"], s["title"], s["artist"], s["duration"]), ("yt-AAAAAAAAAAA", "☁ Y", "X", 100))
        self.assertEqual(s["coverArt"], "yt-AAAAAAAAAAA")
        self.assertIs(s["isDir"], False)
        s2 = sx.make_song({"id": "AAAAAAAAAAA", "title": "ignored", "artist": "Real", "track": "Name", "duration": 5})
        self.assertEqual((s2["artist"], s2["title"]), ("Real", "Name"))


class Lyrics(unittest.TestCase):
    def test_parse_lrc(self):
        lrc = "[ar:Someone]\n[00:12.34]Hello\n[01:02]World\n[00:05.5][00:07.25]Repeat\nno stamp\n[00:00:50]Colon"
        got = [(l["start"], l["value"]) for l in sx.parse_lrc(lrc)]
        self.assertEqual(got, [(500, "Colon"), (5500, "Repeat"), (7250, "Repeat"), (12340, "Hello"), (62000, "World")])
        self.assertEqual(sx.parse_lrc(""), [])

    def test_payload_json_and_xml(self):
        p = sx.lyrics_payload("A", "T", [{"start": 1000, "value": "hi & bye"}])
        j = json.loads(sx.envelope(True, p))["subsonic-response"]
        self.assertEqual(j["lyricsList"]["structuredLyrics"][0]["line"][0], {"start": 1000, "value": "hi & bye"})
        root = ET.fromstring(sx.envelope(False, p))
        line = root.find(f"{NS}lyricsList/{NS}structuredLyrics/{NS}line")
        self.assertEqual((line.get("start"), line.text), ("1000", "hi & bye"))
        self.assertEqual(root.find(f"{NS}lyricsList/{NS}structuredLyrics").get("synced"), "true")

    def test_empty_payload(self):
        j = json.loads(sx.envelope(True, sx.lyrics_payload("", "", [])))["subsonic-response"]
        self.assertEqual(j["lyricsList"]["structuredLyrics"], [])
        ET.fromstring(sx.envelope(False, sx.lyrics_payload("", "", [])))


class Envelopes(unittest.TestCase):
    def test_ok_and_error(self):
        j = json.loads(sx.envelope(True))["subsonic-response"]
        self.assertEqual((j["status"], j["version"]), ("ok", "1.16.1"))
        j = json.loads(sx.envelope(True, error=(40, "bad")))["subsonic-response"]
        self.assertEqual((j["status"], j["error"]), ("failed", {"code": 40, "message": "bad"}))
        root = ET.fromstring(sx.envelope(False, error=(40, "bad")))
        self.assertEqual(root.get("status"), "failed")
        self.assertEqual(root.find(f"{NS}error").get("code"), "40")

    def test_song_xml(self):
        root = ET.fromstring(sx.envelope(False, {"song": sx.make_song({"id": "AAAAAAAAAAA", "title": "X - Y", "duration": 3})}))
        song = root.find(f"{NS}song")
        self.assertEqual((song.get("id"), song.get("isDir"), song.get("duration")), ("yt-AAAAAAAAAAA", "false", "3"))


class LikesAndCovers(unittest.TestCase):
    def test_make_song_album_and_starred(self):
        meta = {"id": "AAAAAAAAAAA", "title": "x", "artist": "Rick Astley", "track": "Never Gonna Give You Up",
                "album": "Whenever You Need Somebody", "duration": 213}
        song = sx.make_song(meta, "☁ ", "2026-10-13T06:00:00Z")
        self.assertEqual((song["album"], song["starred"]), ("Whenever You Need Somebody", "2026-10-13T06:00:00Z"))
        self.assertNotIn("starred", sx.make_song(meta))
        self.assertEqual(sx.make_song({"id": "AAAAAAAAAAA", "title": "A - B"})["album"], "YouTube")

    def test_cover_url_asks_for_a_bigger_square(self):
        self.assertEqual(sx.cover_url("https://lh3.googleusercontent.com/abc=w226-h226-l90-rj"),
                         "https://lh3.googleusercontent.com/abc=w544-h544-l90-rj")
        self.assertEqual(sx.cover_url("https://i.ytimg.com/vi/x/hq.jpg"), "https://i.ytimg.com/vi/x/hq.jpg")
        self.assertEqual(sx.cover_url(""), "")

    def test_merge_starred_json_and_xml(self):
        remote = [sx.make_song({"id": "AAAAAAAAAAA", "title": "X - Y", "duration": 1}, "", "2026-01-01T00:00:00Z")]
        body = json.dumps({"subsonic-response": {"status": "ok", "version": "1.16.1",
                                                   "starred2": {"song": [{"id": "l1", "title": "Z", "artist": "Q"}]}}}).encode()
        out = json.loads(sx.merge_starred(body, True, "starred2", remote))
        self.assertEqual([s["id"] for s in out["subsonic-response"]["starred2"]["song"]], ["l1", "yt-AAAAAAAAAAA"])
        empty = json.dumps({"subsonic-response": {"status": "ok", "version": "1.16.1", "starred2": {}}}).encode()
        out = json.loads(sx.merge_starred(empty, True, "starred2", remote))
        self.assertEqual(len(out["subsonic-response"]["starred2"]["song"]), 1)
        xml = (b'<subsonic-response xmlns="http://subsonic.org/restapi" status="ok" version="1.16.1">'
               b'<starred><song id="l1" title="Z" artist="Q"/></starred></subsonic-response>')
        root = ET.fromstring(sx.merge_starred(xml, False, "starred", remote))
        self.assertEqual([s.get("id") for s in root.find(f"{NS}starred")], ["l1", "yt-AAAAAAAAAAA"])
        self.assertIsNone(sx.merge_starred(b"nope", True, "starred2", remote))

    def test_songs_in_and_find_local_match(self):
        body = json.dumps({"subsonic-response": {"status": "ok", "searchResult3": {"song": [
            {"id": "a", "title": "Never Gonna Give You Up (Remastered)", "artist": "Rick Astley"},
            {"id": "b", "title": "Never Gonna Give You Up", "artist": "Rick Astley"},
            {"id": "c", "title": "Never Gonna Give You Up", "artist": "Cover Band"}]}}}).encode()
        songs = sx.songs_in(body, True, "searchResult3")
        self.assertEqual(len(songs), 3)
        self.assertEqual(sx.find_local_match(songs, "Never Gonna Give You Up", "Rick Astley")["id"], "b")
        self.assertIsNone(sx.find_local_match(songs, "Other song", "Rick Astley"))
        self.assertEqual(sx.find_local_match(songs, "never gonna give you up", "")["id"], "b")
        self.assertEqual(sx.songs_in(b"garbage", True, "searchResult3"), [])
        failed = json.dumps({"subsonic-response": {"status": "failed"}}).encode()
        self.assertEqual(sx.songs_in(failed, True, "searchResult3"), [])
        xml = (b'<subsonic-response xmlns="http://subsonic.org/restapi" status="ok" version="1.16.1">'
               b'<searchResult3><song id="x" title="T" artist="A"/></searchResult3></subsonic-response>')
        self.assertEqual(sx.songs_in(xml, False, "searchResult3")[0]["id"], "x")


class MergeSearch(unittest.TestCase):
    remote = [sx.make_song({"id": "AAAAAAAAAAA", "title": "Rick Astley - Never Gonna Give You Up", "duration": 213}, "☁ ")]

    def body(self, songs=None, status="ok"):
        sr = {"song": songs} if songs else {}
        return json.dumps({"subsonic-response": {"status": status, "version": "1.16.1", "searchResult3": sr}}).encode()

    def test_json_append(self):
        local = [{"id": "l1", "title": "Other", "artist": "Z"}]
        out = json.loads(sx.merge_search3(self.body(local), True, self.remote, "☁ "))
        ids = [s["id"] for s in out["subsonic-response"]["searchResult3"]["song"]]
        self.assertEqual(ids, ["l1", "yt-AAAAAAAAAAA"])

    def test_json_no_local_songs_and_missing_searchresult(self):
        out = json.loads(sx.merge_search3(self.body(), True, self.remote, "☁ "))
        self.assertEqual(len(out["subsonic-response"]["searchResult3"]["song"]), 1)
        bare = json.dumps({"subsonic-response": {"status": "ok", "version": "1.16.1"}}).encode()
        out = json.loads(sx.merge_search3(bare, True, self.remote, "☁ "))
        self.assertEqual(len(out["subsonic-response"]["searchResult3"]["song"]), 1)

    def test_json_dedupes_against_local(self):
        local = [{"id": "l1", "title": "Never Gonna Give You Up", "artist": "Rick Astley"}]
        out = json.loads(sx.merge_search3(self.body(local), True, self.remote, "☁ "))
        self.assertEqual([s["id"] for s in out["subsonic-response"]["searchResult3"]["song"]], ["l1"])

    def test_json_other_artist_not_deduped(self):
        local = [{"id": "l1", "title": "Never Gonna Give You Up", "artist": "Cover Band"}]
        out = json.loads(sx.merge_search3(self.body(local), True, self.remote, "☁ "))
        self.assertEqual(len(out["subsonic-response"]["searchResult3"]["song"]), 2)

    def test_failed_or_garbage_returns_none(self):
        self.assertIsNone(sx.merge_search3(self.body(status="failed"), True, self.remote))
        self.assertIsNone(sx.merge_search3(b"not json", True, self.remote))
        self.assertIsNone(sx.merge_search3(b"<html>", False, self.remote))
        self.assertIsNone(sx.merge_search3(b'{"x": 1}', True, self.remote))

    def test_xml_namespaced(self):
        body = (b'<?xml version="1.0"?><subsonic-response xmlns="http://subsonic.org/restapi" status="ok" version="1.16.1">'
                b'<searchResult3><song id="l1" title="Other" artist="Z"/></searchResult3></subsonic-response>')
        out = sx.merge_search3(body, False, self.remote, "☁ ")
        root = ET.fromstring(out)
        ids = [s.get("id") for s in root.find(f"{NS}searchResult3")]
        self.assertEqual(ids, ["l1", "yt-AAAAAAAAAAA"])
        self.assertIn(b'xmlns="http://subsonic.org/restapi"', out)  # default namespace preserved

    def test_xml_empty_searchresult_and_failed(self):
        ok = b'<subsonic-response xmlns="http://subsonic.org/restapi" status="ok" version="1.16.1"><searchResult3/></subsonic-response>'
        root = ET.fromstring(sx.merge_search3(ok, False, self.remote))
        self.assertEqual(len(root.find(f"{NS}searchResult3")), 1)
        bad = b'<subsonic-response xmlns="http://subsonic.org/restapi" status="failed" version="1.16.1"/>'
        self.assertIsNone(sx.merge_search3(bad, False, self.remote))


if __name__ == "__main__":
    unittest.main()
