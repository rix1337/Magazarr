<p align="center">
  <img src="assets/magazarr-logo.png" alt="Magazarr" width="520">
</p>

Magazarr is a small magazine-only companion for Quasarr.

It keeps a free-text magazine list, searches Quasarr for recent issues, sends chosen releases back to Quasarr for download, imports the largest PDF from completed JDownloader folders, and exposes the library through OPDS.

Magazarr automatically searches active titles every 60 minutes by default and checks completed downloads for import every 5 minutes by default. Set either interval to `0` to disable that background task.

## Run

```bash
uv run magazarr
```

Open `http://127.0.0.1:8090`.

## Docker

Local build:

```bash
uv build
mkdir -p docker/dist
cp dist/*.whl docker/dist/
docker build -t magazarr:local docker
```

Local run:

```bash
docker run --rm \
  -p 8090:8090 \
  -v "$PWD/config:/config" \
  -v "$PWD/library:/library" \
  -v "$PWD/output:/output" \
  magazarr:local
```

Compose template lives at `docker/docker-compose.yml`. Replace `ghcr.io/your-github-user/magazarr:latest` with your published image.

## Required Settings

- Quasarr URL and API key.
- Completed package folders reported by Quasarr must be visible to Magazarr at the exact path returned in Quasarr history.
- Library directory for imported PDFs. Docker defaults to `/library`; local runs default to `library`.

## Run Quasarr Locally For Testing

In a second terminal:

```bash
cd ~/PythonProjects/Quasarr
INTERNAL_ADDRESS=http://127.0.0.1:8080 uv run quasarr
```

Use the API key printed by Quasarr in Magazarr settings:

- Quasarr URL: `http://127.0.0.1:8080`
- Search category: `7000`
- Download category: `docs`

Quasarr still needs working JDownloader credentials and at least one configured magazine-capable hostname before a real download can complete.

## Quasarr Integration

Magazarr uses the Quasarr Newznab/SABnzbd shim:

- Search: `GET /api?t=search&q=<title>&cat=7000&apikey=<key>`
- Download: `GET /api?mode=addurl&name=<quasarr download link>&cat=docs&apikey=<key>`
- Import status: `GET /api?mode=history&apikey=<key>`

## OPDS

OPDS root is:

```text
http://127.0.0.1:8090/opds
```

Optional basic auth applies only to OPDS routes.

Imported PDF issue entries expose a cover image link. Magazarr renders page 1 of the
PDF as a cached PNG on first cover request.

## License

MIT License. Copyright (c) 2026 RiX.
