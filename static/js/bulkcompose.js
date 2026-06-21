// Bulk Compose view: turn an Excel sheet of replies into reply-all DRAFTS in the
// shared mailbox (never sends). Two stages: Preview (read-only, server writes
// nothing) then Create drafts (server recomputes and commits the selected rows).
//
// Every value the server returns (emails, names, subjects, rendered bodies) is
// inserted as DOM TEXT, never HTML — same rule the people fields follow.

let bulkTemplates = [];      // [{id, name, body, attachment_expr, error}, ...]
let bulkEditingId = null;    // template id open in the editor, or null for "new"
let bulkHasPreview = false;  // a successful preview exists for the current inputs

async function loadComposeTemplates(){
    let data;
    try {
        data = await (await fetch('/api/compose-templates')).json();
    } catch(e){ return; }
    bulkTemplates = data.templates || [];
    const info = document.getElementById('bulkSharedInfo');
    if(info){
        info.textContent = 'Drafting from ' + (data.shared_mailbox || '(unset)')
            + (data.mock_mode ? '  •  MOCK MODE (no Outlook)' : '');
        info.classList.toggle('bulk-mock', !!data.mock_mode);
    }
    populateBulkTemplateSelect();
    onBulkTemplateChange();
}

function populateBulkTemplateSelect(){
    const sel = document.getElementById('bulkTemplateSelect');
    const prev = sel.value;
    sel.length = 1;  // keep the placeholder option
    bulkTemplates.forEach(t => {
        const opt = document.createElement('option');
        opt.value = t.id;
        opt.textContent = t.name + (t.error ? '  (invalid)' : '');
        sel.appendChild(opt);
    });
    if(bulkTemplates.some(t => t.id === prev)){ sel.value = prev; }
}

function selectedBulkTemplate(){
    const id = document.getElementById('bulkTemplateSelect').value;
    return bulkTemplates.find(t => t.id === id) || null;
}

function onBulkTemplateChange(){
    const t = selectedBulkTemplate();
    document.getElementById('bulkTmplEditBtn').disabled = !t;
    const err = document.getElementById('bulkTemplateError');
    if(t && t.error){
        err.textContent = 'This template is invalid: ' + t.error;
        err.hidden = false;
    } else {
        err.hidden = true;
    }
    invalidateBulkPreview();
    updateBulkButtons();
}

function onBulkFileChange(){
    invalidateBulkPreview();
    updateBulkButtons();
}

// A changed template/file means the last preview no longer reflects the inputs:
// require a fresh Preview before Create drafts can run again.
function invalidateBulkPreview(){
    bulkHasPreview = false;
    document.getElementById('bulkCreateBtn').disabled = true;
}

function updateBulkButtons(){
    const t = selectedBulkTemplate();
    const hasFile = document.getElementById('bulkFile').files.length > 0;
    document.getElementById('bulkPreviewBtn').disabled = !(t && hasFile && !t.error);
}

// ----- template editor modal -----

function openBulkTemplateEditor(isNew){
    const modal = document.getElementById('bulkTemplateModal');
    const t = isNew ? null : selectedBulkTemplate();
    bulkEditingId = t ? t.id : null;
    document.getElementById('bulkTmplTitle').textContent =
        t ? 'Edit Reply Template' : 'New Reply Template';
    document.getElementById('bulkTmplName').value = t ? t.name : '';
    document.getElementById('bulkTmplBody').value = t ? t.body : '';
    document.getElementById('bulkTmplAttach').value = t ? (t.attachment_expr || '') : '';
    document.getElementById('bulkTmplDeleteBtn').hidden = !t;
    document.getElementById('bulkTmplFormError').hidden = true;
    modal.hidden = false;
}

function closeBulkTemplateEditor(){
    document.getElementById('bulkTemplateModal').hidden = true;
}

async function saveBulkTemplate(){
    const payload = {
        name: document.getElementById('bulkTmplName').value,
        body: document.getElementById('bulkTmplBody').value,
        attachment_expr: document.getElementById('bulkTmplAttach').value,
    };
    const url = bulkEditingId ? '/api/compose-templates/' + bulkEditingId
                              : '/api/compose-templates';
    const method = bulkEditingId ? 'PUT' : 'POST';
    let saved;
    try {
        const resp = await fetch(url, {
            method, headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(payload),
        });
        saved = await resp.json();
    } catch(e){ return; }

    await loadComposeTemplates();
    document.getElementById('bulkTemplateSelect').value = saved.id;
    onBulkTemplateChange();

    // It saves even when invalid (so a half-finished template can be fixed
    // later), but keep the editor open and show the problem if there is one.
    if(saved.error){
        const fe = document.getElementById('bulkTmplFormError');
        fe.textContent = saved.error;
        fe.hidden = false;
        bulkEditingId = saved.id;
        document.getElementById('bulkTmplDeleteBtn').hidden = false;
    } else {
        closeBulkTemplateEditor();
    }
}

async function deleteBulkTemplate(){
    if(!bulkEditingId) return;
    try {
        await fetch('/api/compose-templates/' + bulkEditingId, {method: 'DELETE'});
    } catch(e){ return; }
    closeBulkTemplateEditor();
    await loadComposeTemplates();
}

// ----- preview & commit -----

function bulkFormData(){
    const fd = new FormData();
    fd.append('file', document.getElementById('bulkFile').files[0]);
    fd.append('template_id', document.getElementById('bulkTemplateSelect').value);
    return fd;
}

function setBulkStatus(text, kind){
    const el = document.getElementById('bulkStatus');
    el.textContent = text;
    el.className = 'tray-status' + (kind ? ' bulk-status-' + kind : '');
}

async function bulkPreview(){
    setBulkStatus('Previewing…', '');
    let data;
    try {
        const resp = await fetch('/api/bulk/preview', {method: 'POST', body: bulkFormData()});
        data = await resp.json();
        if(!resp.ok){ throw new Error(data.description || ('HTTP ' + resp.status)); }
    } catch(e){
        setBulkStatus('Preview failed: ' + e.message, 'err');
        return;
    }
    renderBulkPlans(data.plans || []);
    const s = data.summary || {total: 0, ready: 0, blocked: 0};
    const dropped = data.dropped ? ('  •  ' + data.dropped + ' row(s) over the limit dropped') : '';
    document.getElementById('bulkSummary').textContent =
        s.total + ' row(s): ' + s.ready + ' ready, ' + s.blocked + ' blocked' + dropped;
    bulkHasPreview = true;
    document.getElementById('bulkCreateBtn').disabled = s.ready === 0;
    setBulkStatus(s.ready + ' draft(s) ready to create. Review, then Create drafts.', 'ok');
}

function renderBulkPlans(plans){
    const tbody = document.getElementById('bulkRows');
    tbody.textContent = '';
    if(!plans.length){
        const tr = document.createElement('tr');
        const td = document.createElement('td');
        td.colSpan = 7; td.className = 'bulk-empty';
        td.textContent = 'No rows in the sheet.';
        tr.appendChild(td); tbody.appendChild(tr);
        return;
    }
    plans.forEach(p => tbody.appendChild(bulkPlanRow(p)));
    document.getElementById('bulkSelectAll').checked = true;
}

function bulkPlanRow(p){
    const tr = document.createElement('tr');
    const ready = p.status === 'ready';
    tr.className = ready ? 'bulk-ready' : 'bulk-blocked';

    // include checkbox
    const cbCell = document.createElement('td');
    const cb = document.createElement('input');
    cb.type = 'checkbox';
    cb.className = 'bulk-include';
    cb.dataset.index = p.row_index;
    cb.checked = ready;
    cb.disabled = !ready;
    cbCell.appendChild(cb);
    tr.appendChild(cbCell);

    tr.appendChild(textCell(String(p.row_index + 1)));

    const statusCell = document.createElement('td');
    const badge = document.createElement('span');
    badge.className = 'bulk-badge ' + (ready ? 'bulk-badge-ready' : 'bulk-badge-blocked');
    badge.textContent = ready ? 'ready' : 'blocked';
    statusCell.appendChild(badge);
    tr.appendChild(statusCell);

    // recipients
    const rcpt = document.createElement('td');
    if(p.to && p.to.length){ rcpt.appendChild(lineDiv('To: ' + p.to.join(', '))); }
    if(p.cc && p.cc.length){ rcpt.appendChild(lineDiv('Cc: ' + p.cc.join(', '))); }
    tr.appendChild(rcpt);

    tr.appendChild(textCell(p.subject || ''));

    // attachment / ftp
    const att = document.createElement('td');
    if(p.uses_ftp){
        att.appendChild(lineDiv('FTP: ' + (p.ftp_link || '(no link)')));
    } else if(p.attachment){
        const d = lineDiv((p.attachment.exists ? '📎 ' : '⚠ ') + p.attachment.name);
        if(!p.attachment.exists){ d.className += ' bulk-warn'; }
        att.appendChild(d);
    }
    tr.appendChild(att);

    // body preview + warnings
    const last = document.createElement('td');
    if(p.body){
        const pre = document.createElement('pre');
        pre.className = 'bulk-bodyprev';
        pre.textContent = p.body;
        last.appendChild(pre);
    }
    (p.warnings || []).forEach(w => {
        const d = lineDiv('⚠ ' + w);
        d.className = 'bulk-warn';
        last.appendChild(d);
    });
    tr.appendChild(last);

    return tr;
}

function textCell(text){
    const td = document.createElement('td');
    td.textContent = text;
    return td;
}

function lineDiv(text){
    const d = document.createElement('div');
    d.textContent = text;
    return d;
}

function toggleBulkSelectAll(){
    const on = document.getElementById('bulkSelectAll').checked;
    document.querySelectorAll('.bulk-include').forEach(cb => {
        if(!cb.disabled){ cb.checked = on; }
    });
}

async function bulkCreateDrafts(){
    if(!bulkHasPreview){ return; }
    const indices = Array.from(document.querySelectorAll('.bulk-include'))
        .filter(cb => cb.checked && !cb.disabled)
        .map(cb => parseInt(cb.dataset.index, 10));
    if(!indices.length){
        setBulkStatus('Select at least one ready row to create.', 'err');
        return;
    }
    if(!confirm('Create ' + indices.length + ' draft(s) in the shared mailbox? '
                + 'Drafts are NOT sent — you review and send them from Outlook.')){
        return;
    }
    setBulkStatus('Creating drafts…', '');
    const fd = bulkFormData();
    fd.append('indices', JSON.stringify(indices));
    let data;
    try {
        const resp = await fetch('/api/bulk/create-drafts', {method: 'POST', body: fd});
        data = await resp.json();
        if(!resp.ok){ throw new Error(data.description || ('HTTP ' + resp.status)); }
    } catch(e){
        setBulkStatus('Create failed: ' + e.message, 'err');
        return;
    }
    let msg = 'Created ' + data.created + ' of ' + data.requested + ' draft(s).';
    if(data.audit){ msg += '  Audit log: ' + data.audit; }
    setBulkStatus(msg, 'ok');
}
