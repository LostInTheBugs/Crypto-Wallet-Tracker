"""Regroupement des transferts bruts en événements de transaction (v2.12.4).

Un SWAP = un même (wallet, chain, tx_hash) contenant au moins un transfert
'out' ET au moins un transfert 'in' (token A part, token B arrive dans la
même transaction). Sinon : 'send' (uniquement out) ou 'receive' (uniquement in).

Module pur (stdlib uniquement) → testable avec un python3 nu, sans FastAPI.
Le regroupement se fait à la LECTURE : aucun changement de schéma/stockage.
"""


def _leg(row):
    """Normalise une ligne de transfert en 'jambe' d'événement."""
    return {
        "symbol": row.get("token_symbol") or "?",
        "name": row.get("token_name") or "",
        "amount": row.get("amount") or 0,
        "usd_price": row.get("usd_price") or 0,
        "usd_value": row.get("usd_value") or 0,
        "contract": row.get("contract_address") or "",
    }


def _main_leg(legs):
    """Jambe principale = plus grosse usd_value (tie-break sur amount)."""
    best = None
    for leg in legs:
        if best is None or (leg["usd_value"], leg["amount"]) > (best["usd_value"], best["amount"]):
            best = leg
    return best


def group_transaction_events(rows):
    """Regroupe des lignes de transferts en événements swap/send/receive.

    rows : itérable de dict-like avec les clés
      id, wallet_address, token_symbol, token_name, amount, usd_price,
      usd_value, chain, tx_hash, block_time, direction, gas_fee_usd,
      contract_address.

    Retourne une liste d'événements triés par block_time DESC. Chaque événement :
      type            'swap' | 'send' | 'receive'
      direction       'swap' | 'out'  | 'in'   (rétro-compat tri/filtre UI)
      chain, tx_hash, wallet_address, block_time (jambe la plus récente)
      gas_fee_usd     compté UNE fois par tx (le gaz est stocké sur une seule jambe)
      usd_value       send: somme des out ; receive: somme des in ;
                      swap: max(somme out, somme in) — les deux côtés d'un swap
                      valent ± la même chose, les sommer doublerait la valeur
      legs            nombre total de jambes
      sent/received   listes de jambes {symbol, name, amount, usd_price, usd_value, contract}
      sent_symbol/sent_amount, recv_symbol/recv_amount  jambes principales
      token_symbol/token_name/amount/usd_price          résumé pour l'UI/tri
                      (swap: 'A → B', amount = jambe sortante principale, usd_price = None)
    """
    groups = {}
    order = []
    for r in rows:
        row = dict(r)
        tx_hash = row.get("tx_hash") or ""
        if tx_hash:
            key = (row.get("wallet_address") or "", row.get("chain") or "", tx_hash)
        else:
            # Pas de hash → jamais regroupé avec d'autres lignes.
            key = (row.get("wallet_address") or "", row.get("chain") or "", "_row_%s" % row.get("id"))
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(row)

    events = []
    for key in order:
        legs_rows = groups[key]
        first = legs_rows[0]

        # 2026.07.5 — non-transfer event types (approve/contract/native)
        ev_type = (first.get("event_type") or "").strip()
        if ev_type in ("approve", "contract", "native"):
            ev = {
                "type": ev_type,
                "direction": first.get("direction") or "",
                "chain": first.get("chain") or "",
                "tx_hash": first.get("tx_hash") or "",
                "wallet_address": first.get("wallet_address") or "",
                "block_time": first.get("block_time") or "",
                "gas_fee_usd": first.get("gas_fee_usd") or 0,
                "legs": 1,
                "sent": [],
                "received": [],
                "sent_symbol": None,
                "sent_amount": None,
                "recv_symbol": None,
                "recv_amount": None,
                "usd_value": first.get("usd_value") or 0,
                "token_symbol": first.get("token_symbol") or "",
                "token_name": first.get("token_name") or ev_type,
                "amount": first.get("amount") or 0,
                "usd_price": first.get("usd_price") or 0,
                "event_method": first.get("event_method") or "",
            }
            events.append(ev)
            continue

        outs = [_leg(r) for r in legs_rows if (r.get("direction") or "") == "out"]
        ins = [_leg(r) for r in legs_rows if (r.get("direction") or "") != "out"]
        first = legs_rows[0]

        gas = 0.0
        block_time = ""
        for r in legs_rows:
            g = r.get("gas_fee_usd") or 0
            if g > gas:
                gas = g
            bt = r.get("block_time") or ""
            if bt > block_time:
                block_time = bt

        sum_out = sum(leg["usd_value"] or 0 for leg in outs)
        sum_in = sum(leg["usd_value"] or 0 for leg in ins)
        main_out = _main_leg(outs)
        main_in = _main_leg(ins)

        ev = {
            "chain": first.get("chain") or "",
            "tx_hash": first.get("tx_hash") or "",
            "wallet_address": first.get("wallet_address") or "",
            "block_time": block_time,
            "gas_fee_usd": gas,
            "legs": len(outs) + len(ins),
            "sent": outs,
            "received": ins,
            "sent_symbol": main_out["symbol"] if main_out else None,
            "sent_amount": main_out["amount"] if main_out else None,
            "recv_symbol": main_in["symbol"] if main_in else None,
            "recv_amount": main_in["amount"] if main_in else None,
        }
        if outs and ins:
            ev["type"] = "swap"
            ev["direction"] = "swap"
            ev["usd_value"] = max(sum_out, sum_in)
            ev["token_symbol"] = "%s → %s" % (ev["sent_symbol"], ev["recv_symbol"])
            ev["token_name"] = ""
            ev["amount"] = ev["sent_amount"]
            ev["usd_price"] = None
        elif outs:
            mo = main_out or _leg({})
            ev["type"] = "send"
            ev["direction"] = "out"
            ev["usd_value"] = sum_out
            ev["token_symbol"] = mo["symbol"]
            ev["token_name"] = mo["name"]
            ev["amount"] = mo["amount"]
            ev["usd_price"] = mo["usd_price"]
        else:
            ev["type"] = "receive"
            ev["direction"] = "in"
            ev["usd_value"] = sum_in
            ev["token_symbol"] = main_in["symbol"] if main_in else "?"
            ev["token_name"] = main_in["name"] if main_in else ""
            ev["amount"] = main_in["amount"] if main_in else 0
            ev["usd_price"] = main_in["usd_price"] if main_in else 0
        events.append(ev)

    # Tri par date DESC ; block_time vide ("") = plus petit → arrive en dernier.
    events.sort(key=lambda e: e["block_time"], reverse=True)
    return events


def filter_events(events, token=None, direction=None, event_type=None):
    """Filtres post-regroupement (les jambes d'un swap restent entières).

    token      : garde les événements dont AU MOINS une jambe porte ce symbole.
    direction  : rétro-compat 'in'/'out' — un swap a les deux, il matche toujours.
    event_type : 'swap' | 'send' | 'receive' (filtre exact).
    """
    out = events
    if token:
        tl = token.lower()
        out = [e for e in out
               if any((leg["symbol"] or "").lower() == tl for leg in e["sent"] + e["received"])]
    if direction in ("in", "out"):
        out = [e for e in out if e["type"] == "swap" or e["direction"] == direction]
    if event_type in ("swap", "send", "receive", "approve", "contract", "native"):
        out = [e for e in out if e["type"] == event_type]
    return out
