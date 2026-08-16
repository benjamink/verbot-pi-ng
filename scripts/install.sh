#!/usr/bin/env bash
#
# verbot-pi-ng installer for a Raspberry Pi Zero 2 W.
#
#   curl -LsSf https://raw.githubusercontent.com/benjamink/verbot-pi-ng/main/scripts/install.sh | bash
#
# Covers steps 3-8 of docs/deployment.md. Safe to re-run: every step checks
# its own end state first.
#
# Overrides, if you need them:
#   VERBOT_REPO=<git url>   default https://github.com/benjamink/verbot-pi-ng.git
#   VERBOT_REF=<branch|tag> default main
#   VERBOT_DIR=<path>       default $HOME/verbot-pi-ng
#
# Everything lives in functions and main runs on the last line, so a download
# truncated mid-transfer cannot execute a partial script.

set -euo pipefail

REPO="${VERBOT_REPO:-https://github.com/benjamink/verbot-pi-ng.git}"
REF="${VERBOT_REF:-main}"
DEST="${VERBOT_DIR:-$HOME/verbot-pi-ng}"
BOOT_CONFIG=/boot/firmware/config.txt
MARKER_BEGIN="# >>> verbot-pi-ng >>>"
MARKER_END="# <<< verbot-pi-ng <<<"

NEEDS_REBOOT=0

say()  { printf '\n\033[1;36m==>\033[0m %s\n' "$*"; }
info() { printf '    %s\n' "$*"; }
warn() { printf '\033[1;33m[warn]\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31m[fail]\033[0m %s\n' "$*" >&2; exit 1; }

preflight() {
  say "Checking this is the right machine"

  [ "$(id -u)" -ne 0 ] || die "Run as your normal user, not root. The script calls sudo where it needs to."
  command -v sudo >/dev/null || die "sudo not found."

  local model=/proc/device-tree/model
  if [ -r "$model" ] && tr -d '\0' < "$model" | grep -qi "raspberry pi"; then
    info "Model: $(tr -d '\0' < "$model")"
  else
    die "This does not look like a Raspberry Pi. Refusing to edit $BOOT_CONFIG."
  fi

  # 64-bit matters: aarch64 gets prebuilt pydantic-core wheels. On armv7l uv
  # falls back to compiling Rust from source, which a Zero will not finish.
  local arch; arch="$(uname -m)"
  [ "$arch" = "aarch64" ] || die "Need a 64-bit OS, found $arch. Reflash with 64-bit Raspberry Pi OS."
  info "Arch: $arch"

  [ -f "$BOOT_CONFIG" ] || die "$BOOT_CONFIG not found. Expected Bookworm or newer layout."

  # Prime the sudo timestamp now rather than mid-run. Harmless under the
  # NOPASSWD grant cloud-init installs; useful when that is absent.
  sudo -v || die "sudo authentication failed."
}

wait_for_dns() {
  say "Waiting for name resolution"
  # network-config sets optional: true on wlan0 so a missing access point
  # cannot block boot. The cost is that an early SSH can beat NetworkManager
  # to configuring DNS, and the first real fetch then dies on an unresolved
  # host. Wait it out rather than failing halfway through the install.
  local deadline=$(( SECONDS + 90 )) host
  for host in astral.sh github.com; do
    until getent hosts "$host" >/dev/null 2>&1; do
      if [ "$SECONDS" -ge "$deadline" ]; then
        warn "Cannot resolve $host."
        warn "Check:  ip route show default"
        warn "        cat /etc/resolv.conf"
        warn "        systemctl is-active NetworkManager"
        die "No DNS after 90s."
      fi
      info "No DNS yet, retrying: $host"
      sleep 5
    done
    info "Resolved $host"
  done
}

install_packages() {
  say "Installing system packages"
  sudo apt-get update -qq
  sudo apt-get install -y --no-install-recommends espeak-ng i2c-tools git
}

install_uv() {
  say "Installing uv"
  if command -v uv >/dev/null; then
    info "Already present: $(uv --version)"
  else
    curl -LsSf https://astral.sh/uv/install.sh | sh
  fi
  # The installer drops uv in ~/.local/bin, which is not yet on PATH in this
  # non-login shell.
  export PATH="$HOME/.local/bin:$PATH"
  command -v uv >/dev/null || die "uv is still not on PATH after install."
}

fetch_source() {
  say "Fetching verbot-pi-ng into $DEST"
  if [ -d "$DEST/.git" ]; then
    info "Existing checkout, updating to $REF"
    git -C "$DEST" fetch --quiet origin "$REF"
    git -C "$DEST" checkout --quiet "$REF"
    git -C "$DEST" merge --quiet --ff-only "origin/$REF" 2>/dev/null || \
      warn "Could not fast-forward; leaving the working tree as it is."
  else
    [ -e "$DEST" ] && die "$DEST exists but is not a git checkout. Move it aside and re-run."
    git clone --quiet --branch "$REF" "$REPO" "$DEST"
  fi
  info "At $(git -C "$DEST" rev-parse --short HEAD)"
}

sync_deps() {
  say "Installing Python dependencies"
  # --frozen installs exactly the committed uv.lock.
  ( cd "$DEST" && uv sync --extra pi --frozen )
}

add_groups() {
  say "Adding $USER to hardware groups"
  local added=0 g
  for g in gpio i2c audio; do
    if ! getent group "$g" >/dev/null; then
      warn "Group '$g' does not exist on this image; skipping."
      continue
    fi
    if id -nG "$USER" | tr ' ' '\n' | grep -qx "$g"; then
      info "Already in $g"
    else
      sudo usermod -aG "$g" "$USER"
      info "Added to $g"
      added=1
    fi
  done
  # Group membership only applies to new logins.
  [ "$added" -eq 1 ] && NEEDS_REBOOT=1
  return 0
}

apply_firmware_config() {
  say "Applying firmware overlays to $BOOT_CONFIG"
  local src="$DEST/config/config.txt.example"
  [ -f "$src" ] || die "$src is missing from the checkout."

  if grep -qF "$MARKER_BEGIN" "$BOOT_CONFIG"; then
    info "Already applied; leaving $BOOT_CONFIG alone."
    return 0
  fi

  local backup="${BOOT_CONFIG}.verbot-backup"
  [ -f "$backup" ] || sudo cp "$BOOT_CONFIG" "$backup"
  info "Backup at $backup"

  {
    printf '\n%s\n' "$MARKER_BEGIN"
    cat "$src"
    printf '%s\n' "$MARKER_END"
  } | sudo tee -a "$BOOT_CONFIG" >/dev/null

  info "Overlays appended. They load on the next boot."
  NEEDS_REBOOT=1
}

write_env() {
  say "Writing .env"
  local env_file="$DEST/.env"
  if [ -f "$env_file" ] && grep -q '^VERBOT_USE_REAL_HARDWARE=' "$env_file"; then
    info "VERBOT_USE_REAL_HARDWARE already set."
  else
    echo 'VERBOT_USE_REAL_HARDWARE=true' >> "$env_file"
    info "Set VERBOT_USE_REAL_HARDWARE=true"
  fi
  info "Without this the server runs on fakes and the robot will not move."
}

install_service() {
  say "Installing the systemd service"
  local unit="$DEST/config/verbot.service"
  [ -f "$unit" ] || die "$unit is missing from the checkout."

  sudo cp "$unit" /etc/systemd/system/verbot@.service
  sudo systemctl daemon-reload
  sudo systemctl enable "verbot@$USER" >/dev/null
  info "Enabled verbot@$USER (starts on boot)."

  # Starting before the overlays load would fail on a missing pwmchip and
  # trip Restart=on-failure. After a reboot this branch is the live one.
  if [ "$NEEDS_REBOOT" -eq 0 ] && [ -e /sys/class/pwm/pwmchip0 ]; then
    sudo systemctl restart "verbot@$USER"
    info "Started verbot@$USER."
  else
    info "Not starting yet: the PWM overlay is not loaded until you reboot."
  fi
}

summary() {
  say "Done"
  cat <<EOF

    Checkout   $DEST
    Service    verbot@$USER (enabled)

EOF
  if [ "$NEEDS_REBOOT" -eq 1 ]; then
    cat <<EOF
    Reboot now, then verify:

      sudo reboot

      ls /sys/class/pwm/               # expect pwmchip0
      cat /sys/class/pwm/pwmchip0/npwm # expect 2
      aplay -l                         # expect the MAX98357A card
      i2cdetect -y 1                   # expect a device at 0x20
      systemctl status verbot@$USER
      curl -s localhost:8080/healthz   # expect {"status":"ok"}

EOF
  else
    cat <<EOF
    Verify:

      systemctl status verbot@$USER
      curl -s localhost:8080/healthz   # expect {"status":"ok"}

EOF
  fi
  cat <<EOF
    The service is enabled, so it starts on boot and the panel buttons are
    live. Put the robot on a stand with its wheels off the ground before
    working through the bring-up checklist in docs/deployment.md.

EOF
}

main() {
  preflight
  wait_for_dns
  install_packages
  install_uv
  fetch_source
  sync_deps
  add_groups
  apply_firmware_config
  write_env
  install_service
  summary
}

main "$@"
