#!/usr/bin/env bash
# Install (or remove) the robot as systemd services. Idempotent; safe to re-run after updates.
#   sudo ./deploy/install.sh              install/refresh for the invoking user
#   sudo ./deploy/install.sh --uninstall  remove units, rules and the runtime dir
set -euo pipefail

if [[ ${EUID} -ne 0 ]]; then
  echo "run with sudo" >&2
  exit 1
fi

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
USER_NAME="${SUDO_USER:-${USER}}"
VENV="${REPO}/.venv"
SERVICES=(broker safety motion face perception voice brain orchestrator power)
UNIT_DIR=/etc/systemd/system

if [[ "${1:-}" == "--uninstall" ]]; then
  systemctl disable --now robot.target 2>/dev/null || true
  for s in "${SERVICES[@]}"; do systemctl disable --now "robot-${s}.service" 2>/dev/null || true; done
  rm -f "${UNIT_DIR}"/robot-*.service "${UNIT_DIR}"/robot@.service "${UNIT_DIR}"/robot.target
  rm -f /etc/udev/rules.d/99-robot.rules /etc/tmpfiles.d/robot.conf
  systemctl daemon-reload
  udevadm control --reload-rules || true
  echo "removed. Models and memory in ~${USER_NAME}/.local/share/robot were left alone."
  exit 0
fi

if [[ ! -x "${VENV}/bin/robot" ]]; then
  echo "no ${VENV}/bin/robot; run the install steps in docs/INSTALL.md (uv venv --system-site-packages; uv sync --extra pi)" >&2
  exit 1
fi

echo "installing for user ${USER_NAME}, repo ${REPO}"

# The units draw straight to the display (KMS). A desktop session owns the display too, so
# on a Pi that boots to the desktop the face service would fail. Say so instead of half-working.
if [[ "$(systemctl get-default 2>/dev/null)" == "graphical.target" ]]; then
  echo "WARNING: this Pi boots to the desktop (graphical.target). The systemd units need the console." >&2
  echo "  Either: sudo raspi-config -> System Options -> Boot / Auto Login -> Console Autologin, then re-run" >&2
  echo "  Or:     keep the desktop and use \`uv run robot autostart install\` from a terminal on it instead." >&2
  if [[ "${1:-}" != "--force" ]]; then
    echo "  (re-run with --force to install the units anyway)" >&2
    exit 1
  fi
fi

# groups for peripherals
for g in video audio gpio i2c spi render input; do
  getent group "$g" >/dev/null 2>&1 || groupadd -r "$g"
  usermod -aG "$g" "${USER_NAME}"
done

# udev + tmpfiles
install -m 0644 "${REPO}/deploy/udev/99-robot.rules" /etc/udev/rules.d/99-robot.rules
sed "s#@USER@#${USER_NAME}#g" "${REPO}/deploy/tmpfiles.d/robot.conf" > /etc/tmpfiles.d/robot.conf
systemd-tmpfiles --create /etc/tmpfiles.d/robot.conf
udevadm control --reload-rules && udevadm trigger || true

# environment file for the units (bus over unix sockets in /run/robot)
if [[ ! -f "${REPO}/config/robot.env" ]]; then
  cat > "${REPO}/config/robot.env" <<ENV
ROBOT__BUS__XSUB=ipc:///run/robot/xsub
ROBOT__BUS__XPUB=ipc:///run/robot/xpub
ROBOT_CONFIG_DIR=${REPO}/config
ROBOT_LOG_LEVEL=INFO
ENV
  chown "${USER_NAME}:${USER_NAME}" "${REPO}/config/robot.env"
fi

# units
for f in "${REPO}"/deploy/systemd/*; do
  sed -e "s#@USER@#${USER_NAME}#g" -e "s#@REPO@#${REPO}#g" -e "s#@VENV@#${VENV}#g" "$f" > "${UNIT_DIR}/$(basename "$f")"
done
systemctl daemon-reload

# journald: cap the robot's logs so a chatty service cannot fill the SD card
mkdir -p /etc/systemd/journald.conf.d
cat > /etc/systemd/journald.conf.d/robot.conf <<J
[Journal]
SystemMaxUse=200M
MaxRetentionSec=14day
J
systemctl restart systemd-journald || true

# power monitor only when enabled in config
if "${VENV}/bin/robot" --config-dir "${REPO}/config" config show power 2>/dev/null | grep -q "enabled: true"; then
  systemctl enable robot-power.service >/dev/null
else
  systemctl disable robot-power.service >/dev/null 2>&1 || true
fi

echo
echo "installed. Next:"
echo "  sudo systemctl enable --now robot.target"
echo "  systemctl status 'robot-*'"
echo "  journalctl -u 'robot-*' -f"
echo "Log out and back in (or reboot) so the new group memberships apply to ${USER_NAME}."
