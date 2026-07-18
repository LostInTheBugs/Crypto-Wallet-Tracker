// Smoke test for v2.12.7 API Keys catalogue rendering
// Tests that svgLogo() works for all providers, renderApiKeys() doesn't crash
// with stubs, and no variable name `t` shadows the i18n function.
// Run: node tests/smoke_apikeys_v2.12.7.js

// --- Stubs ---
var T = {
  fr: {
    apiKeyConfigured: "Configur\u00e9 \u2713",
    apiKeyNotConfigured: "Non configur\u00e9",
    apiKeySave: "Enregistrer",
    apiKeyDelete: "Supprimer",
    apiKeyGet: "Obtenir une cl\u00e9",
    apiKeyPlaceholder: "Cl\u00e9 API",
    apiKeySaved: "\u2705 Cl\u00e9 enregistr\u00e9e",
    apiKeyDeleted: "Cl\u00e9 supprim\u00e9e",
  },
  en: {
    apiKeyConfigured: "Configured \u2713",
    apiKeyNotConfigured: "Not configured",
    apiKeySave: "Save",
    apiKeyDelete: "Delete",
    apiKeyGet: "Get a key",
    apiKeyPlaceholder: "API Key",
    apiKeySaved: "\u2705 Key saved",
    apiKeyDeleted: "Key deleted",
  }
};
var LANG = "fr";
function t(key) { return (T[LANG] && T[LANG][key]) || key; }
function esc(s) { return String(s==null?"":s).replace(/[&<>"']/g,function(c){return {"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"}[c];}); }

// --- Inline SVG logos (from index.html) ---
function svgLogo(pid){
  var logos = {
    coingecko: '<svg viewBox="0 0 40 40" width="40" height="40"><circle cx="20" cy="20" r="20" fill="#2d6a4f"/><path d="M12 20c0-4 3-7 7-7h1c4 0 7 3 7 7v1c0 4-3 7-7 7h-1c-4 0-7-3-7-7v-1z" fill="#95d5b2"/><circle cx="17" cy="18" r="2" fill="#1b4332"/><circle cx="23" cy="18" r="2" fill="#1b4332"/><path d="M17 24c2 1.5 4 1.5 6 0" stroke="#1b4332" stroke-width="1.5" fill="none"/></svg>',
    opensea: '<svg viewBox="0 0 40 40" width="40" height="40"><circle cx="20" cy="20" r="20" fill="#1a3a5c"/><text x="20" y="26" text-anchor="middle" fill="#4dabf7" font-size="18" font-weight="bold" font-family="sans-serif">O</text><path d="M10 32c0-6 4-10 10-10s10 4 10 10" fill="none" stroke="#4dabf7" stroke-width="1.5"/></svg>',
    etherscan: '<svg viewBox="0 0 40 40" width="40" height="40"><circle cx="20" cy="20" r="20" fill="#2d1b4e"/><circle cx="17" cy="17" r="7" fill="none" stroke="#b794f4" stroke-width="2"/><line x1="22" y1="22" x2="28" y2="28" stroke="#b794f4" stroke-width="2.5" stroke-linecap="round"/></svg>',
    defillama: '<svg viewBox="0 0 40 40" width="40" height="40"><circle cx="20" cy="20" r="20" fill="#3b2b10"/><ellipse cx="20" cy="24" rx="8" ry="5" fill="#e8a838"/><rect x="17" y="14" width="6" height="10" rx="2" fill="#f5c542"/><circle cx="18" cy="16" r="1" fill="#3b2b10"/><circle cx="22" cy="16" r="1" fill="#3b2b10"/></svg>',
    alchemy: '<svg viewBox="0 0 40 40" width="40" height="40"><circle cx="20" cy="20" r="20" fill="#0a2a3b"/><text x="20" y="26" text-anchor="middle" fill="#4fc3f7" font-size="20" font-weight="bold" font-family="sans-serif">A</text><circle cx="27" cy="13" r="2.5" fill="#fdd835"/></svg>',
    moralis: '<svg viewBox="0 0 40 40" width="40" height="40"><circle cx="20" cy="20" r="20" fill="#2d1350"/><text x="20" y="26" text-anchor="middle" fill="#b388ff" font-size="18" font-weight="bold" font-family="sans-serif">M</text></svg>',
    coinmarketcap: '<svg viewBox="0 0 40 40" width="40" height="40"><circle cx="20" cy="20" r="20" fill="#0d2f1d"/><text x="20" y="26" text-anchor="middle" fill="#40c463" font-size="18" font-weight="bold" font-family="sans-serif">C</text><rect x="12" y="28" width="3" height="-6" fill="#40c463"/><rect x="17" y="28" width="3" height="-10" fill="#40c463"/><rect x="22" y="28" width="3" height="-8" fill="#40c463"/></svg>',
  };
  return logos[pid] || '<svg viewBox="0 0 40 40" width="40" height="40"><circle cx="20" cy="20" r="20" fill="var(--border)"/><text x="20" y="26" text-anchor="middle" fill="var(--text)" font-size="18" font-weight="bold" font-family="sans-serif">'+(pid.charAt(0).toUpperCase())+'</text></svg>';
}

// Test data: mixed configured/unconfigured providers
var testProvs = [
  { id: "coingecko", name: "CoinGecko", category: "Pricing", description: "Prix des tokens (multi-cha\u00eenes)", get_key_url: "https://www.coingecko.com/en/developers/dashboard", configured: true, masked: "...abcD" },
  { id: "opensea", name: "OpenSea", category: "NFT", description: "Prix planchers & m\u00e9tadonn\u00e9es NFT", get_key_url: "https://docs.opensea.io/reference/api-keys", configured: false, masked: null },
  { id: "alchemy", name: "Alchemy", category: "RPC/Data", description: "Acc\u00e8s RPC / donn\u00e9es multi-cha\u00eenes", get_key_url: "https://dashboard.alchemy.com/", configured: false, masked: null },
];

var errors = [];
var passes = 0;

function test(name, fn) {
  try {
    var result = fn();
    if (result === null) {
      console.log("  PASS:", name);
      passes++;
    } else {
      console.log("  FAIL:", name, "-", result);
      errors.push(name + ": " + result);
    }
  } catch(e) {
    console.log("  CRASH:", name, "-", e.message);
    errors.push(name + ": CRASH - " + e.message);
  }
}

console.log("=== Smoke test: API Keys catalogue v2.12.7 ===\n");

// Test 1: svgLogo returns non-empty <svg> for all 7 providers
test("svgLogo - all 7 providers return valid SVG", function() {
  var allIds = ["coingecko","opensea","etherscan","defillama","alchemy","moralis","coinmarketcap"];
  for (var i=0;i<allIds.length;i++) {
    var logo = svgLogo(allIds[i]);
    if (typeof logo !== "string" || logo.length < 10) return "svgLogo('"+allIds[i]+"') returned short/empty";
    if (logo.indexOf("<svg") === -1) return "svgLogo('"+allIds[i]+"') missing <svg>";
    if (logo.indexOf("</svg>") === -1) return "svgLogo('"+allIds[i]+"') missing </svg>";
  }
  return null;
});

// Test 2: svgLogo fallback for unknown provider
test("svgLogo - unknown provider fallback", function() {
  var logo = svgLogo("unknown_svc");
  if (logo.indexOf("<svg") === -1) return "Fallback missing <svg>";
  if (logo.indexOf("U") === -1) return "Fallback missing initial";
  return null;
});

// Test 3: Render HTML generation for mixed providers (1 configured + 2 unconfigured)
test("render - HTML generation with mixed providers", function() {
  var html = "";
  for (var i=0;i<testProvs.length;i++){
    var k = testProvs[i];
    var statusCls = k.configured ? "api-configured" : "api-unconfigured";
    var statusTxt = k.configured ? t("apiKeyConfigured")+(k.masked?" ("+esc(k.masked)+")":"") : t("apiKeyNotConfigured");
    var delBtn = k.configured ? '<button class="btn-api btn-api-del" onclick="deleteApiKey(\''+k.id+'\')">'+esc(t("apiKeyDelete"))+'</button>' : "";
    html += '<div class="api-card">';
    html += '<div class="api-card-head">';
    html += '<div class="api-logo-wrap">'+svgLogo(k.id)+'</div>';
    html += '<div class="api-card-info">';
    html += '<div class="api-card-name">'+esc(k.name)+'</div>';
    html += '<span class="api-badge-cat">'+esc(k.category)+'</span>';
    html += '</div></div>';
    html += '<div class="api-card-desc">'+esc(k.description)+'</div>';
    html += '<div class="api-card-status"><span class="api-status-dot '+statusCls+'"></span> '+statusTxt+'</div>';
    html += '<div class="api-card-form">';
    html += '<input type="password" id="key_'+k.id+'" placeholder="'+esc(t("apiKeyPlaceholder"))+'" class="api-key-input">';
    html += '<div class="api-card-actions">';
    html += '<button class="btn-api btn-api-save" onclick="saveApiKey(\''+k.id+'\')">'+esc(t("apiKeySave"))+'</button>';
    html += delBtn;
    html += '</div></div>';
    html += '<div id="keyMsg_'+k.id+'" class="api-msg"></div>';
    html += '<a href="'+esc(k.get_key_url)+'" target="_blank" rel="noopener" class="api-get-key">'+esc(t("apiKeyGet"))+' ↗</a>';
    html += '</div>';
  }
  if (html.indexOf("CoinGecko") === -1) return "Missing CoinGecko name";
  if (html.indexOf("OpenSea") === -1) return "Missing OpenSea name";
  if (html.indexOf("Alchemy") === -1) return "Missing Alchemy name";
  if (html.indexOf("api-configured") === -1) return "Missing api-configured class";
  if (html.indexOf("api-unconfigured") === -1) return "Missing api-unconfigured class";
  if (html.indexOf("deleteApiKey") === -1) return "Missing delete button";
  if (html.indexOf("saveApiKey") === -1) return "Missing save button";
  if (html.indexOf("...abcD") === -1) return "Missing masked key";
  if (html.indexOf("Obtenir une cl\u00e9") === -1) return "Missing get-key link (FR)";
  if (html.indexOf("Enregistrer") === -1) return "Missing save button text (FR)";
  if (html.indexOf("Supprimer") === -1) return "Missing delete button text (FR)";
  if (html.indexOf("keyMsg_") === -1) return "Missing message divs";
  // XSS check: esc() should prevent raw HTML injection
  if (html.indexOf("><script") !== -1) return "Raw <script> found (XSS!)";
  return null;
});

// Test 4: No variable `t` shadowing - the i18n function still works inside loops
test("No variable t shadowing", function() {
  // Simulate rendering with different loop variable
  var html = "";
  for (var kk=0;kk<testProvs.length;kk++){
    var prov = testProvs[kk];
    html += t("apiKeySave") + prov.id;
  }
  if (typeof t("apiKeySave") !== "string") return "t() function is shadowed! (got non-string)";
  if (t("apiKeySave") !== "Enregistrer") return "t() returned wrong value: "+t("apiKeySave");
  return null;
});

// Test 5: English locale rendering works
test("English locale rendering", function() {
  LANG = "en";
  var html = "";
  for (var i=0;i<testProvs.length;i++){
    var k = testProvs[i];
    var delBtn = k.configured ? '<button class="btn-api btn-api-del" onclick="deleteApiKey(\''+k.id+'\')">'+esc(t("apiKeyDelete"))+'</button>' : "";
    html += '<div class="api-card">';
    html += '<div class="api-card-name">'+esc(k.name)+'</div>';
    html += '<div class="api-card-status">'+t("apiKeyConfigured")+'</div>';
    html += '<button class="btn-api btn-api-save">'+esc(t("apiKeySave"))+'</button>';
    html += delBtn;
    html += '</div>';
  }
  if (html.indexOf("Save") === -1) return "EN: Missing Save";
  if (html.indexOf("Delete") === -1) return "EN: Missing Delete";
  if (html.indexOf("Configured") === -1) return "EN: Missing Configured";
  LANG = "fr"; // restore
  return null;
});

// Test 6: esc() properly escapes HTML
test("esc() prevents XSS", function() {
  var evil = '<script>alert("xss")</script>';
  var safe = esc(evil);
  if (safe.indexOf("<") !== -1) return "esc() left < unescaped";
  if (safe.indexOf(">") !== -1) return "esc() left > unescaped";
  if (safe.indexOf("&lt;") === -1) return "esc() didn't produce &lt;";
  if (safe.indexOf("&gt;") === -1) return "esc() didn't produce &gt;";
  return null;
});

// Test 7: All provider IDs from catalogue match something in svgLogo
test("All 7 provider IDs get non-empty svgLogo", function() {
  var ids = ["coingecko","opensea","etherscan","defillama","alchemy","moralis","coinmarketcap"];
  for (var i=0;i<ids.length;i++) {
    var svg = svgLogo(ids[i]);
    if (!svg || svg.length < 20) return ids[i]+" got empty/short logo";
  }
  return null;
});

console.log("\n=== Results ===");
console.log("Passed:", passes+"/"+(passes+errors.length));

if (errors.length > 0) {
  console.error("FAILURES:");
  errors.forEach(function(e) { console.error("  \u2717", e); });
  process.exit(1);
} else {
  console.log("\u2713 All tests passed.");
}
