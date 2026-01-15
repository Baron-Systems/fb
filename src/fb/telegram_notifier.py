"""
Telegram notification module.
Sends backup success/failure notifications.
"""
from __future__ import annotations

import sqlite3
from typing import Any

import httpx


def send_telegram_notification(
    cx: sqlite3.Connection,
    message: str,
    parse_mode: str = "HTML"
) -> bool:
    """
    Send a notification via Telegram if enabled.
    Returns True if sent successfully, False otherwise.
    """
    try:
        # Get Telegram settings
        row = cx.execute("SELECT bot_token, chat_id, enabled FROM telegram_settings WHERE id=1").fetchone()
        
        if not row or not row["enabled"]:
            return False
        
        bot_token = row["bot_token"]
        chat_id = row["chat_id"]
        
        if not bot_token or not chat_id:
            return False
        
        # Send message
        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        payload = {
            "chat_id": chat_id,
            "text": message,
            "parse_mode": parse_mode,
        }
        
        with httpx.Client(timeout=10.0) as client:
            response = client.post(url, json=payload)
            response.raise_for_status()
        
        return True
    except Exception as e:
        print(f"WARNING: Failed to send Telegram notification: {e}")
        return False


def format_backup_success_message(agent_name: str, site: str, duration_s: float) -> str:
    """Format a backup success message for Telegram."""
    return f"""✅ <b>Backup Successful</b>

📦 <b>Site:</b> {site}
🖥 <b>Agent:</b> {agent_name}
⏱ <b>Duration:</b> {duration_s:.1f}s

Backup completed successfully."""


def format_backup_failure_message(agent_name: str, site: str, error: str) -> str:
    """Format a backup failure message for Telegram."""
    return f"""❌ <b>Backup Failed</b>

📦 <b>Site:</b> {site}
🖥 <b>Agent:</b> {agent_name}
⚠️ <b>Error:</b> {error}

Please check the system immediately."""

