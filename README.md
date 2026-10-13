# selfstream

A self-hosted music server that combines **Navidrome** (library + Subsonic/OpenSubsonic API), **yt-dlp** (search and download) and **LRCLIB** (synced `.lrc` lyrics) behind a single port.

- Subsonic clients connect to `http://<host>:8080` exactly as they would to Navidrome.
- `http://<host>:8080/selfstream` is a small page to search YouTube or paste a URL. The track is downloaded as opus, tagged, saved to `/music/<Artist>/<Title>`, and synced lyrics are saved next to it. Navidrome picks it up within a minute.

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

This upgrades Navidrome, yt-dlp and the selfstream app. yt-dlp also updates itself every 24 hours.

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
2. Add the same address to your Subsonic client (Sonawave included, if it speaks Subsonic).
3. Open `http://<host>:8080/selfstream`, search a song and hit Download.

## Not built yet

- Streaming a remote track while it downloads (the proxy currently passes Subsonic traffic through unchanged).
- Embedded cover art.

## Legal

Downloading from YouTube and similar sites may violate their terms and local copyright law. Only download content you have the right to.
