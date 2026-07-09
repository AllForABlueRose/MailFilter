// Card rendering shared by the list, the thread popup, and the workspace.

// Build a mail card. Used everywhere, so attachments, links, and tags render
// identically.
function createCard(mail){
    const card = document.createElement('div');
    card.className = `card ${mail.is_thread ? 'thread' : 'single'}`;

    // subject/preview are pre-escaped server-side (safe as HTML); people are
    // raw and inserted via the DOM as text (see buildMeta).
    const heading = document.createElement('h3');
    heading.innerHTML = `${mail.icon} ${mail.subject}`;
    card.appendChild(heading);

    card.appendChild(buildMeta(mail));

    const preview = document.createElement('div');
    preview.className = 'card-preview';
    preview.innerHTML = mail.preview;   // pre-escaped; carries pw-loc locator spans
    card.appendChild(preview);

    const resources = renderResources(mail);
    if(resources){
        card.appendChild(resources);
    }
    // Detected passwords sit in the same collapsed bottom area as attachments and
    // links: a group label that reveals the value chips on hover. Reuse the
    // resources container if there already is one so there's a single divider.
    if(mail.passwords && mail.passwords.length){
        let container = resources;
        if(!container){
            container = document.createElement('div');
            container.className = 'resources';
            card.appendChild(container);
        }
        container.appendChild(renderPasswordGroup(mail, card));
    }
    // Top-right corner: tags marking actions performed on this mail (and, in the
    // tray, a remove button appended here too). `mail.tags` comes from the
    // server: each action is "recent" (coloured) or "old" (>7 days, greyed).
    const corner = document.createElement('div');
    corner.className = 'card-corner';
    // The emoji tags share one row; the tray-remove button (workspace.js) joins it.
    const cornerTags = document.createElement('div');
    cornerTags.className = 'card-corner-tags';
    const tags = mail.tags || {};
    if(tags.marked){ cornerTags.appendChild(makeTag('🎯', 'Marked', tags.marked)); }
    if(tags.downloaded){ cornerTags.appendChild(makeTag('📥', 'Attachments downloaded', tags.downloaded)); }
    if(tags.links){ cornerTags.appendChild(makeTag('🌐', 'Links opened', tags.links)); }
    if(tags.deduped){ cornerTags.appendChild(makeTag('🧬', 'Deduplicated', tags.deduped)); }
    // A detected password (from the last Smart Password Detection scan). The
    // value itself lives in the resources area (renderPasswordGroup), not here.
    if(mail.has_password){ cornerTags.appendChild(makePasswordBadge()); }
    corner.appendChild(cornerTags);
    // Below the emoji row: the sender's resolved customer-organization label(s),
    // each a pill in that org's colour showing its display name (server-resolved).
    if(mail.org_labels && mail.org_labels.length){ corner.appendChild(makeOrgLabels(mail.org_labels)); }
    card.appendChild(corner);
    return card;
}

// A right-aligned row of org pills that mirror each org's card settings: a `filled`
// card -> colour-filled pill with white/black ink; an `outline` card -> inverted
// (light fill, coloured text + border). CSS reads --org-color; the style-/ink-
// classes pick fill vs text. The display name is user input, so it is inserted as DOM
// text (the people-field rule).
function makeOrgLabels(labels){
    const row = document.createElement('div');
    row.className = 'card-orgs';
    labels.forEach(function(l){
        const pill = document.createElement('span');
        pill.className = 'org-label style-' + (l.card_style || 'outline')
            + (l.card_ink === 'black' ? ' ink-black' : '');
        pill.textContent = l.name;
        pill.title = l.name;
        if(l.color){ pill.style.setProperty('--org-color', l.color); }
        row.appendChild(pill);
    });
    return row;
}

// The 🔑 badge shown on a card whose body contained a detected password.
function makePasswordBadge(){
    const badge = document.createElement('span');
    badge.className = 'card-tag password';
    badge.textContent = '🔑';
    badge.title = 'Password detected';
    return badge;
}

function makeTag(symbol, label, recency){
    const tag = document.createElement('span');
    tag.className = recency === 'old' ? 'card-tag old' : 'card-tag';
    tag.textContent = symbol;
    tag.title = recency === 'old' ? `${label} (over 7 days ago)` : label;
    return tag;
}

// The From / To / Cc / date block. People are draggable into the person search
// fields; the date is plain text.
function buildMeta(mail){
    const meta = document.createElement('div');
    meta.className = 'meta';
    appendPeopleLine(meta, 'From', [mail.sender || {}]);
    if(mail.recipients && mail.recipients.length){ appendPeopleLine(meta, 'To', mail.recipients); }
    if(mail.cc && mail.cc.length){ appendPeopleLine(meta, 'Cc', mail.cc); }
    const date = document.createElement('div');
    date.className = 'meta-date';
    date.textContent = mail.received;
    meta.appendChild(date);
    return meta;
}

function appendPeopleLine(meta, label, people){
    const line = document.createElement('div');
    line.className = 'meta-line';
    const tag = document.createElement('span');
    tag.className = 'meta-label';
    tag.textContent = label + ': ';
    line.appendChild(tag);
    people.forEach((person, i) => {
        if(i > 0){ line.appendChild(document.createTextNode(', ')); }
        line.appendChild(renderPerson(person));
    });
    meta.appendChild(line);
}

// A person chip: the name (falling back to the email) is shown and draggable;
// the email is revealed on hover and is separately draggable. Both insert their
// value into a person search field when dropped there.
function renderPerson(person){
    const name = person.name || person.email || '(unknown)';
    const wrap = document.createElement('span');
    wrap.className = 'person';

    const nameEl = document.createElement('span');
    nameEl.className = 'p-name';
    nameEl.textContent = name;
    nameEl.draggable = true;
    nameEl.title = person.email || name;   // hover shows the email
    nameEl.addEventListener('dragstart', e => personDragStart(e, name));
    wrap.appendChild(nameEl);

    if(person.email && person.email !== name){
        const emailEl = document.createElement('span');
        emailEl.className = 'p-email';
        emailEl.textContent = ` <${person.email}>`;
        emailEl.draggable = true;
        emailEl.addEventListener('dragstart', e => personDragStart(e, person.email));
        wrap.appendChild(emailEl);
    }
    return wrap;
}

function personDragStart(e, value){
    e.stopPropagation();   // don't also start a card (workspace) drag
    e.dataTransfer.setData('text/x-mailfilter-person', value);
    e.dataTransfer.effectAllowed = 'copy';
    // Highlight the exact name/email token being dragged, until the drag ends.
    const el = e.currentTarget;
    el.classList.add('dragging');
    el.addEventListener('dragend', () => el.classList.remove('dragging'), {once: true});
}

// Links and attachment filenames are draggable into the regex compiler.
function segmentDragStart(e, value){
    e.stopPropagation();
    e.dataTransfer.setData('text/x-mailfilter-segment', value);
    e.dataTransfer.effectAllowed = 'copy';
}

// Save one attachment into today's server workspace via the same endpoint (and thus
// the same features: org resolution, manifest, datetime stamping, naming/dedup, the
// 📥 downloaded tag) as the bulk "download tray" button — instead of a browser
// download. Reflects the result inline, then refreshes the list so the tag shows.
function downloadAttachmentToWorkspace(mail, att, statusEl){
    if(statusEl){ statusEl.textContent = ' saving…'; }
    fetch('/api/download', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({items: [{id: mail.id, index: att.index}],
                              append_customer_name: appendCustomerName}),
    }).then(r => r.json()).then(result => {
        const ok = (result.saved || []).length;
        const failed = (result.errors || []).length;
        if(statusEl){ statusEl.textContent = ok ? ' ✓ saved' : (failed ? ' ✗ failed' : ''); }
        if(ok && typeof loadMail === 'function'){ loadMail(); }
    }).catch(() => { if(statusEl){ statusEl.textContent = ' ✗ failed'; } });
}

// Build the attachments/links block. The displayed name/URL is the server's
// pre-escaped + keyword-highlighted HTML; the raw value (used for the icon,
// href, and drag) never reaches the DOM as HTML.
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
            const icon = document.createElement('span');
            icon.className = 'file-icon';
            icon.textContent = fileIcon(att.filename);
            a.appendChild(icon);
            const name = document.createElement('span');
            name.innerHTML = att.filename_html;   // escaped + highlighted server-side
            a.appendChild(name);
            const status = document.createElement('span');
            status.className = 'resource-status';
            a.appendChild(status);
            // Clicking saves into today's server workspace (same features as the bulk
            // download button), instead of a plain browser download.
            a.addEventListener('click', e => {
                e.preventDefault();
                downloadAttachmentToWorkspace(mail, att, status);
            });
            a.addEventListener('dragstart', e => segmentDragStart(e, att.filename));
            group.appendChild(a);
        });
        wrap.appendChild(group);
    }

    if(links.length){
        const group = document.createElement('div');
        group.className = 'resource-group';
        group.appendChild(makeLabel(`🔗 Links (${links.length})`));
        links.forEach(link => {
            const a = document.createElement('a');
            a.href = link.url;
            a.innerHTML = link.url_html;          // escaped + highlighted server-side
            a.target = '_blank';
            a.rel = 'noopener noreferrer';
            a.className = 'resource-item';
            a.addEventListener('dragstart', e => segmentDragStart(e, link.url));
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

// The detected-password group: a collapsed "🔑 Passwords (N)" label whose value
// chips appear on hover (same mechanism as attachments/links). Hovering a chip
// lights up the matching occurrence(s) in this card's message preview in orange.
function renderPasswordGroup(mail, card){
    const group = document.createElement('div');
    group.className = 'resource-group pw-group';
    group.appendChild(makeLabel(`🔑 Passwords (${mail.passwords.length})`));
    mail.passwords.forEach((pw, i) => {
        const item = document.createElement('span');
        item.className = 'resource-item pw-item';
        item.addEventListener('mouseenter', () => togglePwLocation(card, i, true));
        item.addEventListener('mouseleave', () => togglePwLocation(card, i, false));

        const value = document.createElement('span');
        value.className = 'pw-value';
        value.textContent = pw;   // raw value, inserted as DOM text (never HTML)
        value.title = 'Hover to locate it in the message';
        item.appendChild(value);

        // Detected passwords are auto-recorded into the sender's org Key Vault by
        // the scan itself (POST /api/passwords/scan); there is no per-chip save.
        group.appendChild(item);
    });
    return group;
}

// Toggle the orange locator on every occurrence of password `index` in this
// card's preview (the server tagged them with class pw-loc + data-pwloc).
function togglePwLocation(card, index, on){
    card.querySelectorAll(`.pw-loc[data-pwloc="${index}"]`)
        .forEach(el => el.classList.toggle('pw-loc-active', on));
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
