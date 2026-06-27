// Resolve Customer Name To Downloads: the "Suspected Customers List" popup. The
// list (one customer name per line) is persisted server-side via /api/customer-match
// and read at download time by the server, so this file only edits the list. The
// feature's on/off toggle lives in search.js; the sidebar block + this modal are in
// index.html.

function openSuspectedCustomers(){
    fetch('/api/customer-match')
        .then(r => r.json())
        .then(data => {
            document.getElementById('suspectedCustomersText').value =
                (data.customers || []).join('\n');
            document.getElementById('suspectedCustomersModal').hidden = false;
        })
        .catch(() => {});
}

function closeSuspectedCustomers(){
    document.getElementById('suspectedCustomersModal').hidden = true;
}

async function saveSuspectedCustomers(){
    const text = document.getElementById('suspectedCustomersText').value;
    const customers = text.split('\n').map(s => s.trim()).filter(Boolean);
    try{
        const res = await fetch('/api/customer-match', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({customers}),
        });
        if(!res.ok) throw new Error('save failed');
        closeSuspectedCustomers();
    }catch(e){
        // Leave the popup open so the user can retry.
    }
}
