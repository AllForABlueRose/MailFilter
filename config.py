"""Central configuration for the Mail Analyzer app."""

from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

# Storage
CACHE_FILE = BASE_DIR / "mail_cache.json"
# Last-used sidebar search settings (keywords, filters, toggles), persisted so a
# relaunch restores the previous search. Encoded at rest like the mail cache.
SETTINGS_FILE = BASE_DIR / "settings_cache.json"
# Reserved for the upcoming targeted/bulk attachment download feature.
# Single attachment downloads are streamed lazily from Outlook and never
# persisted here (see outlook.fetch_attachment).
ATTACHMENTS_DIR = BASE_DIR / "attachments"
RECEIVED_FORMAT = "%Y-%m-%d %H:%M:%S"

# Outlook
OUTLOOK_INBOX_FOLDER = 6  # olFolderInbox

# Behaviour
REFRESH_INTERVAL_SECONDS = 3600
PREVIEW_CHARS = 800

# Server
HOST = "127.0.0.1"
PORT = 8080
