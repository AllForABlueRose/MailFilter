// Unlock Station: a Workshop slide-in panel that pairs the Key Vault with today's
// workspace files. The top half is a file-explorer view of the vault keys (grouped
// and background-coloured per customer organization); the bottom half is today's
// workspace files (same org colours, a lock badge on encrypted ones). Drag a key
// onto a file to assign it, then "Unlock files" unzips/decrypts them server-side.
//
// Only mounted while the vault is unlocked (workshop.js::renderVaultPanel calls
// syncUnlockStation). Reuses el()/vaultApi() from workshop.js (shared global scope)
// and globals from state.js (unlockOpen, unlockFiles, unlockAssignments, ...).
// Every server/user string is inserted as DOM text, never HTML.

let _unlockKeyTimer = null;      // debounce for the key-explorer search
let _unlockSecretCache = {};     // entry_id -> revealed secret (session only, dropped on lock)

// ----- DOM scaffolding -----

function ensureUnlockDom(){
    const root = document.getElementById("unlockRoot");
    if(!root || document.getElementById("unlockDock")) return;

    // Bottom-right dock: status line, action buttons, and the open/close toggle.
    const dock = el("div", "unlock-dock"); dock.id = "unlockDock"; dock.hidden = true;
    const status = el("div", "unlock-status"); status.id = "unlockStatus"; status.hidden = true;
    const actions = el("div", "unlock-actions"); actions.id = "unlockActions";
    const toggle = el("button", "unlock-toggle", "🗝 Unlock Station");
    toggle.type = "button"; toggle.id = "unlockToggle"; toggle.onclick = toggleUnlockStation;
    dock.append(status, actions, toggle);
    root.appendChild(dock);

    // Slide-in panel: keys (top) over workspace files (bottom).
    const panel = el("div", "unlock-panel"); panel.id = "unlockPanel"; panel.hidden = true;
    const inner = el("div", "unlock-panel-inner");
    inner.appendChild(_buildSection("🔑 Keys", "unlockKeyGrid", "unlockKeySearchInput",
        "Search keys…", v => onUnlockKeySearch(v)));
    inner.appendChild(_buildSection("🗂 Today's workspace", "unlockFileGrid", "unlockFileSearchInput",
        "Search files…", v => { unlockFileSearch = v; renderUnlockFiles(); }, "unlock-file-flow"));
    panel.appendChild(inner);
    root.appendChild(panel);
}

function _buildSection(title, gridId, searchId, placeholder, onSearch, gridClass){
    const sec = el("div", "unlock-sec");
    const head = el("div", "unlock-sec-head");
    head.appendChild(el("span", "unlock-sec-title", title));
    const search = el("input", "unlock-search");
    search.type = "search"; search.id = searchId; search.placeholder = placeholder;
    search.addEventListener("input", () => onSearch(search.value));
    head.appendChild(search);
    const grid = el("div", "unlock-grid" + (gridClass ? " " + gridClass : "")); grid.id = gridId;
    sec.append(head, grid);
    return sec;
}

// ----- show/hide with the vault lock state -----

function syncUnlockStation(){
    ensureUnlockDom();
    const dock = document.getElementById("unlockDock");
    const panel = document.getElementById("unlockPanel");
    if(!dock || !panel) return;
    const unlocked = !!(vaultStatus && vaultStatus.unlocked);
    dock.hidden = !unlocked;
    if(!unlocked){
        // Locked: force the panel closed and drop any revealed secrets.
        unlockOpen = false;
        panel.hidden = true; panel.classList.remove("open");
        _unlockSecretCache = {};
        updateUnlockActions();
    }
}

function toggleUnlockStation(){
    unlockOpen = !unlockOpen;
    const panel = document.getElementById("unlockPanel");
    const toggle = document.getElementById("unlockToggle");
    if(unlockOpen){
        panel.hidden = false;
        requestAnimationFrame(() => panel.classList.add("open"));
        toggle.textContent = "✕ Close Unlock Station";
        loadUnlockData();
    } else {
        panel.classList.remove("open");
        toggle.textContent = "🗝 Unlock Station";
        setTimeout(() => { if(!unlockOpen) panel.hidden = true; }, 320);
    }
    updateUnlockActions();
}

// ----- data loading -----

async function loadUnlockData(){
    const orgs = await vaultApi("/api/organizations");
    unlockOrgMeta = {};
    ((orgs.json && orgs.json.organizations) || []).forEach(o => {
        unlockOrgMeta[o.id] = {
            name: (o.display_name && o.display_name.trim()) ? o.display_name : o.name,
            color: o.color || "#3b82f6",
            card_style: o.card_style || "outline",
            card_pattern: o.card_pattern || "none",
        };
    });
    await loadUnlockKeys();
    await loadUnlockFiles();
}

async function loadUnlockKeys(){
    const q = (unlockKeySearch || "").trim();
    const res = q
        ? await vaultApi("/api/vault/search", "POST", {query: q})
        : await vaultApi("/api/vault/entries");
    unlockKeyEntries = (res.json && res.json.entries) || {};
    renderUnlockKeys();
}

function onUnlockKeySearch(v){
    unlockKeySearch = v;
    clearTimeout(_unlockKeyTimer);
    _unlockKeyTimer = setTimeout(loadUnlockKeys, 180);
}

async function loadUnlockFiles(){
    const res = await vaultApi("/api/workspace/files");
    const data = res.json || {};
    unlockWorkspaceExists = !!data.exists;
    unlockFiles = data.files || [];
    // Drop assignments whose file is gone (e.g. a zip consumed by a prior unlock).
    const present = new Set(unlockFiles.map(f => f.name));
    Object.keys(unlockAssignments).forEach(n => { if(!present.has(n)) delete unlockAssignments[n]; });
    renderUnlockFiles();
    updateUnlockActions();
}

// ----- org colour helpers -----

function unlockOrgColor(orgId){
    return (unlockOrgMeta[orgId] && unlockOrgMeta[orgId].color) || "";
}
function unlockOrgLabel(orgId){
    if(orgId === "unassigned") return "Unassigned (sender not yet mapped to an org)";
    return (unlockOrgMeta[orgId] && unlockOrgMeta[orgId].name) || "(unknown organization)";
}

// ----- key explorer (top) -----

function renderUnlockKeys(){
    const grid = document.getElementById("unlockKeyGrid");
    if(!grid) return;
    grid.innerHTML = "";
    const orgIds = Object.keys(unlockKeyEntries).filter(id => (unlockKeyEntries[id] || []).length);
    if(!orgIds.length){
        grid.appendChild(el("p", "unlock-empty",
            unlockKeySearch ? "No keys match your search." : "No keys in the vault yet."));
        return;
    }
    orgIds.forEach(orgId => {
        const group = el("div", "unlock-key-group");
        const head = el("div", "unlock-group-head", unlockOrgLabel(orgId));
        const color = unlockOrgColor(orgId);
        if(color){ group.style.setProperty("--org-color", color); }
        group.appendChild(head);
        const tiles = el("div", "unlock-tiles");
        (unlockKeyEntries[orgId] || []).forEach(entry => tiles.appendChild(buildKeyTile(entry, orgId)));
        group.appendChild(tiles);
        grid.appendChild(group);
    });
}

function buildKeyTile(entry, orgId){
    // Neutral card; the org colour is a continuous backdrop on the enclosing
    // .unlock-key-group, not the tile itself.
    const tile = el("div", "unlock-key-tile");
    tile.draggable = true;
    tile.dataset.entryId = entry.id;
    tile.addEventListener("dragstart", e => {
        e.dataTransfer.setData("text/x-unlock-key", entry.id);
        e.dataTransfer.setData("text/plain", entry.id);
        e.dataTransfer.effectAllowed = "copy";
    });
    tile.appendChild(el("div", "unlock-key-icon", "🔑"));
    tile.appendChild(el("div", "unlock-key-label", entry.label || "Key"));
    const meta = el("div", "unlock-key-meta");
    meta.appendChild(el("span", "unlock-kind kind-" + (entry.kind || "managed"),
        entry.kind === "temporary" ? "temporary" : "managed"));
    const dt = entry.kind === "temporary" ? (entry.scan_dt || "") : (entry.created || "");
    if(dt){ meta.appendChild(el("span", "unlock-key-dt", dt)); }
    tile.appendChild(meta);
    // Hover reveals the value (fetched once, cached for the session).
    const val = el("div", "unlock-key-value", "••••••");
    tile.appendChild(val);
    tile.addEventListener("mouseenter", async () => {
        if(!entry.has_secret){ val.textContent = "(no value)"; return; }
        if(_unlockSecretCache[entry.id] != null){ val.textContent = _unlockSecretCache[entry.id]; return; }
        const r = await vaultApi("/api/vault/entries/" + entry.id + "/reveal", "POST");
        if(r.ok && r.json){ _unlockSecretCache[entry.id] = r.json.secret; val.textContent = r.json.secret; }
    });
    tile.addEventListener("mouseleave", () => { val.textContent = "••••••"; });
    return tile;
}

function keyLabelById(entryId){
    for(const items of Object.values(unlockKeyEntries)){
        for(const e of items){ if(e.id === entryId){ return e.label; } }
    }
    return null;
}

// ----- workspace files (bottom) -----

function fileIcon(kind){
    return kind === "zip" ? "🗜" : kind === "excel" ? "📊" : "📄";
}

function renderUnlockFiles(){
    const grid = document.getElementById("unlockFileGrid");
    if(!grid) return;
    grid.innerHTML = "";
    if(!unlockWorkspaceExists){
        grid.appendChild(el("p", "unlock-empty",
            "Today's workspace does not exist. Perform required operations first."));
        return;
    }
    const q = (unlockFileSearch || "").trim().toLowerCase();
    const files = unlockFiles.filter(f =>
        !q || f.name.toLowerCase().includes(q) || (f.org_name || "").toLowerCase().includes(q));
    if(!files.length){
        grid.appendChild(el("p", "unlock-empty", q ? "No files match your search."
            : "No files in today's workspace yet."));
        return;
    }
    // Group by organization (mirrors the key explorer) so each org gets one
    // continuous coloured panel; files with no org fall into a trailing neutral bucket.
    const NO_ORG = "__noorg__";
    const byOrg = {};
    files.forEach(f => {
        const key = f.org_id || NO_ORG;
        (byOrg[key] || (byOrg[key] = [])).push(f);
    });
    // Order: org-meta insertion order, then any unknown org, no-org last.
    const ordered = Object.keys(unlockOrgMeta).filter(id => byOrg[id])
        .concat(Object.keys(byOrg).filter(id => id !== NO_ORG && !(id in unlockOrgMeta)));
    if(byOrg[NO_ORG]) ordered.push(NO_ORG);
    ordered.forEach(orgId => {
        const group = el("div", "unlock-file-group");
        if(orgId === NO_ORG){
            group.classList.add("no-org");
            group.appendChild(el("div", "unlock-group-head", "User-added · no organization"));
        } else {
            const color = unlockOrgColor(orgId);
            if(color){ group.style.setProperty("--org-color", color); }
            group.appendChild(el("div", "unlock-group-head", unlockOrgLabel(orgId)));
        }
        const tiles = el("div", "unlock-file-tiles");
        byOrg[orgId].forEach(f => tiles.appendChild(buildFileTile(f)));
        group.appendChild(tiles);
        grid.appendChild(group);
    });
}

function buildFileTile(f){
    // Neutral card; the org colour is a continuous backdrop on the enclosing
    // .unlock-file-group, not the tile itself.
    const tile = el("div", "unlock-file-tile kind-" + f.kind);

    tile.addEventListener("dragover", e => {
        e.preventDefault(); e.dataTransfer.dropEffect = "copy"; tile.classList.add("drop-hover");
    });
    tile.addEventListener("dragleave", () => tile.classList.remove("drop-hover"));
    tile.addEventListener("drop", e => {
        e.preventDefault(); tile.classList.remove("drop-hover");
        const id = e.dataTransfer.getData("text/x-unlock-key") || e.dataTransfer.getData("text/plain");
        if(id){ unlockAssignments[f.name] = id; renderUnlockFiles(); updateUnlockActions(); }
    });

    const icon = el("div", "unlock-file-icon", fileIcon(f.kind));
    if(f.encrypted){
        const lock = el("span", "unlock-lock-badge", "🔒"); lock.title = "Encrypted";
        icon.appendChild(lock);
    }
    tile.appendChild(icon);
    tile.appendChild(el("div", "unlock-file-name", f.name));
    const meta = el("div", "unlock-file-meta");
    if(f.org_name){ meta.appendChild(el("span", "unlock-file-org", f.org_name)); }
    else if(f.source === "external"){ meta.appendChild(el("span", "unlock-file-ext", "user-added · no org")); }
    tile.appendChild(meta);

    const assigned = unlockAssignments[f.name];
    if(assigned){
        const box = el("div", "unlock-assigned");
        box.appendChild(el("span", "unlock-assigned-key", "🔑 " + (keyLabelById(assigned) || "assigned")));
        const x = el("button", "unlock-unassign", "✕");
        x.type = "button"; x.title = "Remove assigned key";
        x.onclick = () => { delete unlockAssignments[f.name]; renderUnlockFiles(); updateUnlockActions(); };
        box.appendChild(x);
        tile.appendChild(box);
    }
    return tile;
}

// ----- action buttons + operations -----

function updateUnlockActions(){
    const box = document.getElementById("unlockActions");
    if(!box) return;
    box.innerHTML = "";
    if(!unlockOpen) return;
    const hasFiles = unlockWorkspaceExists && unlockFiles.length > 0;
    if(Object.keys(unlockAssignments).length > 0){
        const b = el("button", "unlock-act-btn primary", "🔓 Unlock files");
        b.type = "button"; b.onclick = doUnlock; box.appendChild(b);
    }
    if(hasFiles){
        const s = el("button", "unlock-act-btn", "✨ Smart Key Assignment and Unlock");
        s.type = "button"; s.onclick = doSmartUnlock; box.appendChild(s);
    }
    if(unlockLastUnlocked.length > 0){
        const r = el("button", "unlock-act-btn", "📝 Record Customer Key Assignment");
        r.type = "button"; r.onclick = doRecord; box.appendChild(r);
    }
}

function setUnlockStatus(msg){
    const status = document.getElementById("unlockStatus");
    if(!status) return;
    status.textContent = msg || "";
    status.hidden = !msg;
}

async function doUnlock(){
    setUnlockStatus("Unlocking files…");
    const res = await vaultApi("/api/workspace/unlock", "POST",
        {assignments: Object.assign({}, unlockAssignments)});
    handleUnlockResult(res);
}

async function doSmartUnlock(){
    setUnlockStatus("Smart assigning keys and unlocking…");
    const res = await vaultApi("/api/workspace/smart-unlock", "POST", {});
    handleUnlockResult(res);
}

function handleUnlockResult(res){
    if(res.status === 423){ setUnlockStatus("The vault locked. Unlock it and try again."); return; }
    const data = res.json || {};
    if(res.status === 404){ setUnlockStatus(data.error || "No workspace folder for today."); return; }
    const ok = data.unlocked || [];
    const errs = data.errors || [];
    // Only key-bearing unlocks are worth recording as a per-org pattern.
    unlockLastUnlocked = ok.filter(u => u.org_id && u.key_kind)
        .map(u => ({org_id: u.org_id, file_kind: u.file_kind, key_kind: u.key_kind}));
    unlockAssignments = {};
    _unlockSecretCache = {};
    let msg = ok.length + " file(s) unlocked";
    if(errs.length){
        msg += "; " + errs.length + " failed — " + errs.map(e => e.name + " (" + e.error + ")").join(", ");
    }
    setUnlockStatus(msg);
    loadUnlockFiles();
}

async function doRecord(){
    const records = unlockLastUnlocked.map(u => ({
        org_id: u.org_id, file_kind: u.file_kind, key_kind: u.key_kind,
    }));
    if(!records.length){ setUnlockStatus("Nothing to record."); return; }
    const res = await vaultApi("/api/workspace/record-assignment", "POST", {records});
    if(res.ok){
        setUnlockStatus("Recorded key-assignment pattern for " + records.length + " unlock(s).");
        unlockLastUnlocked = [];
        updateUnlockActions();
    } else {
        setUnlockStatus("Could not record the key assignment.");
    }
}
