from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Optional

from .utils import FBError, atomic_write_text


def get_fb_bin_path() -> str:
    """Get the absolute path to the fb executable."""
    # Try to find fb in PATH
    result = subprocess.run(["which", "fb"], capture_output=True, text=True)
    if result.returncode == 0 and result.stdout.strip():
        return result.stdout.strip()
    
    # Fallback to common pipx location
    pipx_path = Path.home() / ".local" / "bin" / "fb"
    if pipx_path.exists():
        return str(pipx_path)
    
    # For development/testing: use "fb" and assume it's in PATH when installed
    # When dry-run, we just show what would be done
    return "fb"


def parse_time(time_str: str) -> tuple[int, int]:
    """Parse time string HH:MM into (hour, minute)."""
    try:
        parts = time_str.strip().split(":")
        if len(parts) != 2:
            raise ValueError
        hour = int(parts[0])
        minute = int(parts[1])
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ValueError
        return hour, minute
    except ValueError as e:
        raise FBError(f"Invalid time format. Expected HH:MM (00:00-23:59), got: {time_str}", exit_code=2) from e


def get_cron_line(profile: Optional[str], time_str: str, fb_bin: str, log_file: Optional[str] = None) -> str:
    """Generate a cron line for fb backup."""
    hour, minute = parse_time(time_str)
    
    profile_flag = f"--profile {profile}" if profile else ""
    log_redirect = f">> {log_file} 2>&1" if log_file else ""
    
    cmd = f"{fb_bin} {profile_flag} backup".strip()
    if log_redirect:
        cmd += f" {log_redirect}"
    
    return f"{minute} {hour} * * * {cmd}"


def get_user_crontab() -> str:
    """Get current user's crontab."""
    result = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
    if result.returncode == 0:
        return result.stdout
    return ""


def set_user_crontab(content: str) -> None:
    """Set current user's crontab."""
    result = subprocess.run(["crontab", "-"], input=content, text=True, capture_output=True)
    if result.returncode != 0:
        raise FBError(f"Failed to set crontab: {result.stderr}", exit_code=1)


def setup_cron_job(profile: Optional[str], time_str: str, dry_run: bool = False) -> str:
    """
    Add a cron job for fb backup.
    Returns the cron line that was added.
    """
    fb_bin = get_fb_bin_path()
    log_file = f"/var/log/fb-backup-{profile}.log" if profile else "/var/log/fb-backup.log"
    
    cron_line = get_cron_line(profile, time_str, fb_bin, log_file)
    marker = f"# fb-backup-{profile}" if profile else "# fb-backup"
    
    if dry_run:
        return cron_line
    
    # Read current crontab
    current = get_user_crontab()
    
    # Check if already exists
    if marker in current:
        raise FBError(f"Cron job already exists for {'profile ' + profile if profile else 'default'}. Use 'fb schedule remove' first.", exit_code=1)
    
    # Add new line
    new_crontab = current.rstrip() + "\n" + marker + "\n" + cron_line + "\n"
    
    # Set crontab
    set_user_crontab(new_crontab)
    
    return cron_line


def remove_cron_job(profile: Optional[str], dry_run: bool = False) -> bool:
    """
    Remove fb backup cron job.
    Returns True if a job was removed, False if not found.
    """
    marker = f"# fb-backup-{profile}" if profile else "# fb-backup"
    
    if dry_run:
        current = get_user_crontab()
        return marker in current
    
    current = get_user_crontab()
    if marker not in current:
        return False
    
    lines = current.splitlines()
    new_lines = []
    skip_next = False
    
    for line in lines:
        if line.strip() == marker:
            skip_next = True
            continue
        if skip_next and line.strip().startswith("#"):
            continue
        if skip_next and line.strip() != "":
            skip_next = False
            continue
        new_lines.append(line)
    
    new_crontab = "\n".join(new_lines) + "\n" if new_lines else ""
    set_user_crontab(new_crontab)
    
    return True


def list_cron_jobs() -> list[dict[str, str]]:
    """List all fb backup cron jobs."""
    current = get_user_crontab()
    lines = current.splitlines()
    
    jobs = []
    for i, line in enumerate(lines):
        if line.strip().startswith("# fb-backup"):
            marker = line.strip()
            profile = None
            if marker.startswith("# fb-backup-"):
                profile = marker.replace("# fb-backup-", "")
            
            # Next line should be the cron line
            if i + 1 < len(lines):
                cron_line = lines[i + 1]
                jobs.append({
                    "profile": profile or "(default)",
                    "schedule": cron_line,
                    "method": "cron",
                })
    
    return jobs


def get_systemd_service_name(profile: Optional[str]) -> str:
    """Get systemd service name for profile."""
    if profile:
        return f"fb-backup-{profile}.service"
    return "fb-backup.service"


def get_systemd_timer_name(profile: Optional[str]) -> str:
    """Get systemd timer name for profile."""
    if profile:
        return f"fb-backup-{profile}.timer"
    return "fb-backup.timer"


def get_systemd_service_path(profile: Optional[str]) -> Path:
    """Get systemd service file path."""
    name = get_systemd_service_name(profile)
    # User service
    return Path.home() / ".config" / "systemd" / "user" / name


def get_systemd_timer_path(profile: Optional[str]) -> Path:
    """Get systemd timer file path."""
    name = get_systemd_timer_name(profile)
    return Path.home() / ".config" / "systemd" / "user" / name


def setup_systemd_timer(profile: Optional[str], time_str: str, dry_run: bool = False) -> tuple[str, str]:
    """
    Create systemd service and timer for fb backup.
    Returns (service_content, timer_content).
    """
    fb_bin = get_fb_bin_path()
    hour, minute = parse_time(time_str)
    
    profile_flag = f"--profile {profile}" if profile else ""
    cmd = f"{fb_bin} {profile_flag} backup".strip()
    
    log_suffix = f"-{profile}" if profile else ""
    
    service_content = f"""[Unit]
Description=Frappe Backup (fb){f' - {profile}' if profile else ''}
After=network.target

[Service]
Type=oneshot
ExecStart={cmd}
StandardOutput=append:/var/log/fb-backup{log_suffix}.log
StandardError=append:/var/log/fb-backup{log_suffix}-error.log
"""

    timer_content = f"""[Unit]
Description=Frappe Backup Timer{f' - {profile}' if profile else ''}

[Timer]
OnCalendar=*-*-* {hour:02d}:{minute:02d}:00
Persistent=true

[Install]
WantedBy=timers.target
"""

    if dry_run:
        return service_content, timer_content
    
    service_path = get_systemd_service_path(profile)
    timer_path = get_systemd_timer_path(profile)
    
    # Check if already exists
    if service_path.exists() or timer_path.exists():
        raise FBError(f"Systemd timer already exists for {'profile ' + profile if profile else 'default'}. Use 'fb schedule remove' first.", exit_code=1)
    
    # Create directory
    service_path.parent.mkdir(parents=True, exist_ok=True)
    
    # Write files
    atomic_write_text(service_path, service_content)
    atomic_write_text(timer_path, timer_content)
    
    # Reload systemd
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
    
    # Enable and start timer
    timer_name = get_systemd_timer_name(profile)
    subprocess.run(["systemctl", "--user", "enable", timer_name], check=True)
    subprocess.run(["systemctl", "--user", "start", timer_name], check=True)
    
    return service_content, timer_content


def remove_systemd_timer(profile: Optional[str], dry_run: bool = False) -> bool:
    """
    Remove systemd service and timer for fb backup.
    Returns True if removed, False if not found.
    """
    service_path = get_systemd_service_path(profile)
    timer_path = get_systemd_timer_path(profile)
    
    if dry_run:
        return service_path.exists() or timer_path.exists()
    
    if not (service_path.exists() or timer_path.exists()):
        return False
    
    timer_name = get_systemd_timer_name(profile)
    
    # Stop and disable timer (ignore errors if not running)
    subprocess.run(["systemctl", "--user", "stop", timer_name], capture_output=True)
    subprocess.run(["systemctl", "--user", "disable", timer_name], capture_output=True)
    
    # Remove files
    if service_path.exists():
        service_path.unlink()
    if timer_path.exists():
        timer_path.unlink()
    
    # Reload systemd
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
    
    return True


def list_systemd_timers() -> list[dict[str, str]]:
    """List all fb backup systemd timers."""
    systemd_user_dir = Path.home() / ".config" / "systemd" / "user"
    if not systemd_user_dir.exists():
        return []
    
    jobs = []
    for timer_file in systemd_user_dir.glob("fb-backup*.timer"):
        name = timer_file.stem
        if name == "fb-backup":
            profile = "(default)"
        else:
            profile = name.replace("fb-backup-", "")
        
        # Get timer status
        result = subprocess.run(
            ["systemctl", "--user", "is-active", timer_file.name],
            capture_output=True,
            text=True
        )
        status = result.stdout.strip() if result.returncode == 0 else "inactive"
        
        # Read timer file for schedule
        timer_content = timer_file.read_text()
        schedule = "unknown"
        for line in timer_content.splitlines():
            if line.strip().startswith("OnCalendar="):
                schedule = line.split("=", 1)[1].strip()
                break
        
        jobs.append({
            "profile": profile,
            "schedule": schedule,
            "method": "systemd",
            "status": status,
        })
    
    return jobs


def list_all_schedules() -> list[dict[str, str]]:
    """List all fb backup schedules (cron + systemd)."""
    schedules = []
    schedules.extend(list_cron_jobs())
    schedules.extend(list_systemd_timers())
    return schedules

