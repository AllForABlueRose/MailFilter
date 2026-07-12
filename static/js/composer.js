// Composer view: the reply-template workbench.
//
// Three columns. LEFT picks the mail the template is rendered against — ten
// built-in examples (with the spreadsheet row assigned to each) in a collapsible
// panel on top, real mail out of the cache below it, fetched ten at a time as you
// scroll. MIDDLE is the template itself: a bar of template squares, then the
// editor or the rendered preview. RIGHT is the function palette you drag into the
// body.
//
// Composer never writes: the preview is a pure dry-run (POST /api/composer/preview
// creates no draft, no audit log, no cache change). Saving a template writes only
// to the template store. Pointing a finished template at a spreadsheet is Press's
// job.
//
// Every server value (subjects, names, rendered bodies, warnings) is inserted as
// DOM TEXT, never HTML — the same rule the people fields follow.

const COMPOSER_BLOCK_MIME = 'application/x-mailfilter-composer-block';

async function loadComposer(){
    await Promise.all([loadComposerTemplates(), loadComposerBlocks(), loadComposerSamples()]);
    resetComposerMails();
    initComposerScroll();
}

// ----- templates (the squares bar + the editor) -----

async function loadComposerTemplates(){
    let data;
    try {
        data = await (await fetch('/api/compose-templates')).json();
    } catch(e){ return; }
    composerTemplates = data.templates || [];
    renderComposerTemplateBar();
}

function renderComposerTemplateBar(){
    const bar = document.getElementById('compTemplateBar');
    bar.textContent = '';
    composerTemplates.forEach(t => {
        const sq = document.createElement('button');
        sq.type = 'button';
        sq.className = 'comp-square';
        sq.style.setProperty('--tmpl-color', t.color || '#0ea5e9');
        sq.title = t.name + (t.error ? '  (invalid)' : '');
        if(t.id === composerTemplateId){ sq.classList.add('comp-selected'); }
        if(t.error){ sq.classList.add('comp-square-invalid'); }

        const initials = document.createElement('span');
        initials.className = 'comp-square-initials';
        initials.textContent = composerInitials(t.name);
        sq.appendChild(initials);

        const label = document.createElement('span');
        label.className = 'comp-square-label';
        label.textContent = t.name;
        sq.appendChild(label);

        sq.addEventListener('click', () => selectComposerTemplate(t.id));
        // Right-click clears the selection (and drops you into a blank new template).
        sq.addEventListener('contextmenu', e => {
            e.preventDefault();
            selectComposerTemplate(null);
        });
        bar.appendChild(sq);
    });

    const add = document.createElement('button');
    add.type = 'button';
    add.className = 'comp-square comp-square-add';
    add.title = 'New template';
    add.textContent = '+';
    if(composerTemplateId === null){ add.classList.add('comp-selected'); }
    add.addEventListener('click', () => selectComposerTemplate(null));
    bar.appendChild(add);
}

function composerInitials(name){
    const words = String(name || '').trim().split(/\s+/).filter(Boolean);
    if(!words.length){ return '—'; }
    return words.slice(0, 2).map(w => w[0].toUpperCase()).join('');
}

function composerTemplate(){
    return composerTemplates.find(t => t.id === composerTemplateId) || null;
}

function selectComposerTemplate(id){
    composerTemplateId = id;
    const t = composerTemplate();
    document.getElementById('compName').value = t ? t.name : '';
    document.getElementById('compBody').value = t ? t.body : '';
    document.getElementById('compAttach').value = t ? (t.attachment_expr || '') : '';
    document.getElementById('compColor').value = t ? (t.color || '#0ea5e9') : '#0ea5e9';
    document.getElementById('compDeleteBtn').hidden = !t;
    setComposerFormError(t && t.error ? t.error : '');
    renderComposerTemplateBar();
    schedulePreview();
}

function setComposerFormError(msg){
    const el = document.getElementById('compFormError');
    el.textContent = msg || '';
    el.hidden = !msg;
}

async function saveComposerTemplate(){
    const payload = {
        name: document.getElementById('compName').value,
        body: document.getElementById('compBody').value,
        attachment_expr: document.getElementById('compAttach').value,
        color: document.getElementById('compColor').value,
    };
    const url = composerTemplateId ? '/api/compose-templates/' + composerTemplateId
                                   : '/api/compose-templates';
    const method = composerTemplateId ? 'PUT' : 'POST';
    let saved;
    try {
        const resp = await fetch(url, {
            method, headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(payload),
        });
        saved = await resp.json();
    } catch(e){ return; }

    await loadComposerTemplates();
    composerTemplateId = saved.id;
    renderComposerTemplateBar();
    document.getElementById('compDeleteBtn').hidden = false;
    // It saves even when invalid (so a half-finished template can be fixed later),
    // but say so rather than pretending it is usable.
    setComposerFormError(saved.error || '');
    schedulePreview();
}

async function deleteComposerTemplate(){
    if(!composerTemplateId){ return; }
    const t = composerTemplate();
    if(!confirm('Delete the template "' + (t ? t.name : '') + '"?')){ return; }
    try {
        await fetch('/api/compose-templates/' + composerTemplateId, {method: 'DELETE'});
    } catch(e){ return; }
    composerTemplateId = null;
    await loadComposerTemplates();
    selectComposerTemplate(null);
}

// ----- edit / preview toggle -----

function setComposerTab(name){
    composerTab = name;
    document.getElementById('compTabEdit').classList.toggle('active', name === 'edit');
    document.getElementById('compTabPreview').classList.toggle('active', name === 'preview');
    document.getElementById('compEditPane').hidden = name !== 'edit';
    document.getElementById('compPreviewPane').hidden = name !== 'preview';
    if(name === 'preview'){ runComposerPreview(); }
}

// ----- the function-block palette -----

async function loadComposerBlocks(){
    let data;
    try {
        data = await (await fetch('/api/composer/blocks')).json();
    } catch(e){ return; }
    composerBlocks = data.blocks || [];
    renderComposerBlocks();
}

function renderComposerBlocks(){
    const wrap = document.getElementById('compBlocks');
    wrap.textContent = '';
    composerBlocks.forEach(b => {
        const card = document.createElement('div');
        card.className = 'comp-block';
        card.draggable = true;
        card.title = 'Drag into the body, or click to insert at the cursor';

        const head = document.createElement('div');
        head.className = 'comp-block-head';
        head.textContent = b.emoji + '  ' + b.name;
        card.appendChild(head);

        const sig = document.createElement('code');
        sig.className = 'comp-block-sig';
        sig.textContent = b.signature;
        card.appendChild(sig);

        const desc = document.createElement('div');
        desc.className = 'comp-block-desc';
        desc.textContent = b.description;
        card.appendChild(desc);

        const demo = document.createElement('div');
        demo.className = 'comp-block-demo';
        const snip = document.createElement('code');
        snip.className = 'comp-block-snippet';
        snip.textContent = b.snippet;
        demo.appendChild(snip);
        const arrow = document.createElement('span');
        arrow.className = 'comp-block-arrow';
        arrow.textContent = 'gives';
        demo.appendChild(arrow);
        const out = document.createElement('code');
        out.className = 'comp-block-out';
        out.textContent = b.demo_output;
        demo.appendChild(out);
        card.appendChild(demo);

        // text/plain is what makes the native drop work: a textarea inserts dropped
        // plain text at the DROP caret, which no manual handler can place as well.
        card.addEventListener('dragstart', e => {
            e.dataTransfer.setData('text/plain', b.snippet);
            e.dataTransfer.setData(COMPOSER_BLOCK_MIME, b.id);
            e.dataTransfer.effectAllowed = 'copy';
            card.classList.add('comp-block-dragging');
        });
        card.addEventListener('dragend', () => card.classList.remove('comp-block-dragging'));
        card.addEventListener('click', () => insertIntoComposerBody(b.snippet));
        wrap.appendChild(card);
    });
}

// The click fallback (and the keyboard path): insert at the cursor.
function insertIntoComposerBody(snippet){
    const ta = document.getElementById('compBody');
    setComposerTab('edit');
    ta.focus();
    const start = ta.selectionStart, end = ta.selectionEnd;
    ta.setRangeText(snippet, start, end, 'end');
    onComposerBodyInput();
}

function onComposerBodyInput(){
    setComposerFormError('');
    schedulePreview();
}

// ----- the mail picker: samples on top, cache mail below -----

async function loadComposerSamples(){
    let data;
    try {
        data = await (await fetch('/api/composer/samples')).json();
    } catch(e){ return; }
    composerSamples = data.samples || [];
    composerFilters = data.filters || [];
    renderComposerSamples();
    renderComposerFilters();
}

function toggleComposerSamples(){
    const panel = document.getElementById('compSamplesPanel');
    const btn = document.getElementById('compSamplesToggle');
    const open = panel.hidden;
    panel.hidden = !open;
    btn.classList.toggle('open', open);
    document.getElementById('compSamplesCaret').textContent = open ? '▾' : '▸';
}

function renderComposerSamples(){
    const list = document.getElementById('compSamplesList');
    list.textContent = '';
    composerSamples.forEach(s => {
        const card = document.createElement('div');
        card.className = 'comp-pick comp-sample';
        card.dataset.ref = s.id;
        if(composerPick && composerPick.source === 'sample' && composerPick.ref === s.id){
            card.classList.add('comp-selected');
        }

        const head = document.createElement('div');
        head.className = 'comp-pick-head';
        head.textContent = s.emoji + '  ' + s.label;
        card.appendChild(head);

        const subj = document.createElement('div');
        subj.className = 'comp-pick-subject';
        subj.textContent = s.mail.subject;
        card.appendChild(subj);

        const from = document.createElement('div');
        from.className = 'comp-pick-from';
        from.textContent = (s.mail.sender || '(no name)') + '  <' + s.mail.sender_email + '>';
        card.appendChild(from);

        const note = document.createElement('div');
        note.className = 'comp-pick-note';
        note.textContent = s.note;
        card.appendChild(note);

        // The sheet row assigned to this example — the row.* half of the context.
        const row = document.createElement('div');
        row.className = 'comp-pick-row';
        Object.keys(s.row).forEach(key => {
            const chip = document.createElement('span');
            chip.className = 'comp-row-chip';
            chip.textContent = 'row.' + key + ' = ' + (s.row[key] === '' ? '(blank)' : s.row[key]);
            row.appendChild(chip);
        });
        card.appendChild(row);

        card.addEventListener('click', () => pickComposerMail('sample', s.id));
        card.addEventListener('contextmenu', e => { e.preventDefault(); clearComposerPick(); });
        list.appendChild(card);
    });
}

function renderComposerFilters(){
    const bar = document.getElementById('compFilters');
    bar.textContent = '';
    composerFilters.forEach(f => {
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'comp-filter';
        btn.title = f.label;
        btn.textContent = f.emoji;
        if(f.id === composerFilterId){ btn.classList.add('active'); }
        btn.addEventListener('click', () => {
            composerFilterId = f.id;
            renderComposerFilters();
            resetComposerMails();
        });
        bar.appendChild(btn);
    });
}

// A filter change starts the lazy list over from the top.
function resetComposerMails(){
    composerCacheMails = [];
    composerOffset = 0;
    composerHasMore = true;
    document.getElementById('compMailList').textContent = '';
    loadMoreComposerMails();
}

async function loadMoreComposerMails(){
    if(composerLoading || !composerHasMore){ return; }
    composerLoading = true;
    setComposerMailStatus('Loading…');
    const params = new URLSearchParams({
        filter: composerFilterId,
        offset: String(composerOffset),
    });
    let data;
    try {
        data = await (await fetch('/api/composer/mails?' + params)).json();
    } catch(e){
        composerLoading = false;
        setComposerMailStatus('Could not load mail.');
        return;
    }
    const page = data.mails || [];
    composerCacheMails = composerCacheMails.concat(page);
    composerOffset += page.length;
    composerHasMore = !!data.has_more;
    page.forEach(m => document.getElementById('compMailList').appendChild(composerMailCard(m)));
    composerLoading = false;
    if(!composerCacheMails.length){
        setComposerMailStatus('No mail in the cache matches this filter.');
    } else if(composerHasMore){
        setComposerMailStatus(composerCacheMails.length + ' of ' + data.total + '  •  scroll for more');
    } else {
        setComposerMailStatus('All ' + data.total + ' shown.');
    }
}

function setComposerMailStatus(text){
    document.getElementById('compMailStatus').textContent = text;
}

function composerMailCard(m){
    const card = document.createElement('div');
    card.className = 'comp-pick comp-cache-mail';
    card.dataset.ref = m.id;
    if(composerPick && composerPick.source === 'mail' && composerPick.ref === m.id){
        card.classList.add('comp-selected');
    }

    const subj = document.createElement('div');
    subj.className = 'comp-pick-subject';
    subj.textContent = m.subject || '(no subject)';
    card.appendChild(subj);

    const from = document.createElement('div');
    from.className = 'comp-pick-from';
    from.textContent = (m.sender.name || m.sender.email || '(unknown)') + '  •  ' + m.received;
    card.appendChild(from);

    const badges = document.createElement('div');
    badges.className = 'comp-pick-badges';
    if(m.has_attachments){ badges.appendChild(composerBadge('📎')); }
    if(m.has_links){ badges.appendChild(composerBadge('🔗')); }
    if(m.has_password){ badges.appendChild(composerBadge('🔑')); }
    if(m.tags && Object.keys(m.tags).length){ badges.appendChild(composerBadge('🏷️')); }
    card.appendChild(badges);
    // The same pill the mail list paints (render.js), from the same resolver — so a
    // mail's org reads identically in both places.
    if((m.org_labels || []).length){ card.appendChild(makeOrgLabels(m.org_labels)); }

    card.addEventListener('click', () => pickComposerMail('mail', m.id));
    card.addEventListener('contextmenu', e => { e.preventDefault(); clearComposerPick(); });
    return card;
}

function composerBadge(text){
    const span = document.createElement('span');
    span.className = 'comp-pick-badge';
    span.textContent = text;
    return span;
}

// The end-of-list sentinel: scrolling it into view fetches the next ten.
function initComposerScroll(){
    if(composerObserver){ return; }
    const sentinel = document.getElementById('compMailSentinel');
    const root = document.getElementById('compCachePanel');
    if(!sentinel || !root || typeof IntersectionObserver === 'undefined'){ return; }
    composerObserver = new IntersectionObserver(entries => {
        if(entries.some(e => e.isIntersecting)){ loadMoreComposerMails(); }
    }, {root});
    composerObserver.observe(sentinel);
}

function pickComposerMail(source, ref){
    composerPick = {source, ref};
    markComposerSelection();
    schedulePreview();
}

function clearComposerPick(){
    composerPick = null;
    markComposerSelection();
    schedulePreview();
}

function markComposerSelection(){
    document.querySelectorAll('#view-composer .comp-pick').forEach(card => {
        const isSample = card.classList.contains('comp-sample');
        const source = isSample ? 'sample' : 'mail';
        const on = !!composerPick && composerPick.source === source
                   && composerPick.ref === card.dataset.ref;
        card.classList.toggle('comp-selected', on);
    });
}

// ----- the live preview -----

function schedulePreview(){
    clearTimeout(composerPreviewTimer);
    composerPreviewTimer = setTimeout(runComposerPreview, 250);
}

async function runComposerPreview(){
    const pane = document.getElementById('compPreviewPane');
    if(!composerPick){
        renderComposerPreviewEmpty('Pick an example email or a cached mail on the left to see '
            + 'what this template renders to.');
        return;
    }
    const payload = {
        source: composerPick.source,
        ref: composerPick.ref,
        body: document.getElementById('compBody').value,
        attachment_expr: document.getElementById('compAttach').value,
    };
    let data, resp;
    try {
        resp = await fetch('/api/composer/preview', {
            method: 'POST', headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(payload),
        });
        data = await resp.json();
        if(!resp.ok){ throw new Error(data.description || ('HTTP ' + resp.status)); }
    } catch(e){
        renderComposerPreviewEmpty('Preview failed: ' + e.message);
        return;
    }
    renderComposerPreview(data.plan, data.row);
    if(data.template_error){ setComposerFormError(data.template_error); }
    pane.scrollTop = 0;
}

function renderComposerPreviewEmpty(text){
    const pane = document.getElementById('compPreviewPane');
    pane.textContent = '';
    const p = document.createElement('p');
    p.className = 'comp-preview-empty';
    p.textContent = text;
    pane.appendChild(p);
}

function renderComposerPreview(plan, row){
    const pane = document.getElementById('compPreviewPane');
    pane.textContent = '';

    const status = document.createElement('div');
    const ready = plan.status === 'ready';
    status.className = 'bulk-badge ' + (ready ? 'bulk-badge-ready' : 'bulk-badge-blocked');
    status.textContent = ready ? 'ready — Press would draft this row'
                               : 'blocked — Press would refuse this row';
    pane.appendChild(status);

    (plan.warnings || []).forEach(w => {
        const d = document.createElement('div');
        d.className = 'bulk-warn';
        d.textContent = '⚠ ' + w;
        pane.appendChild(d);
    });

    pane.appendChild(composerPreviewField('Subject', plan.subject));
    pane.appendChild(composerPreviewField('To', (plan.to || []).join(', ')));
    pane.appendChild(composerPreviewField('Cc', (plan.cc || []).join(', ')));
    if(plan.uses_ftp){
        pane.appendChild(composerPreviewField('FTP link', plan.ftp_link || '(none)'));
    } else if(plan.attachment){
        pane.appendChild(composerPreviewField(
            'Attachment',
            (plan.attachment.exists ? '📎 ' : '⚠ ') + plan.attachment.name
                + (plan.attachment.exists ? '' : '  (not on the file server)')));
    } else {
        pane.appendChild(composerPreviewField('Attachment', '(none resolved)'));
    }

    const label = document.createElement('div');
    label.className = 'comp-preview-label';
    label.textContent = 'Body';
    pane.appendChild(label);
    const body = document.createElement('pre');
    body.className = 'bulk-bodyprev comp-preview-body';
    body.textContent = plan.body || '(empty)';
    pane.appendChild(body);

    // The row this render saw — for a cached mail it is synthesized from the mail
    // itself (there is no spreadsheet), so show what the template was actually given.
    const rowLabel = document.createElement('div');
    rowLabel.className = 'comp-preview-label';
    rowLabel.textContent = 'Row data used';
    pane.appendChild(rowLabel);
    const chips = document.createElement('div');
    chips.className = 'comp-pick-row';
    Object.keys(row || {}).forEach(key => {
        const chip = document.createElement('span');
        chip.className = 'comp-row-chip';
        chip.textContent = 'row.' + key + ' = ' + (row[key] === '' ? '(blank)' : row[key]);
        chips.appendChild(chip);
    });
    pane.appendChild(chips);
}

function composerPreviewField(label, value){
    const wrap = document.createElement('div');
    wrap.className = 'comp-preview-field';
    const l = document.createElement('span');
    l.className = 'comp-preview-label';
    l.textContent = label;
    wrap.appendChild(l);
    const v = document.createElement('span');
    v.className = 'comp-preview-value';
    v.textContent = value || '—';
    wrap.appendChild(v);
    return wrap;
}
