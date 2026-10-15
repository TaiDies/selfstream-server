import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from app import core  # noqa: E402


def item(vid, title, artist, vt=None):
    i = {"id": vid, "title": title, "channel": artist, "duration": 200, "artist": artist, "track": title}
    if vt:
        i["video_type"] = vt
    return i


class OfficialAudio(unittest.TestCase):
    def test_video_copy_is_dropped_when_studio_audio_is_present(self):
        items = [item("v" * 11, "Alright", "Kendrick Lamar", "MUSIC_VIDEO_TYPE_OMV"),
                 item("a" * 11, "Alright", "Kendrick Lamar", "MUSIC_VIDEO_TYPE_ATV"),
                 item("b" * 11, "Alright (Live)", "Kendrick Lamar", "MUSIC_VIDEO_TYPE_OMV")]
        self.assertEqual([i["id"] for i in core.prefer_official_audio(items)], ["a" * 11, "b" * 11])

    def test_lone_video_is_kept_and_unknown_types_count_as_audio(self):
        only = [item("v" * 11, "Skit", "X", "MUSIC_VIDEO_TYPE_OMV")]
        self.assertEqual(core.prefer_official_audio(only), only)
        plain = [item("p" * 11, "Song", "Y"), item("q" * 11, "Song", "Y")]
        self.assertEqual(len(core.prefer_official_audio(plain)), 2)


if __name__ == "__main__":
    unittest.main()
