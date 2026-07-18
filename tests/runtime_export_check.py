#!/usr/bin/env python3
"""Test runtime local des endpoints /api/export/* (2026.07.4).

Sans réseau : _compute_portfolio est monkeypatché, wallets/daily_history/
transactions insérés en SQL direct. À lancer depuis la RACINE du repo
(StaticFiles(directory="public") est relatif au cwd) :

    DB_PATH=/tmp/cwt_export_test.db /tmp/cwt-venv/bin/python tests/runtime_export_check.py
"""

import os
import sqlite3
import sys
import time

os.environ.setdefault("DB_PATH", "/tmp/cwt_export_test.db")
DB = os.environ["DB_PATH"]
if os.path.exists(DB):
    os.remove(DB)

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import app as app_module  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

FAILED = 0


def check(cond, label):
    global FAILED
    print(("OK  " if cond else "FAIL ") + label)
    if not cond:
        FAILED += 1


W1 = "0x15CD7D7A1fc0ca1B91F58d64a591dA4f5C50AD7e"
W2 = "0xEB788C4b57670F5309afE9d6B97929329b593DBd"

FAKE_TOKENS = {
    W1: [
        {"chain": "ethereum", "name": "Ethereum", "symbol": "ETH", "balance": 1.5,
         "usd_value": 4500.0, "usd_price": 3000.0, "icon": "", "type": "native",
         "contract_address": "", "price_unknown": False, "price_confidence": None,
         "category": "wallet"},
        {"chain": "base", "name": 'Weird, "Quoted" Coin', "symbol": "WRD", "balance": 10.0,
         "usd_value": 100.0, "usd_price": 10.0, "icon": "", "type": "ERC-20",
         "contract_address": "0xaaa1", "price_unknown": False, "price_confidence": None,
         "category": "wallet"},
    ],
    W2: [
        {"chain": "ethereum", "name": "Ethereum", "symbol": "ETH", "balance": 0.5,
         "usd_value": 1500.0, "usd_price": 3000.0, "icon": "", "type": "native",
         "contract_address": "", "price_unknown": False, "price_confidence": None,
         "category": "wallet"},
        {"chain": "optimism", "name": "Lido stETH", "symbol": "WSTETH", "balance": 1.0,
         "usd_value": 3600.0, "usd_price": 3600.0, "icon": "", "type": "ERC-20",
         "contract_address": "0xbbb2", "price_unknown": False, "price_confidence": None,
         "category": "staked"},
    ],
}


async def fake_compute_portfolio(address, *args, **kwargs):
    toks = [dict(t) for t in FAKE_TOKENS.get(address, [])]
    total = round(sum(t["usd_value"] for t in toks), 2)
    chains = {}
    for t in toks:
        chains[t["chain"]] = chains.get(t["chain"], 0) + t["usd_value"]
    return {"address": address, "total_usd": total, "token_count": len(toks),
            "chain_count": len(chains), "chains": chains, "tokens": toks,
            "staked_usd": round(sum(t["usd_value"] for t in toks if t["category"] == "staked"), 2)}


app_module._compute_portfolio = fake_compute_portfolio

client = TestClient(app_module.app)
client.__enter__()   # exécute le lifespan (création des tables) — pitfall 140

# ── Compte + login ──────────────────────────────────────────────
r = client.post("/api/auth/register", json={"username": "exporter", "password": "test1234"})
check(r.status_code == 200, "register 200")
r = client.post("/api/auth/login", json={"username": "exporter", "password": "test1234"})
check(r.status_code == 200, "login 200 (cookie)")

# Auth requise sur les exports
anon = TestClient(app_module.app)
check(anon.get("/api/export/holdings.csv").status_code == 401, "holdings.csv sans cookie → 401")

# ── Données en SQL direct (pas de POST /api/wallets → réseau) ──
conn = sqlite3.connect(DB)
cur = conn.cursor()
uid = cur.execute("SELECT id FROM users WHERE username='exporter'").fetchone()[0]
app_module._last_tx_refresh[uid] = time.time()   # neutralise _daily_tx_refresh (réseau)
cur.execute("INSERT INTO wallets (user_id, address, label) VALUES (?,?,?)", (uid, W1, "Main"))
cur.execute("INSERT INTO wallets (user_id, address, label) VALUES (?,?,?)", (uid, W2, "Second"))
# daily_history : agrégat (token_symbol NULL) + per-token pour le cost basis
cur.execute("INSERT INTO daily_history (user_id, wallet_address, date, value_usd, cost_basis_usd, net_flows_usd, token_symbol, chain) VALUES (?,?,?,?,?,?,NULL,NULL)",
            (uid, W1, "2026-07-18", 4600.0, 3000.0, 0.0))
cur.execute("INSERT INTO daily_history (user_id, wallet_address, date, value_usd, cost_basis_usd, net_flows_usd, token_symbol, chain) VALUES (?,?,?,?,?,?,?,NULL)",
            (uid, W1, "2026-07-18", 4500.0, 3000.0, 0.0, "eth"))
# transactions : un swap (2 jambes même tx) + un send, wallet W1
tx_cols = "(user_id, wallet_address, token_symbol, token_name, amount, usd_price, usd_value, chain, tx_hash, block_time, direction, log_index, gas_fee_usd, contract_address)"
cur.execute(f"INSERT INTO transactions {tx_cols} VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (uid, W1, "ETH", "Ethereum", 1.0, 3000, 3000, "ethereum", "0xswap1",
             "2026-07-01T10:00:00.000000Z", "out", 1, 2.5, ""))
cur.execute(f"INSERT INTO transactions {tx_cols} VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (uid, W1, "USDC", "USD Coin", 2998.0, 1, 2998, "ethereum", "0xswap1",
             "2026-07-01T10:00:00.000000Z", "in", 2, 0, "0xusdc"))
cur.execute(f"INSERT INTO transactions {tx_cols} VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (uid, W1, "ETH", "Ethereum", 0.25, 3200, 800, "base", "0xsend1",
             "2026-07-05T08:30:00.000000Z", "out", 0, 0.05, ""))
conn.commit()
conn.close()

# ── holdings.csv ────────────────────────────────────────────────
r = client.get("/api/export/holdings.csv?address=ALL")
check(r.status_code == 200, "holdings.csv ALL → 200")
cd = r.headers.get("content-disposition", "")
check(cd.startswith("attachment;") and "holdings_" in cd and cd.endswith('.csv"'),
      f"holdings.csv: Content-Disposition attachment ({cd})")
check("text/csv" in r.headers.get("content-type", ""), "holdings.csv: Content-Type text/csv")
lines = r.text.splitlines()
check(lines[0] == "token_name,symbol,chain,balance,usd_price,usd_value,category,cost_basis,pnl",
      "holdings.csv: en-têtes exacts")
check(len(lines) == 1 + 3, f"holdings.csv: 3 lignes (ETH fusionné 2 wallets + WRD + WSTETH) — {len(lines)-1}")
eth_line = [ln for ln in lines if ln.startswith("Ethereum,ETH,ethereum")][0]
check(",2," in eth_line and ",6000.00," in eth_line, "holdings.csv: ETH agrégé 2 wallets (bal=2, $6000)")
check(any('"Weird, ""Quoted"" Coin"' in ln for ln in lines), "holdings.csv: quoting CSV du nom piégé")
# cost_basis ETH: rescale 3000/4500 × usd_val(6000)=... per-wallet: W1 ETH cost≈3000×(4500/4500)=3000 ; W2 ETH sans history → None → agrégat None
check(eth_line.endswith(",,"), "holdings.csv: coût partiellement inconnu → cellules vides (honnête)")

r = client.get("/api/export/holdings.csv?address=" + W1)
check(r.status_code == 200 and "Ethereum,ETH,ethereum" in r.text, "holdings.csv wallet unique → 200")
w1_eth = [ln for ln in r.text.splitlines() if ln.startswith("Ethereum,ETH,")][0]
check(w1_eth.split(",")[7] == "3000.00", "holdings.csv W1: cost_basis=3000 (daily_history rescale)")

r = client.get("/api/export/holdings.csv?address=bad")
check(r.status_code == 400, "holdings.csv adresse invalide → 400")

# ── transactions.csv ────────────────────────────────────────────
r = client.get("/api/export/transactions.csv?address=ALL")
check(r.status_code == 200, "transactions.csv → 200")
cd = r.headers.get("content-disposition", "")
check("attachment" in cd and "transactions_" in cd, "transactions.csv: attachment")
tl = r.text.splitlines()
check(tl[0] == "date,type,tokens,amount,usd_value,gas_fee_usd,chain,tx_hash",
      "transactions.csv: en-têtes exacts")
check(len(tl) == 1 + 2, f"transactions.csv: 3 jambes → 2 événements — {len(tl)-1}")
check(any(",Swap,ETH -> USDC," in ln and "-1 ETH / +2998 USDC" in ln for ln in tl),
      "transactions.csv: swap détecté (logique v2.12.4)")
check(any(",Envoyé,ETH,-0.25," in ln for ln in tl), "transactions.csv: send → Envoyé signé")

# ── pnl.csv ─────────────────────────────────────────────────────
r = client.get("/api/export/pnl.csv?address=" + W1)
check(r.status_code == 200, "pnl.csv → 200")
check("pnl_report_" in r.headers.get("content-disposition", ""), "pnl.csv: attachment pnl_report_*")
pl = r.text.splitlines()
check(pl[0] == "symbol,chain,quantity,avg_cost_usd,cost_basis_usd,current_value_usd,unrealized_pnl_usd",
      "pnl.csv: en-têtes exacts")
eth_p = [ln for ln in pl if ln.startswith("ETH,ethereum")][0]
check(eth_p == "ETH,ethereum,1.5,2000,3000.00,4500.00,1500.00",
      f"pnl.csv: quantité/coût moyen/coût/valeur/PnL corrects ({eth_p})")

# ── summary.pdf ─────────────────────────────────────────────────
r = client.get("/api/export/summary.pdf?address=ALL")
check(r.status_code == 200, "summary.pdf → 200")
check(r.headers.get("content-type", "").startswith("application/pdf"), "summary.pdf: Content-Type application/pdf")
cd = r.headers.get("content-disposition", "")
check("attachment" in cd and "portfolio_summary_" in cd and cd.endswith('.pdf"'),
      "summary.pdf: attachment portfolio_summary_*.pdf")
check(r.content.startswith(b"%PDF-1.4") and r.content.rstrip().endswith(b"%%EOF"),
      "summary.pdf: PDF valide (magic + EOF)")
check(b"Synthese du portefeuille" in r.content and b"Tous les wallets \\(2\\)" in r.content,
      "summary.pdf: titre + périmètre ALL (parenthèses échappées)")
check(b"9,700.00" in r.content, "summary.pdf: valeur totale 9700 rendue")
check(b"Repartition par chaine" in r.content and b"Top holdings" in r.content,
      "summary.pdf: sections répartition + top holdings")

# ── Robustesse : utilisateur sans aucun wallet ──────────────────
client.post("/api/auth/register", json={"username": "vide", "password": "test1234"})
client.post("/api/auth/login", json={"username": "vide", "password": "test1234"})
r = client.get("/api/export/holdings.csv?address=ALL")
check(r.status_code == 200 and len(r.text.splitlines()) == 1,
      "holdings.csv sans wallet → 200, en-têtes seuls")
r = client.get("/api/export/summary.pdf?address=ALL")
check(r.status_code == 200 and r.content.startswith(b"%PDF-1.4"),
      "summary.pdf sans wallet → 200, PDF valide")

client.__exit__(None, None, None)

print()
if FAILED:
    print(f"❌ {FAILED} échec(s)")
    sys.exit(1)
print("✅ Runtime export OK")
