"""SQLite store for what selfstream remembers per user: playlists, listening history, follows,
genres, the weekly Discover list and small pieces of synced state (queue, settings).

Plain stdlib sqlite3 behind one lock. Every call is a few milliseconds, so it is used directly from async code.
"""
import json
import re
import sqlite3
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone

import subsonic

SCHEMA = """
CREATE TABLE IF NOT EXISTS tracks(
  key TEXT PRIMARY KEY, akey TEXT, artist TEXT, title TEXT, album TEXT, duration INTEGER,
  genres TEXT, song TEXT, updated REAL);
CREATE TABLE IF NOT EXISTS playlists(
  id TEXT PRIMARY KEY, user TEXT, name TEXT, description TEXT, kind TEXT, source TEXT,
  created REAL, updated REAL);
CREATE INDEX IF NOT EXISTS playlists_user ON playlists(user);
CREATE TABLE IF NOT EXISTS playlist_items(
  id INTEGER PRIMARY KEY AUTOINCREMENT, playlist_id TEXT, pos INTEGER, song_id TEXT, key TEXT,
  song TEXT, added REAL);
CREATE INDEX IF NOT EXISTS items_pl ON playlist_items(playlist_id, pos);
CREATE TABLE IF NOT EXISTS plays(
  id INTEGER PRIMARY KEY AUTOINCREMENT, user TEXT, key TEXT, song_id TEXT, ts REAL, seconds REAL,
  duration REAL, counted INTEGER, UNIQUE(user, ts, song_id));
CREATE INDEX IF NOT EXISTS plays_user_ts ON plays(user, ts);
CREATE TABLE IF NOT EXISTS follows(
  id TEXT PRIMARY KEY, user TEXT, kind TEXT, remote_id TEXT, name TEXT, cover TEXT, artist TEXT,
  playlist_id TEXT, status TEXT, total INTEGER, done INTEGER, failed INTEGER, created REAL, synced REAL,
  UNIQUE(user, kind, remote_id));
CREATE TABLE IF NOT EXISTS artist_genres(artist TEXT PRIMARY KEY, genres TEXT, source TEXT, updated REAL);
CREATE TABLE IF NOT EXISTS kv(user TEXT, k TEXT, v TEXT, updated REAL, PRIMARY KEY(user, k));
"""

COUNT_SECONDS = 30  # a play counts after this many seconds ...
COUNT_FRACTION = 0.5  # ... or after this share of the track, whichever comes first
RANGES = {"week": 7, "month": 30, "year": 365, "all": None}


def _first_artist(artist: str) -> str:
    return re.split(r"\s*(?:,|&|;|\bfeat\.?|\bft\.?|\bx\b)\s*", artist or "", maxsplit=1, flags=re.I)[0]


def artist_key(artist: str) -> str:
    return subsonic.norm(_first_artist(artist))


def track_key(artist: str, title: str) -> str:
    """Same song, same key: the library copy and the YouTube copy of a track share one."""
    return f"{artist_key(artist)}|{subsonic.norm_title(title)}"


def _now() -> float:
    return time.time()


def _scalars(song: dict) -> dict:
    """A song as the apps send it, reduced to plain values (keeps stored snapshots small and safe)."""
    keep = {}
    for k, v in (song or {}).items():
        if isinstance(v, (str, int, float, bool)) and len(str(k)) < 40:
            keep[k] = v if not isinstance(v, str) else v[:500]
    return keep


def split_genres(value) -> list[str]:
    if isinstance(value, list):
        parts = [g.get("name") if isinstance(g, dict) else g for g in value]
    else:
        parts = re.split(r"\s*[;/,]\s*", str(value or ""))
    out, seen = [], set()
    for p in parts:
        p = (p or "").strip()
        if p and p.lower() not in seen and len(p) < 40:
            seen.add(p.lower())
            out.append(p.title() if p.islower() else p)
    return out[:5]


class Store:
    def __init__(self, path: str):
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        with self.lock:
            self.db.executescript(SCHEMA)
            self.db.execute("PRAGMA journal_mode=WAL")
            self.db.commit()

    # ---------------------------------------------------------- helpers
    def q(self, sql: str, args=()) -> list[sqlite3.Row]:
        with self.lock:
            return self.db.execute(sql, args).fetchall()

    def x(self, sql: str, args=()) -> sqlite3.Cursor:
        with self.lock:
            cur = self.db.execute(sql, args)
            self.db.commit()
            return cur

    # ----------------------------------------------------------- tracks
    def remember(self, song: dict) -> str | None:
        """Keep a snapshot of a song (for stats and playlists). Returns its track key."""
        song = _scalars(song)
        title, artist = song.get("title"), song.get("artist")
        if not (title and artist and song.get("id")):
            return None
        key = track_key(artist, title)
        genres = split_genres(song.get("genre"))
        with self.lock:
            old = self.db.execute("SELECT genres, song FROM tracks WHERE key=?", (key,)).fetchone()
            if old:
                have = json.loads(old["genres"] or "[]")
                genres = genres or have
                prev = json.loads(old["song"] or "{}")
                # a library copy (not 'yt-') is the better snapshot: it has the real cover and tags
                if str(song["id"]).startswith("yt-") and prev.get("id") and not str(prev["id"]).startswith("yt-"):
                    song = prev
            self.db.execute(
                "INSERT INTO tracks(key, akey, artist, title, album, duration, genres, song, updated) "
                "VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(key) DO UPDATE SET album=excluded.album, "
                "duration=excluded.duration, genres=excluded.genres, song=excluded.song, updated=excluded.updated",
                (key, artist_key(artist), artist, title, song.get("album") or "", int(song.get("duration") or 0),
                 json.dumps(genres), json.dumps(song), _now()))
            self.db.commit()
        return key

    def track(self, key: str) -> dict | None:
        r = self.q("SELECT * FROM tracks WHERE key=?", (key,))
        if not r:
            return None
        return {**json.loads(r[0]["song"] or "{}"), "genres": json.loads(r[0]["genres"] or "[]")}

    def set_track_genres(self, key: str, genres: list[str]) -> None:
        self.x("UPDATE tracks SET genres=? WHERE key=?", (json.dumps(genres), key))

    # -------------------------------------------------------- playlists
    def _playlist_row(self, user: str, pid: str):
        r = self.q("SELECT * FROM playlists WHERE id=? AND user=?", (pid, user))
        return r[0] if r else None

    def _summary(self, r) -> dict:
        n = self.q("SELECT COUNT(*) c FROM playlist_items WHERE playlist_id=?", (r["id"],))[0]["c"]
        first = self.q("SELECT song FROM playlist_items WHERE playlist_id=? ORDER BY pos LIMIT 1", (r["id"],))
        song = json.loads(first[0]["song"]) if first else {}
        dur = sum(int(json.loads(i["song"]).get("duration") or 0)
                  for i in self.q("SELECT song FROM playlist_items WHERE playlist_id=?", (r["id"],)))
        return {"id": r["id"], "name": r["name"], "description": r["description"] or "", "kind": r["kind"],
                "source": r["source"] or "", "songCount": n, "duration": dur,
                "coverArt": song.get("coverArt", ""), "created": r["created"], "updated": r["updated"]}

    def playlists(self, user: str) -> list[dict]:
        rows = self.q("SELECT * FROM playlists WHERE user=? ORDER BY updated DESC", (user,))
        return [self._summary(r) for r in rows]

    def create_playlist(self, user: str, name: str, description: str = "", kind: str = "user",
                        source: str = "", songs: list[dict] | None = None) -> dict:
        pid = uuid.uuid4().hex[:12]
        now = _now()
        self.x("INSERT INTO playlists(id, user, name, description, kind, source, created, updated) VALUES(?,?,?,?,?,?,?,?)",
               (pid, user, (name or "Untitled").strip()[:120], description[:500], kind, source, now, now))
        if songs:
            self.add_songs(user, pid, songs)
        return self.playlist(user, pid)

    def playlist(self, user: str, pid: str) -> dict | None:
        r = self._playlist_row(user, pid)
        if not r:
            return None
        items = self.q("SELECT id, song FROM playlist_items WHERE playlist_id=? ORDER BY pos", (pid,))
        songs = [{**json.loads(i["song"]), "playlistItemId": i["id"]} for i in items]
        return {**self._summary(r), "songs": songs}

    def update_playlist(self, user: str, pid: str, name: str | None = None, description: str | None = None) -> bool:
        if not self._playlist_row(user, pid):
            return False
        if name is not None:
            self.x("UPDATE playlists SET name=? WHERE id=?", (name.strip()[:120] or "Untitled", pid))
        if description is not None:
            self.x("UPDATE playlists SET description=? WHERE id=?", (description[:500], pid))
        self.x("UPDATE playlists SET updated=? WHERE id=?", (_now(), pid))
        return True

    def delete_playlist(self, user: str, pid: str) -> bool:
        if not self._playlist_row(user, pid):
            return False
        with self.lock:
            self.db.execute("DELETE FROM playlist_items WHERE playlist_id=?", (pid,))
            self.db.execute("DELETE FROM playlists WHERE id=?", (pid,))
            self.db.execute("UPDATE follows SET playlist_id=NULL WHERE playlist_id=?", (pid,))
            self.db.commit()
        return True

    def add_songs(self, user: str, pid: str, songs: list[dict], skip_existing: bool = False) -> int:
        if not self._playlist_row(user, pid):
            return -1
        added = 0
        with self.lock:
            pos = self.db.execute("SELECT COALESCE(MAX(pos), -1) m FROM playlist_items WHERE playlist_id=?", (pid,)).fetchone()["m"]
            have = {r["key"] for r in self.db.execute("SELECT key FROM playlist_items WHERE playlist_id=?", (pid,))}
            for song in songs[:2000]:
                s = _scalars(song)
                if not s.get("id"):
                    continue
                key = self.remember(s) or f"id:{s['id']}"
                if skip_existing and key in have:
                    continue
                have.add(key)
                pos += 1
                self.db.execute("INSERT INTO playlist_items(playlist_id, pos, song_id, key, song, added) VALUES(?,?,?,?,?,?)",
                                (pid, pos, str(s["id"]), key, json.dumps(s), _now()))
                added += 1
            self.db.execute("UPDATE playlists SET updated=? WHERE id=?", (_now(), pid))
            self.db.commit()
        return added

    def remove_item(self, user: str, pid: str, item_id: int) -> bool:
        if not self._playlist_row(user, pid):
            return False
        cur = self.x("DELETE FROM playlist_items WHERE id=? AND playlist_id=?", (item_id, pid))
        self._compact(pid)
        return cur.rowcount > 0

    def reorder(self, user: str, pid: str, item_ids: list[int]) -> bool:
        if not self._playlist_row(user, pid):
            return False
        with self.lock:
            existing = [r["id"] for r in self.db.execute("SELECT id FROM playlist_items WHERE playlist_id=? ORDER BY pos", (pid,))]
            order = [i for i in item_ids if i in set(existing)]
            order += [i for i in existing if i not in set(order)]  # anything not mentioned keeps its place at the end
            for pos, i in enumerate(order):
                self.db.execute("UPDATE playlist_items SET pos=? WHERE id=?", (pos, i))
            self.db.execute("UPDATE playlists SET updated=? WHERE id=?", (_now(), pid))
            self.db.commit()
        return True

    def _compact(self, pid: str) -> None:
        with self.lock:
            for pos, r in enumerate(self.db.execute("SELECT id FROM playlist_items WHERE playlist_id=? ORDER BY pos", (pid,)).fetchall()):
                self.db.execute("UPDATE playlist_items SET pos=? WHERE id=?", (pos, r["id"]))
            self.db.execute("UPDATE playlists SET updated=? WHERE id=?", (_now(), pid))
            self.db.commit()

    # ------------------------------------------------------------ plays
    def record_play(self, user: str, song: dict, seconds: float, duration: float | None = None, ts: float | None = None) -> dict:
        """Log a listen. Safe to send twice (same user, ts and song id is stored once)."""
        key = self.remember(song)
        if not key:
            return {"ok": False, "error": "song needs id, title and artist"}
        duration = float(duration or song.get("duration") or 0)
        seconds = max(0.0, min(float(seconds or 0), (duration or 36000) * 1.05 + 5))
        ts = float(ts or _now())
        counted = int(seconds >= COUNT_SECONDS or (duration > 0 and seconds >= duration * COUNT_FRACTION and seconds >= 10))
        cur = self.x("INSERT OR IGNORE INTO plays(user, key, song_id, ts, seconds, duration, counted) VALUES(?,?,?,?,?,?,?)",
                     (user, key, str(song["id"]), ts, seconds, duration, counted))
        return {"ok": True, "key": key, "counted": bool(counted), "duplicate": cur.rowcount == 0}

    def history(self, user: str, limit: int = 50, offset: int = 0) -> list[dict]:
        rows = self.q("SELECT p.ts, p.seconds, p.counted, t.song FROM plays p JOIN tracks t ON t.key=p.key "
                      "WHERE p.user=? ORDER BY p.ts DESC LIMIT ? OFFSET ?", (user, limit, offset))
        return [{"playedAt": r["ts"], "seconds": r["seconds"], "counted": bool(r["counted"]), "song": json.loads(r["song"])} for r in rows]

    def recent_tracks(self, user: str, limit: int = 20) -> list[dict]:
        """Recently played, each track once, newest first."""
        rows = self.q("SELECT t.song, MAX(p.ts) last FROM plays p JOIN tracks t ON t.key=p.key WHERE p.user=? AND p.seconds>=10 "
                      "GROUP BY p.key ORDER BY last DESC LIMIT ?", (user, limit))
        return [json.loads(r["song"]) for r in rows]

    def played_keys(self, user: str) -> set[str]:
        return {r["key"] for r in self.q("SELECT DISTINCT key FROM plays WHERE user=? AND counted=1", (user,))}

    # ------------------------------------------------------------ stats
    def stats(self, user: str, rng: str = "month", tz_minutes: int = 0, now: float | None = None) -> dict:
        now = now or _now()
        days = RANGES.get(rng, 30)
        since = now - days * 86400 if days else 0
        tz = timezone(timedelta(minutes=tz_minutes))
        rows = self.q("SELECT p.key, p.ts, p.seconds, p.counted, t.artist, t.akey, t.album, t.genres, t.song FROM plays p "
                      "JOIN tracks t ON t.key=p.key WHERE p.user=? AND p.ts>=? ORDER BY p.ts", (user, since))
        total = sum(r["seconds"] for r in rows)
        counted = [r for r in rows if r["counted"]]
        by_track: dict[str, dict] = {}
        by_artist: dict[str, dict] = {}
        by_album: dict[tuple, dict] = {}
        by_genre: dict[str, dict] = {}
        weekday = [0.0] * 7
        hour = [0.0] * 24
        daily: dict[str, float] = {}
        for r in rows:
            local = datetime.fromtimestamp(r["ts"], tz)
            weekday[local.weekday()] += r["seconds"]
            hour[local.hour] += r["seconds"]
            day = local.strftime("%Y-%m-%d" if (days or 9999) <= 90 else "%Y-%m")
            daily[day] = daily.get(day, 0) + r["seconds"]
            t = by_track.setdefault(r["key"], {"seconds": 0.0, "plays": 0, "song": json.loads(r["song"])})
            t["seconds"] += r["seconds"]
            t["plays"] += r["counted"]
            a = by_artist.setdefault(r["akey"], {"name": _first_artist(r["artist"]), "seconds": 0.0, "plays": 0, "tracks": set(),
                                                 "coverArt": json.loads(r["song"]).get("coverArt", "")})
            a["seconds"] += r["seconds"]
            a["plays"] += r["counted"]
            a["tracks"].add(r["key"])
            if r["album"]:
                b = by_album.setdefault((r["akey"], subsonic.norm(r["album"])),
                                        {"name": r["album"], "artist": _first_artist(r["artist"]), "seconds": 0.0, "plays": 0,
                                         "coverArt": json.loads(r["song"]).get("coverArt", ""),
                                         "albumId": json.loads(r["song"]).get("albumId", "")})
                b["seconds"] += r["seconds"]
                b["plays"] += r["counted"]
            for g in json.loads(r["genres"] or "[]"):
                gg = by_genre.setdefault(g.lower(), {"name": g, "seconds": 0.0, "plays": 0})
                gg["seconds"] += r["seconds"]
                gg["plays"] += r["counted"]

        def top(d, n=20, key="seconds"):
            items = sorted(d.values(), key=lambda v: (v[key], v["seconds"]), reverse=True)[:n]
            for it in items:
                if isinstance(it.get("tracks"), set):
                    it["tracks"] = len(it["tracks"])
                it["seconds"] = round(it["seconds"])
            return items

        # artists first heard inside the range (new discoveries)
        before = {r["akey"] for r in self.q("SELECT DISTINCT t.akey FROM plays p JOIN tracks t ON t.key=p.key WHERE p.user=? AND p.ts<?", (user, since))} if days else set()
        day_set = sorted({datetime.fromtimestamp(r["ts"], tz).date() for r in counted})
        longest = cur = 0
        prev = None
        for d in day_set:
            cur = cur + 1 if prev and (d - prev).days == 1 else 1
            longest = max(longest, cur)
            prev = d
        today = datetime.fromtimestamp(now, tz).date()
        streak = 0
        d = today if today in day_set else today - timedelta(days=1)
        while d in set(day_set):
            streak += 1
            d -= timedelta(days=1)
        skipped = sum(1 for r in rows if not r["counted"] and r["seconds"] < COUNT_SECONDS)
        span_days = max(1, days or (len(day_set) and (day_set[-1] - day_set[0]).days + 1) or 1)
        return {
            "range": rng if rng in RANGES else "month",
            "totalSeconds": round(total),
            "totalHours": round(total / 3600, 1),
            "plays": len(counted),
            "uniqueTracks": len({r["key"] for r in counted}),
            "uniqueArtists": len({r["akey"] for r in counted}),
            "newArtists": len({r["akey"] for r in counted} - before),
            "activeDays": len(day_set),
            "avgMinutesPerDay": round(total / 60 / span_days, 1),
            "skipRate": round(skipped / len(rows), 2) if rows else 0,
            "currentStreak": streak,
            "longestStreak": longest,
            "topTracks": [{"song": v["song"], "plays": v["plays"], "seconds": round(v["seconds"])} for v in top(by_track, 20, "plays")],
            "topArtists": top(by_artist, 20),
            "topAlbums": top(by_album, 20),
            "topGenres": top(by_genre, 12),
            "byWeekday": [round(s) for s in weekday],
            "byHour": [round(s) for s in hour],
            "series": [{"day": k, "seconds": round(v)} for k, v in sorted(daily.items())],
            "lifetimeHours": round(self.q("SELECT COALESCE(SUM(seconds),0) s FROM plays WHERE user=?", (user,))[0]["s"] / 3600, 1),
        }

    # ------------------------------------------------------------ taste
    def taste(self, user: str, now: float | None = None, days: int = 120, half_life: float = 30.0) -> dict:
        """What the user likes right now: tracks, artists and genres weighted by listening time, newer listens counting more."""
        now = now or _now()
        rows = self.q("SELECT p.key, p.ts, p.seconds, p.counted, t.akey, t.artist, t.genres, t.song FROM plays p "
                      "JOIN tracks t ON t.key=p.key WHERE p.user=? AND p.ts>=?", (user, now - days * 86400))
        tracks: dict[str, float] = {}
        artists: dict[str, float] = {}
        names: dict[str, str] = {}
        genres: dict[str, float] = {}
        songs: dict[str, dict] = {}
        for r in rows:
            age = (now - r["ts"]) / 86400
            w = r["seconds"] * (0.5 ** (age / half_life))
            if not r["counted"]:
                w *= -0.3 if r["seconds"] < 10 else 0.2  # an instant skip is a (weak) dislike
            tracks[r["key"]] = tracks.get(r["key"], 0) + w
            artists[r["akey"]] = artists.get(r["akey"], 0) + w
            names[r["akey"]] = _first_artist(r["artist"])
            songs[r["key"]] = json.loads(r["song"])
            for g in json.loads(r["genres"] or "[]"):
                genres[g.lower()] = genres.get(g.lower(), 0) + w
        return {"tracks": tracks, "artists": artists, "artistNames": names, "genres": genres, "songs": songs}

    # ----------------------------------------------------------- genres
    def artist_genres(self, artist: str) -> list[str] | None:
        r = self.q("SELECT genres FROM artist_genres WHERE artist=?", (artist_key(artist),))
        return json.loads(r[0]["genres"]) if r else None

    def set_artist_genres(self, artist: str, genres: list[str], source: str) -> None:
        self.x("INSERT INTO artist_genres(artist, genres, source, updated) VALUES(?,?,?,?) "
               "ON CONFLICT(artist) DO UPDATE SET genres=excluded.genres, source=excluded.source, updated=excluded.updated",
               (artist_key(artist), json.dumps(genres), source, _now()))

    def tracks_without_genres(self, limit: int = 30) -> list[dict]:
        rows = self.q("SELECT key, artist, title, genres FROM tracks WHERE genres='[]' OR genres IS NULL ORDER BY updated DESC LIMIT ?", (limit,))
        return [dict(r) for r in rows]

    def apply_artist_genres(self) -> int:
        """Give every track without genres the genres known for its artist."""
        n = 0
        for t in self.tracks_without_genres(5000):
            g = self.artist_genres(t["artist"])
            if g:
                self.set_track_genres(t["key"], g)
                n += 1
        return n

    # ---------------------------------------------------------- follows
    def follow(self, user: str, kind: str, remote_id: str, name: str, cover: str = "", artist: str = "") -> dict:
        row = self.q("SELECT * FROM follows WHERE user=? AND kind=? AND remote_id=?", (user, kind, remote_id))
        if row:
            return dict(row[0])
        fid = uuid.uuid4().hex[:12]
        self.x("INSERT INTO follows(id, user, kind, remote_id, name, cover, artist, status, total, done, failed, created) "
               "VALUES(?,?,?,?,?,?,?,?,0,0,0,?)", (fid, user, kind, remote_id, name[:200], cover, artist[:200], "queued", _now()))
        return self.get_follow(user, fid)

    def get_follow(self, user: str, fid: str) -> dict | None:
        r = self.q("SELECT * FROM follows WHERE id=? AND user=?", (fid, user))
        return dict(r[0]) if r else None

    def follows(self, user: str) -> list[dict]:
        return [dict(r) for r in self.q("SELECT * FROM follows WHERE user=? ORDER BY created DESC", (user,))]

    def update_follow(self, fid: str, **fields) -> None:
        if fields:
            cols = ", ".join(f"{k}=?" for k in fields)
            self.x(f"UPDATE follows SET {cols} WHERE id=?", (*fields.values(), fid))

    def unfollow(self, user: str, fid: str, delete_playlist: bool = False) -> bool:
        f = self.get_follow(user, fid)
        if not f:
            return False
        self.x("DELETE FROM follows WHERE id=?", (fid,))
        if delete_playlist and f.get("playlist_id"):
            self.delete_playlist(user, f["playlist_id"])
        return True

    def open_follows(self, now: float | None = None, resync_days: float = 7) -> list[dict]:
        """Follows to work on: new ones, interrupted ones, and artists/playlists not checked for new songs in a while."""
        cutoff = (now or _now()) - resync_days * 86400
        return [dict(r) for r in self.q(
            "SELECT * FROM follows WHERE status IN ('queued','working') "
            "OR (status='done' AND kind IN ('artist','playlist') AND COALESCE(synced,0)<?) ORDER BY created", (cutoff,))]

    # --------------------------------------------------------- kv state
    def get_state(self, user: str, key: str):
        r = self.q("SELECT v, updated FROM kv WHERE user=? AND k=?", (user, key))
        return {"value": json.loads(r[0]["v"]), "updated": r[0]["updated"]} if r else None

    def set_state(self, user: str, key: str, value) -> float:
        now = _now()
        self.x("INSERT INTO kv(user, k, v, updated) VALUES(?,?,?,?) ON CONFLICT(user,k) DO UPDATE SET v=excluded.v, updated=excluded.updated",
               (user, key, json.dumps(value), now))
        return now
