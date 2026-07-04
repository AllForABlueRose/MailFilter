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
# User-defined automations (saved-search workflows that run periodically). Same
# encoded-at-rest JSON format as the other stores (see mailfilter/automation_store.py).
AUTOMATIONS_FILE = BASE_DIR / "automations_cache.json"
# Customer Management: user-defined organizations (name, formality category,
# member/representative domains, per-contact overrides). Same encoded-at-rest JSON
# format as the other stores (see mailfilter/customer_store.py). The contact
# directory itself is derived live from the mail cache, never persisted.
CUSTOMERS_FILE = BASE_DIR / "customers_cache.json"
# Suspected Customers List for the experimental "Resolve Customer Name To
# Downloads" feature: a flat list of customer names to look for in mail content at
# download time. Same encoded-at-rest JSON seam as the other stores (see
# mailfilter/customer_match_store.py).
CUSTOMER_MATCH_FILE = BASE_DIR / "customer_match_cache.json"
# Which experimental features the user has enabled (the "Experimental Features"
# sidebar section). A small flag set persisted through the same encoded-at-rest
# seam as the other stores (see mailfilter/experimental_store.py). Enablement is
# only "is this feature's control mounted in the sidebar"; a feature's own
# operational state (e.g. the password filter, the normalize-width toggle) lives
# in the search settings.
EXPERIMENTAL_FILE = BASE_DIR / "experimental_cache.json"
RECEIVED_FORMAT = "%Y-%m-%d %H:%M:%S"

# Atomic-write reliability. On Windows os.replace() can intermittently fail with
# PermissionError/WinError 5 when an external process (Defender / Search Indexer)
# briefly holds a handle to a freshly-created file, so the temp -> final rename is
# retried a few times before giving up. Applies to every encoded-at-rest write.
FILE_REPLACE_RETRIES = 5
FILE_REPLACE_DELAY_SECONDS = 0.1

# Key Vault (Workshop view). Per-organization credential storage, protected far
# more strongly than the other caches: the file is sealed with AES-256-GCM under
# a key derived from a user **master passphrase** via scrypt, so it is useless
# without the passphrase even to the same OS user (the caches are only
# DPAPI/base64, i.e. transparent to your own account). Secrets live ONLY here.
VAULT_FILE = BASE_DIR / "vault_cache.json"
# Non-secret per-org index ({org_id: {count, has_managed, has_temporary,
# last_scan_dt}}) so the Customer Management card can show a read-only "has keys"
# line WITHOUT unlocking the vault. It carries no secrets, so it uses the ordinary
# encoded-at-rest seam (DPAPI/base64) like the other caches.
VAULT_INDEX_FILE = BASE_DIR / "vault_index.json"
# Optional "remember on this machine" (the passphrase + DPAPI-assist unlock): the
# scrypt-derived key, DPAPI-wrapped, so a session can unlock without re-entering
# the passphrase. Only as strong as the OS account while present; deleted on
# "forget". Never the passphrase itself, and only created on an explicit opt-in.
VAULT_KEY_DPAPI_FILE = BASE_DIR / "vault_key.dpapi"
# scrypt KDF cost. N must be a power of two; 2**15 with r=8 needs ~32 MiB, which
# the store passes explicitly as maxmem so it is not clamped by the default.
VAULT_SCRYPT_N = 2 ** 15
VAULT_SCRYPT_R = 8
VAULT_SCRYPT_P = 1
VAULT_KEY_LEN = 32  # AES-256
VAULT_PASSPHRASE_MIN = 8
# Entry kinds: "managed" = user-entered customer-managed key; "temporary" =
# captured from a Smart Password Detection scan (carries the scan datetime).
VAULT_KINDS = ("managed", "temporary")
VAULT_LABEL_MAX = 120
VAULT_USERNAME_MAX = 200
VAULT_SECRET_MAX = 4096
VAULT_URL_MAX = 500
VAULT_MAX_ENTRIES_PER_ORG = 200
# Bucket id for captured keys whose sender resolves to no organization. Not a real
# org (no card), just a holding key in the same {org_id: [...]} map; a later
# Customer Management assignment re-homes the entry to the resolved org.
VAULT_UNASSIGNED_ORG_ID = "unassigned"
# Idle auto-lock: an unlocked vault re-locks after this many seconds without a
# successful access, so a walked-away session does not leave secrets reachable.
VAULT_LOCK_TIMEOUT_SECONDS = 900
# Temporary (SDS-captured) keys older than this many days are hidden from the vault
# list and search — the record is kept, just not shown until a newer mail re-records
# the same secret and refreshes its scan datetime. Managed keys are never hidden.
VAULT_TEMP_HIDE_AFTER_DAYS = 7

# Outlook
OUTLOOK_INBOX_FOLDER = 6  # olFolderInbox

# Behaviour
REFRESH_INTERVAL_SECONDS = 3600
# Card body excerpt. The card shows the message body up to whichever limit it
# hits first — PREVIEW_MAX_LINES lines or PREVIEW_CHARS characters — so a value
# (e.g. a password) further down the body is still visible to eyeball. Password
# DETECTION always scans the full cached body, never this excerpt.
PREVIEW_CHARS = 4000
PREVIEW_MAX_LINES = 50

# Automations. STEPS is the canonical set of action steps an automation can run
# on its matched mail, in execution order (see mailfilter/automation.py). The
# scheduler wakes every TICK and runs each enabled automation whose interval has
# elapsed since its last run; per-automation intervals are clamped to MIN.
AUTOMATION_STEPS = ("mark", "download", "report")
AUTOMATION_TICK_SECONDS = 30
AUTOMATION_MIN_INTERVAL_SECONDS = 60
AUTOMATION_DEFAULT_INTERVAL_SECONDS = 3600

# Customer Management. A domain/contact is tied to an organization with one of
# these roles: "member" (normal staff on the org's own domain) or "representative"
# (a 3rd party, or someone on a foreign domain, fronting the org). Resolution
# treats both as belonging to the org; the role is recorded for the future
# reply-formality engine (see mailfilter/customers.py, mailfilter/customer_store.py).
ORG_DOMAIN_ROLES = ("member", "representative")
# Per-string caps so a buggy/hostile client can't grow the customers cache without
# bound (org name, formality category, a single domain, a single email).
ORG_NAME_MAX = 120
# The org's optional display name (a nickname shown in the Customer Management
# view in place of the real `name`; blank => the real name is shown). Downstream
# workflows always resolve the real `name`, never this. Capped like the name.
ORG_DISPLAY_NAME_MAX = 120
ORG_CATEGORY_MAX = 60
# Org-card appearance (Customer Management). `card_style` swaps the card between
# the default outline look (white fill, coloured text/border) and a filled look
# (the org colour as background, white text/border); `card_pattern` overlays a
# subtle, uniform texture. Both are coerced to the first entry when unknown.
ORG_CARD_STYLES = ("outline", "filled")
ORG_CARD_PATTERNS = ("none", "dots", "lines", "grid", "checker")
# Free-text notes shown on the org card — things to be mindful of when dealing
# with this organization. This is also the home for future per-organization
# settings (they extend the coerced fields the same way), so the cap is generous.
ORG_NOTES_MAX = 2000
ORG_DOMAIN_MAX = 255
ORG_EMAIL_MAX = 320

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

# Bulk Compose. The app's only mailbox-WRITING feature: it turns an Excel sheet
# into reply DRAFTS in a shared mailbox (ReplyAll, from the shared address, with
# the shared address CC'd) for a human to review and send. It never sends mail.
# While BULK_MOCK_MODE is on, the shared-mailbox read and the draft creation are
# served by in-process mocks (no Outlook/pywin32 needed) so the whole pipeline is
# exercisable off the production machine. Flip it off there once verified.
BULK_MOCK_MODE = True
# The shared mailbox replies are drafted from (SentOnBehalfOf) and always CC'd to.
SHARED_MAILBOX_ADDRESS = "shared.services@example.com"
# Root of the file server the appended files are read from. Every resolved
# attachment path is confined to this directory (no traversal escapes it).
FILE_SERVER_DIR = BASE_DIR / "mock" / "fileserver"
# Base for the ftp_link() template function: ftp_link("x.pdf") -> this + "x.pdf".
FTP_LINK_BASE = "ftp://ftp.example.com/outgoing/"
# Domains treated as "internal" for sender.is_internal in templates. Lowercased.
INTERNAL_DOMAINS = ("example.com",)
# Mock backends used while BULK_MOCK_MODE is on. The shared inbox is a JSON list
# of mails shaped like the cache (id/subject/sender/sender_email/received/
# recipient_emails/cc_emails); created drafts are written here as one JSON each
# instead of Outlook's Drafts folder, so a run is inspectable.
MOCK_SHARED_INBOX_FILE = BASE_DIR / "mock" / "shared_inbox.json"
MOCK_DRAFTS_DIR = BASE_DIR / "mock" / "drafts"
# Named reply templates (master body text + the attachment-filename expression).
# Same encoded-at-rest JSON format as the other stores (see compose_template_store.py).
COMPOSE_TEMPLATES_FILE = BASE_DIR / "compose_templates_cache.json"
COMPOSE_TEMPLATE_NAME_MAX = 80
COMPOSE_TEMPLATE_BODY_MAX = 20000
COMPOSE_TEMPLATE_EXPR_MAX = 500
# Ingest cap: rows beyond this in an uploaded sheet are dropped (reported).
BULK_MAX_ROWS = 1000
# Newest-first cap on how many shared-mailbox messages a preview reads to match
# rows against (keeps a preview bounded on a large shared inbox).
BULK_SHARED_READ_LIMIT = 1000
# A row's datetime may differ from the matched mail's ReceivedTime by up to this
# many seconds and still match (clock/format slack between the sheet and Outlook).
BULK_MATCH_DATETIME_TOLERANCE_SECONDS = 60
# Spreadsheet header -> canonical row field, for the fields Bulk Compose reasons
# about (matching + attachment/FTP choice). Matching is case-insensitive on the
# trimmed header. EVERY column is still exposed to the DSL by its normalized
# header; these aliases just give the known ones stable names (row.file_name etc.).
BULK_COLUMNS = {
    "subject": "subject",
    "datetime": "datetime",
    "date": "datetime",
    "received": "datetime",
    "sender": "sender",
    "from": "sender",
    "file name": "file_name",
    "filename": "file_name",
    "file": "file_name",
    "ftp": "uses_ftp",
    "uses ftp": "uses_ftp",
}

# Smart Password Detection. An on-demand, rules-based, regex scan of cached mail
# bodies for plaintext passwords customers may have sent. No AI, no network —
# only stdlib regex over the already-cached body. Settings (patterns + rules)
# persist through the same encoded-at-rest seam as the other stores (see
# mailfilter/password_settings_store.py, mailfilter/password_detect.py). The scan
# is manual (a button); its results feed a sidebar filter + a per-card badge and
# are never written to the mailbox. See docs/system-design.md §3.14.
PASSWORD_SETTINGS_FILE = BASE_DIR / "password_settings_cache.json"
# A detection pattern is authored as a COMPONENT, not a raw regex (see
# password_detect.build_regex): the user writes the literal context they expect
# and drops the placeholder token below where the password sits. The context is
# treated as literal text whose whitespace/newlines compile to flexible "\s*", so
# a marker and a value on separate lines still match. The placeholder becomes the
# capturing group; its contents are an OPTIONAL per-pattern value regex (blank =
# the generic value matcher below).
PASSWORD_PLACEHOLDER = "<{(password_value)}>"
# What an empty per-pattern "password pattern" means: a run of non-whitespace
# (no spaces, tabs, or newlines). Shown in the UI as "*  (generic)".
PASSWORD_GENERIC_VALUE_REGEX = r"\S+"
# Seed components. Each is {template[, value_regex]}; the template carries exactly
# one PASSWORD_PLACEHOLDER. Components are identified by their position (a 1-based
# number), not a name. The last seed shows the layout cue: a line break in the
# template means the value may be on a new line in the mail.
PASSWORD_DEFAULT_PATTERNS = (
    {"template": "password: <{(password_value)}>"},
    {"template": "[password] <{(password_value)}>"},
    {"template": "pwd: <{(password_value)}>"},
    {"template": "password:\n<{(password_value)}>"},
)
# The six rules a candidate must satisfy to count as a password. The first four
# are on/off toggles; the last two are length bounds set by sliders (inclusive).
PASSWORD_RULE_DEFAULTS = {
    "no_japanese": True,    # rule 1: reject candidates containing Japanese script
    "no_link": True,        # rule 2: reject candidates that look like a URL
    "no_repeating": True,   # rule 3: reject a single repeating unit (aaaa, abab, ----)
    "no_file": True,        # rule 4: reject candidates that look like a filename
    "min_length": 8,        # rule 5: reject candidates shorter than this
    "max_length": 32,       # rule 6: reject candidates longer than this
}
# Slider bounds for the two length rules (the UI clamps to this range; so does
# the store, so a hostile client can't push a nonsense bound).
PASSWORD_LENGTH_FLOOR = 1
PASSWORD_LENGTH_CEIL = 128
# "Not a file" rule: a candidate ending in ".<one of these>" is treated as a
# filename, not a password, and rejected. Lowercased; compared case-insensitively.
PASSWORD_FILE_EXTENSIONS = (
    "pdf", "doc", "docx", "xls", "xlsx", "ppt", "pptx", "txt", "csv", "rtf",
    "zip", "rar", "7z", "gz", "tar", "png", "jpg", "jpeg", "gif", "bmp", "svg",
    "exe", "msi", "dmg", "json", "xml", "yml", "yaml", "html", "htm", "log",
    "mp3", "wav", "mp4", "mov", "avi", "mkv",
)
# "Not repeating symbols" rule, part 1: reject a candidate that is one repeating
# unit of period up to this many characters (period 1 = "aaaa"/"====", 2 =
# "abab"/"=-=-", ...).
PASSWORD_REPEAT_MAX_PERIOD = 2
# "Not repeating symbols" rule, part 2 (the styling case): reject a candidate made
# up ENTIRELY of non-alphanumeric symbols with at most this many distinct
# characters — the dividers/rules customers paste as styling ("==============",
# "----------", "==----==----", "=-~=-~"), regardless of their period. A varied
# all-symbol string (more distinct chars, e.g. "!@#$%^&*") is left alone, as is
# any candidate containing a letter or digit.
PASSWORD_STYLING_MAX_DISTINCT = 3
# Caps so a buggy/hostile client can't grow the settings file, or a single scan,
# without bound.
PASSWORD_TEMPLATE_MAX = 500       # the context box (literal text + placeholder)
PASSWORD_VALUE_REGEX_MAX = 500    # the optional per-pattern value regex
PASSWORD_MAX_PATTERNS = 50
PASSWORD_MAX_MATCHES_PER_MAIL = 20
# A scan only inspects mail received within this many days of today ("up to one
# month"); older mail is left unscanned and uncaptured.
PASSWORD_SCAN_MAX_AGE_DAYS = 30

# Experimental features. The known feature ids and their default enablement (all
# off, so the sidebar box starts empty). Enabling a feature mounts its control in
# the "Experimental Features" sidebar box; it does not by itself change search
# results. The ids match both the data-feature attributes in the markup and (for
# features that are also a search toggle) the search-settings key.
#   passwords         — Smart Password Detection (the 🔑 filter + Scan + Settings)
#   normalize_width   — Normalize Search Character Width (the 全角/半角 fold below)
#   attachment_search — extend the main/exclude keyword match to attachment names
#   link_search       — extend the main/exclude keyword match to link URLs
#   append_customer_name — append the sender's org name to batch-downloaded files
#   resolve_customer_name — append a Suspected Customers List name found in content
EXPERIMENTAL_DEFAULTS = {
    "passwords": False,
    "normalize_width": False,
    "attachment_search": False,
    "link_search": False,
    "append_customer_name": False,
    "resolve_customer_name": False,
}
# Normalize Search Character Width: the Unicode normalization form used to fold
# full-width (全角) and half-width (半角) variants of a character to one form so a
# keyword search on one width also matches the other. NFKC maps full-width Latin/
# digits/punctuation onto plain ASCII (and half-width katakana onto full-width).
# Applied only when the feature's toggle is on, and only to the main/exclude
# keyword fields (sender/recipient match exactly). See mailfilter/filters.py.
SEARCH_NORMALIZE_FORM = "NFKC"
# Caps on the Suspected Customers List (mailfilter/customer_match_store.py) so a
# buggy/hostile client can't grow the file without bound.
CUSTOMER_MATCH_MAX_NAMES = 500
CUSTOMER_MATCH_NAME_MAX = 200

# Server
HOST = "127.0.0.1"
PORT = 8080
