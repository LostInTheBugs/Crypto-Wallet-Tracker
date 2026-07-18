// Smoke-test v2.12.8 — page DeFi (positions Moralis)
// Exécute le <script> inline RÉEL de public/index.html dans un sandbox vm
// avec stubs (document, localStorage, fetch), puis vérifie :
//   - le script compile et se charge sans crash,
//   - loadDefiPositions() avec configured:false -> CTA « configure ta clé Moralis »,
//   - loadDefiPositions() avec des positions -> cartes protocole, badges type,
//     lignes Fourni/Emprunté/Récompenses, health factor, APY, PnL, liens,
//   - configured:true + 0 position -> « Aucune position DeFi détectée »,
//   - champ error -> bandeau discret,
//   - réponses nulles/garbage -> pas de crash,
//   - t() n'est jamais shadowé (régression v2.11.26),
//   - cohérence sidebar/applyLang/switchPage (pitfall 58 : 4 endroits).
// Run: node tests/smoke_defi_v2.12.8.js
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

// ── Stubs DOM/environnement (même pattern que smoke_sort_v2.12.3) ──
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
const sandbox = {
  document: documentStub,
  localStorage: localStorageStub,
  fetch: function () { return new Promise(function () {}); }, // pending forever au chargement
  Chart: function () { return { destroy() {} }; },
  confirm: function () { return false; },
  console: console,
  setTimeout: function () {}, setInterval: function () {},
  navigator: {}, location: { reload() {} },
};
sandbox.window = sandbox;
vm.createContext(sandbox);
vm.runInContext(code, sandbox, { filename: "index-inline.js" });
console.log("OK  load: script executed in sandbox (no crash at load)");

let failures = 0;
function assertTrue(cond, label) {
  if (cond) console.log("OK  " + label);
  else { console.error("FAIL " + label); failures++; }
}
function run(js) { return vm.runInContext(js, sandbox); }

// ── Stub fetch contrôlable (installé APRÈS le chargement) ───────
let responder = function () { return null; };
sandbox.fetch = function (url) {
  return Promise.resolve({
    ok: true,
    json: function () { return Promise.resolve(responder(String(url))); },
  });
};

// État applicatif minimal
run('wallets=[{address:"0xAAA",label:"w1"}]; activeWallet="ALL";');

(async function main() {

  // ── Scénario 1 : pas de clé Moralis (configured:false) ────────
  responder = function () {
    return { configured: false, address: "0xAAA", positions: [],
      summary: { total_supplied_usd: 0, total_borrowed_usd: 0, total_rewards_usd: 0, net_usd: 0, positions_count: 0 } };
  };
  await run("loadDefiPositions()");
  let pg = elements["pageDefiPos"].innerHTML;
  assertTrue(pg.indexOf("Configurer ma clé Moralis") !== -1,
    "configured:false -> CTA « Configurer ma clé Moralis » affiché");
  assertTrue(pg.indexOf("Clés API externes") !== -1,
    "configured:false -> message mentionne Réglages → Clés API externes");
  assertTrue(pg.indexOf("switchPage") !== -1 && pg.indexOf("settings") !== -1,
    "configured:false -> lien/bouton vers la page Réglages");
  assertTrue(typeof run("t") === "function" && run('t("dpView")') === "Voir la position",
    "t() reste la fonction i18n après le rendu configured:false (pas de shadowing)");

  // ── Scénario 2 : positions normalisées (Aave + Lido + LP XSS) ─
  const AAVE = {
    protocol: "Aave V3", protocol_id: "aave-v3", protocol_url: "https://app.aave.com",
    protocol_logo: "https://cdn.example/aave.png", chain: "eth", type: "lending",
    supplied: [{ symbol: "USDC", amount: 2000.0, usd_value: 2000.0 }],
    borrowed: [{ symbol: "WETH", amount: 0.3, usd_value: 765.5 }],
    rewards: [{ symbol: "AAVE", amount: 0.1, usd_value: 5.0 }],
    supplied_usd: 2000.0, borrowed_usd: 765.5, rewards_usd: 5.0, net_usd: 1239.5,
    pnl: null, health_factor: 1.85, apy: 3.1, link: "https://app.aave.com",
  };
  const LIDO = {
    protocol: "Lido", protocol_id: "lido", protocol_url: null, protocol_logo: null,
    chain: "eth", type: "staking",
    supplied: [{ symbol: "stETH", amount: 1.0, usd_value: 2550.0 }],
    borrowed: [], rewards: [],
    supplied_usd: 2550.0, borrowed_usd: 0, rewards_usd: 0, net_usd: 2550.0,
    pnl: 42.5, health_factor: null, apy: null, link: null,
  };
  const EVIL = {
    protocol: "<script>alert(1)</script>", protocol_id: "evil", protocol_url: null,
    protocol_logo: null, chain: "polygon", type: "liquidity",
    supplied: [{ symbol: "<script>", amount: 1, usd_value: 10 }],
    borrowed: [], rewards: [],
    supplied_usd: 10, borrowed_usd: 0, rewards_usd: 0, net_usd: 10,
    pnl: null, health_factor: null, apy: null,
    link: "https://polygonscan.com/address/0xPOOL",
  };
  responder = function () {
    return { configured: true, address: "0xAAA", positions: [AAVE, LIDO, EVIL],
      summary: { total_supplied_usd: 4560.0, total_borrowed_usd: 765.5, total_rewards_usd: 5.0, net_usd: 3799.5, positions_count: 3 } };
  };
  await run("loadDefiPositions()");
  pg = elements["pageDefiPos"].innerHTML;
  assertTrue(pg.indexOf("Aave V3") !== -1 && pg.indexOf("Lido") !== -1,
    "positions -> cartes protocole Aave V3 et Lido rendues");
  assertTrue(pg.indexOf(">Lending<") !== -1 && pg.indexOf(">Staking<") !== -1,
    "positions -> badges de type Lending et Staking");
  assertTrue(pg.indexOf(">LP<") !== -1,
    "positions -> type liquidity mappé sur badge LP");
  assertTrue(pg.indexOf("Fourni") !== -1 && pg.indexOf("Emprunté") !== -1 && pg.indexOf("Récompenses") !== -1,
    "positions -> sections Fourni / Emprunté / Récompenses");
  assertTrue(pg.indexOf("USDC") !== -1 && pg.indexOf("WETH") !== -1 && pg.indexOf("stETH") !== -1,
    "positions -> symboles de tokens affichés");
  assertTrue(pg.indexOf("Health factor") !== -1, "positions -> health factor affiché");
  assertTrue(pg.indexOf("APY") !== -1, "positions -> APY affiché");
  assertTrue(pg.indexOf("PnL") !== -1, "positions -> PnL affiché quand fourni");
  assertTrue(pg.indexOf('href="https://app.aave.com"') !== -1,
    "positions -> lien « Voir la position » vers l'app du protocole");
  assertTrue(pg.indexOf('href="https://polygonscan.com/address/0xPOOL"') !== -1,
    "positions -> lien fallback explorer");
  assertTrue(pg.indexOf('target="_blank"') !== -1 && pg.indexOf('rel="noopener"') !== -1,
    "positions -> liens en target=_blank rel=noopener");
  assertTrue(pg.indexOf("Voir la position") !== -1, "positions -> libellé du lien i18n FR");
  assertTrue(pg.indexOf("<script") === -1 && pg.indexOf("&lt;script") !== -1,
    "positions -> valeurs échappées par esc() (pas de XSS)");
  assertTrue(pg.indexOf("Total fourni") !== -1 && pg.indexOf("Total emprunté") !== -1 && pg.indexOf("Valeur nette DeFi") !== -1,
    "positions -> en-tête résumé (fourni/emprunté/rewards/net)");
  assertTrue(pg.indexOf("loadDefiPositions(true)") !== -1,
    "positions -> bouton Actualiser (force=true) présent");
  assertTrue(run('t("dpApy")') === "APY" && typeof run("esc") === "function",
    "t()/esc() intacts après rendu des positions");

  // ── Scénario 3 : configured:true mais 0 position ──────────────
  responder = function () {
    return { configured: true, address: "0xAAA", positions: [],
      summary: { total_supplied_usd: 0, total_borrowed_usd: 0, total_rewards_usd: 0, net_usd: 0, positions_count: 0 } };
  };
  await run("loadDefiPositions()");
  pg = elements["pageDefiPos"].innerHTML;
  assertTrue(pg.indexOf("Aucune position DeFi détectée") !== -1,
    "configured:true + 0 position -> message « Aucune position »");

  // ── Scénario 4 : erreur Moralis remontée ──────────────────────
  responder = function () {
    return { configured: true, address: "0xAAA", positions: [],
      summary: { total_supplied_usd: 0, total_borrowed_usd: 0, total_rewards_usd: 0, net_usd: 0, positions_count: 0 },
      error: "Moralis: clé API invalide ou expirée (401)" };
  };
  await run("loadDefiPositions()");
  pg = elements["pageDefiPos"].innerHTML;
  assertTrue(pg.indexOf("⚠️") !== -1 && pg.indexOf("401") !== -1,
    "champ error -> bandeau discret avec le message");

  // ── Scénario 5 : réponses nulles / garbage -> pas de crash ────
  run("renderDefiPositions([null,undefined,42,{},{summary:null}])");
  pg = elements["pageDefiPos"].innerHTML;
  assertTrue(pg.indexOf("Erreur") !== -1,
    "réponses garbage -> message d'erreur discret, pas de crash");
  run("renderDefiPositions(null)");
  console.log("OK  renderDefiPositions(null) ne crashe pas");

  // ── Scénario 6 : multi-wallets, un configured + un non ────────
  run('wallets=[{address:"0xAAA"},{address:"0xBBB"}]; activeWallet="ALL";');
  responder = function (url) {
    if (url.indexOf("0xAAA") !== -1) {
      return { configured: true, address: "0xAAA", positions: [LIDO],
        summary: { total_supplied_usd: 2550, total_borrowed_usd: 0, total_rewards_usd: 0, net_usd: 2550, positions_count: 1 } };
    }
    return { configured: false, address: "0xBBB", positions: [],
      summary: { total_supplied_usd: 0, total_borrowed_usd: 0, total_rewards_usd: 0, net_usd: 0, positions_count: 0 } };
  };
  await run("loadDefiPositions()");
  pg = elements["pageDefiPos"].innerHTML;
  assertTrue(pg.indexOf("Lido") !== -1 && pg.indexOf("Configurer ma clé Moralis") === -1,
    "multi-wallets: au moins un configured -> positions affichées (pas le CTA)");

  // ── Scénario 7 : anglais ───────────────────────────────────────
  run('LANG="en"');
  responder = function () {
    return { configured: false, address: "0xAAA", positions: [],
      summary: { total_supplied_usd: 0, total_borrowed_usd: 0, total_rewards_usd: 0, net_usd: 0, positions_count: 0 } };
  };
  await run("loadDefiPositions()");
  pg = elements["pageDefiPos"].innerHTML;
  assertTrue(pg.indexOf("Configure my Moralis key") !== -1,
    "i18n EN: CTA en anglais");
  run('LANG="fr"');

  // ── Cohérence statique (pitfall 58 : les 4 endroits) ──────────
  const sidebarLinks = (html.match(/data-page="[a-z]+"/g) || []);
  const keysMatch = code.match(/var keys = \[([^\]]+)\]/);
  const keysCount = keysMatch ? keysMatch[1].split(",").length : -1;
  assertTrue(sidebarLinks.length === keysCount,
    "sidebar (" + sidebarLinks.length + " liens) et applyLang keys (" + keysCount + ") alignés");
  assertTrue(sidebarLinks.indexOf('data-page="defi"') !== -1, "sidebar: lien data-page=defi présent");
  assertTrue(html.indexOf('id="pageDefiPos"') !== -1, "div id=pageDefiPos présent");
  assertTrue(code.indexOf('getElementById("pageDefiPos").classList.toggle') !== -1,
    "switchPage: toggle de pageDefiPos présent");
  assertTrue(code.indexOf('if (page=="defi") loadDefiPositions()') !== -1,
    "switchPage: appel loadDefiPositions() sur page defi");
  assertTrue(html.indexOf('id="verCurrent">v2.12.8<') !== -1, "version affichée = v2.12.8");
  assertTrue(code.match(/function dpAmt|function dpTypeLabel|function dpTokenLines/g).length === 3,
    "helpers dpAmt/dpTypeLabel/dpTokenLines définis (jamais nommés t/esc/fmtCurr)");

  console.log("");
  if (failures) { console.error("ECHEC: " + failures + " test(s) en échec"); process.exit(1); }
  console.log("Tous les tests smoke DeFi passent.");
})().catch(function (e) { console.error("CRASH:", e); process.exit(1); });
