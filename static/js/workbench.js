// Workshop -> Workbench Processing screen. A read-only view of today's workspace
// files (the same /api/workspace/files listing the Unlock Station's bottom half
// uses), grouped by customer organization on continuous org-coloured panels. Plus
// one action: "Bring Last Workspace to Today", which renames the most recent past
// dated workspace folder to today so it becomes today's workspace.
//
// Reuses el()/vaultApi() from workshop.js (shared global scope) and the Unlock
// Station's file CSS classes (unlock-file-group / unlock-file-tiles / unlock-file-
// tile / unlock-file-flow) so the scoped, side-by-side layout is shared. Globals
// from state.js (workbenchFiles, workbenchExists, workbenchOrgMeta). Every
// server/user string is inserted as DOM text, never HTML.

// Org-stamp state (module-local, like unlockstation.js's _unlock* locals). A stamp
// carries the picked org via this dataTransfer type; the org_id itself is read from
// _wbStampOrgId on drop (so the "unassigned" empty id survives a drag reliably).
const WB_STAMP_MIME = "text/x-mailfilter-orgstamp";
let _wbStampOpen = false;    // is the "Stamp Customer Organization" builder showing
let _wbStampOrgId = null;    // committed org id ("" = unassigned/clear), null = none picked
let _wbStampOrgName = "";

function openWorkshopWorkbench(){
    showWorkshopScreen("workbench");
    loadWorkbench();
}

async function loadWorkbench(){
    const orgs = await vaultApi("/api/organizations");
    workbenchOrgMeta = {};
    ((orgs.json && orgs.json.organizations) || []).forEach(o => {
        workbenchOrgMeta[o.id] = {
            name: (o.display_name && o.display_name.trim()) ? o.display_name : o.name,
            color: o.color || "#3b82f6",
        };
    });
    const res = await vaultApi("/api/workspace/files");
    const data = res.json || {};
    workbenchExists = !!data.exists;
    workbenchFiles = data.files || [];
    renderWorkbench();
}

function _wbOrgColor(orgId){
    return (workbenchOrgMeta[orgId] && workbenchOrgMeta[orgId].color) || "";
}
function _wbOrgLabel(orgId){
    if(orgId === "unassigned") return "Unassigned (sender not yet mapped to an org)";
    return (workbenchOrgMeta[orgId] && workbenchOrgMeta[orgId].name) || "(unknown organization)";
}
function _wbFileIcon(kind){
    return kind === "zip" ? "🗜" : kind === "excel" ? "📊" : "📄";
}

function renderWorkbench(){
    const root = document.getElementById("workbenchRoot");
    if(!root) return;
    root.innerHTML = "";

    // Toolbar: the "Bring Last Workspace to Today" action. Disabled when today's
    // workspace already exists (nothing to carry forward without clobbering it).
    const bar = el("div", "wb-bar");
    const bring = el("button", "auto-save-btn wb-bring-btn", "⏩ Bring Last Workspace to Today");
    bring.type = "button";
    bring.disabled = workbenchExists;
    bring.title = workbenchExists
        ? "Today's workspace already exists."
        : "Rename the most recent earlier workspace folder to today.";
    bring.onclick = bringLastWorkspace;
    bar.appendChild(bring);
    // "Stamp Customer Organization": pick an org, then drag the stamp onto files to
    // set/overwrite (or clear) their org. Only useful once today's workspace exists.
    const stamp = el("button", "auto-save-btn wb-stamp-btn", "🏷️ Stamp Customer Organization");
    stamp.type = "button";
    stamp.disabled = !workbenchExists;
    stamp.onclick = () => { _wbStampOpen = !_wbStampOpen; if(!_wbStampOpen){ _wbStampOrgId = null; } renderWorkbench(); };
    bar.appendChild(stamp);
    const status = el("div", "wb-status"); status.id = "workbenchStatus"; status.hidden = true;
    bar.appendChild(status);
    root.appendChild(bar);

    if(workbenchExists && _wbStampOpen){ root.appendChild(_wbStampPanel()); }

    const grid = el("div", "unlock-grid unlock-file-flow"); grid.id = "workbenchFileGrid";
    root.appendChild(grid);

    if(!workbenchExists){
        grid.appendChild(el("p", "unlock-empty",
            "Today's workspace does not exist. Perform required operations first."));
        return;
    }
    if(!workbenchFiles.length){
        grid.appendChild(el("p", "unlock-empty", "No files in today's workspace yet."));
        return;
    }

    // Group by organization (mirrors the Unlock Station) so each org gets one
    // continuous coloured panel; files with no org fall into a trailing neutral bucket.
    const NO_ORG = "__noorg__";
    const byOrg = {};
    workbenchFiles.forEach(f => {
        const key = f.org_id || NO_ORG;
        (byOrg[key] || (byOrg[key] = [])).push(f);
    });
    const ordered = Object.keys(workbenchOrgMeta).filter(id => byOrg[id])
        .concat(Object.keys(byOrg).filter(id => id !== NO_ORG && !(id in workbenchOrgMeta)));
    if(byOrg[NO_ORG]) ordered.push(NO_ORG);
    ordered.forEach(orgId => {
        const group = el("div", "unlock-file-group");
        if(orgId === NO_ORG){
            group.classList.add("no-org");
            group.appendChild(el("div", "unlock-group-head", "User-added · no organization"));
        } else {
            const color = _wbOrgColor(orgId);
            if(color){ group.style.setProperty("--org-color", color); }
            group.appendChild(el("div", "unlock-group-head", _wbOrgLabel(orgId)));
        }
        const tiles = el("div", "unlock-file-tiles");
        byOrg[orgId].forEach(f => tiles.appendChild(_wbFileTile(f)));
        group.appendChild(tiles);
        grid.appendChild(group);
    });
}

function _wbFileTile(f){
    const tile = el("div", "unlock-file-tile kind-" + f.kind);
    // Drop target for the org stamp (only reacts to the stamp drag type).
    tile.addEventListener("dragover", e => {
        if(!e.dataTransfer.types.includes(WB_STAMP_MIME)) return;
        e.preventDefault(); e.dataTransfer.dropEffect = "copy"; tile.classList.add("drop-hover");
    });
    tile.addEventListener("dragleave", () => tile.classList.remove("drop-hover"));
    tile.addEventListener("drop", e => {
        if(!e.dataTransfer.types.includes(WB_STAMP_MIME)) return;
        e.preventDefault(); tile.classList.remove("drop-hover");
        if(_wbStampOrgId !== null){ _wbStampFile(f.name, _wbStampOrgId); }
    });
    const icon = el("div", "unlock-file-icon", _wbFileIcon(f.kind));
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
    return tile;
}

function _wbStatus(msg){
    const status = document.getElementById("workbenchStatus");
    if(!status) return;
    status.textContent = msg || "";
    status.hidden = !msg;
}

// ----- org stamp: pick an org, then drag the chip onto files -----

function _wbStampPanel(){
    const panel = el("div", "wb-stamp-panel");
    panel.appendChild(el("span", "wb-stamp-lead", "Stamp:"));
    const chipHost = el("span", "wb-stamp-chiphost");
    panel.appendChild(_wbOrgCombo((id, name) => {
        _wbStampOrgId = id; _wbStampOrgName = name;
        _wbRenderChip(chipHost);
    }));
    panel.appendChild(chipHost);
    panel.appendChild(el("span", "wb-stamp-hint", "→ drag onto files to set their organization"));
    if(_wbStampOrgId !== null){ _wbRenderChip(chipHost); }
    return panel;
}

function _wbRenderChip(host){
    host.innerHTML = "";
    const label = _wbStampOrgId ? _wbStampOrgName : "(unassigned)";
    const chip = el("span", "wb-stamp-chip", "🏷️ " + label);
    chip.draggable = true;
    const color = _wbStampOrgId ? _wbOrgColor(_wbStampOrgId) : "";
    if(color){ chip.style.setProperty("--org-color", color); chip.classList.add("has-org"); }
    chip.addEventListener("dragstart", e => {
        e.dataTransfer.setData(WB_STAMP_MIME, "1");
        e.dataTransfer.effectAllowed = "copy";
    });
    host.appendChild(chip);
}

// A filterable org combobox modeled on customermatch.js::_orgCombo, sourced from
// workbenchOrgMeta and calling onPick(org_id, name) when a choice is committed.
function _wbOrgCombo(onPick){
    const options = [{id: "", name: "(unassigned)"}].concat(
        Object.keys(workbenchOrgMeta).map(id => ({id, name: workbenchOrgMeta[id].name})));

    const combo = el("div", "suspected-org-combo");
    const input = el("input", "suspected-org-input");
    input.type = "text"; input.autocomplete = "off"; input.placeholder = "organization";
    const list = document.createElement("ul");
    list.className = "suspected-org-list"; list.hidden = true;

    function render(query){
        const q = (query || "").trim().toLowerCase();
        list.innerHTML = "";
        const matches = options.filter(o => o.name.toLowerCase().includes(q));
        if(!matches.length){
            list.appendChild(el("li", "suspected-org-empty", "No matching organizations"));
            return;
        }
        matches.forEach(o => {
            const li = el("li", "suspected-org-opt", o.name);
            li.dataset.orgId = o.id;
            // mousedown, not click, so the pick beats the input's blur handler.
            li.addEventListener("mousedown", ev => { ev.preventDefault(); pick(o.id, o.name); });
            list.appendChild(li);
        });
    }
    function show(query){ render(query); list.hidden = false; }
    function pick(id, name){
        input.value = name; list.hidden = true;
        if(onPick){ onPick(id, name); }
    }
    function move(delta){
        const opts = Array.from(list.querySelectorAll(".suspected-org-opt"));
        if(!opts.length) return;
        let idx = opts.findIndex(o => o.classList.contains("suspected-org-opt-active"));
        idx = (idx < 0) ? (delta > 0 ? 0 : opts.length - 1) : idx + delta;
        if(idx < 0) idx = opts.length - 1;
        if(idx >= opts.length) idx = 0;
        opts.forEach(o => o.classList.remove("suspected-org-opt-active"));
        opts[idx].classList.add("suspected-org-opt-active");
        opts[idx].scrollIntoView({block: "nearest"});
    }
    input.addEventListener("focus", () => { input.select(); show(""); });
    input.addEventListener("input", () => show(input.value));
    input.addEventListener("keydown", ev => {
        if(ev.key === "Escape"){ list.hidden = true; return; }
        if(ev.key === "ArrowDown" || ev.key === "ArrowUp"){
            ev.preventDefault();
            if(list.hidden){ show(input.value); return; }
            move(ev.key === "ArrowDown" ? 1 : -1);
            return;
        }
        if(ev.key === "Enter"){
            const active = list.querySelector(".suspected-org-opt-active") ||
                list.querySelector(".suspected-org-opt");
            if(active){ ev.preventDefault(); pick(active.dataset.orgId, active.textContent); }
        }
    });
    input.addEventListener("blur", () => { setTimeout(() => { list.hidden = true; }, 150); });

    combo.append(input, list);
    return combo;
}

async function _wbStampFile(name, orgId){
    _wbStatus("Stamping " + name + "…");
    const res = await vaultApi("/api/workspace/file-org", "POST", {filename: name, org_id: orgId});
    const data = res.json || {};
    if(res.ok && data.ok){
        const who = orgId ? _wbStampOrgName : "no organization";
        _wbStatus("Stamped " + name + " → " + who + ".");
        loadWorkbench();
    } else {
        _wbStatus(data.error || "Could not stamp the file.");
    }
}

async function bringLastWorkspace(){
    _wbStatus("Bringing the last workspace to today…");
    const res = await vaultApi("/api/workspace/bring-last", "POST", {});
    const data = res.json || {};
    if(res.ok && data.ok){
        _wbStatus("Brought " + (data.source || "the last workspace") + " forward to today.");
        loadWorkbench();
    } else {
        _wbStatus(data.error || "Could not bring the last workspace forward.");
    }
}
