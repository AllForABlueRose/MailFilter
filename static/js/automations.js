// Automations view: cards for each saved-search workflow, plus the builder
// modal. An automation chains a saved MailFilter search with action steps and
// runs periodically. Relies on globals from state.js (automationsById,
// editingAutomationId), templates.js (templateBodies, settingsEqual), and
// search.js (currentSettings).

const STEP_LABELS = { mark: "Mark", download: "Download", report: "Report" };
const STEP_BOXES = { mark: "autoStepMark", download: "autoStepDownload", report: "autoStepReport" };

// ----- loading + rendering -----

async function loadAutomations(){
    try{
        const res = await fetch("/api/automations");
        const data = await res.json();
        automationsById = {};
        (data.automations || []).forEach(a => { automationsById[a.id] = a; });
        renderAutomations(data.automations || []);
    }catch(e){
        // Leave whatever is on screen; the tab can be re-entered to retry.
    }
}

function renderAutomations(list){
    const grid = document.getElementById("automationGrid");
    grid.innerHTML = "";
    if(!list.length){
        const empty = document.createElement("p");
        empty.className = "auto-empty";
        empty.textContent = "No automations yet. Create one to chain a saved search with actions that run on a schedule.";
        grid.appendChild(empty);
        return;
    }
    list.forEach(a => grid.appendChild(createAutomationCard(a)));
}

function createAutomationCard(a){
    const card = document.createElement("div");
    card.className = "auto-card" + (a.enabled ? " running" : "");
    card.dataset.id = a.id;
    card.style.setProperty("--auto-color", a.color);

    // Clicking the body opens the editor; the action buttons sit outside it.
    const main = document.createElement("div");
    main.className = "auto-card-main";
    main.title = "Click to edit";
    main.onclick = () => openAutomationBuilder(a.id);

    const head = document.createElement("div");
    head.className = "auto-card-head";
    const cycle = document.createElement("span");
    cycle.className = "auto-card-cycle";
    cycle.textContent = "⟳";  // ⟳ — spins via CSS only while .running
    cycle.setAttribute("aria-hidden", "true");
    const name = document.createElement("h3");
    name.className = "auto-card-name";
    name.textContent = a.name;
    head.append(cycle, name);

    const info = document.createElement("div");
    info.className = "auto-card-info";
    info.append(
        infoLine("Schedule", "every " + humanInterval(a.interval_seconds)),
        infoLine("Steps", stepsLabel(a.steps)),
        infoLine("Last run", a.last_run ? `${a.last_run} — ${a.last_status || ""}` : "never"),
    );

    main.append(head, info);

    const actions = document.createElement("div");
    actions.className = "auto-card-actions";

    const toggle = document.createElement("button");
    toggle.className = "auto-toggle";
    toggle.textContent = a.enabled ? "⏹" : "⏻";  // ⏹ stop / ⏻ start
    toggle.title = a.enabled ? "Stop periodic running" : "Start running periodically";
    toggle.onclick = () => toggleAutomation(a.id, !a.enabled);

    const run = document.createElement("button");
    run.className = "auto-run";
    run.textContent = "⚡";  // ⚡
    run.title = "Run once now";
    run.onclick = () => runAutomationNow(a.id, run);

    actions.append(toggle, run);
    card.append(main, actions);
    return card;
}

function infoLine(label, value){
    const div = document.createElement("div");
    div.className = "auto-info-line";
    const b = document.createElement("span");
    b.className = "auto-info-label";
    b.textContent = label + ": ";
    div.append(b, document.createTextNode(value));
    return div;
}

function stepsLabel(steps){
    if(!steps || !steps.length) return "none";
    return steps.map(s => STEP_LABELS[s] || s).join(", ");
}

// Seconds -> "30 minutes" / "2 hours" / "1 day", picking the largest exact unit.
function humanInterval(seconds){
    const units = [[86400, "day"], [3600, "hour"], [60, "minute"], [1, "second"]];
    for(const [size, label] of units){
        if(seconds % size === 0){
            const n = seconds / size;
            return `${n} ${label}${n === 1 ? "" : "s"}`;
        }
    }
    return `${seconds} seconds`;
}

// ----- card actions -----

async function toggleAutomation(id, enabled){
    try{
        await fetch(`/api/automations/${id}/toggle`, {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({enabled}),
        });
    }catch(e){}
    loadAutomations();
}

async function runAutomationNow(id, btn){
    if(btn) btn.disabled = true;
    try{
        await fetch(`/api/automations/${id}/run`, {method: "POST"});
    }catch(e){}
    // The run is asynchronous server-side; reload shortly to show its result.
    setTimeout(() => { loadAutomations(); }, 1500);
}

// ----- builder modal -----

function openAutomationBuilder(id){
    editingAutomationId = id || null;
    const a = id ? automationsById[id] : null;

    document.getElementById("automationModalTitle").textContent = a ? "Edit Automation" : "New Automation";
    document.getElementById("autoName").value = a ? a.name : "";
    document.getElementById("autoColor").value = a ? a.color : "#3b82f6";

    const [value, unit] = a ? splitInterval(a.interval_seconds) : [1, 3600];
    document.getElementById("autoIntervalValue").value = value;
    document.getElementById("autoIntervalUnit").value = String(unit);

    const steps = (a && a.steps) || [];
    for(const [step, boxId] of Object.entries(STEP_BOXES)){
        document.getElementById(boxId).checked = steps.includes(step);
    }

    populateTemplateSelect(!!a);
    document.getElementById("autoDeleteBtn").hidden = !a;
    document.getElementById("automationModal").hidden = false;
}

function closeAutomationBuilder(){
    document.getElementById("automationModal").hidden = true;
    editingAutomationId = null;
}

// Rebuild the criteria dropdown: when editing, the first option keeps the
// automation's stored search untouched; otherwise it defaults to the live search.
function populateTemplateSelect(editing){
    const select = document.getElementById("autoTemplate");
    select.innerHTML = "";
    if(editing){ select.appendChild(new Option("Keep existing search", "__keep__")); }
    select.appendChild(new Option("Current MailFilter search", ""));
    Object.keys(templateBodies || {}).forEach(name => select.appendChild(new Option(name, name)));
    select.value = editing ? "__keep__" : "";
}

function builderQuery(){
    const choice = document.getElementById("autoTemplate").value;
    if(choice === "__keep__" && editingAutomationId){
        return automationsById[editingAutomationId].query;
    }
    if(choice && templateBodies[choice]){
        return templateBodies[choice];
    }
    return currentSettings();
}

function builderSteps(){
    return Object.entries(STEP_BOXES)
        .filter(([, boxId]) => document.getElementById(boxId).checked)
        .map(([step]) => step);
}

function splitInterval(seconds){
    for(const size of [86400, 3600, 60]){
        if(seconds % size === 0){ return [seconds / size, size]; }
    }
    return [seconds, 1];
}

async function saveAutomation(){
    const name = document.getElementById("autoName").value.trim();
    if(!name){ alert("Give the automation a name."); return; }

    const value = Math.max(1, parseInt(document.getElementById("autoIntervalValue").value, 10) || 1);
    const unit = parseInt(document.getElementById("autoIntervalUnit").value, 10) || 3600;

    const payload = {
        name,
        color: document.getElementById("autoColor").value,
        interval_seconds: value * unit,
        steps: builderSteps(),
        query: builderQuery(),
    };

    const editing = editingAutomationId;
    const url = editing ? `/api/automations/${editing}` : "/api/automations";
    const res = await fetch(url, {
        method: editing ? "PUT" : "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify(payload),
    });
    if(!res.ok){ alert("Could not save the automation."); return; }
    closeAutomationBuilder();
    loadAutomations();
}

async function deleteAutomationFromBuilder(){
    if(!editingAutomationId) return;
    const a = automationsById[editingAutomationId];
    if(!confirm(`Delete the automation "${a ? a.name : ""}"?`)) return;
    await fetch(`/api/automations/${editingAutomationId}`, {method: "DELETE"});
    closeAutomationBuilder();
    loadAutomations();
}
