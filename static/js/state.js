// Shared mutable state. These scripts are plain (non-module) <script>s that
// share one global scope, so every cross-file global is declared exactly once,
// here. Loaded first; main.js (which calls init) is loaded last.

let resourcesOnly = false;
let passwordsOnly = false;       // sidebar "has a detected password" filter
let passwordSettings = null;     // last loaded /api/password-settings (patterns + rules)
let normalizeWidth = false;      // experimental: fold full-width<->half-width on keyword search
let attachmentSearch = false;    // experimental: main/exclude keyword match also covers attachment names
let linkSearch = false;          // experimental: main/exclude keyword match also covers link URLs
let appendCustomerName = false;  // experimental: append the resolved org name to batch-downloaded files
let dedupe = false;              // experimental: Brute Force Mail Deduplication — hide Zendesk notification mails, graft their link onto the twin
let experimentalEnabled = {};    // feature id -> bool, last loaded /api/experimental (which controls are mounted)

let mailById = {};          // id -> view model from the last load (drag source)
let trayMails = [];         // mails collected in the workspace
const trayIds = new Set();
let traySortNewestFirst = true;   // workspace sort direction (toggled by the button)
let trayLinksOnlyNew = false;     // tray 🔗 button "only new" mode (skip mails already tagged links)
let trayDownloadOnlyNew = false;  // tray ⬇ button "only new" mode (skip mails already tagged downloaded)
let cleanupArmed = false;         // "Cleanup Local Workspace" two-press guard: armed (red) after first press
let cleanupArmTimer = null;       // timeout id that auto-disarms the cleanup button

let threadMails = [];       // mails shown in the thread/message popup
let threadOldestFirst = true;

let templateBodies = {};    // template name -> settings, from the last /api/templates load

let automationsById = {};       // id -> automation, from the last /api/automations load
let editingAutomationId = null;  // id open in the builder, or null when creating a new one

let customersById = {};         // id -> organization, from the last /api/organizations load
let editingOrgId = null;         // id open in the org builder, or null when creating a new one
let contactDirectory = [];       // aggregated+resolved contacts, from the last /api/contacts load
let orgCategories = [];          // the selectable category list (/api/categories) — the Category field's autocomplete
let orgPartnerCategory = "Partner"; // the category whose MEMBER domains count as internal in reply templates
let selectedOrgId = null;        // org whose contacts the directory shows; null = unassigned contacts
let roleSortRepsOnTop = false;   // org-contact sort: representatives first when true, members first when false
let showRealOrgNames = false;    // while the "hold to reveal" button/key is held, show real org names instead of display names

let vaultStatus = null;          // last /api/vault/status {available, initialized, unlocked, dpapi_available, remembered}
let vaultEntries = {};           // org_id -> [redacted entry, ...], currently displayed (full list or search results)
let vaultOrgNames = {};          // org_id -> display name, for the Key Vaults list + the entry-editor org picker
let editingVaultEntryId = null;  // vault entry id open in the editor, or null when adding a new one
let vaultSecrets = {};           // entry_id -> secret, cached after a hover reveal / reveal-all (unlocked only)
let vaultPinned = {};            // entry_id -> true: kept visible via the per-key "keep" checkbox (session only)
let vaultSearch = "";            // current Password Manager search query (key value / org / datetime)
let vaultRevealAll = false;      // hovering the "reveal all" area reveals every key's value
let vaultRevealAllPinned = false;// "reveal all" checkbox ticked: keep every key visible (session only)
let vaultHoverId = null;         // entry id whose row is currently hovered (single-key hover reveal)

// Unlock Station (Workshop slide-in panel: keys explorer over today's workspace files).
let unlockOpen = false;          // is the slide-in panel open?
let unlockOrgMeta = {};          // org_id -> {name, color, card_style, card_pattern} from /api/organizations
let unlockKeyEntries = {};       // org_id -> [redacted key entry] shown in the key explorer (full list or search)
let unlockFiles = [];            // last /api/workspace/files listing {name, kind, encrypted, org_id, org_name, source}
let unlockWorkspaceExists = false;// whether today's workspace folder exists
let unlockAssignments = {};      // filename -> assigned vault entry_id (drag-drop; re-drop overwrites)
let unlockLastUnlocked = [];     // {org_id, file_kind, key_kind} from the last successful unlock (feeds Record)
let unlockKeySearch = "";        // key-explorer search query
let unlockFileSearch = "";       // file-explorer search query

// Workshop hub navigation + Key Vault reveal gateway.
let workshopScreen = 'hub';      // 'hub' | 'vault' | 'calendar' | 'workbench' (the visible Workshop sub-screen)
let vaultKeysRevealed = false;   // on the vault screen, has the user pressed "View keys" after unlocking?

// Workshop → Workbench Processing (read-only today's workspace files + "Bring Last Workspace to Today").
let workbenchFiles = [];         // today's workspace files (from /api/workspace/files)
let workbenchExists = false;     // whether today's workspace folder exists
let workbenchOrgMeta = {};       // org_id -> {name, color} from /api/organizations (colours + labels)

// Composer (template workbench: author a reply template, preview it against a picked mail).
let composerTemplates = [];       // [{id, name, color, body, attachment_expr, error}, ...] from /api/compose-templates
let composerTemplateId = null;    // template square currently selected (loaded into the editor); null = unsaved new
let composerBlocks = [];          // the draggable function palette, from /api/composer/blocks
let composerSamples = [];         // the 10 built-in example mails + their sheet rows
let composerFilters = [];         // the picker's emoji filters, from /api/composer/samples
let composerPick = null;          // {source: 'sample'|'mail', ref} the mail the preview renders against
let composerCacheMails = [];      // cache mails loaded into the picker so far (grows as you scroll)
let composerOffset = 0;           // how many cache mails have been fetched (the lazy-load cursor)
let composerHasMore = true;       // is there another page of cache mail to fetch?
let composerLoading = false;      // a page fetch is in flight (guards the scroll sentinel firing twice)
let composerFilterId = "all";     // active picker filter id
let composerTab = "edit";         // middle column: 'edit' | 'preview'
let composerSource = "";          // the template's SOURCE — in preview the text area holds the rendered reply instead
let composerPreviewTimer = null;  // debounce timer for the live preview call
let composerObserver = null;      // IntersectionObserver watching the picker's end-of-list sentinel
let composerCycleMs = 4500;       // how slowly the function palette cycles its demo cases
let composerCycleTimer = null;    // the palette's cycle interval
let composerCaseIndex = 0;        // which demo case the palette is showing
let composerHoverBlock = null;    // index of the hovered block (its cycle pauses so you can read it)

// Press (the reply-draft worklist: cache mail items + a template each -> drafts).
let pressTemplates = [];          // [{id, name, color, body, error}, ...] authored in Composer
let pressTemplateId = null;       // the template selected in the bar (the default / Apply to all)
let pressTemplateBarOpen = false; // is the template strip expanded?
let pressState = null;            // last /api/press/state {mailbox, outlook_available, ready}
let pressFilters = [];            // the picker's emoji filters (server registry, shared with Composer)
let pressItems = [];              // the worklist: [{mail, template_id, row, status, reasons, plan, variables, checked, result}]
let pressColumns = [];            // union of row.* variable names across the applied templates
let pressFilterId = "all";        // active emoji filter for loading mail items
let pressOffset = 0;              // lazy-load cursor into the filtered cache
let pressHasMore = true;          // is there another page of mail to load?
let pressLoading = false;         // a page fetch is in flight
let pressArmed = false;           // the two-press button is armed (press again to create)
let pressComputeTimer = null;     // debounce timer for recomputing after a cell edit
let pressPromptKind = null;       // which mailbox the inline address prompt is for

// Workshop → Calendar (file pins onto days).
let calendarYear = 0;            // year of the month currently shown (0 until first render)
let calendarMonth = 0;           // 0-based month currently shown
let calendarPins = [];           // last /api/calendar/pins listing [{id, date, filename, description, ...}]
let calendarWorkspaceFiles = []; // today's workspace files (from /api/workspace/files) shown in the bottom half
let calendarWorkspaceExists = false; // whether today's workspace folder exists
let pendingPinFilename = "";     // filename awaiting a description in the pin modal
let pendingPinDate = "";         // target day (YYYY-MM-DD) awaiting a description in the pin modal
