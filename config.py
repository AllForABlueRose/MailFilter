"""Central configuration for the Mail Analyzer 2.0 app."""

from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

# Storage
CACHE_FILE = BASE_DIR / "mail_cache.json"
# Last-used sidebar search settings (keywords, filters, toggles), persisted so a
# relaunch restores the previous search. Encoded at rest like the mail cache.
SETTINGS_FILE = BASE_DIR / "settings_cache.json"
# Per-mail workspace action tags (attachments downloaded / links opened) with
# timestamps, so the tags persist across sessions and grey out after a week.
TAGS_FILE = BASE_DIR / "tags_cache.json"
# Workspace exports — "download all attachments" and "export report" — save into
# a dated subfolder here (see routes.api_download / routes.api_report). Single
# attachment downloads remain streamed lazily from Outlook and are never
# persisted (see outlook.fetch_attachment).
WORKSPACE_DIR = BASE_DIR / "workspace"
RECEIVED_FORMAT = "%Y-%m-%d %H:%M:%S"

# Outlook
OUTLOOK_INBOX_FOLDER = 6  # olFolderInbox

# Behaviour
REFRESH_INTERVAL_SECONDS = 3600
PREVIEW_CHARS = 800

# Server
HOST = "127.0.0.1"
PORT = 8080
