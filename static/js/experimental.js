// Experimental Features: the sidebar box that mounts opt-in feature controls and
// the panel that enables/disables them. Enablement (which controls are shown) is
// persisted server-side via /api/experimental; each feature's own operational
// state (the password filter, the normalize-width toggle) is a search setting and
// lives in search.js. Disabling a feature here also turns its behavior off so a
// hidden control can't keep affecting the list.

// One entry per experimental feature, tying its id to how to turn its behavior
// off. The data-feature attributes in index.html (the sidebar blocks and the
// panel checkboxes) use these same ids.
const EXPERIMENTAL_FEATURES = [
    {id: 'passwords',
     isOn: () => passwordsOnly,
     turnOff: () => { passwordsOnly = false; syncPasswordsButton(); }},
    {id: 'normalize_width',
     isOn: () => normalizeWidth,
     turnOff: () => { normalizeWidth = false; syncNormalizeWidthButton(); }},
    {id: 'attachment_search',
     isOn: () => attachmentSearch,
     turnOff: () => { attachmentSearch = false; syncAttachmentSearchButton(); }},
    {id: 'link_search',
     isOn: () => linkSearch,
     turnOff: () => { linkSearch = false; syncLinkSearchButton(); }},
    {id: 'append_customer_name',
     isOn: () => appendCustomerName,
     turnOff: () => { appendCustomerName = false; syncAppendCustomerNameButton(); }},
    // `resolve_customer_name` has no per-search operational state — enabling the
    // feature IS its activation — so it needs no isOn/turnOff entry here.
    {id: 'dedupe',
     isOn: () => dedupe,
     turnOff: () => { dedupe = false; syncDedupeButton(); }},
];

// Load the enabled set and mount the matching controls. Called once at startup.
async function loadExperimental(){
    try{
        const res = await fetch('/api/experimental');
        experimentalEnabled = await res.json();
    }catch(e){
        experimentalEnabled = {};
    }
    applyExperimentalUI();
}

// Show/hide each feature block in the sidebar box to match the enabled set, and
// show the empty-state line when nothing is enabled.
function applyExperimentalUI(){
    let anyOn = false;
    document.querySelectorAll('#experimentalActive .exp-feature').forEach(block => {
        const on = !!experimentalEnabled[block.dataset.feature];
        block.hidden = !on;
        if(on) anyOn = true;
    });
    document.getElementById('expEmpty').hidden = anyOn;
}

function openExperimental(){
    document.querySelectorAll('#expFeatureChecklist input[type=checkbox]').forEach(cb => {
        cb.checked = !!experimentalEnabled[cb.dataset.feature];
    });
    document.getElementById('experimentalModal').hidden = false;
}

function closeExperimental(){
    document.getElementById('experimentalModal').hidden = true;
}

// Persist the checked set, remount the controls, and turn off the behavior of any
// feature that was just disabled.
async function updateExperimental(){
    const flags = {};
    document.querySelectorAll('#expFeatureChecklist input[type=checkbox]').forEach(cb => {
        flags[cb.dataset.feature] = cb.checked;
    });
    let saved;
    try{
        const res = await fetch('/api/experimental', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(flags),
        });
        if(!res.ok) throw new Error('save failed');
        saved = await res.json();
    }catch(e){
        return;  // leave the panel open so the user can retry
    }
    // Brute Force Resolve has no operational toggle — its enablement alone drives
    // the mail-list org pill — so note whether it flipped to reload the list.
    const resolveChanged = (!!experimentalEnabled['resolve_customer_name']) !== (!!saved['resolve_customer_name']);
    experimentalEnabled = saved;
    let operationalChanged = false;
    EXPERIMENTAL_FEATURES.forEach(f => {
        if(!saved[f.id] && f.isOn()){
            f.turnOff();
            operationalChanged = true;
        }
    });
    applyExperimentalUI();
    closeExperimental();
    if(operationalChanged){
        saveSettings();
        highlightActiveTemplate();
        loadMail();
    }else if(resolveChanged){
        loadMail();
    }
}
