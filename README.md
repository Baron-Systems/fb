# Frappe Manager Backup Dashboard (fb)

**Zero-config backup system for Frappe Manager sites.**

## Features

- 🎯 **Zero-Config:** Just run `fb run` - everything auto-configured
- 🔐 **Secure:** HMAC signatures, CSRF protection, audit logging
- 📊 **Multi-Agent:** Manage backups across multiple production servers
- ⏰ **Scheduled:** Automatic daily backups with retention policies
- 🌐 **Web UI:** Clean interface built with Jinja2 + HTMX
- 📦 **Storage:** Organized structure: `/backups/<agent>/<stack>/<site>/<timestamp>/`
- 🔍 **Audit Trail:** Every action logged and traceable

## Quick Start

```bash
# Install
pipx install git+https://github.com/Baron-Systems/fb.git

# Run (starts on port 7311)
fb run
```

Visit: `http://localhost:7311`

## Requirements

- Python 3.11+
- Agent (`fb-agent`) running on production servers

## Documentation

- [Installation Guide](INSTALL.md)
- [Enhancements Applied](../ENHANCEMENTS_APPLIED.md)
- [Review Report](../REVIEW.md)

## Architecture

**Dashboard serves as:**
- Central management UI
- Backup orchestrator
- Storage server
- Scheduler (APScheduler)
- Multi-agent registry

**Storage Preference:**
1. `/srv/backups` (production standard)
2. `/backups` (if writable)
3. `~/.local/share/fb/backups` (fallback)

## systemd Service

```bash
# Install as service
sudo curl -o /etc/systemd/system/fb-dashboard.service \
  https://raw.githubusercontent.com/Baron-Systems/fb/main/fb-dashboard.service

sudo systemctl enable --now fb-dashboard
```

## Development

```bash
# Clone
git clone https://github.com/Baron-Systems/fb.git
cd fb

# Install in dev mode
pip install -e .

# Run
python -m fb.cli
```

## License

Proprietary

## Support

Issues: https://github.com/Baron-Systems/fb/issues
