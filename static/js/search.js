// The sidebar: search settings, the resources toggle, and loading the list.

// Sidebar setting key -> input element id. `resources` is a boolean handled
// separately via the toggle button.
const SETTINGS_FIELDS = {
    start: 'startDate',
    end: 'endDate',
    main: 'mainKeywords',
    optional: 'optionalKeywords',
    exclude: 'excludeKeywords',
    sender: 'senderFilter',
    recipient: 'recipientFilter',
    exclude_sender: 'excludeSenderFilter',
    exclude_recipient: 'excludeRecipientFilter',
    attachment_blacklist: 'attachmentBlacklist',
    links_blacklist: 'linksBlacklist',
    dedupe_subject: 'dedupeSubjectInput',
};

function syncResourcesButton(){
    const btn = document.getElementById('resourcesToggle');
    btn.classList.toggle('active', resourcesOnly);
    btn.setAttribute('aria-pressed', String(resourcesOnly));
    btn.textContent = `📎 Attachments & Links: ${resourcesOnly ? 'On' : 'Off'}`;
}

function toggleResources(){
    resourcesOnly = !resourcesOnly;
    syncResourcesButton();
    saveSettings();
    highlightActiveTemplate();
    loadMail();
}

function syncPasswordsButton(){
    const btn = document.getElementById('passwordsToggle');
    btn.classList.toggle('active', passwordsOnly);
    btn.setAttribute('aria-pressed', String(passwordsOnly));
    btn.textContent = `🔑 Has Password: ${passwordsOnly ? 'On' : 'Off'}`;
}

function togglePasswords(){
    passwordsOnly = !passwordsOnly;
    syncPasswordsButton();
    saveSettings();
    highlightActiveTemplate();
    loadMail();
}

function syncNormalizeWidthButton(){
    const btn = document.getElementById('normalizeWidthToggle');
    btn.classList.toggle('active', normalizeWidth);
    btn.setAttribute('aria-pressed', String(normalizeWidth));
    btn.textContent = `🔀 Normalize Width: ${normalizeWidth ? 'On' : 'Off'}`;
}

function toggleNormalizeWidth(){
    normalizeWidth = !normalizeWidth;
    syncNormalizeWidthButton();
    saveSettings();
    highlightActiveTemplate();
    loadMail();
}

function syncAttachmentSearchButton(){
    const btn = document.getElementById('attachmentSearchToggle');
    btn.classList.toggle('active', attachmentSearch);
    btn.setAttribute('aria-pressed', String(attachmentSearch));
    btn.textContent = `📎 Search Attachments: ${attachmentSearch ? 'On' : 'Off'}`;
}

function toggleAttachmentSearch(){
    attachmentSearch = !attachmentSearch;
    syncAttachmentSearchButton();
    saveSettings();
    highlightActiveTemplate();
    loadMail();
}

function syncLinkSearchButton(){
    const btn = document.getElementById('linkSearchToggle');
    btn.classList.toggle('active', linkSearch);
    btn.setAttribute('aria-pressed', String(linkSearch));
    btn.textContent = `🔗 Search Links: ${linkSearch ? 'On' : 'Off'}`;
}

function toggleLinkSearch(){
    linkSearch = !linkSearch;
    syncLinkSearchButton();
    saveSettings();
    highlightActiveTemplate();
    loadMail();
}

function syncAppendCustomerNameButton(){
    const btn = document.getElementById('appendCustomerNameToggle');
    btn.classList.toggle('active', appendCustomerName);
    btn.setAttribute('aria-pressed', String(appendCustomerName));
    btn.textContent = `🏢 Append Customer Name: ${appendCustomerName ? 'On' : 'Off'}`;
}

// A workspace/download preference, not a filter — persist it but don't reload the
// list (nothing about the displayed mail changes).
function toggleAppendCustomerName(){
    appendCustomerName = !appendCustomerName;
    syncAppendCustomerNameButton();
    saveSettings();
}

// "Brute Force Resolve Customer Name" has no per-search toggle: it is governed
// solely by enabling/disabling its experimental feature, uniformly for the
// mail-list pill, the download name, and the CSV report (one shared source). The
// Suspected Customers List (its keyword->org mappings) is managed separately.

function syncDedupeButton(){
    const btn = document.getElementById('dedupeToggle');
    btn.classList.toggle('active', dedupe);
    btn.setAttribute('aria-pressed', String(dedupe));
    btn.textContent = `🧬 Deduplicate Mail: ${dedupe ? 'On' : 'Off'}`;
}

// A view transform on the list (hides notifications / grafts links), so reload.
function toggleDedupe(){
    dedupe = !dedupe;
    syncDedupeButton();
    saveSettings();
    highlightActiveTemplate();
    loadMail();
}

// The notification subject changed — persist and reload (it changes what's hidden).
function onDedupeSubjectChange(){
    saveSettings();
    loadMail();
}

// `resources`, `passwords`, `normalize_width`, `attachment_search`, `link_search`
// and `append_customer_name` are booleans handled via toggle buttons; the rest are
// text fields mapped by SETTINGS_FIELDS.
const SETTINGS_BOOLS = ['resources', 'passwords', 'normalize_width',
                        'attachment_search', 'link_search', 'append_customer_name',
                        'dedupe'];

function currentSettings(){
    const settings = {resources: resourcesOnly, passwords: passwordsOnly,
                      normalize_width: normalizeWidth,
                      attachment_search: attachmentSearch, link_search: linkSearch,
                      append_customer_name: appendCustomerName,
                      dedupe: dedupe};
    for(const [key, id] of Object.entries(SETTINGS_FIELDS)){
        settings[key] = document.getElementById(id).value;
    }
    return settings;
}

// Persist the last-used search server-side so a relaunch restores it.
function saveSettings(){
    fetch('/api/settings', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(currentSettings()),
    }).catch(() => {});
}

async function restoreSettings(){
    try{
        const response = await fetch('/api/settings');
        applySettings(await response.json());
    }catch(e){
        // No saved settings (or fetch failed) — start from blank defaults.
    }
}

// Populate every sidebar field from a settings object (the shape /api/settings
// and each template return). Shared by restore-on-load and template switching.
function applySettings(settings){
    settings = settings || {};
    for(const [key, id] of Object.entries(SETTINGS_FIELDS)){
        document.getElementById(id).value = settings[key] || '';
    }
    resourcesOnly = !!settings.resources;
    syncResourcesButton();
    passwordsOnly = !!settings.passwords;
    syncPasswordsButton();
    normalizeWidth = !!settings.normalize_width;
    syncNormalizeWidthButton();
    attachmentSearch = !!settings.attachment_search;
    syncAttachmentSearchButton();
    linkSearch = !!settings.link_search;
    syncLinkSearchButton();
    appendCustomerName = !!settings.append_customer_name;
    syncAppendCustomerNameButton();
    dedupe = !!settings.dedupe;
    syncDedupeButton();
    // Reveal an exclude field if it carries a value.
    Object.keys(EXCLUDE_FIELDS).forEach(which => {
        setExcludeVisible(which, !!document.getElementById(EXCLUDE_FIELDS[which].field).value);
    });
    // Reveal the blacklist dropdown if either field carries a value.
    setBlacklistVisible(!!document.getElementById('attachmentBlacklist').value
                        || !!document.getElementById('linksBlacklist').value);
}

// ----- collapsible exclude fields (keywords / sender / recipient) -----
const EXCLUDE_FIELDS = {
    keywords:  {field: 'excludeKeywords',        toggle: 'excludeKeywordsToggle'},
    sender:    {field: 'excludeSenderFilter',     toggle: 'excludeSenderToggle'},
    recipient: {field: 'excludeRecipientFilter',  toggle: 'excludeRecipientToggle'},
};

function setExcludeVisible(which, visible){
    const ids = EXCLUDE_FIELDS[which];
    document.getElementById(ids.field).hidden = !visible;
    document.getElementById(ids.toggle).textContent = (visible ? '− ' : '+ ') + `Exclude ${which}`;
}

function toggleExclude(which){
    setExcludeVisible(which, document.getElementById(EXCLUDE_FIELDS[which].field).hidden);
}

// ----- attachment/links blacklist (collapsed dropdown under the resources toggle) -----
function setBlacklistVisible(visible){
    document.getElementById('blacklistFields').hidden = !visible;
    document.getElementById('blacklistToggle').textContent = (visible ? '− ' : '+ ') + 'Blacklist';
}

function toggleBlacklist(){
    setBlacklistVisible(document.getElementById('blacklistFields').hidden);
}

// A user-initiated search: reset and close the workspace, persist, then load.
function applyFilters(){
    clearTray();
    closeTray();
    saveSettings();
    highlightActiveTemplate();  // the search may now match (or no longer match) a template
    loadMail();
}

async function refreshMail(){
    await fetch('/refresh', {method: 'POST'});
    loadMail();
}

async function loadMail(){
    // Build the query from the same object that gets persisted, so the field
    // list lives in exactly one place (SETTINGS_FIELDS).
    const params = new URLSearchParams();
    for(const [key, value] of Object.entries(currentSettings())){
        params.set(key, SETTINGS_BOOLS.includes(key) ? (value ? '1' : '') : value);
    }
    let data;
    try {
        const response = await fetch(`/api/mail?${params}`);
        data = await response.json();
        if(!response.ok){ throw new Error(data.description || ('HTTP ' + response.status)); }
    } catch(e){
        // The poll failed (server down, or an error page where JSON was expected).
        // Say so in the status box and leave the list standing: without this the
        // function aborted here and the header kept its startup placeholders, which
        // read as "the cache is empty" rather than "the server did not answer".
        document.getElementById('fetchStatus').innerText =
            'Status unavailable — the server did not answer (' + e.message + ')';
        return;
    }

    document.getElementById('lastRefresh').innerText = data.last_refresh;
    let status = data.fetch_status;
    if(data.fetch_progress){
        status += " | " + data.fetch_progress;
    }
    if(data.fetch_error){
        status += " | " + data.fetch_error;
    }
    if(data.query_error){
        status += " | ⚠ " + data.query_error;
    }
    document.getElementById('fetchStatus').innerText = status;

    const container = document.getElementById('mailContainer');
    container.innerHTML = '';
    mailById = {};
    data.mails.forEach(mail => {
        mailById[mail.id] = mail;
        const card = createCard(mail);
        card.dataset.mailId = mail.id;   // lets markWorkspaceCards() find it
        // Draggable into the workspace tray (a distinct type from person drags).
        card.draggable = true;
        card.addEventListener('dragstart', e => {
            e.dataTransfer.setData('text/x-mailfilter-mailid', mail.id);
            e.dataTransfer.effectAllowed = 'copy';
        });
        // Any card opens a popup: the whole thread for a thread, else just itself.
        card.classList.add('clickable');
        card.title = mail.is_thread ? 'Click to view the full thread' : 'Click to view this message';
        card.addEventListener('click', e => {
            if(e.target.closest('a')) return;  // let attachment/link clicks work
            openThread(mail.id);
        });
        container.appendChild(card);
    });
    markWorkspaceCards();   // grey any list item already in the workspace
}
