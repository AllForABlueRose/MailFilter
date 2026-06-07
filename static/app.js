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
        card.innerHTML = `
            <h3>${mail.icon} ${mail.subject}</h3>
            <div class="meta">${mail.sender}<br>${mail.received}</div>
            <div>${mail.preview}</div>
        `;
        container.appendChild(card);
    });
}

loadMail();
setInterval(loadMail, 30000);
