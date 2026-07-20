# Crypto Wallet Tracker — 2026.07.24

**Inventaire local de wallets crypto** — multi-wallets, multi-chaînes EVM + Bitcoin + Solana + Cosmos, 100 % gratuit (API Blockscout + mempool.space + Solana RPC public + LCD Cosmos).

Dashboard agrégé, graphiques d'évolution, historique des prix via DefiLlama, PNL par token, transactions paginées, comptes utilisateurs. Le tout en Docker, une seule commande.

---

## ✨ Fonctionnalités

- 🔗 **22 chaînes EVM** — Ethereum, Base, Optimism, Arbitrum, Polygon, Gnosis, zkSync, Celo, Scroll, Soneium, Ink, Mode, Unichain, Lisk, Linea, Etherlink, Metis, Manta, BOB, Zora, World Chain, HyperEVM
- 🪙 **Solde natif** — ETH/POL/xDAI/CELO/XTZ/METIS récupéré en parallèle des tokens (appel API natif)
- 💰 **Valorisation USD/€** — temps réel via Blockscout, conversion EUR (Frankfurter)
- 🦙 **Fallback prix DefiLlama** — si Blockscout ne donne pas de prix, appel batch à l'API gratuite `coins.llama.fi/prices/current`
- 🔒 **Détection DeFi best-effort** — catégorisation fine (lending, LP, staked, vault, synthetic) via heuristiques sur les symboles, aucun service tiers, 100 % gratuit. Section DeFi dédiée avec badges colorés et sous-totaux par catégorie
- 🏦 **Page DeFi (Moralis)** — page dédiée listant les vraies positions DeFi par protocole (lending fourni/emprunté, staking, LP) avec récompenses, health factor, APY, PnL quand disponibles, et lien vers chaque position (dapp ou explorer). Résumé global fourni/emprunté/récompenses/valeur nette. Clé API Moralis (gratuite) recommandée dans Paramètres → Clés API externes ; **sans clé, mode gratuit best-effort** : les positions (lending fourni/emprunté, staking, LP, vaults) sont reconstruites depuis les balances on-chain Blockscout — récompenses/APY/health factor indisponibles
- 🎛️ **Gestion des tokens intégrée** — tout se passe dans l'onglet « Détail tokens » : compteurs actifs/inactifs, interrupteur on/off sur chaque ligne, section repliable des tokens inactifs (badge du motif), formulaire d'ajout manuel. Les tokens sans valeur, le spam, les memecoins illiquides et les prix à faible confiance DefiLlama sont désactivés par défaut ; un token désactivé est exclu des totaux, de la répartition DeFi et de l'historique (effet rétroactif)
- 👥 **Comptes utilisateurs** — inscription, connexion, wallets privés (bcrypt + sessions)
- 📊 **Dashboard** — valeur totale, répartition par chaîne (donut), cartes PNL Total / PNL 24h, mini-graphe, gaz cumulé
- 📈 **Statistiques** — courbes valeur/coût d'achat, barres PNL journalier (7j/30j/90j/1a/All), filtrable par wallet/token/chaîne
- 📜 **Transactions** — événements regroupés par transaction (Swap / Envoyé / Reçu), tableau paginé, filtrable par wallet/chaîne/type, colonnes prix/valeur/gaz
- 📋 **Détail tokens** — balance, prix, valeur et **PNL par token** (vert/rouge)
- 🔙 **Historique des prix** — DefiLlama (gratuit, sans clé API) + cache SQLite, fallback CoinGecko optionnel
- 🧮 **PNL calculé** — coût moyen pondéré, soldes reconstruits par date, PNL journalier
- 🛡️ **Filtre anti-spam** — détection automatique des tokens de scam/airdrop
- ⚙️ **Paramètres** — langue (FR/EN), devise (USD/EUR), changement de mot de passe, clés API utilisateur
- 🔑 **Clés API par utilisateur** — catalogue de 7 services (CoinGecko, OpenSea, Etherscan, DefiLlama, Alchemy, Moralis, CoinMarketCap) avec validation best-effort et interface en cartes avec logos
- 📦 **Vérification de version** — compare avec le dernier tag GitHub
- ⚡ **Cache prix** — table `price_history`, 2ᵉ rebuild ~0 appel réseau
- 🔔 **Alertes** — prix, valeur portefeuille, mouvements (> X% en 24h), **health factor / risque de liquidation** avec notifications in-app + canaux externes (webhook, Telegram, e-mail)
- 📬 **Digest** — résumé quotidien ou hebdo du portefeuille
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
│       (+ defi_service.py — normaliseur positions DeFi Moralis, module pur)
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
| Prix plancher NFT | OpenSea / Moralis / Reservoir | ✅ / ❌ (clé) |

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
- **Double authentification (2FA TOTP)** optionnelle — activation dans Paramètres
- **Anti-brute-force** — rate-limiting des tentatives de login
- **Changement de mot de passe** dans Paramètres
- **Aucune clé privée** — uniquement des adresses publiques
- Clés API utilisateur : stockées chiffrées, jamais renvoyées en clair (masquées `sk-...abc`)
- Données 100 % locales (SQLite)

---

## 🗺️ Roadmap

### Phase 1 — Fonctionnalites
- [x] 2026.07.3 — Analytics (repartition & performance)
- [x] 2026.07.4 — Export CSV/PDF (holdings, tx, PnL fiscal)
- [x] 2026.07.5 — Transactions : approbations, interactions, gaz
- [x] 2026.07.6 — Moteur d'alertes + digest
- [x] 2026.07.7 — Alertes health-factor / liquidation
- [x] 2026.07.8 — Valorisation NFT (prix planchers)
- [x] 2026.07.9 — Pricing multi-sources + test des cles
- [x] 2026.07.10 — NFT : liens source + fiabilite des floors
- [x] 2026.07.11 — PWA, theme, recherche, watchlist
- [x] 2026.07.12 — Consolidation SQLite (ecritures serialisees)
- [x] 2026.07.13 — Self-update via updater cote hote (bouton Mettre a jour fonctionnel)
- [x] 2026.07.14 — Updater self-update fiabilise
- [x] 2026.07.15 — Declencheur self-update fiabilise
- [x] 2026.07.16 — Choix maj auto/manuelle
- [x] 2026.07.17 — Sauvegardes auto + sante + tests/CI
- [x] 2026.07.18 — Updater self-update en HTTPS (fetch fiable, plus de cle SSH)
- [x] 2026.07.19 — Barre du haut retiree
- [x] 2026.07.20 — Durcissement auth & comptes

### Phase 2 — Multi-chaines non-EVM & airdrops
- [x] 2026.07.21 — Abstraction multi-provider (fondation non-EVM)
- [x] 2026.07.22 — Bitcoin (BTC)
- [x] 2026.07.23 — Solana (SOL + tokens SPL via RPC public)
- [x] 2026.07.24 — Support Cosmos/ATOM (staking natif)
- [ ] Airdrops a claim

## 📋 Changelog

### 2026.07.24 — Support Cosmos/ATOM (solde + staking delegue + rewards via LCD public)

- **CosmosProvider** : nouveau provider multi-chaîne pour les adresses bech32 Cosmos (cosmos1…, osmo1…, celestia1…, juno1…, stars1…, akash1…, inj1…, kujira1…, stride1…). Détection conservative — rejette EVM (`0x...`), BTC bech32 (`bc1...`), Solana. Module `src/services/providers/cosmos.py` (+ fichier test `tests/test_cosmos_provider.py`, 77 assertions).
- **LCD public gratuit** : endpoints Cosmos REST (Polkachu) — solde disponible (`/cosmos/bank/v1beta1/balances`), délégations staking (`/cosmos/staking/v1beta1/delegations`), récompenses en attente (`/cosmos/distribution/v1beta1/delegators/{addr}/rewards`). 3 appels en parallèle, timeout 20s, défensif — chaque appel indépendant, jamais de 500.
- **Prix ATOM/OSMO** : DefiLlama (coins.llama.fi) — gratuit, sans clé. Conversion uatom/uosmo → ATOM/OSMO (÷1e6). Denoms inconnus → `price_unknown`, jamais de prix inventé.
- **Portfolio standard** : token natif disponible + token staké (category `"staked"`) + token récompenses (category `"rewards"`). `staked_usd` agrégé, `chains`, `total_usd`, `defi_breakdown`. Transactions : placeholder (retourne vide, pas de crash).
- **Explorer** : liens Mintscan (`mintscan.io/{chain}/address/` et `/tx/`), mappé selon le HRP de l'adresse.
- **Routage automatique** : `provider_for()` reconnaît les adresses Cosmos → `/api/portfolio` et `/api/wallets` (ajout) fonctionnent sans code spécifique. Vue agrégée ALL somme EVM + BTC + Solana + Cosmos. Mise à jour du test `test_providers.py` (provider_for Cosmos → CosmosProvider au lieu de None).
- **Staking natif** : le staking Cosmos (délégué + récompenses) apparaît dans la vue portfolio avec `category: "staked"` et `category: "rewards"` — prêt pour l'affichage DeFi/positions. NFT : vide propre.

### 2026.07.23 — Support Solana (SOL + tokens SPL via RPC public)

- **SolanaProvider** : nouveau provider multi-chaîne pour les adresses Solana (clés publiques base58 32 octets). Détection conservative — rejette EVM (`0x...`), BTC bech32 (`bc1...`), Cosmos-like. Décodage base58 minimal intégré (stdlib seulement, pas de dépendance externe). Module `src/services/providers/solana.py`.
- **RPC public gratuit** : balance native SOL (`getBalance` en lamports) + comptes SPL (`getTokenAccountsByOwner` via le program ID Token). Timeout 20s, défensif — jamais de 500, best-effort.
- **Prix SOL/USD** : DefiLlama (coins.llama.fi) — gratuit, sans clé. Prix SPL : batch DefiLlama `solana:{mint}` par paquets de 50 — best-effort, sans prix → `price_unknown`.
- **Portfolio standard** : même forme que EVM/BTC — token SOL + tokens SPL (symboles connus pour ~40 tokens majeurs, sinon mint tronqué). `chains`, `total_usd`, `defi_breakdown`, `active_count`. Transactions : placeholder (retourne vide, pas de crash).
- **Explorer** : liens Solscan (`solscan.io/account/` et `/tx/`).
- **Routage automatique** : `provider_for()` reconnaît les adresses Solana → `/api/portfolio` et `/api/wallets` (ajout) fonctionnent sans code spécifique. Vue agrégée ALL somme EVM + BTC + Solana.
- **NFT / DeFi** : renvoie vide proprement pour Solana (pas de crash).
- **Tests** : `tests/test_solana_provider.py` (66 assertions) — base58, detect, provider_for, metadata, portfolio shape, lamports, SPL lookup, registry, transactions placeholder. Tests EVM/BTC mis à jour (provider_for reconnaît désormais toutes les chaînes).

### 2026.07.22 — Support Bitcoin (BTC via mempool.space)

- **BitcoinProvider** : nouveau provider pour adresses Bitcoin (bech32 `bc1...`, legacy `1...`, P2SH `3...`). Solde via mempool.space (gratuit, sans clé), prix BTC/USD via DefiLlama, transactions basiques. Module `src/services/providers/bitcoin.py`.
- **Portfolio standard** : même forme que EVM — token BTC unique, `chains`, `total_usd`, `errors`. Transactions events format standard (send/receive with USD values).
- **Routage** : wallets acceptent et routent les adresses BTC automatiquement via `provider_for()`.

### 2026.07.21 — Abstraction multi-provider (ChainProvider) — fondation pour Bitcoin/Solana/Cosmos, zero changement EVM

- **Interface `ChainProvider`** : classe abstraite définissant le contrat commun pour tous les futurs providers de chaîne (`detect()`, `get_portfolio()`, `get_transactions()`, `explorer_url()`, `chain_type`, `native_symbol`). Module `src/services/providers/base.py`.
- **Registre** : liste ordonnée `PROVIDERS` + fonction `provider_for(address)` qui retourne le premier provider dont `detect()` est vrai, ou `None`. Extensible : ajouter un provider = implémenter l'interface + l'enregistrer.
- **`EvmProvider`** : wrapper fin qui délègue à `_compute_portfolio` et à la logique de transactions existante SANS RÉÉCRIRE AUCUNE logique métier. Détection `0x...` (42 caractères hex). Module `src/services/providers/evm.py`.
- **Routage non-cassant** : les endpoints `/api/portfolio` et `/api/transactions` vérifient `provider_for(address)` avant d'exécuter la logique EVM. Adresse EVM → chemin inchangé (zero regression). Adresse non-EVM (`bc1...`, Solana, Cosmos) → réponse propre `{supported: false, message: "Chaine non prise en charge (a venir)"}` sans erreur 400.
- **Helper `get_portfolio_via_provider(address)`** : point d'entrée canonique pour les futures intégrations multi-chaînes.
- **Tests** : `tests/test_providers.py` (34 assertions) — détection, registre, extensibilité, délégation, contrat de réponse non-supportée.

### 2026.07.20 — Durcissement auth : 2FA TOTP optionnelle, anti-brute-force, changement de mot de passe, isolation multi-utilisateurs

- **2FA TOTP optionnelle** : authentification à deux facteurs (TOTP) via app mobile (Google Authenticator, Authy...). Désactivée par défaut — rétro-compatibilité totale : un utilisateur sans 2FA se connecte comme avant. Activation en 3 étapes dans ⚙️ Paramètres → carte « Sécurité » : scan du QR code (ou saisie manuelle du secret), vérification du code, activation. Désactivation par code TOTP ou mot de passe. QR code généré côté serveur (`pyotp` + `qrcode`) — fonctionne hors ligne. Login : si 2FA activée, le backend renvoie `twofa_required:true` → le frontend affiche un champ code TOTP → vérification en seconde étape via `/api/auth/login/2fa`. Secrets stockés en base, jamais renvoyés après activation.
- **Anti-brute-force** : limiteur de tentatives de login échouées en mémoire (par username+IP). Après 5 échecs consécutifs dans une fenêtre de 5 min, backoff de 60 s (doublant à chaque palier de 5 échecs supplémentaires). Message clair « Trop de tentatives, réessayez dans X s ». Compteur réinitialisé au succès. Aucune persistance — l'application locale n'a pas besoin de Redis.
- **Changement de mot de passe** : endpoint `PUT /api/auth/password` révisé — vérifie `old_password` (bcrypt), impose longueur minimale (4 caractères), stocke le nouveau hash bcrypt. UI dans ⚙️ Paramètres → carte « Mot de passe » avec confirmation.
- **Isolation multi-utilisateurs auditee** : vérification exhaustive de chaque endpoint de données (wallets, transactions, snapshots, PNL, alerts, notifications, API keys, token prefs, backups, analytics, exports, DeFi, NFTs) — tous filtrent par `user_id`. Correction : `PUT /api/wallets/{id}` vérifie désormais `cur.rowcount` et renvoie 404 si le wallet n'appartient pas à l'utilisateur. Tests d'isolation : `tests/test_isolation.py` — 10 assertions (création de 2 utilisateurs, vérification étanche entre leurs wallets, alerts, API keys, 2FA, transactions).

- **Topbar supprimee** : la barre du haut contenant le champ de recherche (`#globalSearch`) et le selecteur rapide de wallet (`#quickWallet`) est retiree. Le bandeau d'onglets wallets (`#walletTabs`) est egalement supprime.
- **Vue agregee permanente** : `activeWallet` est force a `"ALL"` en permanence — l'app affiche toujours l'agregat de tous les wallets.
- **JS neutralise** : les fonctions orphelines (`applyGlobalSearch`, `populateQuickWallet`, `changeWallet`, `renderWalletTabs`) sont supprimees. Aucune erreur JS au chargement (`Cannot read properties of null`).
- **CSS nettoye** : les regles `.topbar`, `.wallets-bar`, `.wallet-tab` retirees (economie ~20 lignes CSS).
- **Smoke test** : `tests/smoke-topbar-removal.js` — verifie que les 4 fonctions sont absentes et que les fonctions coeur (`selectWallet`, `switchPage`, `esc`, `t`) fonctionnent sans erreur.

### 2026.07.18 — Updater self-update en HTTPS (depot public, plus de probleme de cle)

- **Fetch en HTTPS** : l'updater hôte (`host-updater.sh`) utilise désormais `git fetch` via HTTPS (`https://github.com/LostInTheBugs/Crypto-Wallet-Tracker.git`) au lieu de SSH (`git@github.com`). Le dépôt étant public, aucune clé ni authentification n'est nécessaire.
- **Suppression de la logique SSH** : toute la détection/copie de clés SSH (`find_and_copy_key`, `GIT_SSH_COMMAND`, `DEPLOY_KEY`) est retirée. Plus de `Permission denied (publickey)` ni de prompts interactifs — `credential.helper` est explicitement désactivé (`git -c credential.helper=`).
- **Bootstrap** : le remote du dépôt sur la VM de production est basculé en HTTPS. L'updater corrigé est réinstallé, le service redémarré.

### 2026.07.17 — Sauvegardes automatiques de la base, page santé/statut, tests + CI

- **Sauvegardes automatiques** : tâche de fond asyncio sauvegarde `/data/wallets.db` vers `/data/backups/wallets-YYYYMMDD-HHMMSS.db` tous les jours (configurable, `BACKUP_INTERVAL_HOURS`). Sauvegarde cohérente via l'API `sqlite3.backup()` (snapshot WAL), coordonnée avec le verrou d'écriture global. Rétention des N derniers backups (défaut 7, `BACKUP_RETENTION`), les plus anciens sont supprimés. Résiliente : une erreur de backup ne casse pas l'app.
- **Endpoints backup** : `POST /api/backups/run` déclenche une sauvegarde immédiate, `GET /api/backups` liste les backups (nom, taille, date). Authentification requise.
- **Santé / Statut** : `GET /api/health` (public) renvoie `{status, version, db_ok, uptime_s, counts, last_backup}`. Tolérant : `db_ok=false` au lieu de 500. N'expose aucun secret.
- **UI** : carte « 🫀 État / Santé » dans les Paramètres affichant version, état DB, uptime, dernière sauvegarde. Bouton « Sauvegarder maintenant » + liste des backups avec tailles.
- **Tests étendus** : 13 nouveaux tests unitaires purs dans `tests/test_core.py` pour `token_tid`, `classify_token`, et le classifieur DeFi `classify_token_type` (20 tests au total). Exécutable sans réseau ni base réelle.
- **CI GitHub Actions** : workflow `.github/workflows/ci.yml` sur push/PR : `python -m py_compile` sur `src/`, tests unitaires, `node --check` sur le JS inline de `public/index.html`.
- **Roadmap** : lignes corrigées (2026.07.17 cochée, 2026.07.18 pour auth).

### 2026.07.16 — Mise à jour automatique ou manuelle (au choix, dans Paramètres)

- **Choix du mode de mise à jour** : nouveau paramètre dans ⚙️ Paramètres → carte Version — radio bouton `Manuelle / Automatique`. Persisté dans `/data/deploy/config.json` (volume partagé, lisible par l'updater hôte).
- **Endpoints API** : `GET /api/settings/update-mode` (lecture), `PUT /api/settings/update-mode {mode:"auto"|"manual"}` (écriture, auth requise).
- **Mode automatique** : l'updater hôte (`host-updater.sh`) vérifie périodiquement (~3 min) si `origin/main` est en avance via `git fetch` + comparaison de hash. Si une nouvelle version est détectée, le cycle complet de déploiement (reset --hard + rebuild Docker) est déclenché automatiquement, sans clic.
- **Mode manuel** (défaut) : comportement inchangé — le bouton « Mettre à jour » apparaît quand une nouvelle version est disponible, et le déploiement est déclenché par clic (écriture de `request.json`).
- **UI** : en mode auto, le bouton « Mettre à jour » est masqué et remplacé par un message « ⚙️ Mises à jour automatiques activées — l'application se met à jour seule ». Le toggle met à jour le fichier de config et rafraîchit l'affichage immédiatement.
- **i18n** : nouvelles clés `updModeLabel`, `updManual`, `updAuto`, `updAutoMsg` (FR + EN).
- **Robustesse** : l'updater lit le fichier de config à chaque itération (pas de redémarrage nécessaire). Échec de fetch → pas de blocage. Mode auto → status cohérent (`state:done/failed`, version depuis `verCurrent`). Le mécanisme existant de requêtes manuelles (`request.json`) est préservé et prioritaire.

### 2026.07.15 — Declencheur de self-update fiabilise (polling, suppression de la demande, version correcte)

- **Polling robuste** : abandon du systemd.path (PathExists) fragile — remplacement par un service de polling long-running (boucle toutes les ~12 s). Plus de blocage `unit-start-limit-hit` quand `request.json` n'etait pas supprime.
- **Suppression systematique** : `request.json` est toujours supprime apres chaque cycle (succes ou echec) — le prochain clic sur « Mettre a jour » redeclenche proprement.
- **Version reelle** : la version rapportee dans `status.json` est lue depuis `public/index.html` (`id="verCurrent"`) apres le reset, plus depuis un tag git obsolete.
- **Verification** : 3 cycles consecutifs de mise a jour idempotents verifies sur la VM de production.

### 2026.07.14 — Updater fiabilise (git reset --hard, plus de blocage sur divergence locale)

- **Updater robuste** : remplacement de `git pull origin main` par `git fetch origin main --quiet && git reset --hard origin/main && git clean -fd`. L'updater amène désormais toujours /opt/crypto-wallet-tracker exactement à origin/main, quelle que soit la divergence locale — plus jamais de « Your local changes would be overwritten by merge — Aborting ».
- **Vérification** : 2 cycles complets de mise à jour idempotents vérifiés sur la VM de production.

### 2026.07.13 — Self-update via updater côté hôte (bouton Mettre à jour fonctionnel)

- **Updater côté hôte** : le conteneur ne gère plus son propre déploiement (il n'a ni git ni docker). Un clic sur « Mettre à jour » dans ⚙️ Paramètres écrit un fichier de demande sur un volume Docker partagé (`/data/deploy/request.json`). Un service systemd sur l'hôte (`crypto-update.path` + `crypto-update.service`) surveille cette demande, exécute `git pull origin main` puis `docker compose up -d --build`, et écrit l'état dans `/data/deploy/status.json`. Le frontend poll l'état toutes les 3 secondes et recharge la page au succès.
- **Sécurité** : le conteneur n'a jamais accès à la socket Docker, à git, ni au host. Il se contente d'écrire un fichier sur un volume partagé. L'updater tourne en `root` sur l'hôte avec les permissions nécessaires.
- **Correction UI** : le frontend n'affiche plus jamais « undefined » en cas d'erreur — fallback sur `d.msg || d.detail || "Demande échouée"`. Ajout des clés i18n FR/EN pour tous les états du déploiement (demande, déploiement, terminé, échec, timeout).
- Fichiers ajoutés : `deploy/host-updater.sh`, `deploy/crypto-update.path`, `deploy/crypto-update.service`.

### 2026.07.12 — Consolidation SQLite : ecritures serialisees (fin des "database is locked")

- **Verrou d'ecriture global** : un `asyncio.Lock` partagé (`src/services/db.py`) sérialise TOUTES les écritures SQLite (INSERT/UPDATE/DELETE/CREATE/REPLACE + commit). Les lectures ne prennent PAS le verrou (WAL). Plus aucun « database is locked » sous concurrence (workers de fond : rebuild historique, enrichissement prix, evaluateur d'alertes + requetes utilisateur qui écrivent).
- **Defense en profondeur** : WAL + busy_timeout=10000 conserves. Sous-processus (rebuild_worker.py, enrich_worker.py) utilisent sqlite3 synchrone avec busy_timeout.
- **Test de concurrence** : `tests/test_write_lock.py` — 20 workers × 25 ecritures concurrentes = 500 INSERT+COMMIT → 0 erreur « database is locked », toutes les lignes validees.

### 2026.07.11 — PWA installable, theme clair/sombre, recherche globale, watchlist & groupes

- **PWA installable** : manifest.json, service worker (cache app shell, network-first pour API), icônes 192×192 et 512×512. L'app est installable sur mobile/desktop avec affichage hors-ligne basique.
- **Thème clair / sombre** : variables CSS pour les deux thèmes, bouton bascule 🌙/☀️ dans la sidebar, choix persisté dans localStorage. Vérifié pour la lisibilité (contraste des textes, badges, graphiques Chart.js).
- **Recherche / filtre global** : champ de recherche dans la topbar qui filtre instantanément (côté client) les tokens (par symbole/nom/chaîne/wallet) et les transactions (par symbole/nom/hash/adresse/chaîne). Insensible à la casse.
- **Watchlist (lecture seule)** : colonne `watch_only` sur les wallets. Une adresse en surveillance est affichée et consultable mais **exclue des totaux** (net worth, dashboard, analytics, snapshots). Badge « 👁 surveillance » et bouton pour basculer.
- **Groupes de wallets** : champ `group_label` optionnel sur les wallets. Affichage groupé dans la liste des wallets (ligne de séparation par groupe). Sans impact sur les totaux.

### 2026.07.10 — NFT : liens source directs + fiabilite des floors (liquidite)

- **Liens source directs** : chaque NFT affiche désormais un lien direct vers sa source marketplace (`market_url`), en plus du lien explorer Blockscout (`explorer_url`). Si le floor vient d'OpenSea, le lien pointe vers la page de l'asset ou de la collection. Dans `/api/nfts`, chaque item a `market_url` (OpenSea) ET `explorer_url` (Blockscout). La collection de valorisation a aussi les deux liens. **Plus jamais de « source OpenSea » alors que l'item est introuvable sur OpenSea** — le lien pointe vers la vraie source.
- **Fiabilité des floors (liquidité)** : chaque collection valorisée porte désormais `floor_reliable` (bool) + `floor_confidence` ("high"/"low"/"none"), déterminés par des signaux de liquidité récupérés des APIs sources : volume 24h, nombre de listings actifs, meilleure offre (top bid), nombre de propriétaires. Règles conservatrices : un floor sans volume, sans listings et sans offre = non fiable (confidence "none"), exclu du total.
- **Totaux séparés** : `nft_total_value_usd` = somme des floors **fiables** uniquement. `nft_indicative_value_usd` = somme des floors non fiables. Le net worth Tokens+NFTs sur le dashboard n'utilise QUE les floors fiables — **plus jamais de net worth gonflé par des collections zombies**.
- **UI enrichie** : badge de confiance par collection (✓ vert si fiable, « ⚠ indicatif » orange si non fiable, gris si low). Boutons directs « OS » (OpenSea) et « 🔗 » (Explorer) sur chaque carte NFT et chaque ligne de valorisation. La valeur indicative est affichée séparément en orange.
- **APIs sources enrichies** : OpenSea remonte désormais listings_count, best_offer, volume_24h, num_owners. Reservoir remonte volume_24h, listings_count, best_offer_eth. Moralis inchangé (endpoint floor simple).

### 2026.07.9 — Pricing multi-sources (CoinGecko) + test des clés API

- **CoinGecko comme source de prix prioritaire** : quand une clé API CoinGecko est configurée, les prix courants des tokens sont enrichis via l'API CoinGecko (`/simple/token_price` par contrat, `/simple/price` pour les coins natifs). **Conservateur** : un prix CoinGecko n'écrase un prix existant (Blockscout/DefiLlama) que s'il est strictement > 0. Sans clé, le comportement est inchangé. La couverture s'améliore notamment pour les memecoins et tokens exotiques.
- **Champ `price_source` par token** : chaque token dans la réponse portfolio porte désormais `price_source` (`"blockscout"`, `"coingecko"`, ou `"defillama"`) indiquant l'origine de son prix courant.
- **Bouton « Tester » par clé API** : endpoint `POST /api/settings/keys/{provider}/test` valide la clé stockée (ou fournie dans le body) via un appel léger au provider. Retourne `{valid: bool, message}`. Fonctionne pour CoinGecko, OpenSea, Etherscan, DefiLlama, Alchemy, Moralis, CoinMarketCap.
- **Métadonnée « Débloque » par provider** : chaque entrée du catalogue `GET /api/settings/keys` inclut désormais `unlocks`, une courte phrase décrivant ce que la clé active concrètement. Affiché dans la page Réglages > Clés API externes.

### 2026.07.8 — Valorisation NFT (prix planchers) + net worth Tokens+NFTs

- **Valorisation des NFT** : nouvel endpoint `GET /api/nfts/valuation?address=` qui retourne les prix planchers (floor prices) par collection détenue, avec un total `nft_total_value_usd`. Sources, dans l'ordre : OpenSea (clé API), Moralis (clé API), Reservoir (gratuit, best-effort). Sans aucune clé, `floor_source: "none"` + message invitant à configurer une clé. **Jamais de 500** — toute erreur API est isolée et dégrade gracieusement.
- **Cache serveur 1h** par (user, address) — une seule requête par collection, pas par item individuel. Conversion ETH→USD via prix ETH (cache portfolio ou DefiLlama).
- **Dashboard — net worth décomposé** : nouvelle ligne « Tokens : X + NFTs : Y = Total : Z » entre les cartes stats et les cartes PNL. **La valeur NFT n'est PAS injectée** dans le total des tokens, ni dans le PNL par token, ni dans daily_history — c'est une ligne d'affichage additionnelle qui ne pollue pas l'historique.
- **Page NFTs enrichie** : carte de synthèse (valeur totale, source, nombre de collections valorisées), tableau des floors par collection (nom, floor ETH/USD, items, valeur totale, source), et badge d'avertissement « Ajoute une clé OpenSea/Moralis » avec lien vers Réglages quand aucune valorisation n'est disponible.
- **Clés API** : le helper `_get_user_moralis_key` existant + nouveau `_get_user_opensea_key`. Les caches de valorisation sont invalidés à l'ajout/suppression d'une clé OpenSea ou Moralis.
- **i18n** FR/EN complet, `esc()` partout, pas de `\n` littéral en JS, défensif total.

### 2026.07.7 — Alertes health-factor / risque de liquidation

- **Nouveau type d'alerte « health »** : surveille le health factor des positions de lending (lending/borrowing) via l'API Moralis. Déclenche une notification quand le health factor d'au moins une position passe sous le seuil configuré (défaut 1.2).
- **Intégration Moralis** : réutilise la source de données DeFi existante (`/api/defi/positions`). Sans clé Moralis, l'alerte est marquée « Nécessite une clé Moralis » — jamais de faux positifs ni de 500.
- **Message d'alerte** inclut le protocole, la chaîne, le health factor courant, le seuil et les montants fournis/empruntés.
- **UI** : nouveau type « Health / Liquidation » dans le formulaire de création d'alerte, champ seuil (défaut 1.2), scope protocole (tous ou spécifique). Badge « ⚠️ Nécessite une clé Moralis » sur les alertes health sans clé configurée.
- **Correctif** : `POST /api/alerts` renvoie désormais le vrai `id` inséré (via `cursor.lastrowid` au lieu de `connection.last_insert_rowid`).
- **i18n** FR/EN, `esc()` partout, défensif total (pas de clé → état lisible, pas de plantage).

### 2026.07.6 — Moteur d'alertes (prix, portefeuille, mouvements) + notifications + digest

- **Moteur d'alertes** : création/suppression/activation d'alertes de 3 types — **prix** (token au-dessus/en-dessous d'un seuil), **portefeuille** (valeur totale au-dessus/en-dessous d'un seuil), **mouvement** (variation > X% sur 24h). Évaluateur asynchrone toutes les 10 minutes (cooldown par alerte, jamais de re-déclenchement en rafale).
- **Centre de notifications in-app** : une notification est créée à chaque alerte déclenchée (titre + description). Interface dédiée avec marquage « lu », badge de compteur non-lu.
- **Canaux externes** : **Webhook** (POST JSON), **Telegram** (API Bot), **E-mail** (SMTP, optionnel). Configuration par canal (URL, token, credentials), secrets masqués en GET, test d'envoi (`POST /api/alerts/test-channel`), robustes — un canal qui échoue ne bloque pas les autres.
- **Digest** : résumé quotidien ou hebdomadaire du portefeuille (valeur, variations 24h/7j) envoyé via le canal choisi.
- **Page 🔔 Alertes** dans le menu latéral — 4 sections : mes alertes (création + liste), centre de notifications, canaux de notification, digest. i18n FR/EN, `esc()` partout, états vides propres.
- **APIs** : `GET/POST/PUT/DELETE /api/alerts`, `GET /api/notifications`, `POST /api/notifications/read`, `GET/PUT /api/settings/notif-channels`, `GET/PUT /api/settings/digest`, `POST /api/alerts/test-channel`, `GET /api/notifications/count`.
- Base de données : 4 nouvelles tables `alerts`, `notifications`, `notif_channels`, `digest_prefs` (migrations idempotentes).

### 2026.07.5 — Transactions enrichies (approve, contract interactions, gas analytics, tags/notes)

- **Collecte étendue** : en plus des token-transfers, capture maintenant toutes les transactions d'une adresse via l'endpoint Blockscout `/addresses/{address}/transactions`. Détecte les transactions `approve` (approbation de dépense), `contract` (interaction de contrat sans transfert de token), et `native` (envoi/réception de coin native).
- **Pas de doublon** : un tx_hash déjà présent (transfert de token) est conservé enrichi (méthode), jamais dupliqué.
- **API `/api/transactions`** : nouveaux types `approve|contract|native` dans le filtre `type=`, compteurs étendus, tags utilisateur attachés à chaque événement.
- **Gaz analytics** : `GET /api/gas/analytics?address=&range=` → total gaz dépensé, série temporelle journalière, répartition par chaîne. Carte gaz sur la page Transactions avec mini-graphe Chart.js.
- **Tags/notes** : table `user_tx_tags`, endpoints `POST /api/transactions/tag` (upsert) et `GET /api/transactions/tags`. Interface inline : clic sur l'icône tag → éditeur (catégorie + note), sauvegarde immédiate. Catégories suggérées : revenu, trade, transfert, frais, autre.
- **UI** : badges colorés distincts (✅ Approve orange, 📄 Contrat bleu, 📥/📤 Natif vert/rouge). Filtre de type enrichi. i18n FR/EN exhaustif.

### 2026.07.4 — Export CSV/PDF (holdings, transactions, rapport PnL, synthèse)

- **Section ⚙️ Réglages → 📤 Export / Sauvegarde** : 4 boutons de téléchargement (Holdings CSV, Transactions CSV, Rapport PnL CSV, Synthèse PDF), état « Génération… », gestion d'erreurs, respect du wallet actif (adresse ou `ALL`), i18n FR/EN.
- **Endpoints protégés `GET /api/export/holdings.csv|transactions.csv|pnl.csv|summary.pdf?address=0x…|ALL`** avec en-têtes de téléchargement (`Content-Disposition: attachment`). Tokens **actifs** uniquement, wallets existants uniquement (défense anti-données-orphelines), agrégation par symbole+chaîne en mode `ALL`.
- **holdings.csv** : token_name, symbol, chain, balance, usd_price, usd_value, category, cost_basis, pnl. **transactions.csv** : événements Envoyé/Reçu/Swap (logique de détection des swaps, jambes regroupées par tx), montants signés, gas. **pnl.csv** (rapport fiscal best-effort) : quantité, coût moyen unitaire, coût total, valeur actuelle, PnL latent — même logique de coût que le PNL par token du dashboard ; coût inconnu → cellules vides (jamais de faux « acheté gratuit »).
- **summary.pdf** : valeur totale, PnL total, répartition par chaîne et par catégorie (logique /api/analytics), top 15 holdings, date de génération — générateur PDF minimaliste interne (PDF 1.4, **zéro dépendance ajoutée**).
- Robustesse : CSV RFC 4180 (guillemets/virgules/retours ligne échappés), UTF-8, décimales point ; donnée manquante → cellule vide ; toute erreur → export vide avec en-têtes, **jamais de 500**.
- Tests : `python3 tests/test_export_service.py` (CSV quoting, agrégation, PnL, structure PDF/xref) + `node tests/smoke_export_2026.07.4.js` (smoke runtime du rendu de la section Export).

### 2026.07.3 — Page Analytics (répartition & performance)

- **Nouvelle page 📊 Analytics** (menu latéral) : vue synthétique de la répartition et de la performance du portefeuille — première release de la roadmap.
- **Endpoint `GET /api/analytics?address=…&range=24h|7d|30d`** (address = wallet ou `ALL`) : allocation par chaîne / catégorie (wallet, lending, staked, LP, vault, synthetic) / actif (top 12 + « Autres »), variations de la valeur totale sur 24h/7j/30j (agrégat `daily_history`), meilleurs/pires performers par variation de **prix** (neutralise les apports/retraits, spam et poussière ignorés), benchmark best-effort Portefeuille vs BTC/ETH (DefiLlama). Tokens **actifs** uniquement. Défensif : historique insuffisant → `null`/« — », jamais de 500. Cache serveur 300 s par (user, address, range).
- **UI** : 3 cartes de variation (vert/rouge, « — » si indispo), 3 donuts Chart.js (thème sombre, couleurs chaînes cohérentes avec le dashboard), tableau Top gagnants / Top perdants, sélecteur de période 24h/7j/30j, i18n FR/EN, états vides propres, destruction propre des instances Chart.js au rechargement.
- Tests : `python3 tests/test_analytics_service.py` (48 assertions) + `node tests/smoke_analytics_2026.07.3.js` (smoke runtime du rendu). Pages Stats/Dashboard/portfolio inchangées (rétro-compat totale).

### 2026.07.2 — En-tête Cache-Control no-cache sur le SPA (fin des versions périmées en cache navigateur)

- **Cache-Control: no-cache, must-revalidate** sur la route racine — le navigateur conserve le fichier en cache mais doit revalider à chaque visite via ETag/Last-Modified. 304 si inchangé, nouvelle version dès le déploiement. Adieu les versions périmées servies depuis le cache du navigateur.

### 2026.07.1 — Passage au versioning calendaire (AAAA.MM.N)

**Ce qui change :**
- Le projet passe du **semver** (vX.Y.Z) au **calendar versioning** au format `AAAA.MM.N` (année.mois.numéro).
- `N` repart à 1 chaque mois (prochaine release de juillet = `2026.07.2`, août = `2026.08.1`, etc.).
- Le nouveau schéma démarre à `2026.07.1` (les anciens tags de développement ont été retirés pour garder un dépôt propre).
- **Backend** : `GET /api/version/latest` reconnaît les tags CalVer et les compare numériquement par (année, mois, N). Un tag CalVer est toujours considéré plus récent qu'un tag semver.
- **Frontend** : `checkVersion()` compare désormais correctement les versions CalVer (année → mois → N). Tout format inattendu déclenche un fallback égalité = à jour.
- L'affichage n'utilise plus de préfixe `v` dans les messages de version.

> Historique complet des anciennes versions v1/v2 : voir l'historique des commits Git.

## 📝 Licence

MIT
