# Installation Guide

This guide assumes Debian or Ubuntu with systemd.

## 1. Install system dependencies

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip python3-dev build-essential git ffmpeg curl
```

## 2. Install the application

```bash
sudo mkdir -p /opt/telesaved
sudo cp -a app.py frontend build-telesaved-deb.sh config.example.json requirements.txt systemd scripts install-ldap-deps.sh /opt/telesaved/
cd /opt/telesaved
```

## 3. Create the Python virtual environment

```bash
sudo ./scripts/install-telesaved-venv.sh
```

The installer creates `/opt/telesaved/venv`, installs CPU PyTorch, installs TeleSaved dependencies, installs CLIP, installs the systemd unit, and starts the service if `config.json` exists.

## 4. Start TeleSaved

```bash
sudo systemctl daemon-reload
sudo systemctl enable telesaved
sudo systemctl restart telesaved
sudo systemctl status telesaved --no-pager
```

Open `http://SERVER_IP:8080/` — the setup wizard will guide you through Telegram credential configuration and admin account creation.

## Debian package build

```bash
./build-telesaved-deb.sh
sudo apt install ./dist/telesaved-ai_4.5.0_all.deb
```

The package installs files under `/opt/telesaved`, creates a dedicated `telesaved` system user, and registers a systemd service. The Python venv is built on the target machine on first install.

## Upgrade notes

- Back up `/opt/telesaved/config.json`.
- Back up the configured `base_path` metadata files.
- Apply the new files or install the new package.
- Restart `telesaved.service`.
- Schema migrations run automatically on startup — no manual DB changes required.
