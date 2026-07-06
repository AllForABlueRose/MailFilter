// Search templates (presets): a named, switchable collection of saved searches,
// each stored server-side as a PNG image file and exportable/importable as one.
// Relies on globals from search.js (currentSettings, applySettings, saveSettings,
// loadMail, SETTINGS_FIELDS) and templateBodies from state.js.

// Fields a template deliberately doesn't carry (mirrors
// settings_store.TEMPLATE_EXCLUDED_FIELDS): the date range and the width-
// normalization toggle are per-session choices, so they're ignored when matching
// the active template and re-applied from the live search after switching.
const TEMPLATE_EXCLUDED_FIELDS = ['start', 'end', 'normalize_width',
                                  'append_customer_name'];

// Fetch the template list and render the dropdown. Called once at startup.
async function loadTemplates(){
    try{
        const res = await fetch('/api/templates');
        renderTemplates(await res.json());
    }catch(e){
        // Leave the dropdown at its empty placeholder.
    }
}

// Rebuild the dropdown from a snapshot ({names, templates}). Options are created
// via new Option(text) so a template name is never interpolated into HTML. The
// active selection is derived from the live search, not stored server-side.
function renderTemplates(snapshot){
    templateBodies = snapshot.templates || {};
    const select = document.getElementById('templateSelect');
    select.innerHTML = '';
    select.appendChild(new Option('— Templates —', ''));
    (snapshot.names || []).forEach(name => select.appendChild(new Option(name, name)));
    highlightActiveTemplate();
}

// Select whichever template matches the current search (or the placeholder if
// none does), and sync the export/delete buttons. Called whenever the form or
// the template set changes, so the dropdown always reflects the live search.
function highlightActiveTemplate(){
    const current = currentSettings();
    let active = '';
    for(const [name, body] of Object.entries(templateBodies)){
        if(settingsEqual(current, body)){ active = name; break; }
    }
    document.getElementById('templateSelect').value = active;
    syncTemplateButtons();
}

function settingsEqual(a, b){
    if(!!a.resources !== !!b.resources) return false;
    for(const key of Object.keys(SETTINGS_FIELDS)){
        if(TEMPLATE_EXCLUDED_FIELDS.includes(key)) continue;  // not part of a template
        if((a[key] || '') !== (b[key] || '')) return false;
    }
    return true;
}

// Export/delete act on the selected template, so they're disabled with none.
function syncTemplateButtons(){
    const selected = !!document.getElementById('templateSelect').value;
    document.getElementById('templateExport').disabled = !selected;
    document.getElementById('templateDelete').disabled = !selected;
}

// Switch to the chosen template: load its settings into the sidebar, make them
// the live last-used search, and run it. The date range and normalize-width
// toggle aren't part of a template, so the live values are kept across the switch.
function switchTemplate(){
    const name = document.getElementById('templateSelect').value;
    syncTemplateButtons();
    if(!name) return;
    const settings = templateBodies[name];
    if(!settings) return;
    const preserved = currentSettings();
    applySettings(settings);
    applyPreservedTemplateFields(preserved);
    saveSettings();
    loadMail();
}

// Re-apply the excluded (per-session) fields after a template loaded its own. The
// date range comes from the text inputs; normalize-width is a toggle global.
function applyPreservedTemplateFields(preserved){
    document.getElementById(SETTINGS_FIELDS.start).value = preserved.start || '';
    document.getElementById(SETTINGS_FIELDS.end).value = preserved.end || '';
    normalizeWidth = !!preserved.normalize_width;
    syncNormalizeWidthButton();
    appendCustomerName = !!preserved.append_customer_name;
    syncAppendCustomerNameButton();
}

async function saveTemplate(){
    const name = (prompt('Save the current search as a template named:') || '').trim();
    if(!name) return;
    const res = await fetch('/api/templates', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({name, settings: currentSettings()}),
    });
    if(res.ok){
        renderTemplates(await res.json());
    }else{
        alert('Could not save the template.');
    }
}

async function deleteTemplate(){
    const name = document.getElementById('templateSelect').value;
    if(!name) return;
    if(!confirm(`Delete the template "${name}"?`)) return;
    const res = await fetch('/api/templates/' + encodeURIComponent(name), {method: 'DELETE'});
    if(res.ok){ renderTemplates(await res.json()); }
}

// Export the selected template to a PNG download.
async function exportTemplate(){
    const name = document.getElementById('templateSelect').value;
    if(!name) return;
    const res = await fetch('/api/templates/export', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({name}),
    });
    if(!res.ok) return;
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = name + '.png';
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
}

// Import a template from a chosen PNG, then switch to it so its search applies.
async function importTemplate(input){
    const file = input.files && input.files[0];
    if(!file) return;
    const form = new FormData();
    form.append('file', file);
    const res = await fetch('/api/templates/import', {method: 'POST', body: form});
    input.value = '';  // let the same file be re-selected later
    if(!res.ok){
        alert('Could not import that file — it is not a valid template image.');
        return;
    }
    const data = await res.json();
    renderTemplates(data);
    document.getElementById('templateSelect').value = data.imported;
    switchTemplate();
}
