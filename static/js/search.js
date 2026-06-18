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
    loadMail();
}

function currentSettings(){
    const settings = {resources: resourcesOnly};
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
        const settings = await response.json();
        for(const [key, id] of Object.entries(SETTINGS_FIELDS)){
            document.getElementById(id).value = settings[key] || '';
        }
        resourcesOnly = !!settings.resources;
        syncResourcesButton();
        // Reveal an exclude field if it was previously used.
        Object.keys(EXCLUDE_FIELDS).forEach(which => {
            if(document.getElementById(EXCLUDE_FIELDS[which].field).value){
                setExcludeVisible(which, true);
            }
        });
        // Reveal the blacklist dropdown if either field was previously used.
        if(document.getElementById('attachmentBlacklist').value
           || document.getElementById('linksBlacklist').value){
            setBlacklistVisible(true);
        }
    }catch(e){
        // No saved settings (or fetch failed) — start from blank defaults.
    }
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
        params.set(key, key === 'resources' ? (value ? '1' : '') : value);
    }
    const response = await fetch(`/api/mail?${params}`);
    const data = await response.json();

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
}
