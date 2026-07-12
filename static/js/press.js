// Press view: turn an Excel sheet of replies into reply-all DRAFTS in the shared
// mailbox (never sends). Two stages: Preview (read-only, the server writes
// nothing) then Create drafts (the server recomputes and commits the selected rows).
//
// Templates are PICKED here, not authored — authoring lives in Composer, which
// can show you what a template renders to before you point it at a whole sheet.
//
// Every value the server returns (emails, names, subjects, rendered bodies) is
// inserted as DOM TEXT, never HTML — the same rule the people fields follow.

async function loadPressTemplates(){
    let data;
    try {
        data = await (await fetch('/api/compose-templates')).json();
    } catch(e){ return; }
    pressTemplates = data.templates || [];
    const info = document.getElementById('pressSharedInfo');
    if(info){
        info.textContent = 'Drafting from ' + (data.shared_mailbox || '(unset)')
            + (data.mock_mode ? '  •  MOCK MODE (no Outlook)' : '');
        info.classList.toggle('bulk-mock', !!data.mock_mode);
    }
    populatePressTemplateSelect();
    onPressTemplateChange();
}

function populatePressTemplateSelect(){
    const sel = document.getElementById('pressTemplateSelect');
    const prev = sel.value;
    sel.length = 1;  // keep the placeholder option
    pressTemplates.forEach(t => {
        const opt = document.createElement('option');
        opt.value = t.id;
        opt.textContent = t.name + (t.error ? '  (invalid)' : '');
        sel.appendChild(opt);
    });
    if(pressTemplates.some(t => t.id === prev)){ sel.value = prev; }
}

function selectedPressTemplate(){
    const id = document.getElementById('pressTemplateSelect').value;
    return pressTemplates.find(t => t.id === id) || null;
}

function onPressTemplateChange(){
    const t = selectedPressTemplate();
    const err = document.getElementById('pressTemplateError');
    if(t && t.error){
        err.textContent = 'This template is invalid: ' + t.error
            + '  —  fix it in Composer.';
        err.hidden = false;
    } else {
        err.hidden = true;
    }
    invalidatePressPreview();
    updatePressButtons();
}

function onPressFileChange(){
    invalidatePressPreview();
    updatePressButtons();
}

// A changed template/file means the last preview no longer reflects the inputs:
// require a fresh Preview before Create drafts can run again.
function invalidatePressPreview(){
    pressHasPreview = false;
    document.getElementById('pressCreateBtn').disabled = true;
}

function updatePressButtons(){
    const t = selectedPressTemplate();
    const hasFile = document.getElementById('pressFile').files.length > 0;
    document.getElementById('pressPreviewBtn').disabled = !(t && hasFile && !t.error);
}

// ----- preview & commit -----

function pressFormData(){
    const fd = new FormData();
    fd.append('file', document.getElementById('pressFile').files[0]);
    fd.append('template_id', document.getElementById('pressTemplateSelect').value);
    return fd;
}

function setPressStatus(text, kind){
    const el = document.getElementById('pressStatus');
    el.textContent = text;
    el.className = 'tray-status' + (kind ? ' bulk-status-' + kind : '');
}

async function pressPreview(){
    setPressStatus('Previewing…', '');
    let data;
    try {
        const resp = await fetch('/api/bulk/preview', {method: 'POST', body: pressFormData()});
        data = await resp.json();
        if(!resp.ok){ throw new Error(data.description || ('HTTP ' + resp.status)); }
    } catch(e){
        setPressStatus('Preview failed: ' + e.message, 'err');
        return;
    }
    renderPressPlans(data.plans || []);
    const s = data.summary || {total: 0, ready: 0, blocked: 0};
    const dropped = data.dropped ? ('  •  ' + data.dropped + ' row(s) over the limit dropped') : '';
    document.getElementById('pressSummary').textContent =
        s.total + ' row(s): ' + s.ready + ' ready, ' + s.blocked + ' blocked' + dropped;
    pressHasPreview = true;
    document.getElementById('pressCreateBtn').disabled = s.ready === 0;
    setPressStatus(s.ready + ' draft(s) ready to create. Review, then Create drafts.', 'ok');
}

function renderPressPlans(plans){
    const tbody = document.getElementById('pressRows');
    tbody.textContent = '';
    if(!plans.length){
        const tr = document.createElement('tr');
        const td = document.createElement('td');
        td.colSpan = 7; td.className = 'bulk-empty';
        td.textContent = 'No rows in the sheet.';
        tr.appendChild(td); tbody.appendChild(tr);
        return;
    }
    plans.forEach(p => tbody.appendChild(pressPlanRow(p)));
    document.getElementById('pressSelectAll').checked = true;
}

function pressPlanRow(p){
    const tr = document.createElement('tr');
    const ready = p.status === 'ready';
    tr.className = ready ? 'bulk-ready' : 'bulk-blocked';

    // include checkbox
    const cbCell = document.createElement('td');
    const cb = document.createElement('input');
    cb.type = 'checkbox';
    cb.className = 'press-include';
    cb.dataset.index = p.row_index;
    cb.checked = ready;
    cb.disabled = !ready;
    cbCell.appendChild(cb);
    tr.appendChild(cbCell);

    tr.appendChild(pressTextCell(String(p.row_index + 1)));

    const statusCell = document.createElement('td');
    const badge = document.createElement('span');
    badge.className = 'bulk-badge ' + (ready ? 'bulk-badge-ready' : 'bulk-badge-blocked');
    badge.textContent = ready ? 'ready' : 'blocked';
    statusCell.appendChild(badge);
    tr.appendChild(statusCell);

    // recipients
    const rcpt = document.createElement('td');
    if(p.to && p.to.length){ rcpt.appendChild(pressLineDiv('To: ' + p.to.join(', '))); }
    if(p.cc && p.cc.length){ rcpt.appendChild(pressLineDiv('Cc: ' + p.cc.join(', '))); }
    tr.appendChild(rcpt);

    tr.appendChild(pressTextCell(p.subject || ''));

    // attachment / ftp
    const att = document.createElement('td');
    if(p.uses_ftp){
        att.appendChild(pressLineDiv('FTP: ' + (p.ftp_link || '(no link)')));
    } else if(p.attachment){
        const d = pressLineDiv((p.attachment.exists ? '📎 ' : '⚠ ') + p.attachment.name);
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
        const d = pressLineDiv('⚠ ' + w);
        d.className = 'bulk-warn';
        last.appendChild(d);
    });
    tr.appendChild(last);

    return tr;
}

function pressTextCell(text){
    const td = document.createElement('td');
    td.textContent = text;
    return td;
}

function pressLineDiv(text){
    const d = document.createElement('div');
    d.textContent = text;
    return d;
}

function togglePressSelectAll(){
    const on = document.getElementById('pressSelectAll').checked;
    document.querySelectorAll('.press-include').forEach(cb => {
        if(!cb.disabled){ cb.checked = on; }
    });
}

async function pressCreateDrafts(){
    if(!pressHasPreview){ return; }
    const indices = Array.from(document.querySelectorAll('.press-include'))
        .filter(cb => cb.checked && !cb.disabled)
        .map(cb => parseInt(cb.dataset.index, 10));
    if(!indices.length){
        setPressStatus('Select at least one ready row to create.', 'err');
        return;
    }
    if(!confirm('Create ' + indices.length + ' draft(s) in the shared mailbox? '
                + 'Drafts are NOT sent — you review and send them from Outlook.')){
        return;
    }
    setPressStatus('Creating drafts…', '');
    const fd = pressFormData();
    fd.append('indices', JSON.stringify(indices));
    let data;
    try {
        const resp = await fetch('/api/bulk/create-drafts', {method: 'POST', body: fd});
        data = await resp.json();
        if(!resp.ok){ throw new Error(data.description || ('HTTP ' + resp.status)); }
    } catch(e){
        setPressStatus('Create failed: ' + e.message, 'err');
        return;
    }
    let msg = 'Created ' + data.created + ' of ' + data.requested + ' draft(s).';
    if(data.audit){ msg += '  Audit log: ' + data.audit; }
    setPressStatus(msg, 'ok');
}
