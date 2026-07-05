// Brute Force Resolve Customer Name: the "Suspected Customers List" popup. The list
// is keyword->organization mappings ({keyword, org_id}) persisted server-side via
// /api/customer-match and read at download/report time by the server, so this file
// only edits the list. The feature's on/off toggle lives in search.js; the sidebar
// block + this modal are in index.html.
//
// Each row is a keyword text input + an organization <select> populated from
// Customer Management (/api/organizations). The org dropdown is fetched fresh when
// the popup opens so it works regardless of whether the Customers tab was visited.

let suspectedCustomerOrgs = [];  // [{id, name}, ...] for the row dropdowns

function openSuspectedCustomers(){
    Promise.all([
        fetch('/api/organizations').then(r => r.json()).catch(() => ({organizations: []})),
        fetch('/api/customer-match').then(r => r.json()).catch(() => ({customers: []})),
    ]).then(([orgData, matchData]) => {
        suspectedCustomerOrgs = (orgData.organizations || [])
            .map(o => ({id: o.id, name: o.name}));
        const rows = document.getElementById('suspectedCustomersRows');
        rows.innerHTML = '';
        const saved = matchData.customers || [];
        if(saved.length){
            saved.forEach(m => addSuspectedCustomerRow(m.keyword, m.org_id));
        }else{
            addSuspectedCustomerRow();  // start with one empty row
        }
        document.getElementById('suspectedCustomersModal').hidden = false;
    }).catch(() => {});
}

function closeSuspectedCustomers(){
    document.getElementById('suspectedCustomersModal').hidden = true;
}

// Build the <select> options: a blank "(unassigned)" plus one per organization.
// A saved org that no longer exists is shown as a disabled placeholder so it isn't
// silently dropped from a row the user hasn't touched.
function _orgSelect(orgId){
    const sel = document.createElement('select');
    sel.className = 'suspected-org';
    const blank = document.createElement('option');
    blank.value = '';
    blank.textContent = '(unassigned)';
    sel.appendChild(blank);
    let matched = !orgId;
    suspectedCustomerOrgs.forEach(o => {
        const opt = document.createElement('option');
        opt.value = o.id;
        opt.textContent = o.name;
        if(o.id === orgId){ opt.selected = true; matched = true; }
        sel.appendChild(opt);
    });
    if(!matched){
        const gone = document.createElement('option');
        gone.value = orgId;
        gone.textContent = '(deleted organization)';
        gone.selected = true;
        sel.appendChild(gone);
    }
    return sel;
}

function addSuspectedCustomerRow(keyword, orgId){
    const rows = document.getElementById('suspectedCustomersRows');
    const row = document.createElement('div');
    row.className = 'suspected-row';

    const kw = document.createElement('input');
    kw.type = 'text';
    kw.className = 'suspected-keyword';
    kw.placeholder = 'keyword in mail content';
    kw.value = keyword || '';

    const arrow = document.createElement('span');
    arrow.className = 'suspected-arrow';
    arrow.textContent = '→';

    const sel = _orgSelect(orgId || '');

    const remove = document.createElement('button');
    remove.type = 'button';
    remove.className = 'icon-btn';
    remove.title = 'Remove';
    remove.textContent = '✕';
    remove.onclick = () => row.remove();

    row.append(kw, arrow, sel, remove);
    rows.appendChild(row);
}

async function saveSuspectedCustomers(){
    const customers = [];
    document.querySelectorAll('#suspectedCustomersRows .suspected-row').forEach(row => {
        const keyword = row.querySelector('.suspected-keyword').value.trim();
        const org_id = row.querySelector('.suspected-org').value;
        if(keyword){ customers.push({keyword, org_id}); }
    });
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
