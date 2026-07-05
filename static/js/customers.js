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

// The name to *show* for an org in this view: its display-name nickname, or the
// real `name` when the nickname is blank — unless the reveal key (Z) is currently
// held (showRealOrgNames), which forces the real name everywhere. Resolution/
// workflows elsewhere always use the real `name`; this is display-only.
function orgDisplayName(o){
    if(!o) return "";
    if(showRealOrgNames) return o.name;
    return (o.display_name && o.display_name.trim()) ? o.display_name : o.name;
}

// org id -> display name, for directory rows that only carry the org id.
function orgDisplayNameById(id){
    return id ? orgDisplayName(customersById[id]) : "";
}

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

// Read the (already-clamped) appearance enums off a stored org into one cfg the
// shared painter understands. Defaults mirror the store's own first-entry clamps.
function orgCfgFromObj(o){
    return {
        color: o.color,
        style: o.card_style || "outline",
        pattern: o.card_pattern || "none",
        ink: o.card_ink || "white",
        corner: o.card_corner || "none",
        cornerPos: o.card_corner_pos || "top-right",
        banner: o.card_banner || "none",
        scene: o.card_scene || "none",
    };
}

// The appearance class tokens. style/pattern/ink and the edge banners are pure
// CSS (banners ride the card's ::before/::after), so they are class names; the
// corner and bottom-scene motifs are overlay children synced separately below.
function orgAppearanceClasses(cfg){
    return "style-" + cfg.style
        + " pattern-" + cfg.pattern
        + (cfg.ink === "black" ? " ink-black" : "")
        + ((cfg.banner === "bottom" || cfg.banner === "both") ? " banner-bottom" : "")
        + ((cfg.banner === "right" || cfg.banner === "both") ? " banner-right" : "");
}

// Paint a card element from cfg: accent colour + class tokens (plus caller extras
// like "selected"/"org-preview-card"), then (re)build the corner + scene overlay
// children. Overlays are absolutely positioned under the card text (z-index rules
// in style.css), so append order relative to the content does not matter. Shared
// by the grid cards and the builder's live preview so the two never drift.
function applyOrgAppearance(card, cfg, extra){
    card.className = ("org-card " + orgAppearanceClasses(cfg) + " " + (extra || "")).trim();
    card.style.setProperty("--org-color", cfg.color);
    card.querySelectorAll(".org-corner, .org-scene").forEach(el => el.remove());
    if(cfg.corner !== "none"){
        const c = document.createElement("div");
        c.className = "org-corner corner-" + cfg.corner + " pos-" + cfg.cornerPos;
        card.appendChild(c);
    }
    if(cfg.scene !== "none"){
        const s = document.createElement("div");
        s.className = "org-scene scene-" + cfg.scene;
        card.appendChild(s);
    }
}

function createOrgCard(o, counts){
    const card = document.createElement("div");
    applyOrgAppearance(card, orgCfgFromObj(o), o.id === selectedOrgId ? "selected" : "");
    card.title = "Click to view this organization's contacts";
    card.onclick = () => selectOrg(o.id);
    makeOrgDropTarget(card, o.id);

    // The category reads as a label pinned to the card's top-right corner, tinted
    // by its own colour (dotted border + dotted fill). Appended to the card (not
    // the head) so it positions absolutely against the card box.
    if(o.category){
        const cat = document.createElement("span");
        cat.className = "org-cat";
        cat.style.setProperty("--cat-color", o.category_color || "#6366f1");
        cat.textContent = o.category;
        card.appendChild(cat);
    }

    const head = document.createElement("div");
    head.className = "org-card-head";
    const name = document.createElement("h3");
    name.className = "org-card-name";
    name.textContent = orgDisplayName(o);
    head.appendChild(name);
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

    // Notes: free-text "be mindful of this org" copy. User input, so inserted as
    // DOM text (never HTML), the people-field rule. Clamped by CSS; full text on
    // hover. Omitted entirely when blank so empty cards stay compact.
    if((o.notes || "").trim()){
        const notes = document.createElement("div");
        notes.className = "org-notes";
        notes.textContent = o.notes;
        notes.title = o.notes;
        card.appendChild(notes);
    }

    // Read-only Key Vault status (fixed, not editable). Driven by the non-secret
    // vault index merged into the org payload — never the secrets themselves. Sits
    // in the notes area as a "be mindful: this org has stored keys" line. The same
    // box is mirrored, read-only, into the org editor (buildVaultInfoBox).
    const vaultBox = buildVaultInfoBox(o.vault);
    if(vaultBox) card.appendChild(vaultBox);

    // Read-only recorded key-assignment habits (Unlock Station "Record Customer
    // Key Assignment"). Fixed text, never editable.
    const kaBox = buildKeyAssignmentBox(o.key_assignments);
    if(kaBox) card.appendChild(kaBox);

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

// One read-only Key Vault status line (fixed text, never user input).
function makeVaultLine(text){
    const line = document.createElement("div");
    line.className = "org-vault-line";
    line.textContent = text;
    return line;
}

// The read-only Key Vault status box from the non-secret org.vault index, or null
// when the org has no stored keys. Shared by the org card and the org editor.
function buildVaultInfoBox(vault){
    vault = vault || {};
    if(!vault.count) return null;
    const box = document.createElement("div");
    box.className = "org-vault-info";
    box.title = "Manage these in Workshop → Key Vaults";
    const lead = document.createElement("span");
    lead.className = "org-vault-lead";
    lead.textContent = "🔑 Key Vault";
    box.appendChild(lead);
    if(vault.has_managed){
        box.appendChild(makeVaultLine("Has customer-managed keys"));
    }
    if(vault.has_temporary){
        box.appendChild(makeVaultLine(vault.last_scan_dt
            ? `Has temporary keys from ${vault.last_scan_dt} scan`
            : "Has temporary keys"));
    }
    return box;
}

// The read-only recorded key-assignment patterns box (or null when none). Each
// pattern says which kind of Key Vault key the Unlock Station used to unlock a
// given file kind for this org, so "Smart Key Assignment and Unlock" can replay it.
const KA_FILE_LABELS = {zip: "Zip files", excel: "Excel files"};
const KA_SELECTOR_LABELS = {managed: "managed key", recent_temporary: "most recent temporary key"};
function buildKeyAssignmentBox(assignments){
    assignments = assignments || [];
    if(!assignments.length) return null;
    const box = document.createElement("div");
    box.className = "org-vault-info org-keyassign-info";
    box.title = "Recorded by the Unlock Station; replayed by Smart Key Assignment";
    const lead = document.createElement("span");
    lead.className = "org-vault-lead";
    lead.textContent = "🗝 Key assignment habits";
    box.appendChild(lead);
    assignments.forEach(k => {
        const file = KA_FILE_LABELS[k.file_kind] || k.file_kind;
        const sel = KA_SELECTOR_LABELS[k.selector] || k.selector;
        box.appendChild(makeVaultLine(`${file}: ${sel}`));
    });
    return box;
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

// ----- "hold to reveal real names" -----

// While the reveal key (Z) is held, every org name in the view shows the real
// `name` instead of the display nickname; releasing restores. Idempotent so a
// stray keyup/blur can't desync.
function revealRealOrgNames(on){
    on = !!on;
    if(on === showRealOrgNames) return;
    showRealOrgNames = on;
    renderOrganizations();
    renderDirectory();
}

// Keyboard hold: hold Z to reveal — only while the Customer Management view is
// showing and the user isn't typing in a field (so it never eats a literal "z"
// in the org-name or directory-search box). Wired once from init().
function initOrgNameReveal(){
    const inField = (el) => el && (el.tagName === "INPUT" || el.tagName === "TEXTAREA"
        || el.isContentEditable);
    const customersActive = () => {
        const view = document.getElementById("view-customers");
        return view && !view.classList.contains("view-hidden");
    };
    document.addEventListener("keydown", (e) => {
        if(e.code !== "KeyZ" || e.repeat || e.ctrlKey || e.metaKey || e.altKey) return;
        if(!customersActive() || inField(e.target)) return;
        e.preventDefault();
        revealRealOrgNames(true);
    });
    document.addEventListener("keyup", (e) => {
        if(e.code === "KeyZ") revealRealOrgNames(false);
    });
    // If focus leaves the window mid-hold the keyup may never arrive.
    window.addEventListener("blur", () => revealRealOrgNames(false));
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
        org ? `${orgDisplayName(org)} — assigned contacts` : "Unassigned contacts";
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
        const base = orgDisplayNameById(c.member_org_id) || c.member_org_name || "no base org";
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
    document.getElementById("orgDisplayName").value = o ? (o.display_name || "") : "";
    document.getElementById("orgColor").value = o ? o.color : "#3b82f6";
    document.getElementById("orgCategory").value = o ? (o.category || "") : "";
    document.getElementById("orgCategoryColor").value = o ? (o.category_color || "#6366f1") : "#6366f1";
    document.getElementById("orgCardStyle").value = o ? (o.card_style || "outline") : "outline";
    document.getElementById("orgCardPattern").value = o ? (o.card_pattern || "none") : "none";
    setOrgInkToggle(o ? (o.card_ink || "white") : "white");
    document.getElementById("orgCardCorner").value = o ? (o.card_corner || "none") : "none";
    setOrgCornerPosToggle(o ? (o.card_corner_pos || "top-right") : "top-right");
    document.getElementById("orgCardBanner").value = o ? (o.card_banner || "none") : "none";
    document.getElementById("orgCardScene").value = o ? (o.card_scene || "none") : "none";
    document.getElementById("orgNotes").value = o ? (o.notes || "") : "";

    // Read-only Key Vault status mirrored from the card (shown only when the org
    // has stored keys); never editable, never the secrets themselves.
    const vaultWrap = document.getElementById("orgVaultInfo");
    const vaultBody = document.getElementById("orgVaultInfoBody");
    vaultBody.innerHTML = "";
    const vaultBox = o ? buildVaultInfoBox(o.vault) : null;
    const kaBox = o ? buildKeyAssignmentBox(o.key_assignments) : null;
    if(vaultBox) vaultBody.appendChild(vaultBox);
    if(kaBox) vaultBody.appendChild(kaBox);
    vaultWrap.hidden = !(vaultBox || kaBox);

    updateOrgPreview();
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

// ----- colour helpers (builder) -----

// Hex <-> HSL so we can derive a related accent and generate pleasant randoms.
function hexToHsl(hex){
    const m = /^#?([0-9a-f]{6})$/i.exec(hex || "");
    const n = m ? parseInt(m[1], 16) : 0x3b82f6;
    let r = ((n >> 16) & 255) / 255, g = ((n >> 8) & 255) / 255, b = (n & 255) / 255;
    const max = Math.max(r, g, b), min = Math.min(r, g, b), d = max - min;
    let h = 0;
    if(d){
        if(max === r) h = ((g - b) / d) % 6;
        else if(max === g) h = (b - r) / d + 2;
        else h = (r - g) / d + 4;
        h *= 60;
        if(h < 0) h += 360;
    }
    const l = (max + min) / 2;
    const s = d ? d / (1 - Math.abs(2 * l - 1)) : 0;
    return {h, s, l};
}

function hslToHex(h, s, l){
    h = ((h % 360) + 360) % 360;
    s = Math.min(1, Math.max(0, s));
    l = Math.min(1, Math.max(0, l));
    const c = (1 - Math.abs(2 * l - 1)) * s, x = c * (1 - Math.abs((h / 60) % 2 - 1)), m = l - c / 2;
    let r = 0, g = 0, b = 0;
    if(h < 60){ r = c; g = x; }
    else if(h < 120){ r = x; g = c; }
    else if(h < 180){ g = c; b = x; }
    else if(h < 240){ g = x; b = c; }
    else if(h < 300){ r = x; b = c; }
    else { r = c; b = x; }
    const hh = (v) => Math.round((v + m) * 255).toString(16).padStart(2, "0");
    return "#" + hh(r) + hh(g) + hh(b);
}

// The badge accent derived from the card colour: a close, distinct variant —
// hue nudged ~25 deg and lightness/saturation pulled toward a legible mid-tone.
function deriveCategoryColor(hex){
    const {h, s, l} = hexToHsl(hex);
    return hslToHex(h + 25, Math.max(0.45, Math.min(0.9, s)), Math.min(0.62, Math.max(0.45, l)));
}

// A random but pleasant card colour (full hue range, mid saturation/lightness so
// it is never near-white or near-black) plus its derived badge accent. Vary the
// hue from the current one so a repeat click visibly changes it.
function randomizeOrgColor(){
    const prev = hexToHsl(document.getElementById("orgColor").value);
    const h = (prev.h + 60 + Math.floor(Math.random() * 240)) % 360;
    const color = hslToHex(h, 0.55 + Math.random() * 0.25, 0.45 + Math.random() * 0.12);
    document.getElementById("orgColor").value = color;
    onOrgColorChanged();
}

// Card colour changed (picker or randomize): re-derive the badge accent and
// refresh the live preview. The user can still edit the badge colour afterward.
function onOrgColorChanged(){
    document.getElementById("orgCategoryColor").value =
        deriveCategoryColor(document.getElementById("orgColor").value);
    updateOrgPreview();
}

// The appearance cfg read off the builder form controls (the toggle buttons keep
// their value in a data-attribute; the rest are plain <select>s). Mirror shape of
// orgCfgFromObj so the same painter drives both the preview and the grid cards.
function orgCfgFromForm(){
    return {
        color: document.getElementById("orgColor").value,
        style: document.getElementById("orgCardStyle").value,
        pattern: document.getElementById("orgCardPattern").value,
        ink: document.getElementById("orgCardInk").dataset.value,
        corner: document.getElementById("orgCardCorner").value,
        cornerPos: document.getElementById("orgCardCornerPos").dataset.value,
        banner: document.getElementById("orgCardBanner").value,
        scene: document.getElementById("orgCardScene").value,
    };
}

// The two flip toggles keep their state in data-value and show a labelled glyph.
function setOrgInkToggle(v){
    const btn = document.getElementById("orgCardInk");
    btn.dataset.value = v;
    btn.textContent = v === "black" ? "⬛ Black ink" : "⬜ White ink";
}
function toggleOrgInk(){
    const btn = document.getElementById("orgCardInk");
    setOrgInkToggle(btn.dataset.value === "black" ? "white" : "black");
    updateOrgPreview();
}
function setOrgCornerPosToggle(v){
    const btn = document.getElementById("orgCardCornerPos");
    btn.dataset.value = v;
    btn.textContent = v === "bottom-right" ? "↘ Bottom-right" : "↗ Top-right";
}
function toggleOrgCornerPos(){
    const btn = document.getElementById("orgCardCornerPos");
    setOrgCornerPosToggle(btn.dataset.value === "bottom-right" ? "top-right" : "bottom-right");
    updateOrgPreview();
}

// Paint the modal's mini preview card from the current control values so the
// style/pattern/colour choices are visible before saving.
function updateOrgPreview(){
    const card = document.getElementById("orgPreview");
    if(!card) return;
    applyOrgAppearance(card, orgCfgFromForm(), "org-preview-card");
    const name = document.getElementById("orgName").value.trim()
        || document.getElementById("orgDisplayName").value.trim() || "Preview";
    card.querySelector(".org-card-name").textContent = name;
    const cat = card.querySelector(".org-cat");
    const catText = document.getElementById("orgCategory").value.trim();
    cat.textContent = catText;
    cat.hidden = !catText;
    cat.style.setProperty("--cat-color", document.getElementById("orgCategoryColor").value);
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
        display_name: document.getElementById("orgDisplayName").value.trim(),
        color: document.getElementById("orgColor").value,
        category: document.getElementById("orgCategory").value.trim(),
        category_color: document.getElementById("orgCategoryColor").value,
        card_style: document.getElementById("orgCardStyle").value,
        card_pattern: document.getElementById("orgCardPattern").value,
        card_ink: document.getElementById("orgCardInk").dataset.value,
        card_corner: document.getElementById("orgCardCorner").value,
        card_corner_pos: document.getElementById("orgCardCornerPos").dataset.value,
        card_banner: document.getElementById("orgCardBanner").value,
        card_scene: document.getElementById("orgCardScene").value,
        notes: document.getElementById("orgNotes").value.trim(),
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
