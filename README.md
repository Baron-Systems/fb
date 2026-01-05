## fb (Frappe Backup)

`fb` is a **pull-based**, **backup-server-only** CLI for backing up **Frappe / ERPNext** sites over **SSH** and **rsync**.

- **No local bench / frappe required**
- **Remote bench commands executed via SSH**
- **Backups are pulled to the backup server via rsync**
- **Multi-site per bench is first-class**
- **Per-site retention**, **verify**, and **safe restore**

### Installation (pipx)

Install from git (example):

- `pipx install git+https://github.com/Baron-Systems/fb.git`

### Quick start

1. Initialize config and registry:

- `fb init`

2. Configure the remote host and bench path:

- `fb config set FRAPPE_REMOTE_HOST frappe-prod-01.example.com`
- `fb config set FRAPPE_REMOTE_USER frappe`
- `fb config set FRAPPE_BENCH_PATH /home/frappe/frappe-bench`
- `fb config set FRAPPE_LOCAL_BACKUP_ROOT /data/frappe-backups`

3. Add sites:

- `fb site add site1.com 30`
- `fb site add site2.com 7`

4. Run backups:

- `fb backup`
- `fb backup --site site1.com`

5. Verify backups:

- `fb verify`
- `fb verify --site site1.com --date 2026-01-05`

6. Restore (requires explicit confirmation):

- `fb restore --site site1.com --date 2026-01-05 --confirm`
- `fb restore --site site1.com --date 2026-01-05 --dry-run`

### Backup layout (local)

For each site, backups are stored as:

- `<LOCAL_ROOT>/<SITE>/<YYYY-MM-DD>/database.sql.gz`
- `<LOCAL_ROOT>/<SITE>/<YYYY-MM-DD>/files.tar`
- `<LOCAL_ROOT>/<SITE>/<YYYY-MM-DD>/private-files.tar` (optional)
- `<LOCAL_ROOT>/<SITE>/.meta/last_run.json`

### Security model

- **SSH key auth only** (`ssh -o BatchMode=yes`, passwords disabled)
- **No sudo**
- **Strict site name validation** (no path traversal)
- **Remote commands are not user-provided**; `fb` constructs and executes a **small, whitelisted set** of safe commands
- **Secrets never printed** (Telegram token is redacted)

### Configuration

Config is loaded from:

- Environment variables (highest precedence)
- Config file (default: `~/.config/fb/config.toml`)

Required:

- `FRAPPE_REMOTE_MODE` (default: `bench`, values: `bench` | `docker`)
- `FRAPPE_REMOTE_HOST`
- `FRAPPE_BENCH_PATH`

Optional defaults:

- `FRAPPE_REMOTE_USER=frappe`
- `FRAPPE_LOCAL_BACKUP_ROOT=/data/frappe-backups`
- `FRAPPE_DOCKER_CONTAINER` (required when `FRAPPE_REMOTE_MODE=docker`)
- `TELEGRAM_TOKEN` / `TELEGRAM_CHAT_ID`

### Docker / fm mode

If your Frappe/ERPNext is managed via **Docker/fm**, set:

- `FRAPPE_REMOTE_MODE=docker`
- `FRAPPE_DOCKER_CONTAINER=<container_name>`

In this mode `fb` will:

- Run `bench` inside the container via `docker exec`
- Stage backup artifacts from container → remote host `/tmp` via `docker cp`
- Pull them to the backup server via `rsync` (still pull-based)

Tip: to find your container name on the remote host, run `docker ps` and pick the Frappe/ERPNext container that has `bench` available.

Sites registry:

- `~/.config/fb/sites.conf` with lines: `SITE_NAME  RETENTION_DAYS`

### Commands

- `fb init`
- `fb site add|remove|edit ...`
- `fb list`
- `fb backup [--site SITE] [--dry-run]`
- `fb verify [--site SITE] [--date YYYY-MM-DD] [--dry-run]`
- `fb restore --site SITE --date YYYY-MM-DD (--confirm | --dry-run)`
- `fb status`
- `fb test`
- `fb version`
- `fb config show|check|set|get|unset`

### Architecture / modules

- `fb/cli.py`: argparse CLI + command routing (stdlib-only)
- `fb/config.py`: Config load/store (env + TOML)
- `fb/sites.py`: `sites.conf` registry
- `fb/remote.py`: secure SSH execution wrapper
- `fb/rsync.py`: rsync pull/push wrapper
- `fb/backup_engine.py`: orchestrates remote backup + pull + retention + metadata
- `fb/verify.py`: verifies local backup artifacts
- `fb/restore.py`: safe restore flow (verify-first, maintenance-mode, restore db/files)
- `fb/retention.py`: per-site pruning
- `fb/metadata.py`: run metadata + last_run.json
- `fb/notifications.py`: Telegram notifications (optional)
- `fb/utils.py`: logging, validation, subprocess helpers


