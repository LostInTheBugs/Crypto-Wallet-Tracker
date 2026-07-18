// Smoke-test v2.12.4 — détection des SWAPS dans la page Transactions.
// Exécute le <script> inline de public/index.html dans un sandbox vm avec stubs,
// puis vérifie : rendu d'événements swap/send/receive sans crash, pas de
// shadowing de t(), badge Swap, échange signé (-A → +B), jambes multiples,
// rétro-compat lignes v2.12.3 (direction seule), tri sur les événements,
// et construction de l'URL avec le paramètre type=.
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
  fetch: function (u) { sandbox.__lastUrl = String(u); return new Promise(function () {}); },
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
function assertEq(actual, expected, label) {
  const a = JSON.stringify(actual), e = JSON.stringify(expected);
  if (a === e) { console.log("OK  " + label + " -> " + a); }
  else { console.error("FAIL " + label + "\u000A  attendu: " + e + "\u000A  obtenu : " + a); failures++; }
}
function assertTrue(cond, label) {
  if (cond) console.log("OK  " + label);
  else { console.error("FAIL " + label); failures++; }
}
function boldSeq(htmlStr) { // ordre des <b>…</b> = ordre des lignes rendues
  return Array.from(htmlStr.matchAll(/<b>([^<]*)<\/b>/g)).map(x => x[1]);
}
function run(js) { return vm.runInContext(js, sandbox); }

// ── Version bump ────────────────────────────────────────────────
assertTrue(html.indexOf('id="verCurrent">v2.12.4<') !== -1, "version: verCurrent = v2.12.4");

// ── Données d'exemple : événements regroupés v2.12.4 ────────────
const W = "0xAAAA111122223333444455556666777788889999";
sandbox.TXNS = {
  items: [
    { type: "swap", direction: "swap", chain: "ethereum", tx_hash: "0xswap", wallet_address: W,
      block_time: "2026-07-15 10:00:05", gas_fee_usd: 2.5, usd_value: 4440, legs: 2,
      sent: [{ symbol: "ETH", name: "Ether", amount: 1.2, usd_value: 4440, contract: "" }],
      received: [{ symbol: "USDC", name: "USD Coin", amount: 4435, usd_value: 4435, contract: "0xusdc" }],
      sent_symbol: "ETH", sent_amount: 1.2, recv_symbol: "USDC", recv_amount: 4435,
      token_symbol: "ETH → USDC", token_name: "", amount: 1.2, usd_price: null,
      explorer_url: "https://eth.blockscout.com/tx/0xswap" },
    { type: "send", direction: "out", chain: "base", tx_hash: "0xsend", wallet_address: W,
      block_time: "2026-07-16 08:00:00", gas_fee_usd: 0.1, usd_value: 45, legs: 1,
      sent: [{ symbol: "AERO", name: "Aerodrome", amount: 50, usd_value: 45, contract: "" }], received: [],
      sent_symbol: "AERO", sent_amount: 50, recv_symbol: null, recv_amount: null,
      token_symbol: "AERO", token_name: "Aerodrome", amount: 50, usd_price: 0.9, explorer_url: "" },
    { type: "receive", direction: "in", chain: "arbitrum", tx_hash: "0xrecv", wallet_address: W,
      block_time: "2026-07-17 09:00:00", gas_fee_usd: 0, usd_value: 950, legs: 1,
      sent: [], received: [{ symbol: "WBTC", name: "Wrapped BTC", amount: 0.01, usd_value: 950, contract: "" }],
      sent_symbol: null, sent_amount: null, recv_symbol: "WBTC", recv_amount: 0.01,
      token_symbol: "WBTC", token_name: "Wrapped BTC", amount: 0.01, usd_price: 95000, explorer_url: "" },
    { type: "swap", direction: "swap", chain: "base", tx_hash: "0xmulti", wallet_address: W,
      block_time: "2026-07-14 12:00:00", gas_fee_usd: 1.1, usd_value: 1050, legs: 3,
      sent: [{ symbol: "WETH", name: "Wrapped Ether", amount: 0.27, usd_value: 1000, contract: "" },
             { symbol: "AERO", name: "Aerodrome", amount: 55, usd_value: 50, contract: "" }],
      received: [{ symbol: "USDC", name: "USD Coin", amount: 1040, usd_value: 1040, contract: "" }],
      sent_symbol: "WETH", sent_amount: 0.27, recv_symbol: "USDC", recv_amount: 1040,
      token_symbol: "WETH → USDC", token_name: "", amount: 0.27, usd_price: null, explorer_url: "" },
    // Ligne à L'ANCIEN format (v2.12.3, transfert brut sans type/legs/sent) → rétro-compat rendu
    { block_time: null, wallet_address: W, token_symbol: "ZZZ", token_name: "Zzz", chain: "zora",
      amount: 7, usd_price: 0, usd_value: 0, gas_fee_usd: 0.3, direction: "in", explorer_url: "" },
  ], total: 5, page: 0,
};

// ── Rendu par défaut (Date desc) ────────────────────────────────
run("txnData = TXNS; renderTxnTable();");
let tc = elements["txnContent"].innerHTML;
assertTrue(typeof run("t") === "function" && typeof run('t("swap")') === "string",
  "t() reste une fonction i18n après rendu (pas de shadowing)");
assertTrue(typeof run("esc") === "function" && typeof run("fmtCurr") === "function",
  "esc()/fmtCurr() non masquées");
assertEq(boldSeq(tc), ["WBTC", "AERO", "ETH", "USDC", "WETH", "USDC", "ZZZ"],
  "DÉFAUT Date desc: recv(17) > send(16) > swap(15) > swap multi(14) > date null EN DERNIER");
assertTrue(tc.indexOf("🔄") !== -1 && tc.indexOf("#a371f7") !== -1,
  "swap: badge 🔄 avec pilule violette distincte (#a371f7)");
assertTrue(tc.indexOf("Swap") !== -1, "swap: libellé i18n 'Swap' affiché");
assertTrue(tc.indexOf("→") !== -1, "swap: flèche → entre les deux jambes (ETH → USDC)");
assertTrue(tc.indexOf('white-space:nowrap">-') !== -1,
  "swap: montant sortant signé -X présent (rouge)");
assertTrue(tc.indexOf('white-space:nowrap">+') !== -1, "swap: montant entrant signé +Y présent (vert)");
assertTrue(tc.indexOf("(3 " + run('t("legsLbl")') + ")") !== -1,
  "swap multi: indicateur discret du nombre de jambes '(3 jambes)'");
assertTrue(tc.indexOf("📥") !== -1 && tc.indexOf("📤") !== -1,
  "send/receive: badges 📥/📤 conservés");
assertTrue(tc.indexOf("https://eth.blockscout.com/tx/0xswap") !== -1,
  "swap: lien explorer conservé");
assertTrue(tc.indexOf("2.50") !== -1, "swap: gaz affiché une seule fois (2.50)");
assertTrue(boldSeq(tc)[boldSeq(tc).length - 1] === "ZZZ",
  "rétro-compat: ligne v2.12.3 (direction seule, sans type) rendue sans crash");

// ── Tri sur les ÉVÉNEMENTS ──────────────────────────────────────
run("sortBy('txn','usd_value','num')"); tc = elements["txnContent"].innerHTML;
assertEq(boldSeq(tc), ["ETH", "USDC", "WETH", "USDC", "WBTC", "AERO", "ZZZ"],
  "tri Valeur desc: swap 4440 > swap 1050 > recv 950 > send 45 > 0");
run("sortBy('txn','direction','str')"); tc = elements["txnContent"].innerHTML;
assertEq(boldSeq(tc), ["WBTC", "ZZZ", "AERO", "ETH", "USDC", "WETH", "USDC"],
  "tri Sens asc: in < out < swap (le type Swap est triable)");
run("sortBy('txn','block_time','date')"); tc = elements["txnContent"].innerHTML;
assertEq(boldSeq(tc), ["WBTC", "AERO", "ETH", "USDC", "WETH", "USDC", "ZZZ"],
  "retour tri Date desc: fonctionne toujours (v2.12.3 conservé)");
assertTrue(tc.indexOf("Date ▼") !== -1, "flèche ▼ sur la colonne Date");

// ── Filtre type= dans l'URL ─────────────────────────────────────
documentStub.getElementById("txnDir").value = "swap";
run("loadTransactions()");
assertTrue(String(sandbox.__lastUrl).indexOf("type=swap") !== -1,
  "filtre: l'option Swap envoie type=swap à l'API -> " + sandbox.__lastUrl);
documentStub.getElementById("txnDir").value = "receive";
run("loadTransactions()");
assertTrue(String(sandbox.__lastUrl).indexOf("type=receive") !== -1,
  "filtre: l'option Entrant envoie type=receive");

console.log(failures === 0 ? "\u000ASMOKE-TEST SWAP UI: ALL PASS" : "\u000ASMOKE-TEST SWAP UI: " + failures + " FAILURE(S)");
process.exit(failures === 0 ? 0 : 1);
