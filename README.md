# Crypto Wallet Tracker — v2.10.0

**Inventaire local de wallets crypto** — multi-wallets, multi-chaînes EVM, 100 % gratuit (API Blockscout).

Dashboard agrégé, graphiques d'évolution, historique des prix via DefiLlama, PNL par token, transactions paginées, comptes utilisateurs. Le tout en Docker, une seule commande.

---

## ✨ Fonctionnalités

- 🔗 **21 chaînes EVM** — Ethereum, Base, Optimism, Arbitrum, Polygon, Gnosis, zkSync, Celo, Scroll, Soneium, Ink, Mode, Unichain, Lisk, Linea, Etherlink, Metis, Manta, BOB, Zora, World Chain
- 🪙 **Solde natif** — ETH/POL/xDAI/CELO/XTZ/METIS récupéré en parallèle des tokens (appel API natif)
- 💰 **Valorisation USD/€** — temps réel via Blockscout, conversion EUR (Frankfurter)
- 🦙 **Fallback prix DefiLlama** — si Blockscout ne donne pas de prix, appel batch à l'API gratuite `coins.llama.fi/prices/current`
- 🔒 **Détection tokens stakés** — badge visuel + agrégat `staked_usd` (LST, aTokens, Beefy, Stargate, LP tokens)
- 👥 **Comptes utilisateurs** — inscription, connexion, wallets privés (bcrypt + sessions)
- 📊 **Dashboard** — valeur totale, répartition par chaîne (donut), cartes PNL Total / PNL 24h, mini-graphe, gaz cumulé
- 📈 **Statistiques** — courbes valeur/coût d'achat, barres PNL journalier (7j/30j/90j/1a/All), filtrable par wallet/token/chaîne
- 📜 **Transactions** — tableau paginé, filtrable par wallet/chaîne/direction, colonnes prix/valeur/gaz
- 📋 **Détail tokens** — balance, prix, valeur et **PNL par token** (vert/rouge)
- 🔙 **Historique des prix** — DefiLlama (gratuit, sans clé API) + cache SQLite, fallback CoinGecko optionnel
- 🧮 **PNL calculé** — coût moyen pondéré, soldes reconstruits par date, PNL journalier
- 🛡️ **Filtre anti-spam** — détection automatique des tokens de scam/airdrop
- ⚙️ **Paramètres** — langue (FR/EN), devise (USD/EUR), changement de mot de passe, clés API utilisateur
- 🔑 **Clés API par utilisateur** — CoinGecko, Alchemy (saisies et validées dans Paramètres, jamais en clair dans l'API)
- 📦 **Vérification de version** — compare avec le dernier tag GitHub
- ⚡ **Cache prix** — table `price_history`, 2ᵉ rebuild ~0 appel réseau
- 🐳 **Docker** — une commande pour déployer

---

## 🚀 Installation

```bash
curl -fsSL https://raw.githubusercontent.com/LostInTheBugs/Crypto-Wallet-Tracker/main/install.sh | sudo bash
```

Puis ouvre `http://<ip-du-serveur>`.

### Manuel (Docker)

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
│   ├── app.py               # Backend FastAPI — routes, auth, wallet CRUD (~900 lignes)
│   └── services/            # Modules métier
│       ├── price_service.py   # SYMBOL_TO_CG, DefiLlama/CoinGecko, cache prix
│       ├── pnl_service.py     # Timeline unifié, reconstruction soldes, PNL
│       └── portfolio_service.py  # 21 chaînes, natif, fallback prix, spam, staked
├── public/index.html        # Frontend SPA + Chart.js (~800 lignes)
├── Dockerfile
├── docker-compose.yml
├── install.sh               # Installeur automatique
├── requirements.txt
└── README.md
```

---

## 🔧 Configuration (.env)

| Variable | Défaut | Description |
|---|---|---|
| `PORT` | `80` | Port d'écoute |
| `SESSION_SECRET` | auto | Secret JWT (fixer pour persister les sessions) |
| `ALCHEMY_API_KEY` | — | Optionnel : fallback pour balances/transfers si Blockscout échoue |

---

## 🛠️ Stack

| Couche | Technologie |
|---|---|
| Backend | Python 3.12 · FastAPI · aiosqlite · httpx |
| Frontend | Vanilla JS · Chart.js 4 · GitHub dark theme |
| Prix historiques | **DefiLlama** (primaire, gratuit) + CoinGecko (fallback, nécessite clé) |
| Transactions | Blockscout API v2 (ERC-20/721/1155 token-transfers) |
| Déploiement | Docker · docker compose |

---

## 📡 Sources de données

| Donnée | Source | Gratuit |
|---|---|---|
| Soldes temps réel | Blockscout `/token-balances` | ✅ |
| Transferts de tokens | Blockscout `/token-transfers` | ✅ |
| Prix historiques | DefiLlama `/chart` | ✅ |
| Prix historiques (fallback) | CoinGecko `/market_chart/range` | ❌ (clé API) |
| Frais de gaz | Blockscout `/transactions` | ✅ |
| Prix actuels | Blockscout (intégré dans `/token-balances`) | ✅ |
| Conversion EUR | Frankfurter (BCE) | ✅ |

---

## 🧮 Calcul du PNL

- **Soldes reconstruits** : cumul des transferts signés par date (`in` − `out`)
- **Coût d'achat** : coût moyen pondéré par token (entrées au prix du jour, sorties au coût moyen)
- **PNL** : `valeur_actuelle − coût_moyen`
- **PNL journalier** : `valeur(j) − valeur(j−1) − flux_nets(j)`
- **Réconciliation** : delta entre historique et portfolio affiché en avertissement si >15%

---

## 🔐 Sécurité

- Mots de passe hashés **bcrypt**
- Sessions en cookies httpOnly
- **Aucune clé privée** — uniquement des adresses publiques
- Clés API utilisateur : stockées chiffrées, jamais renvoyées en clair (masquées `sk-...abc`)
- Données 100 % locales (SQLite)

---

## 📝 Licence

MIT
