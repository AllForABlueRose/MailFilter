// Shared mutable state. These scripts are plain (non-module) <script>s that
// share one global scope, so every cross-file global is declared exactly once,
// here. Loaded first; main.js (which calls init) is loaded last.

let resourcesOnly = false;

let mailById = {};          // id -> view model from the last load (drag source)
let trayMails = [];         // mails collected in the workspace
const trayIds = new Set();
let traySortNewestFirst = true;   // workspace sort direction (toggled by the button)

let threadMails = [];       // mails shown in the thread/message popup
let threadOldestFirst = true;

let templateBodies = {};    // template name -> settings, from the last /api/templates load
