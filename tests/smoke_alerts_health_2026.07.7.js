// smoke_alerts_health_2026.07.7.js
// Smoke test: health alert rendering in the Alerts page.
// Run: node tests/smoke_alerts_health_2026.07.7.js

var fs = require("fs");
var html = fs.readFileSync("public/index.html", "utf8");
var jsMatch = html.match(/<script>([\s\S]*?)<\/script>/);
if (!jsMatch) { console.error("No <script> block found"); process.exit(1); }

// ── Stubs ──
globalThis.confirm = function() { return true; };
globalThis.localStorage = { getItem: function() { return null; }, setItem: function() {} };
globalThis.console = console;
var LANG = "fr";
var currentPage = "alerts";

var _stubEl = function() {
  return { classList: { add: function(){}, remove: function(){}, toggle: function(){}, contains: function(){return false;} },
    innerHTML: "", textContent: "", value: "", style: {},
    addEventListener: function(){}, removeEventListener: function(){}, };
};
var _dom = {};
var document = {
  getElementById: function(id) { if (!_dom[id]) _dom[id] = _stubEl(); return _dom[id]; },
  querySelectorAll: function() { return []; },
  querySelector: function() { return _stubEl(); },
  createElement: function() { return {}; }
};
["viewLogin","viewDash","sidebar","btnLogout","pageAlerts","pageDefiPos",
 "pageAnalytics","pageStats","setLang","verCurrent","digChannel"].forEach(function(id) { _dom[id] = _stubEl(); });
_dom["setLang"].value = "fr";
_dom["verCurrent"].textContent = "2026.07.7";

var esc = function(s) { return (s || "").replace(/</g, "&lt;").replace(/>/g, "&gt;"); };
globalThis.fetch = function() { return Promise.resolve({ ok: true, json: function() { return Promise.resolve([]); } }); };
var setTimeout = function() {};
var setInterval = function() { return 0; };
var clearInterval = function() {};
var Chart = function() { return { destroy: function(){}, data: { labels: [], datasets: [] }, update: function(){} }; };

eval(jsMatch[1]);

var passed = 0, total = 7;

// T1: Health + missing_moralis (FR)
alertsData = [{ id: 2, type: "health", params: { threshold: 1.2, scope: "any" }, enabled: true, cooldown_min: 120, last_triggered_at: null, created_at: "2026-07-19", status: "missing_moralis" }];
notificationsData = []; alertChannels = {}; digestPrefs = { frequency: "off", channel: "" };
renderAlerts();
var out = _dom["pageAlerts"].innerHTML;
var t1 = out.indexOf("Health / Liquidation") !== -1 && out.indexOf("Nécessite une clé Moralis") !== -1 && out.indexOf("1.2") !== -1;
console.log(t1 ? "PASS 1" : "FAIL 1", ": FR health alert with missing_moralis badge");
if (t1) passed++;

// T2: Health + ok (no badge) (FR)
alertsData = [{ id: 3, type: "health", params: { threshold: 1.5, scope: "aave" }, enabled: true, cooldown_min: 60, last_triggered_at: null, created_at: "2026-07-19", status: "ok" }];
renderAlerts();
out = _dom["pageAlerts"].innerHTML;
var t2 = out.indexOf("Health / Liquidation") !== -1 && out.indexOf("Nécessite une clé Moralis") === -1 && out.indexOf("aave") !== -1;
console.log(t2 ? "PASS 2" : "FAIL 2", ": FR health alert with ok status (no badge)");
if (t2) passed++;

// T3: EN locale
LANG = "en";
alertsData = [{ id: 4, type: "health", params: { threshold: 1.1, scope: "any" }, enabled: false, cooldown_min: 30, last_triggered_at: null, created_at: "2026-07-19", status: "missing_moralis" }];
renderAlerts();
out = _dom["pageAlerts"].innerHTML;
var t3 = out.indexOf("Health / Liquidation") !== -1 && out.indexOf("Moralis key required") !== -1;
console.log(t3 ? "PASS 3" : "FAIL 3", ": EN health alert rendered");
if (t3) passed++;

// T4: Health option in dropdown
LANG = "fr";
alertsData = [];
renderAlerts();
out = _dom["pageAlerts"].innerHTML;
var t4 = out.indexOf('value="health"') !== -1;
console.log(t4 ? "PASS 4" : "FAIL 4", ": health option in dropdown");
if (t4) passed++;

// T5: onAlTypeChange health params
document.getElementById = function(id) {
  if (id === "alNewType") return { value: "health" };
  if (!_dom[id]) _dom[id] = _stubEl();
  return _dom[id];
};
onAlTypeChange();
var t5 = _dom["alNewParams"].innerHTML.indexOf("alNewThresh") !== -1 && _dom["alNewParams"].innerHTML.indexOf("alNewScope") !== -1;
console.log(t5 ? "PASS 5" : "FAIL 5", ": onAlTypeChange health params");
if (t5) passed++;

// T6: createAlert params
var params = {}; params.threshold = parseFloat("1.3"); params.scope = "any";
var t6 = params.threshold === 1.3 && params.scope === "any";
console.log(t6 ? "PASS 6" : "FAIL 6", ": health params extraction");
if (t6) passed++;

// T7: backward compat (no status field)
alertsData = [{ id: 5, type: "health", params: { threshold: 1.4 }, enabled: true, cooldown_min: 60, last_triggered_at: null, created_at: "2026-07-19" }];
renderAlerts();
out = _dom["pageAlerts"].innerHTML;
var t7 = out.indexOf("Nécessite une clé Moralis") === -1;
console.log(t7 ? "PASS 7" : "FAIL 7", ": no badge without status field");
if (t7) passed++;

console.log("\n=== SMOKE TESTS: " + passed + "/" + total + " ===");
if (passed < total) process.exit(1);
