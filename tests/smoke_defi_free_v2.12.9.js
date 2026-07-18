// Smoke-test v2.12.9 — page DeFi : mode GRATUIT best-effort (sans clé Moralis)
// Exécute le <script> inline RÉEL de public/index.html dans un sandbox vm
// avec stubs (document, localStorage, fetch), puis vérifie :
//   - source:"best-effort" -> bandeau « Mode gratuit (on-chain) » + lien Réglages,
//     positions rendues (supplied/borrowed/staking), PAS de CTA plein écran,
//   - rewards du résumé = « — » en best-effort (pas de valeur inventée),
//   - health factor / APY / PnL masqués (null) en best-effort,
//   - net négatif possible (dette seule), lien explorer Blockscout,
//   - source:"best-effort" + 0 position -> bandeau + « Aucune position »,
//   - source:"moralis" -> comportement riche INCHANGÉ (pas de bandeau,
//     rewards chiffrés, health factor/APY affichés),
//   - configured:false SANS source (vieux backend) -> CTA rétro-compatible,
//   - XSS échappé, t() jamais shadowé, i18n EN, version v2.12.9.
// Run: node tests/smoke_defi_free_v2.12.9.js
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

// ── Stubs DOM/environnement (même pattern que smoke_defi_v2.12.8) ──
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
  fetch: function () { return new Promise(function () {}); },
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

// ── Stub fetch contrôlable ───────────────────────────────────────
let responder = function () { return null; };
sandbox.fetch = function (url) {
  return Promise.resolve({
    ok: true,
    json: function () { return Promise.resolve(responder(String(url))); },
  });
};

run('wallets=[{address:"0xAAA",label:"w1"}]; activeWallet="ALL";');

// ── Jeux de données best-effort (mêmes chiffres que le test python) ──
const BE_AAVE = {
  protocol: "Aave", protocol_id: "aave", protocol_url: null, protocol_logo: null,
  chain: "ethereum", type: "lending",
  supplied: [{ symbol: "aEthUSDC", amount: 1500.0, usd_value: 1500.0 }],
  borrowed: [{ symbol: "variableDebtEthUSDC", amount: 400.0, usd_value: 400.0 }],
  rewards: [],
  supplied_usd: 1500.0, borrowed_usd: 400.0, rewards_usd: 0.0, net_usd: 1100.0,
  pnl: null, health_factor: null, apy: null,
  link: "https://eth.blockscout.com/address/0xAAVE1", source: "best-effort",
};
const BE_LIDO = {
  protocol: "Lido", protocol_id: "lido", protocol_url: null, protocol_logo: null,
  chain: "ethereum", type: "staking",
  supplied: [{ symbol: "wstETH", amount: 1.2, usd_value: 3600.0 }],
  borrowed: [], rewards: [],
  supplied_usd: 3600.0, borrowed_usd: 0.0, rewards_usd: 0.0, net_usd: 3600.0,
  pnl: null, health_factor: null, apy: null,
  link: "https://eth.blockscout.com/address/0xWSTETH", source: "best-effort",
};
const BE_EVIL = {
  protocol: "<script>alert(1)</script>", protocol_id: "evil", protocol_url: null,
  protocol_logo: null, chain: "base", type: "vault",
  supplied: [{ symbol: "<script>", amount: 1, usd_value: 10 }],
  borrowed: [], rewards: [],
  supplied_usd: 10, borrowed_usd: 0, rewards_usd: 0, net_usd: 10,
  pnl: null, health_factor: null, apy: null, link: null, source: "best-effort",
};
function beResponse(positions, supplied, borrowed) {
  return {
    configured: false, source: "best-effort", address: "0xAAA", positions: positions,
    summary: { total_supplied_usd: supplied, total_borrowed_usd: borrowed,
      total_rewards_usd: 0, net_usd: supplied - borrowed,
      positions_count: positions.length, source: "best-effort" },
  };
}

(async function main() {

  // ── Scénario 1 : SANS clé -> best-effort avec positions ────────
  responder = function () { return beResponse([BE_AAVE, BE_LIDO, BE_EVIL], 5110, 400); };
  await run("loadDefiPositions()");
  let pg = elements["pageDefiPos"].innerHTML;
  assertTrue(pg.indexOf("Mode gratuit (on-chain)") !== -1,
    "best-effort -> bandeau « Mode gratuit (on-chain) » affiché");
  assertTrue(pg.indexOf("Clés API externes") !== -1,
    "bandeau -> mentionne Réglages → Clés API externes");
  assertTrue(pg.indexOf("switchPage") !== -1 && pg.indexOf("settings") !== -1,
    "bandeau -> lien vers la page Réglages (clés API)");
  assertTrue(pg.indexOf('class="btn btn-blue"') === -1,
    "best-effort -> PAS de CTA plein écran « configure ta clé »");
  assertTrue(pg.indexOf("Aave") !== -1 && pg.indexOf("Lido") !== -1,
    "best-effort -> cartes protocole Aave et Lido rendues");
  assertTrue(pg.indexOf(">Lending<") !== -1 && pg.indexOf(">Staking<") !== -1 && pg.indexOf(">Vault<") !== -1,
    "best-effort -> badges de type Lending / Staking / Vault");
  assertTrue(pg.indexOf("aEthUSDC") !== -1 && pg.indexOf("variableDebtEthUSDC") !== -1 && pg.indexOf("wstETH") !== -1,
    "best-effort -> symboles supplied ET borrowed affichés");
  assertTrue(pg.indexOf("Fourni") !== -1 && pg.indexOf("Emprunté") !== -1,
    "best-effort -> sections Fourni / Emprunté");
  assertTrue(pg.indexOf('color:#6e7681">—<') !== -1,
    "best-effort -> carte Récompenses du résumé = « — » (pas de valeur inventée)");
  assertTrue(pg.indexOf("Health factor") === -1,
    "best-effort -> health factor masqué (null)");
  assertTrue(pg.indexOf("APY :") === -1,
    "best-effort -> APY masqué (null)");
  assertTrue(pg.indexOf("PnL") === -1,
    "best-effort -> PnL masqué (null)");
  assertTrue(pg.indexOf('href="https://eth.blockscout.com/address/0xAAVE1"') !== -1,
    "best-effort -> lien explorer Blockscout du contrat");
  assertTrue(pg.indexOf("Total fourni") !== -1 && pg.indexOf("Total emprunté") !== -1 && pg.indexOf("Valeur nette DeFi") !== -1,
    "best-effort -> résumé fourni/emprunté/net présent");
  assertTrue(pg.indexOf("<script") === -1 && pg.indexOf("&lt;script") !== -1,
    "best-effort -> valeurs échappées par esc() (pas de XSS)");
  assertTrue(pg.indexOf("loadDefiPositions(true)") !== -1,
    "best-effort -> bouton Actualiser présent");
  assertTrue(typeof run("t") === "function" && run('t("dpView")') === "Voir la position",
    "t() reste la fonction i18n après le rendu best-effort (pas de shadowing)");

  // ── Scénario 2 : best-effort avec 0 position ───────────────────
  responder = function () { return beResponse([], 0, 0); };
  await run("loadDefiPositions()");
  pg = elements["pageDefiPos"].innerHTML;
  assertTrue(pg.indexOf("Aucune position DeFi détectée") !== -1,
    "best-effort + 0 position -> « Aucune position DeFi détectée »");
  assertTrue(pg.indexOf("Mode gratuit (on-chain)") !== -1,
    "best-effort + 0 position -> bandeau mode gratuit toujours affiché");

  // ── Scénario 3 : AVEC clé Moralis -> comportement riche INCHANGÉ ─
  const MORALIS_POS = {
    protocol: "Aave V3", protocol_id: "aave-v3", protocol_url: "https://app.aave.com",
    protocol_logo: null, chain: "eth", type: "lending",
    supplied: [{ symbol: "USDC", amount: 2000.0, usd_value: 2000.0 }],
    borrowed: [{ symbol: "WETH", amount: 0.3, usd_value: 765.5 }],
    rewards: [{ symbol: "AAVE", amount: 0.1, usd_value: 5.0 }],
    supplied_usd: 2000.0, borrowed_usd: 765.5, rewards_usd: 5.0, net_usd: 1239.5,
    pnl: 12.3, health_factor: 1.85, apy: 3.1, link: "https://app.aave.com",
  };
  responder = function () {
    return { configured: true, source: "moralis", address: "0xAAA", positions: [MORALIS_POS],
      summary: { total_supplied_usd: 2000, total_borrowed_usd: 765.5, total_rewards_usd: 5,
        net_usd: 1239.5, positions_count: 1, source: "moralis" } };
  };
  await run("loadDefiPositions()");
  pg = elements["pageDefiPos"].innerHTML;
  assertTrue(pg.indexOf("Mode gratuit") === -1,
    "moralis -> PAS de bandeau mode gratuit");
  assertTrue(pg.indexOf('color:#6e7681">—<') === -1,
    "moralis -> carte Récompenses chiffrée (pas de « — »)");
  assertTrue(pg.indexOf("Health factor") !== -1 && pg.indexOf("APY") !== -1 && pg.indexOf("PnL") !== -1,
    "moralis -> health factor / APY / PnL affichés (inchangé)");
  assertTrue(pg.indexOf("Aave V3") !== -1 && pg.indexOf("Récompenses") !== -1,
    "moralis -> positions et rewards rendus (inchangé)");

  // ── Scénario 4 : vieux backend (configured:false SANS source) ──
  responder = function () {
    return { configured: false, address: "0xAAA", positions: [],
      summary: { total_supplied_usd: 0, total_borrowed_usd: 0, total_rewards_usd: 0, net_usd: 0, positions_count: 0 } };
  };
  await run("loadDefiPositions()");
  pg = elements["pageDefiPos"].innerHTML;
  assertTrue(pg.indexOf("Configurer ma clé Moralis") !== -1 && pg.indexOf('class="btn btn-blue"') !== -1,
    "configured:false sans source -> CTA rétro-compatible affiché");

  // ── Scénario 5 : multi-wallets, un moralis + un best-effort ────
  run('wallets=[{address:"0xAAA"},{address:"0xBBB"}]; activeWallet="ALL";');
  responder = function (url) {
    if (url.indexOf("0xAAA") !== -1) {
      return { configured: true, source: "moralis", address: "0xAAA", positions: [MORALIS_POS],
        summary: { total_supplied_usd: 2000, total_borrowed_usd: 765.5, total_rewards_usd: 5,
          net_usd: 1239.5, positions_count: 1, source: "moralis" } };
    }
    return beResponse([BE_LIDO], 3600, 0);
  };
  await run("loadDefiPositions()");
  pg = elements["pageDefiPos"].innerHTML;
  assertTrue(pg.indexOf("Aave V3") !== -1 && pg.indexOf("Lido") !== -1,
    "multi-wallets mixte -> positions moralis ET best-effort rendues");
  assertTrue(pg.indexOf("Mode gratuit (on-chain)") !== -1,
    "multi-wallets mixte -> bandeau mode gratuit affiché (au moins un wallet en BE)");
  run('wallets=[{address:"0xAAA",label:"w1"}]; activeWallet="ALL";');

  // ── Scénario 6 : réponses garbage -> pas de crash ──────────────
  run("renderDefiPositions([null,undefined,42,{},{summary:null}])");
  pg = elements["pageDefiPos"].innerHTML;
  assertTrue(pg.indexOf("Erreur") !== -1, "réponses garbage -> message d'erreur, pas de crash");

  // ── Scénario 7 : i18n EN ────────────────────────────────────────
  run('LANG="en"');
  responder = function () { return beResponse([BE_LIDO], 3600, 0); };
  await run("loadDefiPositions()");
  pg = elements["pageDefiPos"].innerHTML;
  assertTrue(pg.indexOf("Free mode (on-chain)") !== -1,
    "i18n EN: bandeau « Free mode (on-chain) »");
  assertTrue(pg.indexOf("Configure my Moralis key") !== -1,
    "i18n EN: lien de configuration en anglais");
  run('LANG="fr"');

  // ── Cohérence statique ──────────────────────────────────────────
  assertTrue(html.indexOf('id="verCurrent">v2.12.9<') !== -1, "version affichée = v2.12.9");
  assertTrue(code.indexOf('dpFreeMode') !== -1 && (code.match(/dpFreeMode:/g) || []).length === 2,
    "clé i18n dpFreeMode présente en FR et EN");
  assertTrue(code.indexOf('res.source==="best-effort"') !== -1,
    "renderDefiPositions détecte source best-effort");

  console.log("");
  if (failures) { console.error("ECHEC: " + failures + " test(s) en échec"); process.exit(1); }
  console.log("Tous les tests smoke DeFi mode gratuit passent.");
})().catch(function (e) { console.error("CRASH:", e); process.exit(1); });
