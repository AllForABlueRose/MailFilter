// Workshop view -> Key Vaults subsection. Talks to /api/vault/*. The vault holds
// per-organization credentials sealed under a master passphrase (AES-256-GCM);
// this UI is the unlock gate plus the consolidated, org-grouped key list.
//
// Security posture mirrored on the client: secrets are NEVER rendered in the list
// (the server redacts them); a single secret is fetched only on an explicit
// "Reveal" and is dropped from the DOM again on Hide / re-render. Every secret /
// label / name from the server or the user is inserted as DOM text, never HTML.
//
// Relies on globals from state.js (vaultStatus, vaultEntries, vaultOrgNames,
// editingVaultEntryId).

function el(tag, className, text){
    const node = document.createElement(tag);
    if(className) node.className = className;
    if(text != null) node.textContent = text;
    return node;
}

async function vaultApi(path, method, body){
    try{
        const res = await fetch(path, {
            method: method || "GET",
            headers: body ? {"Content-Type": "application/json"} : undefined,
            body: body ? JSON.stringify(body) : undefined,
        });
        let json = null;
        try{ json = await res.json(); }catch(e){}
        return {ok: res.ok, status: res.status, json};
    }catch(e){
        return {ok: false, status: 0, json: null};
    }
}

// ----- load + render the panel -----

async function loadVault(){
    const res = await vaultApi("/api/vault/status");
    vaultStatus = res.json || {available: false, initialized: false, unlocked: false};
    // Org names back the list grouping and the entry-editor picker.
    const orgs = await vaultApi("/api/organizations");
    vaultOrgNames = {};
    (orgs.json && orgs.json.organizations || []).forEach(o => {
        vaultOrgNames[o.id] = (o.display_name && o.display_name.trim()) ? o.display_name : o.name;
    });
    if(vaultStatus.unlocked){
        await loadVaultEntries();
    } else {
        // Locked: drop every cached secret and any reveal/pin state.
        vaultEntries = {}; vaultSecrets = {}; vaultPinned = {}; vaultSearch = "";
        renderVaultPanel();
    }
}

function renderVaultPanel(){
    const panel = document.getElementById("vaultPanel");
    if(!panel) return;
    panel.innerHTML = "";
    const s = vaultStatus || {};
    if(!s.available){
        panel.appendChild(el("p", "vault-note",
            "The Key Vault needs the cryptography package, which isn't installed on this machine. "
            + "Install it (see requirements.txt) to enable encrypted key storage."));
        return;
    }
    if(!s.initialized){ panel.appendChild(buildCreateForm(() => loadMail())); return; }
    if(!s.unlocked){ panel.appendChild(buildUnlockForm(() => loadMail())); return; }
    panel.appendChild(buildUnlockedView());
}

// `onDone` runs after a successful create/unlock (e.g. hide the login overlay and
// refresh mail badges); the forms keep no element ids so they can be mounted in
// both the Workshop panel and the startup login overlay at once.
function buildCreateForm(onDone){
    const wrap = el("div", "vault-gate");
    wrap.appendChild(el("p", "vault-note",
        "Create your Key Vault. The master passphrase encrypts every key and is "
        + "never stored — if you lose it, the vault cannot be recovered."));
    const pass = el("input", "vault-input");
    pass.type = "password"; pass.placeholder = "Master passphrase (min 8 chars)";
    pass.autocomplete = "new-password";
    const confirm = el("input", "vault-input");
    confirm.type = "password"; confirm.placeholder = "Confirm passphrase";
    confirm.autocomplete = "new-password";
    const err = el("div", "bulk-tmpl-error"); err.hidden = true;
    const btn = el("button", "auto-save-btn", "Create vault");
    btn.type = "button"; btn.onclick = () => createVault(pass.value, confirm.value, err, onDone);
    wrap.append(pass, confirm, btn, err);
    return wrap;
}

function buildUnlockForm(onDone){
    const wrap = el("div", "vault-gate");
    wrap.appendChild(el("p", "vault-note", "The vault is locked. Enter your master passphrase to unlock it."));
    const pass = el("input", "vault-input");
    pass.type = "password"; pass.placeholder = "Master passphrase";
    pass.autocomplete = "current-password";
    const err = el("div", "bulk-tmpl-error"); err.hidden = true;
    pass.addEventListener("keydown", e => { if(e.key === "Enter") unlockVault(pass.value, err, onDone); });
    const btn = el("button", "auto-save-btn", "Unlock");
    btn.type = "button"; btn.onclick = () => unlockVault(pass.value, err, onDone);
    wrap.append(pass, btn);
    if(vaultStatus && vaultStatus.remembered && vaultStatus.dpapi_available){
        const dpapi = el("button", "flip-btn vault-dpapi-btn", "Unlock on this machine");
        dpapi.type = "button"; dpapi.title = "Use the key remembered on this machine (no passphrase)";
        dpapi.onclick = () => unlockVaultDpapi(err, onDone);
        wrap.appendChild(dpapi);
    }
    wrap.appendChild(err);
    return wrap;
}

function buildUnlockedView(){
    const wrap = el("div", "vault-unlocked");

    const bar = el("div", "vault-toolbar");
    const add = el("button", "auto-new-btn", "+ Add key");
    add.type = "button"; add.onclick = () => openVaultEntry(null, null);
    bar.appendChild(add);

    // Hold-Z over this area reveals every key's value at once.
    const revealAll = el("div", "vault-reveal-all", "👁 hold Z to reveal all");
    revealAll.title = "Hold Z and hover here to reveal every key's value";
    revealAll.addEventListener("mouseenter", () => {
        vaultRevealAll = true;
        if(vaultZHeld) ensureAllSecrets();
        updateAllSecretCells();
    });
    revealAll.addEventListener("mouseleave", () => { vaultRevealAll = false; updateAllSecretCells(); });
    bar.appendChild(revealAll);

    const search = el("input", "vault-search");
    search.type = "search"; search.placeholder = "Search value, organization, date…";
    search.value = vaultSearch;
    search.addEventListener("input", () => onVaultSearch(search.value));
    bar.appendChild(search);

    const spacer = el("span", "vault-toolbar-spacer"); bar.appendChild(spacer);
    if(vaultStatus.dpapi_available){
        const label = el("label", "vault-remember");
        const cb = el("input"); cb.type = "checkbox"; cb.checked = !!vaultStatus.remembered;
        cb.onchange = () => setRemember(cb.checked);
        label.append(cb, document.createTextNode(" Remember on this machine"));
        bar.appendChild(label);
    }
    const lock = el("button", "flip-btn", "🔒 Lock");
    lock.type = "button"; lock.onclick = lockVault;
    bar.appendChild(lock);
    wrap.appendChild(bar);

    wrap.appendChild(el("p", "vault-hint",
        "Hold Z and hover a key to reveal it; tick its box to keep it visible."));

    // A stable container so a search re-render doesn't rebuild (and unfocus) the bar.
    const groups = el("div", "vault-groups"); groups.id = "vaultGroups";
    wrap.appendChild(groups);
    // Defer fill until the container is in the DOM (renderVaultGroups reads by id).
    setTimeout(renderVaultGroups, 0);
    return wrap;
}

function renderVaultGroups(){
    const groups = document.getElementById("vaultGroups");
    if(!groups) return;
    groups.innerHTML = "";
    const orgIds = Object.keys(vaultEntries).filter(id => (vaultEntries[id] || []).length);
    if(!orgIds.length){
        groups.appendChild(el("p", "auto-empty", vaultSearch
            ? "No keys match your search."
            : "No keys yet. Add one, or run a Smart Password Detection scan to capture them."));
        return;
    }
    // Group order follows org creation order (vaultOrgNames insertion order).
    const ordered = Object.keys(vaultOrgNames).filter(id => orgIds.includes(id))
        .concat(orgIds.filter(id => !(id in vaultOrgNames)));
    ordered.forEach(orgId => groups.appendChild(buildOrgGroup(orgId)));
}

function buildOrgGroup(orgId){
    const group = el("div", "vault-org");
    // "unassigned" is the holding bucket for captures whose sender resolves to no
    // org (kept in sync with config.VAULT_UNASSIGNED_ORG_ID).
    const name = orgId === "unassigned" ? "Unassigned (sender not yet mapped to an org)"
        : (vaultOrgNames[orgId] || "(unknown organization)");
    group.appendChild(el("h4", "vault-org-name", name));
    const table = el("table", "directory-table vault-table");
    const tbody = el("tbody");
    (vaultEntries[orgId] || []).forEach(entry => tbody.appendChild(buildEntryRow(entry)));
    table.appendChild(tbody);
    group.appendChild(table);
    return group;
}

function buildEntryRow(entry){
    const tr = el("tr", "vault-row");
    // Hold-Z while hovering a row reveals just that key.
    tr.addEventListener("mouseenter", () => {
        vaultHoverId = entry.id;
        if(vaultZHeld) updateSecretCell(entry.id);
    });
    tr.addEventListener("mouseleave", () => {
        if(vaultHoverId === entry.id) vaultHoverId = null;
        if(vaultZHeld) updateSecretCell(entry.id);
    });

    const label = el("td", "vault-cell-label");
    label.appendChild(el("span", "vault-label-text", entry.label || "Key"));
    if(entry.kind === "temporary"){
        const tag = el("span", "vault-kind vault-kind-temp",
            entry.scan_dt ? "temporary · " + entry.scan_dt : "temporary");
        tag.title = "Captured from a Smart Password Detection scan";
        label.appendChild(tag);
    } else {
        label.appendChild(el("span", "vault-kind vault-kind-managed", "managed"));
    }
    if(entry.url){
        const url = el("div", "vault-url", entry.url);
        label.appendChild(url);
    }
    tr.appendChild(label);

    const user = el("td", "vault-cell-user", entry.username || "—");
    tr.appendChild(user);

    // Secret cell: masked by default; the value is revealed only while held with Z
    // (or pinned). Its id lets the reveal handlers refresh just this cell.
    const secretCell = el("td", "vault-cell-secret");
    secretCell.id = "vaultSecret-" + entry.id;
    renderSecretCell(secretCell, entry);
    tr.appendChild(secretCell);

    const actions = el("td", "vault-cell-actions");
    const edit = el("button", "org-edit", "✎");
    edit.type = "button"; edit.title = "Edit key";
    edit.onclick = () => openVaultEntry(entry.id, entry.org_id);
    actions.appendChild(edit);
    tr.appendChild(actions);
    return tr;
}

// ----- hold-Z reveal -----

// Is this key's value currently shown? Pinned keys stay shown; otherwise the value
// shows only while Z is held and the row (or the reveal-all area) is hovered.
function secretShown(id){
    return !!vaultPinned[id] || (vaultZHeld && (vaultRevealAll || vaultHoverId === id));
}

function findVaultEntry(id){
    for(const list of Object.values(vaultEntries)){
        const found = (list || []).find(e => e.id === id);
        if(found) return found;
    }
    return null;
}

function renderSecretCell(cell, entry){
    cell.innerHTML = "";
    if(!entry.has_secret){ cell.appendChild(el("span", "vault-nosecret", "—")); return; }
    const id = entry.id;
    if(!secretShown(id)){
        cell.appendChild(el("span", "vault-secret-mask", "••••••••"));
        return;
    }
    const secret = vaultSecrets[id];
    if(secret === undefined){
        cell.appendChild(el("span", "vault-secret-loading", "…"));
        ensureSecret(id);
    } else {
        cell.appendChild(el("code", "vault-secret-value", secret));
        const copy = el("button", "flip-btn vault-copy", "Copy");
        copy.type = "button"; copy.onclick = () => copyText(secret, copy);
        cell.appendChild(copy);
    }
    // The "keep visible" toggle (pins past Z release).
    const pin = el("label", "vault-pin");
    const cb = el("input"); cb.type = "checkbox"; cb.checked = !!vaultPinned[id];
    cb.title = "Keep this key visible";
    cb.onchange = () => {
        if(cb.checked) vaultPinned[id] = true; else delete vaultPinned[id];
        updateSecretCell(id);
    };
    pin.append(cb, document.createTextNode(" keep"));
    cell.appendChild(pin);
}

function updateSecretCell(id){
    const cell = document.getElementById("vaultSecret-" + id);
    const entry = findVaultEntry(id);
    if(cell && entry) renderSecretCell(cell, entry);
}

function updateAllSecretCells(){
    for(const list of Object.values(vaultEntries)){
        (list || []).forEach(e => updateSecretCell(e.id));
    }
}

async function ensureSecret(id){
    if(vaultSecrets[id] !== undefined) return;
    const res = await vaultApi("/api/vault/entries/" + id + "/reveal", "POST");
    if(res.status === 423){ await loadVault(); return; }
    if(res.ok && res.json){ vaultSecrets[id] = res.json.secret; updateSecretCell(id); }
}

async function ensureAllSecrets(){
    const res = await vaultApi("/api/vault/reveal-all", "POST");
    if(res.status === 423){ await loadVault(); return; }
    if(res.ok && res.json){ Object.assign(vaultSecrets, res.json.secrets || {}); updateAllSecretCells(); }
}

// Wires the Workshop hold-Z reveal (mirrors Customer Management's org-name reveal,
// scoped to the Workshop view so the two never fight over the Z key).
function initVaultReveal(){
    const inField = (el) => el && (el.tagName === "INPUT" || el.tagName === "TEXTAREA" || el.isContentEditable);
    const active = () => {
        const v = document.getElementById("view-workshop");
        return v && !v.classList.contains("view-hidden");
    };
    document.addEventListener("keydown", (e) => {
        if(e.code !== "KeyZ" || e.repeat || e.ctrlKey || e.metaKey || e.altKey) return;
        if(!active() || inField(e.target)) return;
        vaultZHeld = true;
        if(vaultRevealAll) ensureAllSecrets();
        updateAllSecretCells();
    });
    document.addEventListener("keyup", (e) => {
        if(e.code === "KeyZ"){ vaultZHeld = false; updateAllSecretCells(); }
    });
    window.addEventListener("blur", () => { vaultZHeld = false; updateAllSecretCells(); });
}

// ----- search -----

let _vaultSearchTimer = null;
function onVaultSearch(query){
    vaultSearch = query;
    if(_vaultSearchTimer) clearTimeout(_vaultSearchTimer);
    _vaultSearchTimer = setTimeout(async () => {
        const res = await vaultApi("/api/vault/search", "POST", {query: vaultSearch});
        if(res.status === 423){ await loadVault(); return; }
        if(res.ok && res.json){ vaultEntries = res.json.entries || {}; renderVaultGroups(); }
    }, 150);
}

// ----- gate actions -----

async function createVault(p1, p2, err, onDone){
    const fail = (m) => { if(err){ err.textContent = m; err.hidden = false; } };
    if((p1 || "").length < 8){ fail("Passphrase must be at least 8 characters."); return; }
    if(p1 !== p2){ fail("The passphrases don't match."); return; }
    const res = await vaultApi("/api/vault/init", "POST", {passphrase: p1});
    if(!res.ok){ fail("Could not create the vault."); return; }
    vaultStatus = res.json;
    if(onDone) await onDone();
    await loadVault();
}

async function unlockVault(p1, err, onDone){
    const res = await vaultApi("/api/vault/unlock", "POST", {passphrase: p1});
    if(!res.ok){ if(err){ err.textContent = "Wrong passphrase, or the vault could not be unlocked."; err.hidden = false; } return; }
    vaultStatus = res.json;
    if(onDone) await onDone();
    await loadVault();
}

async function unlockVaultDpapi(err, onDone){
    const res = await vaultApi("/api/vault/unlock", "POST", {dpapi: true});
    if(!res.ok){ if(err){ err.textContent = "Could not unlock with the remembered key."; err.hidden = false; } return; }
    vaultStatus = res.json;
    if(onDone) await onDone();
    await loadVault();
}

// ----- startup login overlay -----

// Shown first on launch (called from main.js::init). Reuses the gate forms; on
// success it hides the overlay and refreshes mail (the server auto-scans on unlock,
// so badges/captures are up to date). Skipping leaves the Password Manager locked
// while the rest of the app runs normally.
async function showVaultLogin(){
    const overlay = document.getElementById("vaultLoginOverlay");
    const panel = document.getElementById("vaultLoginPanel");
    if(!overlay || !panel) return;
    const res = await vaultApi("/api/vault/status");
    vaultStatus = res.json || {available: false, initialized: false, unlocked: false};
    panel.innerHTML = "";
    const done = async () => { hideVaultLogin(); loadMail(); };
    if(!vaultStatus.available){
        panel.appendChild(el("p", "vault-note",
            "The Key Vault needs the cryptography package, which isn't installed here. "
            + "The rest of the app works without it."));
        panel.appendChild(skipButton("Continue"));
    } else if(vaultStatus.unlocked){
        hideVaultLogin(); return;
    } else if(!vaultStatus.initialized){
        panel.appendChild(el("p", "vault-note",
            "Set a master passphrase to create your Key Vault. It encrypts every key "
            + "and is never stored. You can also skip and set it up later in Workshop."));
        panel.appendChild(buildCreateForm(done));
        panel.appendChild(skipButton("Skip for now"));
    } else {
        panel.appendChild(el("p", "vault-note",
            "Log in to your Key Vault to enable the Password Manager and automatic "
            + "password capture. You can skip and use the rest of the app without it."));
        panel.appendChild(buildUnlockForm(done));
        panel.appendChild(skipButton("Skip for now"));
    }
    overlay.hidden = false;
}

function hideVaultLogin(){
    const overlay = document.getElementById("vaultLoginOverlay");
    if(overlay) overlay.hidden = true;
}

function skipButton(text){
    const btn = el("button", "flip-btn vault-skip", text);
    btn.type = "button"; btn.onclick = hideVaultLogin;
    return btn;
}

async function lockVault(){
    await vaultApi("/api/vault/lock", "POST");
    vaultSecrets = {}; vaultPinned = {}; vaultSearch = "";
    await loadVault();
}

async function setRemember(enable){
    const res = await vaultApi("/api/vault/remember", "POST", {enable: enable});
    if(res.json) vaultStatus = res.json;
}

// ----- entries -----

async function loadVaultEntries(){
    const res = await vaultApi("/api/vault/entries");
    if(res.status === 423){ vaultStatus.unlocked = false; renderVaultPanel(); return; }
    vaultEntries = (res.json && res.json.entries) || {};
    renderVaultPanel();
}

function copyText(text, btn){
    const done = () => { if(btn){ const o = btn.textContent; btn.textContent = "Copied"; setTimeout(() => { btn.textContent = o; }, 1200); } };
    if(navigator.clipboard && navigator.clipboard.writeText){
        navigator.clipboard.writeText(text).then(done, () => {});
        return;
    }
    const ta = document.createElement("textarea");
    ta.value = text; document.body.appendChild(ta); ta.select();
    try{ document.execCommand("copy"); done(); }catch(e){}
    document.body.removeChild(ta);
}

// ----- entry editor modal -----

function openVaultEntry(entryId, presetOrgId){
    editingVaultEntryId = entryId || null;
    let entry = null;
    if(entryId){
        for(const list of Object.values(vaultEntries)){
            const found = (list || []).find(e => e.id === entryId);
            if(found){ entry = found; break; }
        }
    }
    const orgSel = document.getElementById("vaultEntryOrg");
    orgSel.innerHTML = "";
    Object.keys(vaultOrgNames).forEach(id => {
        const opt = document.createElement("option");
        opt.value = id; opt.textContent = vaultOrgNames[id];
        orgSel.appendChild(opt);
    });
    const targetOrg = (entry && entry.org_id) || presetOrgId || orgSel.value;
    orgSel.value = targetOrg || "";
    // An existing key keeps its org fixed (moving keys between orgs isn't a flow).
    orgSel.disabled = !!entryId;

    document.getElementById("vaultEntryTitle").textContent = entryId ? "Edit Key" : "New Key";
    document.getElementById("vaultEntryLabel").value = entry ? (entry.label || "") : "";
    document.getElementById("vaultEntryUsername").value = entry ? (entry.username || "") : "";
    document.getElementById("vaultEntrySecret").value = "";
    document.getElementById("vaultEntrySecret").placeholder =
        entry && entry.has_secret ? "(unchanged — type to replace)" : "the password / key";
    document.getElementById("vaultEntryUrl").value = entry ? (entry.url || "") : "";
    document.getElementById("vaultEntryDeleteBtn").hidden = !entryId;
    document.getElementById("vaultEntryError").hidden = true;
    document.getElementById("vaultEntryModal").hidden = false;
}

function closeVaultEntry(){
    // Don't leave a typed secret sitting in the field after closing.
    document.getElementById("vaultEntrySecret").value = "";
    document.getElementById("vaultEntryModal").hidden = true;
    editingVaultEntryId = null;
}

function vaultEntryError(msg){
    const err = document.getElementById("vaultEntryError");
    err.textContent = msg; err.hidden = false;
}

async function saveVaultEntry(){
    const label = document.getElementById("vaultEntryLabel").value.trim();
    const secret = document.getElementById("vaultEntrySecret").value;
    const payload = {
        org_id: document.getElementById("vaultEntryOrg").value,
        label: label,
        username: document.getElementById("vaultEntryUsername").value.trim(),
        url: document.getElementById("vaultEntryUrl").value.trim(),
    };
    if(!label){ vaultEntryError("Give the key a label."); return; }

    let res;
    if(editingVaultEntryId){
        // Omit the secret on edit unless the user typed a replacement.
        if(secret) payload.secret = secret;
        res = await vaultApi("/api/vault/entries/" + editingVaultEntryId, "PUT", payload);
    } else {
        if(!secret){ vaultEntryError("Enter the secret to store."); return; }
        if(!payload.org_id){ vaultEntryError("Pick an organization."); return; }
        payload.secret = secret;
        res = await vaultApi("/api/vault/entries", "POST", payload);
    }
    if(res.status === 423){ closeVaultEntry(); await loadVault(); return; }
    if(!res.ok){ vaultEntryError("Could not save the key."); return; }
    closeVaultEntry();
    await loadVaultEntries();
}

async function deleteVaultEntry(){
    if(!editingVaultEntryId) return;
    if(!confirm("Delete this key? This cannot be undone.")) return;
    const res = await vaultApi("/api/vault/entries/" + editingVaultEntryId, "DELETE");
    if(res.status === 423){ closeVaultEntry(); await loadVault(); return; }
    closeVaultEntry();
    await loadVaultEntries();
}

// Smart Password Detection auto-records detected passwords into the sender's org
// Key Vault during the scan (POST /api/passwords/scan); senders that resolve to no
// org are parked under the "Unassigned" group and re-homed when the org is later
// set in Customer Management. There is no per-mail capture action here.
