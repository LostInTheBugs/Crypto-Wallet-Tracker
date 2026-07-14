# Crypto Wallet Tracker — V2.1.7

**Inventaire local de wallets crypto** — multi-wallets, multi-chaînes EVM, 100 % gratuit (API Blockscout).

Dashboard agrégé, graphiques d'évolution, historique automatique via CoinGecko, comptes utilisateurs. Le tout en Docker, une seule commande pour installer.

---

## ✨ Fonctionnalités

- 🔗 **9 chaînes EVM** — Ethereum, Base, Optimism, Arbitrum, Polygon, Gnosis, zkSync, Celo, Scroll
- 💰 **Valorisation USD/€** — temps réel via Blockscout, conversion EUR via BCE (Frankfurter)
- 👥 **Comptes utilisateurs** — inscription, connexion, wallets privés (bcrypt + JWT)
- 📊 **Dashboard** — valeur totale, répartition par chaîne (donut + %), mini-graphe 1J/1S/1M
- 📈 **Statistiques** — graphe d'évolution complet, filtrable par wallet/token/période (All → 1 mois)
- 🔙 **Historique automatique** — backfill depuis la 1ʳᵉ transaction du wallet via CoinGecko
- 💼 **Gestion des wallets** — page dédiée, ajout/suppression avec labels
- ⚡ **Cache intelligent** — portfolios en cache 1h, changement de devise instantané sans re-scan
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
├── src/app.py              # Backend FastAPI (auth, portfolios, snapshots, backfill)
├── public/index.html        # Frontend SPA (dashboard, stats, tokens, wallets)
├── Dockerfile
├── docker-compose.yml
├── install.sh               # Installeur automatique (Docker + systemd)
├── requirements.txt
└── README.md
```

---

## 🔧 Configuration (.env)

| Variable | Défaut | Description |
|---|---|---|
| `PORT` | `80` | Port d'écoute |
| `SESSION_SECRET` | auto | Secret JWT (fixer pour persister les sessions) |
| `ALCHEMY_API_KEY` | — | Optionnel : meilleure découverte de tokens |

---

## 🛠️ Stack

| Couche | Technologie |
|---|---|
| Backend | Python 3.12 · FastAPI · SQLite · httpx |
| Frontend | Vanilla JS · Chart.js · GitHub dark theme |
| Données | Blockscout API (gratuit) + CoinGecko (historique) |
| Déploiement | Docker · docker compose |

---

## 🔐 Sécurité

- Mots de passe hashés **bcrypt**
- Sessions **JWT** en cookies httpOnly
- **Aucune clé privée** — uniquement des adresses publiques
- Données 100 % locales (SQLite)

---

## 📝 Licence

MIT
