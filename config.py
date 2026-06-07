"""Central configuration for the Mail Analyzer app."""

from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

# Storage
CACHE_FILE = BASE_DIR / "mail_cache.json"
RECEIVED_FORMAT = "%Y-%m-%d %H:%M:%S"

# Outlook
OUTLOOK_INBOX_FOLDER = 6  # olFolderInbox

# Behaviour
REFRESH_INTERVAL_SECONDS = 3600
PREVIEW_CHARS = 800

# Server
HOST = "127.0.0.1"
PORT = 8080
