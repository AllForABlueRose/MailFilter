let resourcesOnly = false;

function toggleResources(){
    resourcesOnly = !resourcesOnly;
    const btn = document.getElementById('resourcesToggle');
    btn.classList.toggle('active', resourcesOnly);
    btn.setAttribute('aria-pressed', String(resourcesOnly));
    btn.textContent = `📎 Attachments & Links: ${resourcesOnly ? 'On' : 'Off'}`;
    loadMail();
}

async function refreshMail(){
    await fetch('/refresh', {method: 'POST'});
    loadMail();
}

async function loadMail(){
    const params = new URLSearchParams({
        start: document.getElementById('startDate').value,
        end: document.getElementById('endDate').value,
        main: document.getElementById('mainKeywords').value,
        optional: document.getElementById('optionalKeywords').value,
        exclude: document.getElementById('excludeKeywords').value,
        sender: document.getElementById('senderFilter').value,
        recipient: document.getElementById('recipientFilter').value,
        resources: resourcesOnly ? '1' : '',
    });
    const response = await fetch(`/api/mail?${params}`);
    const data = await response.json();

    document.getElementById('lastRefresh').innerText = data.last_refresh;
    let status = data.fetch_status;
    if(data.fetch_error){
        status += " | " + data.fetch_error;
    }
    document.getElementById('fetchStatus').innerText = status;

    const container = document.getElementById('mailContainer');
    container.innerHTML = '';
    data.mails.forEach(mail => {
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
        container.appendChild(card);
    });
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
            a.textContent = att.filename;
            a.className = 'resource-item';
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

loadMail();
setInterval(loadMail, 30000);
