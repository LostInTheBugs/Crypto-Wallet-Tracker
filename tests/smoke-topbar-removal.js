// Smoke test: top bar removal (2026.07.19)
// Verifies removed functions are gone and core functions still work.
// Run: node tests/smoke-topbar-removal.js

function makeEl(){
  return {classList:{add:function(){},remove:function(){},toggle:function(){},contains:function(){return false}},style:{cssText:'',display:''},innerHTML:'',textContent:'',value:'',checked:false,disabled:false,parentElement:null,querySelector:function(){return null},querySelectorAll:function(){return[]},addEventListener:function(){},getContext:function(){return null},options:[],insertAdjacentHTML:function(){},remove:function(){},focus:function(){},getAttribute:function(){return''},setAttribute:function(){}};
}
localStorage={}; localStorage.getItem=function(k){return null}; localStorage.setItem=function(k,v){};
var mockEl=makeEl();
document={getElementById:function(id){return mockEl;},querySelector:function(s){return mockEl;},querySelectorAll:function(s){return [mockEl];},createElement:function(){return makeEl()},body:{appendChild:function(){}},documentElement:{classList:{add:function(){},remove:function(){},contains:function(){return false}}},addEventListener:function(){}};
setTimeout=function(fn){if(typeof fn==='function')fn();return 1;};
setInterval=function(){return 1;};clearInterval=function(){};clearTimeout=function(){};
fetch=function(){return Promise.resolve({ok:true,json:function(){return Promise.resolve([])}});};
Chart=function(){this.destroy=function(){};this.getContext=function(){return{}};};Chart.prototype={};
confirm=function(){return false};alert=function(){};window={};
URL={createObjectURL:function(){return''},revokeObjectURL:function(){}};
location={reload:function(){},hash:''};navigator={serviceWorker:{register:function(){return Promise.resolve()}}};
encodeURIComponent=function(s){return s};

var fs=require('fs');
var html=fs.readFileSync(__dirname+'/../public/index.html','utf8');
var m=html.match(/<script>([\s\S]*)<\/script>/);
try{ eval(m[1]); } catch(e){ console.log('FAIL: eval error:',e.message); process.exit(1); }

var ok=true;
if(typeof applyGlobalSearch!=='undefined'){console.log('FAIL: applyGlobalSearch exists');ok=false}
if(typeof populateQuickWallet!=='undefined'){console.log('FAIL: populateQuickWallet exists');ok=false}
if(typeof changeWallet!=='undefined'){console.log('FAIL: changeWallet exists');ok=false}
if(typeof renderWalletTabs!=='undefined'){console.log('FAIL: renderWalletTabs exists');ok=false}
if(activeWallet!=='ALL'){console.log('FAIL: activeWallet='+activeWallet);ok=false}
try{selectWallet('0x')}catch(e){console.log('FAIL: selectWallet:',e.message);ok=false}
try{switchPage('dashboard')}catch(e){console.log('FAIL: switchPage:',e.message);ok=false}
try{esc('<x>')}catch(e){console.log('FAIL: esc:',e.message);ok=false}
try{t('dashboard')}catch(e){console.log('FAIL: t():',e.message);ok=false}
if(ok){console.log('SMOKE TEST PASSED');process.exit(0)}
else{console.log('SMOKE TEST FAILED');process.exit(1)}
