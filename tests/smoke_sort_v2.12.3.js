// Smoke-test v2.12.3 — tri des colonnes (Detail tokens + Transactions)
// Exécute le <script> inline de public/index.html dans un sandbox vm avec stubs,
// puis vérifie : rendu sans crash, pas de shadowing de t(), tri multi-colonnes
// asc/desc, nulls en fin de liste, flèches ▲/▼, en-têtes cliquables.
"use strict";
const fs = require("fs");
const vm = require("vm");

const html = fs.readFileSync("public/index.html", "utf8");
const m = html.match(/<script>([\s\S]*?)<\/script>/);
if (!m) { console.error("FAIL: inline <script> not found"); process.exit(1); }
const code = m[1];

// 1) Syntax check (equivalent node --check)
new vm.Script(code);
console.log("OK  syntax: inline script compiles");

// ── Stubs DOM/environnement ─────────────────────────────────────
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
  fetch: function () { return new Promise(function () {}); }, // pending forever: aucun appel réseau réel
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

// ── Helpers d'assertion ─────────────────────────────────────────
let failures = 0;
function assertEq(actual, expected, label) {
  const a = JSON.stringify(actual), e = JSON.stringify(expected);
  if (a === e) { console.log("OK  " + label + " -> " + a); }
  else { console.error("FAIL " + label + "\n  attendu: " + e + "\n  obtenu : " + a); failures++; }
}
function assertTrue(cond, label) {
  if (cond) console.log("OK  " + label);
  else { console.error("FAIL " + label); failures++; }
}
function boldSeq(htmlStr) { // ordre des <b>…</b> = ordre des lignes rendues
  return Array.from(htmlStr.matchAll(/<b>([^<]*)<\/b>/g)).map(x => x[1]);
}
function run(js) { return vm.runInContext(js, sandbox); }

// ── Données d'exemple ───────────────────────────────────────────
const SAMPLE = {
  active_count: 4, inactive_count: 3, defi_breakdown: {}, defi_usd: 0, chains: {},
  tokens: [
    { name: "Ether",       symbol: "ETH",  chain: "ethereum", balance: 2.5,      usd_price: 3700,  usd_value: 9250, pnl: 1200, tid: "a1" },
    { name: "USD Coin",    symbol: "USDC", chain: "base",     balance: 5000,     usd_price: 1,     usd_value: 5000, pnl: -50,  tid: "a2" },
    { name: "Aerodrome",   symbol: "AERO", chain: "base",     balance: 800,      usd_price: 0.9,   usd_value: 720,  pnl: null, tid: "a3" },
    { name: "Wrapped BTC", symbol: "WBTC", chain: "arbitrum", balance: 0.1,      usd_price: 95000, usd_value: 9500, pnl: 300,  tid: "a4" },
    { name: "SpamCoin", symbol: "SPAM", chain: "polygon", balance: 61000000, usd_value: 0,     reason: "spam",             enabled: false, tid: "i1" },
    { name: "Dust",     symbol: "DST",  chain: "celo",    balance: 1,        usd_value: 0.001, reason: "zero_value",       enabled: false, tid: "i2" },
    { name: "Meme",     symbol: "MEME", chain: "base",    balance: 5,        usd_value: 0.5,   reason: "memecoin_pattern", enabled: false, tid: "i3" },
  ],
};
sandbox.SAMPLE = SAMPLE;
run("lastResult = SAMPLE; tokInactOpen = true;"); // details ouverts pour rendre les lignes inactives

// ── Tests Détail tokens ─────────────────────────────────────────
run("renderTokenTable(SAMPLE)");
let pg = elements["pageTokens"].innerHTML;
assertTrue(typeof run("t") === "function" && typeof run('t("tmValue")') === "string",
  "tokens: t() reste une fonction i18n après rendu (pas de shadowing)");
assertTrue(pg.indexOf("onclick=\"sortBy('act'") !== -1 && pg.indexOf("onclick=\"sortBy('inact'") !== -1,
  "tokens: en-têtes cliquables (onclick sortBy) présents sur tables active ET inactive");
assertTrue(pg.indexOf("cursor:pointer") !== -1, "tokens: cursor:pointer sur les en-têtes");
assertTrue(pg.indexOf("▼") !== -1, "tokens: flèche ▼ affichée (tri défaut Valeur desc)");
assertEq(boldSeq(pg), ["Wrapped BTC", "Ether", "USD Coin", "Aerodrome", "Meme", "Dust", "SpamCoin"],
  "tokens DÉFAUT: actifs par Valeur desc (9500,9250,5000,720) + inactifs Valeur desc (0.5,0.001,0)");

run("sortBy('act','balance','num')"); pg = elements["pageTokens"].innerHTML;
assertEq(boldSeq(pg).slice(0, 4), ["USD Coin", "Aerodrome", "Ether", "Wrapped BTC"],
  "tokens actifs: clic 1 Balance -> tri NUMÉRIQUE desc (5000,800,2.5,0.1)");
run("sortBy('act','balance','num')"); pg = elements["pageTokens"].innerHTML;
assertEq(boldSeq(pg).slice(0, 4), ["Wrapped BTC", "Ether", "Aerodrome", "USD Coin"],
  "tokens actifs: clic 2 Balance -> sens inversé asc (0.1,2.5,800,5000)");
assertTrue(pg.indexOf("▲") !== -1, "tokens: flèche ▲ affichée en tri ascendant");

run("sortBy('act','name','str')"); pg = elements["pageTokens"].innerHTML;
assertEq(boldSeq(pg).slice(0, 4), ["Aerodrome", "Ether", "USD Coin", "Wrapped BTC"],
  "tokens actifs: Token -> tri alphabétique asc");

run("sortBy('act','pnl','num')"); pg = elements["pageTokens"].innerHTML;
assertEq(boldSeq(pg).slice(0, 4), ["Ether", "Wrapped BTC", "USD Coin", "Aerodrome"],
  "tokens actifs: PNL desc (1200,300,-50) + PNL null (Aerodrome) EN DERNIER");
run("sortBy('act','pnl','num')"); pg = elements["pageTokens"].innerHTML;
assertEq(boldSeq(pg).slice(0, 4), ["USD Coin", "Wrapped BTC", "Ether", "Aerodrome"],
  "tokens actifs: PNL asc (-50,300,1200) + PNL null TOUJOURS EN DERNIER");

run("sortBy('inact','reason','str')"); pg = elements["pageTokens"].innerHTML;
assertEq(boldSeq(pg).slice(4), ["Meme", "SpamCoin", "Dust"],
  "tokens inactifs: Motif asc (memecoin_pattern < spam < zero_value)");
run("sortBy('inact','balance','num'); sortBy('inact','balance','num')"); pg = elements["pageTokens"].innerHTML;
assertEq(boldSeq(pg).slice(4), ["Dust", "Meme", "SpamCoin"],
  "tokens inactifs: Balance asc numérique (1 < 5 < 61000000)");

// ── Tests Transactions ──────────────────────────────────────────
const W = "0xAAAA111122223333444455556666777788889999";
sandbox.TXNS = {
  items: [
    { block_time: "2026-07-15T10:00:00", wallet_address: W, token_symbol: "ETH",  token_name: "Ether",    chain: "ethereum", amount: 1.2, usd_price: 3700, usd_value: 4440, gas_fee_usd: 2.5,  direction: "in",  explorer_url: "" },
    { block_time: "2026-07-17T08:30:00", wallet_address: W, token_symbol: "USDC", token_name: "USD Coin", chain: "base",     amount: 100, usd_price: 1,    usd_value: 100,  gas_fee_usd: 0.1,  direction: "out", explorer_url: "" },
    { block_time: "2026-06-01T00:00:00", wallet_address: W, token_symbol: "AERO", token_name: "Aero",     chain: "base",     amount: 50,  usd_price: 0.9,  usd_value: 45,   gas_fee_usd: null, direction: "in",  explorer_url: "" },
    { block_time: null,                  wallet_address: W, token_symbol: "ZZZ",  token_name: "Zzz",      chain: "zora",     amount: 7,   usd_price: 0,    usd_value: 0,    gas_fee_usd: 0.3,  direction: "out", explorer_url: "" },
  ], total: 4, page: 0,
};
run("txnData = TXNS; renderTxnTable();");
let tc = elements["txnContent"].innerHTML;
assertTrue(typeof run("t") === "function" && typeof run('t("txns")') === "string",
  "txns: t() reste une fonction i18n après rendu (boucle nommée tx, pas t)");
assertTrue(tc.indexOf("onclick=\"sortBy('txn'") !== -1 && tc.indexOf("cursor:pointer") !== -1,
  "txns: en-têtes cliquables (onclick sortBy) présents");
assertTrue(tc.indexOf("Date ▼") !== -1, "txns: flèche ▼ sur la colonne Date (défaut Date desc)");
assertEq(boldSeq(tc), ["USDC", "ETH", "AERO", "ZZZ"],
  "txns DÉFAUT: Date desc chronologique (17/07,15/07,01/06) + date null EN DERNIER");

run("sortBy('txn','usd_value','num')"); tc = elements["txnContent"].innerHTML;
assertEq(boldSeq(tc), ["ETH", "USDC", "AERO", "ZZZ"],
  "txns: clic 1 Valeur -> tri NUMÉRIQUE desc (4440,100,45,0)");
run("sortBy('txn','usd_value','num')"); tc = elements["txnContent"].innerHTML;
assertEq(boldSeq(tc), ["ZZZ", "AERO", "USDC", "ETH"],
  "txns: clic 2 Valeur -> sens inversé asc (0,45,100,4440)");

run("sortBy('txn','gas_fee_usd','num')"); tc = elements["txnContent"].innerHTML;
assertEq(boldSeq(tc), ["ETH", "ZZZ", "USDC", "AERO"],
  "txns: Gaz desc (2.5,0.3,0.1) + gaz null (AERO) EN DERNIER");

run("sortBy('txn','token_symbol','str')"); tc = elements["txnContent"].innerHTML;
assertEq(boldSeq(tc), ["AERO", "ETH", "USDC", "ZZZ"], "txns: Token asc alphabétique");

run("sortBy('txn','direction','str')"); tc = elements["txnContent"].innerHTML;
assertEq(boldSeq(tc).slice(0, 2).sort(), ["AERO", "ETH"], "txns: Sens asc (les 2 'in' d'abord)");

run("sortBy('txn','block_time','date')"); tc = elements["txnContent"].innerHTML;
assertEq(boldSeq(tc), ["USDC", "ETH", "AERO", "ZZZ"],
  "txns: retour sur Date (nouvelle colonne) -> desc chronologique + date null EN DERNIER");
run("sortBy('txn','block_time','date')"); tc = elements["txnContent"].innerHTML;
assertEq(boldSeq(tc), ["AERO", "ETH", "USDC", "ZZZ"],
  "txns: 2e clic Date -> asc chronologique (01/06,15/07,17/07) + date null EN DERNIER");

// ── Persistance + ré-application au refresh ─────────────────────
assertTrue(!!storage["sortState_v1"], "persistance: sortState_v1 écrit en localStorage");
run("txnData = TXNS; renderTxnTable();"); tc = elements["txnContent"].innerHTML; // simule un refresh de données
assertEq(boldSeq(tc), ["AERO", "ETH", "USDC", "ZZZ"],
  "refresh: le tri courant (Date asc) est réappliqué après rechargement des données");

console.log(failures === 0 ? "\nSMOKE-TEST: ALL PASS ✔" : "\nSMOKE-TEST: " + failures + " FAILURE(S) ✘");
process.exit(failures === 0 ? 0 : 1);
