// Brute Force Resolve Customer Name: the "Suspected Customers List" popup. The list
// is keyword->organization mappings ({keyword, org_id}) persisted server-side via
// /api/customer-match and read at download/report time by the server, so this file
// only edits the list. The feature's on/off toggle lives in search.js; the sidebar
// block + this modal are in index.html.
//
// Each row is a keyword text input + an organization picker populated from
// Customer Management (/api/organizations). The org list is fetched fresh when
// the popup opens so it works regardless of whether the Customers tab was visited.
// The picker is a filterable combobox: typing narrows the org list to substring
// matches (see _orgCombo).

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

// Build the org picker: a filterable combobox (text input over a dropdown) whose
// committed org_id lives in a hidden .suspected-org input the save path reads. The
// option list is a blank "(unassigned)" plus one per organization; typing narrows
// it to case-insensitive substring matches. A saved org that no longer exists shows
// as "(deleted organization)" and keeps its id, so a row the user hasn't touched
// isn't silently dropped.
function _orgCombo(orgId){
    const options = [{id: '', name: '(unassigned)'}].concat(suspectedCustomerOrgs);

    const combo = document.createElement('div');
    combo.className = 'suspected-org-combo';

    const input = document.createElement('input');
    input.type = 'text';
    input.className = 'suspected-org-input';
    input.autocomplete = 'off';
    input.placeholder = 'organization';

    const hidden = document.createElement('input');
    hidden.type = 'hidden';
    hidden.className = 'suspected-org';
    hidden.value = orgId || '';

    const list = document.createElement('ul');
    list.className = 'suspected-org-list';
    list.hidden = true;

    // The visible label for whatever org_id is currently committed.
    function labelFor(id){
        if(!id) return '(unassigned)';
        const o = suspectedCustomerOrgs.find(x => x.id === id);
        return o ? o.name : '(deleted organization)';
    }
    input.value = labelFor(hidden.value);

    function render(query){
        const q = (query || '').trim().toLowerCase();
        list.innerHTML = '';
        const matches = options.filter(o => o.name.toLowerCase().includes(q));
        if(!matches.length){
            const li = document.createElement('li');
            li.className = 'suspected-org-empty';
            li.textContent = 'No matching organizations';
            list.appendChild(li);
            return;
        }
        matches.forEach(o => {
            const li = document.createElement('li');
            li.className = 'suspected-org-opt';
            li.textContent = o.name;
            li.dataset.orgId = o.id;
            if(o.id === hidden.value) li.classList.add('suspected-org-opt-active');
            // mousedown, not click, so the pick beats the input's blur handler.
            li.addEventListener('mousedown', ev => { ev.preventDefault(); pick(o.id, o.name); });
            list.appendChild(li);
        });
    }

    // Open downward, but flip above the input if the menu would spill past the
    // scrolling modal body (an absolutely-positioned menu there gets clipped).
    function place(){
        list.classList.remove('suspected-org-list-up');
        const body = combo.closest('.modal-body');
        if(!body) return;
        const spaceBelow = body.getBoundingClientRect().bottom -
            input.getBoundingClientRect().bottom;
        if(spaceBelow < list.offsetHeight + 8){
            list.classList.add('suspected-org-list-up');
        }
    }

    function show(query){
        render(query);
        list.hidden = false;
        place();
    }

    function pick(id, name){
        hidden.value = id;
        input.value = name;
        list.hidden = true;
    }

    function move(delta){
        const opts = Array.from(list.querySelectorAll('.suspected-org-opt'));
        if(!opts.length) return;
        let idx = opts.findIndex(o => o.classList.contains('suspected-org-opt-active'));
        idx = (idx < 0) ? (delta > 0 ? 0 : opts.length - 1) : idx + delta;
        if(idx < 0) idx = opts.length - 1;
        if(idx >= opts.length) idx = 0;
        opts.forEach(o => o.classList.remove('suspected-org-opt-active'));
        opts[idx].classList.add('suspected-org-opt-active');
        opts[idx].scrollIntoView({block: 'nearest'});
    }

    input.addEventListener('focus', () => { input.select(); show(''); });
    input.addEventListener('input', () => show(input.value));
    input.addEventListener('keydown', ev => {
        if(ev.key === 'Escape'){ list.hidden = true; return; }
        if(ev.key === 'ArrowDown' || ev.key === 'ArrowUp'){
            ev.preventDefault();
            if(list.hidden){ show(input.value); return; }
            move(ev.key === 'ArrowDown' ? 1 : -1);
            return;
        }
        if(ev.key === 'Enter'){
            const active = list.querySelector('.suspected-org-opt-active') ||
                list.querySelector('.suspected-org-opt');
            if(active){ ev.preventDefault(); pick(active.dataset.orgId, active.textContent); }
        }
    });
    input.addEventListener('blur', () => {
        // Let a pending option mousedown land first, then close and restore the
        // label for the committed value (dropping any unmatched typing).
        setTimeout(() => { list.hidden = true; input.value = labelFor(hidden.value); }, 150);
    });

    combo.append(input, hidden, list);
    return combo;
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

    const sel = _orgCombo(orgId || '');

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
