"""prefetch_streams: warms the stream-address lookup for cloud songs without doing extra work."""
import asyncio
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))
import test_remote  # noqa: E402,F401  (sets the test environment)

import core  # noqa: E402


class PrefetchTest(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self._orig = core.resolve_stream

        async def fake(vid, fresh=False):
            self.calls.append(vid)
            core._stream_cache[vid] = (__import__("time").time(), {"url": "u", "headers": {}, "ext": "m4a"})

        core.resolve_stream = fake
        core._stream_cache.clear()
        core._warming.clear()

    def tearDown(self):
        core.resolve_stream = self._orig
        core._stream_cache.clear()

    def run_it(self, ids, **kw):
        async def go():
            n = core.prefetch_streams(ids, **kw)
            await asyncio.sleep(0.05)
            return n
        return asyncio.run(go())

    def test_only_cloud_ids_and_limit(self):
        n = self.run_it(["abc", "yt-AAAAAAAAAAA", "yt-BBBBBBBBBBB", "yt-CCCCCCCCCCC", "yt-DDDDDDDDDDD"], limit=3)
        self.assertEqual(n, 3)
        self.assertEqual(self.calls, ["AAAAAAAAAAA", "BBBBBBBBBBB", "CCCCCCCCCCC"])

    def test_skips_cached_and_bad_ids(self):
        core._stream_cache["AAAAAAAAAAA"] = (__import__("time").time(), {"url": "u"})
        n = self.run_it(["yt-AAAAAAAAAAA", "yt-", "yt-bad id!", "yt-BBBBBBBBBBB"])
        self.assertEqual(n, 1)
        self.assertEqual(self.calls, ["BBBBBBBBBBB"])

    def test_skips_downloaded(self):
        core.downloaded["EEEEEEEEEEE"] = "/x.opus"
        try:
            self.assertEqual(self.run_it(["yt-EEEEEEEEEEE"]), 0)
        finally:
            core.downloaded.pop("EEEEEEEEEEE", None)


if __name__ == "__main__":
    unittest.main()


class ResolveRetryTest(unittest.TestCase):
    def test_second_attempt_is_used(self):
        orig_run = core.run
        calls = []

        async def fake_run(*args, timeout=60):
            calls.append(1)
            if len(calls) == 1:
                return 1, "", "HTTP Error 429"
            return 0, '{"url": "http://a/b", "http_headers": {}, "ext": "m4a", "title": "T", "duration": 10}', ""

        core.run = fake_run
        core._stream_cache.pop("RRRRRRRRRRR", None)
        try:
            info = asyncio.run(core.resolve_stream("RRRRRRRRRRR"))
        finally:
            core.run = orig_run
            core._stream_cache.pop("RRRRRRRRRRR", None)
        self.assertEqual(len(calls), 2)
        self.assertEqual(info["url"], "http://a/b")
