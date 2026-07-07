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
    const status = el("div", "wb-status"); status.id = "workbenchStatus"; status.hidden = true;
    bar.appendChild(status);
    root.appendChild(bar);

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
