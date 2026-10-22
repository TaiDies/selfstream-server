#!/usr/bin/env bash
# selfstream installer - runs INSIDE the Debian LXC (called by ct/selfstream.sh).
# SELFSTREAM_MODE=update re-runs only the download/upgrade steps.
set -Eeuo pipefail
export DEBIAN_FRONTEND=noninteractive

REPO="${SELFSTREAM_REPO:?SELFSTREAM_REPO not set}"
BRANCH="${SELFSTREAM_BRANCH:-main}"
MODE="${SELFSTREAM_MODE:-install}"
FALLBACK_NAVIDROME="0.54.5"

GN=$'\033[32m'; RD=$'\033[31m'; CL=$'\033[0m'
msg() { echo -e "${GN}  ->${CL} $*"; }
die() { echo -e "${RD}ERROR:${CL} $*" >&2; exit 1; }
trap 'die "failed at line $LINENO"' ERR

install_deps() {
  msg "Installing packages"
  apt-get update -qq
  apt-get install -y -qq ffmpeg python3 python3-venv python3-mutagen curl ca-certificates tar xz-utils >/dev/null
}

create_user() {
  id selfstream &>/dev/null || useradd -m -u 1000 -s /usr/sbin/nologin selfstream
  mkdir -p /music /var/lib/navidrome /opt/navidrome /opt/selfstream /etc/selfstream /var/lib/selfstream/app
  chown selfstream:selfstream /music /var/lib/navidrome 2>/dev/null \
    || echo "  (could not chown /music - fix permissions from the Proxmox host if it is a bind mount)"
}

install_navidrome() {
  local arch ver
  case "$(dpkg --print-architecture)" in
    amd64) arch=amd64 ;;
    arm64) arch=arm64 ;;
    *) die "unsupported CPU architecture: $(dpkg --print-architecture)" ;;
  esac
  ver=$(curl -fsSL https://api.github.com/repos/navidrome/navidrome/releases/latest \
        | grep -oP '"tag_name":\s*"v\K[^"]+' || true)
  ver="${ver:-$FALLBACK_NAVIDROME}"
  msg "Installing Navidrome ${ver}"
  # download and unpack aside first: a cut-off download must never replace the working binary
  local tmp
  tmp=$(mktemp -d)
  curl -fsSL "https://github.com/navidrome/navidrome/releases/download/v${ver}/navidrome_${ver}_linux_${arch}.tar.gz" -o "${tmp}/nd.tar.gz"
  tar -xzf "${tmp}/nd.tar.gz" -C "$tmp" navidrome
  install -m 755 "${tmp}/navidrome" /opt/navidrome/navidrome.new
  mv -f /opt/navidrome/navidrome.new /opt/navidrome/navidrome   # a rename: safe while the old one is running
  rm -rf "$tmp"
  echo "$ver" > /etc/selfstream/navidrome.version
}

install_ytdlp() {
  msg "Installing yt-dlp"
  curl -fsSL https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp -o /usr/local/bin/yt-dlp.new
  chmod 755 /usr/local/bin/yt-dlp.new
  chown selfstream:selfstream /usr/local/bin/yt-dlp.new   # lets the app self-update it
  mv -f /usr/local/bin/yt-dlp.new /usr/local/bin/yt-dlp
}

install_deno() {
  # yt-dlp needs a JavaScript runtime to get audio from YouTube; Deno is the one it uses by default
  local target
  case "$(dpkg --print-architecture)" in
    amd64) target=x86_64-unknown-linux-gnu ;;
    arm64) target=aarch64-unknown-linux-gnu ;;
    *) die "unsupported CPU architecture: $(dpkg --print-architecture)" ;;
  esac
  msg "Installing Deno (JavaScript runtime for yt-dlp)"
  command -v unzip >/dev/null || { apt-get update -qq; apt-get install -y -qq unzip >/dev/null; }
  local tmp
  tmp=$(mktemp -d)
  curl -fsSL "https://github.com/denoland/deno/releases/latest/download/deno-${target}.zip" -o "${tmp}/deno.zip"
  unzip -q -o "${tmp}/deno.zip" -d "$tmp" deno
  install -m 755 "${tmp}/deno" /usr/local/bin/deno.new
  mv -f /usr/local/bin/deno.new /usr/local/bin/deno
  rm -rf "$tmp"
}

install_app() {
  msg "Installing selfstream app (${REPO}@${BRANCH})"
  local tmp
  tmp=$(mktemp -d)
  curl -fsSL "https://github.com/${REPO}/archive/refs/heads/${BRANCH}.tar.gz" \
    | tar -xz -C "$tmp" --strip-components=1
  [[ -f "$tmp/app/main.py" ]] || die "the download of selfstream is incomplete"
  rm -rf /opt/selfstream/app.new /opt/selfstream/app.old
  cp -r "$tmp/app" /opt/selfstream/app.new
  [[ -d /opt/selfstream/app ]] && mv /opt/selfstream/app /opt/selfstream/app.old
  mv /opt/selfstream/app.new /opt/selfstream/app
  rm -rf "$tmp" /opt/selfstream/app.old
  [[ -d /opt/selfstream/venv ]] || python3 -m venv /opt/selfstream/venv
  /opt/selfstream/venv/bin/pip install -q --upgrade pip
  /opt/selfstream/venv/bin/pip install -q -r /opt/selfstream/app/requirements.txt
  # optional: songs-only YouTube Music search. If this fails, search falls back to plain YouTube results.
  /opt/selfstream/venv/bin/pip install -q --upgrade ytmusicapi \
    || echo "  (could not install ytmusicapi - search will use plain YouTube results)"
  chown -R selfstream:selfstream /opt/selfstream
}

install_android_build() {
  # optional: the newest Android test build from a private GitHub repo (needs /etc/selfstream/android-update.conf, see the README)
  /opt/selfstream/venv/bin/python /opt/selfstream/app/apkfetch.py || echo "  (could not fetch the Android build)"
  chown -R selfstream:selfstream /var/lib/selfstream/app 2>/dev/null || true
}

write_config() {
  cat > /etc/selfstream/selfstream.env <<'EOF'
# Navidrome (listens on localhost only; the proxy on :8080 is the public entry point)
ND_MUSICFOLDER=/music
ND_DATAFOLDER=/var/lib/navidrome
ND_ADDRESS=127.0.0.1
ND_PORT=4533
ND_SCANSCHEDULE="@every 1m"
ND_SCANNER_SCHEDULE="@every 1m"
# selfstream app
MUSIC_DIR=/music
NAVIDROME_URL=http://127.0.0.1:4533
AUDIO_FORMAT=opus
YTDLP_UPDATE_HOURS=24
# Remote (YouTube) results inside Subsonic apps: search, stream, heart = download
REMOTE_ENABLED=1
REMOTE_RESULTS=8
# ytmusic = songs only (real artist, album, cover); youtube = plain video search
SEARCH_SOURCE=ytmusic
# Optional marker in front of cloud (not yet downloaded) track titles, e.g. REMOTE_TITLE_PREFIX="☁ "
# REMOTE_TITLE_PREFIX=""
# Log every Subsonic request (no passwords) - handy while testing a new app, set 0 when happy
PROXY_LOG=1
# Extra yt-dlp options, e.g. cookies if YouTube asks you to sign in:
# YTDLP_EXTRA_ARGS="--cookies /var/lib/navidrome/cookies.txt"
EOF
  # Discord "now playing" (optional): the application ID from discord.com/developers/applications
  if [[ "${DISCORD_CLIENT_ID:-}" =~ ^[0-9]{15,25}$ ]]; then
    printf '# Discord application ID (lets clients show what they play on Discord)\nDISCORD_CLIENT_ID=%s\n' "$DISCORD_CLIENT_ID" >> /etc/selfstream/selfstream.env
  else
    printf '# Discord application ID, to let clients show "now playing" on Discord:\n# DISCORD_CLIENT_ID=\n' >> /etc/selfstream/selfstream.env
  fi
  # "like = download to the server": 0 hides the switch in the apps and never downloads on a heart
  printf '# Let people use "like to download on the server" (0 = no, hides the switch in the apps)\nALLOW_LIKE_DOWNLOAD=%s\n' "${ALLOW_LIKE_DOWNLOAD:-1}" >> /etc/selfstream/selfstream.env
  printf 'REPO=%s\nBRANCH=%s\n' "$REPO" "$BRANCH" > /etc/selfstream/install.conf

  cat > /usr/bin/update <<'EOF'
#!/usr/bin/env bash
# Updates Navidrome, yt-dlp and the selfstream app.
set -Eeuo pipefail
. /etc/selfstream/install.conf
curl -fsSL "https://raw.githubusercontent.com/${REPO}/${BRANCH}/install/selfstream-install.sh" \
  | SELFSTREAM_MODE=update SELFSTREAM_REPO="$REPO" SELFSTREAM_BRANCH="$BRANCH" bash
EOF
  chmod +x /usr/bin/update

  cat > /etc/systemd/system/navidrome.service <<'EOF'
[Unit]
Description=Navidrome music server
After=network.target

[Service]
User=selfstream
Group=selfstream
EnvironmentFile=/etc/selfstream/selfstream.env
ExecStart=/opt/navidrome/navidrome
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

  cat > /etc/systemd/system/selfstream.service <<'EOF'
[Unit]
Description=selfstream ingest service and Subsonic proxy
After=network.target navidrome.service
Wants=navidrome.service

[Service]
User=selfstream
Group=selfstream
EnvironmentFile=/etc/selfstream/selfstream.env
WorkingDirectory=/opt/selfstream/app
ExecStart=/opt/selfstream/venv/bin/uvicorn main:app --host 0.0.0.0 --port 8080
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
  systemctl daemon-reload
}

if [[ "$MODE" == "update" ]]; then
  msg "Updating selfstream"
  # older installs set a cloud marker by default; the default is now none (a marker you set yourself is kept)
  sed -i '/^REMOTE_TITLE_PREFIX="☁ "$/d' /etc/selfstream/selfstream.env 2>/dev/null || true
  apt-get update -qq && apt-get install -y -qq python3-mutagen >/dev/null || echo "  (could not install python3-mutagen - cover art may not be embedded)"
  # Nothing is stopped while downloading: every file is fetched aside and renamed into place, so a failed update
  # (GitHub unreachable, say) leaves the running music server exactly as it was. The services restart at the end.
  trap 'echo -e "${RD}Update failed at line $LINENO - the services keep running the previous version.${CL}" >&2; systemctl start navidrome selfstream 2>/dev/null || true; exit 1' ERR
  install_navidrome
  install_ytdlp
  install_deno
  install_app
  install_android_build
  systemctl restart navidrome selfstream
  msg "Update complete"
  exit 0
fi

install_deps
create_user
install_navidrome
install_ytdlp
install_deno
install_app
install_android_build
write_config
systemctl enable --now navidrome selfstream >/dev/null 2>&1

sleep 3
systemctl is-active --quiet navidrome  || die "navidrome failed to start (journalctl -u navidrome)"
systemctl is-active --quiet selfstream || die "selfstream failed to start (journalctl -u selfstream)"
msg "Services are running"
