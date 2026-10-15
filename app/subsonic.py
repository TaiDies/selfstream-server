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
_ARTIST_RE = re.compile(r"^ya-([A-Za-z0-9_-]{8,64})$")
_ALBUM_RE = re.compile(r"^yb-([A-Za-z0-9_-]{8,64})$")
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


def remote_artist_id(channel_id: str) -> str:
    return f"ya-{channel_id}"


def remote_album_id(browse_id: str) -> str:
    return f"yb-{browse_id}"


def parse_artist_id(value: str | None) -> str | None:
    m = _ARTIST_RE.match(value or "")
    return m.group(1) if m else None


def parse_album_id(value: str | None) -> str | None:
    m = _ALBUM_RE.match(value or "")
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


def cover_url(url: str, size: int = 544) -> str:
    """YouTube Music thumbnails end in '=w226-h226-...': ask for a bigger square instead."""
    return re.sub(r"=w\d+-h\d+.*$", f"=w{size}-h{size}-l90-rj", url or "")


def track_names(meta: dict) -> tuple[str, str]:
    """(artist, title) a remote track is shown with: YouTube Music's own names, else guessed from the video title."""
    artist, track = meta.get("artist"), meta.get("track")
    if not (artist and track):
        artist, track = split_title(meta.get("title", ""), meta.get("channel", ""))
    return artist, track


def make_song(meta: dict, prefix: str = "", starred: str | None = None) -> dict:
    """Subsonic <song> dict for a remote track.

    meta: id, title, channel, duration and optionally artist, track, album.
    starred: ISO timestamp when the user has hearted it.
    """
    artist, track = track_names(meta)
    duration = int(meta.get("duration") or 0)
    rid = remote_id(meta["id"])
    song = {
        "id": rid,
        "parent": "yt",
        "isDir": False,
        "title": f"{prefix}{track}",
        "album": meta.get("album") or "YouTube",
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
    if starred:
        song["starred"] = starred
    return song


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


_TITLE_NOISE = re.compile(
    r"\s*[\(\[]\s*(?:official|lyrics?|lyric video|audio|video|music video|visuali[sz]er|explicit|clean|hd|hq|4k"
    r"|(?:\d{4}\s+)?(?:digital\s+)?remaster)[^\)\]]*[\)\]]"
    r"|\s+-\s+(?:\d{4}\s+)?(?:digital\s+)?remaster(?:ed)?(?:\s+\d{4})?\s*$",
    re.I,
)


def norm_title(s: str) -> str:
    """Title for matching the same song across sources: '(Remastered 2009)', '(Official Video)' etc. ignored."""
    return _norm(_TITLE_NOISE.sub("", s or ""))


def _album_known(name: str) -> str:
    a = _norm(name)
    return "" if a in ("", "youtube") else a


def _same_album(a: str, b: str) -> bool:
    """Albums agree, or one side does not say (then we cannot tell them apart and treat them as the same)."""
    a, b = _album_known(a), _album_known(b)
    return not a or not b or a in b or b in a


def _is_dup(remote: dict, local_pairs: set[tuple[str, str, str]], prefix: str) -> bool:
    """A streamed song is hidden only if the library already has that song on that album (same title, artist and album).

    The same song on another album (single, compilation, deluxe edition) stays visible.
    """
    title = norm_title(remote["title"].removeprefix(prefix))
    artist = _norm(remote.get("artist", ""))
    for lt, la, lal in local_pairs:
        if lt == title and (not artist or not la or artist in la or la in artist) and _same_album(remote.get("album", ""), lal):
            return True
    return False


def merge_songs(body: bytes, as_json: bool, key: str, remote_songs: list[dict], prefix: str = "") -> bytes | None:
    """Append remote songs to the <key> element ('searchResult3', 'starred2', ...) of a Navidrome response.

    Returns None when the upstream body is not a successful response (caller then passes the
    original through untouched). Remote songs that already exist locally are dropped.
    """
    try:
        if as_json:
            data = json.loads(body)
            resp = data["subsonic-response"]
            if resp.get("status") != "ok":
                return None
            sr = resp.setdefault(key, {})
            local = sr.get("song") or []
            pairs = {(norm_title(s.get("title", "")), _norm(s.get("artist", "")), s.get("album", "")) for s in local}
            sr["song"] = local + [s for s in remote_songs if not _is_dup(s, pairs, prefix)]
            return json.dumps(data).encode()

        root = ET.fromstring(body)
        if root.get("status") != "ok":
            return None
        ns = root.tag[: root.tag.index("}") + 1] if root.tag.startswith("{") else ""
        sr = next((e for e in root if e.tag == f"{ns}{key}"), None)
        if sr is None:
            sr = ET.SubElement(root, f"{ns}{key}")
        pairs = {(norm_title(e.get("title", "")), _norm(e.get("artist", "")), e.get("album", "")) for e in sr if e.tag == f"{ns}song"}
        for song in remote_songs:
            if _is_dup(song, pairs, prefix):
                continue
            el = ET.SubElement(sr, f"{ns}song")
            for k, v in song.items():
                el.set(k, _scalar(v))
        return ET.tostring(root, encoding="utf-8", xml_declaration=True)
    except (ValueError, KeyError, TypeError, ET.ParseError):
        return None


def merge_search3(body: bytes, as_json: bool, remote_songs: list[dict], prefix: str = "") -> bytes | None:
    return merge_songs(body, as_json, "searchResult3", remote_songs, prefix)


def merge_starred(body: bytes, as_json: bool, key: str, remote_songs: list[dict], prefix: str = "") -> bytes | None:
    """key is 'starred2' (getStarred2) or 'starred' (getStarred)."""
    return merge_songs(body, as_json, key, remote_songs, prefix)


def songs_in(body: bytes, as_json: bool, key: str) -> list[dict]:
    """The <song> entries of a successful response's <key> element, as dicts (empty on any problem)."""
    try:
        if as_json:
            resp = json.loads(body)["subsonic-response"]
            return (resp.get("status") == "ok" and (resp.get(key) or {}).get("song")) or []
        root = ET.fromstring(body)
        if root.get("status") != "ok":
            return []
        return [dict(e.attrib) for sr in root if sr.tag.endswith(key) for e in sr if e.tag.endswith("song")]
    except (ValueError, KeyError, TypeError, ET.ParseError):
        return []


def find_local_match(songs: list[dict], title: str, artist: str) -> dict | None:
    """The library song that is the same track as (title, artist), or None. An exact title beats a variant."""
    a = _norm(artist)
    for fuzzy in (False, True):
        t = norm_title(title) if fuzzy else _norm(title)
        for s in songs:
            lt = norm_title(s.get("title", "")) if fuzzy else _norm(s.get("title", ""))
            la = _norm(s.get("artist", ""))
            if lt == t and (not a or not la or a in la or la in a):
                return s
    return None


# ---------------------------------------------------- artists and albums
def norm(s: str) -> str:
    return _norm(s)


def make_artist(item: dict, prefix: str = "", album_count: int = 0) -> dict:
    """Subsonic artist dict for a remote artist: item = {id, name, cover}."""
    rid = remote_artist_id(item["id"])
    artist = {"id": rid, "name": f"{prefix}{item['name']}", "coverArt": rid}
    if album_count:
        artist["albumCount"] = album_count
    return artist


def make_album(item: dict, prefix: str = "", song_count: int = 0, duration: int = 0) -> dict:
    """Subsonic album dict for a remote album: item = {id, title, artist, artistId, year, cover}."""
    rid = remote_album_id(item["id"])
    title = f"{prefix}{item['title']}"
    album = {
        "id": rid, "parent": remote_artist_id(item["artistId"]) if item.get("artistId") else "yt",
        "isDir": True, "name": title, "title": title, "album": title,
        "artist": item.get("artist") or "", "coverArt": rid,
        "songCount": song_count, "duration": duration, "created": "2000-01-01T00:00:00Z",
    }
    if item.get("artistId"):
        album["artistId"] = remote_artist_id(item["artistId"])
    if item.get("year"):
        album["year"] = int(item["year"])
    return album


def entity(body: bytes, as_json: bool, key: str) -> dict | None:
    """Plain attributes of the <key> element (e.g. 'artist', 'album') of a successful response."""
    try:
        if as_json:
            resp = json.loads(body)["subsonic-response"]
            e = resp.get(key) if resp.get("status") == "ok" else None
            return {k: v for k, v in e.items() if not isinstance(v, (list, dict))} if isinstance(e, dict) else None
        root = ET.fromstring(body)
        if root.get("status") != "ok":
            return None
        return next((dict(e.attrib) for e in root if e.tag.endswith(key)), None)
    except (ValueError, KeyError, TypeError, AttributeError, ET.ParseError):
        return None


def merge_items(body: bytes, as_json: bool, key: str, tag: str, items: list[dict], keep, create: bool = True) -> bytes | None:
    """Append dicts to the <tag> list inside <key> of a successful response.

    keep(existing, item) says whether an item should be added, given the entries already there.
    create=False leaves the response alone when <key> is missing. None = upstream said no.
    """
    try:
        if as_json:
            data = json.loads(body)
            resp = data["subsonic-response"]
            if resp.get("status") != "ok":
                return None
            holder = resp.get(key)
            if holder is None:
                if not create:
                    return None
                holder = resp[key] = {}
            existing = holder.get(tag) or []
            holder[tag] = existing + [i for i in items if keep(existing, i)]
            return json.dumps(data).encode()
        root = ET.fromstring(body)
        if root.get("status") != "ok":
            return None
        ns = root.tag[: root.tag.index("}") + 1] if root.tag.startswith("{") else ""
        holder = next((e for e in root if e.tag == f"{ns}{key}"), None)
        if holder is None:
            if not create:
                return None
            holder = ET.SubElement(root, f"{ns}{key}")
        existing = [dict(e.attrib) for e in holder if e.tag == f"{ns}{tag}"]
        for item in items:
            if keep(existing, item):
                el = ET.SubElement(holder, f"{ns}{tag}")
                for k, v in item.items():
                    el.set(k, _scalar(v))
        return ET.tostring(root, encoding="utf-8", xml_declaration=True)
    except (ValueError, KeyError, TypeError, AttributeError, ET.ParseError):
        return None


def keep_artist(prefix: str = ""):
    def keep(existing: list[dict], item: dict) -> bool:
        name = norm(item["name"].removeprefix(prefix))
        return all(norm(e.get("name", "")) != name for e in existing)
    return keep


def keep_album(prefix: str = "", same_artist: bool = True):
    def keep(existing: list[dict], item: dict) -> bool:
        title, artist = norm(item["name"].removeprefix(prefix)), norm(item.get("artist", ""))
        for e in existing:
            if norm(e.get("name") or e.get("title", "")) != title:
                continue
            ea = norm(e.get("artist", ""))
            if not same_artist or not artist or not ea or artist in ea or ea in artist:
                return False
        return True
    return keep


def keep_song(prefix: str = ""):
    def keep(existing: list[dict], item: dict) -> bool:
        title = norm_title(item["title"].removeprefix(prefix))
        return all(norm_title(e.get("title", "")) != title for e in existing)
    return keep
