# Avancement du projet

> Mis à jour au fil des sessions. Référence rapide pour savoir où on en est.

---

## À déployer (code fait, pas encore en prod)

| Quoi | Commande | Active quoi |
|---|---|---|
| **Frontend → Vercel** | `git push` ou `npx vercel --prod` (depuis `frontend/`) | Fix curseurs (#11), filtrage dashboard (#7), toggles inline (#14) |
| **Bot licence → VPS** | Copier-coller `admin_license_bot_V3.py` manuellement sur le VPS | `/gen dev` (#13), `/gen [pack]`, `/channels` (#7) |

> L'exe (`Bot_FTMO_V14.0.0.exe`) est déjà recompilé (2026-07-01) — pas besoin de recompiler pour les issues ci-dessus.

---

## À tester (sur le VPS après déploiement)

Checklist complète dans `TESTS_A_FAIRE.md`. Issues à valider :

- [ ] **#2** TP SABR lu en entier
- [ ] **#3** SABR hors-or dimensionné par risque %
- [ ] **#4** Anti-doublon SABR sans spam
- [ ] **#5** Forwarder SABR respecte le mute
- [ ] **#6** Zone SABR indépendante de l'ordre d'écriture
- [ ] **#7** Packs par client (`/gen [pack]`, `/channels`, dashboard, logs)
- [ ] **#8** Toggle ON/OFF disjoncteur drawdown
- [ ] **#9** Sections fermées par défaut
- [ ] **#10** Toggles SL Suiveur / Soft SL
- [ ] **#11** Curseurs sans popup bloquant / `/forward_sabr` / base drawdown

> Fermer chaque issue GitHub après validation.

---

## Issues ouvertes (à faire plus tard)

| # | Titre | Notes |
|---|---|---|
| **#12** | Nettoyage tech-debt | Imports/globals inutiles, code mort (`is_strat_2`, `CFG_TP3_PCT`, `MAX_SPREAD_PIPS`…), purge collection Firestore `commands`, 2 warnings eslint exhaustive-deps |
| **#1** | Refactor monolithe `bot_release13.py` | ~4900 lignes → chantier séparé, pas commencé |

---

## Issues fermées (référence)

| # | Titre | Commit |
|---|---|---|
| #2 | TP SABR lu en entier | `72718ba` |
| #3 | SABR hors-or par risque % | `232872c` |
| #4 | Anti-doublon SABR sans spam | `b09395d` |
| #5 | Forwarder respecte le mute | `0920bb1` |
| #6 | Zone SABR indépendante | `7abba3a` |
| #7 | Packs par client | `e3496f1` |
| #8 | Toggle disjoncteur drawdown | `8b22205` |
| #9 | Sections fermées par défaut | `0a90a76` |
| #10 | Toggles SL Suiveur / Soft SL | `0a90a76` |
| #11 | Bugs mineurs (curseurs, /forward_sabr, DD) | `9b4377d` |
| #13 | `/gen dev` tous canaux + illimité | `50d3c22` |
| #14 | Toggles à droite de leur réglage | `7a778ef` |
