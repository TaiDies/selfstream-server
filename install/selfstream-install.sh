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
  apt-get install -y -qq ffmpeg python3 python3-venv curl ca-certificates tar xz-utils >/dev/null
}

create_user() {
  id selfstream &>/dev/null || useradd -m -u 1000 -s /usr/sbin/nologin selfstream
  mkdir -p /music /var/lib/navidrome /opt/navidrome /opt/selfstream /etc/selfstream
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
  curl -fsSL "https://github.com/navidrome/navidrome/releases/download/v${ver}/navidrome_${ver}_linux_${arch}.tar.gz" \
    | tar -xz -C /opt/navidrome navidrome
  chmod +x /opt/navidrome/navidrome
  echo "$ver" > /etc/selfstream/navidrome.version
}

install_ytdlp() {
  msg "Installing yt-dlp"
  curl -fsSL https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp -o /usr/local/bin/yt-dlp
  chmod 755 /usr/local/bin/yt-dlp
  chown selfstream:selfstream /usr/local/bin/yt-dlp   # lets the app self-update it
}

install_app() {
  msg "Installing selfstream app (${REPO}@${BRANCH})"
  local tmp
  tmp=$(mktemp -d)
  curl -fsSL "https://github.com/${REPO}/archive/refs/heads/${BRANCH}.tar.gz" \
    | tar -xz -C "$tmp" --strip-components=1
  rm -rf /opt/selfstream/app
  cp -r "$tmp/app" /opt/selfstream/app
  rm -rf "$tmp"
  [[ -d /opt/selfstream/venv ]] || python3 -m venv /opt/selfstream/venv
  /opt/selfstream/venv/bin/pip install -q --upgrade pip
  /opt/selfstream/venv/bin/pip install -q -r /opt/selfstream/app/requirements.txt
  chown -R selfstream:selfstream /opt/selfstream
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
EOF
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
  systemctl stop selfstream navidrome
  install_navidrome
  install_ytdlp
  install_app
  systemctl start navidrome selfstream
  msg "Update complete"
  exit 0
fi

install_deps
create_user
install_navidrome
install_ytdlp
install_app
write_config
systemctl enable --now navidrome selfstream >/dev/null 2>&1

sleep 3
systemctl is-active --quiet navidrome  || die "navidrome failed to start (journalctl -u navidrome)"
systemctl is-active --quiet selfstream || die "selfstream failed to start (journalctl -u selfstream)"
msg "Services are running"
