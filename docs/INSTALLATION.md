# Installation

TeleSaved AI is designed for a Debian/Ubuntu-style host and installs cleanly under `/opt/telesaved`.

## Install from Debian package (recommended)

Build the package from the repository root:

```bash
chmod +x build-telesaved-deb.sh
./build-telesaved-deb.sh
sudo apt install ./dist/telesaved-ai_4.5.0_all.deb
```

The package:
- Creates a dedicated non-root `telesaved` system user.
- Installs files under `/opt/telesaved`.
- Creates and populates the Python venv on first install.
- Registers and enables a systemd service.

After installation, open `http://SERVER_IP:8080/` and complete the setup wizard.

## Install from source

### Required system packages

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip python3-dev build-essential git ffmpeg curl
```

### Copy application files

```bash
sudo mkdir -p /opt/telesaved
sudo cp -a app.py frontend build-telesaved-deb.sh config.example.json requirements.txt systemd scripts install-ldap-deps.sh /opt/telesaved/
cd /opt/telesaved
```

### Create the Python virtual environment

```bash
sudo ./scripts/install-telesaved-venv.sh
```

The installer creates `/opt/telesaved/venv`, installs CPU PyTorch, installs TeleSaved dependencies, installs CLIP, installs the systemd unit, and starts the service.

### Service management

```bash
sudo systemctl daemon-reload
sudo systemctl enable telesaved
sudo systemctl restart telesaved
sudo systemctl status telesaved --no-pager
```

Open `http://SERVER_IP:8080/` and complete the 4-step setup wizard to configure Telegram credentials, vault path, and the initial Admin account.

## First run

On first launch with no `config.json`, TeleSaved automatically shows the setup wizard:

1. **Welcome** — legal disclaimer acknowledgement.
2. **Credentials** — Telegram API ID, API hash, and phone number. Validated live against Telegram before advancing.
3. **Vault path** — where downloaded media and metadata are stored.
4. **Admin account** — username and password for the initial Admin user.

After completing the wizard, add a Telegram source under Administration → Sources.

## Upgrade notes

- Back up `/opt/telesaved/config.json`.
- Back up the configured `base_path` metadata files (database, auth, jobs).
- Apply the new files or install the new package.
- Restart `telesaved.service`.
- Schema migrations run automatically on startup — no manual DB changes required.
