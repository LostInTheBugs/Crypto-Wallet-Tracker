// Smoke-test 2026.07.3 — page Analytics (répartition & performance)
// Exécute le <script> inline RÉEL de public/index.html dans un sandbox vm
// avec stubs (document, localStorage, fetch, Chart), puis vérifie :
//   - loadAnalytics/renderAnalytics : aucun crash, aucun "X is not a function",
//   - 3 cartes de variation (24h/7j/30j), « — » + « Historique insuffisant »
//     quand une période est null,
//   - 3 donuts Chart.js (chaîne/catégorie/actif) : labels capitalisés,
//     catégories traduites, OTHERS → « Autres », couleurs chaîne cohérentes,
//   - tableau performers (gagnants verts / perdants rouges), XSS échappé,
//   - ligne benchmark Portefeuille vs BTC / ETH,
//   - états vides (aucun wallet, historique insuffisant, fetch KO),
//   - destroy() des instances Chart avant recréation (pas de fuite),
//   - sélecteur de période (anSetRange → range=30d dans l'URL),
//   - switchPage("analytics") déclenche loadAnalytics, i18n EN, t() non shadowé,
//   - version 2026.07.3.
// Run: node tests/smoke_analytics_2026.07.3.js
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

// ── Stubs DOM/environnement (même pattern que smoke_defi_free_v2.12.9) ──
const elements = {};
function makeEl(id) {
  return {
    id: id, innerHTML: "", value: "", textContent: "", placeholder: "",
    style: {}, options: { length: 0 }, checked: false, disabled: false, open: false,
    classList: { add() {}, remove() {}, toggle() {}, contains() { return false; } },
    addEventListener() {}, insertAdjacentHTML(pos, h) { this.innerHTML += h; },
    querySelector() { return null; }, remove() {}, getContext() { return {}; },
    setAttribute() {}, getAttribute() { return null; },
  };
}
const documentStub = {
  getElementById(id) { if (!elements[id]) elements[id] = makeEl(id); return elements[id]; },
  querySelectorAll() { return []; },
  querySelector() { return makeEl("_q"); },
  addEventListener() {},
};
const storage = {};
const localStorageStub = {
  getItem(k) { return Object.prototype.hasOwnProperty.call(storage, k) ? storage[k] : null; },
  setItem(k, v) { storage[k] = String(v); },
  removeItem(k) { delete storage[k]; },
};

// Chart stub instrumenté : enregistre les configs + compte les destroy()
const chartCalls = [];
let destroyCount = 0;
function ChartStub(ctx, cfg) {
  chartCalls.push(cfg);
  this.cfg = cfg;
  this.destroy = function () { destroyCount++; };
}

// fetch stub contrôlable — le fetch INITIAL ne résout jamais (comme
// smoke_defi_free_v2.12.9) pour neutraliser l'IIFE d'auto-login du script.
let responder = function () { return null; };
let fetchOk = true;
let lastUrl = "";
const sandbox = {
  document: documentStub,
  localStorage: localStorageStub,
  fetch: function () { return new Promise(function () {}); },
  Chart: ChartStub,
  confirm: function () { return false; },
  console: console,
  setTimeout: function () {}, setInterval: function () {},
  navigator: {}, location: { reload() {} },
};
sandbox.window = sandbox;
vm.createContext(sandbox);
vm.runInContext(code, sandbox, { filename: "index-inline.js" });
console.log("OK  load: script executed in sandbox (no crash at load)");

// Fetch contrôlable installé APRÈS le chargement (l'auto-login reste pendu)
sandbox.fetch = function (url) {
  lastUrl = String(url);
  return Promise.resolve({
    ok: fetchOk,
    status: fetchOk ? 200 : 500,
    json: function () { return Promise.resolve(responder(String(url))); },
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
assertTrue(/<strong id="verCurrent">2026\.07\.3<\/strong>/.test(html),
  "version: verCurrent = 2026.07.3");

// ── Présence des fonctions ───────────────────────────────────────
assertTrue(run("typeof loadAnalytics") === "function", "loadAnalytics est une fonction");
assertTrue(run("typeof renderAnalytics") === "function", "renderAnalytics est une fonction");
assertTrue(run("typeof anDrawDonut") === "function", "anDrawDonut est une fonction");
assertTrue(run("typeof anSetRange") === "function", "anSetRange est une fonction");

// ── Jeu de données complet ───────────────────────────────────────
const AN_FULL = {
  address: "ALL", range: "7d", total_usd: 1000.5,
  allocation: {
    by_chain: [
      { key: "ethereum", usd_value: 630.0, pct: 62.97 },
      { key: "base", usd_value: 150.0, pct: 14.99 },
      { key: "optimism", usd_value: 200.0, pct: 19.99 },
      { key: "arbitrum", usd_value: 20.0, pct: 2.0 },
      { key: "gnosis", usd_value: 0.5, pct: 0.05 },
    ],
    by_category: [
      { key: "wallet", usd_value: 950.5, pct: 95.0 },
      { key: "lending", usd_value: 30.0, pct: 3.0 },
      { key: "staked", usd_value: 20.0, pct: 2.0 },
    ],
    by_asset: [
      { symbol: "ETH", usd_value: 750.0, pct: 74.96 },
      { symbol: "USDC", usd_value: 200.0, pct: 19.99 },
      { symbol: "WSTETH", usd_value: 20.0, pct: 2.0 },
      { symbol: "OTHERS", usd_value: 30.5, pct: 3.05 },
    ],
  },
  change: {
    "24h": { abs_usd: 20.0, pct: 2.04 },
    "7d": null,
    "30d": { abs_usd: -200.0, pct: -16.67 },
  },
  performers: {
    best: [
      { symbol: "ETH", usd_value: 750.0, pct: 20.0 },
      { symbol: "<script>evil</script>", usd_value: 30.0, pct: 5.0 },
    ],
    worst: [{ symbol: "WSTETH", usd_value: 20.0, pct: -10.26 }],
  },
  benchmark: { portfolio_pct: null, btc_pct: 3.5, eth_pct: -1.2 },
};

run('wallets=[{address:"0xAAA",label:"w1"}]; activeWallet="ALL"; currentPage="analytics";');

(async function main() {

  // ── Scénario 1 : données complètes ─────────────────────────────
  responder = function () { return AN_FULL; };
  fetchOk = true;
  await run("loadAnalytics()");
  let pg = elements["pageAnalytics"].innerHTML;
  assertTrue(pg.indexOf("📊 Analytics") !== -1, "rendu: en-tête Analytics présent");
  assertTrue(lastUrl.indexOf("/api/analytics?address=ALL&range=7d") !== -1,
    "fetch: URL /api/analytics?address=ALL&range=7d");
  assertTrue(pg.indexOf("Variation 24h") !== -1 && pg.indexOf("Variation 7 jours") !== -1
    && pg.indexOf("Variation 30 jours") !== -1, "rendu: 3 cartes de variation");
  assertTrue(pg.indexOf("+$20.00") !== -1 && pg.indexOf("+2.04%") !== -1,
    "rendu: variation 24h positive (+$20.00 / +2.04%) en vert");
  assertTrue(pg.indexOf("-$200.00") !== -1 && pg.indexOf("-16.67%") !== -1,
    "rendu: variation 30j négative (-$200.00 / -16.67%)");
  assertTrue(pg.indexOf("Historique insuffisant") !== -1 && pg.indexOf("—") !== -1,
    "rendu: période 7j null → « — » + Historique insuffisant");
  assertTrue(pg.indexOf("color:#40c463") !== -1 && pg.indexOf("color:#f85149") !== -1,
    "rendu: vert pour hausse, rouge pour baisse");
  assertTrue(pg.indexOf("Répartition par chaîne") !== -1 && pg.indexOf("Répartition par catégorie") !== -1
    && pg.indexOf("Répartition par actif") !== -1, "rendu: 3 titres de donuts");
  assertTrue(pg.indexOf('id="anChainChart"') !== -1 && pg.indexOf('id="anCatChart"') !== -1
    && pg.indexOf('id="anAssetChart"') !== -1, "rendu: 3 canvases présents");
  assertTrue(pg.indexOf("Meilleurs / Pires performers") !== -1, "rendu: carte performers");
  assertTrue(pg.indexOf("Top gagnants") !== -1 && pg.indexOf("Top perdants") !== -1,
    "rendu: sections Top gagnants / Top perdants");
  assertTrue(pg.indexOf("ETH") !== -1 && pg.indexOf("+20.00%") !== -1,
    "rendu: performer ETH +20.00%");
  assertTrue(pg.indexOf("WSTETH") !== -1 && pg.indexOf("-10.26%") !== -1,
    "rendu: performer WSTETH -10.26%");
  assertTrue(pg.indexOf("&lt;script&gt;") !== -1 && pg.indexOf("<script>evil") === -1,
    "sécurité: symbole malveillant échappé par esc() (pas de XSS)");
  assertTrue(pg.indexOf("Portefeuille vs BTC / ETH") !== -1 && pg.indexOf("+3.50%") !== -1
    && pg.indexOf("-1.20%") !== -1, "rendu: benchmark BTC/ETH présent");
  assertTrue(chartCalls.length === 3, "charts: 3 donuts créés (" + chartCalls.length + ")");
  assertTrue(chartCalls.every(function (c) { return c.type === "doughnut"; }),
    "charts: type doughnut partout");
  assertTrue(chartCalls[0].data.labels[0] === "Ethereum",
    "charts: label chaîne capitalisé (Ethereum)");
  assertTrue(chartCalls[0].data.datasets[0].backgroundColor[0] === "#627eea",
    "charts: couleur ethereum cohérente avec le dashboard (#627eea)");
  assertTrue(chartCalls[1].data.labels.indexOf("Wallet") !== -1
    && chartCalls[1].data.labels.indexOf("Prêts (Lending)") !== -1,
    "charts: catégories traduites (Wallet, Prêts (Lending))");
  assertTrue(chartCalls[2].data.labels.indexOf("Autres") !== -1,
    "charts: OTHERS → « Autres » (FR)");
  const others_idx = chartCalls[2].data.labels.indexOf("Autres");
  assertTrue(chartCalls[2].data.datasets[0].backgroundColor[others_idx] === "#6e7681",
    "charts: part « Autres » en gris");
  // Légende avec pourcentages
  const gl = chartCalls[0].options.plugins.legend.labels.generateLabels({ data: chartCalls[0].data });
  assertTrue(gl[0].text.indexOf("Ethereum") === 0 && gl[0].text.indexOf("63%") !== -1,
    "charts: légende avec pourcentage (Ethereum 63%)");
  assertTrue(run("typeof t") === "function" && run('t("anBest")') === "Top gagnants",
    "t() reste la fonction i18n après le rendu (pas de shadowing)");
  assertTrue(run("typeof esc") === "function" && run("typeof fmtCurr") === "function",
    "esc()/fmtCurr() non shadowés");

  // ── Scénario 2 : re-render → destroy des instances précédentes ─
  const destroyBefore = destroyCount;
  await run("loadAnalytics()");
  assertTrue(destroyCount >= destroyBefore + 3,
    "charts: destroy() appelé sur les 3 donuts avant recréation (" + (destroyCount - destroyBefore) + ")");

  // ── Scénario 3 : sélecteur de période ──────────────────────────
  run('anSetRange("30d",null)');
  await flush();
  assertTrue(run("anRange") === "30d", "range: anRange mis à jour (30d)");
  assertTrue(lastUrl.indexOf("range=30d") !== -1, "range: URL fetch contient range=30d");
  run('anRange="7d"');

  // ── Scénario 4 : états vides (historique insuffisant partout) ──
  responder = function () {
    return {
      address: "ALL", range: "7d", total_usd: 0.0,
      allocation: { by_chain: [], by_category: [], by_asset: [] },
      change: { "24h": null, "7d": null, "30d": null },
      performers: { best: [], worst: [] },
    };
  };
  const chartsBefore = chartCalls.length;
  await run("loadAnalytics()");
  pg = elements["pageAnalytics"].innerHTML;
  assertTrue(pg.indexOf("Historique insuffisant") !== -1,
    "vide: cartes variation → Historique insuffisant");
  assertTrue(pg.indexOf("Pas assez d&#39;historique de prix") !== -1,
    "vide: performers → Pas assez d'historique de prix (apostrophe échappée)");
  assertTrue(chartCalls.length === chartsBefore,
    "vide: aucun donut créé sur listes vides (pas de crash)");
  assertTrue(pg.indexOf("Portefeuille vs BTC / ETH") === -1,
    "vide: bloc benchmark absent quand omis par le backend");

  // ── Scénario 5 : aucun wallet ──────────────────────────────────
  run("wallets=[]");
  await run("loadAnalytics()");
  pg = elements["pageAnalytics"].innerHTML;
  assertTrue(pg.indexOf("Aucun wallet") !== -1, "aucun wallet: message dédié");
  run('wallets=[{address:"0xAAA",label:"w1"}]');

  // ── Scénario 6 : fetch KO → message d'erreur, pas de crash ────
  fetchOk = false;
  await run("loadAnalytics()");
  pg = elements["pageAnalytics"].innerHTML;
  assertTrue(pg.indexOf("Erreur lors du chargement des analytics") !== -1,
    "fetch KO: message d'erreur affiché (pas de crash)");
  fetchOk = true;

  // ── Scénario 7 : switchPage("analytics") déclenche le chargement ─
  responder = function () { return AN_FULL; };
  lastUrl = "";
  run('switchPage("analytics")');
  await flush();
  assertTrue(run("currentPage") === "analytics", "switchPage: currentPage = analytics");
  assertTrue(lastUrl.indexOf("/api/analytics") !== -1, "switchPage: loadAnalytics déclenché");
  assertTrue(elements["pageAnalytics"].innerHTML.indexOf("📊 Analytics") !== -1,
    "switchPage: page rendue");

  // ── Scénario 8 : i18n EN ───────────────────────────────────────
  run('LANG="en"');
  await run("loadAnalytics()");
  pg = elements["pageAnalytics"].innerHTML;
  assertTrue(pg.indexOf("24h change") !== -1 && pg.indexOf("7-day change") !== -1,
    "i18n EN: cartes de variation traduites");
  assertTrue(pg.indexOf("Top gainers") !== -1 && pg.indexOf("Top losers") !== -1,
    "i18n EN: performers traduits");
  assertTrue(chartCalls[chartCalls.length - 1].data.labels.indexOf("Others") !== -1,
    "i18n EN: OTHERS → Others");
  run('LANG="fr"');

  // ── Résultat ───────────────────────────────────────────────────
  console.log("");
  if (failures) { console.error("❌ " + failures + " test(s) en échec"); process.exit(1); }
  console.log("✅ Smoke-test Analytics 2026.07.3 : tous les tests passent");
})().catch(function (e) { console.error("FATAL", e); process.exit(1); });
