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
    preview.innerHTML = mail.preview;
    card.appendChild(preview);

    const resources = renderResources(mail);
    if(resources){
        card.appendChild(resources);
    }
    // Top-right corner: tags marking actions performed on this mail (and, in the
    // tray, a remove button appended here too). `mail.tags` comes from the
    // server: each action is "recent" (coloured) or "old" (>7 days, greyed).
    const corner = document.createElement('div');
    corner.className = 'card-corner';
    const tags = mail.tags || {};
    if(tags.downloaded){ corner.appendChild(makeTag('📥', 'Attachments downloaded', tags.downloaded)); }
    if(tags.links){ corner.appendChild(makeTag('🌐', 'Links opened', tags.links)); }
    card.appendChild(corner);
    return card;
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
}

// Links and attachment filenames are draggable into the regex compiler.
function segmentDragStart(e, value){
    e.stopPropagation();
    e.dataTransfer.setData('text/x-mailfilter-segment', value);
    e.dataTransfer.effectAllowed = 'copy';
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
