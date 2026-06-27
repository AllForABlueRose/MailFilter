// The workspace tray: collect dragged mail, then bulk-download / open links.

// The right panel hosts two modes that share the area: the workspace tray and
// the regex compiler. The two top-bar buttons switch between them.
let panelMode = null;   // 'workspace' | 'regex' | null (closed)

function toggleTray(){ togglePanel('workspace'); }
function toggleRegex(){ togglePanel('regex'); }

function togglePanel(mode){
    const tray = document.getElementById('tray');
    if(!tray.hidden && panelMode === mode){
        tray.hidden = true;
        panelMode = null;
    }else{
        tray.hidden = false;
        panelMode = mode;
    }
    applyPanelMode();
}

function applyPanelMode(){
    const open = !document.getElementById('tray').hidden;
    document.getElementById('workspaceView').hidden = !(open && panelMode === 'workspace');
    document.getElementById('regexView').hidden = !(open && panelMode === 'regex');
    document.getElementById('trayToggle').classList.toggle('active', open && panelMode === 'workspace');
    document.getElementById('regexToggle').classList.toggle('active', open && panelMode === 'regex');
}

function closeTray(){
    document.getElementById('tray').hidden = true;
    panelMode = null;
    applyPanelMode();
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
        sortTrayMails();   // keep the tray in the chosen date order as items arrive
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
    updateTrayActions();
}

// Reflect the current tray contents in the action buttons: download/links are
// disabled when nothing here carries that resource, and the mark button flips
// to "Unmark" (a 🧽 sponge) once every item is already marked.
function updateTrayActions(){
    const hasAttachments = trayMails.some(m => (m.attachments || []).length);
    const hasLinks = trayMails.some(m => (m.links || []).length);
    document.getElementById('trayDownloadBtn').disabled = !hasAttachments;
    document.getElementById('trayLinksBtn').disabled = !hasLinks;
    document.getElementById('trayReportBtn').disabled = !trayMails.length;

    const markBtn = document.getElementById('trayMarkBtn');
    const unmark = allTrayMarked();
    markBtn.disabled = !trayMails.length;
    markBtn.textContent = unmark ? '🧽' : '🎯';
    markBtn.title = unmark ? 'Unmark every item here' : 'Mark every item here';

    const sortBtn = document.getElementById('traySortBtn');
    sortBtn.disabled = !trayMails.length;
    sortBtn.textContent = traySortNewestFirst ? '▼' : '▲';
    sortBtn.title = traySortNewestFirst
        ? 'Sorted newest first — click for oldest first'
        : 'Sorted oldest first — click for newest first';
}

// `received` is "%Y-%m-%d %H:%M:%S", so a lexical compare is also chronological.
function sortTrayMails(){
    trayMails.sort((a, b) => {
        const cmp = (a.received || '').localeCompare(b.received || '');
        return traySortNewestFirst ? -cmp : cmp;
    });
}

function toggleTraySort(){
    traySortNewestFirst = !traySortNewestFirst;
    renderTray();
}

function allTrayMarked(){
    return trayMails.length > 0 && trayMails.every(m => m.tags && m.tags.marked);
}

// Mark all collected mails, or unmark them all when every one is already marked.
function toggleTrayMark(){
    const ids = trayMails.map(m => m.id);
    if(!ids.length) return;
    const op = allTrayMarked() ? 'remove' : 'add';
    trayMails.forEach(m => {
        const tags = Object.assign({}, m.tags);
        if(op === 'add'){ tags.marked = 'recent'; } else { delete tags.marked; }
        m.tags = tags;
    });
    fetch('/api/tags', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ids, action: 'marked', op}),
    }).catch(() => {});
    renderTray();   // show/hide the 🎯 tag and flip the button
}

// Save every attachment of the collected mails into a dated folder on the
// server (no browser "Save As" dialog) — one at a time, server-side.
function downloadTrayAttachments(){
    // Use each attachment's original index (blacklisted ones are already absent
    // from the view model, so they're naturally skipped here).
    const items = [];
    trayMails.forEach(m => (m.attachments || []).forEach(att => items.push({id: m.id, index: att.index})));
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

// Export a CSV report (Datetime, subject, recipient, sender) of the collected
// mails into the dated workspace folder on the server. The tray status line
// doubles as the "done" notification.
function exportTrayReport(){
    const ids = trayMails.map(m => m.id);
    const status = document.getElementById('trayStatus');
    if(!ids.length){ status.textContent = 'No mails to export.'; return; }
    status.textContent = `Exporting report for ${ids.length} mail(s)...`;
    fetch('/api/report', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ids}),
    }).then(r => r.json()).then(result => {
        status.textContent = `Report saved: ${result.folder}/${result.name} (${result.count} row(s))`;
    }).catch(() => { status.textContent = 'Report export failed.'; });
}

// Open every link of every collected mail, each in its own new tab. No feature
// string -> browsers open tabs (not popups), so they aren't blocked after the
// first the way `window.open(url, '_blank', 'noopener')` was.
function openTrayLinks(){
    const opened = [];
    trayMails.forEach(m => {
        let any = false;
        (m.links || []).forEach(link => {
            const w = window.open(link.url, '_blank');
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

// "Collect matching mails" (𖣐): a hover popup showing one label symbol at a
// time. Scroll to cycle through the labels the displayed mails carry; click to
// pull every displayed mail with the shown label into the workspace. Each
// (action, recency) pair is its own label, because a greyed (old >7 days) tag
// reads as a distinct thing from its fresh counterpart.
// Each label is either a (recency-specific) action tag, or — for the 🔑 password
// badge, which is not a tag — a `match(mail)` predicate.
const COLLECT_LABELS = [
    {action: 'marked',     recency: 'recent', symbol: '🎯', name: 'Marked'},
    {action: 'marked',     recency: 'old',    symbol: '🎯', name: 'Marked (7+ days)', grey: true},
    {action: 'downloaded', recency: 'recent', symbol: '📥', name: 'Downloaded'},
    {action: 'downloaded', recency: 'old',    symbol: '📥', name: 'Downloaded (7+ days)', grey: true},
    {action: 'links',      recency: 'recent', symbol: '🌐', name: 'Links opened'},
    {action: 'links',      recency: 'old',    symbol: '🌐', name: 'Links opened (7+ days)', grey: true},
    {name: 'Password detected', symbol: '🔑', match: m => !!m.has_password},
];

let collectAvailable = [];  // labels present in the current display
let collectIndex = 0;       // focused label within collectAvailable

// Whether a mail carries a collect label: a predicate label uses its match();
// an action label matches when the mail's tag has that exact recency.
function labelMatches(mail, label){
    if(label.match){ return label.match(mail); }
    return !!(mail.tags && mail.tags[label.action] === label.recency);
}

// Only the labels at least one displayed mail actually carries.
function labelsInDisplay(){
    const mails = Object.values(mailById);
    return COLLECT_LABELS.filter(label => mails.some(m => labelMatches(m, label)));
}

function openCollectWheel(){
    collectAvailable = labelsInDisplay();
    collectIndex = 0;
    renderCollectWheel();
    document.getElementById('collectWheel').hidden = false;
}

function closeCollectWheel(){
    document.getElementById('collectWheel').hidden = true;
}

// Minimal popup: just the focused label's symbol (greyed for old tags). The
// name lives in the tooltip so the box stays text-free. `direction` ('next' |
// 'prev'), when given, slides the new symbol in horizontally.
function renderCollectWheel(direction){
    const wheel = document.getElementById('collectWheel');
    wheel.innerHTML = '';
    if(!collectAvailable.length){
        wheel.classList.add('empty');
        wheel.title = 'No labels in view';
        wheel.textContent = '∅';
        return;
    }
    wheel.classList.remove('empty');
    const label = collectAvailable[collectIndex];
    const sym = document.createElement('span');
    sym.className = 'collect-sym' + (label.grey ? ' grey' : '');
    if(direction){ sym.classList.add(direction === 'next' ? 'slide-next' : 'slide-prev'); }
    sym.textContent = label.symbol;
    wheel.appendChild(sym);
    wheel.title = `${label.name} — click to collect`;
}

function cycleCollect(e){
    e.preventDefault();
    const n = collectAvailable.length;
    if(!n) return;
    const forward = e.deltaY > 0;
    collectIndex = (collectIndex + (forward ? 1 : -1) + n) % n;
    renderCollectWheel(forward ? 'next' : 'prev');
}

// Click the popup to collect the label it currently shows.
function collectFocused(){
    if(collectAvailable.length){ collectByLabel(collectAvailable[collectIndex]); }
}

function collectByLabel(label){
    let added = false;
    Object.values(mailById).forEach(m => {
        if(labelMatches(m, label) && !trayIds.has(m.id)){
            trayIds.add(m.id);
            trayMails.push(m);
            added = true;
        }
    });
    closeCollectWheel();
    if(added) renderTray();
}
