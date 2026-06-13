// Card rendering shared by the list, the thread popup, and the workspace.

// Build a mail card. Used everywhere, so attachments, links, and tags render
// identically.
function createCard(mail){
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
