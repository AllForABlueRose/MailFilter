// App-shell view router (modeled on the FlaskTaskScheduler tab-header).
//
// Each top-level view is a `.view` element with id `view-<name>`; the matching
// `.appnav-item[data-tab="<name>"]` button in the header activates it. Exactly
// one view is visible at a time (the rest carry `.view-hidden`); switching
// cross-fades via `.view-fading`. This is a single-route SPA, so switching is
// purely client-side — no history/URL changes.

const TAB_TITLES = {
    mailfilter: "Mail Analyzer 3.0",
    automations: "Automations",
    customers: "Customer Management",
    composer: "Composer",
    press: "Press",
    workshop: "Workshop",
};

function setActiveTab(name){
    document.querySelectorAll(".appnav-item[data-tab]").forEach(btn => {
        const on = btn.dataset.tab === name;
        btn.classList.toggle("active", on);
        if(on){ btn.setAttribute("aria-current", "page"); }
        else { btn.removeAttribute("aria-current"); }
    });
    if(TAB_TITLES[name]){ document.title = TAB_TITLES[name]; }
}

// Per-view refresh when a tab becomes visible.
function onTabEntered(name){
    if(name === "automations" && typeof loadAutomations === "function"){
        loadAutomations();
    }
    // Loaded on tab-enter only (the directory is recomputed server-side from the
    // whole mail cache) — deliberately not on the 30s mail poll.
    if(name === "customers" && typeof loadCustomers === "function"){
        loadCustomers();
    }
    // Composer: the templates, the function palette, the examples, and the first
    // page of cache mail (the rest is fetched as the picker is scrolled).
    if(name === "composer" && typeof loadComposer === "function"){
        loadComposer();
    }
    // Press: refresh the reply-template list (and the shared-mailbox banner) on entry
    // — a template authored in Composer must show up here without a reload.
    if(name === "press" && typeof loadPressTemplates === "function"){
        loadPressTemplates();
    }
    // Always land on the Workshop hub (its card menu); re-check the vault lock
    // state (it can auto-lock) and re-render so the vault card is ready when opened.
    if(name === "workshop"){
        if(typeof resetWorkshop === "function"){ resetWorkshop(); }
        if(typeof loadVault === "function"){ loadVault(); }
    }
}

function switchTab(name){
    const next = document.getElementById("view-" + name);
    if(!next) return;
    setActiveTab(name);
    const current = document.querySelector(".view:not(.view-hidden)");
    if(current === next){ onTabEntered(name); return; }
    if(current){
        current.classList.add("view-fading");
        setTimeout(() => {
            current.classList.add("view-hidden");
            current.classList.remove("view-fading");
            next.classList.add("view-fading");
            next.classList.remove("view-hidden");
            requestAnimationFrame(() => requestAnimationFrame(() => {
                next.classList.remove("view-fading");
            }));
            onTabEntered(name);
        }, 150);
    } else {
        next.classList.remove("view-hidden");
        onTabEntered(name);
    }
}

function initTabs(){
    document.querySelectorAll(".appnav-item[data-tab]").forEach(btn => {
        btn.addEventListener("click", () => switchTab(btn.dataset.tab));
    });
    const initial = document.querySelector(".view:not(.view-hidden)");
    setActiveTab(initial ? initial.id.replace("view-", "") : "mailfilter");
}
