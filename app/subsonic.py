"""Pure helpers for building and merging Subsonic API responses.

No third-party imports on purpose: everything here is unit-tested without a web stack.
"""
import json
import re
import xml.etree.ElementTree as ET

NS = "http://subsonic.org/restapi"
API_VERSION = "1.16.1"
ET.register_namespace("", NS)

_ID_RE = re.compile(r"^yt-([A-Za-z0-9_-]{11})$")
_LRC_TS = re.compile(r"\[(\d{1,3}):(\d{2})(?:[.:](\d{1,3}))?\]")
_NOISE = re.compile(
    r"\s*[\(\[]\s*(?:official|lyrics?|lyric video|audio|video|music video|hd|hq|4k|visuali[sz]er)[^\)\]]*[\)\]]",
    re.I,
)


# --------------------------------------------------------------------- ids
def remote_id(video_id: str) -> str:
    return f"yt-{video_id}"


def parse_remote_id(value: str | None) -> str | None:
    """'yt-dQw4w9WgXcQ' -> 'dQw4w9WgXcQ'; anything else -> None."""
    m = _ID_RE.match(value or "")
    return m.group(1) if m else None


def wants_json(fmt: str | None) -> bool:
    return (fmt or "").lower() in ("json", "jsonp")


# ------------------------------------------------------------------ titles
def split_title(title: str, channel: str = "") -> tuple[str, str]:
    """Best-effort (artist, track) from a YouTube title and channel name."""
    t = _NOISE.sub("", title or "").strip() or (title or "Unknown")
    if " - " in t:
        artist, track = t.split(" - ", 1)
        return artist.strip() or "Unknown", track.strip() or t
    artist = re.sub(r"\s*-\s*Topic$", "", channel or "").strip() or "Unknown"
    return artist, t


def make_song(meta: dict, prefix: str = "") -> dict:
    """Subsonic <song> dict for a remote track. meta: id, title, channel, duration, [artist, track]."""
    artist, track = meta.get("artist"), meta.get("track")
    if not (artist and track):
        artist, track = split_title(meta.get("title", ""), meta.get("channel", ""))
    duration = int(meta.get("duration") or 0)
    rid = remote_id(meta["id"])
    return {
        "id": rid,
        "parent": "yt",
        "isDir": False,
        "title": f"{prefix}{track}",
        "album": "YouTube",
        "artist": artist,
        "duration": duration,
        "coverArt": rid,
        "size": duration * 16000,  # rough guess (~128 kbps); some clients want a number
        "contentType": "audio/mp4",
        "suffix": "m4a",
        "bitRate": 128,
        "path": f"YouTube/{artist}/{track}.m4a",
        "type": "music",
        "mediaType": "song",
        "isVideo": False,
    }


# ------------------------------------------------------------------- lyrics
def parse_lrc(text: str) -> list[dict]:
    """LRC text -> [{'start': ms, 'value': line}], sorted. Ignores [ar:..] style tags."""
    lines: list[dict] = []
    for raw in (text or "").splitlines():
        stamps = list(_LRC_TS.finditer(raw))
        if not stamps:
            continue
        value = raw[stamps[-1].end():].strip()
        for m in stamps:
            mins, secs, frac = m.groups()
            ms = int(mins) * 60000 + int(secs) * 1000 + (int(frac.ljust(3, "0")[:3]) if frac else 0)
            lines.append({"start": ms, "value": value})
    lines.sort(key=lambda x: x["start"])
    return lines


def lyrics_payload(artist: str, title: str, lines: list[dict]) -> dict:
    if not lines:
        return {"lyricsList": {"structuredLyrics": []}}
    return {
        "lyricsList": {
            "structuredLyrics": [
                {
                    "displayArtist": artist,
                    "displayTitle": title,
                    "lang": "xxx",
                    "synced": True,
                    "offset": 0,
                    "line": lines,
                }
            ]
        }
    }


# --------------------------------------------------------------- envelopes
def _q(tag: str) -> str:
    return f"{{{NS}}}{tag}"


def _scalar(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    return str(v)


def _fill(el: ET.Element, data: dict) -> None:
    for key, val in data.items():
        if val is None:
            continue
        if key == "value" and not isinstance(val, (dict, list)):
            el.text = _scalar(val)  # e.g. <line start="1000">text</line>
        elif isinstance(val, dict):
            _fill(ET.SubElement(el, _q(key)), val)
        elif isinstance(val, list):
            for item in val:
                child = ET.SubElement(el, _q(key))
                if isinstance(item, dict):
                    _fill(child, item)
                else:
                    child.text = _scalar(item)
        else:
            el.set(key, _scalar(val))


def envelope(as_json: bool, payload: dict | None = None, error: tuple[int, str] | None = None) -> bytes:
    """A complete subsonic-response document. error = (code, message)."""
    attrs = {
        "status": "failed" if error else "ok",
        "version": API_VERSION,
        "type": "selfstream",
        "serverVersion": "0.2",
        "openSubsonic": True,
    }
    body = dict(attrs)
    if error:
        body["error"] = {"code": error[0], "message": error[1]}
    elif payload:
        body.update(payload)
    if as_json:
        return json.dumps({"subsonic-response": body}).encode()
    root = ET.Element(_q("subsonic-response"))
    _fill(root, body)
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


# ------------------------------------------------------------ search merge
def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (s or "").lower())


def _is_dup(remote: dict, local_pairs: set[tuple[str, str]], prefix: str) -> bool:
    title = _norm(remote["title"].removeprefix(prefix))
    artist = _norm(remote.get("artist", ""))
    for lt, la in local_pairs:
        if lt == title and (not artist or not la or artist in la or la in artist):
            return True
    return False


def merge_search3(body: bytes, as_json: bool, remote_songs: list[dict], prefix: str = "") -> bytes | None:
    """Append remote songs to a Navidrome search3 response.

    Returns None when the upstream body is not a successful search3 response (caller then
    passes the original through untouched). Remote songs that already exist locally are dropped.
    """
    try:
        if as_json:
            data = json.loads(body)
            resp = data["subsonic-response"]
            if resp.get("status") != "ok":
                return None
            sr = resp.setdefault("searchResult3", {})
            local = sr.get("song") or []
            pairs = {(_norm(s.get("title", "")), _norm(s.get("artist", ""))) for s in local}
            sr["song"] = local + [s for s in remote_songs if not _is_dup(s, pairs, prefix)]
            return json.dumps(data).encode()

        root = ET.fromstring(body)
        if root.get("status") != "ok":
            return None
        ns = root.tag[: root.tag.index("}") + 1] if root.tag.startswith("{") else ""
        sr = next((e for e in root if e.tag == f"{ns}searchResult3"), None)
        if sr is None:
            sr = ET.SubElement(root, f"{ns}searchResult3")
        pairs = {(_norm(e.get("title", "")), _norm(e.get("artist", ""))) for e in sr if e.tag == f"{ns}song"}
        for song in remote_songs:
            if _is_dup(song, pairs, prefix):
                continue
            el = ET.SubElement(sr, f"{ns}song")
            for k, v in song.items():
                el.set(k, _scalar(v))
        return ET.tostring(root, encoding="utf-8", xml_declaration=True)
    except (ValueError, KeyError, TypeError, ET.ParseError):
        return None
