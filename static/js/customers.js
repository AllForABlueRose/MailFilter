// Customer Management view: organization cards (top ~70%) over a contact
// directory (bottom 30%) derived from all cached mail. Organizations group
// people so replies can later be templated by formality. Relies on globals from
// state.js (customersById, editingOrgId, contactDirectory, selectedOrgId).
//
// Interaction model:
//   - Clicking an org card *selects* it; the directory below then shows that
//     org's assigned contacts. Clicking it again (or "← Unassigned") returns to
//     the default unassigned-contacts view. A pencil button on the card edits it.
//   - Each contact exposes three draggable chips — name, email, domain. Dropping
//     a name or email on an org pins that person as a Representative; dropping a
//     domain maps the whole domain to the org as Member (everyone on it).
//
// Every value originating from mail content or user input (email, name, domain,
// category, org name) is inserted via textContent / createTextNode, never
// innerHTML — the people-field escaping rule (presenter.py, design §5).

const ROLE_LABELS = { member: "Member", representative: "Representative" };

// Drag payloads. A name/email chip carries the contact's email under MIME_REP
// (drop => pin as Representative); a domain chip carries the domain under
// MIME_DOMAIN (drop => map domain as Member). Distinct types let the org card's
// drop handler tell the two intents apart.
const MIME_REP = "text/x-mailfilter-cust-rep";
const MIME_DOMAIN = "text/x-mailfilter-cust-domain";

// ----- loading + rendering -----

async function loadCustomers(){
    try{
        const [orgsRes, contactsRes] = await Promise.all([
            fetch("/api/organizations"),
            fetch("/api/contacts"),
        ]);
        const orgs = (await orgsRes.json()).organizations || [];
        contactDirectory = (await contactsRes.json()).contacts || [];
        customersById = {};
        orgs.forEach(o => { customersById[o.id] = o; });
        // Drop a stale selection if its org was deleted.
        if(selectedOrgId && !customersById[selectedOrgId]) selectedOrgId = null;
        renderOrganizations();
        renderDirectory();
    }catch(e){
        // Leave whatever is on screen; the tab can be re-entered to retry.
    }
}

function orgsInOrder(){
    return Object.values(customersById).sort((a, b) =>
        (a.created || "").localeCompare(b.created || ""));
}

// org id -> {member: n, representative: n} from the resolved directory. A contact
// counts as a member of its base org and a representative of any org it fronts —
// the two axes are independent, so one person can add to both.
function orgRoleCounts(){
    const counts = {};
    const bucket = (id) => counts[id] || (counts[id] = {member: 0, representative: 0});
    contactDirectory.forEach(c => {
        if(c.member_org_id) bucket(c.member_org_id).member += 1;
        if(c.rep_org_id) bucket(c.rep_org_id).representative += 1;
    });
    return counts;
}

function renderOrganizations(){
    const grid = document.getElementById("orgGrid");
    grid.innerHTML = "";
    const list = orgsInOrder();
    if(!list.length){
        const empty = document.createElement("p");
        empty.className = "auto-empty";
        empty.textContent = "No organizations yet. Create one, then drag contacts or a domain onto it.";
        grid.appendChild(empty);
        return;
    }
    const counts = orgRoleCounts();
    list.forEach(o => grid.appendChild(createOrgCard(o, counts[o.id])));
}

function createOrgCard(o, counts){
    const card = document.createElement("div");
    card.className = "org-card" + (o.id === selectedOrgId ? " selected" : "");
    card.style.setProperty("--org-color", o.color);
    card.title = "Click to view this organization's contacts";
    card.onclick = () => selectOrg(o.id);
    makeOrgDropTarget(card, o.id);

    const head = document.createElement("div");
    head.className = "org-card-head";
    const name = document.createElement("h3");
    name.className = "org-card-name";
    name.textContent = o.name;
    head.appendChild(name);
    if(o.category){
        const cat = document.createElement("span");
        cat.className = "org-cat";
        cat.textContent = o.category;
        head.appendChild(cat);
    }
    card.appendChild(head);

    const chips = document.createElement("div");
    chips.className = "org-chips";
    (o.domains || []).forEach(d => {
        const chip = document.createElement("span");
        chip.className = "domain-chip role-" + d.role;
        chip.textContent = d.domain;
        chip.title = ROLE_LABELS[d.role] || d.role;
        chips.appendChild(chip);
    });
    if(!(o.domains || []).length){
        const none = document.createElement("span");
        none.className = "org-nodomains";
        none.textContent = "no domains yet";
        chips.appendChild(none);
    }
    card.appendChild(chips);

    const c = counts || {member: 0, representative: 0};
    const overrides = (o.contacts || []).length;
    const foot = document.createElement("div");
    foot.className = "org-card-foot";
    foot.textContent = `${c.member} member${c.member === 1 ? "" : "s"} · `
        + `${c.representative} rep${c.representative === 1 ? "" : "s"}`
        + (overrides ? ` · ${overrides} pinned` : "");
    card.appendChild(foot);

    const actions = document.createElement("div");
    actions.className = "org-card-actions";
    const edit = document.createElement("button");
    edit.className = "org-edit";
    edit.textContent = "✎";
    edit.title = "Edit organization";
    edit.onclick = (e) => { e.stopPropagation(); openOrgBuilder(o.id); };
    actions.appendChild(edit);
    card.appendChild(actions);
    return card;
}

// Make an org card accept dropped contact/domain chips.
function makeOrgDropTarget(card, orgId){
    card.addEventListener("dragover", (e) => {
        if(e.dataTransfer.types.includes(MIME_REP) || e.dataTransfer.types.includes(MIME_DOMAIN)){
            e.preventDefault();
            e.dataTransfer.dropEffect = "copy";
            card.classList.add("drag-over");
        }
    });
    card.addEventListener("dragleave", () => card.classList.remove("drag-over"));
    card.addEventListener("drop", (e) => {
        e.preventDefault();
        card.classList.remove("drag-over");
        const email = e.dataTransfer.getData(MIME_REP);
        const domain = e.dataTransfer.getData(MIME_DOMAIN);
        if(email) assignRepresentative(email, orgId);
        else if(domain) assignDomainMembers(domain, orgId);
    });
}

// ----- selection -----

function selectOrg(id){
    selectedOrgId = (selectedOrgId === id) ? null : id;   // click again to deselect
    renderOrganizations();
    renderDirectory();
}

function clearOrgSelection(){
    selectedOrgId = null;
    renderOrganizations();
    renderDirectory();
}

// ----- contact directory -----

function scopedContacts(){
    // Default: unassigned contacts (no base, not representing anyone). With an org
    // selected: its members plus the representatives who front for it.
    return selectedOrgId
        ? contactDirectory.filter(c => c.member_org_id === selectedOrgId || c.rep_org_id === selectedOrgId)
        : contactDirectory.filter(c => !c.member_org_id && !c.rep_org_id);
}

// Is this contact a representative *in the currently selected org*?
function isRepInScope(c){
    return !!selectedOrgId && c.rep_org_id === selectedOrgId;
}

function renderDirectory(){
    const tbody = document.getElementById("directoryRows");
    tbody.innerHTML = "";

    const org = selectedOrgId ? customersById[selectedOrgId] : null;
    document.getElementById("directoryScope").textContent =
        org ? `${org.name} — assigned contacts` : "Unassigned contacts";
    document.getElementById("directoryBackBtn").hidden = !org;
    // The role sort only matters when an org (with both members and reps) is shown.
    document.getElementById("roleSortBtn").hidden = !org;

    const term = (document.getElementById("directorySearch").value || "").trim().toLowerCase();
    let rows = scopedContacts().filter(c => !term
        || c.email.includes(term)
        || (c.name || "").toLowerCase().includes(term)
        || (c.domain || "").includes(term));

    if(org){
        // Stable sort: reps group vs members group; order within a group is kept
        // (the directory already arrives sorted by mail count, then email).
        const repRank = roleSortRepsOnTop ? 0 : 1;
        rows = rows.slice().sort((a, b) =>
            (isRepInScope(a) ? repRank : 1 - repRank) - (isRepInScope(b) ? repRank : 1 - repRank));
    }

    document.getElementById("directoryCount").textContent = `(${rows.length})`;
    if(!rows.length){
        const tr = document.createElement("tr");
        const td = document.createElement("td");
        td.colSpan = 7;
        td.className = "directory-empty";
        td.textContent = emptyDirectoryMessage(org, term);
        tr.appendChild(td);
        tbody.appendChild(tr);
        return;
    }
    rows.forEach(c => tbody.appendChild(createContactRow(c)));
}

function toggleRoleSort(){
    roleSortRepsOnTop = !roleSortRepsOnTop;
    updateRoleSortButton();
    renderDirectory();
}

function updateRoleSortButton(){
    document.getElementById("roleSortBtn").textContent =
        roleSortRepsOnTop ? "Reps first" : "Members first";
}

function emptyDirectoryMessage(org, term){
    if(term) return "No contacts match your search.";
    if(org) return "No contacts assigned to this organization yet — drag some here.";
    if(!contactDirectory.length) return "No contacts yet — refresh mail to populate the directory.";
    return "No unassigned contacts — everyone is grouped into an organization.";
}

function createContactRow(c){
    const tr = document.createElement("tr");
    const isRep = isRepInScope(c);
    if(isRep) tr.className = "rep-row";   // grey bounding box marks a representative

    const name = document.createElement("td");
    if(c.name){
        name.appendChild(makeChip(c.name, "chip-name", MIME_REP, c.email,
            "Drag onto an organization to pin as Representative"));
    } else {
        name.appendChild(document.createTextNode("—"));
    }
    tr.appendChild(name);

    const email = document.createElement("td");
    email.appendChild(makeChip(c.email, "chip-email", MIME_REP, c.email,
        "Drag onto an organization to pin as Representative"));
    tr.appendChild(email);

    const domain = document.createElement("td");
    domain.appendChild(makeChip(c.domain, "chip-domain", MIME_DOMAIN, c.domain,
        "Drag onto an organization to make everyone on this domain a Member"));
    tr.appendChild(domain);

    tr.appendChild(roleCell(c, isRep));

    const count = document.createElement("td");
    count.className = "contact-count";
    count.textContent = c.count;
    tr.appendChild(count);

    const last = document.createElement("td");
    last.className = "contact-last";
    last.textContent = c.last_received;
    tr.appendChild(last);

    const actions = document.createElement("td");
    // Only an explicitly pinned representative (not a domain-derived one) can be
    // unpinned per-contact, and only from the org it represents.
    if(c.rep_pinned && isRep){
        const unpin = document.createElement("button");
        unpin.className = "contact-unpin";
        unpin.textContent = "✕";
        unpin.title = "Remove this representative pin";
        unpin.onclick = () => unpinContact(c.email);
        actions.appendChild(unpin);
    }
    tr.appendChild(actions);
    return tr;
}

// The role classifier, relative to the org being viewed: Representative (with the
// org they're actually a member of), Member, or — when no org is selected — the
// contact's overall standing.
function roleCell(c, isRep){
    const td = document.createElement("td");
    if(isRep){
        td.appendChild(roleBadge("Representative", "role-rep"));
        const base = c.member_org_name || "no base org";
        const sub = document.createElement("span");
        sub.className = "role-subtle";
        sub.textContent = "member of " + base;
        td.appendChild(sub);
    } else if(selectedOrgId && c.member_org_id === selectedOrgId){
        td.appendChild(roleBadge("Member", "role-member"));
    } else if(!selectedOrgId){
        td.appendChild(document.createTextNode("—"));
    } else {
        td.appendChild(roleBadge("Member", "role-member"));
    }
    return td;
}

function roleBadge(text, cls){
    const span = document.createElement("span");
    span.className = "role-badge " + cls;
    span.textContent = text;
    return span;
}

function makeChip(text, cls, mime, value, title){
    const chip = document.createElement("span");
    chip.className = "contact-chip " + cls;
    chip.textContent = text;
    chip.title = title;
    chip.draggable = true;
    chip.addEventListener("dragstart", (e) => {
        e.dataTransfer.setData(mime, value);
        e.dataTransfer.setData("text/plain", value);
        e.dataTransfer.effectAllowed = "copy";
    });
    return chip;
}

// ----- assignment actions (drag-drop + unpin) -----

async function assignRepresentative(email, orgId){
    // A representative must have a base organization first (who they work for).
    const c = contactDirectory.find(x => x.email === email);
    if(c && c.member_org_id === orgId){
        alert("This contact is already a Member of this organization.");
        return;
    }
    if(!c || !c.member_org_id){
        alert("Set this contact's base organization first: drag their domain onto "
            + "the organization they are a Member of, then assign them as a Representative.");
        return;
    }
    try{
        const res = await fetch("/api/contacts/assign", {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({email, org_id: orgId, role: "representative"}),
        });
        if(res.status === 409){
            alert("Set this contact's base organization (a Member) before assigning them as a Representative.");
            return;
        }
    }catch(e){}
    loadCustomers();
}

async function assignDomainMembers(domain, orgId){
    try{
        await fetch(`/api/organizations/${orgId}/domains`, {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({domain, role: "member"}),
        });
    }catch(e){}
    loadCustomers();
}

async function unpinContact(email){
    try{
        await fetch("/api/contacts/unassign", {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({email}),
        });
    }catch(e){}
    loadCustomers();
}

// ----- organization builder modal -----

function openOrgBuilder(id){
    editingOrgId = id || null;
    const o = id ? customersById[id] : null;

    document.getElementById("orgModalTitle").textContent = o ? "Edit Organization" : "New Organization";
    document.getElementById("orgName").value = o ? o.name : "";
    document.getElementById("orgColor").value = o ? o.color : "#3b82f6";
    document.getElementById("orgCategory").value = o ? (o.category || "") : "";
    document.getElementById("orgMemberDomains").value = domainsForRole(o, "member");
    document.getElementById("orgRepDomains").value = domainsForRole(o, "representative");

    document.getElementById("orgDeleteBtn").hidden = !o;
    document.getElementById("organizationModal").hidden = false;
}

function domainsForRole(o, role){
    if(!o) return "";
    return (o.domains || []).filter(d => d.role === role).map(d => d.domain).join("\n");
}

function closeOrgBuilder(){
    document.getElementById("organizationModal").hidden = true;
    editingOrgId = null;
}

// Parse a textarea (one domain per line) into [{domain, role}, ...].
function parseDomains(textareaId, role){
    return (document.getElementById(textareaId).value || "")
        .split("\n")
        .map(s => s.trim().toLowerCase())
        .filter(Boolean)
        .map(domain => ({domain, role}));
}

async function saveOrganization(){
    const name = document.getElementById("orgName").value.trim();
    if(!name){ alert("Give the organization a name."); return; }

    const payload = {
        name,
        color: document.getElementById("orgColor").value,
        category: document.getElementById("orgCategory").value.trim(),
        domains: parseDomains("orgMemberDomains", "member")
            .concat(parseDomains("orgRepDomains", "representative")),
    };

    const editing = editingOrgId;
    const url = editing ? `/api/organizations/${editing}` : "/api/organizations";
    const res = await fetch(url, {
        method: editing ? "PUT" : "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify(payload),
    });
    if(!res.ok){ alert("Could not save the organization."); return; }
    closeOrgBuilder();
    loadCustomers();
}

async function deleteOrganizationFromBuilder(){
    if(!editingOrgId) return;
    const o = customersById[editingOrgId];
    if(!confirm(`Delete the organization "${o ? o.name : ""}"? Its contact pins are removed too.`)) return;
    await fetch(`/api/organizations/${editingOrgId}`, {method: "DELETE"});
    closeOrgBuilder();
    loadCustomers();
}
