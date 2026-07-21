# Avancement du projet

> Mis à jour au fil des sessions. Référence rapide pour savoir où on en est.

---

## À déployer (code fait, pas encore en prod)

| Quoi | Commande | Active quoi |
|---|---|---|
| **Frontend → Vercel** | `git push` ou `npx vercel --prod` (depuis `frontend/`) | Fix curseurs (#11), filtrage dashboard (#7), toggles inline (#14), nettoyage eslint (#12), ergonomie panneau (#15), risque journalier (#16), toggles instantanés (#17) |
| **Bot licence → VPS** | Copier-coller `admin_license_bot_V3.py` manuellement sur le VPS | `/gen dev` (#13), `/gen [pack]`, `/channels` (#7), relais sécu 5s→1h, fix nom client écrasé |

> L'exe (`Bot_FTMO_V14.0.0.exe`) recompilé le 2026-07-02 23:01 (inclut la zone de la mort par canal #18 + filtre spread #11) — à recopier sur le VPS avec le reste du lot.

**Rappel commande** : pour régler le % de clôture au TP1 d'un canal, la commande est **`/sell_lot CANAL %`** (pas `/tp1`) — ex: `/sell_lot MDT_VIP 40`.

## TTL Firestore — abandonné (non prioritaire)

Tentative d'activer une purge auto de `users/{uid}/commands` (2026-07-01) : l'appel direct via `serviceAccountKey.json` a échoué (clé invalide, cf. mémoire `firebase-service-account-key-invalid`) ; la console web a renvoyé un 403 même en Propriétaire ; testé enfin via Cloud Shell (`gcloud firestore fields ttls update ...`), qui a donné la vraie cause : **`PERMISSION_DENIED: Project ftmo-trading-bot-59dae has billing disabled`**. Cette opération TTL exige le plan Blaze (facturation activée), même si l'usage réel reste dans le quota gratuit.

**Décision : abandonné, impact réel négligeable** (documents minuscules, la requête de polling filtre déjà sur `status == pending` donc pas de coût/lenteur liés à la croissance de la collection). Le code écrit déjà un champ `expire_at` sur chaque commande (commit `3c6af78`) — si un jour l'envie revient : activer le plan Blaze (Firebase Console → Paramètres → Usage and billing), puis `gcloud firestore fields ttls update expire_at --collection-group=commands --enable-ttl` (fonctionne directement dans Cloud Shell, pas besoin d'installer gcloud).

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
| **#1** | Refactor monolithe `bot_release13.py` | ~4900 lignes → chantier séparé, pas commencé |
| **#20** | Stratégie MDT VIP : analyser clôtures/BE du provider | Bloqué, attente captures d'écran utilisateur |
| **#21** | Parsing par IA (LLM) pour canaux au format libre (ex: canal indonésien) | Exploration faisable, attente exemples de messages |
| **#22** | Mise à jour parsing MDT_FREE (format changé) | Bloqué, attente nouveaux exemples de messages |

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
| #12 | Nettoyage tech-debt (imports, globals, code mort, eslint) | `5fd34ef`, `677ba30`, `3c6af78` |
| #15 | Ergonomie panneau : debounce sliders only, lots SABR réalignés, lock risques, jours cliquables | `8c34481` |
| #16 | Risque journalier : jour actuel compact + liste verticale au clic (fix débordement mobile) | `d7b6cd1` |
| #17 | Toggles (drawdown, SL suiveur, soft SL, mute) instantanés | `e4cdeba` |
| #11 | Filtre spread MAX_SPREAD_PIPS activé (dernier point) | `656a0ce` |
| #18 | Analyse MDT VIP/Free : zone de la mort par canal + réglages appliqués | `3b34905` |
| #23 | Audit stats "Par canal" -- fusion SABR_COPIE confirmée normale, reste différencié | -- |
