let resourcesOnly = false;

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
    }catch(e){
        // No saved settings (or fetch failed) — start from blank defaults.
    }
}

// A user-initiated search: persist the settings, then load.
function applyFilters(){
    saveSettings();
    loadMail();
}

async function refreshMail(){
    await fetch('/refresh', {method: 'POST'});
    loadMail();
}

async function loadMail(){
    const params = new URLSearchParams({
        start: document.getElementById('startDate').value,
        end: document.getElementById('endDate').value,
        main: document.getElementById('mainKeywords').value,
        optional: document.getElementById('optionalKeywords').value,
        exclude: document.getElementById('excludeKeywords').value,
        sender: document.getElementById('senderFilter').value,
        recipient: document.getElementById('recipientFilter').value,
        resources: resourcesOnly ? '1' : '',
    });
    const response = await fetch(`/api/mail?${params}`);
    const data = await response.json();

    document.getElementById('lastRefresh').innerText = data.last_refresh;
    let status = data.fetch_status;
    if(data.fetch_error){
        status += " | " + data.fetch_error;
    }
    if(data.query_error){
        status += " | ⚠ " + data.query_error;
    }
    document.getElementById('fetchStatus').innerText = status;

    const container = document.getElementById('mailContainer');
    container.innerHTML = '';
    data.mails.forEach(mail => {
        const card = document.createElement('div');
        card.className = `card ${mail.is_thread ? 'thread' : 'single'}`;
        // subject/sender/preview are already HTML-escaped server-side.
        card.innerHTML = `
            <h3>${mail.icon} ${mail.subject}</h3>
            <div class="meta">${mail.sender}<br>${mail.received}</div>
            <div>${mail.preview}</div>
        `;
        const resources = renderResources(mail);
        if(resources){
            card.appendChild(resources);
        }
        container.appendChild(card);
    });
}

// Build the attachments/links block with the DOM API so filenames and URLs
// (which originate from email content) are inserted as text, never as HTML.
function renderResources(mail){
    const attachments = mail.attachments || [];
    const links = mail.links || [];
    if(!attachments.length && !links.length){
        return null;
    }
    const wrap = document.createElement('div');
    wrap.className = 'resources';

    if(attachments.length){
        const group = document.createElement('div');
        group.className = 'resource-group';
        group.appendChild(makeLabel(`📎 Attachments (${attachments.length})`));
        attachments.forEach(att => {
            const a = document.createElement('a');
            a.href = att.url;
            a.className = 'resource-item';
            // A file-type icon for clarity, then the filename as text (it comes
            // from mail content, so it must never be inserted as HTML).
            const icon = document.createElement('span');
            icon.className = 'file-icon';
            icon.textContent = fileIcon(att.filename);
            a.appendChild(icon);
            a.appendChild(document.createTextNode(att.filename));
            group.appendChild(a);
        });
        wrap.appendChild(group);
    }

    if(links.length){
        const group = document.createElement('div');
        group.className = 'resource-group';
        group.appendChild(makeLabel(`🔗 Links (${links.length})`));
        links.forEach(url => {
            const a = document.createElement('a');
            a.href = url;
            a.textContent = url;
            a.target = '_blank';
            a.rel = 'noopener noreferrer';
            a.className = 'resource-item';
            group.appendChild(a);
        });
        wrap.appendChild(group);
    }

    return wrap;
}

function makeLabel(text){
    const label = document.createElement('div');
    label.className = 'resource-label';
    label.textContent = text;
    return label;
}

// One glyph per broad file-type family, picked from the filename extension —
// enough to tell at a glance what kind of file an attachment is.
const FILE_ICONS = {
    pdf:'📕',
    doc:'📘', docx:'📘', odt:'📘', rtf:'📘',
    txt:'📄', md:'📄', log:'📄',
    xls:'📗', xlsx:'📗', csv:'📗', ods:'📗',
    ppt:'📙', pptx:'📙', odp:'📙',
    png:'🖼️', jpg:'🖼️', jpeg:'🖼️', gif:'🖼️', bmp:'🖼️', svg:'🖼️', webp:'🖼️', heic:'🖼️',
    zip:'🗜️', rar:'🗜️', '7z':'🗜️', gz:'🗜️', tar:'🗜️',
    mp3:'🎵', wav:'🎵', flac:'🎵', m4a:'🎵', ogg:'🎵',
    mp4:'🎬', mov:'🎬', avi:'🎬', mkv:'🎬', webm:'🎬',
    exe:'⚙️', msi:'⚙️', dmg:'⚙️',
    json:'🔧', xml:'🔧', yml:'🔧', yaml:'🔧',
    html:'🌐', htm:'🌐',
};

function fileIcon(filename){
    const dot = filename.lastIndexOf('.');
    const ext = dot > -1 ? filename.slice(dot + 1).toLowerCase() : '';
    return FILE_ICONS[ext] || '📎';
}

async function init(){
    await restoreSettings();   // repopulate the sidebar from the saved search
    loadMail();
    setInterval(loadMail, 30000);
}

init();
