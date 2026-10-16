# Drive Manager Sync

A modern, self-hosted web application for managing network storage mounts, fstab entries, sync jobs, rclone remotes, and media renaming — all from a single container with a responsive, themeable web UI.

![Dashboard](https://img.shields.io/badge/status-production-green.svg) ![FastAPI](https://img.shields.io/badge/backend-FastAPI-009688.svg) ![Python](https://img.shields.io/badge/python-3.11+-blue.svg) ![Docker](https://img.shields.io/badge/docker-ready-2496ED.svg)

---

## What it does

Drive Manager Sync is a **single-container, single-process** web application built with FastAPI and a vanilla-JS frontend. It gives you a unified dashboard to:

| Capability | Description |
|---|---|
| **Dashboard** | Real-time system metrics (CPU, memory, uptime, processes), disk-space usage bars for local and network mounts, recent activity, system processes, and system logs — all refreshing live. |
| **File Browser** | Navigate directories and files across mount points with breadcrumb navigation, tree view, and up/root shortcuts. |
| **Mounts** | Add NFSv4, SMB/CIFS, and Rclone mounts. Every mount is validated with read/write/rename/delete tests, and results include ownership info. |
| **Persistent Mounts (fstab)** | View and edit host `/etc/fstab` entries — toggle automount, nofail, mount-at-boot, and options. Entries are backed up before any change. |
| **Sync Jobs** | Schedule local-to-local or rclone copy/sync jobs (manual, cron, or event-driven). Mounts are auto-validated and self-healed before every run. |
| **Rclone** | Remote editor, mount/unmount under `/mnt/rclone/<remote>` with VFS caching, and one-click sync. |
| **Media Renamer** | Detect series, movies, or fall back to an LLM. Produces a Jellyfin-ready layout via atomic staging moves. |
| **Servers** | Configure NFS and SMB/CIFS sharing services (install, start, stop, restart). |
| **Settings** | Telegram/webhook notifications (failures only), password change, folder-picker roots, and UI theme selection. |

---

## Dashboard — at a glance

The dashboard is the command center. It updates **every 5 seconds** while you're on the tab:

- **System stats cards** — uptime, CPU %, memory %, process count
- **Disk Space panel** — per-path usage with color-coded progress bars (green <70%, amber 70–90%, red >90%). Hover a bar to reveal used/free GB.
- **Mounted Network Mounts panel** — every currently mounted network share with its own usage bar, type label (NFS/SMB), and hover details.
- **CPU & Memory** — live progress bars with percentage labels.
- **Recent Activity** — sync/media job runs with status and timestamps.
- **System Processes** — top 15 processes with PID, name, and state, in a scrollable table with sticky header.
- **System Logs** — log selector, level filter, line count, refresh button, and auto-follow.

All panels are **content-sized** — they shrink to fit their data instead of stretching to fill empty space. The grid reflows responsively from desktop (multiple columns) to tablet (two columns) to mobile (single column).

---

## Quick start

### Prerequisites

- Docker + Docker Compose
- Debian 12 / Ubuntu 22.04+ / Ubuntu 24.04+ host
- `/mnt` must be a **shared** mount (required for container mounts to reach host mount points):

```bash
findmnt -o TARGET,PROPAGATION /mnt   # should show "shared"
sudo mount --make-rshared /mnt       # if it doesn't
```

### One-command deploy

```bash
git clone https://github.com/tagorehyd/drive-manager-sync.git
cd drive-manager-sync
cp .env.example .env        # optional; tune TZ, WEBUI_PORT, etc.
docker compose up -d --build
```

Open `http://<host>:8080`. On first boot you're redirected to **/setup** to create the admin account. The setup password is printed in the container logs:

```bash
docker compose logs drive-manager | grep "setup password"
```

Or set it yourself with `SETUP_PASSWORD` in `.env`. After setup, sign in at **/login** (default credentials: `admin` / `T@gore123+` if pre-configured).

### Rebuild after code changes

Per project convention, rebuild and redeploy the container after every code change:

```bash
docker compose up -d --build
```

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                      Host System                            │
│  /etc/fstab   /mnt/Cloud4TB   /proc  /sys  Docker socket   │
└──────────────┬────────────────┬─────────────────────────────┘
               │ nsenter        │ host PID / mount namespace
               ▼                ▼
┌─────────────────────────────────────────────────────────────┐
│                   drive-manager container                   │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────┐  │
│  │  FastAPI    │  │   uvicorn   │  │  SQLite (app.db)    │  │
│  │   backend   │  │    server   │  │  (run history cache)│  │
│  └──────┬──────┘  └──────┬──────┘  └──────────┬──────────┘  │
│         └────────────────┴────────────────────┘              │
│                    vanilla JS + CSS UI                        │
│  Dashboard • File Browser • Mounts • fstab • Sync • Rclone   │
│  Media • Servers • Settings                                  │
└─────────────────────────────────────────────────────────────┘
```

- **Config** lives in `/config/` as JSON (`auth.json`, `app.json`, `peers.json`, `fstab.json`, `sync-jobs.json`, `media-jobs.json`, `notifications.json`, `rclone.conf`, `secret.key`).
- **Data** lives in `/data/` (`app.db`, `logs/`, `media-staging/`, `rclone-cache/`).
- **Host access** uses `nsenter --target 1 --mount …` (requires `pid: host`, `SYS_ADMIN`, `SYS_PTRACE`, and AppArmor unconfined). NFS/SMB mounts execute **on the host** via nsenter, so they survive container restarts.

---

## Mounts and permission validation

Being mounted is not enough. Every mount passes a four-step test on a hidden probe file:

1. **read** — open and read the file
2. **write + fsync + read-back** — write data, flush to disk, verify it reads back
3. **rename** — rename the file
4. **delete** — delete the renamed file

Results are stored as `{"mounted", "readable", "writable", "rename_ok", "delete_ok"}`. The **Ownership** column shows the mount root's uid/gid next to the app's uid/gid.

### Self-healing

Before every sync or media job, source and destination mounts are validated. If unhealthy, the app unmounts and remounts up to **3 times** before failing. This applies to managed mounts and `/etc/fstab` entries under `/mnt`.

### Default mount options

| Type | Options |
|---|---|
| NFS | `mount -t nfs4 -o hard,noatime,_netdev,nofail,x-systemd.automount host:/export /mnt/<name>` |
| SMB | `mount -t cifs -o vers=3.0,uid=1000,gid=1000,file_mode=0664,dir_mode=0775,_netdev,nofail,credentials=… //server/share /mnt/<name>` |

Tick **Persist in /etc/fstab** to write a matching fstab line (replaced if one already exists for that mount point).

---

## fstab (Persistent Mounts)

The host `/etc/fstab` is read via nsenter at startup and on **Reload**. Each entry shows:

- Device, mount point, type, options
- Mounted status
- Automount (`x-systemd.automount`), Nofail flags
- Actions: Mount / Unmount / Edit / Import / Remove

**Edit** opens a modal where you can toggle:
- **Mount at boot** (adds/removes `noauto`)
- **Automount** (adds/removes `x-systemd.automount`)
- **Nofail** (adds/removes `nofail`)
- **Options** (free-form comma-separated list)

Before any change, a backup is written to `/etc/fstab.drive-manager.bak`, then the file is replaced atomically (write to `.tmp`, `fsync`, `os.replace`).

**Remove** requires typed confirmation (`REMOVE`).

---

## Sync jobs

Each job has: `name, source, destination, mode (copy|sync), enabled, schedule, retry_count`.

- **Local → local**: built-in engine, idempotent (skips files with same size + mtime), atomic writes, `sync` mode deletes extra destination files.
- **`remote:path`**: uses `rclone copy|sync`.
- Schedules: manual, every N minutes, hourly, daily, weekly, monthly, custom cron, or event-driven.
- Failed runs send **one** notification; no more until a run succeeds.
- Weekdays are written as names (`mon`) so cron and the scheduler agree. Times use the `TZ` time zone.

---

## Media renamer

Supported extensions: `.mkv .mp4 .avi .mov .m4v`. Detection order:

1. **Series** — `S01E02`, `1x02`, `Season 1 Episode 2`. Show name from file name or parent folder.
2. **Movie** — `Movie Title (2024)` or dotted release names (`Movie.Title.2024.1080p`).
3. **LLM fallback** — OpenAI-compatible `/chat/completions` endpoint (`OPENAI_COMPATIBLE_API_KEY`, optional `OPENAI_COMPATIBLE_BASE_URL`). Returns `{movie_name, year, language, imdb_id}`.

Output layout:

```
<destination>/Movies/<Category>/<Movie Name (Year)>/<Movie Name (Year)>.mkv
<destination>/Series/<Show>/Season 01/<Show> S01E02.mkv
```

Category comes from language rules in Settings (e.g. `Telugu=Tollywood`); otherwise the default category is used.

Processing: copy to `/data/media-staging/<job-id>` → size check → atomic move (`.tmp` + `fsync` + `os.replace`, or rename on same filesystem) → cleanup → delete source (move mode only). Files modified in the last 30 seconds are skipped until stable.

---

## Servers

Configure host sharing services:

- **NFS** — install, export shares, start/stop/restart the kernel server
- **SMB/CIFS** — install, add shares/users, start/stop/restart Samba

Each service page shows status badges and action buttons.

---

## Settings

| Section | What you can do |
|---|---|
| Notifications | Telegram bot token / chat ID, webhook URL (JSON POST). Failures only, with duplicate suppression. |
| Password | Change admin password (invalidates all other sessions). |
| Roots | Folder-picker root directories (default `/mnt,/data`). |
| Theme | Choose from Dark (slate), Matrix (green), Ocean (blue), Sunset (orange), Monochrome (gray). Saved in `localStorage`. |

---

## Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `TZ` | `UTC` | Time zone for schedules |
| `WEBUI_PORT` | `8080` | Container port published to host |
| `COOKIE_SECURE` | `false` | Set `true` behind HTTPS |
| `COOKIE_SAMESITE` | `lax` | `lax`, `strict`, or `none` |
| `CORS_ORIGINS` | `*` | Allowed cross-site origins |
| `SETUP_PASSWORD` | generated | First-boot password for `/setup` |
| `SESSION_DAYS` | `7` | Session lifetime |
| `RESET_AUTH` | `false` | `true` = forget login, force `/setup` |
| `BROWSE_ROOTS` | — | Comma list overriding folder-picker roots |
| `OPENAI_COMPATIBLE_API_KEY` / `_BASE_URL` | — | Media LLM fallback |
| `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` | — | Telegram defaults (also settable in Settings) |

---

## Health check

`GET /healthz` is public and returns:

```json
{"status": "ok", "config": true, "database": true, "rclone": true, "mounts": true, "disk_free": "812.4 GiB"}
```

`status` is `degraded` when an auto-mount is down.

---

## Security notes

⚠️ The container runs as **root** with `SYS_ADMIN`, `SYS_PTRACE`, host PID, and AppArmor unconfined. This is required for mounting and editing `/etc/fstab` — anyone who controls the web UI controls the host. **Do not expose it to the internet.** Put it behind a VPN or HTTPS reverse proxy and set `COOKIE_SECURE=true`.

- Passwords are hashed with **bcrypt**. Sessions are signed with `/config/secret.key` (mode 0600). Five failed logins lock the account for 5 minutes.
- SMB passwords are stored only on the host in `/etc/drive-manager/credentials/<id>` (root, mode 0600) — never in JSON.
- CORS defaults to any origin (`CORS_ORIGINS=*`). Restrict it to your own front-ends where possible.
- The Telegram token lives in `/config/notifications.json` (mode 0600). Protect `/config`.

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| `path /mnt is mounted on / but it is not a shared mount` | `sudo mount --make-rshared /mnt` |
| `nsenter: Operation not permitted` | Verify `pid: host`, `cap_add: [SYS_ADMIN, SYS_PTRACE]`, `apparmor:unconfined` |
| Mount shows **mounted** but **write** fails | NFS `root_squash`/`all_squash` or export permissions; SMB user rights. Compare the Ownership column. |
| Rclone mount fails | Ensure `/dev/fuse` is passed through. Check `Logs → rclone-<remote>.log` |
| Stale NFS handle | Unmount then Mount (jobs do this automatically, up to 3 times) |
| Dashboard metrics blank | Verify `/proc/uptime`, `/proc/stat`, `/proc/meminfo` are readable inside the container |

---

## Running tests

```bash
docker compose run --rm drive-manager pytest
```

---

## Repository layout

```
/config   auth.json app.json notifications.json peers.json sync-jobs.json media-jobs.json fstab.json rclone.conf secret.key
/data     app.db  logs/  media-staging/  rclone-cache/
/app      FastAPI backend, routes, templates, static UI (JS + CSS)
/tests    pytest suite for mounts, auth, sync, media
```

---

## License

Internal tooling. Use at your own risk — this container has host-level privileges.
