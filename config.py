"""Central configuration for the Mail Analyzer 2.0 app."""

from datetime import timedelta
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

# Storage
CACHE_FILE = BASE_DIR / "mail_cache.json"
# In-progress marker for the cold-start full sync (mailfilter/bootstrap.py). It
# exists only while an initial sync is running or was interrupted; its presence
# (or a missing cache) is what tells startup to (re)run the bootstrap rather than
# treat a partial cache as complete. Removed once the first full sync finishes.
INITIAL_SYNC_MARKER = BASE_DIR / "mail_cache.syncing"
# Last-used sidebar search settings (keywords, filters, toggles), persisted so a
# relaunch restores the previous search. Encoded at rest like the mail cache.
SETTINGS_FILE = BASE_DIR / "settings_cache.json"
# Per-mail workspace action tags (attachments downloaded / links opened) with
# timestamps, so the tags persist across sessions and grey out after a week.
TAGS_FILE = BASE_DIR / "tags_cache.json"
# Named, switchable search templates (presets) live as individual PNG image
# files in this directory — one file per template, in the exact format the
# export/import feature uses (see mailfilter/imgcodec.py). The folder *is* the
# storage: there is no separate JSON cache. NOTE: deliberately not "templates/",
# which is the Jinja/HTML template folder. Because each file doubles as the
# portable export, template contents are obfuscated (image-packed) rather than
# DPAPI-encrypted at rest — search presets are far less sensitive than mail.
TEMPLATES_DIR = BASE_DIR / "search_templates"
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

# Incremental fetch lookback. The fetch scans the inbox newest-first and stops
# once it drops this far below the newest message already cached. A bare
# high-water mark (lookback of zero) silently misses any mail that lands *below*
# the newest cached message — delivered out of order, synced from another
# device, or moved into the folder carrying its original (older) ReceivedTime —
# and drops same-second arrivals at the boundary. Re-scanned messages inside the
# window are cheap: they are skipped by EntryID before any body/attachment is
# read. Widen this to catch later-but-older mail; set it to ``None`` to rescan
# the whole folder every refresh (slowest, but catches arbitrarily old moved-in
# mail). See docs/system-design.md §3.2.
FETCH_LOOKBACK = timedelta(days=7)

# Mails parsed per persisted batch. A fetch ingests in batches rather than
# accumulating everything and saving once at the end, so progress is visible in
# the UI, mail appears as it arrives, and an interruption keeps the batches
# already written (a from-scratch sync then resumes instead of restarting — see
# bootstrap.py). Each batch triggers a full atomic cache rewrite, so very small
# batches cost extra disk writes during a large initial sync; this is a balance
# between progress granularity and that overhead.
FETCH_BATCH_SIZE = 200

# Server
HOST = "127.0.0.1"
PORT = 8080
