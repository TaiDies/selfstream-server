# selfstream

A self-hosted music server that combines **Navidrome** (library + Subsonic/OpenSubsonic API), **yt-dlp** (search and download) and **LRCLIB** (synced `.lrc` lyrics) behind a single port.

- Subsonic clients (Musly, Symfonium, DSub, Feishin, ...) connect to `http://<host>:8080` exactly as they would to Navidrome.
- **Search finds YouTube tracks too.** Results that aren't in your library are listed after your own. Play one and it streams straight from YouTube (seeking works); **heart it to download** it into your library, with tags and synced lyrics.
- `http://<host>:8080/selfstream` is a small page to search YouTube or paste a URL. Downloads are saved as opus to `/music/<Artist>/<Title>` with a synced `.lrc` next to them, and Navidrome picks them up within a minute.

## Clients

Any Subsonic client works. For the extra features (playlists, follows, Discover, Activity, search suggestions,
listening history, queue sync) use the **Selfstream app**, which lives in its own repository:
<https://github.com/TaiDies/selfstream>. The extension API the app uses is documented there in `docs/SERVER.md`.

The server keeps its data in `selfstream.db` next to the state file (`DB_FILE` to move it). Genres come from your
library tags, then Deezer and MusicBrainz (looked up once per artist in the background, no keys needed).

## Discord "now playing" (optional)

Clients that support it (the Selfstream Windows app) can show the song, artist, cover and an **Open** button on the
user's Discord profile. The server supplies the card, so all users share one Discord application:

1. Create an application at <https://discord.com/developers/applications> (its name appears as "Playing <name>") and copy its **Application ID**.
2. Add `DISCORD_CLIENT_ID=<that id>` to `/etc/selfstream/selfstream.env` and run `systemctl restart selfstream`.

The Proxmox installer asks for this ID during setup (you can leave it empty and add it later). Without an ID the server does not offer the feature and the app shows the setting greyed out.

## Install on Proxmox VE (recommended)

Run this in the **Proxmox host shell** (Datacenter > your node > Shell):

```bash
bash -c "$(curl -fsSL https://raw.githubusercontent.com/TaiDies/selfstream-server/main/ct/selfstream.sh)"
```

It creates an unprivileged Debian 12 LXC (2 cores, 1 GB RAM, 8 GB root disk, 20 GB music volume by default) and installs everything natively inside it. No Docker involved.

Pick *Default* or *Advanced* in the dialog, or skip the dialog and set values with environment variables:

```bash
SS_DEFAULTS=1 CORES=4 RAM=2048 MUSIC_SIZE=200 IP=192.168.1.50/24 GW=192.168.1.1 \
  bash -c "$(curl -fsSL https://raw.githubusercontent.com/TaiDies/selfstream-server/main/ct/selfstream.sh)"
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
git clone https://github.com/TaiDies/selfstream-server.git && cd selfstream-server/docker
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
| `ALLOW_LIKE_DOWNLOAD` | `1` | `0` = hearting a YouTube track never downloads it, and the apps hide the "like to download on the server" switch |
| `YTDLP_EXTRA_ARGS` | empty | extra yt-dlp options, e.g. `--cookies /var/lib/navidrome/cookies.txt` |

### Admin page

Open `http://<host>:8080/selfstream/admin` and sign in with a **Navidrome administrator** account (the login is checked
against Navidrome; other accounts are refused). There you can change the settings above without editing files or
restarting: YouTube search, what liking and following download, the download format, the Discord ID, and more. You can
also paste a YouTube `cookies.txt`, see the yt-dlp version and update it.

Changes apply at once and are saved in `/var/lib/navidrome/selfstream-settings.json`; they win over
`/etc/selfstream/selfstream.env`, and "Reset" on a setting goes back to the env value. The sign-in lasts 8 hours and
wrong passwords are rate limited. The login is sent as a normal web request, so put the server behind HTTPS (for example a
Cloudflare Tunnel) if you open the admin page from outside your network.

Apply changes with `systemctl restart navidrome selfstream` inside the container.

## Android test builds (optional)

While you test the Android app before it is on Google Play, the server can hand the newest build to the app so it
can update itself. The app compares the build number in the file name (`selfstream-0.3.0-12.apk`) with its own and
offers to install a newer one. Builds from Google Play are updated by Google Play and ignore this.

**Automatic (recommended).** `update` downloads the newest APK from the Android releases of a private GitHub repo.
Once, on the server:

1. On GitHub create a *fine-grained personal access token* limited to that one repo with **read-only** access to
   *Contents* (Metadata is added automatically), and a long expiry.
2. Create `/etc/selfstream/android-update.conf` and lock it down:

   ```
   REPO=<owner>/<private repo>
   TOKEN=<the token>
   ```

   `chmod 600 /etc/selfstream/android-update.conf`

From then on every `update` puts the highest-numbered `selfstream-<version>-<build>.apk` into `/var/lib/selfstream/app`
(older builds are removed). Without the file nothing is downloaded and nothing breaks.

**By hand.** Copy an APK named like the above into `/var/lib/selfstream/app`; the highest build wins.

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
