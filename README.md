# Drive Manager

Self-hosted web app for **Debian 12 / Ubuntu 22.04+ / Ubuntu 24.04+**. It covers:

- **Mounts**: NFSv4, SMB/CIFS and Rclone mounts. Each mount is checked with read, write, rename and delete tests, and the UI shows ownership.
- **Persistent mounts**: the host `/etc/fstab` is read and shown in the UI. You can mount, unmount, reload and remove entries (removal needs typed confirmation).
- **Sync jobs**: manual, every N minutes, hourly, daily, weekly, monthly, custom cron or event driven. Mounts are validated (and remounted if needed) before every run. Failed runs are retried.
- **Rclone**: remote editor, mount and unmount under `/mnt/rclone/<remote>` with a VFS cache, and Sync Now.
- **Media renamer**: detects series, then movies, then falls back to an LLM. It writes a Jellyfin layout through a staging folder using atomic moves.
- **Notifications**: Telegram and webhook, for failures only, with duplicate suppression.

The app is one container, one FastAPI process and one web UI. All configuration lives in `/config` as JSON and is the source of truth. SQLite (`/data/app.db`) is only a cache for run history and the list of processed media.

```
/config  auth.json app.json notifications.json peers.json sync-jobs.json media-jobs.json fstab.json rclone.conf secret.key
/data    app.db  logs/  media-staging/  rclone-cache/
```

## Quick start

```bash
cp .env.example .env            # optional
sudo mount --make-rshared /mnt  # only if needed, see below
docker compose up -d --build
```

Open `http://<host>:8080`. On first start you are sent to **/setup**. Creating the admin account requires the **setup password**:

```bash
docker compose logs drive-manager | grep "setup password"   # or: sudo cat config/setup-password
```

Set `SETUP_PASSWORD` in `.env` to choose it yourself. The file is deleted once setup completes, and a new one is generated after `RESET_AUTH=true`. After setup, you sign in at **/login**.

Run the tests inside the image:

```bash
docker compose run --rm drive-manager pytest
```

## How it works

| Concern | Implementation |
|---|---|
| Host access | `nsenter --target 1 --mount …` (needs `pid: host`, `SYS_ADMIN` and `SYS_PTRACE`) |
| NFS / SMB mounts | Run **on the host** through nsenter, so they keep working when the container stops. `nfs-common` / `cifs-utils` are installed on the host with `apt` the first time they are needed. |
| Rclone mounts | `rclone mount` inside the container on `/mnt/rclone/<remote>`. They reach the host through `/mnt:rshared`. Cache: `/data/rclone-cache`. |
| fstab | Read with `nsenter … cat /etc/fstab` at startup and on **Reload**. Before any change, a backup is written to `/etc/fstab.drive-manager.bak`, then the file is replaced atomically. |
| SMB passwords | Stored only on the host in `/etc/drive-manager/credentials/<id>` (root, mode 0600). They are never written to JSON. |
| Writes | Config files and copied files go to a `.tmp` file, are fsynced, then moved into place with `os.replace`. |

### `/mnt` must be a shared mount

```bash
findmnt -o TARGET,PROPAGATION /mnt     # should print "shared"
sudo mount --make-rshared /mnt         # if it does not (to make it permanent, add a systemd unit or use a bind mount in fstab)
```

## Mounts and permission validation

Being mounted is **not** enough. For every mount, **Check** (and every job start) runs these tests in a hidden test file:

`read` → `write + fsync + read-back` → `rename` → `delete`

The result is saved as `{"mounted", "readable", "writable", "rename_ok", "delete_ok"}`. The **Ownership** column shows the mount root's uid and gid next to the app's uid and gid. SMB mounts default to `uid=1000,gid=1000,file_mode=0664,dir_mode=0775`.

**Self-healing:** when a sync or media job starts, the source and destination mounts are validated. If one is unhealthy, the app runs an unmount and remount cycle up to **3 times** before the job fails. This works for managed mounts and for `/etc/fstab` entries under `/mnt`.

Default mount options:

| Type | Options |
|---|---|
| NFS | `mount -t nfs4 -o hard,noatime,_netdev,nofail,x-systemd.automount host:/export /mnt/<name>` |
| SMB | `mount -t cifs -o vers=3.0,uid=1000,gid=1000,file_mode=0664,dir_mode=0775,_netdev,nofail,credentials=… //server/share /mnt/<name>` |

Tick **Persist in /etc/fstab** to add the matching fstab line. The line is replaced if one already exists for that mount point.

## Sync jobs

Each job has: `name, source, destination, mode (copy|sync), enabled, schedule, retry_count`.

- Local to local uses a built-in engine. It is idempotent (files with the same size and mtime are skipped) and writes files atomically. `sync` mode also deletes extra files in the destination.
- A `remote:path` source or destination uses `rclone copy|sync`.
- If all retries fail, a notification is sent once. No further notifications are sent until a run succeeds.

The schedule builder produces standard cron. Weekdays are written as names (`mon`) so cron and APScheduler agree. Times use the `TZ` time zone.

## Media renamer

Supported files: `.mkv .mp4 .avi .mov .m4v`. Detection order:

1. **Series**: `S01E02`, `1x02`, `Season 1 Episode 2`. The show name comes from the file name, or from the parent folder if the file name has none.
2. **Movie**: `Movie Title (2024)` (dotted release names like `Movie.Title.2024.1080p` work too).
3. **LLM**: an OpenAI-compatible `/chat/completions` endpoint (`OPENAI_COMPATIBLE_API_KEY`, optional `OPENAI_COMPATIBLE_BASE_URL`). It returns `{movie_name, year, language, imdb_id}`.

Output layout:

```
<destination>/Movies/<Category>/<Movie Name (Year)>/<Movie Name (Year)>.mkv
<destination>/Series/<Show>/Season 01/<Show> S01E02.mkv
```

The category comes from the language through the rules in Settings (for example `Telugu=Tollywood`). Otherwise the default category is used.

Processing steps: copy to `/data/media-staging/<job-id>` → check the size → move to the destination (`.tmp` + fsync + `os.replace`, or a rename when on the same filesystem) → clean up → delete the source (move mode only). Files modified in the last 30 s are skipped until they stop changing.

## Health

`GET /healthz` is public:

```json
{"status": "ok", "config": true, "database": true, "rclone": true, "mounts": true, "disk_free": "812.4 GiB"}
```

`status` is `degraded` when an auto-mount is down.

## Environment

| Variable | Default | Purpose |
|---|---|---|
| `TZ` | `UTC` | Time zone for schedules |
| `WEBUI_PORT` | `8080` | Published port |
| `COOKIE_SECURE` | `false` | Set `true` behind HTTPS |
| `COOKIE_SAMESITE` | `lax` | `lax`, `strict` or `none` (cross-site cookies, forces Secure) |
| `CORS_ORIGINS` | `*` | Allowed cross-site origins (comma list) |
| `SETUP_PASSWORD` | generated | First-boot password for `/setup` |
| `SESSION_DAYS` | `7` | Session lifetime |
| `RESET_AUTH` | `false` | `true` = forget the login and force `/setup` (remove afterwards) |
| `BROWSE_ROOTS` | | Comma list overriding the folder-picker roots (default `/mnt,/data`) |
| `OPENAI_COMPATIBLE_API_KEY` / `_BASE_URL` | | Media LLM fallback |
| `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` | | Telegram defaults (can also be set in Settings) |

## Security notes

- The container runs as root with `SYS_ADMIN`, `SYS_PTRACE`, host PID and AppArmor unconfined. That is needed to mount and to edit `/etc/fstab`, and it means **anyone who controls the web UI controls the host**. Do not expose it to the internet. Put it behind a VPN or an HTTPS reverse proxy and set `COOKIE_SECURE=true`.
- Passwords are hashed with bcrypt. Sessions are signed with `/config/secret.key` (mode 0600). Changing the password invalidates all other sessions. Five failed logins (or wrong setup passwords) lock the account for 5 minutes.
- Cross-site requests are allowed. CORS defaults to any origin with credentials (`CORS_ORIGINS=*`); restrict it to your own front-ends where possible. With the default `COOKIE_SAMESITE=lax`, browsers only send the session cookie on top-level cross-site navigations. Set `COOKIE_SAMESITE=none` (HTTPS required) for cross-site `fetch` calls with cookies. With `CORS_ORIGINS=*` and `COOKIE_SAMESITE=none`, any website you visit while logged in can call the API as you.
- The Telegram token is stored in `/config/notifications.json` (mode 0600). Protect `/config`.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `path /mnt is mounted on / but it is not a shared mount` | `sudo mount --make-rshared /mnt` |
| `nsenter: … Operation not permitted` | Check `pid: host`, `cap_add: [SYS_ADMIN, SYS_PTRACE]` and `apparmor:unconfined` |
| Mount shows **mounted** but **write** fails | NFS `root_squash` / `all_squash` or export permissions. SMB user rights. Compare the ownership column. |
| Rclone mount fails | Make sure `/dev/fuse` is passed through. See `Logs → rclone-<remote>.log` |
| Stale NFS handle | Unmount, then Mount (jobs do this automatically, up to 3 times) |
# drive-manager-sync
