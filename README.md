# Crypto Wallet Tracker — 2026.08.002

> ⚠️ **DISCLAIMER — This is a proof-of-concept / experimental application.** It is NOT financial, tax, accounting, or investment advice and is NOT a substitute for a qualified professional. Balances, valuations, transactions, PnL and any tax-related figures may be inaccurate, incomplete, or wrong — do NOT rely on them for decisions, reporting, or filing. Always verify with a licensed professional. Use at your own risk; no warranty of any kind.

**Local crypto wallet inventory** — multi-wallet, multi-chain EVM + Bitcoin + Solana, 100% free (Blockscout API + mempool.space + Solana public RPC).

Aggregated dashboard, historical charts, price history via DefiLlama, per-token PNL, paginated transactions, user accounts. All Docker, one command.

---

## ✨ Features

- 🔗 **22 EVM chains** — Ethereum, Base, Optimism, Arbitrum, Polygon, Gnosis, zkSync, Celo, Scroll, Soneium, Ink, Mode, Unichain, Lisk, Linea, Etherlink, Metis, Manta, BOB, Zora, World Chain, HyperEVM
- 🪙 **Native balance** — ETH/POL/xDAI/CELO/XTZ/METIS fetched in parallel with tokens (native API call)
- 💰 **USD/€ valuation** — real-time via Blockscout, EUR conversion (Frankfurter)
- 🦙 **DefiLlama price fallback** — if Blockscout has no price, batch call to free `coins.llama.fi/prices/current` API
- 🔒 **Best-effort DeFi detection** — fine-grained categorization (lending, LP, staked, vault, synthetic) via symbol heuristics, zero third-party services, 100% free. Dedicated DeFi section with colored badges and per-category subtotals
- 🏦 **DeFi Page (Moralis)** — dedicated page listing real DeFi positions by protocol (lending supplied/borrowed, staking, LP) with rewards, health factor, APY, PnL when available, and link to each position (dapp or explorer). Global supplied/borrowed/rewards/net value summary. Moralis API key (free) recommended in Settings → External API keys; **without a key, free best-effort mode**: positions (lending supplied/borrowed, staking, LP, vaults) are reconstructed from on-chain Blockscout balances — rewards/APY/health factor unavailable
- 🎛️ **Integrated token management** — everything in the « Token Details » tab: active/inactive counters, on/off toggle per row, collapsible inactive tokens section (with reason badge), manual add form. Zero-value tokens, spam, illiquid memecoins, and low-confidence DefiLlama prices are disabled by default; a disabled token is excluded from totals, DeFi breakdown, and history (retroactive effect)
- 👥 **User accounts** — sign-up, login, private wallets (bcrypt + sessions)
- 📊 **Dashboard** — total value, chain breakdown (donut), Total PNL / 24h PNL cards, mini chart, cumulative gas
- 📈 **Statistics** — value/cost-basis curves, daily PNL bars (7d/30d/90d/1y/All), filterable by wallet/token/chain
- 📜 **Transactions** — events grouped by transaction (Swap / Sent / Received), paginated table, filterable by wallet/chain/type, price/value/gas columns
- 📋 **Token Details** — balance, price, value, and **per-token PNL** (green/red)
- 🔙 **Price history** — DefiLlama (free, no API key) + SQLite cache, optional CoinGecko fallback
- 🧮 **PNL calculated** — weighted average cost, balances reconstructed by date, daily PNL
- 🛡️ **Anti-spam filter** — automatic scam/airdrop token detection
- ⚙️ **Settings** — language (FR/EN), currency (USD/EUR), password change, per-user API keys
- 🔑 **Per-user API keys** — catalog of 7 services (CoinGecko, OpenSea, Etherscan, DefiLlama, Alchemy, Moralis, CoinMarketCap) with best-effort validation and card UI with logos
- 📦 **Version check** — compares against latest GitHub tag
- ⚡ **Price cache** — `price_history` table, 2nd rebuild ≈ 0 network calls
- 🔔 **Alerts** — price, portfolio value, movements (> X% in 24h), **health factor / liquidation risk** with in-app notifications + external channels (webhook, Telegram, email)
- 🪂 **Airdrops to claim** — best-effort multi-chain detection via extensible checker registry. API `/api/airdrops` + dedicated page
- 📬 **Digest** — daily or weekly portfolio summary
- 🐳 **Docker** — single command to deploy

---

## 🚀 Installation

```bash
curl -fsSL https://raw.githubusercontent.com/LostInTheBugs/Crypto-Wallet-Tracker/main/install.sh | sudo bash
```

Then open `http://<server-ip>`.

### Manual (Docker)

```bash
git clone https://github.com/LostInTheBugs/Crypto-Wallet-Tracker.git
cd Crypto-Wallet-Tracker
docker compose up -d
```

---

## 📁 Structure

```
Crypto-Wallet-Tracker/
├── src/
│   ├── app.py               # FastAPI backend — routes, auth, wallet CRUD (~900 lines)
│   └── services/            # Business modules
│       ├── price_service.py   # SYMBOL_TO_CG, DefiLlama/CoinGecko, price cache
│       ├── pnl_service.py     # Unified timeline, balance reconstruction, PNL
│       ├── portfolio_service.py  # 22 chains, native, price fallback, spam, staked
│       ├── airdrops/              # Best-effort airdrop detection (extensible checkers)
│       ├── providers/             # Multi-chain abstraction (EVM, BTC, Solana)
│       │   ├── base.py, evm.py, bitcoin.py, solana.py
│       (+ defi_service.py — Moralis DeFi positions normalizer, pure module)
├── public/index.html        # SPA frontend + Chart.js (~800 lines)
├── Dockerfile
├── docker-compose.yml
├── install.sh               # Auto-installer
├── requirements.txt
└── README.md
```

---

## 🔧 Configuration (.env)

| Variable | Default | Description |
|---|---|---|
| `PORT` | `8001` | Listen port |
| `SESSION_SECRET` | auto | JWT secret (set to persist sessions) |
| `ALCHEMY_API_KEY` | — | Optional: fallback for balances/transfers if Blockscout fails |

---

## 🛠️ Stack

| Layer | Technology |
|---|---|
| Backend | Python 3.12 · FastAPI · aiosqlite · httpx |
| Frontend | Vanilla JS · Chart.js 4 · GitHub dark theme |
| Historical prices | **DefiLlama** (primary, free) + CoinGecko (fallback, requires key) |
| Transactions | Blockscout API v2 (ERC-20/721/1155 token-transfers) |
| Deployment | Docker · docker compose |

---

## 📡 Data sources

| Data | Source | Free |
|---|---|---|
| Real-time balances | Blockscout `/token-balances` | ✅ |
| Token transfers | Blockscout `/token-transfers` | ✅ |
| Historical prices | DefiLlama `/chart` | ✅ |
| Historical prices (fallback) | CoinGecko `/market_chart/range` | ❌ (API key) |
| Gas fees | Blockscout `/transactions` | ✅ |
| Current prices | Blockscout (built into `/token-balances`) | ✅ |
| EUR conversion | Frankfurter (ECB) | ✅ |
| NFT floor prices | OpenSea / Moralis / Reservoir | ✅ / ❌ (key) |

---

## 🧮 PNL calculation

- **Reconstructed balances**: cumulative signed transfers by date (`in` − `out`)
- **Cost basis**: weighted average cost per token (buys at day's price, sells at average cost)
- **PNL**: `current_value − average_cost`
- **Daily PNL**: `value(j) − value(j−1) − net_flows(j)`
- **Reconciliation**: delta between history and portfolio shown as warning if >15%

---

## 🔐 Security

- Passwords hashed with **bcrypt**
- httpOnly cookie sessions
- **Optional two-factor authentication (2FA TOTP)** — enable in Settings
- **Anti-brute-force** — login attempt rate-limiting
- **Password change** in Settings
- **No private keys** — public addresses only
- User API keys: stored encrypted, never returned in plaintext (masked `sk-...abc`)
- 100% local data (SQLite)

---

## 🗺️ Roadmap

### Phase 1 — Features
- [x] 2026.07.3 — Analytics (allocation & performance)
- [x] 2026.07.4 — CSV/PDF export (holdings, txs, tax PnL)
- [x] 2026.07.5 — Transactions: approvals, interactions, gas
- [x] 2026.07.6 — Alert engine + digest
- [x] 2026.07.7 — Health factor / liquidation alerts
- [x] 2026.07.8 — NFT valuation (floor prices)
- [x] 2026.07.9 — Multi-source pricing + key testing
- [x] 2026.07.10 — NFT: source links + floor reliability
- [x] 2026.07.11 — PWA, theme, search, watchlist
- [x] 2026.07.12 — SQLite consolidation (serialized writes)
- [x] 2026.07.13 — Self-update via host-side updater (Update button functional)
- [x] 2026.07.14 — Updater self-update hardened
- [x] 2026.07.15 — Self-update trigger hardened
- [x] 2026.07.16 — Auto/manual update choice
- [x] 2026.07.17 — Auto backups + health + tests/CI
- [x] 2026.07.18 — HTTPS self-update (reliable fetch, no SSH key)
- [x] 2026.07.19 — Top bar removed
- [x] 2026.07.20 — Auth hardening & accounts

### Phase 2 — Non-EVM multi-chain & airdrops
- [x] 2026.07.21 — Multi-provider abstraction (non-EVM foundation)
- [x] 2026.07.22 — Bitcoin (BTC)
- [x] 2026.07.23 — Solana (SOL + SPL tokens via public RPC)
- [x] 2026.07.24 — ~~Support Cosmos/ATOM (native staking)~~ (removed 2026.07.29)
- [x] 2026.07.25 — Airdrops to claim (best-effort detection + alerts)

**Phase 2 complete!** 🎉

### Phase 3 — Full transactions, tax/PnL, cross-chain DeFi, new chains
- [x] 2026.07.26 — Solana transactions (full history)
- [x] 2026.07.27 — Non-EVM transactions in aggregated view (Solana visible)
- [x] 2026.07.27.c1 — Fix: SPL detection by owner (not accountIndex)
- [x] 2026.07.28 — ~~Cosmos transactions (full history)~~ (removed 2026.07.29)
- [x] 2026.07.29 — Cleanup: Cosmos removal + README translation + disclaimer
- [x] 2026.07.30 — Fix: Analytics spurious negative change (history anchored to live portfolio)
- [x] 2026.07.031 — Advanced tax / PnL (cost basis, realized vs unrealized)
- [ ] 2026.07.32 — Cross-chain DeFi (LP, lending, unified health factor)
- [ ] 2026.07.33 — New chains (L2 EVM then non-EVM)

## 📋 Changelog

### 2026.07.031 — Advanced Tax/PnL module: weighted-average cost basis, realized vs unrealized P&L per asset, dedicated page with in-app proof-of-concept disclaimer. Version numbering switched to 3-digit (AAAA.MM.NNN).

- **Tax/PnL engine** (`src/services/tax_service.py`): weighted-average cost basis per asset (symbol+chain) walking all transactions chronologically. Realized PnL computed as `sale_proceeds - cost_of_sold_units` for each sale, unrealized PnL = `current_value - remaining_cost_basis`. Assets without sufficient transaction history or price data are flagged `"incomplete"` rather than showing false numbers.
- **API `/api/tax`**: returns per-asset breakdown (symbol, chain, qty_held, cost_basis, current_value, unrealized_pnl, realized_pnl, method, status) + aggregate totals. Filterable by wallet address or "ALL". 5-minute server cache.
- **Tax / PnL page**: new sidebar entry "📊 Fiscal / PnL" with sortable table (symbol, chain, qty, cost, value, unrealized, realized, status), green/red PnL coloring, totals row, weighted-average method label, and prominent in-app proof-of-concept disclaimer banner.
- **Disclaimer**: visible on both the Tax page and Analytics page.
- **Version format**: numbering now uses 3-digit patch (`2026.07.031` instead of `2026.07.31`). The comparison logic (`_cmpVer` in frontend, `_calver_key` in `/api/version/latest`) uses `parseInt(parts[3], 10)` which correctly handles `"031"` → 31. Old tags like `"2026.07.30"` remain valid (2-digit patch, comparison still works). Correction tags (`.cX`) unchanged.
- **Tests**: `tests/test_tax_service.py` — weighted-average cost, realized/unrealized PnL on known in/out sequences, incomplete asset detection, user isolation.

### 2026.07.30 — Fix: Analytics change no longer shows a spurious large negative — history reconstruction anchored to the live spam-filtered portfolio (no phantom balances) and change shows "—" when history is flat/unreliable

- **`_rebuild_history` anchored to live portfolio**: phantom tokens (present in reconstruction but absent from current on-chain balance, e.g. WETH unwrapped without a captured outbound tx) are now excluded from the historical value computation. The reconstruction uses the same token set as the live spam-filtered portfolio — no more inflated daily_history aggregates (~$18918 plateau with phantom WETH vs real $8530).
- **`compute_change_periods` flat/aberrant guards**: if the `daily_history` aggregate is plateaued (all rows identical — a reconstruction artefact, not real market data), the Analytics `change` block returns `None` for all three periods (frontend displays "—"). Additionally, if the picked baseline diverges by more than 2× from the current portfolio value (phantom balances, missing outbound txs), the period is skipped — never show a misleading −54.9% based on a phantom baseline.
- **Tests**: `test_analytics_service.py` extended with 8 new assertions covering flat history detection, aberrant baseline filtering, and non-regression of normal scenarios. All existing tests pass (EVM, Solana, BTC unchanged).

### 2026.07.29 — Removed Cosmos support (fragmented ecosystem); README translated to English; added proof-of-concept disclaimer

- **Cosmos support fully removed**: `CosmosProvider`, Cosmos routing, staking rewards airdrop checker, and all Cosmos tests deleted. `provider_for("cosmos1...")` and `provider_for("osmo1...")` now return `None` (chain not supported). EVM, Bitcoin, and Solana providers unchanged. All existing tests pass.
- **README translated to English**: full French-to-English translation of all sections, titles, feature descriptions, roadmap, and changelog.
- **Disclaimer added**: prominent proof-of-concept disclaimer at the top of the README.

### 2026.07.28 — Cosmos transactions: real history (MsgSend + delegations via Polkachu public LCD, live-merge compatible event shape with root `amount`, Mintscan links)

- **CosmosProvider.get_transactions()**: full implementation replacing the empty placeholder. History fetched via Polkachu public LCD REST API with `message.sender='{addr}'` (outgoing) and `transfer.recipient='{addr}'` (incoming) queries, merged and deduped by txhash. Parsing of `MsgSend`, `MsgDelegate` and `MsgUndelegate` with uatom/uosmo → ATOM/OSMO conversion (÷1e6).
- **Strictly identical event format to Solana**: each event contains `type`, `direction`, `tx_hash`, `block_time` (ISO 8601), `token_symbol`, `chain`, `amount` (root = sent_amount for send, recv_amount for receive), `usd_value`, `sent`/`received` (dicts with symbol/name/amount/usd_price/usd_value/contract), `sent_symbol`/`sent_amount`/`recv_symbol`/`recv_amount`, `gas_fee_usd`, `wallet_address`, `log_index`. Live merge (`/api/transactions` aggregated) makes them immediately visible without DB persistence.
- **Mintscan links**: `explorer_tx_url` generates `https://www.mintscan.io/cosmos/tx/{hash}` URLs. Chain resolved by HRP (cosmos1→cosmos, osmo1→osmosis).
- **Real tests**: `tests/test_cosmos_tx.py` — validation with real Osmosis address `osmo19ce3d285j37fvdm277qlvw4sth2j7cwapjk6sc` (6 events: 1 send + 5 receive detected, format verified, direction/type/chain filters functional).
- **Non-regression**: all existing tests pass (core 20/20, swap_grouping, agg_tx 67/67, solana_tx 74/74, live_merge 52/52, providers 34/34). py_compile and node --check OK.

### 2026.07.27 — Non-EVM transactions persisted in aggregated view: Solana/BTC/Cosmos now appear on Transactions page (provider routing + persistence + per-chain explorer links)

- **Non-EVM persistence in `transactions` table**: `_fetch_transactions_for_wallet` now routes ALL wallets (EVM and non-EVM). When a non-EVM provider is detected, events are persisted in the same SQLite table as EVM — one row per leg (swap = 2 rows same tx_hash, distinct log_index). Idempotent dedup on `(tx_hash, log_index, user_id)`. The aggregated view (`/api/transactions` without wallet filter) now reads transactions from ALL chains from the same table.
- **Per-chain explorer links**: explorer URLs resolved by provider (`explorer_tx_url`) for non-EVM chains (Solscan for Solana, mempool.space for Bitcoin, Mintscan for Cosmos). EVM chains continue using the `CHAINS` mapping (Blockscout).
- **Refresh**: daily refresh and manual fetch (`POST /api/transactions/fetch`) now correctly iterate non-EVM wallets — their transactions are persisted and visible in the aggregated view.
- **Case-insensitive wallet filter preserved**: the `lower(wallet_address) IN (SELECT lower(address) FROM wallets)` filter works correctly for Solana addresses (base58, case-sensitive) because comparison is done in lowercase on both sides.
- **Tests**: `tests/test_agg_tx.py` — 67 assertions (send/receive/swap persistence, idempotence, reconstruction via `group_transaction_events`, non-EVM explorer links, EVM non-regression, wallet-aware filter). All existing tests pass (swap_grouping, solana_tx).

### 2026.07.27.c4 — Fix: non-EVM transaction amounts (Solana) now displayed (root `amount` field added to send/receive/native events)

- **Root cause**: non-EVM events (Solana, Bitcoin) built by providers did NOT expose a root `amount` field (only `sent_amount`, `recv_amount`, `sent.amount`, `received.amount`). The frontend (`renderTxnTable`) reads `tx.amount` for send/receive/native types (line 2328) → `undefined` → `fmtAmt` returns 0. EVM events (`group_transaction_events`) had `amount` via grouping, but live non-EVM events passed in the aggregated view (c3 merge) arrived without this field.
- **Fix (server-side, providers)**: added a root `amount` field consistent with event type in `_parse_solana_tx` (Solana) and `get_transactions` (Bitcoin):
  * send → `amount = sent_amount`
  * receive → `amount = recv_amount`
  * swap → `amount = sent_amount` (consistent with EVM convention from `group_transaction_events`)
  * Existing fields (`sent_amount`, `recv_amount`, `sent`, `received`) remain unchanged.
- **Non-regression**: all existing tests pass (solana_tx 74/74, swap_grouping, agg_tx 67/67, core 20/20). Explicit validation: 16 assertions proving `amount > 0` and `amount == sent_amount/recv_amount` for each Solana event type.

### 2026.07.27.c3 — Non-EVM transactions in aggregated view via live merge (Solana visible) + update system fix for .cX tags

- **Live merge of non-EVM transactions**: the aggregated view (`/api/transactions` without wallet filter) now DIRECTLY merges non-EVM transactions (Solana, BTC, Cosmos) from providers, instead of depending on SQLite persistence (which silently fails in production). 300s in-memory cache per (user, wallet_address) to avoid re-calling RPC on every navigation. Defensive: a provider timeout NEVER impacts the EVM view. Normalized date format (YYYY-MM-DD HH:MM:SS) for consistent lexical sorting between EVM and non-EVM.
- **Automatic explorer links**: Solscan (Solana), mempool.space (Bitcoin), and Mintscan (Cosmos) URLs are attached to live events.
- **Update system fix**: `GET /api/version/latest` now recognizes `.cX` correction tags (regex `^\d{4}\.\d{2}\.\d+(\.c\d+)?$`) and sorts them correctly via `_calver_key`: a correction is considered later than the base version (e.g., 2026.07.27.c3 > 2026.07.27). The frontend (`_cmpVer` in `checkVersion()`) now compares 4 components (year, month, patch, cn) with cn=0 for versions without suffix.
- **Improved logging**: `_persist_non_evm_events` logs at WARNING with exception type for easier future diagnosis of persistence issues.
- **Tests**: `tests/test_live_merge_2026_07_27_c3.py` — 52 assertions (EVM+Solana live merge, cache TTL, isolated provider failure, EVM non-regression, `_calver_key` with .cN sorting, regex accept/reject). All existing tests pass (swap_grouping, agg_tx).

### 2026.07.27.c2 — Fix: non-EVM transaction fetch hardened (non-EVM wallets processed first + trigger on non-EVM portfolio load) — Solana/BTC/Cosmos transactions now fetched and displayed

### 2026.07.27.c1 — Fix: Solana SPL transactions now detected (matching by owner instead of accountIndex) — SPL token transfers now appear on Transactions page

- **Root cause**: `_tb_lookup` in `_parse_solana_tx` filtered `pre/postTokenBalances` entries by `accountIndex` (the token account ATA index), instead of `owner` (the wallet owner address). Since an ATA's `accountIndex` is never equal to the wallet's index in `accountKeys`, the function NEVER returned an SPL delta → any pure SPL transaction (receiving/sending USDC or a token, the most common case) was dropped via `if not has_out and not has_in: return None`. Only transactions with native SOL movements produced an event.
- **1-line fix**: `if entry.get("accountIndex") != our_idx` → `if entry.get("owner") != address` in `_tb_lookup`. The `owner` field is the wallet owner and directly matches `address`.
- **Handled edge cases**: token account appears in pre but not post (closed account) or vice versa (new account) → merge mints with pre_amt/post_amt defaulting to 0.
- **Non-regression**: native SOL detection unchanged (`our_idx` still used for `preBalances/postBalances`). EVM/BTC/Cosmos providers unchanged.
- **Real mainnet validation**: address `5Q544fKrFoe6tsEbD7S8EmxGTJYAKtTVhAW5Q5pge4j1` — 3 SPL swaps detected (USDT→SOL, SOL→USDC, USDC→SOL), 6 legs persisted, `group_transaction_events` grouping correct, idempotence verified, Solscan links valid, case-insensitive wallet-aware filter preserves Solana base58 addresses.
- **Tests**: `tests/test_solana_spl_fix.py` — 27 assertions (SPL detection via owner, non-owner not captured, SOL non-regression, ATA creation/closure). `tests/test_solana_tx.py` updated (`_tb_entry` fixtures with `owner` field). All existing tests pass (74+70+34+67+swap_grouping = 245+ assertions).

### 2026.07.26 — Solana transactions: full history (getSignaturesForAddress + getTransaction RPC, send/receive/swap, Solscan links)

- **Solana transaction history**: the empty placeholder replaced with real history via public RPC `api.mainnet-beta.solana.com`. Uses `getSignaturesForAddress` (25 recent signatures) then `getTransaction` (jsonParsed, maxSupportedTransactionVersion:0) for ~22 transactions per call.
- **Send/receive/swap detection**: direction inferred from `preBalances`/`postBalances` (SOL) and `preTokenBalances`/`postTokenBalances` (SPL). A transaction with both out and in of different tokens is classified as "swap". Types "send", "receive", and "swap" are detected.
- **Best-effort USD pricing**: SOL price via DefiLlama (`_get_sol_price_usd`), SPL prices via batch (`_get_spl_prices`). If a price is unavailable, `usd_value` is 0 without crashing.
- **Defensive**: timeout, RPC errors, 429 → failed transaction skipped, others continue. Never 500. Wallet with no transactions returns empty list cleanly.
- **Solscan links**: each event has `tx_hash` + link `https://solscan.io/tx/{signature}` (via existing `explorer_tx_url`).
- **Standard format**: events follow the same format as EVM/BTC transactions — `type`, `direction`, `sent`/`received`, `sent_symbol`/`recv_symbol`, `gas_fee_usd`, `usd_value`, `block_time`. Compatible with `group_transaction_events()` in `tx_events.py` and existing frontend display.
- **Tests**: `tests/test_solana_tx.py` — 74 assertions (SOL send/receive parsing, SPL send/receive, USDC→USDT swap, SOL→USDC swap, defensive, non-regression). `tests/test_solana_provider.py` updated (placeholder test now verifies real event shape). Live test on real Solana address: 8 transactions successfully fetched.
- **Phase 3 Roadmap** added to README.

### 2026.07.25 — Airdrops to claim: best-effort detection (claimable staking rewards, extensible checker registry) + dedicated page + alerts

- **Checker registry**: extensible architecture `src/services/airdrops/` — interface `AirdropChecker` (name, chain_types, async check()) + registry `get_claimable_airdrops(address, chain_type)`. Routing by chain_type: a cosmos checker is NEVER called for an EVM address. Defensive: a checker that fails/times out never impacts others (15s timeout per checker, total isolation).
- **Cosmos staking rewards checker**: the most reliable — reuses CosmosProvider's existing LCD calls (`_get_rewards`) to detect pending staking rewards. Each reward becomes an AirdropClaim (status "claimable", Mintscan link, amount + USD value). DefiLlama pricing (free).
- **API `/api/airdrops`**: GET endpoint that aggregates all claimable airdrops for all user wallets (all chains). Response grouped by `wallet_address → chain → claims`, with `total_claimable_usd` and `total_claims`. Automatic in-app notification generation.
- **Airdrops Page 🪂**: new dedicated page in sidebar menu (between NFTs and Transactions). Table by wallet/chain with source, token, amount, value, Claim link. Clean empty state ("No claimable airdrops detected"). i18n FR/EN.
- **Integrated alerts**: when an airdrop is detected, a notification is automatically created via `send_alert_notification()` (existing system). Non-intrusive, visible in Alerts page 🔔.
- **Tests**: `tests/test_airdrops.py` — 32 assertions: registry routing by chain_type, static rewards parsing, defensive isolation (crashing/hanging checker), registry introspection, non-regression provider_for(x) for EVM/BTC/Solana/Cosmos.

### 2026.07.24 — Support Cosmos/ATOM (balance + delegated staking + rewards via public LCD)

- **CosmosProvider**: new multi-chain provider for Cosmos bech32 addresses (cosmos1…, osmo1…, celestia1…, juno1…, stars1…, akash1…, inj1…, kujira1…, stride1…). Conservative detection — rejects EVM (`0x...`), BTC bech32 (`bc1...`), Solana. Module `src/services/providers/cosmos.py` (+ test file `tests/test_cosmos_provider.py`, 77 assertions).
- **Free public LCD**: Cosmos REST endpoints (Polkachu) — available balance (`/cosmos/bank/v1beta1/balances`), staking delegations (`/cosmos/staking/v1beta1/delegations`), pending rewards (`/cosmos/distribution/v1beta1/delegators/{addr}/rewards`). 3 parallel calls, 20s timeout, defensive — each call independent, never 500.
- **ATOM/OSMO pricing**: DefiLlama (coins.llama.fi) — free, no key. uatom/uosmo → ATOM/OSMO conversion (÷1e6). Unknown denoms → `price_unknown`, never invented prices.
- **Standard portfolio**: available native token + staked token (category `"staked"`) + rewards token (category `"rewards"`). Aggregated `staked_usd`, `chains`, `total_usd`, `defi_breakdown`. Transactions: placeholder (returns empty, no crash).
- **Explorer**: Mintscan links (`mintscan.io/{chain}/address/` and `/tx/`), mapped by address HRP.
- **Auto-routing**: `provider_for()` recognizes Cosmos addresses → `/api/portfolio` and `/api/wallets` (add) work without chain-specific code. ALL aggregated view sums EVM + BTC + Solana + Cosmos. `test_providers.py` updated (provider_for Cosmos → CosmosProvider instead of None).
- **Native staking**: Cosmos staking (delegated + rewards) appears in portfolio view with `category: "staked"` and `category: "rewards"` — ready for DeFi/positions display. NFT: clean empty.

### 2026.07.23 — Solana support (SOL + SPL tokens via public RPC)

- **SolanaProvider**: new multi-chain provider for Solana addresses (32-byte base58 public keys). Conservative detection — rejects EVM (`0x...`), BTC bech32 (`bc1...`), Cosmos-like. Minimal built-in base58 decoding (stdlib only, no external dependency). Module `src/services/providers/solana.py`.
- **Free public RPC**: native SOL balance (`getBalance` in lamports) + SPL accounts (`getTokenAccountsByOwner` via Token program ID). 20s timeout, defensive — never 500, best-effort.
- **SOL/USD price**: DefiLlama (coins.llama.fi) — free, no key. SPL pricing: DefiLlama batch `solana:{mint}` in batches of 50 — best-effort, no price → `price_unknown`.
- **Standard portfolio**: same shape as EVM/BTC — SOL token + SPL tokens (known symbols for ~40 major tokens, otherwise truncated mint). `chains`, `total_usd`, `defi_breakdown`, `active_count`. Transactions: placeholder (returns empty, no crash).
- **Explorer**: Solscan links (`solscan.io/account/` and `/tx/`).
- **Auto-routing**: `provider_for()` recognizes Solana addresses → `/api/portfolio` and `/api/wallets` (add) work without chain-specific code. ALL aggregated view sums EVM + BTC + Solana.
- **NFT / DeFi**: returns clean empty for Solana (no crash).
- **Tests**: `tests/test_solana_provider.py` (66 assertions) — base58, detect, provider_for, metadata, portfolio shape, lamports, SPL lookup, registry, transactions placeholder. EVM/BTC tests updated (provider_for now recognizes all chains).

### 2026.07.22 — Bitcoin support (BTC via mempool.space)

- **BitcoinProvider**: new provider for Bitcoin addresses (bech32 `bc1...`, legacy `1...`, P2SH `3...`). Balance via mempool.space (free, no key), BTC/USD price via DefiLlama, basic transactions. Module `src/services/providers/bitcoin.py`.
- **Standard portfolio**: same shape as EVM — single BTC token, `chains`, `total_usd`, `errors`. Transaction events in standard format (send/receive with USD values).
- **Routing**: wallets accept and route BTC addresses automatically via `provider_for()`.

### 2026.07.21 — Multi-provider abstraction (ChainProvider) — foundation for Bitcoin/Solana/Cosmos, zero EVM changes

- **`ChainProvider` interface**: abstract class defining the common contract for all future chain providers (`detect()`, `get_portfolio()`, `get_transactions()`, `explorer_url()`, `chain_type`, `native_symbol`). Module `src/services/providers/base.py`.
- **Registry**: ordered `PROVIDERS` list + `provider_for(address)` function returning the first provider whose `detect()` is true, or `None`. Extensible: adding a provider = implementing the interface + registering it.
- **`EvmProvider`**: thin wrapper delegating to `_compute_portfolio` and existing transaction logic WITHOUT REWRITING ANY business logic. Detection `0x...` (42 hex chars). Module `src/services/providers/evm.py`.
- **Non-breaking routing**: `/api/portfolio` and `/api/transactions` endpoints check `provider_for(address)` before executing EVM logic. EVM address → unchanged path (zero regression). Non-EVM address (`bc1...`, Solana, Cosmos) → clean response `{supported: false, message: "Chaine non prise en charge (a venir)"}` without 400 error.
- **Helper `get_portfolio_via_provider(address)`**: canonical entry point for future multi-chain integrations.
- **Tests**: `tests/test_providers.py` (34 assertions) — detection, registry, extensibility, delegation, unsupported response contract.

### 2026.07.20 — Auth hardening: optional 2FA TOTP, anti-brute-force, password change, multi-user isolation

- **Optional 2FA TOTP**: two-factor authentication (TOTP) via mobile app (Google Authenticator, Authy...). Disabled by default — full backward compatibility: a user without 2FA logs in as before. 3-step activation in ⚙️ Settings → "Security" card: scan QR code (or manual secret entry), verify code, activate. Deactivation by TOTP code or password. QR code generated server-side (`pyotp` + `qrcode`) — works offline. Login: if 2FA enabled, backend returns `twofa_required:true` → frontend shows TOTP code field → second-step verification via `/api/auth/login/2fa`. Secrets stored in DB, never returned after activation.
- **Anti-brute-force**: in-memory failed login attempt limiter (by username+IP). After 5 consecutive failures in a 5-min window, backoff of 60s (doubling at each 5-failure threshold). Clear message "Too many attempts, try again in X s". Counter reset on success. No persistence — local app doesn't need Redis.
- **Password change**: `PUT /api/auth/password` endpoint revised — verifies `old_password` (bcrypt), enforces minimum length (4 chars), stores new bcrypt hash. UI in ⚙️ Settings → "Password" card with confirmation.
- **Multi-user isolation audited**: exhaustive check of every data endpoint (wallets, transactions, snapshots, PNL, alerts, notifications, API keys, token prefs, backups, analytics, exports, DeFi, NFTs) — all filter by `user_id`. Fix: `PUT /api/wallets/{id}` now checks `cur.rowcount` and returns 404 if wallet doesn't belong to user. Isolation tests: `tests/test_isolation.py` — 10 assertions (2 users created, airtight verification across their wallets, alerts, API keys, 2FA, transactions).

- **Topbar removed**: the top bar containing the search field (`#globalSearch`) and quick wallet selector (`#quickWallet`) is removed. The wallet tab band (`#walletTabs`) is also removed.
- **Permanent aggregated view**: `activeWallet` is forced to `"ALL"` permanently — the app always shows the aggregate of all wallets.
- **JS neutralized**: orphan functions (`applyGlobalSearch`, `populateQuickWallet`, `changeWallet`, `renderWalletTabs`) removed. No JS errors on load (`Cannot read properties of null`).
- **CSS cleaned**: `.topbar`, `.wallets-bar`, `.wallet-tab` rules removed (~20 CSS lines saved).
- **Smoke test**: `tests/smoke-topbar-removal.js` — verifies that 4 functions are absent and core functions (`selectWallet`, `switchPage`, `esc`, `t`) work without errors.

### 2026.07.18 — Updater self-update via HTTPS (public repo, no key issues)

- **HTTPS fetch**: the host updater (`host-updater.sh`) now uses `git fetch` via HTTPS (`https://github.com/LostInTheBugs/Crypto-Wallet-Tracker.git`) instead of SSH (`git@github.com`). Since the repo is public, no key or authentication needed.
- **SSH logic removed**: all SSH key detection/copying (`find_and_copy_key`, `GIT_SSH_COMMAND`, `DEPLOY_KEY`) removed. No more `Permission denied (publickey)` or interactive prompts — `credential.helper` is explicitly disabled (`git -c credential.helper=`).
- **Bootstrap**: the production VM repo remote switched to HTTPS. The fixed updater reinstalled, service restarted.

### 2026.07.17 — Auto database backups, health/status page, tests + CI

- **Auto backups**: background asyncio task backs up `/data/wallets.db` to `/data/backups/wallets-YYYYMMDD-HHMMSS.db` daily (configurable, `BACKUP_INTERVAL_HOURS`). Consistent backup via `sqlite3.backup()` API (WAL snapshot), coordinated with global write lock. Retention of last N backups (default 7, `BACKUP_RETENTION`), oldest deleted. Resilient: a backup error doesn't crash the app.
- **Backup endpoints**: `POST /api/backups/run` triggers immediate backup, `GET /api/backups` lists backups (name, size, date). Auth required.
- **Health / Status**: `GET /api/health` (public) returns `{status, version, db_ok, uptime_s, counts, last_backup}`. Tolerant: `db_ok=false` instead of 500. No secrets exposed.
- **UI**: "🫀 Health / Status" card in Settings showing version, DB state, uptime, last backup. "Backup now" button + backup list with sizes.
- **Extended tests**: 13 new pure unit tests in `tests/test_core.py` for `token_tid`, `classify_token`, and DeFi classifier `classify_token_type` (20 tests total). Runnable without network or real DB.
- **CI GitHub Actions**: workflow `.github/workflows/ci.yml` on push/PR: `python -m py_compile` on `src/`, unit tests, `node --check` on inline JS in `public/index.html`.
- **Roadmap**: corrected lines (2026.07.17 checked, 2026.07.18 for auth).

### 2026.07.16 — Auto or manual updates (choice, in Settings)

- **Update mode choice**: new setting in ⚙️ Settings → Version card — radio button `Manual / Auto`. Persisted in `/data/deploy/config.json` (shared volume, readable by host updater).
- **API endpoints**: `GET /api/settings/update-mode` (read), `PUT /api/settings/update-mode {mode:"auto"|"manual"}` (write, auth required).
- **Auto mode**: host updater (`host-updater.sh`) periodically checks (~3 min) if `origin/main` is ahead via `git fetch` + hash comparison. If a new version is detected, the full deploy cycle (reset --hard + Docker rebuild) is triggered automatically, without a click.
- **Manual mode** (default): unchanged behavior — "Update" button appears when a new version is available, deploy triggered by click (writing `request.json`).
- **UI**: in auto mode, the "Update" button is hidden and replaced with "⚙️ Auto-updates enabled — the app updates itself". Toggle updates config file and refreshes display immediately.
- **i18n**: new keys `updModeLabel`, `updManual`, `updAuto`, `updAutoMsg` (FR + EN).
- **Robustness**: updater reads config file on every iteration (no restart needed). Fetch failure → no blocking. Auto mode → consistent status (`state:done/failed`, version from `verCurrent`). Existing manual request mechanism (`request.json`) preserved and takes priority.

### 2026.07.15 — Self-update trigger hardened (polling, request cleanup, correct version)

- **Robust polling**: abandoned fragile systemd.path (PathExists) — replaced with long-running polling service (loop every ~12s). No more `unit-start-limit-hit` when `request.json` wasn't deleted.
- **Systematic cleanup**: `request.json` always deleted after each cycle (success or failure) — the next "Update" click cleanly re-triggers.
- **Real version**: version reported in `status.json` is read from `public/index.html` (`id="verCurrent"`) after reset, not from an obsolete git tag.
- **Verification**: 3 consecutive idempotent update cycles verified on production VM.

### 2026.07.14 — Updater hardened (git reset --hard, no more local divergence blocking)

- **Robust updater**: replaced `git pull origin main` with `git fetch origin main --quiet && git reset --hard origin/main && git clean -fd`. The updater now always brings /opt/crypto-wallet-tracker exactly to origin/main, regardless of local divergence — never again "Your local changes would be overwritten by merge — Aborting".
- **Verification**: 2 complete idempotent update cycles verified on production VM.

### 2026.07.13 — Self-update via host-side updater (Update button functional)

- **Host-side updater**: the container no longer manages its own deployment (it has neither git nor docker). Clicking "Update" in ⚙️ Settings writes a request file on a shared Docker volume (`/data/deploy/request.json`). A systemd service on the host (`crypto-update.path` + `crypto-update.service`) watches this request, runs `git pull origin main` then `docker compose up -d --build`, and writes status to `/data/deploy/status.json`. Frontend polls status every 3 seconds and reloads on success.
- **Security**: the container never has access to the Docker socket, git, or host. It only writes a file on a shared volume. Updater runs as `root` on host with necessary permissions.
- **UI fix**: frontend never shows "undefined" on error — falls back to `d.msg || d.detail || "Request failed"`. Added i18n keys FR/EN for all deploy states (requesting, deploying, done, failed, timeout).
- Files added: `deploy/host-updater.sh`, `deploy/crypto-update.path`, `deploy/crypto-update.service`.

### 2026.07.12 — SQLite consolidation: serialized writes (end of "database is locked")

- **Global write lock**: a shared `asyncio.Lock` (`src/services/db.py`) serializes ALL SQLite writes (INSERT/UPDATE/DELETE/CREATE/REPLACE + commit). Reads do NOT take the lock (WAL). No more "database is locked" under concurrency (background workers: history rebuild, price enrichment, alert evaluator + user write requests).
- **Defense in depth**: WAL + busy_timeout=10000 preserved. Subprocesses (rebuild_worker.py, enrich_worker.py) use synchronous sqlite3 with busy_timeout.
- **Concurrency test**: `tests/test_write_lock.py` — 20 workers × 25 concurrent writes = 500 INSERT+COMMIT → 0 "database is locked" errors, all rows validated.

### 2026.07.11 — Installable PWA, light/dark theme, global search, watchlist & groups

- **Installable PWA**: manifest.json, service worker (app shell cache, network-first for API), 192×192 and 512×512 icons. App installable on mobile/desktop with basic offline display.
- **Light / Dark theme**: CSS variables for both themes, 🌙/☀️ toggle button in sidebar, choice persisted in localStorage. Verified for readability (text contrast, badges, Chart.js charts).
- **Global search / filter**: search field in topbar that instantly filters (client-side) tokens (by symbol/name/chain/wallet) and transactions (by symbol/name/hash/address/chain). Case-insensitive.
- **Watchlist (read-only)**: `watch_only` column on wallets. A watched address is displayed and viewable but **excluded from totals** (net worth, dashboard, analytics, snapshots). "👁 watch" badge and toggle button.
- **Wallet groups**: optional `group_label` field on wallets. Grouped display in wallet list (separator line per group). No impact on totals.

### 2026.07.10 — NFT: direct source links + floor reliability (liquidity)

- **Direct source links**: each NFT now shows a direct link to its marketplace source (`market_url`), in addition to the Blockscout explorer link (`explorer_url`). If floor comes from OpenSea, the link points to the asset or collection page. In `/api/nfts`, each item has `market_url` (OpenSea) AND `explorer_url` (Blockscout). Valuation collection also has both links. **No more "source OpenSea" when item is unfindable on OpenSea** — link points to real source.
- **Floor reliability (liquidity)**: each valued collection now carries `floor_reliable` (bool) + `floor_confidence` ("high"/"low"/"none"), determined by liquidity signals from source APIs: 24h volume, active listing count, best offer (top bid), owner count. Conservative rules: floor with no volume, no listings, and no offer = unreliable (confidence "none"), excluded from total.
- **Separate totals**: `nft_total_value_usd` = sum of **reliable** floors only. `nft_indicative_value_usd` = sum of unreliable floors. Token+NFT net worth on dashboard only uses reliable floors — **no more inflated net worth from zombie collections**.
- **Enriched UI**: confidence badge per collection (✓ green if reliable, "⚠ indicative" orange if unreliable, gray if low). Direct "OS" (OpenSea) and "🔗" (Explorer) buttons on each NFT card and each valuation row. Indicative value displayed separately in orange.
- **Enriched source APIs**: OpenSea now returns listings_count, best_offer, volume_24h, num_owners. Reservoir returns volume_24h, listings_count, best_offer_eth. Moralis unchanged (simple floor endpoint).

### 2026.07.9 — Multi-source pricing (CoinGecko) + API key testing

- **CoinGecko as primary price source**: when a CoinGecko API key is configured, current token prices are enriched via CoinGecko API (`/simple/token_price` by contract, `/simple/price` for native coins). **Conservative**: a CoinGecko price only overrides an existing price (Blockscout/DefiLlama) if strictly > 0. Without a key, behavior is unchanged. Coverage improves notably for memecoins and exotic tokens.
- **`price_source` field per token**: each token in portfolio response now carries `price_source` (`"blockscout"`, `"coingecko"`, or `"defillama"`) indicating its current price origin.
- **"Test" button per API key**: endpoint `POST /api/settings/keys/{provider}/test` validates the stored (or body-provided) key via a lightweight provider call. Returns `{valid: bool, message}`. Works for CoinGecko, OpenSea, Etherscan, DefiLlama, Alchemy, Moralis, CoinMarketCap.
- **"Unlocks" metadata per provider**: each `GET /api/settings/keys` catalog entry now includes `unlocks`, a short phrase describing what the key concretely enables. Displayed on Settings > External API Keys page.

### 2026.07.8 — NFT valuation (floor prices) + Tokens+NFT net worth

- **NFT valuation**: new endpoint `GET /api/nfts/valuation?address=` returning floor prices per held collection, with total `nft_total_value_usd`. Sources, in order: OpenSea (API key), Moralis (API key), Reservoir (free, best-effort). Without any key, `floor_source: "none"` + message inviting key configuration. **Never 500** — any API error is isolated and degrades gracefully.
- **1h server cache** per (user, address) — one request per collection, not per individual item. ETH→USD conversion via ETH price (portfolio cache or DefiLlama).
- **Dashboard — decomposed net worth**: new line "Tokens: X + NFTs: Y = Total: Z" between stat cards and PNL cards. **NFT value is NOT injected** into token total, token PNL, or daily_history — it's an additional display line that doesn't pollute history.
- **Enriched NFTs page**: summary card (total value, source, valued collection count), floor table per collection (name, floor ETH/USD, items, total value, source), and warning badge "Add an OpenSea/Moralis key" with Settings link when no valuation available.
- **API keys**: existing `_get_user_moralis_key` helper + new `_get_user_opensea_key`. Valuation caches invalidated on OpenSea or Moralis key add/delete.
- **i18n**: full FR/EN, `esc()` everywhere, no literal `\n` in JS, fully defensive.

### 2026.07.7 — Health factor / liquidation risk alerts

- **New "health" alert type**: monitors lending position health factor via Moralis API. Triggers a notification when at least one position's health factor drops below the configured threshold (default 1.2).
- **Moralis integration**: reuses existing DeFi data source (`/api/defi/positions`). Without a Moralis key, alert is marked "Requires Moralis key" — never false positives or 500.
- **Alert message** includes protocol, chain, current health factor, threshold, and supplied/borrowed amounts.
- **UI**: new type "Health / Liquidation" in alert creation form, threshold field (default 1.2), protocol scope (all or specific). Badge "⚠️ Requires Moralis key" on health alerts without configured key.
- **Fix**: `POST /api/alerts` now returns real inserted `id` (via `cursor.lastrowid` instead of `connection.last_insert_rowid`).
- **i18n** FR/EN, `esc()` everywhere, fully defensive (no key → readable state, no crash).

### 2026.07.6 — Alert engine (price, portfolio, movements) + notifications + digest

- **Alert engine**: create/delete/enable alerts of 3 types — **price** (token above/below threshold), **portfolio** (total value above/below threshold), **movement** (change > X% in 24h). Async evaluator every 10 minutes (cooldown per alert, never burst re-triggering).
- **In-app notification center**: a notification created on each triggered alert (title + description). Dedicated interface with "read" marking, unread counter badge.
- **External channels**: **Webhook** (POST JSON), **Telegram** (Bot API), **Email** (SMTP, optional). Per-channel configuration (URL, token, credentials), secrets masked in GET, send test (`POST /api/alerts/test-channel`), robust — a failing channel doesn't block others.
- **Digest**: daily or weekly portfolio summary (value, 24h/7d changes) sent via chosen channel.
- **🔔 Alerts page** in sidebar menu — 4 sections: my alerts (create + list), notification center, notification channels, digest. i18n FR/EN, `esc()` everywhere, clean empty states.
- **APIs**: `GET/POST/PUT/DELETE /api/alerts`, `GET /api/notifications`, `POST /api/notifications/read`, `GET/PUT /api/settings/notif-channels`, `GET/PUT /api/settings/digest`, `POST /api/alerts/test-channel`, `GET /api/notifications/count`.
- Database: 4 new tables `alerts`, `notifications`, `notif_channels`, `digest_prefs` (idempotent migrations).

### 2026.07.5 — Enriched transactions (approve, contract interactions, gas analytics, tags/notes)

- **Extended collection**: in addition to token-transfers, now captures all address transactions via Blockscout `/addresses/{address}/transactions` endpoint. Detects `approve` (spend approval), `contract` (contract interaction without token transfer), and `native` (native coin send/receive) transactions.
- **No duplicates**: an already-present tx_hash (token transfer) is kept enriched (method), never duplicated.
- **`/api/transactions` API**: new types `approve|contract|native` in `type=` filter, extended counts, user tags attached to each event.
- **Gas analytics**: `GET /api/gas/analytics?address=&range=` → total gas spent, daily time series, per-chain breakdown. Gas card on Transactions page with mini Chart.js chart.
- **Tags/notes**: `user_tx_tags` table, endpoints `POST /api/transactions/tag` (upsert) and `GET /api/transactions/tags`. Inline interface: click tag icon → editor (category + note), immediate save. Suggested categories: income, trade, transfer, fee, other.
- **UI**: distinct colored badges (✅ Approve orange, 📄 Contract blue, 📥/📤 Native green/red). Enriched type filter. Exhaustive i18n FR/EN.

### 2026.07.4 — CSV/PDF export (holdings, transactions, PnL report, summary)

- **⚙️ Settings → 📤 Export / Backup section**: 4 download buttons (Holdings CSV, Transactions CSV, PnL Report CSV, Summary PDF), "Generating…" state, error handling, active wallet respect (address or `ALL`), i18n FR/EN.
- **Protected endpoints `GET /api/export/holdings.csv|transactions.csv|pnl.csv|summary.pdf?address=0x…|ALL`** with download headers (`Content-Disposition: attachment`). **Active** tokens only, existing wallets only (anti-orphan-data defense), aggregation by symbol+chain in `ALL` mode.
- **holdings.csv**: token_name, symbol, chain, balance, usd_price, usd_value, category, cost_basis, pnl. **transactions.csv**: Sent/Received/Swap events (swap detection logic, legs grouped by tx), signed amounts, gas. **pnl.csv** (best-effort tax report): quantity, average unit cost, total cost, current value, unrealized PnL — same cost logic as per-token PNL in dashboard; unknown cost → empty cells (never false "bought for free").
- **summary.pdf**: total value, total PnL, chain and category breakdown (/api/analytics logic), top 15 holdings, generation date — internal minimal PDF generator (PDF 1.4, **zero added dependencies**).
- Robustness: CSV RFC 4180 (quotes/commas/newlines escaped), UTF-8, dot decimals; missing data → empty cell; any error → empty export with headers, **never 500**.
- Tests: `python3 tests/test_export_service.py` (CSV quoting, aggregation, PnL, PDF structure/xref) + `node tests/smoke_export_2026.07.4.js` (runtime smoke of Export section rendering).

### 2026.07.3 — Analytics page (allocation & performance)

- **New 📊 Analytics page** (sidebar menu): synthetic view of portfolio allocation and performance — first roadmap release.
- **Endpoint `GET /api/analytics?address=…&range=24h|7d|30d`** (address = wallet or `ALL`): allocation by chain / category (wallet, lending, staked, LP, vault, synthetic) / asset (top 12 + "Others"), total value changes over 24h/7d/30d (`daily_history` aggregate), best/worst performers by **price** change (neutralizes inflows/outflows, spam and dust ignored), best-effort Portfolio vs BTC/ETH benchmark (DefiLlama). **Active** tokens only. Defensive: insufficient history → `null`/"—", never 500. 300s server cache per (user, address, range).
- **UI**: 3 change cards (green/red, "—" if unavailable), 3 Chart.js donuts (dark theme, chain colors consistent with dashboard), Top gainers / Top losers table, 24h/7d/30d period selector, i18n FR/EN, clean empty states, proper Chart.js instance destruction on reload.
- Tests: `python3 tests/test_analytics_service.py` (48 assertions) + `node tests/smoke_analytics_2026.07.3.js` (runtime smoke of rendering). Stats/Dashboard/portfolio pages unchanged (full backward compat).

### 2026.07.2 — Cache-Control: no-cache header on SPA (end of stale browser-cached versions)

- **Cache-Control: no-cache, must-revalidate** on root route — browser keeps file in cache but must revalidate on each visit via ETag/Last-Modified. 304 if unchanged, new version on deploy. Goodbye stale versions served from browser cache.

### 2026.07.1 — Switch to calendar versioning (YYYY.MM.N)

**What changes:**
- Project switches from **semver** (vX.Y.Z) to **calendar versioning** in `YYYY.MM.N` format (year.month.number).
- `N` resets to 1 each month (next July release = `2026.07.2`, August = `2026.08.1`, etc.).
- New scheme starts at `2026.07.1` (old dev tags removed to keep repo clean).
- **Backend**: `GET /api/version/latest` recognizes CalVer tags and compares them numerically by (year, month, N). A CalVer tag is always considered newer than a semver tag.
- **Frontend**: `checkVersion()` now correctly compares CalVer versions (year → month → N). Any unexpected format triggers an equality fallback = up to date.
- Display no longer uses `v` prefix in version messages.

> Full history of old v1/v2 versions: see Git commit history.

## 📝 License

MIT

---

## 🔖 Current version: **2026.08.001** — [View all releases](https://github.com/LostInTheBugs/Crypto-Wallet-Tracker/releases)
