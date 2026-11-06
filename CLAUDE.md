# Plateforme d'automatisation de trading — notes de dépôt

Repère court pour développer sur ce dépôt. Pour la présentation du projet
(architecture, sécurité, installation), voir le [README](README.md).

## Structure

- `backend/` — service Python : client Telegram (Telethon) qui lit des signaux,
  passerelle MetaTrader 5 pour l'exécution des ordres, synchronisation Firestore.
  Point d'entrée : `bot_release13.py`.
- `frontend/` — tableau de bord React + Vite (Telegram Mini App).
- `firestore.rules` / `firestore.indexes.json` / `firebase.json` — règles d'accès
  et configuration Firestore.

## Configuration locale (jamais commitée)

- `backend/settings.json` — identifiants runtime (Telegram `api_id`/`api_hash`,
  token du bot, email/mot de passe Firebase, chemin MT5). Ignoré par git.
- `backend/channels_config.py` — identifiants des canaux surveillés. Ignoré par
  git ; copier `channels_config.example.py` et renseigner ses propres valeurs.
- `.env` — variables d'environnement optionnelles (voir `.env.example`). Ignoré.

Rien de tout cela n'est versionné : seuls les modèles `*.example` le sont.

## Lancer

```bash
# Backend (nécessite un terminal MetaTrader 5 connecté sous Windows)
cd backend && pip install -r requirements_firebase.txt && python bot_release13.py

# Frontend
cd frontend && npm install && npm run dev      # dev
cd frontend && npm run build                   # build de production

# Règles Firestore
firebase deploy --only firestore:rules
```

## Conventions

- **Versionnage** (`VERSION_BOT` dans `bot_release13.py`) : SemVer selon
  l'impact **pour l'utilisateur** — MAJEUR = action requise, MINEUR = nouvelle
  fonctionnalité visible, PATCH = correctif invisible. `VERSION_BOT` doit rester
  purement numérique `X.Y.Z` (comparé à un minimum distant par `is_version_older`).
- **Marqueurs `(#nn)`** dans les commentaires : identifient le lot de travail qui
  a écrit une ligne (traçabilité des correctifs). Vérifier le plus grand déjà
  employé avant d'en poser un nouveau ; un lot = un numéro, jamais réutilisé.
- **Build de l'exécutable** : Nuitka (`--onefile --msvc=latest`), lancé depuis
  `backend/`. Vérifier la taille du binaire produit (Nuitka peut sortir en code 0
  même en échec). Sortie dans un dossier à chemin court (limite Windows 260 car.).
- **Qualité** : `py_compile` sur le backend, `npm run lint` + `npm run build` sur
  le frontend avant de pousser.
