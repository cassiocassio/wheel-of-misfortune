#!/usr/bin/env bash
# Install Wheel of Misfortune as a background service that starts on boot/login,
# restarts if it dies, and (on macOS) keeps the machine awake.
#   macOS → launchd LaunchAgent      (~/Library/LaunchAgents)
#   Linux → systemd --user service   (~/.config/systemd/user) + lingering
#
#   ./deploy/install.sh            # install + start
#   ./deploy/install.sh uninstall  # stop + remove
set -euo pipefail

LABEL="com.storey.misfortune"          # launchd label (macOS)
UNIT="wheel-of-misfortune"             # systemd unit name (Linux)
WORKDIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OS="$(uname -s)"
ACTION="${1:-install}"

# --- macOS / launchd -------------------------------------------------------- #
mac_dest() { echo "$HOME/Library/LaunchAgents/$LABEL.plist"; }
mac_unload() {
  launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null \
    || launchctl unload -w "$(mac_dest)" 2>/dev/null || true
}
mac_install() {
  local dest; dest="$(mac_dest)"
  mkdir -p "$(dirname "$dest")" "$WORKDIR/logs"
  sed "s|__WORKDIR__|$WORKDIR|g" "$WORKDIR/deploy/$LABEL.plist" > "$dest"
  mac_unload  # in case a previous version is running
  launchctl bootstrap "gui/$(id -u)" "$dest" 2>/dev/null || launchctl load -w "$dest"
  echo "Installed launchd agent → $dest"
  echo "Reachable at: http://$(scutil --get LocalHostName 2>/dev/null || echo misfortune).local:8000"
  echo "Logs:         $WORKDIR/logs/misfortune.{out,err}.log"
  echo
  echo "Tip: friendly name → System Settings ▸ General ▸ Sharing ▸ Local hostname → 'misfortune'."
  echo "Tip: belt-and-braces anti-sleep →  sudo pmset -a sleep 0 disksleep 0"
}
mac_uninstall() { mac_unload; rm -f "$(mac_dest)"; echo "Removed launchd agent."; }

# --- Linux / systemd --user ------------------------------------------------- #
linux_dest() { echo "$HOME/.config/systemd/user/$UNIT.service"; }
linux_install() {
  local dest; dest="$(linux_dest)"
  mkdir -p "$(dirname "$dest")"
  sed "s|__WORKDIR__|$WORKDIR|g" "$WORKDIR/deploy/$UNIT.service" > "$dest"
  systemctl --user daemon-reload
  systemctl --user enable --now "$UNIT.service"
  # start at boot without an interactive login (the RunAtLoad equivalent)
  sudo loginctl enable-linger "$USER" 2>/dev/null \
    || echo "  note: couldn't enable linger — run:  sudo loginctl enable-linger $USER"
  local ip; ip="$(hostname -I 2>/dev/null | awk '{print $1}')"
  echo "Installed systemd --user unit → $dest"
  echo "Reachable at: http://${ip:-<this-host-ip>}:8000   (works now — nothing else to install)"
  echo "Logs:         journalctl --user -u $UNIT -f"
  echo
  echo "Optional: macOS/iOS already resolve misfortune.local for free. ONLY if you also want the"
  echo "          Linux host to answer to that name (pure convenience) → sudo apt install avahi-daemon"
}
linux_uninstall() {
  systemctl --user disable --now "$UNIT.service" 2>/dev/null || true
  rm -f "$(linux_dest)"
  systemctl --user daemon-reload 2>/dev/null || true
  echo "Removed systemd --user unit."
}

# --- preflight (shared) ----------------------------------------------------- #
if [[ "$ACTION" != "uninstall" ]]; then
  if [[ ! -x "$WORKDIR/.venv/bin/uvicorn" ]]; then
    echo "ERROR: $WORKDIR/.venv/bin/uvicorn not found." >&2
    echo "  python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt" >&2
    exit 1
  fi
  if [[ ! -f "$WORKDIR/family.yaml" ]]; then
    echo "No family.yaml yet — copying the example. Edit it, then re-run."
    cp "$WORKDIR/family.example.yaml" "$WORKDIR/family.yaml"
  fi
fi

# --- dispatch on OS + action ------------------------------------------------ #
case "$OS:$ACTION" in
  Darwin:install)   mac_install ;;
  Darwin:uninstall) mac_uninstall ;;
  Linux:install)    linux_install ;;
  Linux:uninstall)  linux_uninstall ;;
  *) echo "Unsupported OS '$OS'. This installer handles macOS (launchd) and Linux (systemd)." >&2
     exit 1 ;;
esac
