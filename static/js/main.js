// Entry point: wire global listeners, restore the saved search, start polling.
// Loaded last, after every function/global the others define.

async function init(){
    // Close the thread popup on a backdrop click or Escape.
    document.getElementById('threadModal').addEventListener('click', e => {
        if(e.target.id === 'threadModal'){ closeThread(); }
    });
    document.addEventListener('keydown', e => {
        if(e.key === 'Escape'){ closeThread(); return; }
        if(e.key !== 'Enter') return;
        // Enter runs the search from anywhere (even after dragging moved focus
        // out of the sidebar). Let buttons/links/textareas keep their own Enter,
        // and don't search while the thread popup is open.
        const tag = e.target.tagName;
        if(tag === 'BUTTON' || tag === 'A' || tag === 'TEXTAREA') return;
        if(!document.getElementById('threadModal').hidden) return;
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
        addToTray(e.dataTransfer.getData('text/plain'));
    });

    await restoreSettings();   // repopulate the sidebar from the saved search
    loadMail();
    setInterval(loadMail, 30000);
}

init();
