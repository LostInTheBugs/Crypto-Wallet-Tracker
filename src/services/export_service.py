"""Export de données — CSV (holdings, transactions, PnL) + PDF de synthèse (2026.07.4).

Module PUR (stdlib uniquement) → testable avec un python3 nu, sans FastAPI :
    python3 tests/test_export_service.py

- CSV : encodage UTF-8, séparateur ",", décimales avec point, quoting RFC 4180
  (guillemets/virgules/retours ligne échappés par le module csv), lignes CRLF.
- PDF : générateur minimaliste écrit à la main (PDF 1.4, polices core Helvetica/
  Courier, flux non compressés, xref correcte) — AUCUNE dépendance externe,
  aucun risque d'installation dans l'image Docker.
- Robustesse : donnée manquante → cellule vide ; coût d'acquisition inconnu →
  cost_basis/pnl vides (JAMAIS un faux "acheté gratuit", cf. pitfall v2.11.10).
"""

import csv
import io
import math

# ── En-têtes CSV (contrat public des endpoints /api/export/*) ──────

HOLDINGS_HEADERS = ["token_name", "symbol", "chain", "balance", "usd_price",
                    "usd_value", "category", "cost_basis", "pnl"]
TRANSACTIONS_HEADERS = ["date", "type", "tokens", "amount", "usd_value",
                        "gas_fee_usd", "chain", "tx_hash"]
PNL_HEADERS = ["symbol", "chain", "quantity", "avg_cost_usd", "cost_basis_usd",
               "current_value_usd", "unrealized_pnl_usd"]

DEFAULT_TX_TYPE_LABELS = {"swap": "Swap", "send": "Envoyé", "receive": "Reçu"}


def _f(v) -> float:
    """Coercition float défensive : None/str invalide/NaN/inf → 0.0."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return 0.0
    return f if math.isfinite(f) else 0.0


def fmt_num(v, dec: int = 2, trim: bool = False) -> str:
    """Formate un nombre pour CSV : point décimal, jamais de notation
    scientifique. None / non-fini → chaîne vide (cellule vide)."""
    if v is None:
        return ""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return ""
    if not math.isfinite(f):
        return ""
    s = f"{f:.{dec}f}"
    if trim and "." in s:
        s = s.rstrip("0").rstrip(".")
        if s in ("", "-"):
            s = "0"
    return s


def rows_to_csv(headers, rows) -> str:
    """Sérialise en CSV RFC 4180 (QUOTE_MINIMAL, CRLF). Cellules None → ''."""
    buf = io.StringIO()
    writer = csv.writer(buf, quoting=csv.QUOTE_MINIMAL, lineterminator="\r\n")
    writer.writerow(list(headers))
    for row in rows or []:
        writer.writerow(["" if c is None else c for c in row])
    return buf.getvalue()


# ── Agrégation des holdings ─────────────────────────────────────────

def aggregate_holdings(tokens):
    """Fusionne une liste de tokens (déjà filtrés ACTIFS, possiblement issus
    de plusieurs wallets) par (symbol.lower(), chain).

    - balance / usd_value : sommés.
    - usd_price : recalculé (valeur/quantité) quand possible, sinon max vu.
    - cost_basis/pnl : connus SEULEMENT si TOUTES les lignes fusionnées ont un
      cost_basis non-null (sinon None → cellules vides ; on ne mélange jamais
      coût partiel et valeur totale, cf. pitfall v2.11.10).
    Retourne une liste de dicts triée par usd_value décroissante.
    """
    agg = {}
    order = []
    for tk in tokens or []:
        if not isinstance(tk, dict):
            continue
        sym = str(tk.get("symbol") or "?")
        chain = str(tk.get("chain") or "?")
        key = (sym.lower(), chain)
        if key not in agg:
            agg[key] = {
                "name": "", "symbol": sym, "chain": chain,
                "balance": 0.0, "usd_value": 0.0, "usd_price": 0.0,
                "category": str(tk.get("category") or "wallet"),
                "_cost": 0.0, "_cost_known": True,
            }
            order.append(key)
        a = agg[key]
        if not a["name"]:
            a["name"] = str(tk.get("name") or "")
        a["balance"] += _f(tk.get("balance"))
        a["usd_value"] += _f(tk.get("usd_value"))
        price = _f(tk.get("usd_price"))
        if price > a["usd_price"]:
            a["usd_price"] = price
        cb = tk.get("cost_basis")
        try:
            cbf = float(cb) if cb is not None else None
        except (TypeError, ValueError):
            cbf = None
        if cbf is None or not math.isfinite(cbf):
            a["_cost_known"] = False
        else:
            a["_cost"] += cbf
    out = []
    for key in order:
        a = agg[key]
        if a["balance"] > 0 and a["usd_value"] > 0:
            a["usd_price"] = a["usd_value"] / a["balance"]
        if a["_cost_known"]:
            a["cost_basis"] = round(a["_cost"], 2)
            a["pnl"] = round(a["usd_value"] - a["_cost"], 2)
        else:
            a["cost_basis"] = None
            a["pnl"] = None
        a.pop("_cost", None)
        a.pop("_cost_known", None)
        a["usd_value"] = round(a["usd_value"], 2)
        out.append(a)
    out.sort(key=lambda r: r["usd_value"], reverse=True)
    return out


def build_holdings_rows(agg_rows):
    """Lignes CSV holdings depuis la sortie d'aggregate_holdings()."""
    rows = []
    for a in agg_rows or []:
        try:
            rows.append([
                a.get("name") or "",
                a.get("symbol") or "",
                a.get("chain") or "",
                fmt_num(a.get("balance"), 8, trim=True),
                fmt_num(a.get("usd_price"), 6, trim=True),
                fmt_num(a.get("usd_value"), 2),
                a.get("category") or "wallet",
                fmt_num(a.get("cost_basis"), 2),
                fmt_num(a.get("pnl"), 2),
            ])
        except Exception:
            continue
    return rows


def build_pnl_rows(agg_rows):
    """Lignes CSV du rapport PnL/fiscal (best-effort) depuis les holdings
    agrégés. avg_cost_usd = coût moyen unitaire (cost_basis / quantité)."""
    rows = []
    for a in agg_rows or []:
        try:
            bal = _f(a.get("balance"))
            cost = a.get("cost_basis")
            avg = None
            if cost is not None and bal > 0:
                avg = _f(cost) / bal
            rows.append([
                a.get("symbol") or "",
                a.get("chain") or "",
                fmt_num(bal, 8, trim=True),
                fmt_num(avg, 6, trim=True),
                fmt_num(cost, 2),
                fmt_num(a.get("usd_value"), 2),
                fmt_num(a.get("pnl"), 2),
            ])
        except Exception:
            continue
    return rows


def build_transaction_rows(events, type_labels=None):
    """Lignes CSV transactions depuis les événements de tx_events (v2.12.4).

    - type : Envoyé / Reçu / Swap (labels surchargables).
    - tokens : symbole, ou "A -> B" pour un swap (flèche ASCII pour rester
      lisible dans tous les tableurs).
    - amount : signé pour send/receive ; "-X A / +Y B" pour un swap.
    """
    labels = dict(DEFAULT_TX_TYPE_LABELS)
    if type_labels:
        labels.update(type_labels)
    rows = []
    for ev in events or []:
        try:
            if not isinstance(ev, dict):
                continue
            etype = ev.get("type") or ""
            date = str(ev.get("block_time") or "").replace("T", " ")[:19]
            if etype == "swap":
                s_sym = ev.get("sent_symbol") or "?"
                r_sym = ev.get("recv_symbol") or "?"
                tokens_field = f"{s_sym} -> {r_sym}"
                s_amt = fmt_num(ev.get("sent_amount"), 8, trim=True)
                r_amt = fmt_num(ev.get("recv_amount"), 8, trim=True)
                amount_field = f"-{s_amt or '?'} {s_sym} / +{r_amt or '?'} {r_sym}"
            else:
                tokens_field = ev.get("token_symbol") or "?"
                amt = fmt_num(ev.get("amount"), 8, trim=True)
                if not amt:
                    amount_field = ""
                elif etype == "send":
                    amount_field = "-" + amt
                else:
                    amount_field = amt
            rows.append([
                date,
                labels.get(etype, etype),
                tokens_field,
                amount_field,
                fmt_num(ev.get("usd_value"), 2),
                fmt_num(ev.get("gas_fee_usd"), 4, trim=True),
                ev.get("chain") or "",
                ev.get("tx_hash") or "",
            ])
        except Exception:
            continue
    return rows


# ── Générateur PDF minimaliste (PDF 1.4, sans dépendance) ──────────

def _pdf_escape(s) -> str:
    """Échappe une chaîne pour un littéral PDF (…) et force latin-1
    (WinAnsi) — caractère non représentable → '?'."""
    txt = str(s if s is not None else "")
    txt = txt.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    txt = txt.replace("\r", " ").replace("\n", " ")
    return txt.encode("latin-1", "replace").decode("latin-1")


class _Pdf:
    """Assembleur PDF 1.4 multi-pages : texte (Helvetica/Courier), rectangles
    pleins et filets. Flux non compressés, xref exacte."""

    W = 595.28   # A4 portrait (points)
    H = 841.89

    def __init__(self):
        self.pages = []
        self._ops = []
        self.new_page()

    def new_page(self):
        self._ops = []
        self.pages.append(self._ops)

    def text(self, x, y, s, font="F1", size=10, color=(0.1, 0.1, 0.1)):
        r, g, b = color
        self._ops.append(
            f"BT /{font} {size} Tf {r:.3f} {g:.3f} {b:.3f} rg "
            f"{x:.2f} {y:.2f} Td ({_pdf_escape(s)}) Tj ET")

    def rect(self, x, y, w, h, color=(0.9, 0.9, 0.9)):
        r, g, b = color
        self._ops.append(
            f"q {r:.3f} {g:.3f} {b:.3f} rg {x:.2f} {y:.2f} {w:.2f} {h:.2f} re f Q")

    def hline(self, x1, x2, y, color=(0.82, 0.84, 0.87), width=0.7):
        r, g, b = color
        self._ops.append(
            f"q {r:.3f} {g:.3f} {b:.3f} RG {width} w "
            f"{x1:.2f} {y:.2f} m {x2:.2f} {y:.2f} l S Q")

    def build(self) -> bytes:
        out = bytearray()
        offsets = {}

        def w(bs):
            out.extend(bs)

        def add_obj(num, body: bytes):
            offsets[num] = len(out)
            w(f"{num} 0 obj\n".encode("latin-1"))
            w(body)
            w(b"\nendobj\n")

        w(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
        n_pages = len(self.pages)
        page_nums = [7 + 2 * i for i in range(n_pages)]
        kids = " ".join(f"{n} 0 R" for n in page_nums)
        add_obj(1, b"<< /Type /Catalog /Pages 2 0 R >>")
        add_obj(2, f"<< /Type /Pages /Kids [{kids}] /Count {n_pages} >>".encode("latin-1"))
        fonts = [("F1", "Helvetica"), ("F2", "Helvetica-Bold"),
                 ("F3", "Courier"), ("F4", "Courier-Bold")]
        for i, (_fid, base) in enumerate(fonts):
            add_obj(3 + i,
                    f"<< /Type /Font /Subtype /Type1 /BaseFont /{base} "
                    f"/Encoding /WinAnsiEncoding >>".encode("latin-1"))
        res = "<< /Font << /F1 3 0 R /F2 4 0 R /F3 5 0 R /F4 6 0 R >> >>"
        for i, ops in enumerate(self.pages):
            stream = "\n".join(ops).encode("latin-1", "replace")
            pnum, cnum = 7 + 2 * i, 8 + 2 * i
            add_obj(pnum,
                    f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {self.W:.2f} {self.H:.2f}] "
                    f"/Resources {res} /Contents {cnum} 0 R >>".encode("latin-1"))
            add_obj(cnum, b"<< /Length " + str(len(stream)).encode("latin-1")
                    + b" >>\nstream\n" + stream + b"\nendstream")
        max_num = 6 + 2 * n_pages
        xref_pos = len(out)
        w(f"xref\n0 {max_num + 1}\n".encode("latin-1"))
        w(b"0000000000 65535 f \n")
        for n in range(1, max_num + 1):
            w(f"{offsets[n]:010d} 00000 n \n".encode("latin-1"))
        w(f"trailer\n<< /Size {max_num + 1} /Root 1 0 R >>\n"
          f"startxref\n{xref_pos}\n%%EOF\n".encode("latin-1"))
        return bytes(out)


def _money(v) -> str:
    if v is None:
        return "n/d"
    try:
        f = float(v)
    except (TypeError, ValueError):
        return "n/d"
    if not math.isfinite(f):
        return "n/d"
    return f"${f:,.2f}"


def _signed_money(v) -> str:
    if v is None:
        return "n/d"
    f = _f(v)
    return ("+" if f >= 0 else "-") + "$" + f"{abs(f):,.2f}"


CATEGORY_LABELS_FR = {
    "wallet": "Wallet", "staked": "Stake", "lending": "Lending",
    "lp": "LP", "vault": "Vault", "synthetic": "Synthetique",
}


def build_summary_pdf(summary) -> bytes:
    """PDF de synthèse du portefeuille. `summary` (tout optionnel) :
      generated_at, scope_label, wallet_count, total_usd, pnl_usd, cost_usd,
      token_count, chain_count,
      by_chain [{key, usd_value, pct}], by_category [{key, usd_value, pct}],
      top_holdings [{symbol, chain, balance, usd_value, pct, pnl}].
    Ne lève jamais : toute donnée manquante est remplacée par n/d / listes vides.
    """
    s = summary if isinstance(summary, dict) else {}
    pdf = _Pdf()
    M = 40.0
    RIGHT = _Pdf.W - M
    y = [_Pdf.H - 56]

    ACCENT = (0.20, 0.45, 0.85)
    GREY = (0.42, 0.45, 0.50)
    DARK = (0.10, 0.12, 0.15)
    GREEN = (0.16, 0.55, 0.28)
    RED = (0.78, 0.19, 0.19)

    def ensure(h):
        if y[0] - h < 50:
            pdf.new_page()
            y[0] = _Pdf.H - 56

    def line(txt, font="F1", size=10, dy=15, x=M, color=DARK):
        ensure(dy)
        pdf.text(x, y[0], txt, font, size, color)
        y[0] -= dy

    def section(title):
        ensure(34)
        y[0] -= 8
        pdf.text(M, y[0], title, "F2", 12, ACCENT)
        y[0] -= 6
        pdf.hline(M, RIGHT, y[0])
        y[0] -= 14

    # ── En-tête ──────────────────────────────────────────────
    pdf.rect(0, _Pdf.H - 46, _Pdf.W, 46, (0.07, 0.09, 0.13))
    pdf.text(M, _Pdf.H - 30, "Synthese du portefeuille", "F2", 16, (1, 1, 1))
    pdf.text(RIGHT - 150, _Pdf.H - 28, "Crypto Wallet Tracker", "F1", 9, (0.75, 0.8, 0.88))
    y[0] = _Pdf.H - 70

    line(f"Genere le : {s.get('generated_at') or 'n/d'}", "F1", 9, 13, M, GREY)
    line(f"Perimetre : {s.get('scope_label') or 'n/d'}", "F1", 9, 13, M, GREY)

    # ── Vue d'ensemble ───────────────────────────────────────
    section("Vue d'ensemble")
    total = s.get("total_usd")
    pnl = s.get("pnl_usd")
    cost = s.get("cost_usd")
    line(f"Valeur totale : {_money(total)}", "F2", 13, 20)
    if pnl is None:
        line("PnL total : n/d (cout d'acquisition inconnu)", "F1", 10, 16, M, GREY)
    else:
        pct = ""
        if cost is not None and _f(cost) > 0:
            pct = f" ({_f(pnl) / _f(cost) * 100:+.2f}%)"
        line(f"PnL total : {_signed_money(pnl)}{pct}"
             + (f"  -  Cout d'acquisition : {_money(cost)}" if cost is not None else ""),
             "F2", 11, 17, M, GREEN if _f(pnl) >= 0 else RED)
    counts = []
    if s.get("token_count") is not None:
        counts.append(f"Tokens actifs : {s.get('token_count')}")
    if s.get("chain_count") is not None:
        counts.append(f"Chaines : {s.get('chain_count')}")
    if s.get("wallet_count") is not None:
        counts.append(f"Wallets : {s.get('wallet_count')}")
    if counts:
        line("   |   ".join(counts), "F1", 10, 16)

    def alloc_rows(items, label_fn):
        rows = [it for it in (items or []) if isinstance(it, dict)]
        if not rows:
            line("Aucune donnee", "F1", 9, 14, M, GREY)
            return
        for it in rows[:14]:
            ensure(16)
            label = label_fn(it)
            val = _f(it.get("usd_value"))
            pct = _f(it.get("pct"))
            pdf.text(M, y[0], str(label)[:22], "F3", 9, DARK)
            bar_x, bar_w = 190.0, 200.0
            pdf.rect(bar_x, y[0] - 1.5, bar_w, 8, (0.90, 0.92, 0.95))
            fill = max(0.0, min(pct, 100.0)) / 100.0 * bar_w
            if fill > 0:
                pdf.rect(bar_x, y[0] - 1.5, fill, 8, ACCENT)
            txt = f"{_money(val):>15}  {pct:6.2f}%"
            pdf.text(bar_x + bar_w + 12, y[0], txt, "F3", 9, DARK)
            y[0] -= 16

    # ── Répartitions ─────────────────────────────────────────
    section("Repartition par chaine")
    alloc_rows(s.get("by_chain"), lambda it: str(it.get("key") or "?").capitalize())

    section("Repartition par categorie")
    alloc_rows(s.get("by_category"),
               lambda it: CATEGORY_LABELS_FR.get(str(it.get("key") or "wallet"),
                                                 str(it.get("key") or "?")))

    # ── Top holdings ─────────────────────────────────────────
    section("Top holdings")
    holdings = [h for h in (s.get("top_holdings") or []) if isinstance(h, dict)]
    if not holdings:
        line("Aucune donnee", "F1", 9, 14, M, GREY)
    else:
        header = (f"{'TOKEN':<11}{'CHAINE':<13}{'QUANTITE':>16}"
                  f"{'VALEUR':>15}{'PART':>8}  {'PNL':>13}")
        ensure(16)
        pdf.text(M, y[0], header, "F4", 9, GREY)
        y[0] -= 6
        pdf.hline(M, RIGHT, y[0])
        y[0] -= 12
        for h in holdings[:15]:
            ensure(15)
            sym = str(h.get("symbol") or "?")[:10]
            chain = str(h.get("chain") or "?")[:12]
            qty = fmt_num(h.get("balance"), 6, trim=True) or "0"
            if len(qty) > 15:
                qty = qty[:15]
            val = _money(_f(h.get("usd_value")))
            pct = f"{_f(h.get('pct')):5.1f}%"
            pnl_v = h.get("pnl")
            pnl_txt = "n/d" if pnl_v is None else _signed_money(pnl_v)
            row = f"{sym:<11}{chain:<13}{qty:>16}{val:>15}{pct:>8}  {pnl_txt:>13}"
            color = DARK if pnl_v is None else (GREEN if _f(pnl_v) >= 0 else RED)
            pdf.text(M, y[0], row, "F3", 9, color)
            y[0] -= 13

    # ── Pied de page (toutes les pages) ──────────────────────
    total_pages = len(pdf.pages)
    for i, ops in enumerate(pdf.pages):
        pdf._ops = ops
        pdf.hline(M, RIGHT, 38)
        pdf.text(M, 27, "Crypto Wallet Tracker - export automatique", "F1", 7, GREY)
        pdf.text(RIGHT - 60, 27, f"Page {i + 1} / {total_pages}", "F1", 7, GREY)

    return pdf.build()
