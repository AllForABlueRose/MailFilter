// Entry point: wire global listeners, restore the saved search, start polling.
// Loaded last, after every function/global the others define.

async function init(){
    // Wire the app-shell header tabs (view switching).
    initTabs();

    // Hold-to-reveal real org names in Customer Management (button + ` key).
    initOrgNameReveal();

    // Hold-Z reveal for Key Vault keys, and the startup Key Vault login screen.
    initVaultReveal();
    showVaultLogin();

    // Close the thread / automation popups on a backdrop click or Escape.
    document.getElementById('threadModal').addEventListener('click', e => {
        if(e.target.id === 'threadModal'){ closeThread(); }
    });
    document.getElementById('automationModal').addEventListener('click', e => {
        if(e.target.id === 'automationModal'){ closeAutomationBuilder(); }
    });
    document.getElementById('bulkTemplateModal').addEventListener('click', e => {
        if(e.target.id === 'bulkTemplateModal'){ closeBulkTemplateEditor(); }
    });
    document.getElementById('passwordModal').addEventListener('click', e => {
        if(e.target.id === 'passwordModal'){ closePasswordSettings(); }
    });
    document.getElementById('experimentalModal').addEventListener('click', e => {
        if(e.target.id === 'experimentalModal'){ closeExperimental(); }
    });
    document.getElementById('suspectedCustomersModal').addEventListener('click', e => {
        if(e.target.id === 'suspectedCustomersModal'){ closeSuspectedCustomers(); }
    });
    document.getElementById('vaultEntryModal').addEventListener('click', e => {
        if(e.target.id === 'vaultEntryModal'){ closeVaultEntry(); }
    });
    document.getElementById('calendarPinModal').addEventListener('click', e => {
        if(e.target.id === 'calendarPinModal'){ closeCalendarPin(); }
    });
    document.addEventListener('keydown', e => {
        if(e.key === 'Escape'){
            // If a modal is open, Escape closes it; otherwise, on a Workshop
            // sub-screen it steps back to the hub (the Workshop "Back" binding).
            const modalIds = ['threadModal','automationModal','bulkTemplateModal',
                'passwordModal','experimentalModal','suspectedCustomersModal',
                'vaultEntryModal','calendarPinModal'];
            const anyModalOpen = modalIds.some(id => {
                const m = document.getElementById(id); return m && !m.hidden;
            });
            closeThread(); closeAutomationBuilder(); closeBulkTemplateEditor();
            closePasswordSettings(); closeExperimental(); closeSuspectedCustomers();
            closeVaultEntry(); closeCalendarPin();
            const wsView = document.getElementById('view-workshop');
            const wsActive = wsView && !wsView.classList.contains('view-hidden');
            if(!anyModalOpen && wsActive && workshopScreen !== 'hub'){ workshopBack(); }
            return;
        }
        if(e.key !== 'Enter') return;
        // Enter runs the search from anywhere (even after dragging moved focus
        // out of the sidebar). Let buttons/links/textareas keep their own Enter,
        // and don't search while a popup is open.
        const tag = e.target.tagName;
        if(tag === 'BUTTON' || tag === 'A' || tag === 'TEXTAREA') return;
        if(!document.getElementById('threadModal').hidden) return;
        if(!document.getElementById('automationModal').hidden) return;
        if(!document.getElementById('bulkTemplateModal').hidden) return;
        if(!document.getElementById('passwordModal').hidden) return;
        if(!document.getElementById('experimentalModal').hidden) return;
        if(!document.getElementById('suspectedCustomersModal').hidden) return;
        if(!document.getElementById('vaultEntryModal').hidden) return;
        if(!document.getElementById('calendarPinModal').hidden) return;
        e.preventDefault();
        applyFilters();
    });

    // Workspace tray accepts mail items dragged from the list.
    const tray = document.getElementById('tray');
    tray.addEventListener('dragover', e => {
        e.preventDefault();
        tray.classList.add('drag-over');
    });
    tray.addEventListener('dragleave', e => {
        if(e.target === tray){ tray.classList.remove('drag-over'); }
    });
    tray.addEventListener('drop', e => {
        e.preventDefault();
        tray.classList.remove('drag-over');
        addToTray(e.dataTransfer.getData('text/x-mailfilter-mailid'));
    });

    // "Collect matching mails": the wheel opens on hover and stays open while the
    // pointer is over either the button or the wheel (both live in #collectWrap).
    const collectWrap = document.getElementById('collectWrap');
    collectWrap.addEventListener('mouseenter', openCollectWheel);
    collectWrap.addEventListener('mouseleave', closeCollectWheel);
    const collectWheel = document.getElementById('collectWheel');
    collectWheel.addEventListener('wheel', cycleCollect, {passive: false});
    collectWheel.addEventListener('click', collectFocused);
    collectWheel.addEventListener('dblclick', collectAllDisplayed);  // collect every displayed mail

    // Scroll over the 🔗 / ⬇ tray buttons to cycle the "only new" variant (starred
    // icon): each scroll gesture — up or down — flips to the next mode. Debounced so
    // a trackpad emitting many wheel events per gesture counts as a single step,
    // keeping the landing state predictable.
    let lastTrayModeWheel = 0;
    const cycleTrayMode = flip => {
        const now = Date.now();
        if(now - lastTrayModeWheel < 200) return;
        lastTrayModeWheel = now;
        flip();
        syncTrayModeButtons();
    };
    const trayLinksBtn = document.getElementById('trayLinksBtn');
    trayLinksBtn.addEventListener('wheel', e => {
        e.preventDefault();
        cycleTrayMode(() => { trayLinksOnlyNew = !trayLinksOnlyNew; });
    }, {passive: false});
    const trayDownloadBtn = document.getElementById('trayDownloadBtn');
    trayDownloadBtn.addEventListener('wheel', e => {
        e.preventDefault();
        cycleTrayMode(() => { trayDownloadOnlyNew = !trayDownloadOnlyNew; });
    }, {passive: false});

    // The regex compiler accepts dragged segments (people, links, filenames,
    // plain text), each appended as its own line.
    const regexBox = document.getElementById('regexSegments');
    regexBox.addEventListener('dragover', e => { e.preventDefault(); regexBox.classList.add('drop-target'); });
    regexBox.addEventListener('dragleave', () => regexBox.classList.remove('drop-target'));
    regexBox.addEventListener('drop', e => {
        e.preventDefault();
        regexBox.classList.remove('drop-target');
        addRegexSegment(
            e.dataTransfer.getData('text/x-mailfilter-segment')
            || e.dataTransfer.getData('text/x-mailfilter-person')
            || e.dataTransfer.getData('text/uri-list')
            || e.dataTransfer.getData('text/plain')
        );
    });

    // Person fields accept a dragged name/email, appended with ", ".
    document.querySelectorAll('.person-drop').forEach(input => {
        input.addEventListener('dragover', e => {
            if(e.dataTransfer.types.includes('text/x-mailfilter-person')){
                e.preventDefault();
                input.classList.add('drop-target');
            }
        });
        input.addEventListener('dragleave', () => input.classList.remove('drop-target'));
        input.addEventListener('drop', e => {
            const value = e.dataTransfer.getData('text/x-mailfilter-person');
            if(!value) return;
            e.preventDefault();
            input.classList.remove('drop-target');
            const current = input.value.trim();
            input.value = current ? current + ', ' + value : value;
        });
    });

    // The compiled regex (or any plain text) can be dragged into a search field,
    // appended with ", " (or input directly when the field is empty).
    const regexOut = document.getElementById('regexOutput');
    regexOut.addEventListener('dragstart', e => {
        if(!regexOut.value) return;
        e.dataTransfer.setData('text/x-mailfilter-regex', regexOut.value);
        e.dataTransfer.setData('text/plain', regexOut.value);
        e.dataTransfer.effectAllowed = 'copy';
    });
    document.querySelectorAll('.sidebar input[type="text"]').forEach(input => {
        input.addEventListener('dragover', e => {
            if(e.dataTransfer.types.includes('text/x-mailfilter-regex')
               || e.dataTransfer.types.includes('text/plain')){
                e.preventDefault();
                input.classList.add('drop-target');
            }
        });
        input.addEventListener('dragleave', () => input.classList.remove('drop-target'));
        input.addEventListener('drop', e => {
            const value = e.dataTransfer.getData('text/x-mailfilter-regex')
                       || e.dataTransfer.getData('text/plain');
            if(!value) return;
            e.preventDefault();
            input.classList.remove('drop-target');
            const current = input.value.trim();
            input.value = current ? current + ', ' + value : value;
        });
    });

    await restoreSettings();   // repopulate the sidebar from the saved search
    loadTemplates();           // populate the search-template dropdown
    loadExperimental();        // mount enabled experimental-feature controls
    loadMail();
    setInterval(loadMail, 30000);
}

init();
