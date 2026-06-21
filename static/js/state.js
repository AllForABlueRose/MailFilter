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

let automationsById = {};       // id -> automation, from the last /api/automations load
let editingAutomationId = null;  // id open in the builder, or null when creating a new one

let customersById = {};         // id -> organization, from the last /api/organizations load
let editingOrgId = null;         // id open in the org builder, or null when creating a new one
let contactDirectory = [];       // aggregated+resolved contacts, from the last /api/contacts load
let selectedOrgId = null;        // org whose contacts the directory shows; null = unassigned contacts
let roleSortRepsOnTop = false;   // org-contact sort: representatives first when true, members first when false
