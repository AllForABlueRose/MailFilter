// Regex Compiler: induce a regex matching a set of dragged/typed segments
// (names, emails, links, attachment filenames, plain text), as specific as
// reasonable — never a trivial catch-all.
//
// It is type-aware: when every segment is the same kind (email / URL / file),
// it generalises by that kind's structure (e.g. emails -> local@domain, with
// the shared registered domain kept literal). Otherwise it falls back to a
// generic prefix/suffix + character-class generaliser. The result is always
// verified to match every input; if it somehow doesn't, a literal alternation
// that provably does is emitted instead.

function escapeRegex(s){
    return s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

function regexQuant(n){
    return n === 1 ? '' : `{${n}}`;
}

function regexQuantRange(min, max){
    return min === max ? regexQuant(min) : `{${min},${max}}`;
}

// ----- generic character-class generalisation -----

// 'L' lowercase, 'U' uppercase, 'D' digit, else the literal character.
function charClassOf(ch){
    if(ch >= 'a' && ch <= 'z') return 'L';
    if(ch >= 'A' && ch <= 'Z') return 'U';
    if(ch >= '0' && ch <= '9') return 'D';
    return ch;
}

function classBase(cls){
    if(cls === 'L') return '[a-z]';
    if(cls === 'U') return '[A-Z]';
    if(cls === 'D') return '\\d';
    return escapeRegex(cls);
}

function tokenizeRuns(str){
    const tokens = [];
    for(const ch of str){
        const cls = charClassOf(ch);
        const last = tokens[tokens.length - 1];
        if(last && last.cls === cls){ last.len++; }
        else { tokens.push({cls, len: 1}); }
    }
    return tokens;
}

function generalizeOne(str){
    if(str === '') return '';
    return tokenizeRuns(str).map(t => classBase(t.cls) + regexQuant(t.len)).join('');
}

function structuredPattern(strings){
    const runs = strings.map(tokenizeRuns);
    const len = runs[0].length;
    for(const r of runs){
        if(r.length !== len) return null;
        for(let i = 0; i < len; i++){
            if(r[i].cls !== runs[0][i].cls) return null;
        }
    }
    let out = '';
    for(let i = 0; i < len; i++){
        let min = Infinity, max = 0;
        for(const r of runs){
            min = Math.min(min, r[i].len);
            max = Math.max(max, r[i].len);
        }
        out += classBase(runs[0][i].cls) + regexQuantRange(min, max);
    }
    return out;
}

function generalizeSet(strings){
    const uniq = [...new Set(strings)];
    if(uniq.length === 1) return generalizeOne(uniq[0]);
    const structured = structuredPattern(uniq);
    if(structured !== null) return structured;
    return '(?:' + uniq.map(escapeRegex).join('|') + ')';
}

function commonPrefix(strings){
    let p = strings[0];
    for(const s of strings){
        let i = 0;
        while(i < p.length && i < s.length && p[i] === s[i]) i++;
        p = p.slice(0, i);
        if(!p) break;
    }
    return p;
}

function commonSuffix(strings, prefixLen){
    const first = strings[0];
    let k = first.length;
    for(const s of strings){
        let i = 0;
        while(i < k && i < s.length && first[first.length - 1 - i] === s[s.length - 1 - i]) i++;
        k = i;
    }
    for(const s of strings){ k = Math.min(k, s.length - prefixLen); }
    return k > 0 ? first.slice(first.length - k) : '';
}

// Generic generalisation of a set into an (unanchored) pattern.
function innerPattern(segments){
    if(segments.length === 1) return generalizeOne(segments[0]);
    const prefix = commonPrefix(segments);
    const suffix = commonSuffix(segments, prefix.length);
    const middles = segments.map(s => s.slice(prefix.length, s.length - suffix.length));
    const mid = middles.every(m => m === '') ? '' : generalizeSet(middles);
    return escapeRegex(prefix) + mid + escapeRegex(suffix);
}

// Build a character class `[...]` covering every character seen (category
// ranges where possible), e.g. {a..z, '.'} -> "[a-z.]".
function charClassFromChars(chars){
    let lower = false, upper = false, digit = false;
    const lits = new Set();
    for(const c of chars){
        if(c >= 'a' && c <= 'z') lower = true;
        else if(c >= 'A' && c <= 'Z') upper = true;
        else if(c >= '0' && c <= '9') digit = true;
        else lits.add(c);
    }
    let body = (lower ? 'a-z' : '') + (upper ? 'A-Z' : '') + (digit ? '0-9' : '');
    const hasDash = lits.delete('-');
    for(const c of [...lits].sort()){
        body += /[\\\]^]/.test(c) ? '\\' + c : c;   // escape class-special chars
    }
    if(hasDash) body += '-';                         // hyphen safe at the end
    return '[' + body + ']';
}

// ----- type-aware generalisers -----

// Shared trailing domain labels kept literal; leading (subdomain) labels become
// an optional/repeated class. e.g. backup.nimbustech.io + nimbustech.io ->
// "(?:[a-z]+\.)?nimbustech\.io".
function generalizeDomain(domains){
    const uniq = [...new Set(domains)];
    const labels = uniq.map(d => d.split('.'));
    const minLen = Math.min(...labels.map(a => a.length));
    const common = [];
    for(let i = 1; i <= minLen; i++){
        const label = labels[0][labels[0].length - i];
        if(labels.every(a => a[a.length - i] === label)) common.unshift(label);
        else break;
    }
    if(common.length === 0) return innerPattern(uniq);   // no shared suffix

    const trailing = common.map(escapeRegex).join('\\.');
    const leadCounts = labels.map(a => a.length - common.length);
    const minLead = Math.min(...leadCounts), maxLead = Math.max(...leadCounts);
    if(maxLead === 0) return trailing;

    const leadChars = new Set();
    labels.forEach(a => {
        for(let i = 0; i < a.length - common.length; i++){
            for(const ch of a[i]) leadChars.add(ch);
        }
    });
    const labelPat = charClassFromChars(leadChars) + '+';
    if(minLead === 1 && maxLead === 1){
        return `${labelPat}\\.` + trailing;   // exactly one leading label, no group needed
    }
    const reps = (minLead === 0 && maxLead === 1) ? '?' : `{${minLead},${maxLead}}`;
    return `(?:${labelPat}\\.)${reps}` + trailing;
}

function generalizeEmails(segments){
    const locals = [], domains = [];
    for(const s of segments){
        const at = s.lastIndexOf('@');
        locals.push(s.slice(0, at));
        domains.push(s.slice(at + 1));
    }
    const localChars = new Set();
    locals.forEach(l => { for(const ch of l) localChars.add(ch); });
    return charClassFromChars(localChars) + '+@' + generalizeDomain(domains);
}

function generalizeUrls(segments){
    let urls;
    try{ urls = segments.map(s => new URL(s)); }
    catch(e){ return null; }   // not all parse -> caller falls back to generic
    const schemes = [...new Set(urls.map(u => u.protocol.replace(':', '')))];
    const scheme = schemes.length === 1 ? escapeRegex(schemes[0]) : '[a-z][a-z0-9+.-]*';
    const host = generalizeDomain(urls.map(u => u.hostname));
    // Take the path straight from the original string — URL.pathname normalises a
    // missing path to "/", which wouldn't match a bare-domain input.
    const paths = segments.map(s => {
        const after = s.slice(s.indexOf('://') + 3);
        const slash = after.indexOf('/');
        return slash === -1 ? '' : after.slice(slash);
    });
    const path = paths.every(p => p === paths[0]) ? escapeRegex(paths[0]) : innerPattern(paths);
    return scheme + '://' + host + path;
}

function generalizeFilenames(segments){
    const stems = [], exts = [];
    for(const s of segments){
        const dot = s.lastIndexOf('.');
        stems.push(s.slice(0, dot));
        exts.push(s.slice(dot + 1));
    }
    const ext = exts.every(e => e === exts[0]) ? escapeRegex(exts[0]) : generalizeSet(exts);
    return innerPattern(stems) + '\\.' + ext;
}

function detectType(s){
    if(/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(s)) return 'email';
    if(/^[a-z][a-z0-9+.-]*:\/\//i.test(s)) return 'url';
    if(/^[^/\\@\s]+\.[A-Za-z0-9]{1,8}$/.test(s)) return 'file';
    return 'text';
}

function literalAlternation(segments){
    return '(?:' + [...new Set(segments)].map(escapeRegex).join('|') + ')';
}

// Public: segments -> an UNANCHORED regex string matching them all. Patterns are
// left unanchored so they drop straight into the search fields, which do a
// substring match (re.search) over a combined blob (e.g. sender name + email).
function generateRegex(segments){
    if(!segments.length) return '';
    try{
        let pattern = null;
        const types = new Set(segments.map(detectType));
        if(types.size === 1){
            const t = [...types][0];
            if(t === 'email') pattern = generalizeEmails(segments);
            else if(t === 'url') pattern = generalizeUrls(segments);
            else if(t === 'file') pattern = generalizeFilenames(segments);
        }
        if(pattern === null) pattern = innerPattern(segments);
        // Verify a full match (anchored, case-insensitive — as the search fields
        // use it) to catch generation bugs, then emit the unanchored pattern.
        const full = new RegExp('^(?:' + pattern + ')$', 'i');
        if(segments.every(s => full.test(s))) return pattern;
    }catch(e){ /* fall through */ }
    return literalAlternation(segments);
}

// ----- UI -----
function getRegexSegments(){
    return [...new Set(
        document.getElementById('regexSegments').value
            .split('\n').map(s => s.trim()).filter(Boolean)
    )];
}

function addRegexSegment(value){
    const v = String(value || '').trim();
    if(!v) return;
    const box = document.getElementById('regexSegments');
    box.value = (box.value && !box.value.endsWith('\n')) ? box.value + '\n' + v : box.value + v;
}

function compileRegex(){
    document.getElementById('regexOutput').value = generateRegex(getRegexSegments());
    updateEncaseButton();
}

function clearRegex(){
    document.getElementById('regexSegments').value = '';
    document.getElementById('regexOutput').value = '';
    updateEncaseButton();
}

// One button that reflects whether the output is wrapped in the search-field
// regex delimiters: 🥚 Encase adds them, 🐣 Decase removes them.
function isEncased(s){
    return s.startsWith('<{(') && s.endsWith(')}>');
}

function updateEncaseButton(){
    const out = document.getElementById('regexOutput').value;
    const btn = document.getElementById('encaseBtn');
    btn.disabled = !out;
    const encased = isEncased(out);
    btn.textContent = encased ? '📭 Decase' : '📦 Encase';
    btn.title = encased ? 'Remove the <{( … )}> casing'
                        : 'Wrap the regex in <{( … )}> for a search field';
}

function toggleEncase(){
    const out = document.getElementById('regexOutput');
    if(!out.value) return;
    out.value = isEncased(out.value) ? out.value.slice(3, -3) : '<{(' + out.value + ')}>';
    updateEncaseButton();
}
