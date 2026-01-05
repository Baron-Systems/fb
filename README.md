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

### Multi-Profile Support (NEW in 0.6.0)

**Manage multiple Frappe benches/servers from a single backup server.**

Each profile has its own:
- Configuration (`config.toml`)
- Sites registry (`sites.conf`)
- Isolated backup directory structure

#### Create a profile

```bash
fb profile create prod-bench1 \
  --host frappe-prod-01.example.com \
  --bench-path /home/frappe/frappe-bench \
  --user frappe \
  --mode bench \
  --local-backup-root /data/backups/prod-bench1
```

For Frappe Manager (fm):

```bash
fb profile create fm-dev \
  --host 192.168.1.205 \
  --bench-path /home/baron/frappe/sites/dev.mby-solution.vip/workspace/frappe-bench \
  --user baron \
  --mode fm \
  --remote-bench dev.mby-solution.vip \
  --fm-bin /home/baron/.local/bin/fm \
  --fm-transport export \
  --fm-export-dir /home/baron/frappe-exports \
  --local-backup-root /data/backups/fm-dev
```

#### Manage profiles

```bash
# List profiles
fb profile list

# Show profile details
fb profile show prod-bench1

# Set default profile (used when --profile is not specified)
fb profile set-default prod-bench1

# Delete a profile
fb profile delete old-bench
```

#### Use profiles

```bash
# Add sites to a profile
fb --profile prod-bench1 site add site1.com 30
fb --profile prod-bench1 site add site2.com 7

# Backup using specific profile
fb --profile prod-bench1 backup

# Backup using default profile (if set)
fb backup

# Other commands work the same way
fb --profile prod-bench1 verify
fb --profile prod-bench1 status
fb --profile prod-bench1 restore --site site1.com --date 2026-01-05 --confirm
```

#### Profile storage

Profiles are stored in:

```
~/.config/fb/
├── profiles/
│   ├── prod-bench1/
│   │   ├── config.toml
│   │   └── sites.conf
│   ├── prod-bench2/
│   │   ├── config.toml
│   │   └── sites.conf
│   └── fm-dev/
│       ├── config.toml
│       └── sites.conf
└── default_profile (contains name of default profile)
```

### Automated Scheduling (NEW in 0.7.0)

**Built-in commands to schedule automatic backups (no manual cron/systemd editing required).**

#### Setup scheduling

```bash
# Schedule daily backup at 2:00 AM using cron (default)
fb schedule setup --time 02:00

# Schedule using systemd timer
fb schedule setup --time 02:00 --method systemd

# Schedule for specific profile
fb --profile prod-bench1 schedule setup --time 02:00
fb --profile prod-bench2 schedule setup --time 03:00
fb --profile fm-dev schedule setup --time 04:00
```

#### Manage schedules

```bash
# List all scheduled backups
fb schedule list

# Example output:
# PROFILE        METHOD   SCHEDULE                            STATUS
# (default)      cron     0 2 * * * fb backup >> /var/log/... enabled
# prod-bench1    systemd  *-*-* 02:00:00                      active

# Remove scheduled backup
fb schedule remove

# Remove specific method only
fb schedule remove --method cron
fb schedule remove --method systemd

# Remove for specific profile
fb --profile prod-bench1 schedule remove
```

#### Scheduling methods

**Cron (default)**:
- ✅ Simple, widely supported
- ✅ No sudo required
- ✅ Logs to `/var/log/fb-backup.log`
- ✅ Use: `fb schedule setup --time HH:MM --method cron`

**Systemd timer**:
- ✅ Modern, better logging
- ✅ Persistent (runs missed schedules on boot)
- ✅ Use: `fb schedule setup --time HH:MM --method systemd`
- ✅ Check status: `systemctl --user status fb-backup.timer`
- ✅ View logs: `journalctl --user -u fb-backup.service`

#### Complete example

```bash
# Create profiles
fb profile create prod-bench1 --host prod1.example.com --bench-path /home/frappe/frappe-bench
fb profile create prod-bench2 --host prod2.example.com --bench-path /home/frappe/frappe-bench

# Add sites to each profile
fb --profile prod-bench1 site add site1.com 30
fb --profile prod-bench2 site add site2.com 7

# Schedule backups
fb --profile prod-bench1 schedule setup --time 02:00 --method systemd
fb --profile prod-bench2 schedule setup --time 03:00 --method systemd

# Verify schedules
fb schedule list

# All backups will now run automatically!
```

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
- `FRAPPE_REMOTE_BENCH` (required when `FRAPPE_REMOTE_MODE=fm`)
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

### fm (Frappe Manager) mode

If you manage benches via **Frappe Manager (fm)**, set:

- `FRAPPE_REMOTE_MODE=fm`
- `FRAPPE_FM_EXPORT_DIR=/workspace/exports`
- `FRAPPE_FM_TRANSPORT=export` (default) or `stream`
- `FRAPPE_FM_BIN=/home/baron/.local/bin/fm`

In this mode `fb` will run bench commands via:

- `fm shell <BENCHNAME> <<'EOF' ... EOF` (stdin script; compatible with fm builds that don't accept extra args)

Notes:

- In fm mode (transport=export), `fb` exports the newest backup artifacts to `FRAPPE_FM_EXPORT_DIR/<SITE>/` on the remote host, then pulls from there via `rsync`.
- In fm mode (transport=stream), `fb` streams a tarball over SSH directly into `<LOCAL_ROOT>/<SITE>/<DATE>/` (no rsync for artifacts).
- `FRAPPE_BENCH_PATH` must still be correct *inside* the `fm shell` environment so it can `cd` and locate `sites/<SITE>/private/backups/`.
- If you see `fm shell ... Got unexpected extra arguments`, set `FRAPPE_REMOTE_BENCH=<benchname>` and ensure fb is using heredoc-based execution (current versions do).

Sites registry:

- `~/.config/fb/sites.conf` with lines: `SITE_NAME  RETENTION_DAYS`

### Commands Reference

#### Core Commands

```bash
# Initialize config directory and sites registry
fb init

# List configured sites
fb list

# Run backup (all sites or specific site)
fb backup [--site SITE] [--dry-run]

# Verify backups
fb verify [--site SITE] [--date YYYY-MM-DD] [--dry-run]

# Restore backup (requires --confirm)
fb restore --site SITE --date YYYY-MM-DD [--confirm|--dry-run]

# Export backup to external directory or remote host
fb export --site SITE --date YYYY-MM-DD --to /path/or/user@host:/path

# Show last run status for all sites
fb status

# Test connection and tools
fb test

# Show version
fb version
```

#### Site Management

```bash
# Add site with retention days
fb site add SITE RETENTION_DAYS

# Remove site from registry
fb site remove SITE

# Edit site retention
fb site edit SITE RETENTION_DAYS
```

#### Configuration Management

```bash
# Show effective configuration (redacted)
fb config show

# Check configuration and required tools
fb config check

# Get specific config key
fb config get KEY

# Set config key in config file
fb config set KEY VALUE

# Remove config key from config file
fb config unset KEY
```

#### Profile Management (Multi-bench/server support)

```bash
# Create a new profile
fb profile create NAME --host HOST --bench-path PATH [OPTIONS]

# List all profiles
fb profile list

# Show profile details
fb profile show NAME

# Set default profile
fb profile set-default NAME

# Delete a profile
fb profile delete NAME

# Use specific profile for any command
fb --profile NAME <command>
```

#### Schedule Management (Automated backups)

```bash
# Setup scheduled backup (cron or systemd)
fb schedule setup --time HH:MM [--method cron|systemd]

# List all scheduled backups
fb schedule list

# Remove scheduled backup
fb schedule remove [--method cron|systemd]

# Schedule for specific profile
fb --profile NAME schedule setup --time HH:MM
```

#### Global Options

```bash
# Dry-run mode (simulate without executing)
fb --dry-run <command>

# Verbose logging
fb --verbose <command>

# Use specific profile
fb --profile NAME <command>
```

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


