import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

import store  # noqa: E402

DAY = 86400
NOW = 1_800_000_000.0


def song(i, title, artist="Nirvana", album="Nevermind", dur=200, genre=None):
    s = {"id": i, "title": title, "artist": artist, "album": album, "duration": dur, "coverArt": i}
    if genre:
        s["genre"] = genre
    return s


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.s = store.Store(os.path.join(self.dir.name, "t.db"))

    def tearDown(self):
        self.dir.cleanup()

    def test_track_key_matches_library_and_cloud_copies(self):
        self.assertEqual(store.track_key("Nirvana", "Come As You Are (2021 Remaster)"), store.track_key("nirvana", "Come as you are"))
        self.assertEqual(store.track_key("Nirvana, Someone", "X"), store.track_key("Nirvana", "X"))
        self.assertNotEqual(store.track_key("Nirvana", "Dumb"), store.track_key("Nirvana", "Dumb (Live)"))

    def test_playlist_roundtrip_and_order(self):
        p = self.s.create_playlist("u", "Road", songs=[song("yt-aaaaaaaaaaa", "A"), song("2", "B"), song("3", "C")])
        self.assertEqual(p["songCount"], 3)
        full = self.s.playlist("u", p["id"])
        ids = [x["playlistItemId"] for x in full["songs"]]
        self.s.reorder("u", p["id"], [ids[2], ids[0]])
        self.assertEqual([x["title"] for x in self.s.playlist("u", p["id"])["songs"]], ["C", "A", "B"])
        self.s.remove_item("u", p["id"], ids[0])
        self.assertEqual([x["title"] for x in self.s.playlist("u", p["id"])["songs"]], ["C", "B"])
        self.assertEqual(self.s.add_songs("u", p["id"], [song("9", "C"), song("10", "D")], skip_existing=True), 1)

    def test_playlists_are_private_to_a_user(self):
        p = self.s.create_playlist("alice", "Mine")
        self.assertIsNone(self.s.playlist("bob", p["id"]))
        self.assertFalse(self.s.delete_playlist("bob", p["id"]))
        self.assertEqual(self.s.playlists("bob"), [])
        self.assertTrue(self.s.delete_playlist("alice", p["id"]))

    def test_play_counting_and_idempotence(self):
        a = self.s.record_play("u", song("1", "A"), 45, 200, ts=NOW)
        self.assertTrue(a["counted"])
        self.assertTrue(self.s.record_play("u", song("1", "A"), 45, 200, ts=NOW)["duplicate"])
        self.assertFalse(self.s.record_play("u", song("1", "A"), 5, 200, ts=NOW + 1)["counted"])
        self.assertTrue(self.s.record_play("u", song("2", "Short"), 20, 30, ts=NOW + 2)["counted"])  # over half of a 30s track
        self.assertFalse(self.s.record_play("u", {"id": "x"}, 10)["ok"])

    def test_stats(self):
        t = NOW - 2 * DAY
        for n in range(3):
            self.s.record_play("u", song("1", "Dumb", genre="Grunge"), 200, 200, ts=t + n * 1000)
        self.s.record_play("u", song("2", "Milk It", genre="Grunge; Rock"), 100, 200, ts=t + 5000)
        self.s.record_play("u", song("3", "Hey", artist="Pixies", album="Doolittle", genre="Alt"), 190, 190, ts=t + 9000)
        self.s.record_play("u", song("3", "Hey", artist="Pixies", album="Doolittle"), 3, 190, ts=t + 9500)  # skip
        self.s.record_play("other", song("1", "Dumb"), 200, 200, ts=t)
        st = self.s.stats("u", "month", now=NOW)
        self.assertEqual(st["plays"], 5)
        self.assertEqual(st["uniqueTracks"], 3)
        self.assertEqual(st["topTracks"][0]["song"]["title"], "Dumb")
        self.assertEqual(st["topTracks"][0]["plays"], 3)
        self.assertEqual(st["topArtists"][0]["name"], "Nirvana")
        self.assertEqual(st["topGenres"][0]["name"], "Grunge")
        self.assertEqual(st["totalSeconds"], 600 + 100 + 190 + 3)
        self.assertEqual(st["newArtists"], 2)
        self.assertGreater(st["skipRate"], 0)
        self.assertEqual(sum(st["byHour"]), st["totalSeconds"])
        self.assertEqual(st["activeDays"], 1)
        self.assertEqual(st["topAlbums"][0]["name"], "Nevermind")
        self.assertEqual(self.s.stats("u", "week", now=NOW + 30 * DAY)["plays"], 0)  # outside the range
        self.assertEqual(self.s.stats("nobody", "all", now=NOW)["plays"], 0)

    def test_streaks_and_timezone(self):
        for d in (5, 4, 3, 1, 0):
            self.s.record_play("u", song("1", "A"), 100, 100, ts=NOW - d * DAY)
        st = self.s.stats("u", "all", now=NOW)
        self.assertEqual(st["longestStreak"], 3)
        self.assertEqual(st["currentStreak"], 2)

    def test_taste_weights_recent_and_penalises_skips(self):
        self.s.record_play("u", song("1", "Old"), 200, 200, ts=NOW - 100 * DAY)
        self.s.record_play("u", song("2", "New"), 200, 200, ts=NOW - 1 * DAY)
        self.s.record_play("u", song("3", "Skipped"), 2, 200, ts=NOW - 1 * DAY)
        t = self.s.taste("u", now=NOW)
        k = store.track_key
        self.assertGreater(t["tracks"][k("Nirvana", "New")], t["tracks"][k("Nirvana", "Old")])
        self.assertLess(t["tracks"][k("Nirvana", "Skipped")], 0)

    def test_library_snapshot_wins_over_cloud_copy(self):
        self.s.remember(song("yt-aaaaaaaaaaa", "Dumb"))
        self.s.remember(song("77", "Dumb"))
        self.s.remember(song("yt-aaaaaaaaaaa", "Dumb"))
        self.assertEqual(self.s.track(store.track_key("Nirvana", "Dumb"))["id"], "77")

    def test_genre_propagation_from_artist(self):
        self.s.remember(song("1", "A", artist="Pixies"))
        self.assertEqual(len(self.s.tracks_without_genres()), 1)
        self.s.set_artist_genres("Pixies", ["Alternative"], "test")
        self.assertEqual(self.s.apply_artist_genres(), 1)
        self.assertEqual(self.s.track(store.track_key("Pixies", "A"))["genres"], ["Alternative"])

    def test_follows_and_state(self):
        f = self.s.follow("u", "album", "yb-1", "Nevermind")
        self.assertEqual(self.s.follow("u", "album", "yb-1", "Nevermind")["id"], f["id"])
        self.assertEqual(len(self.s.open_follows()), 1)
        self.s.update_follow(f["id"], status="done")
        self.assertEqual(self.s.open_follows(), [])
        self.assertTrue(self.s.unfollow("u", f["id"]))
        self.s.set_state("u", "queue", {"ids": [1, 2]})
        self.assertEqual(self.s.get_state("u", "queue")["value"], {"ids": [1, 2]})
        self.assertIsNone(self.s.get_state("v", "queue"))


if __name__ == "__main__":
    unittest.main()
