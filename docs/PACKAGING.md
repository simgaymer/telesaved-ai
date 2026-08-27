# Packaging

Build a Debian package from the repository root:

```bash
chmod +x build-telesaved-deb.sh
./build-telesaved-deb.sh
```

Output appears in `dist/`:

```text
dist/telesaved-ai_4.5.0_all.deb
```

Install:

```bash
sudo apt install ./dist/telesaved-ai_4.5.0_all.deb
```

The package:
- Creates a dedicated non-root `telesaved` system user.
- Installs the application under `/opt/telesaved/`.
- Builds the Python venv on the target machine (avoids cross-distribution venv incompatibilities).
- Installs and enables a systemd service unit pointing at the venv Python interpreter.
- Sets `HOME` and `XDG_CACHE_HOME` to `/opt/telesaved` so AI model caches land in the correct directory.

## Config handling

By default, `config.json` is not bundled because it contains Telegram credentials.

For private/internal builds only:

```bash
TELESAVED_INCLUDE_CONFIG=1 ./build-telesaved-deb.sh
```

## Post-install

After installing the package, open `http://SERVER_IP:8080/` and complete the setup wizard. No manual `config.json` editing is required.

To authorize Telegram (required for downloads):

```bash
sudo telesaved-telegram-login
```
