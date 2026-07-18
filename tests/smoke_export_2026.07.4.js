// Smoke-test 2026.07.4 — section Export / Sauvegarde (Réglages)
// Exécute le <script> inline RÉEL de public/index.html dans un sandbox vm
// avec stubs (document, localStorage, fetch, URL, Chart), puis vérifie :
//   - renderExportSection : aucun crash, 4 boutons (holdings/transactions/pnl/pdf),
//     périmètre affiché (ALL + wallet actif), XSS échappé dans le label,
//   - doExport : URL correcte (address=ALL ou wallet actif), état
//     « Génération… » puis restauration du bouton, téléchargement via
//     createObjectURL + click, nom de fichier repris du Content-Disposition,
//   - doExport en échec (HTTP 500) : message d'erreur, bouton réactivé,
//   - i18n EN, t()/esc() non shadowés, version 2026.07.4.
// Run: node tests/smoke_export_2026.07.4.js
"use strict";
const fs = require("fs");
const vm = require("vm");

const html = fs.readFileSync("public/index.html", "utf8");
const m = html.match(/<script>([\s\S]*?)<\/script>/);
if (!m) { console.error("FAIL: inline <script> not found"); process.exit(1); }
const code = m[1];

// 1) Syntax check (équivalent node --check)
new vm.Script(code);
console.log("OK  syntax: inline script compiles");

// ── Stubs DOM/environnement (même pattern que smoke_analytics_2026.07.3) ──
const elements = {};
let clickedLinks = [];
function makeEl(id) {
  return {
    id: id, innerHTML: "", value: "", textContent: "", placeholder: "",
    style: {}, options: { length: 0 }, checked: false, disabled: false, open: false,
    href: "", download: "",
    classList: { add() {}, remove() {}, toggle() {}, contains() { return false; } },
    addEventListener() {}, insertAdjacentHTML(pos, h) { this.innerHTML += h; },
    querySelector() { return null; }, remove() {}, getContext() { return {}; },
    setAttribute() {}, getAttribute() { return null; },
    click() { clickedLinks.push({ href: this.href, download: this.download }); },
  };
}
const documentStub = {
  getElementById(id) { if (!elements[id]) elements[id] = makeEl(id); return elements[id]; },
  querySelectorAll() { return []; },
  querySelector() { return makeEl("_q"); },
  addEventListener() {},
  createElement(tag) { return makeEl("_new_" + tag); },
  body: { appendChild() {} },
};
const storage = {};
const localStorageStub = {
  getItem(k) { return Object.prototype.hasOwnProperty.call(storage, k) ? storage[k] : null; },
  setItem(k, v) { storage[k] = String(v); },
  removeItem(k) { delete storage[k]; },
};
function ChartStub() { this.destroy = function () {}; }

let objectUrls = 0, revoked = 0;
const sandbox = {
  document: documentStub,
  localStorage: localStorageStub,
  // fetch initial : Promise qui ne résout JAMAIS (neutralise l'IIFE d'auto-login)
  fetch: function () { return new Promise(function () {}); },
  Chart: ChartStub,
  confirm: function () { return false; },
  console: console,
  setTimeout: function () {}, setInterval: function () {},
  navigator: {}, location: { reload() {} },
  URL: {
    createObjectURL: function () { objectUrls++; return "blob:stub-" + objectUrls; },
    revokeObjectURL: function () { revoked++; },
  },
};
sandbox.window = sandbox;
vm.createContext(sandbox);

vm.runInContext(code, sandbox, { filename: "index-inline.js" });
console.log("OK  load: script executed in sandbox (no crash at load)");

// Fetch contrôlable installé APRÈS le chargement
let fetchOk = true;
let lastUrl = "";
let contentDisposition = 'attachment; filename="holdings_2026-07-19.csv"';
sandbox.fetch = function (url) {
  lastUrl = String(url);
  return Promise.resolve({
    ok: fetchOk,
    status: fetchOk ? 200 : 500,
    headers: { get: function (h) { return h === "Content-Disposition" ? contentDisposition : null; } },
    blob: function () { return Promise.resolve({ size: 42 }); },
    json: function () { return Promise.resolve({}); },
  });
};

let failures = 0;
function assertTrue(cond, label) {
  if (cond) console.log("OK  " + label);
  else { console.error("FAIL " + label); failures++; }
}
function run(js) { return vm.runInContext(js, sandbox); }
function flush() { return new Promise(function (res) { setImmediate(res); }).then(function () {
  return new Promise(function (res2) { setImmediate(res2); });
}); }

// ── Version ──────────────────────────────────────────────────────
assertTrue(/<strong id="verCurrent">2026\.07\.4<\/strong>/.test(html),
  "version: verCurrent = 2026.07.4");
assertTrue(/id="exportTitle"/.test(html) && /id="exportSection"/.test(html),
  "html: carte Export présente dans Réglages");

// ── Présence des fonctions ───────────────────────────────────────
assertTrue(run("typeof renderExportSection") === "function", "renderExportSection est une fonction");
assertTrue(run("typeof doExport") === "function", "doExport est une fonction");
assertTrue(run("typeof exportScopeLabel") === "function", "exportScopeLabel est une fonction");
assertTrue(run("typeof EXPORT_KINDS") === "object" && run("EXPORT_KINDS.length") === 4,
  "EXPORT_KINDS: 4 formats d'export");

(async function () {
  // ── Rendu FR, périmètre ALL ────────────────────────────────────
  run('LANG="fr"; activeWallet="ALL"; wallets=[];');
  run("renderExportSection()");
  let sec = run('document.getElementById("exportSection").innerHTML');
  assertTrue(sec.indexOf("expBtn_holdings") !== -1 && sec.indexOf("expBtn_transactions") !== -1
    && sec.indexOf("expBtn_pnl") !== -1 && sec.indexOf("expBtn_pdf") !== -1,
    "rendu: les 4 boutons d'export sont présents");
  assertTrue(sec.indexOf("Holdings (CSV)") !== -1 && sec.indexOf("Rapport PnL (CSV)") !== -1
    && sec.indexOf("Synthèse (PDF)") !== -1, "rendu FR: labels traduits");
  assertTrue(sec.indexOf("Tous les wallets") !== -1, "rendu: périmètre ALL affiché");

  // ── Périmètre wallet actif + XSS ───────────────────────────────
  run('wallets=[{id:1,address:"0x15CD7D7A1fc0ca1B91F58d64a591dA4f5C50AD7e",label:"<b>Main</b>"}];'
    + 'activeWallet="0x15CD7D7A1fc0ca1B91F58d64a591dA4f5C50AD7e";');
  run("renderExportSection()");
  sec = run('document.getElementById("exportSection").innerHTML');
  assertTrue(sec.indexOf("&lt;b&gt;Main&lt;/b&gt;") !== -1 && sec.indexOf("<b>Main</b>") === -1,
    "XSS: label de wallet échappé via esc()");

  // ── doExport succès (wallet actif) ─────────────────────────────
  clickedLinks = [];
  fetchOk = true;
  run('doExport("holdings")');
  await flush();
  assertTrue(lastUrl.indexOf("/api/export/holdings.csv?address=0x15CD7D7A") === 0,
    "doExport: URL holdings avec le wallet actif");
  assertTrue(clickedLinks.length === 1 && clickedLinks[0].download === "holdings_2026-07-19.csv",
    "doExport: lien cliqué, nom de fichier repris du Content-Disposition");
  assertTrue(objectUrls > 0, "doExport: createObjectURL appelé (blob)");
  let msg = run('document.getElementById("expMsg").textContent');
  assertTrue(msg.indexOf("✅") === 0 && msg.indexOf("holdings_2026-07-19.csv") !== -1,
    "doExport: message de succès avec nom de fichier");
  assertTrue(run('document.getElementById("expBtn_holdings").disabled') === false,
    "doExport: bouton réactivé après succès");

  // ── doExport ALL + PDF ─────────────────────────────────────────
  run('activeWallet="ALL"');
  contentDisposition = 'attachment; filename="portfolio_summary_2026-07-19.pdf"';
  clickedLinks = [];
  run('doExport("pdf")');
  await flush();
  assertTrue(lastUrl === "/api/export/summary.pdf?address=ALL",
    "doExport: URL summary.pdf avec address=ALL");
  assertTrue(clickedLinks.length === 1 && clickedLinks[0].download === "portfolio_summary_2026-07-19.pdf",
    "doExport: PDF téléchargé avec le bon nom");

  // ── doExport échec HTTP ────────────────────────────────────────
  fetchOk = false;
  run('doExport("pnl")');
  await flush();
  msg = run('document.getElementById("expMsg").textContent');
  assertTrue(msg.indexOf("❌") === 0 && msg.indexOf("HTTP 500") !== -1,
    "doExport: échec HTTP → message d'erreur");
  assertTrue(run('document.getElementById("expBtn_pnl").disabled') === false,
    "doExport: bouton réactivé après échec");
  fetchOk = true;

  // ── i18n EN ────────────────────────────────────────────────────
  run('LANG="en"');
  run("renderExportSection()");
  sec = run('document.getElementById("exportSection").innerHTML');
  assertTrue(sec.indexOf("PnL report (CSV)") !== -1 && sec.indexOf("Summary (PDF)") !== -1
    && sec.indexOf("All wallets") !== -1, "i18n EN: labels traduits");
  run('LANG="fr"');

  // ── applyLang ne crashe pas et met à jour la carte ────────────
  run("applyLang()");
  assertTrue(run('document.getElementById("exportTitle").textContent') === "📤 Export / Sauvegarde",
    "applyLang: titre de la carte Export mis à jour");

  // ── t()/esc() non shadowés ─────────────────────────────────────
  assertTrue(run("typeof t") === "function" && run('t("expHoldings")') === "Holdings (CSV)",
    "t() reste une fonction (pas de shadowing)");
  assertTrue(run("typeof esc") === "function" && run('esc("<x>")') === "&lt;x&gt;",
    "esc() reste une fonction (pas de shadowing)");

  console.log("");
  if (failures) { console.error("❌ " + failures + " échec(s)"); process.exit(1); }
  console.log("✅ Smoke export 2026.07.4 OK");
})().catch(function (e) { console.error("FAIL exception:", e); process.exit(1); });
