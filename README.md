# selfstream

A self-hosted music server that combines **Navidrome** (library + Subsonic/OpenSubsonic API), **yt-dlp** (search and download) and **LRCLIB** (synced `.lrc` lyrics) behind a single port.

- Subsonic clients (Musly, Symfonium, DSub, Feishin, ...) connect to `http://<host>:8080` exactly as they would to Navidrome.
- **Search finds YouTube tracks too.** Results that aren't in your library are listed after your own. Play one and it streams straight from YouTube (seeking works); **heart it to download** it into your library, with tags and synced lyrics.
- `http://<host>:8080/selfstream` is a small page to search YouTube or paste a URL. Downloads are saved as opus to `/music/<Artist>/<Title>` with a synced `.lrc` next to them, and Navidrome picks them up within a minute.

## The Selfstream app (Android)

`client/` is a Flutter app that replaces Vici. Every build is signed and published as the **android-latest** release
on GitHub (Actions tab -> "android apk"). Install the `.apk`, enter `http://<server>:8080` and your Navidrome login.

What it does:

- **Search** your library and YouTube Music (songs, artists, albums, playlists); play anything instantly.
- **Heart** a song to download it into Navidrome. **Follow** an album, artist or playlist: it becomes its own
  playlist and every song is downloaded in the background (progress in Library -> Following).
- **Playlists on the server**: the same on every device you sign in on. The play queue and position follow you too.
- **Activity**: hours listened, top songs, artists, albums and genres, listening by weekday and hour, streaks.
- **Discover**: a weekly mix of songs you have not played, built from your listening time, skips and genres.
- Synced lyrics, queue editing, sleep timer, speed, offline saves, streaming quality, lock-screen controls.

The server keeps its data in `selfstream.db` next to the state file (`DB_FILE` to move it). Genres come from your
library tags, then Deezer and MusicBrainz (looked up once per artist in the background, no keys needed).

The app code is split so other platforms can reuse it: `lib/core` (API + models, no Flutter), `lib/state`
(player, library, session) and `lib/ui` (screens).

## Install on Proxmox VE (recommended)

Run this in the **Proxmox host shell** (Datacenter > your node > Shell):

```bash
bash -c "$(curl -fsSL https://raw.githubusercontent.com/TaiDies/selfstream/main/ct/selfstream.sh)"
```

It creates an unprivileged Debian 12 LXC (2 cores, 1 GB RAM, 8 GB root disk, 20 GB music volume by default) and installs everything natively inside it. No Docker involved.

Pick *Default* or *Advanced* in the dialog, or skip the dialog and set values with environment variables:

```bash
SS_DEFAULTS=1 CORES=4 RAM=2048 MUSIC_SIZE=200 IP=192.168.1.50/24 GW=192.168.1.1 \
  bash -c "$(curl -fsSL https://raw.githubusercontent.com/TaiDies/selfstream/main/ct/selfstream.sh)"
```

| Variable | Default | Meaning |
|---|---|---|
| `CTID` | next free ID | container ID |
| `CT_HOSTNAME` | `selfstream` | hostname |
| `CORES` / `RAM` / `SWAP` | `2` / `1024` / `512` | resources (MB for RAM/swap) |
| `DISK` | `8` | root disk, GB |
| `MUSIC_SIZE` | `20` | separate music volume, GB |
| `MUSIC_BIND` | empty | bind-mount a host folder at `/music` instead (e.g. your NAS share) |
| `BRIDGE` | `vmbr0` | network bridge |
| `IP` / `GW` | `dhcp` / empty | static IP as CIDR plus gateway |
| `CT_STORAGE` / `TEMPLATE_STORAGE` | auto-detected | where the disk / template live |
| `DEBIAN_VERSION` | `12` | template to use |

**Bind-mounting an existing music folder:** the container is unprivileged, so its app user (uid 1000) is uid `101000` on the host. Run `chown -R 101000:101000 /your/music/path` on the host so downloads can be written.

### Update

```bash
pct exec <CTID> -- update
```

This upgrades Navidrome, yt-dlp, Deno and the selfstream app. yt-dlp also updates itself every 24 hours. (Deno is the JavaScript runtime yt-dlp now needs to read YouTube.)

### Logs and config

```bash
pct exec <CTID> -- journalctl -u navidrome -u selfstream -f
pct exec <CTID> -- nano /etc/selfstream/selfstream.env    # then: systemctl restart navidrome selfstream
```

## Install with Docker (alternative)

```bash
git clone https://github.com/TaiDies/selfstream.git && cd selfstream/docker
mkdir -p data && docker compose up -d --build
```

Edit the music path in `docker/docker-compose.yml` first (default `/srv/music`).

## First use

1. Open `http://<host>:8080` and create the Navidrome admin account.
2. Add the same address and login to your Subsonic app.
3. Search for a song that isn't in your library. YouTube results appear after your own.
4. Play one to stream it. Heart (star) it to download it into your library and add it to your liked songs; the YouTube copy disappears from search once the download finishes, and the library copy takes over the like.

## How remote tracks work

| In your app | What happens |
|---|---|
| Search | Navidrome's results plus up to 8 **YouTube Music songs** (real artist, album and square cover; ids look like `yt-<videoId>`). Empty searches (used by apps to sync the library) and page 2+ never include YouTube. If YouTube Music search is unavailable it falls back to plain YouTube results. |
| Artists and albums | Search also lists YouTube Music **artists and albums** you don't have. Open one to see its albums and tracks and play them. Artist and album pages of music you *do* have gain the albums and tracks the library is missing, so you can stream the rest. |
| Play | Audio is resolved with yt-dlp and passed through as m4a/AAC. Expect 1-3 s before it starts. |
| Heart (star) | Remembers the like (it shows in the app's liked/favourites list straight away), downloads the track into `/music` with the same title, artist, album and cover art the search showed, and fetches synced lyrics from LRCLIB. When the file appears in the library, that copy is hearted for you with *your* login (kept in memory only) and the cloud entry is dropped. Un-hearting forgets the like but never deletes a file. |
| Download button | Some apps have their own offline download: for a cloud track it is saved into the library first, then sent to the app. |
| Lyrics / cover art | Served for cloud tracks too (apps that use the OpenSubsonic lyrics call get synced lyrics). |

Only users Navidrome accepts can use cloud tracks; the proxy checks the credentials with Navidrome first.

### Settings (`/etc/selfstream/selfstream.env`)

| Variable | Default | Meaning |
|---|---|---|
| `REMOTE_ENABLED` | `1` | `0` turns YouTube results off completely |
| `REMOTE_RESULTS` | `8` | cloud songs per search |
| `REMOTE_BROWSE` | `1` | `0` turns off YouTube artists/albums in search and on artist/album pages |
| `REMOTE_ARTISTS` / `REMOTE_ALBUMS` | `3` / `5` | cloud artists / albums per search |
| `SEARCH_SOURCE` | `ytmusic` | `ytmusic` = songs only via YouTube Music; `youtube` = plain video search |
| `REMOTE_TITLE_PREFIX` | empty | optional marker in front of cloud track titles, e.g. `"☁ "` |
| `REMOTE_MAX_SECONDS` | `900` | skip results longer than this (mixes, podcasts) |
| `REMOTE_WAIT_SECONDS` | `8` | how long a search waits for YouTube before answering with library results only |
| `AUDIO_FORMAT` | `opus` | format of downloaded files |
| `PROXY_LOG` | `1` | log every Subsonic request (passwords are hidden) |
| `CORS_ALLOW_ORIGIN` | `*` | CORS header for browser-based apps (comma-separated origins, empty = off) |
| `EMBED_COVER` | `1` | embed a square cover image into downloaded files (`0` = off) |
| `YTDLP_EXTRA_ARGS` | empty | extra yt-dlp options, e.g. `--cookies /var/lib/navidrome/cookies.txt` |

Apply changes with `systemctl restart navidrome selfstream` inside the container.

## Known limits

- With `SEARCH_SOURCE=youtube` (or when YouTube Music search fails) results are plain YouTube **videos**, so covers, live versions and lyric videos can show up next to the studio track.
- Artist and album pages are matched to YouTube Music by name, so an artist or album with an unusual spelling may not get extra content. Top-songs and similar-artist lists are not extended.
- Liked cloud tracks are stored in `selfstream-state.json` next to Navidrome's data (`STATE_FILE` to change it).
- Apps that sync the whole library and search it offline only on the device never ask the server to search, so they won't show cloud tracks.
- YouTube changes often. If streaming or downloads stop working, run `pct exec <CTID> -- update` first; failures are logged in the journal.
- The `/selfstream` page has no login. Keep port 8080 on your home network and don't expose it to the internet.

## Troubleshooting

- **Streaming or downloads fail:** open `http://<host>:8080/selfstream/api/diagnose`. It shows the yt-dlp and Deno versions and runs one real YouTube lookup with the verbose log, so you can see why it fails (missing JavaScript runtime, YouTube asking for a sign-in, ...). If YouTube asks you to sign in, export your browser cookies to a `cookies.txt` file and set `YTDLP_EXTRA_ARGS="--cookies /var/lib/navidrome/cookies.txt"`.
- **Search shows plain videos instead of songs:** the same diagnose page has a `ytmusic_search` entry; if it says `ok: false` the error is shown there (the app then falls back to plain YouTube results).
- **A Subsonic app misbehaves:** `pct exec <CTID> -- journalctl -u selfstream -n 80 --no-pager` shows each request the app made and how it was answered (`PROXY_LOG=1`).

## Development

```bash
python3 -m unittest discover -s tests -v    # needs only Python 3.11+ and starlette; no network or yt-dlp
```

## Legal

Downloading from YouTube and similar sites may violate their terms and local copyright law. Only download content you have the right to.
