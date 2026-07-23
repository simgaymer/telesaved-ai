# Packaging

Build a Debian package from the repository root:

```bash
chmod +x build-telesaved-deb.sh
./build-telesaved-deb.sh
```

Output appears in `dist/`:

```text
dist/telesaved-ai_4.4.2_amd64.deb
```

Install:

```bash
sudo apt install ./dist/telesaved-ai_4.4.2_amd64.deb
```

## Config handling

By default, `config.json` is not bundled because it contains Telegram credentials.

For private/internal builds only:

```bash
TELESAVED_INCLUDE_CONFIG=1 ./build-telesaved-deb.sh
```
