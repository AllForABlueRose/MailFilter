// Workshop -> Calendar screen. Top half: a month grid whose days accept a
// workspace file dragged from the bottom half. Bottom half: today's workspace
// files (from /api/workspace/files), each a drag source. Dropping a file on a day
// pins it (a copy goes to the server's limbo folder); on the day it lands the
// server materializes it into that day's workspace on startup.
//
// Reuses el() and vaultApi() from workshop.js (shared global scope) and globals
// from state.js (calendarYear, calendarMonth, calendarPins, calendarWorkspaceFiles,
// calendarWorkspaceExists, pendingPinFilename, pendingPinDate). Every server/user
// string is inserted as DOM text, never HTML.

const CAL_DRAG_TYPE = "text/x-mailfilter-pinfile";
const CAL_WEEKDAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const CAL_MONTHS = ["January", "February", "March", "April", "May", "June", "July",
    "August", "September", "October", "November", "December"];

function _pad2(n){ return (n < 10 ? "0" : "") + n; }

// A local "today" (YYYY-MM-DD), matching how the server names dated folders.
function _todayStr(){
    const d = new Date();
    return d.getFullYear() + "-" + _pad2(d.getMonth() + 1) + "-" + _pad2(d.getDate());
}

function _dayStr(year, month0, day){
    return year + "-" + _pad2(month0 + 1) + "-" + _pad2(day);
}

// ----- load + render -----

async function loadCalendar(){
    if(!calendarYear){
        const now = new Date();
        calendarYear = now.getFullYear();
        calendarMonth = now.getMonth();
    }
    const pins = await vaultApi("/api/calendar/pins");
    calendarPins = (pins.json && pins.json.pins) || [];
    const files = await vaultApi("/api/workspace/files");
    const data = files.json || {};
    calendarWorkspaceExists = !!data.exists;
    calendarWorkspaceFiles = data.files || [];
    renderCalendar();
    renderCalendarWorkspace();
}

function calendarShiftMonth(delta){
    calendarMonth += delta;
    while(calendarMonth < 0){ calendarMonth += 12; calendarYear -= 1; }
    while(calendarMonth > 11){ calendarMonth -= 12; calendarYear += 1; }
    renderCalendar();
}

function _pinsByDate(){
    const map = {};
    calendarPins.forEach(p => {
        (map[p.date] = map[p.date] || []).push(p);
    });
    return map;
}

function renderCalendar(){
    const wrap = document.getElementById("calendarGrid");
    if(!wrap) return;
    wrap.innerHTML = "";

    const bar = el("div", "cal-navbar");
    const prev = el("button", "flip-btn", "◀");
    prev.type = "button"; prev.title = "Previous month";
    prev.onclick = () => calendarShiftMonth(-1);
    const label = el("span", "cal-month-label", CAL_MONTHS[calendarMonth] + " " + calendarYear);
    const next = el("button", "flip-btn", "▶");
    next.type = "button"; next.title = "Next month";
    next.onclick = () => calendarShiftMonth(1);
    bar.append(prev, label, next);
    wrap.appendChild(bar);

    const grid = el("div", "cal-grid");
    CAL_WEEKDAYS.forEach(w => grid.appendChild(el("div", "cal-weekday", w)));

    const firstWeekday = new Date(calendarYear, calendarMonth, 1).getDay();
    const daysInMonth = new Date(calendarYear, calendarMonth + 1, 0).getDate();
    for(let i = 0; i < firstWeekday; i++){
        grid.appendChild(el("div", "cal-day cal-day-blank"));
    }
    const today = _todayStr();
    const byDate = _pinsByDate();
    for(let day = 1; day <= daysInMonth; day++){
        const dateStr = _dayStr(calendarYear, calendarMonth, day);
        grid.appendChild(buildDayCell(day, dateStr, today, byDate[dateStr] || []));
    }
    wrap.appendChild(grid);
}

function buildDayCell(day, dateStr, today, pins){
    const cell = el("div", "cal-day");
    if(dateStr === today){ cell.classList.add("cal-day-today"); }
    // A file only materializes when its day *arrives* at server start, so pinning
    // to a past day would never fire — those days are not drop targets.
    const isPast = dateStr < today;
    if(isPast){ cell.classList.add("cal-day-past"); }

    cell.appendChild(el("div", "cal-day-num", String(day)));
    const chips = el("div", "cal-day-pins");
    pins.forEach(p => chips.appendChild(buildPinChip(p)));
    cell.appendChild(chips);

    if(!isPast){
        cell.addEventListener("dragover", e => {
            if(e.dataTransfer.types.includes(CAL_DRAG_TYPE)){
                e.preventDefault();
                cell.classList.add("cal-day-drop");
            }
        });
        cell.addEventListener("dragleave", () => cell.classList.remove("cal-day-drop"));
        cell.addEventListener("drop", e => {
            const filename = e.dataTransfer.getData(CAL_DRAG_TYPE);
            cell.classList.remove("cal-day-drop");
            if(!filename) return;
            e.preventDefault();
            openCalendarPin(filename, dateStr);
        });
    }
    return cell;
}

function buildPinChip(pin){
    const chip = el("div", "cal-pin-chip" + (pin.materialized ? " cal-pin-done" : ""), null);
    const label = (pin.materialized ? "✓ " : "📎 ") + (pin.filename || "file");
    chip.appendChild(el("span", "cal-pin-name", label));
    let title = pin.filename || "file";
    if(pin.description){ title += " — " + pin.description; }
    if(pin.materialized){ title += " (already placed in its day's workspace)"; }
    title += "\nClick to remove this pin.";
    chip.title = title;
    chip.onclick = () => removePin(pin);
    return chip;
}

async function removePin(pin){
    const msg = pin.materialized
        ? "Remove this pin record? The file already placed in its day's workspace is kept."
        : "Remove this pin? Its held copy in limbo will be deleted.";
    if(!confirm(msg)) return;
    const res = await vaultApi("/api/calendar/pins/" + pin.id, "DELETE");
    if(res.ok){ await loadCalendar(); }
}

// ----- bottom half: today's workspace files (drag sources) -----

function renderCalendarWorkspace(){
    const wrap = document.getElementById("calendarWorkspace");
    if(!wrap) return;
    wrap.innerHTML = "";
    wrap.appendChild(el("h4", "cal-ws-title", "🗂 Today's workspace"));

    if(!calendarWorkspaceExists){
        const note = el("p", "cal-ws-empty",
            "Today's workspace does not exist yet. Create it, then download or "
            + "export files into it to pin them to a day.");
        wrap.appendChild(note);
        const btn = el("button", "auto-save-btn", "Create today's workspace");
        btn.type = "button"; btn.onclick = createTodayWorkspace;
        wrap.appendChild(btn);
        return;
    }
    if(!calendarWorkspaceFiles.length){
        wrap.appendChild(el("p", "cal-ws-empty",
            "No files in today's workspace yet. Download attachments or export a "
            + "report to fill it, then drag a file onto a day above."));
        return;
    }
    wrap.appendChild(el("p", "cal-ws-hint", "Drag a file onto a day above to pin it."));
    const list = el("div", "cal-ws-files");
    calendarWorkspaceFiles.forEach(f => list.appendChild(buildWorkspaceFileCard(f)));
    wrap.appendChild(list);
}

function buildWorkspaceFileCard(file){
    const card = el("div", "cal-ws-file");
    card.draggable = true;
    card.addEventListener("dragstart", e => {
        e.dataTransfer.setData(CAL_DRAG_TYPE, file.name);
        e.dataTransfer.effectAllowed = "copy";
        card.classList.add("dragging");
    });
    card.addEventListener("dragend", () => card.classList.remove("dragging"));

    const icon = file.kind === "zip" ? "🗜" : (file.kind === "excel" ? "📊" : "📄");
    card.appendChild(el("span", "cal-ws-file-icon", icon));
    card.appendChild(el("span", "cal-ws-file-name", file.name));
    if(file.org_name){ card.appendChild(el("span", "cal-ws-file-org", file.org_name)); }
    return card;
}

async function createTodayWorkspace(){
    const res = await vaultApi("/api/calendar/create-workspace", "POST");
    if(res.ok){ await loadCalendar(); }
}

// ----- pin modal (optional description) -----

function openCalendarPin(filename, dateStr){
    pendingPinFilename = filename;
    pendingPinDate = dateStr;
    document.getElementById("calendarPinSummary").textContent =
        "Pin “" + filename + "” to " + dateStr + ".";
    document.getElementById("calendarPinDescription").value = "";
    document.getElementById("calendarPinError").hidden = true;
    document.getElementById("calendarPinModal").hidden = false;
}

function closeCalendarPin(){
    pendingPinFilename = "";
    pendingPinDate = "";
    const modal = document.getElementById("calendarPinModal");
    if(modal){ modal.hidden = true; }
}

async function confirmCalendarPin(){
    const description = document.getElementById("calendarPinDescription").value;
    const res = await vaultApi("/api/calendar/pins", "POST", {
        date: pendingPinDate,
        filename: pendingPinFilename,
        description: description,
    });
    if(!res.ok){
        const err = document.getElementById("calendarPinError");
        err.textContent = (res.json && res.json.error) || "Could not pin this file.";
        err.hidden = false;
        return;
    }
    closeCalendarPin();
    await loadCalendar();
}
