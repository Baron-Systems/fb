# Installation Guide - Frappe Manager Backup System

## Quick Install (Zero-Config)

### Dashboard (Backup Server)

```bash
# Install
pipx install git+https://github.com/Baron-Systems/fb.git

# Run
fb run
```

Dashboard will be available at: `http://localhost:7311`

### Agent (Production Server)

```bash
# Install
pipx install git+https://github.com/Baron-Systems/fb-agent.git

# Run
fb-agent run
```

Agent will automatically:
- Discover `fm` binary
- Generate stable agent ID
- Discover and register with Dashboard
- Start API on port 8888

## Installation as System Service

### Dashboard Service

```bash
# Copy service file
sudo curl -o /etc/systemd/system/fb-dashboard.service \
  https://raw.githubusercontent.com/Baron-Systems/fb/main/fb-dashboard.service

# Enable and start
sudo systemctl daemon-reload
sudo systemctl enable fb-dashboard
sudo systemctl start fb-dashboard

# Check status
sudo systemctl status fb-dashboard
```

### Agent Service

```bash
# Copy service file
sudo curl -o /etc/systemd/system/fb-agent.service \
  https://raw.githubusercontent.com/Baron-Systems/fb-agent/main/fb-agent.service

# Enable and start
sudo systemctl daemon-reload
sudo systemctl enable fb-agent
sudo systemctl start fb-agent

# Check status
sudo systemctl status fb-agent
```

## Logs

```bash
# Dashboard logs
sudo journalctl -u fb-dashboard -f

# Agent logs
sudo journalctl -u fb-agent -f
```

## Data Locations

### Dashboard
- **Database:** `~/.local/share/fb/fb.sqlite3`
- **Backups:** 
  - Preferred: `/srv/backups/`
  - Fallback: `~/.local/share/fb/backups/`

### Agent
- **State:** `~/.local/share/fb-agent/`
- **Shared Secret:** `~/.local/share/fb-agent/shared_secret.txt`

## Ports

- **Dashboard:** 7311 (HTTP)
- **Agent:** 8888 (HTTP)
- **Discovery:** 7310 (UDP broadcast)

## Firewall Configuration

### Dashboard Server

```bash
# Allow Dashboard port
sudo ufw allow 7311/tcp

# Allow UDP discovery (optional, for agent auto-discovery)
sudo ufw allow 7310/udp
```

### Production Server (Agent)

```bash
# Allow Agent port (from Dashboard server only)
sudo ufw allow from <dashboard_ip> to any port 8888 proto tcp
```

## Upgrade

```bash
# Dashboard
pipx upgrade fb

# Agent
pipx upgrade fb-agent
```

## Uninstall

```bash
# Stop services
sudo systemctl stop fb-dashboard fb-agent
sudo systemctl disable fb-dashboard fb-agent

# Remove services
sudo rm /etc/systemd/system/fb-dashboard.service
sudo rm /etc/systemd/system/fb-agent.service
sudo systemctl daemon-reload

# Uninstall packages
pipx uninstall fb fb-agent

# Remove data (optional)
rm -rf ~/.local/share/fb ~/.local/share/fb-agent
```

## Troubleshooting

### Agent can't find fm

```bash
# Ensure fm is in PATH
which fm

# Or create symlink
sudo ln -s /path/to/fm /usr/local/bin/fm
```

### Agent not connecting to Dashboard

1. Check Dashboard is running: `curl http://dashboard_ip:7311/`
2. Check firewall rules
3. Check Agent logs: `journalctl -u fb-agent -n 50`
4. Manually add agent in Dashboard UI if auto-discovery fails

### Backups not working

1. Check site is running: `fm list`
2. Start site: `fm start <site_name>`
3. Check Agent logs for errors
4. Check Dashboard audit logs in UI

## Support

For issues, please visit: https://github.com/Baron-Systems/fb/issues

