# Plateforme d'automatisation de trading

Système complet d'automatisation de l'exécution d'ordres de trading, piloté par
des signaux reçus sur Telegram et exécuté sur MetaTrader 5, avec un tableau de
bord web temps réel. Projet personnel conçu, développé et sécurisé de bout en bout.

> **Dépôt public à des fins de démonstration technique.** Il met en avant
> l'architecture, les choix d'ingénierie et le travail de **sécurité applicative**
> (voir la section dédiée). Voir la [licence](LICENSE) pour les conditions d'usage.

---

## Aperçu

La plateforme lit en continu des messages structurés sur des canaux Telegram, en
extrait des ordres (symbole, sens, entrée, stop, objectifs), puis les exécute et
les gère automatiquement sur un terminal MetaTrader 5 (prises partielles échelonnées,
mise à seuil de rentabilité, stop suiveur, coupe-circuit de drawdown). Un tableau de
bord web (Telegram Mini App) donne à l'utilisateur un suivi temps réel et des
commandes à distance (statistiques, historique, réglages, arrêt d'urgence).

Trois processus indépendants, reliés par **Firestore** qui sert à la fois de base
temps réel et de bus de commandes :

```
┌──────────────┐   signaux    ┌─────────────────┐   ordres    ┌─────────────┐
│   Telegram   │ ───────────► │  Bot (Python)   │ ──────────► │ MetaTrader 5│
│  (canaux)    │              │  Telethon + MT5 │             │  (courtier) │
└──────────────┘              └────────┬────────┘             └─────────────┘
                                        │  état (push)  ▲ commandes (poll)
                                        ▼               │
                               ┌─────────────────────────────┐
                               │   Firestore (temps réel)     │
                               │   + Firebase Auth            │
                               └───────────────┬─────────────┘
                                   état ▲       │ ▼ commandes
                               ┌───────────────┴─────────────┐
                               │  Dashboard (React/Vite)      │
                               │  Telegram Mini App           │
                               └─────────────────────────────┘
```

Le bot **pousse** son état dans Firestore et **lit** les commandes déposées par le
dashboard ; le dashboard ne touche jamais directement aux données de trading — il
passe par une file de commandes contrôlée côté serveur (voir Sécurité).

## Stack technique

| Couche | Technologies |
|---|---|
| Bot | Python, [Telethon](https://docs.telethon.dev/) (client Telegram), API `MetaTrader5`, asyncio |
| Temps réel / données | Firebase Firestore, Firebase Auth (REST) |
| Dashboard | React, Vite, Telegram Mini App SDK |
| Distribution | Exécutable autonome compilé avec [Nuitka](https://nuitka.net/) |

## Choix d'ingénierie notables

- **Firestore comme bus de commandes** plutôt qu'une API HTTP exposée : pas de port
  ouvert sur la machine de l'utilisateur, authentification et autorisation déléguées
  à des **règles déclaratives** évaluées côté serveur.
- **Séparation lecture/écriture** stricte : le bot est seul à écrire l'état ; le
  client ne fait que déposer des commandes typées et lire leur résultat.
- **Robustesse terrain** : reconnexions Telegram/MT5 sans redémarrage, *watchdog*
  à battement de cœur, coupe-circuit de drawdown persistant, garde-fou sur les
  comptes en mode *netting*, journalisation traçable de chaque action (ou de son
  absence).
- **Gestion du temps rigoureuse** : conversion explicite heure courtier ⇄ heure
  locale pour que les statistiques et l'historique tombent dans les bonnes journées
  quel que soit le fuseau.

---

## 🔒 Sécurité applicative

Le cœur de la sécurité repose sur les **règles Firestore** ([`firestore.rules`](firestore.rules)) :
l'autorisation n'est jamais faite côté client, elle est évaluée par le serveur à
chaque lecture/écriture. Un audit a mis en évidence et corrigé **quatre failles de
contrôle d'accès**. Chacune est documentée ci-dessous (contexte → risque → correctif).

> **Note sur la clé API Firebase.** La clé `apiKey` et l'ID de projet présents dans
> le code **ne sont pas des secrets** : Google les conçoit pour être publics
> (intégrés côté client). La confidentialité des données ne repose pas sur eux mais
> sur les règles ci-dessous. Les véritables secrets (identifiants Telegram, mots de
> passe, identifiants courtier) vivent hors dépôt (fichiers de configuration locaux
> ignorés par git, cf. `.env.example`).

### 1. Prise de contrôle de compte entre utilisateurs

- **Contexte.** Chaque utilisateur est identifié par un jeton de licence lié à son
  identité Telegram. La propriété d'un compte est déduite de ce lien.
- **Risque.** La règle d'écriture de la licence était trop permissive : un
  utilisateur authentifié pouvait **réécrire le lien d'identité** de sa licence vers
  l'identité d'un **autre** utilisateur et devenir propriétaire de son compte et de
  ses données.
- **Correctif.** Les champs d'identité (`telegram_id`, `hwid`) sont désormais
  **inscriptibles une seule fois** (uniquement tant qu'ils sont vides), via un
  contrôle strict du différentiel de champs (`diff().affectedKeys().hasOnly(...)`).
  Création et suppression de licence réservées au backend (Admin SDK).

### 2. Élévation de privilèges via une commande réservée

- **Contexte.** Le dashboard agit en déposant des **commandes** dans une
  sous-collection ; certaines commandes sont réservées au backend.
- **Risque.** Aucune restriction de **type** : le client pouvait créer n'importe
  quelle commande, y compris une commande d'administration (attribution de droits)
  normalement émise par le seul backend — et ainsi **s'octroyer des accès** non
  attribués.
- **Correctif.** Liste blanche explicite des types de commandes qu'un client peut
  créer ; les commandes privilégiées sont refusées, et `update`/`delete` sont
  réservés au backend.

### 3. Falsification de données par appel direct à l'API

- **Contexte.** Les données affichées à l'utilisateur (droits, soldes, statistiques)
  sont écrites par le bot dans son document utilisateur.
- **Risque.** L'écriture client était **libre et sans restriction de champ** : en
  appelant Firestore directement (DevTools / REST), un utilisateur pouvait
  **falsifier son propre état** affiché en contournant totalement l'interface.
- **Correctif.** L'écriture de ce document est **réservée au bot** (contrôle de
  propriété) ; le client ne l'écrit jamais directement et passe exclusivement par la
  file de commandes contrôlée.

### 4. Maintien d'accès après révocation de licence

- **Contexte.** Une licence peut être révoquée ou expirer côté serveur.
- **Risque.** Le statut de la licence n'était **pas revérifié au moment de l'action** :
  une licence révoquée conservait la capacité d'émettre des commandes d'action
  (arrêt, clôture, etc.) tant que le compte d'authentification existait.
- **Correctif.** Une fonction `licenseActiveForUser()` **relit le statut de la
  licence à chaque action privilégiée** ; une licence inactive ne peut plus rien
  déclencher, indépendamment de l'état du compte d'authentification.

### Durcissements complémentaires

- **Limitation de fréquence côté serveur** : un cooldown auparavant appliqué
  seulement dans l'UI (donc contournable) est désormais rendu infranchissable par
  une écriture conditionnelle dans le même *batch*.
- **Collections sensibles** (événements de sécurité, compteurs) en écriture/lecture
  **réservées au backend** (`allow … : if false`).
- **Moindre privilège** appliqué partout : chaque collection n'autorise que les
  opérations strictement nécessaires à chaque acteur.

---

## Installation (développement)

### Pré-requis
- Python 3.11+, Node.js 18+
- Un projet Firebase (Firestore + Auth e-mail/mot de passe)
- Sous Windows, un terminal MetaTrader 5 connecté (pour le bot)

### Backend
```bash
cd backend
pip install -r requirements_firebase.txt
cp settings_template.json settings.json   # puis renseigner ses identifiants
cp channels_config.example.py channels_config.py   # puis renseigner ses canaux
python bot_release13.py
```

### Frontend
```bash
cd frontend
npm install
npm run dev        # serveur de développement
npm run build      # build de production
```

### Règles Firestore
```bash
firebase deploy --only firestore:rules
```

---

## Captures d'écran

> _À compléter._ Placer les images dans `docs/screenshots/` et les référencer ici,
> par exemple :
>
> ```markdown
> ![Tableau de bord — vue globale](docs/screenshots/dashboard-global.png)
> ![Statistiques par canal](docs/screenshots/stats-canal.png)
> ```

---

## Licence

Voir [`LICENSE`](LICENSE). Dépôt public pour consultation et évaluation ;
toute réutilisation nécessite l'accord écrit de l'auteur.
