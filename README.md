# fb - Frappe Backup Tool

**Production-ready backup solution for Frappe/ERPNext sites**

`fb` is a **pull-based**, **SSH+rsync** backup tool designed for dedicated backup servers. No frappe/bench installation required on the backup server.

## ✨ Key Features

- 🔄 **Pull-based backups** - Backup server initiates and controls the process
- 🌐 **Multi-site support** - Backup unlimited sites from one or multiple benches
- 🗂️ **Multi-profile** - Manage multiple benches/servers from a single backup server
- 🔐 **SSH key authentication** - Secure, password-less connections
- 📦 **Per-site retention** - Individual retention policies for each site
- ✅ **Verify & Restore** - Built-in backup verification and safe restore
- 🐳 **Docker/Frappe Manager** - Native support for containerized setups
- 📅 **Automated scheduling** - Built-in cron/systemd integration
- 🔔 **Telegram alerts** - Optional notifications for backup status
- 🧪 **Dry-run mode** - Test before executing
- 🐍 **Pure Python** - No external CLI dependencies

---

## 📦 Installation

### Requirements

- Python 3.8+
- `pipx` (recommended) or `pip`
- SSH access to Frappe server(s)
- `rsync`, `tar`, `gzip` on both servers

### Install via pipx (recommended)

```bash
pipx install git+https://github.com/Baron-Systems/fb.git
```

### Verify installation

```bash
fb version
# Output: 0.7.0
```

---

## 🚀 Quick Start (5 minutes)

### Step 1: Initialize

```bash
fb init
```

Creates:
- `~/.config/fb/config.toml`
- `~/.config/fb/sites.conf`

### Step 2: Configure remote server

```bash
fb config set FRAPPE_REMOTE_HOST your-server.com
fb config set FRAPPE_REMOTE_USER frappe
fb config set FRAPPE_BENCH_PATH /home/frappe/frappe-bench
fb config set FRAPPE_LOCAL_BACKUP_ROOT /data/frappe-backups
```

### Step 3: Add sites

```bash
fb site add site1.example.com 30    # Keep backups for 30 days
fb site add site2.example.com 7     # Keep backups for 7 days
```

### Step 4: Test connection

```bash
fb test
```

### Step 5: Run your first backup

```bash
fb backup
```

### Step 6: Verify backups

```bash
fb verify
```

### Step 7: Setup automated backups

```bash
fb schedule setup --time 02:00
```

✅ Done! Your backups will now run automatically every day at 2:00 AM.

---

## 📚 Core Commands

### Backup Operations

```bash
# Backup all configured sites
fb backup

# Backup specific site only
fb backup --site site1.example.com

# Dry-run (see what would happen without executing)
fb backup --dry-run
```

### Verification

```bash
# Verify all latest backups
fb verify

# Verify specific site
fb verify --site site1.example.com

# Verify specific date
fb verify --site site1.example.com --date 2026-01-05
```

### Restore

```bash
# Dry-run restore (safe)
fb restore --site site1.example.com --date 2026-01-05 --dry-run

# Actual restore (requires --confirm)
fb restore --site site1.example.com --date 2026-01-05 --confirm
```

⚠️ **Restore is destructive** - Always test with `--dry-run` first!

### Site Management

```bash
# List configured sites
fb list

# Add new site
fb site add mysite.com 30

# Edit site retention
fb site edit mysite.com 60

# Remove site
fb site remove mysite.com

# Check status
fb status
```

### Configuration

```bash
# Show current configuration
fb config show

# Check configuration and tools
fb config check

# Set a config value
fb config set KEY VALUE

# Get a config value
fb config get KEY

# Remove a config value
fb config unset KEY
```

---

## 🗂️ Multi-Profile Setup

**Manage multiple Frappe servers from a single backup server.**

### Why use profiles?

- Backup multiple production benches
- Separate dev/staging/production environments
- Different retention policies per environment
- Isolated configuration per server

### Create profiles

```bash
# Production Bench 1
fb profile create prod-bench1 \
  --host prod1.example.com \
  --bench-path /home/frappe/frappe-bench \
  --user frappe \
  --local-backup-root /data/backups/prod1

# Production Bench 2
fb profile create prod-bench2 \
  --host prod2.example.com \
  --bench-path /home/frappe/frappe-bench \
  --user frappe \
  --local-backup-root /data/backups/prod2

# Development (using Frappe Manager)
fb profile create dev-bench \
  --host 192.168.1.100 \
  --bench-path /home/baron/frappe/sites/dev/workspace/frappe-bench \
  --user baron \
  --mode fm \
  --remote-bench dev.example.com \
  --fm-bin /home/baron/.local/bin/fm \
  --fm-transport export \
  --fm-export-dir /home/baron/frappe-exports \
  --local-backup-root /data/backups/dev
```

### Manage profiles

```bash
# List all profiles
fb profile list

# Show profile details
fb profile show prod-bench1

# Set default profile (optional)
fb profile set-default prod-bench1

# Delete profile
fb profile delete old-bench
```

### Use profiles

```bash
# Add sites to specific profile
fb --profile prod-bench1 site add site1.com 30
fb --profile prod-bench2 site add site2.com 30

# Backup specific profile
fb --profile prod-bench1 backup

# Backup using default profile
fb backup

# All commands support --profile flag
fb --profile prod-bench1 verify
fb --profile prod-bench1 status
fb --profile prod-bench1 list
```

### Profile directory structure

```
~/.config/fb/
├── profiles/
│   ├── prod-bench1/
│   │   ├── config.toml
│   │   └── sites.conf
│   ├── prod-bench2/
│   │   ├── config.toml
│   │   └── sites.conf
│   └── dev-bench/
│       ├── config.toml
│       └── sites.conf
└── default_profile
```

---

## 📅 Automated Scheduling

**Built-in scheduling without manual cron/systemd editing.**

### Setup scheduling

```bash
# Schedule daily backup at 2:00 AM using cron (default)
fb schedule setup --time 02:00

# Use systemd timer instead
fb schedule setup --time 02:00 --method systemd

# Schedule for specific profile
fb --profile prod-bench1 schedule setup --time 02:00
fb --profile prod-bench2 schedule setup --time 03:00
```

### Manage schedules

```bash
# List all scheduled backups
fb schedule list

# Example output:
# PROFILE       METHOD   SCHEDULE          STATUS
# prod-bench1   systemd  *-*-* 02:00:00   active
# prod-bench2   cron     0 3 * * * fb...  enabled

# Remove schedule
fb schedule remove

# Remove for specific profile
fb --profile prod-bench1 schedule remove

# Remove specific method only
fb schedule remove --method cron
```

### Scheduling methods

#### Cron (simple & universal)

```bash
fb schedule setup --time 02:00 --method cron
```

- ✅ Works on all systems
- ✅ No sudo required
- 📁 Logs: `/var/log/fb-backup.log`

#### Systemd (modern & robust)

```bash
fb schedule setup --time 02:00 --method systemd
```

- ✅ Better logging (journald integration)
- ✅ Persistent (runs missed schedules)
- 📁 View logs: `journalctl --user -u fb-backup.service`
- 🔍 Check status: `systemctl --user status fb-backup.timer`

---

## 🐳 Docker & Frappe Manager Support

### Docker Mode

For Frappe running in Docker containers:

```bash
fb profile create docker-prod \
  --host docker-host.com \
  --bench-path /home/frappe/frappe-bench \
  --mode docker \
  --docker-container frappe-bench-frappe-1
```

How it works:
1. `fb` runs `docker exec <container> bench backup`
2. Uses `docker cp` to copy artifacts to host `/tmp`
3. Pulls artifacts via `rsync`

### Frappe Manager (fm) Mode

For Frappe managed by [Frappe Manager](https://github.com/rtCamp/Frappe-Manager):

```bash
fb profile create fm-prod \
  --host fm-host.com \
  --bench-path /home/baron/frappe/sites/mysite/workspace/frappe-bench \
  --mode fm \
  --remote-bench mysite.example.com \
  --fm-bin /home/baron/.local/bin/fm \
  --fm-transport export \
  --fm-export-dir /home/baron/frappe-exports
```

#### FM Transport Modes

**Export mode** (default, recommended):
```bash
--fm-transport export
--fm-export-dir /path/to/export/dir
```
- Stages backups to export directory
- Uses rsync for transfer
- More reliable for large backups

**Stream mode** (experimental):
```bash
--fm-transport stream
```
- Direct tar stream over SSH
- No staging required
- May have issues with output contamination

#### FM Configuration Keys

- `FRAPPE_REMOTE_MODE=fm` - Enable fm mode
- `FRAPPE_REMOTE_BENCH` - Bench name for `fm shell <BENCH>`
- `FRAPPE_FM_BIN` - Path to fm executable (default: `/home/baron/.local/bin/fm`)
- `FRAPPE_FM_TRANSPORT` - `export` or `stream`
- `FRAPPE_FM_EXPORT_DIR` - Export directory (required for export mode)

---

## 📋 Configuration Reference

### Required Configuration

| Key | Description | Example |
|-----|-------------|---------|
| `FRAPPE_REMOTE_HOST` | Remote Frappe server hostname/IP | `prod.example.com` |
| `FRAPPE_REMOTE_USER` | SSH user on remote server | `frappe` |
| `FRAPPE_BENCH_PATH` | Absolute path to bench directory | `/home/frappe/frappe-bench` |

### Optional Configuration

| Key | Default | Description |
|-----|---------|-------------|
| `FRAPPE_REMOTE_MODE` | `bench` | Mode: `bench`, `docker`, or `fm` |
| `FRAPPE_LOCAL_BACKUP_ROOT` | `/data/frappe-backups` | Local backup storage path |
| `FRAPPE_DOCKER_CONTAINER` | - | Container name (required for docker mode) |
| `FRAPPE_REMOTE_BENCH` | - | Bench name (required for fm mode) |
| `FRAPPE_FM_BIN` | `/home/baron/.local/bin/fm` | fm executable path |
| `FRAPPE_FM_TRANSPORT` | `export` | fm transport: `export` or `stream` |
| `FRAPPE_FM_EXPORT_DIR` | - | Export directory for fm export mode |
| `TELEGRAM_TOKEN` | - | Telegram bot token for alerts |
| `TELEGRAM_CHAT_ID` | - | Telegram chat ID for alerts |

### Configuration Priority (highest to lowest)

1. **Environment variables** - `export FRAPPE_REMOTE_HOST=...`
2. **Profile config** - `~/.config/fb/profiles/<PROFILE>/config.toml`
3. **Global config** - `~/.config/fb/config.toml`
4. **Built-in defaults**

---

## 📁 Backup Storage Layout

```
/data/frappe-backups/
├── site1.example.com/
│   ├── 2026-01-05/
│   │   ├── database.sql.gz         # Compressed database dump
│   │   ├── files.tar               # Public files
│   │   └── private-files.tar       # Private files
│   ├── 2026-01-04/
│   │   ├── database.sql.gz
│   │   ├── files.tar
│   │   └── private-files.tar
│   └── .meta/
│       └── last_run.json           # Metadata for last backup
└── site2.example.com/
    └── ...
```

### Backup artifacts

| File | Contents | Required |
|------|----------|----------|
| `database.sql.gz` | Compressed MariaDB dump | ✅ Yes |
| `files.tar` | Public files (`sites/<site>/public/files`) | ✅ Yes |
| `private-files.tar` | Private files (`sites/<site>/private/files`) | ✅ Yes |
| `site_config_backup.json` | Site configuration backup | ✅ Yes |

---

## 🔐 Security Model

### SSH Security

- ✅ **SSH key authentication only** - No password prompts
- ✅ **BatchMode enabled** - Fails if password is required
- ✅ **StrictHostKeyChecking** - Prevents MITM attacks
- ✅ **Connection timeouts** - Prevents hanging connections

### Access Control

- ✅ **No sudo required** - Runs as regular user
- ✅ **Whitelisted commands** - Only predefined bench commands
- ✅ **No user input in commands** - Prevents command injection
- ✅ **Strict path validation** - Prevents path traversal

### Data Protection

- ✅ **Secret redaction** - Tokens hidden in logs
- ✅ **Atomic writes** - No partial states on failure
- ✅ **Strict site name validation** - Only `[a-z0-9.-_]` allowed

---

## 🧪 Complete Example Workflow

### Scenario: Production + Staging + Development

```bash
# 1. Install fb
pipx install git+https://github.com/Baron-Systems/fb.git

# 2. Create profiles for each environment
fb profile create production \
  --host prod.example.com \
  --bench-path /home/frappe/frappe-bench \
  --user frappe \
  --local-backup-root /data/backups/production

fb profile create staging \
  --host staging.example.com \
  --bench-path /home/frappe/frappe-bench \
  --user frappe \
  --local-backup-root /data/backups/staging

fb profile create development \
  --host 192.168.1.100 \
  --bench-path /home/baron/frappe/sites/dev/workspace/frappe-bench \
  --user baron \
  --mode fm \
  --remote-bench dev.example.com \
  --fm-bin /home/baron/.local/bin/fm \
  --fm-transport export \
  --fm-export-dir /home/baron/frappe-exports \
  --local-backup-root /data/backups/development

# 3. Set production as default
fb profile set-default production

# 4. Add sites to each profile
fb --profile production site add site1.com 90
fb --profile production site add site2.com 90
fb --profile staging site add staging.site.com 14
fb --profile development site add dev.site.com 7

# 5. Test connections
fb --profile production test
fb --profile staging test
fb --profile development test

# 6. Setup automated schedules
fb --profile production schedule setup --time 02:00 --method systemd
fb --profile staging schedule setup --time 03:00 --method systemd
fb --profile development schedule setup --time 04:00 --method cron

# 7. Verify schedules
fb schedule list

# 8. Manual backup (optional - test before scheduling)
fb --profile production backup
fb --profile staging backup
fb --profile development backup

# 9. Verify backups
fb --profile production verify
fb --profile staging verify
fb --profile development verify

# 10. Check status
fb --profile production status
fb --profile staging status
fb --profile development status
```

✅ All environments now have automated daily backups!

---

## 🔧 Advanced Usage

### Export Backups

Copy backups to external storage or remote server:

```bash
# Copy to local directory
fb export --site site1.com --date 2026-01-05 --to /mnt/external-drive/

# Copy to remote server (using rsync)
fb export --site site1.com --date 2026-01-05 --to backup-user@remote-server:/backups/

# Restore will use files from:
# /mnt/external-drive/site1.com/2026-01-05/
```

### Telegram Notifications

Get alerts on backup failures:

```bash
# Configure Telegram
fb config set TELEGRAM_TOKEN "your_bot_token"
fb config set TELEGRAM_CHAT_ID "your_chat_id"

# Test notification
fb backup  # Will send alert on failure
```

### Dry-Run Everything

Test commands safely:

```bash
fb --dry-run backup
fb --dry-run verify
fb --dry-run restore --site site1.com --date 2026-01-05
fb --dry-run schedule setup --time 02:00
```

### Verbose Logging

Debug issues:

```bash
fb --verbose backup
fb --verbose --profile prod test
```

---

## 🐛 Troubleshooting

### Connection Issues

```bash
# Test SSH connection
ssh frappe@your-server.com "bench --version"

# Test with fb
fb test

# Check configuration
fb config show
fb config check
```

### Permission Errors

```bash
# Ensure SSH key is added
ssh-copy-id frappe@your-server.com

# Verify no password prompt
ssh -o BatchMode=yes frappe@your-server.com "echo OK"
```

### Backup Failures

```bash
# Check last run status
fb status

# Verify with verbose mode
fb --verbose backup --site site1.com

# Check remote bench
ssh frappe@your-server.com "cd /home/frappe/frappe-bench && bench --site site1.com backup"
```

### Scheduling Issues

```bash
# Check cron logs
grep fb /var/log/syslog

# Check systemd logs
journalctl --user -u fb-backup.service -f

# Verify schedule
fb schedule list

# Test manual execution
fb backup
```

### Frappe Manager (fm) Issues

```bash
# Test fm shell access
ssh user@host "fm shell <BENCH> <<'EOF'
bench --version
EOF"

# Check bench path inside fm
ssh user@host "fm shell <BENCH> <<'EOF'
pwd
ls -la
EOF"

# Verify export directory exists
ssh user@host "mkdir -p /path/to/export/dir && ls -la /path/to/export/dir"
```

---

## 📖 Help & Documentation

```bash
# Main help
fb --help

# Command-specific help
fb backup --help
fb restore --help
fb profile --help
fb schedule --help

# Show version
fb version

# Check system
fb test
```

---

## 🏗️ Architecture

### Modules

- `cli.py` - Command-line interface and routing
- `config.py` - Configuration and profile management
- `sites.py` - Sites registry management
- `remote.py` - Secure SSH command execution
- `rsync.py` - rsync wrapper for file transfer
- `backup_engine.py` - Backup orchestration
- `verify.py` - Backup verification
- `restore.py` - Safe restore workflow
- `retention.py` - Retention policy enforcement
- `schedule.py` - Automated scheduling (cron/systemd)
- `metadata.py` - Run metadata management
- `notifications.py` - Telegram notifications
- `utils.py` - Shared utilities

### Design Principles

- **Pull-based** - Backup server controls the process
- **Modular** - Each module has a single responsibility
- **Secure** - SSH keys, no sudo, strict validation
- **Atomic** - All operations complete or fail entirely
- **Testable** - Comprehensive dry-run support
- **Portable** - Stdlib-only CLI dependencies

---

## 🤝 Contributing

Contributions welcome! Please:

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Test thoroughly with `--dry-run`
5. Submit a pull request

---

## 📄 License

MIT License - see LICENSE file for details

---

## 🔗 Links

- **Repository**: https://github.com/Baron-Systems/fb
- **Frappe Framework**: https://frappeframework.com
- **ERPNext**: https://erpnext.com
- **Frappe Manager**: https://github.com/rtCamp/Frappe-Manager

---

## 📮 Support

For issues and questions:

1. Check the troubleshooting section above
2. Run `fb --verbose <command>` to see detailed logs
3. Open an issue on GitHub with logs and configuration (redact sensitive info)

---

**Made with ❤️ for the Frappe/ERPNext community**
