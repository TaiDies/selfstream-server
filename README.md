# selfstream

A self-hosted music server that combines **Navidrome** (library + Subsonic/OpenSubsonic API), **yt-dlp** (search and download) and **LRCLIB** (synced `.lrc` lyrics) behind a single port.

- Subsonic clients (Musly, Symfonium, DSub, Feishin, ...) connect to `http://<host>:8080` exactly as they would to Navidrome.
- **Search finds YouTube tracks too.** Results that aren't in your library are marked with a cloud. Play one and it streams straight from YouTube (seeking works); **heart it to download** it into your library, with tags and synced lyrics.
- `http://<host>:8080/selfstream` is a small page to search YouTube or paste a URL. Downloads are saved as opus to `/music/<Artist>/<Title>` with a synced `.lrc` next to them, and Navidrome picks them up within a minute.

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
3. Search for a song that isn't in your library. YouTube results appear after your own, prefixed with a cloud.
4. Play one to stream it. Heart (star) it to download it into your library; the cloud copy disappears from search once the download finishes.

## How remote tracks work

| In your app | What happens |
|---|---|
| Search | Navidrome's results plus up to 8 YouTube hits (ids look like `yt-<videoId>`). Empty searches (used by apps to sync the library) and page 2+ never include YouTube. |
| Play | Audio is resolved with yt-dlp and passed through as m4a/AAC. Expect 1-3 s before it starts. |
| Heart (star) | Downloads the track into `/music`, tags it, fetches synced lyrics from LRCLIB. Un-hearting a cloud track does nothing. |
| Download button | Some apps have their own offline download: for a cloud track it is saved into the library first, then sent to the app. |
| Lyrics / cover art | Served for cloud tracks too (apps that use the OpenSubsonic lyrics call get synced lyrics). |

Only users Navidrome accepts can use cloud tracks; the proxy checks the credentials with Navidrome first.

### Settings (`/etc/selfstream/selfstream.env`)

| Variable | Default | Meaning |
|---|---|---|
| `REMOTE_ENABLED` | `1` | `0` turns YouTube results off completely |
| `REMOTE_RESULTS` | `8` | YouTube results per search |
| `REMOTE_TITLE_PREFIX` | cloud + space | marker in front of cloud track titles (empty = none) |
| `REMOTE_MAX_SECONDS` | `900` | skip results longer than this (mixes, podcasts) |
| `REMOTE_WAIT_SECONDS` | `8` | how long a search waits for YouTube before answering with library results only |
| `AUDIO_FORMAT` | `opus` | format of downloaded files |
| `PROXY_LOG` | `1` | log every Subsonic request (passwords are hidden) |
| `YTDLP_EXTRA_ARGS` | empty | extra yt-dlp options, e.g. `--cookies /var/lib/navidrome/cookies.txt` |

Apply changes with `systemctl restart navidrome selfstream` inside the container.

## Known limits

- Cloud tracks are YouTube **videos**, so you will see covers, live versions and lyric videos next to the studio track.
- Apps that sync the whole library and search it offline only on the device never ask the server to search, so they won't show cloud tracks.
- YouTube changes often. If streaming or downloads stop working, run `pct exec <CTID> -- update` first; failures are logged in the journal.
- The `/selfstream` page has no login. Keep port 8080 on your home network and don't expose it to the internet.

## Development

```bash
python3 -m unittest discover -s tests -v    # needs only Python 3.11+ and starlette; no network or yt-dlp
```

## Legal

Downloading from YouTube and similar sites may violate their terms and local copyright law. Only download content you have the right to.
