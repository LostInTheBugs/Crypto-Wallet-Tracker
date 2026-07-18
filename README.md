# Crypto Wallet Tracker — v2.12.7

**Inventaire local de wallets crypto** — multi-wallets, multi-chaînes EVM, 100 % gratuit (API Blockscout).

Dashboard agrégé, graphiques d'évolution, historique des prix via DefiLlama, PNL par token, transactions paginées, comptes utilisateurs. Le tout en Docker, une seule commande.

---

## ✨ Fonctionnalités

- 🔗 **22 chaînes EVM** — Ethereum, Base, Optimism, Arbitrum, Polygon, Gnosis, zkSync, Celo, Scroll, Soneium, Ink, Mode, Unichain, Lisk, Linea, Etherlink, Metis, Manta, BOB, Zora, World Chain, HyperEVM
- 🪙 **Solde natif** — ETH/POL/xDAI/CELO/XTZ/METIS récupéré en parallèle des tokens (appel API natif)
- 💰 **Valorisation USD/€** — temps réel via Blockscout, conversion EUR (Frankfurter)
- 🦙 **Fallback prix DefiLlama** — si Blockscout ne donne pas de prix, appel batch à l'API gratuite `coins.llama.fi/prices/current`
- 🔒 **Détection DeFi best-effort** — catégorisation fine (lending, LP, staked, vault, synthetic) via heuristiques sur les symboles, aucun service tiers, 100 % gratuit. Section DeFi dédiée avec badges colorés et sous-totaux par catégorie
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

### v2.12.7 — Catalogue des clés API externes (inspiré de Rotki)

- **Catalogue enrichi** — `GET /api/settings/keys` renvoie désormais un catalogue complet de 7 services externes (CoinGecko, OpenSea, Etherscan, DefiLlama, Alchemy, Moralis, CoinMarketCap) avec ID, nom, catégorie, description, lien d'obtention, statut et clé masquée. Stockage clé-valeur dans `user_api_keys`, PUT/DELETE génériques fonctionnent pour tout fournisseur du catalogue.
- **Validation best-effort** — CoinGecko et Alchemy conservent leur validation réelle ; les autres fournisseurs sont stockés sans blocage (pass-through). Plus de rejet « Provider inconnu ».
- **Interface en grille de cartes** — la section « Clés API externes » dans Paramètres présente chaque service sous forme de carte avec logo inline SVG (jamais d'image cassée), nom, badge de catégorie, description, statut (vert ✓ / gris), champ de saisie masqué, boutons Enregistrer / Supprimer, et lien « Obtenir une clé ». Logos inline 100 % intégrés (Geo, OpenSea, Etherscan, DefiLlama, Alchemy, Moralis, CMC) avec palettes de couleurs distinctives.
- **i18n** — tous les libellés ajoutés en FR et EN (apiKeysTitle, apiKeyDesc, apiKeyPlaceholder, etc.).
- **Aucune régression** — rétro-compatibilité des endpoints, pas de variable `t` dans les boucles, py_compile ok.

### v2.12.6 — PNL par token visible dès la fin du rebuild (invalidation du cache)

- **Correctif cache périmé** — après l'ajout (ou le ré-ajout) d'un wallet, le premier `/api/portfolio` était calculé et mis en cache AVANT la fin de la reconstruction d'historique (`daily_history` encore vide) : tous les PNL par token restaient à « — » et ce résultat sans PNL était servi depuis le cache pendant jusqu'à 1h.
- **Invalidation ciblée du cache** — le cache portfolio du wallet est maintenant purgé dès que l'import (`_fetch_then_rebuild`) termine la reconstruction ; le rebuild global (`_run_history_rebuild`, déclenché par un toggle de token, un ajout manuel, etc.) purge le cache de tous les wallets de l'utilisateur à la fin du sous-processus. Comparaison insensible à la casse dans les deux cas.
- Résultat : le prochain appel `/api/portfolio` (même sans `force=true`) recalcule avec le cost basis désormais disponible — le PNL par token apparaît automatiquement quelques secondes après la fin du rebuild, sans rafraîchissement forcé.
- Aucun changement du TTL (1h), de la logique de calcul du PNL ni de la forme des réponses.

### v2.12.5 — Suppression de wallet fiabilisée (plus aucune donnée résiduelle)

- **Cascade insensible à la casse** — `DELETE /api/wallets/{id}` supprime désormais `transactions` et `daily_history` avec `lower(wallet_address)=lower(address)`. L'écart de casse (adresses checksum écrites par le worker de reconstruction vs adresse stockée dans `wallets`) laissait des centaines de milliers de lignes orphelines qui restaient visibles dans Transactions et Statistiques après suppression du wallet.
- **Défense en profondeur côté lecture** — `GET /api/transactions`, `/api/snapshots`, `/api/snapshots/tokens`, `/api/pnl` et `/api/transactions/gas-total` restreignent maintenant leurs résultats aux wallets réellement présents dans la table `wallets` (`lower(wallet_address) IN (SELECT lower(address) FROM wallets WHERE user_id=?)`). Même si un cascade échouait, aucune donnée d'un wallet supprimé ne peut plus s'afficher ; un compte sans wallet ne voit que du vide. Les filtres `wallet=` de ces endpoints sont eux aussi devenus insensibles à la casse. Formes de réponse inchangées.
- **Sweep des orphelins au démarrage** — nettoyage idempotent dans le lifespan (après les migrations) : suppression des lignes `daily_history`/`transactions` sans wallet correspondant (comparaison insensible à la casse) et des `snapshots` d'utilisateurs sans aucun wallet. Le nombre de lignes purgées est loggé (`[SWEEP] orphan rows removed: ...`).

### v2.12.4 — Détection des swaps dans les Transactions
- **Événements regroupés par transaction** — `GET /api/transactions` regroupe désormais les transferts par `(wallet, chaîne, tx_hash)` et classe chaque événement : **`swap`** (au moins un transfert sortant ET un entrant dans la même transaction — ex. token A vendu contre token B sur un DEX), **`send`** (uniquement sortant) ou **`receive`** (uniquement entrant). Un swap n'apparaît plus comme deux lignes « Envoyé » + « Reçu » séparées mais comme un seul événement « Swap A → B ».
- **Jambes exposées** — chaque événement porte ses jambes (`sent`/`received` : symbole, quantité, valeur, contrat), un résumé pour l'affichage (`sent_symbol`/`sent_amount`, `recv_symbol`/`recv_amount` = jambes principales par valeur USD), la date la plus récente, le **gaz compté une seule fois par transaction**, et la valeur USD de l'échange (max des deux côtés — pas de double comptage).
- **Pagination correcte** — le regroupement est effectué **avant** la pagination : les deux jambes d'un swap ne peuvent plus tomber sur deux pages différentes ; `total` compte des événements.
- **UI** — badge violet distinct « 🔄 Swap » dans la colonne Sens, colonne Token affichant l'échange `A → B`, quantités signées `-X / +Y` (rouge/vert), nombre de jambes indiqué discrètement pour les swaps multi-jambes, infobulle listant toutes les jambes. Filtre « Sens » enrichi d'une option **Swap** (paramètre `type=swap|send|receive`, l'ancien `direction=in|out` reste supporté). Tri par colonne (v2.12.3) inchangé et opérationnel sur les événements, y compris par type/date/valeur. i18n FR/EN.
- **Aucune migration** — le regroupement se fait à la lecture, les données existantes fonctionnent telles quelles. Nouveau module pur `src/services/tx_events.py` + tests (`tests/test_swap_grouping.py`, `tests/smoke_swap_v2.12.4.js`).

### v2.12.3 — Colonnes triables (Détail tokens & Transactions)
- **Tri par clic sur les en-têtes** — 100 % côté client (aucun appel API supplémentaire) : un clic trie la colonne, un second clic inverse le sens ; flèche ▲/▼ sur la colonne active. Le choix de tri est conservé (localStorage) et réappliqué à chaque rafraîchissement des données.
- **Détail tokens** — tokens actifs triables par Token, Chaîne, Balance, Prix, Valeur et PNL ; tokens inactifs triables par Token, Chaîne, Balance, Valeur et Motif. Tri par défaut inchangé : Valeur décroissante.
- **Transactions** — triables par Date, Token, Chaîne, Qté, Prix, Valeur, Gaz et Sens. Tri par défaut inchangé : Date décroissante.
- **Tri robuste** — les colonnes numériques sont comparées comme des nombres (jamais comme du texte), les dates chronologiquement, et les valeurs inconnues (ex. PNL « — ») sont toujours renvoyées en fin de liste quel que soit le sens.

### v2.12.2 — Gestion des tokens fusionnée dans « Détail tokens »
- **Un seul onglet** — la page « Gestion tokens » disparaît (menu + page dédiée) ; tout est désormais dans « 📋 Détail tokens » : cartes DeFi + compteurs « X actifs / Y inactifs », tableau des tokens actifs avec un interrupteur on/off par ligne, section repliable « Tokens inactifs (N) » (fermée par défaut, plafonnée à 100 lignes avec bouton « Voir tout »), et formulaire « Ajouter un token manuellement » avec la liste des tokens manuels (interrupteur + suppression) en bas de page.
- **Auto-désactivation étendue** — deux nouveaux motifs en plus de `memecoin_pattern` et `low_confidence` : `zero_value` (valeur nulle ou prix inconnu) et `spam` (motif anti-spam). Appliqué aux nouveaux tokens **et rétroactivement** aux tokens jamais touchés par l'utilisateur (un choix explicite n'est jamais écrasé).
- **API portfolio enrichie** — `/api/portfolio` renvoie désormais aussi les tokens inactifs (`tid`, `enabled`, `reason`) + `active_count`/`inactive_count` exacts ; les totaux (`total_usd`, `defi_usd`, `defi_breakdown`, `token_count`) restent calculés sur les seuls tokens actifs. Chaque interrupteur appelle `POST /api/tokens/toggle` avec le `tid`, invalide le cache client et re-rend l'interface (Dashboard inclus).

### v2.12.1 — Hotfix « database is locked »
- **Écritures fiables sous rebuild** — l'ajout manuel d'un token (et tout write API) pouvait échouer en 500 « database is locked » pendant qu'un recalcul d'historique commitait en arrière-plan. `busy_timeout` est désormais appliqué **par connexion** (get_db, prefs, rebuild) et l'écriture de `daily_history` passe par un `executemany` unique (transaction beaucoup plus courte).

### v2.12.0 — Gestion des tokens (activer/désactiver)
- **Nouvelle page « 🎛️ Gestion des tokens »** avec deux sous-onglets : **Détectés** (tokens trouvés automatiquement sur les chaînes) et **Ajoutés manuellement** (ajout par chaîne + adresse de contrat).
- **Interrupteur on/off par token** — un token désactivé est exclu du total, du nombre de tokens, de la répartition par chaîne, de la répartition DeFi **et de l'historique** (snapshots + PNL recalculés rétroactivement en tâche de fond via un worker dédié).
- **Auto-désactivation des tokens douteux** (conservatrice, motif affiché et modifiable) :
  - `memecoin_pattern` — grosse valeur affichée (≥ 500 $) issue d'un prix microscopique (≤ 0,0001 $) sur une balance énorme (≥ 10 M d'unités) ;
  - `low_confidence` — indice de confiance DefiLlama du prix < 0,8 (le champ `confidence` de l'API est désormais capturé et propagé).
  Le défaut n'est appliqué qu'à la **première détection** d'un token : le choix explicite de l'utilisateur n'est jamais écrasé.
- **Tokens manuels** — formulaire chaîne + adresse (validation 0x…, 42 caractères), métadonnées récupérées via Blockscout, prix via Blockscout/DefiLlama, fusion dans le portefeuille, suppression possible.
- **Nouveaux endpoints** : `GET /api/tokens?scope=detected|manual`, `POST /api/tokens/toggle`, `POST /api/tokens/bulk`, `POST /api/tokens/manual`, `DELETE /api/tokens/manual`.
- **Nouvelle table** `user_token_prefs` (préférences par utilisateur et par token, clé `(user_id, tid)`, migration idempotente).
- UI : tokens désactivés grisés avec badge motif dans « Détail tokens », boutons « Tout activer / Tout désactiver », i18n FR/EN complète.

### v2.11.27 — Hotfix rendu tokens
- **Correction d'une regression v2.11.26** : dans l'onglet « Détail tokens », la variable de boucle `t` masquait la fonction de traduction `t()` nouvellement appelée dans la même fonction (hoisting), provoquant une `TypeError` qui laissait la page vide et masquait la section DeFi. La variable de boucle est renommée ; le tableau des tokens et la section DeFi s'affichent à nouveau.

### v2.11.26 — DeFi best-effort gratuite
- **Catégorisation DeFi fine** — détection heuristique de 5 catégories (lending, LP, staked/LST, vault/yield, synthetic) à partir des symboles de tokens, sans aucune API tierce.
- **Section DeFi dédiée** — dans l'onglet « Détail tokens », encart récapitulatif avec sous-totaux par catégorie et badges colorés (bleu=lending, violet=LP, vert=staked, jaune=vault, orange=synthetic).
- Carte dashboard : « 🔒 Staké » → « 🔒 DeFi » (agrège toutes les catégories DeFi).
- Rétro-compatibilité : `staked_usd` toujours présent (égale `defi_usd`), `defi_breakdown` ajouté par catégorie.

### v2.11.25 — Page NFTs
- **Nouvelle page NFTs** — grille d'images des NFT (ERC-721 / ERC-1155 / ERC-404) détenus par le(s) wallet(s), agrégée sur toutes les chaînes via l'API Blockscout.
- Nouvel endpoint `GET /api/nfts?address=…` (interroge toutes les chaînes en parallèle, filtre le spam, résout les URI IPFS, plafonné à 600 items).
- Affichage : nom, collection, chaîne et type de token ; images en `lazy-load` avec repli 🖼️ si l'image est indisponible. Fonctionne pour un wallet ou en vue agrégée (« ALL »).

### v2.11.24
- **Client HTTP partagé** — le backfill des frais de gaz réutilise une seule connexion HTTP par chaîne (au lieu d'en créer une par transaction), réduisant le churn de connexions.

### v2.11.23 — Reconstruction par contrat
- **Fin des collisions de symbole** — la reconstruction historique regroupe désormais les tokens par **adresse de contrat** (et non plus par symbole). Deux tokens partageant un symbole (ex. le vrai BOB ~1 $ et un spam « bob » à millions d'unités) ne sont plus fusionnés ni valorisés au prix de l'autre → fin des valeurs aberrantes dans l'agrégat.

### v2.11.22 — Robustesse & qualité
- **SQLite en mode WAL** + `busy_timeout` : lectures concurrentes pendant une écriture, bien moins de « database is locked »
- **Pagination des tokens** : `fetch_chain` parcourt plusieurs pages (plafond de sécurité) au lieu d'une seule → plus de tokens détectés sur les gros wallets
- **Snapshots conservés** au redémarrage (plus de purge de la table `snapshots`)
- **Cohérence UTC** dans le cache de prix (fin d'un décalage possible selon le fuseau du serveur)
- **Nettoyage** d'une condition SQL parasite ; ajout d'une suite de tests des fonctions pures (`tests/test_core.py`)

### v2.11.21 — Sécurité
- **SESSION_SECRET** — génération et persistance automatiques d'une clé aléatoire forte si aucune n'est fournie (les jetons de session n'étaient plus falsifiables). Ne retombe jamais sur une valeur vide ou connue.
- **Anti-XSS** — échappement des noms, symboles et icônes de tokens (contrôlés par des tiers) dans les tableaux Détail tokens et Transactions.
- **/api/update** — désactivé par défaut ; nécessite `ALLOW_UPDATE=1` (l'endpoint pouvait exécuter du code amont).

### v2.11.20
- **Interface épurée** — suppression de la barre de titre en haut : la page courante est simplement mise en évidence dans le menu de gauche. Le nom du compte connecté est déplacé dans Paramètres (section Session).

### v2.11.19
- **Déconnexion déplacée** — le bouton Déconnexion n'est plus dans la barre du haut de chaque page ; il est désormais dans la page Paramètres (section Session).

### v2.11.18
- **Sidebar fixe** — le menu de gauche reste visible (position sticky, hauteur écran) même sur les pages longues comme Transactions ; le lien Paramètres n'est plus repoussé tout en bas.

### v2.11.17
- **Correctif graphiques multi-wallets** — les courbes d'évolution et le PNL agrégés somment désormais les valeurs par date (GROUP BY). Avec plusieurs wallets, l'historique stockait une ligne par wallet et par date, tracées comme des points successifs → fausse oscillation. Résolu.

### v2.11.16
- **Enrichissement fiabilisé (sous-process)** — l'enrichissement des prix historiques s'exécute désormais dans un process dédié, ce qui le rend fiable (les mêmes appels échouaient de façon intermittente dans la boucle événementielle du serveur). Ajout d'un drapeau `price_checked` pour la convergence.

### v2.11.15
- **Ordre d'enrichissement** — l'endpoint d'enrichissement lance d'abord les prix historiques (budget de requêtes DefiLlama propre) avant les autres enrichissements, évitant les échecs liés au rate-limit

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
