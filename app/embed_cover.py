"""Embed a cover image into an audio file: python embed_cover.py <audio file> <image file>

Run as a separate process (so a broken file can never take the server down). Needs mutagen.
Supports opus/ogg, m4a/mp4, mp3 and flac. Exit code 0 = embedded.
"""
import base64
import sys


def mime_of(data: bytes) -> str:
    if data.startswith(b"\x89PNG"):
        return "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return "image/jpeg"


def embed(path: str, data: bytes) -> None:
    mime = mime_of(data)
    ext = path.rsplit(".", 1)[-1].lower()
    if ext in ("opus", "ogg"):
        from mutagen.flac import Picture
        from mutagen.oggopus import OggOpus

        pic = Picture()
        pic.type, pic.mime, pic.desc, pic.data = 3, mime, "Cover", data
        f = OggOpus(path)
        f["metadata_block_picture"] = [base64.b64encode(pic.write()).decode("ascii")]
        f.save()
    elif ext in ("m4a", "mp4", "aac"):
        from mutagen.mp4 import MP4, MP4Cover

        f = MP4(path)
        fmt = MP4Cover.FORMAT_PNG if mime == "image/png" else MP4Cover.FORMAT_JPEG
        f["covr"] = [MP4Cover(data, imageformat=fmt)]
        f.save()
    elif ext == "mp3":
        from mutagen.id3 import APIC, ID3, ID3NoHeaderError

        try:
            tags = ID3(path)
        except ID3NoHeaderError:
            tags = ID3()
        tags.delall("APIC")
        tags.add(APIC(encoding=3, mime=mime, type=3, desc="Cover", data=data))
        tags.save(path)
    elif ext == "flac":
        from mutagen.flac import FLAC, Picture

        pic = Picture()
        pic.type, pic.mime, pic.desc, pic.data = 3, mime, "Cover", data
        f = FLAC(path)
        f.clear_pictures()
        f.add_picture(pic)
        f.save()
    else:
        raise ValueError(f"unsupported format: {ext}")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit("usage: embed_cover.py <audio file> <image file>")
    with open(sys.argv[2], "rb") as fh:
        image = fh.read()
    if len(image) < 500:
        sys.exit("image too small to be a cover")
    embed(sys.argv[1], image)
