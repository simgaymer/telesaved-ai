#!/usr/bin/env bash
set -euo pipefail

APP_DIR="/opt/telesaved"
VENV_DIR="${APP_DIR}/venv"
SERVICE_NAME="telesaved"

log() {
  echo
  echo "==== $* ===="
}

fail() {
  echo "ERROR: $*" >&2
  exit 1
}

if [ "$(id -u)" -ne 0 ]; then
  fail "Run with sudo: sudo /opt/telesaved/install-ldap-deps.sh"
fi

if [ ! -f "${APP_DIR}/app.py" ]; then
  fail "${APP_DIR}/app.py not found"
fi

export DEBIAN_FRONTEND=noninteractive

log "Installing LDAP system prerequisites"
apt-get update
apt-get install -y python3 python3-venv python3-pip ca-certificates

if [ ! -x "${VENV_DIR}/bin/python" ]; then
  log "Creating TeleSaved venv at ${VENV_DIR}"
  python3 -m venv "${VENV_DIR}"
fi

log "Installing ldap3 into TeleSaved venv"
"${VENV_DIR}/bin/python" -m pip install --upgrade pip setuptools wheel
"${VENV_DIR}/bin/python" -m pip install --upgrade ldap3

log "Verifying ldap3 import"
"${VENV_DIR}/bin/python" - <<'PY'
import ldap3
print("ldap3 OK:", ldap3.__version__)
PY

log "Restarting TeleSaved"
systemctl restart "${SERVICE_NAME}"
sleep 3
systemctl status "${SERVICE_NAME}" --no-pager || true

log "Done"
echo "LDAP support is installed. Open Administration → Users & Access → LDAP Identity Backend."
