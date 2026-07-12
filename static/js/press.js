// Press view: the reply-draft worklist.
//
// Load mail items out of the cache with the emoji filters, give them a template
// (one for all, or drag a different template square onto an individual row), fill
// in the data each template needs (edit a cell, or download the Excel form and
// upload it back), and watch every item compute grey / red / green. Then create the
// drafts in two deliberate presses.
//
// Nothing is drafted until the mailbox you draft from has been PROVED against
// Outlook, and the server recomputes every plan at commit time — the client says
// what it wants drafted, the server decides what is.
//
// Every value the server returns (subjects, senders, rendered bodies, reasons) is
// inserted as DOM TEXT, never HTML — the same rule the people fields follow.

const PRESS_TEMPLATE_MIME = 'text/x-mailfilter-compose-template';

// ----- entry -----

async function loadPress(){
    await loadPressState();
    renderPressFilters();
    if(!pressItems.length){ resetPressMails(); }
}

async function loadPressState(){
    let data;
    try {
        data = await (await fetch('/api/press/state')).json();
    } catch(e){ return; }
    pressState = data;
    pressTemplates = data.templates || [];
    // The picker's emoji filters come from the server's single registry (mail_picker),
    // the same list Composer's left column renders.
    if((data.filters || []).length){ pressFilters = data.filters; }
    renderPressMailbox();
    renderPressTemplateBar();
}

// ----- the mailbox: nothing drafts until Outlook has proved it -----

function renderPressMailbox(){
    if(!pressState) return;
    const state = pressState.mailbox;
    const kind = state.selected;
    const box = state[kind];
    document.getElementById('pressMailboxSelect').value = kind;
    document.getElementById('pressCc').checked = !!state.cc_enabled;

    const status = document.getElementById('pressMailboxStatus');
    status.className = 'press-mbx-status press-mbx-' + box.status;
    if(box.status === 'verified'){
        status.textContent = '✓ ' + box.address;
    } else if(box.status === 'pending'){
        status.textContent = '⏳ ' + box.address + ' — not checked yet (Outlook unavailable)';
    } else {
        status.textContent = '⚠ no mailbox set';
    }

    const err = document.getElementById('pressMailboxError');
    if(box.error && box.status !== 'verified'){
        err.textContent = box.error;
        err.hidden = false;
    } else {
        err.hidden = true;
    }
    updatePressCommit();
}

function openPressMailboxPrompt(){
    pressPromptKind = document.getElementById('pressMailboxSelect').value;
    const personal = pressPromptKind === 'personal';
    document.getElementById('pressPromptLabel').textContent =
        personal ? 'Your own mailbox address' : 'The shared mailbox address';
    document.getElementById('pressPromptHint').textContent = personal
        ? 'Checked against the address the running Outlook profile is signed in as.'
        : 'Checked by opening the mailbox — you must actually have access to it.';
    const input = document.getElementById('pressMailboxInput');
    input.value = (pressState && pressState.mailbox[pressPromptKind].address) || '';
    document.getElementById('pressMailboxPrompt').hidden = false;
    input.focus();
}

function closePressMailboxPrompt(){
    document.getElementById('pressMailboxPrompt').hidden = true;
    pressPromptKind = null;
}

async function savePressMailbox(){
    if(!pressPromptKind) return;
    const address = document.getElementById('pressMailboxInput').value.trim();
    setPressStatus('Checking the mailbox against Outlook…', '');
    let data;
    try {
        const resp = await fetch('/api/press/mailbox', {
            method: 'POST', headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({kind: pressPromptKind, address}),
        });
        data = await resp.json();
        if(!resp.ok){ throw new Error(data.description || ('HTTP ' + resp.status)); }
    } catch(e){
        setPressStatus('Could not check the mailbox: ' + e.message, 'err');
        return;
    }
    await loadPressState();
    const box = data.mailbox;
    if(box.status === 'verified'){
        setPressStatus('Mailbox verified: ' + box.address, 'ok');
        closePressMailboxPrompt();
    } else if(box.status === 'pending'){
        setPressStatus('Saved. Outlook is not running, so the check is deferred — '
            + 'draft creation stays locked until it passes.', '');
        closePressMailboxPrompt();
    } else {
        // Rejected: the address was dropped, so ask again rather than leave it wrong.
        setPressStatus(box.error || 'That mailbox was rejected.', 'err');
    }
}

async function onPressMailboxSelect(){
    await pressSaveSettings({selected: document.getElementById('pressMailboxSelect').value});
    const box = pressState.mailbox[pressState.mailbox.selected];
    // Selecting a mailbox that was never set prompts for it, per the same rule as
    // the personal one — you cannot draft from a mailbox you haven't proved.
    if(box.status === 'unset'){ openPressMailboxPrompt(); }
}

async function onPressCcToggle(){
    await pressSaveSettings({cc_enabled: document.getElementById('pressCc').checked});
    schedulePressCompute();  // the CC lands in every plan, so recompute the previews
}

async function pressSaveSettings(patch){
    try {
        const resp = await fetch('/api/press/settings', {
            method: 'PUT', headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(patch),
        });
        const data = await resp.json();
        if(pressState){ pressState.mailbox = data.state; pressState.ready = data.ready; }
    } catch(e){ /* leave the UI as-is; the next state load will correct it */ }
    renderPressMailbox();
}

// ----- the template bar (Composer's squares, reused) -----

function togglePressTemplateBar(){
    pressTemplateBarOpen = !pressTemplateBarOpen;
    document.getElementById('pressTemplateBar').hidden = !pressTemplateBarOpen;
}

function pressTemplate(id){
    return pressTemplates.find(t => t.id === id) || null;
}

function renderPressTemplateBar(){
    const bar = document.getElementById('pressTemplateBar');
    bar.textContent = '';
    if(!pressTemplates.length){
        const hint = document.createElement('span');
        hint.className = 'press-none';
        hint.textContent = 'No templates yet — author one in Composer.';
        bar.appendChild(hint);
    }
    pressTemplates.forEach(t => {
        const sq = document.createElement('button');
        sq.type = 'button';
        sq.className = 'comp-square';
        sq.style.setProperty('--tmpl-color', t.color || '#0ea5e9');
        sq.title = t.name + (t.error ? '  (invalid)' : '') + '  — drag onto a row to give it this template';
        if(t.id === pressTemplateId){ sq.classList.add('comp-selected'); }
        if(t.error){ sq.classList.add('comp-square-invalid'); }
        sq.draggable = true;

        const initials = document.createElement('span');
        initials.className = 'comp-square-initials';
        initials.textContent = pressInitials(t.name);
        sq.appendChild(initials);
        const label = document.createElement('span');
        label.className = 'comp-square-label';
        label.textContent = t.name;
        sq.appendChild(label);

        sq.addEventListener('click', () => selectPressTemplate(t.id));
        sq.addEventListener('contextmenu', e => { e.preventDefault(); selectPressTemplate(null); });
        // Dragging a square onto a row gives that ONE item this template — the
        // distinct MIME type is what lets the row tell this drop from any other.
        sq.addEventListener('dragstart', e => {
            e.dataTransfer.setData(PRESS_TEMPLATE_MIME, t.id);
            e.dataTransfer.setData('text/plain', t.name);
            e.dataTransfer.effectAllowed = 'copy';
        });
        bar.appendChild(sq);
    });
    renderPressTemplateToggle();
}

function renderPressTemplateToggle(){
    const t = pressTemplate(pressTemplateId);
    const toggle = document.getElementById('pressTemplateToggle');
    toggle.style.setProperty('--tmpl-color', t ? (t.color || '#0ea5e9') : '#9ca3af');
    toggle.classList.toggle('comp-selected', !!t);
    document.getElementById('pressTemplateInitials').textContent = t ? pressInitials(t.name) : '＋';
    document.getElementById('pressTemplateName').textContent = t ? t.name : 'Template';
    document.getElementById('pressApplyAll').hidden = !(t && pressItems.length);
}

function pressInitials(name){
    const words = String(name || '').trim().split(/\s+/).filter(Boolean);
    if(!words.length){ return '—'; }
    return words.slice(0, 2).map(w => w[0].toUpperCase()).join('');
}

function selectPressTemplate(id){
    pressTemplateId = id;
    renderPressTemplateBar();
}

// Give every loaded item the selected template, then recompute the lot.
function pressApplyToAll(){
    if(!pressTemplateId) return;
    pressItems.forEach(item => {
        item.template_id = pressTemplateId;
        item.checked = false;      // its computation changed; it must be re-marked
        item.result = null;
    });
    pressArmed = false;
    runPressCompute();
}

// ----- loading mail items through the emoji filters -----

function renderPressFilters(){
    const bar = document.getElementById('pressFilters');
    bar.textContent = '';
    pressFilters.forEach(f => {
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'comp-filter';
        btn.title = 'Load: ' + f.label;
        btn.textContent = f.emoji;
        if(f.id === pressFilterId){ btn.classList.add('active'); }
        btn.addEventListener('click', () => {
            pressFilterId = f.id;
            renderPressFilters();
            resetPressMails();
        });
        bar.appendChild(btn);
    });
}

function resetPressMails(){
    pressItems = [];
    pressOffset = 0;
    pressHasMore = true;
    pressArmed = false;
    loadMorePressMails();
}

async function loadMorePressMails(){
    if(pressLoading || !pressHasMore) return;
    pressLoading = true;
    const params = new URLSearchParams({filter: pressFilterId, offset: String(pressOffset)});
    let data;
    try {
        data = await (await fetch('/api/composer/mails?' + params)).json();
    } catch(e){
        pressLoading = false;
        setPressStatus('Could not load mail.', 'err');
        return;
    }
    (data.mails || []).forEach(mail => {
        pressItems.push({
            mail,
            template_id: pressTemplateId || null,
            row: {},
            status: 'empty',
            reasons: [],
            plan: null,
            checked: false,
            result: null,
        });
    });
    pressOffset += (data.mails || []).length;
    pressHasMore = !!data.has_more;
    pressLoading = false;
    document.getElementById('pressMore').hidden = !pressHasMore;
    document.getElementById('pressSummary').textContent =
        pressItems.length + ' of ' + data.total + ' mail item(s) loaded';
    runPressCompute();
}

// ----- compute: the grey / red / green the whole view turns on -----

function schedulePressCompute(){
    clearTimeout(pressComputeTimer);
    pressComputeTimer = setTimeout(runPressCompute, 300);
}

async function runPressCompute(){
    if(!pressItems.length){ renderPressTable(); return; }
    const payload = {
        items: pressItems.map(i => ({
            mail_id: i.mail.id,
            template_id: i.template_id,
            row: i.row,
        })),
    };
    let data;
    try {
        const resp = await fetch('/api/press/compute', {
            method: 'POST', headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(payload),
        });
        data = await resp.json();
        if(!resp.ok){ throw new Error(data.description || ('HTTP ' + resp.status)); }
    } catch(e){
        setPressStatus('Compute failed: ' + e.message, 'err');
        return;
    }
    const byId = {};
    (data.results || []).forEach(r => { byId[r.mail_id] = r; });
    pressItems.forEach(item => {
        const r = byId[item.mail.id];
        if(!r) return;
        item.status = r.status;
        item.reasons = r.reasons || [];
        item.plan = r.plan;
        item.variables = r.variables || [];
        // A row that no longer computes cannot stay marked for drafting.
        if(item.status !== 'ok'){ item.checked = false; }
    });
    pressColumns = data.columns || [];
    renderPressTable();
}

// ----- the table -----

function renderPressTable(){
    renderPressHead();
    const tbody = document.getElementById('pressRows');
    tbody.textContent = '';
    if(!pressItems.length){
        const tr = document.createElement('tr');
        const td = document.createElement('td');
        td.colSpan = 5 + pressColumns.length;
        td.className = 'bulk-empty';
        td.textContent = 'Load mail items with the emoji filters above, then give them a template.';
        tr.appendChild(td);
        tbody.appendChild(tr);
    } else {
        pressItems.forEach((item, i) => tbody.appendChild(pressRow(item, i)));
    }
    renderPressTemplateToggle();
    updatePressCommit();
}

function renderPressHead(){
    const head = document.getElementById('pressHead');
    head.textContent = '';
    const tr = document.createElement('tr');
    ['', 'Status', 'Mail item', 'Template'].forEach(label => {
        const th = document.createElement('th');
        th.textContent = label;
        tr.appendChild(th);
    });
    // One editable column per row.* variable ANY applied template reads (the union):
    // a cell its own row's template does not read is greyed and ignored.
    pressColumns.forEach(name => {
        const th = document.createElement('th');
        th.className = 'press-var-col';
        th.textContent = 'row.' + name;
        tr.appendChild(th);
    });
    const err = document.createElement('th');
    err.textContent = 'Result';
    tr.appendChild(err);
    head.appendChild(tr);
}

function pressRow(item, index){
    const tr = document.createElement('tr');
    tr.className = 'press-row-' + item.status;
    if(item.checked){ tr.classList.add('press-marked'); }
    if(item.result){ tr.classList.add('press-' + item.result.status); }
    tr.dataset.mailId = item.mail.id;

    // A template square dropped here gives THIS item its own template.
    tr.addEventListener('dragover', e => {
        if(e.dataTransfer.types.includes(PRESS_TEMPLATE_MIME)){
            e.preventDefault();
            e.dataTransfer.dropEffect = 'copy';
            tr.classList.add('drop-hover');
        }
    });
    tr.addEventListener('dragleave', () => tr.classList.remove('drop-hover'));
    tr.addEventListener('drop', e => {
        const tid = e.dataTransfer.getData(PRESS_TEMPLATE_MIME);
        if(!tid) return;
        e.preventDefault();
        tr.classList.remove('drop-hover');
        item.template_id = tid;
        item.checked = false;
        item.result = null;
        runPressCompute();
    });

    // checkbox — only an item that computes can be marked for drafting
    const cbCell = document.createElement('td');
    const cb = document.createElement('input');
    cb.type = 'checkbox';
    cb.className = 'press-include';
    cb.checked = !!item.checked;
    cb.disabled = item.status !== 'ok';
    cb.addEventListener('change', () => {
        item.checked = cb.checked;
        renderPressTable();
    });
    cbCell.appendChild(cb);
    tr.appendChild(cbCell);

    // status dot — hovering shows the rendered draft, or why it failed
    const stCell = document.createElement('td');
    const dot = document.createElement('span');
    dot.className = 'press-dot press-dot-' + item.status;
    dot.textContent = item.status === 'ok' ? '●' : (item.status === 'failed' ? '●' : '○');
    dot.title = pressHoverText(item);
    stCell.appendChild(dot);
    tr.appendChild(stCell);

    // the mail
    const mailCell = document.createElement('td');
    const subj = document.createElement('div');
    subj.className = 'press-subject';
    subj.textContent = item.mail.subject || '(no subject)';
    mailCell.appendChild(subj);
    const from = document.createElement('div');
    from.className = 'press-from';
    from.textContent = (item.mail.sender.name || item.mail.sender.email || '(unknown)')
        + '  •  ' + item.mail.received;
    mailCell.appendChild(from);
    tr.appendChild(mailCell);

    // template
    const tCell = document.createElement('td');
    const t = pressTemplate(item.template_id);
    if(t){
        const chip = document.createElement('span');
        chip.className = 'press-tmpl-chip';
        chip.style.setProperty('--tmpl-color', t.color || '#0ea5e9');
        chip.textContent = t.name;
        tCell.appendChild(chip);
    } else {
        const none = document.createElement('span');
        none.className = 'press-none';
        none.textContent = '— drag one here —';
        tCell.appendChild(none);
    }
    tr.appendChild(tCell);

    // The editable row.* cells. Which ones this item's template actually reads comes
    // from the server (the DSL's own parser decided it), so a `row.x` inside a string
    // literal is not mistaken for a variable.
    const needed = item.variables || [];
    pressColumns.forEach(name => {
        const td = document.createElement('td');
        const used = needed.includes(name);
        const input = document.createElement('input');
        input.type = 'text';
        input.className = 'press-cell' + (used ? '' : ' press-cell-unused');
        input.value = item.row[name] || '';
        input.disabled = !used;
        input.title = used ? ('row.' + name)
                           : "this row's template does not use row." + name;
        input.addEventListener('input', () => {
            item.row[name] = input.value;
            // The computation changed, so this item loses its mark — but only this
            // one; the rest of the armed worklist stands.
            item.checked = false;
            item.result = null;
            schedulePressCompute();
        });
        td.appendChild(input);
        tr.appendChild(td);
    });

    // result / reason
    const resCell = document.createElement('td');
    resCell.className = 'press-result';
    if(item.result){
        const d = document.createElement('div');
        d.className = item.result.status === 'created' ? 'press-ok-text' : 'bulk-warn';
        d.textContent = item.result.status === 'created'
            ? '✓ draft created' : ('✕ ' + (item.result.detail || 'failed'));
        resCell.appendChild(d);
    } else if(item.reasons.length){
        item.reasons.forEach(r => {
            const d = document.createElement('div');
            d.className = 'bulk-warn';
            d.textContent = '⚠ ' + r;
            resCell.appendChild(d);
        });
    }
    tr.appendChild(resCell);

    return tr;
}

function pressHoverText(item){
    if(item.status === 'empty'){ return 'No template assigned yet.'; }
    if(item.status === 'failed'){
        return 'Cannot draft this:\n' + (item.reasons || []).map(r => '• ' + r).join('\n');
    }
    const p = item.plan || {};
    const lines = ['Subject: ' + (p.subject || ''),
                   'To: ' + (p.to || []).join(', '),
                   'Cc: ' + (p.cc || []).join(', ')];
    if(p.uses_ftp){ lines.push('FTP: ' + (p.ftp_link || '')); }
    else if(p.attachment){ lines.push('Attachment: ' + p.attachment.name); }
    lines.push('', p.body || '');
    return lines.join('\n');
}

// ----- the Excel form -----

async function pressDownloadForm(){
    setPressStatus('Writing the form…', '');
    const rows = {};
    pressItems.forEach(i => { rows[i.mail.id] = i.row; });
    let data;
    try {
        const resp = await fetch('/api/press/form', {
            method: 'POST', headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                template_id: pressTemplateId,
                mail_ids: pressItems.map(i => i.mail.id),
                rows,
            }),
        });
        data = await resp.json();
        if(!resp.ok){ throw new Error(data.description || ('HTTP ' + resp.status)); }
    } catch(e){
        setPressStatus('Could not write the form: ' + e.message, 'err');
        return;
    }
    setPressStatus('Form saved to ' + data.folder + '/' + data.name
        + '  •  columns: ' + data.columns.join(', ')
        + '.  Fill it in and upload it back.', 'ok');
}

async function pressUploadForm(){
    const input = document.getElementById('pressFile');
    if(!input.files.length) return;
    const fd = new FormData();
    fd.append('file', input.files[0]);
    fd.append('mail_ids', JSON.stringify(pressItems.map(i => i.mail.id)));
    setPressStatus('Reading the form…', '');
    let data;
    try {
        const resp = await fetch('/api/press/upload', {method: 'POST', body: fd});
        data = await resp.json();
        if(!resp.ok){ throw new Error(data.description || ('HTTP ' + resp.status)); }
    } catch(e){
        setPressStatus('Upload failed: ' + e.message, 'err');
        return;
    }
    input.value = '';

    let bound = 0;
    pressItems.forEach(item => {
        const row = data.bound[item.mail.id];
        if(!row) return;
        bound++;
        Object.keys(row).forEach(key => {
            if(String(row[key]).trim()){ item.row[key] = row[key]; }
        });
        item.checked = false;   // its data changed
        item.result = null;
    });
    pressArmed = false;

    let msg = 'Loaded data for ' + bound + ' mail item(s).';
    if((data.unbound || []).length){
        msg += '  ' + data.unbound.length + ' row(s) could not be matched: '
            + data.unbound.map(u => 'row ' + (u.row_index + 2) + ' — ' + u.reason).join('; ');
    }
    setPressStatus(msg, (data.unbound || []).length ? 'err' : 'ok');
    runPressCompute();
}

// ----- the two-press commit -----

function pressComputedCount(){
    return pressItems.filter(i => i.status === 'ok').length;
}

function pressCheckedCount(){
    return pressItems.filter(i => i.checked && i.status === 'ok').length;
}

function updatePressCommit(){
    const btn = document.getElementById('pressCommit');
    if(!btn) return;
    const ready = !!(pressState && pressState.ready);
    const computed = pressComputedCount();
    btn.disabled = !(ready && computed);
    btn.classList.toggle('press-armed', pressArmed);
    if(!ready){
        btn.textContent = 'Verify a mailbox first';
    } else if(!computed){
        btn.textContent = 'Nothing computes yet';
    } else if(!pressArmed){
        btn.textContent = 'Mark ' + computed + ' item(s) to draft';
    } else {
        btn.textContent = 'Create ' + pressCheckedCount() + ' draft(s)';
    }
}

// Press once: re-sync the mailbox and mark every computed item. Press again: create.
async function pressCommit(){
    if(!pressArmed){
        await loadPressState();          // re-sync: the mailbox must still be reachable
        if(!pressState.ready){
            setPressStatus('The selected mailbox is no longer verified.', 'err');
            return;
        }
        pressItems.forEach(i => { i.checked = i.status === 'ok'; });
        pressArmed = true;
        renderPressTable();
        setPressStatus('Marked ' + pressCheckedCount() + ' item(s). Press again to create the '
            + 'drafts, untick any you do not want, or right-click the button to clear.', 'ok');
        return;
    }

    const selected = pressItems.filter(i => i.checked && i.status === 'ok');
    if(!selected.length){
        setPressStatus('No items are marked.', 'err');
        return;
    }
    if(!confirm('Create ' + selected.length + ' reply draft(s)? '
                + 'Drafts are NOT sent — you review and send them from Outlook.')){
        return;
    }
    setPressStatus('Creating drafts…', '');
    let data;
    try {
        const resp = await fetch('/api/press/create-drafts', {
            method: 'POST', headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                items: pressItems.map(i => ({
                    mail_id: i.mail.id, template_id: i.template_id, row: i.row,
                })),
                selected: selected.map(i => i.mail.id),
            }),
        });
        data = await resp.json();
        if(!resp.ok){ throw new Error(data.description || ('HTTP ' + resp.status)); }
    } catch(e){
        setPressStatus('Create failed: ' + e.message, 'err');
        return;
    }

    const byId = {};
    (data.results || []).forEach(r => { byId[r.mail_id] = r; });
    pressItems.forEach(item => {
        const r = byId[item.mail.id];
        if(r){ item.result = r; item.checked = false; }
    });
    pressArmed = false;
    renderPressTable();
    let msg = 'Created ' + data.created + ' of ' + data.requested + ' draft(s).';
    if(data.audit){ msg += '  Audit log: ' + data.audit; }
    setPressStatus(msg, data.created === data.requested ? 'ok' : 'err');
}

// Right-clicking the button clears every mark at once.
function pressDisarm(event){
    event.preventDefault();
    pressArmed = false;
    pressItems.forEach(i => { i.checked = false; });
    renderPressTable();
    setPressStatus('Cleared every mark.', '');
}

function setPressStatus(text, kind){
    const el = document.getElementById('pressStatus');
    el.textContent = text;
    el.className = 'tray-status' + (kind ? ' bulk-status-' + kind : '');
}
