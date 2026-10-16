#!/usr/bin/env bash
# selfstream - Proxmox VE helper script
# Creates an unprivileged Debian LXC and installs Navidrome + yt-dlp + LRCLIB lyrics
# ingest service natively inside it (no Docker).
#
# Run on the Proxmox VE host shell:
#   bash -c "$(curl -fsSL https://raw.githubusercontent.com/TaiDies/selfstream-server/main/ct/selfstream.sh)"
#
# Non-interactive: SS_DEFAULTS=1 bash -c "$(curl ...)"
# Update an existing container: bash -c "$(curl ...)" -- update <CTID>
#
# Override any setting through environment variables, for example:
#   CORES=4 RAM=2048 MUSIC_SIZE=100 IP=192.168.1.50/24 GW=192.168.1.1 bash -c "$(curl ...)"

set -Eeuo pipefail

APP="selfstream"
REPO="${SELFSTREAM_REPO:-TaiDies/selfstream-server}"
BRANCH="${SELFSTREAM_BRANCH:-main}"
RAW="https://raw.githubusercontent.com/${REPO}/${BRANCH}"

# ----------------------------------------------------------------- defaults
CTID="${CTID:-}"
CT_HOSTNAME="${CT_HOSTNAME:-selfstream}"
CORES="${CORES:-2}"
RAM="${RAM:-1024}"
SWAP="${SWAP:-512}"
DISK="${DISK:-8}"                 # root disk, GB
MUSIC_SIZE="${MUSIC_SIZE:-20}"    # separate music volume, GB (ignored if MUSIC_BIND is set)
MUSIC_BIND="${MUSIC_BIND:-}"      # optional host path to bind-mount at /music instead
BRIDGE="${BRIDGE:-vmbr0}"
IP="${IP:-dhcp}"                  # dhcp or CIDR, e.g. 192.168.1.50/24
GW="${GW:-}"                      # gateway, only needed with a static IP
DEBIAN_VERSION="${DEBIAN_VERSION:-12}"
CT_STORAGE="${CT_STORAGE:-}"
TEMPLATE_STORAGE="${TEMPLATE_STORAGE:-}"

# ------------------------------------------------------------------ helpers
GN=$'\033[32m'; RD=$'\033[31m'; YW=$'\033[33m'; CL=$'\033[0m'
msg()  { echo -e "${GN}==>${CL} $*"; }
warn() { echo -e "${YW}!!${CL}  $*"; }
die()  { echo -e "${RD}ERROR:${CL} $*" >&2; exit 1; }
trap 'die "failed at line $LINENO (exit $?)"' ERR

pick_storage() {  # pick_storage <content-type>
  local list
  list=$(pvesm status -content "$1" 2>/dev/null | awk 'NR>1 && $3=="active"{print $1}')
  if grep -qx "local-lvm" <<<"$list"; then echo "local-lvm"; else head -n1 <<<"$list"; fi
}

ask() {           # ask VAR "Label"
  local var=$1 label=$2 val
  val=$(whiptail --title "$APP" --inputbox "$label" 9 64 "${!var}" 3>&1 1>&2 2>&3) || exit 1
  printf -v "$var" '%s' "$val"
}

# ------------------------------------------------------------------- checks
[[ $EUID -eq 0 ]] || die "run this as root on the Proxmox host"
command -v pct >/dev/null && command -v pveam >/dev/null || die "this must be run on a Proxmox VE host"

# `selfstream.sh update <CTID>` shortcut
if [[ "${1:-}" == "update" ]]; then
  [[ -n "${2:-}" ]] || die "usage: $0 update <CTID>"
  exec pct exec "$2" -- update
fi

[[ "$REPO" != "GH_REPO" ]] || die "SELFSTREAM_REPO is not set (expected user/repo)"

# ------------------------------------------------------------ settings menu
if [[ -t 0 && -z "${SS_DEFAULTS:-}" ]] && command -v whiptail >/dev/null; then
  if ! whiptail --title "$APP" --yesno \
"Install $APP (Navidrome + yt-dlp + synced lyrics) in a new LXC container?

Yes = default settings
No  = advanced settings" 12 64 --yes-button "Default" --no-button "Advanced"; then
    ask CT_HOSTNAME "Hostname"
    ask CORES "CPU cores"
    ask RAM "RAM (MB)"
    ask DISK "Root disk (GB)"
    ask MUSIC_SIZE "Music volume (GB)"
    ask BRIDGE "Network bridge"
    ask IP "IP address (dhcp or CIDR like 192.168.1.50/24)"
    [[ "$IP" == "dhcp" ]] || ask GW "Gateway"
    ask MUSIC_BIND "Host path to bind-mount as /music (leave empty to create a volume)"
  fi
  # optional: lets the Selfstream Windows app show "now playing" on Discord (see the README)
  DISCORD_CLIENT_ID=$(whiptail --title "$APP" --inputbox \
"Optional: Discord application ID, for the Discord 'now playing' status.
Create one at discord.com/developers/applications (New Application, copy the Application ID).
Leave empty to skip; you can add it later in /etc/selfstream/selfstream.env." 12 70 "${DISCORD_CLIENT_ID:-}" 3>&1 1>&2 2>&3) || exit 1
  DISCORD_CLIENT_ID=${DISCORD_CLIENT_ID//[[:space:]]/}
  if [[ -n "$DISCORD_CLIENT_ID" && ! "$DISCORD_CLIENT_ID" =~ ^[0-9]{15,25}$ ]]; then
    echo "  ($DISCORD_CLIENT_ID does not look like a Discord application ID: skipped)"
    DISCORD_CLIENT_ID=""
  fi
fi

# -------------------------------------------------------------- preparation
CTID="${CTID:-$(pvesh get /cluster/nextid)}"
CT_STORAGE="${CT_STORAGE:-$(pick_storage rootdir)}"
TEMPLATE_STORAGE="${TEMPLATE_STORAGE:-$(pick_storage vztmpl)}"
[[ -n "$CT_STORAGE" ]]       || die "no storage that can hold containers was found (set CT_STORAGE)"
[[ -n "$TEMPLATE_STORAGE" ]] || die "no storage that can hold templates was found (set TEMPLATE_STORAGE)"
[[ "$IP" == "dhcp" || -n "$GW" ]] || die "a static IP also needs GW=<gateway>"
pct status "$CTID" &>/dev/null && die "container ID $CTID already exists"
[[ -z "$MUSIC_BIND" || -d "$MUSIC_BIND" ]] || die "MUSIC_BIND path does not exist: $MUSIC_BIND"

msg "Using CT $CTID on '$CT_STORAGE' (templates on '$TEMPLATE_STORAGE')"

msg "Looking for the Debian $DEBIAN_VERSION template"
pveam update >/dev/null
TEMPLATE=$(pveam available --section system | awk -v v="debian-${DEBIAN_VERSION}-standard" '$2 ~ v {print $2}' | sort -V | tail -n1)
[[ -n "$TEMPLATE" ]] || die "no debian-${DEBIAN_VERSION} template available (try DEBIAN_VERSION=13)"
if ! pveam list "$TEMPLATE_STORAGE" | grep -q "$TEMPLATE"; then
  msg "Downloading $TEMPLATE"
  pveam download "$TEMPLATE_STORAGE" "$TEMPLATE" >/dev/null
fi

NET0="name=eth0,bridge=${BRIDGE},ip=${IP}"
[[ -z "$GW" ]] || NET0+=",gw=${GW}"

if [[ -n "$MUSIC_BIND" ]]; then
  MP0="${MUSIC_BIND},mp=/music"
  warn "Bind-mounted music folder: in an unprivileged container the app user (uid 1000) appears as uid 101000 on the host."
  warn "Run 'chown -R 101000:101000 ${MUSIC_BIND}' on the host so downloads can be written."
else
  MP0="${CT_STORAGE}:${MUSIC_SIZE},mp=/music"
fi

# ------------------------------------------------------------ create the CT
msg "Creating LXC $CTID ($CT_HOSTNAME)"
pct create "$CTID" "${TEMPLATE_STORAGE}:vztmpl/${TEMPLATE}" \
  --hostname "$CT_HOSTNAME" \
  --cores "$CORES" --memory "$RAM" --swap "$SWAP" \
  --rootfs "${CT_STORAGE}:${DISK}" \
  --mp0 "$MP0" \
  --net0 "$NET0" \
  --unprivileged 1 --features nesting=1 \
  --onboot 1 --tags selfstream \
  --description "selfstream: Navidrome + yt-dlp + synced lyrics (https://github.com/${REPO})" \
  >/dev/null

msg "Starting container"
pct start "$CTID"

msg "Waiting for network"
for _ in $(seq 1 30); do
  pct exec "$CTID" -- getent hosts github.com >/dev/null 2>&1 && break
  sleep 2
done
pct exec "$CTID" -- getent hosts github.com >/dev/null 2>&1 \
  || die "container has no network/DNS. Check the bridge/IP settings, then 'pct destroy $CTID' and retry"

# ---------------------------------------------------------------- install
msg "Installing $APP inside the container (this takes a few minutes)"
pct exec "$CTID" -- bash -c "apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq curl ca-certificates >/dev/null"
pct exec "$CTID" -- env SELFSTREAM_REPO="$REPO" SELFSTREAM_BRANCH="$BRANCH" DISCORD_CLIENT_ID="${DISCORD_CLIENT_ID:-}" \
  bash -c "curl -fsSL '${RAW}/install/selfstream-install.sh' | bash"

CT_IP=$(pct exec "$CTID" -- hostname -I | awk '{print $1}')

echo
msg "Done."
echo "    Navidrome + proxy : http://${CT_IP}:8080"
echo "    Search / download : http://${CT_IP}:8080/selfstream"
echo "    Update later      : pct exec ${CTID} -- update"
echo "                        (or run this script with: update ${CTID})"
echo "    Use Subsonic clients with the first URL, user = the admin you create on first visit."
