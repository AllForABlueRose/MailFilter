// The thread / single-message popup.

async function openThread(mailId){
    try{
        // Pass the active search so matches highlight in the popup too.
        const params = new URLSearchParams({
            id: mailId,
            main: document.getElementById('mainKeywords').value,
            optional: document.getElementById('optionalKeywords').value,
        });
        const response = await fetch('/api/thread?' + params);
        const data = await response.json();
        threadMails = data.mails || [];
        threadOldestFirst = true;   // earliest -> latest, top to bottom
        renderThread();
        document.getElementById('threadModal').hidden = false;
    }catch(e){
        // leave the list as-is on failure
    }
}

function renderThread(){
    const body = document.getElementById('threadBody');
    body.innerHTML = '';
    const ordered = threadOldestFirst ? threadMails : threadMails.slice().reverse();
    ordered.forEach(mail => body.appendChild(createCard(mail)));

    const count = threadMails.length;
    document.getElementById('threadTitle').textContent =
        count === 1 ? 'Message' : `Thread — ${count} messages`;
    const flip = document.getElementById('threadFlip');
    flip.hidden = count <= 1;   // ordering is meaningless for a single message
    flip.textContent = threadOldestFirst ? 'Order: Oldest → Newest' : 'Order: Newest → Oldest';
}

function flipThreadOrder(){
    threadOldestFirst = !threadOldestFirst;
    renderThread();
}

function closeThread(){
    document.getElementById('threadModal').hidden = true;
}
