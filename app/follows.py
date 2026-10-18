"""Following (liking) an album, artist or playlist: it becomes a playlist of its own that keeps itself up to date.
The songs stream from YouTube Music; they are only downloaded into the library when FOLLOW_DOWNLOADS=1.
Runs in the background; progress is stored on the follow so apps can show it.
"""
import asyncio
import time

import core
import store as store_mod
import subsonic

KINDS = ("album", "artist", "playlist")
MAX_ARTIST_ALBUMS = 40
BATCH = 3  # downloads in flight at once (core limits the real yt-dlp concurrency further)
_running: set[str] = set()


async def collect(kind: str, remote_id: str) -> dict | None:
    """{name, cover, artist, tracks: [meta]} for a remote album, artist or playlist."""
    if kind == "album":
        a = await core.remote_album(remote_id)
        return {"name": a["title"], "cover": a["cover"], "artist": a["artist"], "tracks": a["tracks"]} if a else None
    if kind == "playlist":
        p = await core.remote_playlist(remote_id, wait=None)
        return {"name": p["title"], "cover": p["cover"], "artist": p["author"], "tracks": p["tracks"]} if p else None
    if kind == "artist":
        page = await core.remote_artist(remote_id)
        if not page:
            return None
        tracks, seen = [], set()
        # albums first (newest last in YouTube's list, so keep its order), singles after; the same song on an album and
        # as a single is taken once
        for album in page["albums"][:MAX_ARTIST_ALBUMS]:
            full = await core.remote_album(album["id"])
            for t in (full or {}).get("tracks", []):
                artist, title = subsonic.track_names(t)
                key = store_mod.track_key(artist, title)
                if key not in seen:
                    seen.add(key)
                    tracks.append(t)
        return {"name": page["name"], "cover": page["cover"], "artist": page["name"], "tracks": tracks}
    return None


def _id_of(kind: str, remote: str) -> str:
    return {"album": subsonic.parse_album_id, "artist": subsonic.parse_artist_id}.get(kind, lambda v: v[3:] if v.startswith("yp-") else v)(remote) or remote


async def run(st: "store_mod.Store", f: dict) -> None:
    fid = f["id"]
    if fid in _running:
        return
    _running.add(fid)
    try:
        st.update_follow(fid, status="working")
        data = await collect(f["kind"], _id_of(f["kind"], f["remote_id"]))
        if not data or not data["tracks"]:
            st.update_follow(fid, status="error")
            return
        tracks = data["tracks"]
        pid = f.get("playlist_id")
        if not pid or not st.playlist(f["user"], pid):
            label = {"album": data["name"], "artist": data["name"], "playlist": data["name"]}[f["kind"]]
            pid = st.create_playlist(f["user"], label, kind="follow", source=f"{f['kind']}:{f['remote_id']}",
                                     description={"album": f"Album by {data['artist']}", "artist": "All songs by this artist",
                                                  "playlist": f"Playlist by {data['artist']}" if data["artist"] else "Playlist"}[f["kind"]])["id"]
        st.add_songs(f["user"], pid, [subsonic.make_song(t) for t in tracks], skip_existing=True)
        st.update_follow(fid, playlist_id=pid, total=len(tracks), name=data["name"] or f["name"],
                         cover=f.get("cover") or data["cover"], artist=data["artist"])
        done = failed = 0
        if not core.FOLLOW_DOWNLOADS:
            st.update_follow(fid, status="done", synced=time.time(), done=0, failed=0)
            return
        for i in range(0, len(tracks), BATCH):
            if not st.get_follow(f["user"], fid):
                return  # unfollowed meanwhile
            chunk = tracks[i:i + BATCH]
            jobs = []
            for t in chunk:
                if t["id"] in core.done_video_ids():
                    done += 1
                else:
                    jobs.append(core.start_ingest(core.watch_url(t["id"]), t["id"]))
            for job in jobs:
                await core.job_events[job["id"]].wait()
                if job["status"] == "done":
                    done += 1
                else:
                    failed += 1
            st.update_follow(fid, done=done, failed=failed)
        st.update_follow(fid, status="done", synced=time.time(), done=done, failed=failed)
    except Exception as exc:
        core.log(f"follow {fid} failed: {type(exc).__name__}: {exc}")
        st.update_follow(fid, status="error")
    finally:
        _running.discard(fid)


async def worker(st: "store_mod.Store", interval: float = 10.0) -> None:
    while True:
        try:
            for f in st.open_follows():
                if f["id"] not in _running:
                    asyncio.create_task(run(st, f))
        except Exception as exc:
            core.log(f"follow worker: {type(exc).__name__}: {exc}")
        await asyncio.sleep(interval)
