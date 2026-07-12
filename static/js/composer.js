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
    composerSource = t ? t.body : '';
    document.getElementById('compName').value = t ? t.name : '';
    document.getElementById('compAttach').value = t ? (t.attachment_expr || '') : '';
    document.getElementById('compColor').value = t ? (t.color || '#0ea5e9') : '#0ea5e9';
    document.getElementById('compDeleteBtn').hidden = !t;
    if(composerTab === 'edit'){
        document.getElementById('compBody').value = composerSource;
    }
    setComposerFormError(t && t.error ? t.error : '');
    renderComposerTemplateBar();
    schedulePreview();
}

// The template's source, wherever we are. In Preview the text area holds the RENDERED
// reply, so the source must be kept here rather than read back off it.
function composerBodyText(){
    return composerTab === 'edit'
        ? document.getElementById('compBody').value : composerSource;
}

function setComposerFormError(msg){
    const el = document.getElementById('compFormError');
    el.textContent = msg || '';
    el.hidden = !msg;
}

async function saveComposerTemplate(){
    const payload = {
        name: document.getElementById('compName').value,
        body: composerBodyText(),
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
    composerSource = saved.body;
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
//
// There is ONE text area. In Edit it holds the template source and you type into it;
// in Preview it holds the rendered reply and is read-only — you read the result in the
// same place you wrote it, at full height. Switching back restores the source (the
// template is kept in composerSource, never read back off the textarea in preview).

function setComposerTab(name){
    if(name === composerTab) return;
    const ta = document.getElementById('compBody');
    if(name === 'preview'){
        composerSource = ta.value;          // remember what is being edited
    }
    composerTab = name;
    document.getElementById('compTabEdit').classList.toggle('active', name === 'edit');
    document.getElementById('compTabPreview').classList.toggle('active', name === 'preview');

    const editing = name === 'edit';
    ta.readOnly = !editing;
    ta.classList.toggle('comp-body-preview', !editing);
    // The editor's own controls have no meaning while reading the output.
    document.getElementById('compEditRow').hidden = !editing;
    document.getElementById('compHint').hidden = !editing;
    document.getElementById('compAttachLabel').hidden = !editing;
    document.getElementById('compAttach').hidden = !editing;

    if(editing){
        ta.value = composerSource;
    } else {
        ta.value = '';                      // filled by the preview when it lands
        runComposerPreview();
    }
}

// ----- the function-block palette -----

async function loadComposerBlocks(){
    let data;
    try {
        data = await (await fetch('/api/composer/blocks')).json();
    } catch(e){ return; }
    composerBlocks = data.blocks || [];
    composerCycleMs = data.cycle_ms || 4500;
    renderComposerBlocks();
    startComposerBlockCycle();
}

function renderComposerBlocks(){
    const wrap = document.getElementById('compBlocks');
    wrap.textContent = '';
    composerBlocks.forEach((b, i) => {
        const card = document.createElement('div');
        card.className = 'comp-block';
        card.draggable = true;
        card.dataset.index = String(i);
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

        // The snippet, then INPUT vs RESULT for one demo case. The case cycles slowly
        // (below), so you watch the same snippet answer different inputs.
        const demo = document.createElement('div');
        demo.className = 'comp-block-demo';
        const snip = document.createElement('code');
        snip.className = 'comp-block-snippet';
        snip.textContent = b.snippet;
        demo.appendChild(snip);

        const io = document.createElement('div');
        io.className = 'comp-block-io';
        demo.appendChild(io);
        card.appendChild(demo);

        const dots = document.createElement('div');
        dots.className = 'comp-block-dots';
        card.appendChild(dots);

        // Hovering pauses the cycle on this block (and highlights it), so a case you
        // want to read does not slide away under you.
        card.addEventListener('mouseenter', () => { composerHoverBlock = i; });
        card.addEventListener('mouseleave', () => {
            if(composerHoverBlock === i){ composerHoverBlock = null; }
        });

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
        paintComposerBlockCase(card, b, 0);
    });
}

// Draw one demo case: the inputs it was given, and the result they produced.
function paintComposerBlockCase(card, block, caseIndex){
    const demos = block.demos || [];
    if(!demos.length) return;
    const demo = demos[caseIndex % demos.length];

    const io = card.querySelector('.comp-block-io');
    io.textContent = '';
    (demo.inputs || []).forEach(input => {
        const line = document.createElement('div');
        line.className = 'comp-block-in';
        const k = document.createElement('code');
        k.className = 'comp-block-in-key';
        k.textContent = input.name;
        line.appendChild(k);
        const v = document.createElement('code');
        v.className = 'comp-block-in-val';
        v.textContent = input.value === '' ? '(blank)' : input.value;
        line.appendChild(v);
        io.appendChild(line);
    });
    const arrow = document.createElement('div');
    arrow.className = 'comp-block-arrow';
    arrow.textContent = 'gives';
    io.appendChild(arrow);
    const out = document.createElement('code');
    out.className = 'comp-block-out';
    out.textContent = demo.output;
    io.appendChild(out);

    // A dot per case, so it is obvious there is more than one to see.
    const dots = card.querySelector('.comp-block-dots');
    dots.textContent = '';
    if(demos.length > 1){
        demos.forEach((_d, i) => {
            const dot = document.createElement('span');
            dot.className = 'comp-block-dot' + (i === caseIndex % demos.length ? ' on' : '');
            dot.textContent = '•';
            dots.appendChild(dot);
        });
    }
    io.classList.remove('comp-block-io-fade');
    // Restart the fade-in so the change is noticed rather than blinking.
    void io.offsetWidth;
    io.classList.add('comp-block-io-fade');
}

// Cycle every block to its next case, slowly. The hovered block holds still.
function startComposerBlockCycle(){
    clearInterval(composerCycleTimer);
    composerCycleTimer = setInterval(() => {
        const view = document.getElementById('view-composer');
        if(!view || view.classList.contains('view-hidden')) return;   // not on screen
        composerCaseIndex++;
        document.querySelectorAll('#compBlocks .comp-block').forEach(card => {
            const i = parseInt(card.dataset.index, 10);
            if(composerHoverBlock === i) return;                       // paused: being read
            paintComposerBlockCase(card, composerBlocks[i], composerCaseIndex);
        });
    }, composerCycleMs);
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
    if(composerTab === 'edit'){
        composerSource = document.getElementById('compBody').value;
    }
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
    if(!composerPick){
        setComposerPreviewText('');
        renderComposerInfo(null, null,
            'Pick an example email or a cached mail on the left to see what this '
            + 'template renders to.');
        return;
    }
    const payload = {
        source: composerPick.source,
        ref: composerPick.ref,
        body: composerTab === 'edit'
            ? document.getElementById('compBody').value : composerSource,
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
        renderComposerInfo(null, null, 'Preview failed: ' + e.message);
        return;
    }
    setComposerPreviewText(data.plan.body || '');
    renderComposerInfo(data.plan, data.row, '');
    if(data.template_error){ setComposerFormError(data.template_error); }
}

// The rendered reply goes into the main text area itself — read-only, full height.
function setComposerPreviewText(text){
    if(composerTab !== 'preview') return;
    const ta = document.getElementById('compBody');
    ta.value = text;
    ta.scrollTop = 0;
}

// The slim strip under the text area: what would be drafted, and the row it used.
// Both halves are height-capped and scroll; neither repeats the body.
function renderComposerInfo(plan, row, message){
    const meta = document.getElementById('compInfoMeta');
    const rowBox = document.getElementById('compInfoRow');
    meta.textContent = '';
    rowBox.textContent = '';

    if(message){
        const p = document.createElement('div');
        p.className = 'comp-info-empty';
        p.textContent = message;
        meta.appendChild(p);
        return;
    }
    if(!plan) return;

    const status = document.createElement('span');
    const ready = plan.status === 'ready';
    status.className = 'bulk-badge ' + (ready ? 'bulk-badge-ready' : 'bulk-badge-blocked');
    status.textContent = ready ? 'ready — Press would draft this'
                               : 'blocked — Press would refuse this';
    meta.appendChild(status);

    (plan.warnings || []).forEach(w => {
        const d = document.createElement('div');
        d.className = 'bulk-warn';
        d.textContent = '⚠ ' + w;
        meta.appendChild(d);
    });

    meta.appendChild(composerInfoField('Subject', plan.subject));
    meta.appendChild(composerInfoField('To', (plan.to || []).join(', ')));
    meta.appendChild(composerInfoField('Cc', (plan.cc || []).join(', ')));
    if(plan.uses_ftp){
        meta.appendChild(composerInfoField('FTP link', plan.ftp_link || '(none)'));
    } else if(plan.attachment){
        meta.appendChild(composerInfoField(
            'Attachment',
            (plan.attachment.exists ? '📎 ' : '⚠ ') + plan.attachment.name
                + (plan.attachment.exists ? '' : '  (not on the file server)')));
    } else {
        meta.appendChild(composerInfoField('Attachment', '(none resolved)'));
    }

    // The row this render actually saw. For a cached mail it is synthesized from the
    // mail itself (there is no spreadsheet), so show what the template was given.
    Object.keys(row || {}).forEach(key => {
        const chip = document.createElement('span');
        chip.className = 'comp-row-chip';
        chip.textContent = 'row.' + key + ' = ' + (row[key] === '' ? '(blank)' : row[key]);
        rowBox.appendChild(chip);
    });
}

function composerInfoField(label, value){
    const wrap = document.createElement('div');
    wrap.className = 'comp-info-field';
    const l = document.createElement('span');
    l.className = 'comp-info-key';
    l.textContent = label;
    wrap.appendChild(l);
    const v = document.createElement('span');
    v.className = 'comp-info-value';
    v.textContent = value || '—';
    wrap.appendChild(v);
    return wrap;
}
