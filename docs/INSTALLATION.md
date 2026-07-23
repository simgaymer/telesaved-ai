# Installation

TeleSaved AI is designed for a Debian/Ubuntu-style host and installs cleanly under `/opt/telesaved`.

## Required system packages

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip python3-dev build-essential git ffmpeg curl
```

## Install from source checkout

```bash
sudo mkdir -p /opt/telesaved
sudo cp -a app.py frontend build-telesaved-deb.sh config.example.json requirements.txt systemd scripts install-ldap-deps.sh /opt/telesaved/
cd /opt/telesaved
sudo cp config.example.json config.json
sudo nano config.json
sudo ./scripts/install-telesaved-venv.sh
```

## Service management

```bash
sudo systemctl status telesaved --no-pager
sudo systemctl restart telesaved
sudo journalctl -u telesaved -f
```

## First run

When the app first loads, create the initial local Admin account. Keep this local Admin account available even when LDAP is enabled so you have emergency access if the identity backend is unavailable.
