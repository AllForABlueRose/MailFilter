// The workspace tray: collect dragged mail, then bulk-download / open links.

function toggleTray(){
    const tray = document.getElementById('tray');
    const willOpen = tray.hidden;
    tray.hidden = !willOpen;
    document.getElementById('trayToggle').classList.toggle('active', willOpen);
}

function closeTray(){
    document.getElementById('tray').hidden = true;
    document.getElementById('trayToggle').classList.remove('active');
}

function addToTray(id){
    if(!id || trayIds.has(id)) return;
    const mail = mailById[id];
    if(!mail) return;
    trayIds.add(id);
    trayMails.push(mail);
    renderTray();
}

function removeFromTray(id){
    trayIds.delete(id);
    trayMails = trayMails.filter(m => m.id !== id);
    renderTray();
}

function clearTray(){
    trayMails = [];
    trayIds.clear();
    renderTray();
}

function renderTray(){
    const body = document.getElementById('trayBody');
    body.innerHTML = '';
    if(!trayMails.length){
        const hint = document.createElement('p');
        hint.className = 'tray-empty';
        hint.textContent = 'Drag mail items here to collect them.';
        body.appendChild(hint);
    } else {
        trayMails.forEach(mail => {
            // Same card as the list, so highlights, attachments, and links persist.
            const card = createCard(mail);
            card.classList.add('clickable');
            card.title = mail.is_thread ? 'Click to view the full thread' : 'Click to view this message';
            card.addEventListener('click', e => {
                if(e.target.closest('a') || e.target.closest('.tray-remove')) return;
                openThread(mail.id);
            });
            const remove = document.createElement('button');
            remove.className = 'tray-remove';
            remove.textContent = '✕';
            remove.title = 'Remove from workspace';
            remove.addEventListener('click', e => {
                e.stopPropagation();
                removeFromTray(mail.id);
            });
            card.querySelector('.card-corner').appendChild(remove);
            body.appendChild(card);
        });
    }
    document.getElementById('trayCount').textContent = `(${trayMails.length})`;
}

// Save every attachment of the collected mails into a dated folder on the
// server (no browser "Save As" dialog) — one at a time, server-side.
function downloadTrayAttachments(){
    const items = [];
    trayMails.forEach(m => (m.attachments || []).forEach((att, i) => items.push({id: m.id, index: i})));
    const status = document.getElementById('trayStatus');
    if(!items.length){ status.textContent = 'No attachments to download.'; return; }
    status.textContent = `Downloading ${items.length} attachment(s)...`;
    fetch('/api/download', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({items}),
    }).then(r => r.json()).then(result => {
        // Server persisted the "downloaded" tag; reflect it immediately.
        const ids = new Set((result.saved || []).map(s => s.id));
        trayMails.forEach(m => {
            if(ids.has(m.id)){ m.tags = Object.assign({}, m.tags, {downloaded: 'recent'}); }
        });
        let msg = `Saved ${result.saved.length} file(s) to ${result.folder}`;
        if(result.errors && result.errors.length){ msg += ` — ${result.errors.length} failed`; }
        status.textContent = msg;
        renderTray();   // show the 📥 tag on downloaded mails
    }).catch(() => { status.textContent = 'Download failed.'; });
}

// Open every link of every collected mail, each in its own new tab. No feature
// string -> browsers open tabs (not popups), so they aren't blocked after the
// first the way `window.open(url, '_blank', 'noopener')` was.
function openTrayLinks(){
    const opened = [];
    trayMails.forEach(m => {
        let any = false;
        (m.links || []).forEach(url => {
            const w = window.open(url, '_blank');
            if(w){ w.opener = null; any = true; }
        });
        if(any){
            m.tags = Object.assign({}, m.tags, {links: 'recent'});
            opened.push(m.id);
        }
    });
    if(opened.length){
        // Persist the "links opened" tag (the download tag is recorded server-side).
        fetch('/api/tags', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ids: opened, action: 'links'}),
        }).catch(() => {});
    }
    renderTray();   // show the 🌐 tag on mails whose links were opened
}
