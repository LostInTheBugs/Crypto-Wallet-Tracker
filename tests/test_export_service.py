#!/usr/bin/env python3
"""Tests unitaires du service export (2026.07.4) — stdlib only.

Run: python3 tests/test_export_service.py
"""

import csv
import io
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from services.export_service import (  # noqa: E402
    rows_to_csv, fmt_num, aggregate_holdings, build_holdings_rows,
    build_pnl_rows, build_transaction_rows, build_summary_pdf,
    HOLDINGS_HEADERS, TRANSACTIONS_HEADERS, PNL_HEADERS,
)
from services.tx_events import group_transaction_events  # noqa: E402

FAILED = 0


def check(cond, label):
    global FAILED
    if cond:
        print(f"OK  {label}")
    else:
        print(f"FAIL {label}")
        FAILED += 1


# ── fmt_num ─────────────────────────────────────────────────────────
check(fmt_num(None) == "", "fmt_num: None → cellule vide")
check(fmt_num(float("nan")) == "", "fmt_num: NaN → cellule vide")
check(fmt_num(float("inf")) == "", "fmt_num: inf → cellule vide")
check(fmt_num(1234.5678, 2) == "1234.57", "fmt_num: 2 décimales, point décimal")
check(fmt_num(1.50000000, 8, trim=True) == "1.5", "fmt_num: trim des zéros")
check(fmt_num(0.000000001, 8, trim=True) == "0", "fmt_num: dust → 0")
check(fmt_num("abc") == "", "fmt_num: chaîne invalide → vide")

# ── CSV quoting (RFC 4180) ──────────────────────────────────────────
nasty_name = 'Evil "Token", Inc.\nLigne2'
text = rows_to_csv(["a", "b", "c"], [[nasty_name, "x,y", 'q"q'], [None, "", 0]])
parsed = list(csv.reader(io.StringIO(text)))
check(parsed[0] == ["a", "b", "c"], "csv: en-têtes intacts")
check(parsed[1][0] == nasty_name, "csv: guillemets+virgule+retour ligne round-trip")
check(parsed[1][1] == "x,y" and parsed[1][2] == 'q"q', "csv: virgule et guillemet interne")
check(parsed[2][0] == "" and parsed[2][2] == "0", "csv: None → cellule vide")
check("\r\n" in text, "csv: fins de ligne CRLF")

# ── aggregate_holdings ──────────────────────────────────────────────
tokens = [
    {"name": "Ethereum", "symbol": "ETH", "chain": "ethereum", "balance": 1.0,
     "usd_value": 3000.0, "usd_price": 3000.0, "category": "wallet",
     "cost_basis": 2000.0, "pnl": 1000.0},
    {"name": "Ethereum", "symbol": "ETH", "chain": "ethereum", "balance": 0.5,
     "usd_value": 1500.0, "usd_price": 3000.0, "category": "wallet",
     "cost_basis": 1200.0, "pnl": 300.0},
    {"name": "Ethereum", "symbol": "ETH", "chain": "base", "balance": 2.0,
     "usd_value": 6000.0, "usd_price": 3000.0, "category": "wallet",
     "cost_basis": None, "pnl": None},
    {"name": "USD Coin", "symbol": "USDC", "chain": "base", "balance": 100.0,
     "usd_value": 100.0, "usd_price": 1.0, "category": "wallet"},
    {"symbol": "STETH", "chain": "ethereum", "balance": 1.0, "usd_value": 3100.0,
     "usd_price": 3100.0, "category": "staked", "cost_basis": 1700.0, "pnl": 1400.0},
]
agg = aggregate_holdings(tokens)
keys = [(a["symbol"], a["chain"]) for a in agg]
check(("ETH", "ethereum") in keys and ("ETH", "base") in keys,
      "agg: fusion par (symbole, chaîne) — les chaînes restent séparées")
eth_main = [a for a in agg if a["symbol"] == "ETH" and a["chain"] == "ethereum"][0]
check(abs(eth_main["balance"] - 1.5) < 1e-9, "agg: balances sommées (2 wallets)")
check(abs(eth_main["usd_value"] - 4500.0) < 1e-9, "agg: valeurs sommées")
check(eth_main["cost_basis"] == 3200.0 and eth_main["pnl"] == 1300.0,
      "agg: coûts connus sommés, pnl = valeur − coût")
eth_base = [a for a in agg if a["symbol"] == "ETH" and a["chain"] == "base"][0]
check(eth_base["cost_basis"] is None and eth_base["pnl"] is None,
      "agg: coût inconnu → None (honnête, pitfall 124)")
usdc = [a for a in agg if a["symbol"] == "USDC"][0]
check(usdc["cost_basis"] is None, "agg: cost_basis absent → None")
check(agg[0]["usd_value"] >= agg[-1]["usd_value"], "agg: tri par valeur décroissante")
check(aggregate_holdings(None) == [] and aggregate_holdings([{"bad": 1}, None, 42]) != None,  # noqa: E711
      "agg: entrées invalides tolérées")

# ── build_holdings_rows ─────────────────────────────────────────────
rows = build_holdings_rows(agg)
check(len(rows) == len(agg), "holdings: une ligne par token agrégé")
check(len(rows[0]) == len(HOLDINGS_HEADERS), "holdings: nb colonnes = en-têtes")
eth_row = [r for r in rows if r[1] == "ETH" and r[2] == "ethereum"][0]
check(eth_row[3] == "1.5" and eth_row[5] == "4500.00", "holdings: balance trim + valeur 2 déc.")
check(eth_row[7] == "3200.00" and eth_row[8] == "1300.00", "holdings: cost_basis et pnl formatés")
base_row = [r for r in rows if r[1] == "ETH" and r[2] == "base"][0]
check(base_row[7] == "" and base_row[8] == "", "holdings: coût inconnu → cellules vides")

# ── build_pnl_rows ──────────────────────────────────────────────────
prow = build_pnl_rows(agg)
check(len(prow[0]) == len(PNL_HEADERS), "pnl: nb colonnes = en-têtes")
eth_p = [r for r in prow if r[0] == "ETH" and r[1] == "ethereum"][0]
# avg_cost = 3200 / 1.5 = 2133.333333
check(eth_p[3] == "2133.333333", "pnl: coût moyen unitaire = coût/quantité")
check(eth_p[4] == "3200.00" and eth_p[5] == "4500.00" and eth_p[6] == "1300.00",
      "pnl: coût total / valeur actuelle / PnL latent")
steth_p = [r for r in prow if r[0] == "STETH"][0]
check(steth_p[3] == "1700" and steth_p[6] == "1400.00", "pnl: STETH coût moyen honnête")

# ── build_transaction_rows (via la vraie logique v2.12.4) ───────────
raw = [
    {"id": 1, "wallet_address": "0xW", "token_symbol": "ETH", "token_name": "Ethereum",
     "amount": 1.5, "usd_price": 3000, "usd_value": 4500, "chain": "ethereum",
     "tx_hash": "0xswap", "block_time": "2026-07-01T10:00:00.000000Z",
     "direction": "out", "log_index": 1, "gas_fee_usd": 2.5, "contract_address": "0xa"},
    {"id": 2, "wallet_address": "0xW", "token_symbol": "USDC", "token_name": "USD Coin",
     "amount": 4498.0, "usd_price": 1, "usd_value": 4498, "chain": "ethereum",
     "tx_hash": "0xswap", "block_time": "2026-07-01T10:00:00.000000Z",
     "direction": "in", "log_index": 2, "gas_fee_usd": 0, "contract_address": "0xb"},
    {"id": 3, "wallet_address": "0xW", "token_symbol": "ETH", "token_name": "Ethereum",
     "amount": 0.2, "usd_price": 3000, "usd_value": 600, "chain": "base",
     "tx_hash": "0xsend", "block_time": "2026-07-02T09:00:00.000000Z",
     "direction": "out", "log_index": 0, "gas_fee_usd": 0.12345, "contract_address": ""},
    {"id": 4, "wallet_address": "0xW", "token_symbol": 'PWN", evil', "token_name": "x",
     "amount": 10.0, "usd_price": 0, "usd_value": 0, "chain": "gnosis",
     "tx_hash": "0xrecv", "block_time": "2026-07-03T08:00:00.000000Z",
     "direction": "in", "log_index": 0, "gas_fee_usd": 0, "contract_address": ""},
]
events = group_transaction_events(raw)
trows = build_transaction_rows(events)
check(len(trows) == 3, "tx: 4 jambes → 3 événements (swap regroupé)")
check(all(len(r) == len(TRANSACTIONS_HEADERS) for r in trows), "tx: nb colonnes = en-têtes")
swap_row = [r for r in trows if r[7] == "0xswap"][0]
check(swap_row[1] == "Swap" and swap_row[2] == "ETH -> USDC", "tx: swap → type Swap, tokens 'A -> B'")
check(swap_row[3] == "-1.5 ETH / +4498 USDC", "tx: swap → amount '-X A / +Y B'")
check(swap_row[4] == "4500.00" and swap_row[5] == "2.5", "tx: swap → max(out,in) + gas une fois")
send_row = [r for r in trows if r[7] == "0xsend"][0]
check(send_row[1] == "Envoyé" and send_row[3] == "-0.2", "tx: send → Envoyé, montant négatif")
check(send_row[0] == "2026-07-02 09:00:00", "tx: date normalisée (T → espace, 19 chars)")
recv_row = [r for r in trows if r[7] == "0xrecv"][0]
check(recv_row[1] == "Reçu" and recv_row[3] == "10", "tx: receive → Reçu, montant positif")
# quoting de bout en bout
full = rows_to_csv(TRANSACTIONS_HEADERS, trows)
reparsed = list(csv.reader(io.StringIO(full)))
check(reparsed[0] == TRANSACTIONS_HEADERS, "tx: en-têtes CSV exacts")
check(any('PWN", evil' in c for r in reparsed for c in r), "tx: symbole piégé round-trip CSV")

# ── build_summary_pdf ───────────────────────────────────────────────
summary = {
    "generated_at": "2026-07-19 12:00 UTC",
    "scope_label": "Tous les wallets (2)",
    "wallet_count": 2, "total_usd": 12345.67, "pnl_usd": 2345.67, "cost_usd": 10000.0,
    "token_count": 42, "chain_count": 7,
    "by_chain": [{"key": "ethereum", "usd_value": 8000.0, "pct": 64.8},
                 {"key": "base", "usd_value": 4345.67, "pct": 35.2}],
    "by_category": [{"key": "wallet", "usd_value": 9000.0, "pct": 72.9},
                    {"key": "staked", "usd_value": 3345.67, "pct": 27.1}],
    "top_holdings": [
        {"symbol": "ETH", "chain": "ethereum", "balance": 2.5, "usd_value": 7500.0,
         "pct": 60.7, "pnl": 1500.0},
        {"symbol": "WEIRD(1)", "chain": "base", "balance": 10.0, "usd_value": 100.0,
         "pct": 0.8, "pnl": None},
    ],
}
pdf = build_summary_pdf(summary)
check(pdf.startswith(b"%PDF-1.4"), "pdf: magic %PDF-1.4")
check(pdf.rstrip().endswith(b"%%EOF"), "pdf: se termine par %%EOF")
check(b"/Type /Catalog" in pdf and b"/Type /Page" in pdf, "pdf: catalog + page présents")
check(b"Synthese du portefeuille" in pdf, "pdf: titre présent (flux non compressé)")
check(b"12,345.67" in pdf, "pdf: valeur totale rendue")
check(b"WEIRD\\(1\\)" in pdf, "pdf: parenthèses échappées dans les littéraux")

# xref: chaque offset pointe bien sur 'N 0 obj'
m = re.search(rb"startxref\s+(\d+)\s+%%EOF", pdf)
check(m is not None, "pdf: startxref présent")
if m:
    xref_pos = int(m.group(1))
    check(pdf[xref_pos:xref_pos + 4] == b"xref", "pdf: startxref pointe sur la table xref")
    lines = pdf[xref_pos:].split(b"\n")
    n_entries = int(lines[1].split()[1])
    ok_offsets = True
    for i in range(2, 2 + n_entries):
        entry = lines[i]
        if entry.endswith(b"f "):   # objet libre 0
            continue
        off = int(entry[:10])
        objnum = i - 2
        if not pdf[off:].startswith(f"{objnum} 0 obj".encode()):
            ok_offsets = False
            print(f"     offset invalide pour obj {objnum}")
    check(ok_offsets, "pdf: tous les offsets xref pointent sur leurs objets")

# PDF vide (aucune donnée) : jamais d'exception
pdf_empty = build_summary_pdf({})
check(pdf_empty.startswith(b"%PDF-1.4") and b"Aucune donnee" in pdf_empty,
      "pdf: résumé vide → PDF valide avec 'Aucune donnee'")
pdf_none = build_summary_pdf(None)
check(pdf_none.startswith(b"%PDF-1.4"), "pdf: summary None toléré")

# Multi-pages : 60 holdings → au moins 2 pages, /Count cohérent
many = dict(summary)
many["top_holdings"] = [{"symbol": f"TK{i}", "chain": "ethereum", "balance": i + 1,
                         "usd_value": 10.0 * (i + 1), "pct": 1.0, "pnl": 1.0}
                        for i in range(60)]
many["by_chain"] = [{"key": f"chain{i}", "usd_value": 10.0, "pct": 1.0} for i in range(14)]
pdf_many = build_summary_pdf(many)
mcount = re.search(rb"/Count (\d+)", pdf_many)
check(mcount is not None and int(mcount.group(1)) >= 1
      and pdf_many.count(b"/Type /Page ") == int(mcount.group(1)),
      "pdf: /Count = nombre d'objets Page")
check(b"Page 1 /" in pdf_many, "pdf: pied de page numéroté")

print()
if FAILED:
    print(f"❌ {FAILED} test(s) en échec")
    sys.exit(1)
print("✅ Tous les tests export passent")
