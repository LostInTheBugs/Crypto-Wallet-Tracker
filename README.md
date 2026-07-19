# Crypto Wallet Tracker — 2026.07.12

**Inventaire local de wallets crypto** — multi-wallets, multi-chaînes EVM, 100 % gratuit (API Blockscout).

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
- [ ] 2026.07.13 — Sauvegardes auto + sante + tests/CI
- [ ] 2026.07.14 — Durcissement auth & comptes

### Phase 2 — Multi-chaines non-EVM & airdrops
- [ ] Refactor abstraction multi-provider (prerequis)
- [ ] Bitcoin (BTC)
- [ ] Solana
- [ ] Cosmos / ATOM
- [ ] Airdrops a claim

## 📋 Changelog

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
