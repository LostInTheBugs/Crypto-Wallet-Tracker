# Crypto Wallet Tracker — V1.3

Outil web local pour inventorier les assets de vos wallets crypto.
Multi-wallets et multi-chaînes EVM (tokens ERC-20), total global et détail par wallet, affichage en USD ou EUR (taux BCE via Frankfurter).
Chaînes supportées : **Ethereum, Base, Optimism, Arbitrum, Polygon, Gnosis, zkSync, Celo, Scroll**.
Les tokens de faible valeur (< $0.01) sont masqués par défaut.
**Cache intelligent** : les inventaires sont mis en cache 1 heure, changement de devise instantané sans re-scan.
Données stockées localement, aucune inscription à un service tiers requise.

## Sources de données

Utilise l'**API gratuite Blockscout** — aucun compte ni clé API nécessaire. Détection automatique de tous les tokens ERC-20 du wallet, métadonnées et prix en un appel par chaîne.

| Avantage | Détail |
|---|---|
| 🆓 **100% gratuit** | Pas de quota, pas de clé API |
| 🔗 **9 chaînes** | Ethereum + 8 L2/L1 EVM |
| ⚡ **Parallèle** | Requêtes simultanées sur toutes les chaînes |

## Prérequis

- **Docker** et **docker compose** (installés automatiquement par le script d'installation)
- Optionnel : une clé API Alchemy (recommandée pour la découverte complète des tokens)

## Installation

### Sur un serveur Linux (Debian/Ubuntu)

```bash
curl -fsSL https://raw.githubusercontent.com/LostInTheBugs/Crypto-Wallet-Tracker/main/install.sh | sudo bash
```

Puis ouvrez `http://<adresse-ip-du-serveur>`.

### Manuelle (Docker)

```bash
git clone https://github.com/LostInTheBugs/Crypto-Wallet-Tracker.git
cd Crypto-Wallet-Tracker
cp .env.example .env    # éditer si besoin
docker compose up -d
```

## Utilisation

1. Ouvrez `http://<adresse-ip>:80`
2. Premier lancement : créez votre compte (identifiant + mot de passe)
3. Ajoutez une ou plusieurs adresses de wallet (format `0x…`) avec un libellé
4. L'inventaire s'affiche : **dashboard agrégé** avec le total de tous vos wallets, puis détail par wallet et par chaîne

## Variables d'environnement

| Variable | Défaut | Description |
|---|---|---|
| `PORT` | `80` | Port d'écoute |
| `HOST` | `0.0.0.0` | Interface d'écoute |
| `SESSION_SECRET` | auto | Secret JWT (fixez-le pour garder les sessions entre redémarrages) |
| `ALCHEMY_API_KEY` | — | Clé API Alchemy (optionnelle, pour meilleure découverte) |
| `DB_PATH` | `/data/wallets.db` | Chemin de la base SQLite |

## Stack technique

| Couche | Technologie |
|---|---|
| **Backend** | Python 3.12 · FastAPI · SQLite |
| **Frontend** | Vanilla JS · GitHub Dark Theme |
| **API données** | Blockscout REST API (gratuit) |
| **Déploiement** | Docker · docker compose |
| **Auth** | bcrypt · JWT (httpOnly cookies) |

## Sécurité

- Mots de passe hachés avec bcrypt
- Sessions JWT en cookies httpOnly
- **Aucune clé privée** — seules les adresses **publiques** sont utilisées
- Toutes les données sont stockées localement dans SQLite

## Structure du projet

```
Crypto-Wallet-Tracker/
├── src/
│   └── app.py            # Backend FastAPI (auth, wallets, portfolio)
├── public/
│   └── index.html        # Frontend SPA (dashboard, login)
├── Dockerfile
├── docker-compose.yml
├── install.sh            # Script d'installation automatique
├── requirements.txt
└── README.md
```

## Évolutions prévues

- Support Solana et Cosmos
- Graphique d'évolution du portefeuille
- Export CSV / PDF des inventaires
- Mode sombre/clair

## Licence

MIT
