// Smart Password Detection: the settings popup (patterns + rules) and the
// on-demand scan. The sidebar 🔑 toggle and badge live in search.js / render.js;
// this file owns the modal and the /api/password-settings + /api/passwords/scan
// calls. The list is NOT rescanned on every search — the user runs a scan
// explicitly (the 🔍 Scan button), and the badges/filter reflect the last scan.

// Mirror config.PASSWORD_LENGTH_FLOOR / _CEIL. The server clamps regardless;
// these just bound the sliders.
const PWD_LENGTH_FLOOR = 1;
const PWD_LENGTH_CEIL = 128;
// Mirror config.PASSWORD_PLACEHOLDER. The token a component drops where the
// password sits; PWD_PLACEHOLDER_RE matches any <{( label )}> form.
const PWD_PLACEHOLDER = "<{(password_value)}>";
const PWD_PLACEHOLDER_RE = /<\{\(.*?\)\}>/;

function openPasswordSettings(){
    fetch('/api/password-settings')
        .then(r => r.json())
        .then(settings => {
            passwordSettings = settings;
            populatePasswordForm(settings);
            document.getElementById('pwdFormError').hidden = true;
            document.getElementById('passwordModal').hidden = false;
        })
        .catch(() => {});
}

function closePasswordSettings(){
    document.getElementById('passwordModal').hidden = true;
}

function populatePasswordForm(settings){
    const rules = settings.rules || {};
    document.getElementById('pwdRuleJapanese').checked = !!rules.no_japanese;
    document.getElementById('pwdRuleLink').checked = !!rules.no_link;
    document.getElementById('pwdRuleRepeating').checked = !!rules.no_repeating;
    document.getElementById('pwdRuleFile').checked = !!rules.no_file;

    const min = document.getElementById('pwdMinLength');
    const max = document.getElementById('pwdMaxLength');
    min.min = max.min = PWD_LENGTH_FLOOR;
    min.max = max.max = PWD_LENGTH_CEIL;
    min.value = rules.min_length != null ? rules.min_length : 8;
    max.value = rules.max_length != null ? rules.max_length : 32;
    document.getElementById('pwdMinValue').textContent = min.value;
    document.getElementById('pwdMaxValue').textContent = max.value;

    renderPasswordPatterns(settings.patterns || []);
}

function renderPasswordPatterns(patterns){
    const box = document.getElementById('pwdPatternRows');
    box.innerHTML = '';
    if(!patterns.length){
        addPasswordPattern();
        return;
    }
    patterns.forEach(p => box.appendChild(makePatternRow(p)));
    renumberPasswordComponents();
}

// Label every component with its 1-based position. Called after any add/remove
// so the numbers (and the validation/scan messages that cite them) stay current.
function renumberPasswordComponents(){
    document.querySelectorAll('#pwdPatternRows .pwd-pattern-num')
        .forEach((el, i) => { el.textContent = 'Component ' + (i + 1); });
}

// One editable component card: enabled toggle + name + remove in the header, a
// multi-line context box (with an "insert marker" helper), and the optional
// per-pattern value regex below. All user text is set via .value (inert), never
// innerHTML, so nothing a saved component holds can inject.
function makePatternRow(pattern){
    const row = document.createElement('div');
    row.className = 'pwd-pattern-row';

    const head = document.createElement('div');
    head.className = 'pwd-pattern-head';

    const enabled = document.createElement('input');
    enabled.type = 'checkbox';
    enabled.className = 'pwd-pattern-enabled';
    enabled.checked = pattern.enabled !== false;
    enabled.title = 'Enable this component';
    head.appendChild(enabled);

    // Components are identified by their position; the number is filled in by
    // renumberPasswordComponents() and kept current as rows are added/removed.
    const num = document.createElement('span');
    num.className = 'pwd-pattern-num';
    head.appendChild(num);

    const spacer = document.createElement('span');
    spacer.className = 'pwd-pattern-spacer';
    head.appendChild(spacer);

    const marker = document.createElement('button');
    marker.type = 'button';
    marker.className = 'flip-btn pwd-marker-btn';
    marker.textContent = '+ marker';
    marker.title = 'Insert the <{(password_value)}> marker at the cursor';
    head.appendChild(marker);

    const remove = document.createElement('button');
    remove.type = 'button';
    remove.className = 'flip-btn pwd-pattern-del';
    remove.textContent = '✕';
    remove.title = 'Remove this component';
    remove.onclick = () => { row.remove(); renumberPasswordComponents(); };
    head.appendChild(remove);

    row.appendChild(head);

    const context = document.createElement('textarea');
    context.className = 'pwd-pattern-template';
    context.rows = 2;
    context.placeholder = 'password: ' + PWD_PLACEHOLDER;
    context.maxLength = 500;
    context.value = pattern.template || '';
    row.appendChild(context);
    marker.onclick = () => insertPasswordMarker(context);

    const valueRow = document.createElement('div');
    valueRow.className = 'pwd-value-row';
    const valueLabel = document.createElement('span');
    valueLabel.className = 'pwd-value-label';
    valueLabel.textContent = 'Password pattern:';
    valueRow.appendChild(valueLabel);
    const value = document.createElement('input');
    value.type = 'text';
    value.className = 'pwd-pattern-value';
    // Empty = greyed "*" generic match (the placeholder shows it; CSS greys it).
    value.placeholder = '*   generic password string detection';
    value.maxLength = 500;
    value.value = pattern.value_regex || '';
    valueRow.appendChild(value);
    row.appendChild(valueRow);

    return row;
}

// Insert the marker token at the textarea's cursor (or append it), then refocus.
function insertPasswordMarker(textarea){
    const start = textarea.selectionStart != null ? textarea.selectionStart : textarea.value.length;
    const end = textarea.selectionEnd != null ? textarea.selectionEnd : textarea.value.length;
    textarea.value = textarea.value.slice(0, start) + PWD_PLACEHOLDER + textarea.value.slice(end);
    const caret = start + PWD_PLACEHOLDER.length;
    textarea.focus();
    textarea.setSelectionRange(caret, caret);
}

function addPasswordPattern(){
    document.getElementById('pwdPatternRows')
        .appendChild(makePatternRow({template: '', value_regex: '', enabled: true}));
    renumberPasswordComponents();
}

// Read the form back into the settings shape the API expects.
function collectPasswordSettings(){
    const patterns = [];
    document.querySelectorAll('#pwdPatternRows .pwd-pattern-row').forEach(row => {
        const template = row.querySelector('.pwd-pattern-template').value;
        if(!template.trim()) return;  // blank components are dropped (server too)
        patterns.push({
            template: template,
            value_regex: row.querySelector('.pwd-pattern-value').value.trim(),
            enabled: row.querySelector('.pwd-pattern-enabled').checked,
        });
    });
    return {
        patterns: patterns,
        rules: {
            no_japanese: document.getElementById('pwdRuleJapanese').checked,
            no_link: document.getElementById('pwdRuleLink').checked,
            no_repeating: document.getElementById('pwdRuleRepeating').checked,
            no_file: document.getElementById('pwdRuleFile').checked,
            min_length: parseInt(document.getElementById('pwdMinLength').value, 10),
            max_length: parseInt(document.getElementById('pwdMaxLength').value, 10),
        },
    };
}

// Every enabled component needs exactly one marker, or it can't say where the
// password is. Walks the visible rows so the cited number matches the on-screen
// "Component N". Returns an error string to show, or null when all are fine.
function validatePasswordComponents(){
    const rows = document.querySelectorAll('#pwdPatternRows .pwd-pattern-row');
    for(let i = 0; i < rows.length; i++){
        const row = rows[i];
        if(!row.querySelector('.pwd-pattern-enabled').checked) continue;
        const template = row.querySelector('.pwd-pattern-template').value;
        if(!template.trim()) continue;  // blank rows are dropped, not validated
        const hits = template.match(/<\{\(.*?\)\}>/g) || [];
        if(hits.length === 0){
            return `Component ${i + 1} has no ${PWD_PLACEHOLDER} marker — add one where the password is.`;
        }
        if(hits.length > 1){
            return `Component ${i + 1} has more than one marker — keep just one.`;
        }
    }
    return null;
}

async function persistPasswordSettings(){
    const settings = collectPasswordSettings();
    const problem = validatePasswordComponents();
    if(problem){
        showPasswordFormError(problem);
        throw new Error(problem);
    }
    const res = await fetch('/api/password-settings', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(settings),
    });
    if(!res.ok) throw new Error('save failed');
    passwordSettings = await res.json();
    return passwordSettings;
}

async function savePasswordSettings(){
    try{
        await persistPasswordSettings();
        closePasswordSettings();
        setPasswordStatus('Settings saved. Hit 🔍 Scan to apply them.');
    }catch(e){
        // A validation problem already surfaced its own message; only override
        // for an actual save failure.
        if(document.getElementById('pwdFormError').hidden){
            showPasswordFormError('Could not save the settings.');
        }
    }
}

async function saveAndScanPasswords(){
    try{
        await persistPasswordSettings();
        closePasswordSettings();
        await runPasswordScan();
    }catch(e){
        if(document.getElementById('pwdFormError').hidden){
            showPasswordFormError('Could not save the settings.');
        }
    }
}

// Scan every cached mail for passwords, then refresh the list so badges (and the
// 🔑 filter, if on) reflect the new results.
async function runPasswordScan(){
    setPasswordStatus('Scanning…');
    try{
        const res = await fetch('/api/passwords/scan', {method: 'POST'});
        const data = await res.json();
        let msg = `🔑 ${data.flagged} of ${data.scanned} mails have a password.`;
        if(data.pattern_errors && data.pattern_errors.length){
            const nums = data.pattern_errors.map(e => '#' + e.component).join(', ');
            msg += ` ⚠ Check component(s): ${nums}`;
        }
        // Detected passwords are auto-saved into each sender's Key Vault. When the
        // vault is locked the writes are deferred until it's unlocked.
        if(data.vault_locked && data.vault_pending){
            msg += ` 🔒 ${data.vault_pending} key(s) queued — unlock the Key Vault `
                + `(Workshop → Key Vaults) to record them.`;
        } else if(data.vault_captured){
            msg += ` 🔒 ${data.vault_captured} key(s) saved to the Key Vault.`;
        }
        setPasswordStatus(msg);
        loadMail();
    }catch(e){
        setPasswordStatus('Scan failed.');
    }
}

function setPasswordStatus(text){
    document.getElementById('passwordScanStatus').textContent = text;
}

function showPasswordFormError(text){
    const el = document.getElementById('pwdFormError');
    el.textContent = text;
    el.hidden = false;
}
