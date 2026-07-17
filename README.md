# Crypto Wallet Tracker — v2.11.14

**Inventaire local de wallets crypto** — multi-wallets, multi-chaînes EVM, 100 % gratuit (API Blockscout).

Dashboard agrégé, graphiques d'évolution, historique des prix via DefiLlama, PNL par token, transactions paginées, comptes utilisateurs. Le tout en Docker, une seule commande.

---

## ✨ Fonctionnalités

- 🔗 **22 chaînes EVM** — Ethereum, Base, Optimism, Arbitrum, Polygon, Gnosis, zkSync, Celo, Scroll, Soneium, Ink, Mode, Unichain, Lisk, Linea, Etherlink, Metis, Manta, BOB, Zora, World Chain, HyperEVM
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
│       └── portfolio_service.py  # 22 chaînes, natif, fallback prix, spam, staked
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

## 📋 Changelog

### v2.11.14
- **Enrichissement des prix fiabilisé** — concurrence globale douce (une seule limite partagée entre toutes les chaînes au lieu d'une par chaîne), retries avec backoff sur timeout/erreur, et drapeau `price_checked` pour ne plus re-tester à chaque exécution les tokens sans prix (spam/inconnus)
- **Agrégat aligné** — la dernière valeur des courbes d'évolution et la réconciliation portent désormais sur l'ensemble des tokens réellement valorisés (tokens déjà filtrés du spam), pour un indicateur cohérent

### v2.11.13
- **Correctif concurrence enrichissement** — l'enrichissement des prix historiques ouvrait une connexion SQLite par écriture, en parallèle, provoquant des verrous silencieux (0 ligne enrichie). Les appels réseau DefiLlama restent concurrents, mais les écritures en base sont désormais sérialisées sur une connexion unique avec un commit final.
- **Tokens prisés non mappés inclus dans l'historique** — les tokens absents du mapping CoinGecko mais disposant de prix d'acquisition en transaction ne sont plus exclus du rebuild : leurs prix de transaction forment des séries de prix par date (forward-fill), ce qui les intègre à l'historique agrégé et améliore la réconciliation.

### v2.11.12
- **Coût moyen pondéré** — le calcul du coût d'acquisition par token (fallback transactions) utilise désormais la méthode du coût moyen pondéré : les ventes retirent du coût cumulé au coût moyen d'achat, pas au prix de vente. PNL correct pour les tokens ayant eu des ventes.

### v2.11.11
- **Enrichissement des prix historiques** — prix d'acquisition par transaction via l'API historique DefiLlama (prix à date par adresse de contrat). La colonne `contract_address` est désormais stockée dans les transactions, ce qui permet de résoudre le prix réel d'achat de chaque token et de calculer un PNL exact (là où le prix est disponible).

### v2.11.10
- **PNL** — affichage "—" lorsque le prix d'acquisition est inconnu (transactions sans prix historique), au lieu d'un PNL trompeur égal à la valeur totale.

### v2.11.9
- **Correctif** — suppression d un caractère d échappement littéral (\n) introduit dans le JavaScript en v2.11.8, qui cassait toute la page (impossible de se connecter). Page de nouveau fonctionnelle.

### v2.11.8
- **Frais de gaz** — ne comptabilise que le gaz réellement payé par le wallet (émetteur de la tx, `from` == adresse du wallet) ; les réceptions sont exclues (gaz payé par l'expéditeur)
- **Colonne Gaz par wallet** — ajout d'une colonne Gaz (⛽) dans la page Wallets, avec somme des frais réellement supportés par chaque wallet, rafraîchie depuis l'API

### v2.11.7
- **Page Wallets** — affichage par wallet du montant total (USD/€), du nombre de tokens et des chaînes détectées (nombre + liste compacte triée par montant), rempli depuis le cache client puis rafraîchi via l'API ; ligne Total en bas du tableau

### v2.11.6
- **Cache client du portefeuille** — le résultat du portfolio est mémorisé dans le navigateur (localStorage, par vue « Tous » ou par wallet) : affichage instantané au rechargement de la page, rafraîchissement en tâche de fond avec indicateur « Mis à jour il y a X min », plus de « Scan… » systématique (conservé uniquement au premier chargement ou en rafraîchissement forcé)

### v2.11.5
- **Dashboard** — plages du graphe Évolution remplacées par 1 semaine / 1 mois / 3 mois (retrait du « 1J » qui n'affichait qu'un point)

### v2.11.4
- **Page Transactions** — lien vers l'explorateur de blocs par transaction (🔗) + colonne "Sens" clarifiée avec libellé Reçu/Envoyé et infobulle

### v2.11.3
- **Correctif Blockscout** — prise en charge de la clé `address_hash` (instances récentes type hyperscan) en fallback de `address` pour la résolution des adresses de contrat → pricing DefiLlama des tokens HyperEVM (WHYPE, etc.)

### v2.11.2
- **HyperEVM** — support de la chaîne HyperEVM (Blockscout hyperscan) avec pricing du coin natif HYPE via son token wrappé WHYPE sur DefiLlama. Note : seules les positions de wallet sont couvertes, pas le DeFi/staking.

### v2.11.1
- **Correctif frais de gaz** — valorisation au prix du jeton natif par chaîne (xDAI pour Gnosis, CELO pour Celo, POL pour Polygon, XTZ pour Etherlink, METIS pour Metis, ETH pour Ethereum et L2) au lieu du prix ETH systématique qui surévaluait les frais sur les chaînes non-ETH (un petit montant de jeton natif multiplié par le prix de l ETH gonflait fortement le total)

### v2.11.0
- **Correctifs** — snapshots limités à 500, PNL par token (stablecoins sans PNL fantôme), frais de gaz anti-surcomptage (imputation unique par tx_hash)
- **Backfill gaz parallèle** — requêtes par chaîne en parallèle avec concurrence bornée (asyncio.Semaphore), circuit breaker par chaîne après 5 échecs consécutifs, timeout réduit à 8s
- **Pagination transactions complète** — le cap dur de 100 pages est remplacé par une boucle jusqu'à épuisement de `next_page_params` (garde-fou `MAX_TX_PAGES` configurable, défaut 1000), avec retries exponentiels sur erreurs HTTP transitoires (timeout, 5xx)

---

## 📝 Licence

MIT
