#!/bin/sh
set -eu

PACKAGE_NAME="telesaved-ai"
APP_NAME="TeleSaved AI"
VERSION="${TELESAVED_VERSION:-4.4.2}"
ARCH="${TELESAVED_ARCH:-amd64}"
MAINTAINER='James "Jamie" Villegas <maintainer@localhost>'
INCLUDE_CONFIG="${TELESAVED_INCLUDE_CONFIG:-0}"

# Usage:
#   ./build-telesaved-deb.sh [SOURCE_DIR]
#
# Environment options:
#   TELESAVED_VERSION=4.4.2
#   TELESAVED_ARCH=amd64
#   TELESAVED_OUTPUT_DIR=/path/to/dist
#   TELESAVED_BUILD_DIR=/tmp/telesaved-deb-build
#   TELESAVED_INCLUDE_CONFIG=1   # Private builds only: bundles /opt/telesaved/config.json

if [ "${1:-}" ]; then
  SOURCE_DIR="$1"
elif [ -f "/opt/telesaved/app.py" ]; then
  SOURCE_DIR="/opt/telesaved"
else
  SOURCE_DIR="$(pwd)"
fi

SOURCE_DIR="$(cd "$SOURCE_DIR" && pwd)"
OUTPUT_DIR="${TELESAVED_OUTPUT_DIR:-$SOURCE_DIR/dist}"
BUILD_PARENT="${TELESAVED_BUILD_DIR:-/tmp/telesaved-deb-build}"
PKG_ROOT="$BUILD_PARENT/${PACKAGE_NAME}_${VERSION}_${ARCH}"
OUTPUT_DEB="$OUTPUT_DIR/${PACKAGE_NAME}_${VERSION}_${ARCH}.deb"

need_command() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "ERROR: Required command not found: $1" >&2
    exit 1
  fi
}

need_file() {
  if [ ! -f "$1" ]; then
    echo "ERROR: Required file missing: $1" >&2
    exit 1
  fi
}

copy_optional_file() {
  src="$1"
  dst="$2"
  mode="${3:-0644}"
  if [ -f "$src" ]; then
    mkdir -p "$(dirname "$dst")"
    cp "$src" "$dst"
    chmod "$mode" "$dst"
  fi
}

copy_readmes() {
  dst_dir="$1"
  mkdir -p "$dst_dir"
  found=0
  for src in "$SOURCE_DIR"/README "$SOURCE_DIR"/README.* "$SOURCE_DIR"/readme "$SOURCE_DIR"/readme.*; do
    if [ -f "$src" ]; then
      cp "$src" "$dst_dir/$(basename "$src")"
      chmod 0644 "$dst_dir/$(basename "$src")"
      found=1
    fi
  done
  if [ "$found" -eq 0 ]; then
    cat > "$dst_dir/README.Debian" <<READMEEOF
$APP_NAME

This Debian package installs the TeleSaved AI application files under /opt/telesaved and installs the telesaved.service systemd unit.

Configuration, media files, state files, backups, thumbnails, previews, and Telegram session files are not bundled into this package by default.

For private/internal deployment only, set TELESAVED_INCLUDE_CONFIG=1 when building to include /opt/telesaved/config.json as a Debian conffile.
READMEEOF
    chmod 0644 "$dst_dir/README.Debian"
  fi
}

need_command dpkg-deb
need_file "$SOURCE_DIR/app.py"

if [ -f "$SOURCE_DIR/frontend/index.html" ]; then
  FRONTEND_INDEX="$SOURCE_DIR/frontend/index.html"
elif [ -f "$SOURCE_DIR/index.html" ]; then
  FRONTEND_INDEX="$SOURCE_DIR/index.html"
else
  echo "ERROR: Could not find frontend/index.html or index.html in $SOURCE_DIR" >&2
  exit 1
fi

rm -rf "$PKG_ROOT"
mkdir -p "$PKG_ROOT/DEBIAN"
mkdir -p "$PKG_ROOT/opt/telesaved/frontend"
mkdir -p "$PKG_ROOT/etc/systemd/system"
mkdir -p "$PKG_ROOT/usr/share/doc/$PACKAGE_NAME"
mkdir -p "$OUTPUT_DIR"

cp "$SOURCE_DIR/app.py" "$PKG_ROOT/opt/telesaved/app.py"
chmod 0644 "$PKG_ROOT/opt/telesaved/app.py"

cp "$FRONTEND_INDEX" "$PKG_ROOT/opt/telesaved/frontend/index.html"
chmod 0644 "$PKG_ROOT/opt/telesaved/frontend/index.html"

copy_optional_file "$SOURCE_DIR/requirements.txt" "$PKG_ROOT/opt/telesaved/requirements.txt" 0644
copy_optional_file "$SOURCE_DIR/LICENSE" "$PKG_ROOT/usr/share/doc/$PACKAGE_NAME/LICENSE" 0644
copy_optional_file "$SOURCE_DIR/NOTICE" "$PKG_ROOT/usr/share/doc/$PACKAGE_NAME/NOTICE" 0644
copy_optional_file "$SOURCE_DIR/Dockerfile" "$PKG_ROOT/opt/telesaved/Dockerfile" 0644
copy_optional_file "$SOURCE_DIR/docker-compose.yml" "$PKG_ROOT/opt/telesaved/docker-compose.yml" 0644
copy_optional_file "$SOURCE_DIR/.dockerignore" "$PKG_ROOT/opt/telesaved/.dockerignore" 0644
copy_optional_file "$SOURCE_DIR/install-ldap-deps.sh" "$PKG_ROOT/opt/telesaved/install-ldap-deps.sh" 0755
copy_readmes "$PKG_ROOT/usr/share/doc/$PACKAGE_NAME"

# Always ship a safe example config when no project example already exists.
if [ -f "$SOURCE_DIR/config.example.json" ]; then
  cp "$SOURCE_DIR/config.example.json" "$PKG_ROOT/opt/telesaved/config.example.json"
else
  cat > "$PKG_ROOT/opt/telesaved/config.example.json" <<'CONFIGEXAMPLEEOF'
{
  "api_id": 123456,
  "api_hash": "replace_with_your_telegram_api_hash",
  "base_path": "/mnt/rda1/.vault/telesaved"
}
CONFIGEXAMPLEEOF
fi
chmod 0644 "$PKG_ROOT/opt/telesaved/config.example.json"

# Private/internal builds may bundle config.json. This file contains secrets, so it is OFF by default.
if [ "$INCLUDE_CONFIG" = "1" ]; then
  need_file "$SOURCE_DIR/config.json"
  cp "$SOURCE_DIR/config.json" "$PKG_ROOT/opt/telesaved/config.json"
  chmod 0600 "$PKG_ROOT/opt/telesaved/config.json"
  echo "/opt/telesaved/config.json" > "$PKG_ROOT/DEBIAN/conffiles"
  chmod 0644 "$PKG_ROOT/DEBIAN/conffiles"
fi

cat > "$PKG_ROOT/etc/systemd/system/telesaved.service" <<'SERVICEEOF'
[Unit]
Description=TeleSaved AI
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=/opt/telesaved
ExecStart=/usr/bin/python3 /opt/telesaved/app.py
Restart=always
RestartSec=5
User=root
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
SERVICEEOF
chmod 0644 "$PKG_ROOT/etc/systemd/system/telesaved.service"

cat > "$PKG_ROOT/DEBIAN/control" <<CONTROLEOF
Package: $PACKAGE_NAME
Version: $VERSION
Section: utils
Priority: optional
Architecture: $ARCH
Maintainer: $MAINTAINER
Depends: python3, python3-pip, python3-venv, ffmpeg
Homepage: https://localhost/telesaved-ai
Description: TeleSaved AI Telegram video archiver and AI media manager
 TeleSaved AI is a self-hosted Telegram video archiving, indexing,
 tagging, review, and media-management application.
CONTROLEOF
chmod 0644 "$PKG_ROOT/DEBIAN/control"

cat > "$PKG_ROOT/DEBIAN/preinst" <<'PREINSTEOF'
#!/bin/sh
set -e

# Extra safety: if a locally-managed config exists, keep a timestamped copy before package changes.
if [ -f /opt/telesaved/config.json ]; then
  ts="$(date +%Y%m%d-%H%M%S 2>/dev/null || echo unknown)"
  cp /opt/telesaved/config.json "/opt/telesaved/config.json.preinst-backup-$ts" 2>/dev/null || true
fi

exit 0
PREINSTEOF
chmod 0755 "$PKG_ROOT/DEBIAN/preinst"

cat > "$PKG_ROOT/DEBIAN/postinst" <<'POSTINSTEOF'
#!/bin/sh
set -e

mkdir -p /opt/telesaved/frontend
chmod 755 /opt/telesaved || true
chmod 644 /opt/telesaved/app.py || true
chmod 644 /opt/telesaved/frontend/index.html || true
chmod 644 /opt/telesaved/config.example.json || true
if [ -f /opt/telesaved/config.json ]; then
  chmod 600 /opt/telesaved/config.json || true
fi

systemctl daemon-reload >/dev/null 2>&1 || true
systemctl enable telesaved.service >/dev/null 2>&1 || true

if [ ! -f /opt/telesaved/config.json ]; then
  cat <<'MSG'
TeleSaved AI package installed, but /opt/telesaved/config.json is missing.

Create it before starting the service, for example:
  sudo cp /opt/telesaved/config.example.json /opt/telesaved/config.json
  sudo nano /opt/telesaved/config.json
  sudo systemctl restart telesaved
MSG
  exit 0
fi

if systemctl is-active --quiet telesaved.service; then
  systemctl restart telesaved.service || true
fi

cat <<'MSG'
TeleSaved AI package installed.

Notes:
- Existing /opt/telesaved/config.json is preserved.
- Python ML dependencies may need to be installed separately according to your deployment method.
MSG

exit 0
POSTINSTEOF
chmod 0755 "$PKG_ROOT/DEBIAN/postinst"

cat > "$PKG_ROOT/DEBIAN/prerm" <<'PRERMEOF'
#!/bin/sh
set -e

if [ "${1:-}" = "remove" ] || [ "${1:-}" = "deconfigure" ]; then
  systemctl stop telesaved.service >/dev/null 2>&1 || true
fi

exit 0
PRERMEOF
chmod 0755 "$PKG_ROOT/DEBIAN/prerm"

cat > "$PKG_ROOT/DEBIAN/postrm" <<'POSTRMEOF'
#!/bin/sh
set -e

systemctl daemon-reload >/dev/null 2>&1 || true

exit 0
POSTRMEOF
chmod 0755 "$PKG_ROOT/DEBIAN/postrm"

if command -v chown >/dev/null 2>&1; then
  chown -R root:root "$PKG_ROOT" 2>/dev/null || true
fi

dpkg-deb --build "$PKG_ROOT" "$OUTPUT_DEB"

echo "Built: $OUTPUT_DEB"
echo "Install with: sudo apt install ./$OUTPUT_DEB"
echo ""
echo "Config behavior:"
echo "  Default: config.json is NOT bundled, because it contains Telegram credentials."
echo "  Private build: TELESAVED_INCLUDE_CONFIG=1 ./build-telesaved-deb.sh"
