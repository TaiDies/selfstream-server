import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import apkfetch  # noqa: E402

BIG = b"x" * 1_100_000


class ApkFetchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        apkfetch.CONF = self.tmp / "android-update.conf"
        apkfetch.DEST = self.tmp / "app"
        self.calls = []

    def conf(self, text="REPO=me/private-repo\nTOKEN=secret\n"):
        apkfetch.CONF.write_text(text)

    def releases(self):
        return [{"assets": [{"id": 1, "name": "selfstream-0.3.0-9.apk"}, {"id": 5, "name": "build.log"}]},
                {"draft": True, "assets": [{"id": 99, "name": "selfstream-0.3.0-50.apk"}]},
                {"assets": [{"id": 2, "name": "selfstream-0.3.0-12.apk"}]}]

    def fetch(self, releases=None, payload=BIG):
        def get_json(url, token):
            self.calls.append(("list", url, token))
            return self.releases() if releases is None else releases

        def download(url, token, dest):
            self.calls.append(("download", url))
            dest.write_bytes(payload)

        return apkfetch.run(get_json, download)

    def test_pick_takes_the_highest_build_and_ignores_drafts_and_other_files(self):
        self.assertEqual(apkfetch.pick(self.releases()), (12, "selfstream-0.3.0-12.apk", 2))
        self.assertIsNone(apkfetch.pick([{"assets": [{"id": 1, "name": "notes.txt"}]}]))
        self.assertIsNone(apkfetch.pick({"message": "Bad credentials"}))

    def test_read_conf(self):
        self.assertEqual(apkfetch.read_conf("# c\nREPO = a/b\nTOKEN='t'\nbad line\n"), {"REPO": "a/b", "TOKEN": "t"})

    def test_does_nothing_without_a_config(self):
        self.assertEqual(self.fetch(), 0)
        self.assertEqual(self.calls, [])
        self.assertFalse(apkfetch.DEST.exists())

    def test_downloads_the_newest_and_removes_older_builds(self):
        self.conf()
        apkfetch.DEST.mkdir()
        (apkfetch.DEST / "selfstream-0.3.0-9.apk").write_bytes(BIG)
        (apkfetch.DEST / "notes.txt").write_text("keep me")
        self.fetch()
        self.assertEqual(sorted(p.name for p in apkfetch.DEST.iterdir()), ["notes.txt", "selfstream-0.3.0-12.apk"])
        self.assertEqual(self.calls[1], ("download", "https://api.github.com/repos/me/private-repo/releases/assets/2"))
        self.fetch()  # already there: no second download
        self.assertEqual([c[0] for c in self.calls].count("download"), 1)

    def test_a_tiny_download_is_not_kept(self):
        self.conf()
        self.fetch(payload=b"<html>error</html>")
        self.assertEqual(list(apkfetch.DEST.glob("*.apk")), [])
        self.assertEqual(list(apkfetch.DEST.glob("*.part")), [])

    def test_bad_config_and_network_errors_never_raise(self):
        self.conf("REPO=not-a-repo\nTOKEN=x\n")
        self.assertEqual(self.fetch(), 0)
        self.assertEqual(self.calls, [])
        self.conf()

        def boom(url, token):
            raise OSError("offline")

        self.assertEqual(apkfetch.run(boom, None), 0)


if __name__ == "__main__":
    unittest.main()
