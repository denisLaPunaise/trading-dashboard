# ✅ Tests à faire (correctifs en attente de validation réelle)

> Liste tenue à jour au fil des correctifs. Chaque bloc = un correctif livré dans le code,
> à tester manuellement (et recompiler l'exe quand `bot_release13.py` est modifié :
> `pyinstaller Bot_FTMO_V14.0.0.spec --clean`).

---

## #2 — TP SABR mal lu (`backend/bot_release13.py`, `extract_sabr_tp`)
Recompiler l'exe avant test.

- [ ] Signal `Gold sell 4015-21 / Tp 3975 / Tp 3910 / Sl 4029` → TP retenu = **3910** (min pour VENTE).
- [ ] Signal `Gold buy ... / Tp 4750 / Tp 4810 / Sl ...` → TP retenu = **4810** (max pour ACHAT).
- [ ] Formats variés OK : `TP1: 3975`, `TP1 3975`, `TP: 4750`, `Target 4750`.
- [ ] **L'ordre passe maintenant réellement sur MT5** (avant : refusé car TP invalide type 750).
- [ ] Aucun TP "rogné" dans le diagnostic SABR du log (`🎯 TP lu`).

---

## #6 — Zone SABR : ordre d'écriture des prix (`backend/bot_release13.py`, branche ZONE)
Recompiler l'exe avant test.

- [ ] `buy 4005-4000` ET `buy 4000-4005` → **même découpage** : gros lot (45%) sur le prix le plus HAUT (4005), petit (20%) sur le plus bas (4000).
- [ ] `sell 4015-4021` ET `sell 4021-4015` → **même découpage** : gros lot (45%) sur le prix le plus BAS (4015), petit (20%) sur le plus haut (4021).
- [ ] Vérifier dans le log `🪓 Découpage (Prix, Lot)` que la répartition ne dépend plus de l'ordre.
- [ ] SL global par défaut cohérent (sous la zone en ACHAT, au-dessus en VENTE).
- [ ] ⚠️ **Valider la règle SELL sur un vrai signal SELL** (déduite par symétrie).

---

## #5 — Canal SABR copie : respect du mute (`backend/bot_release13.py`, forwarder)
Recompiler l'exe avant test.

- [ ] Mute `CANAL SABR_COPIE` (dashboard ou `/mute SABR_CP`) → **plus aucun message recopié** dans le canal copie.
- [ ] Unmute → la recopie reprend normalement.
- [ ] `/forward_sabr off` coupe toujours la recopie (interrupteur dédié intact).
- [ ] Le mute du canal copie ne casse pas le trading des autres canaux SABR (VIP/Free).

---

## #4 — Anti-doublon SABR : pas d'incrément du n° ni de spam (`backend/bot_release13.py`)
Recompiler l'exe avant test.

- [ ] Signal SABR **bloqué par l'anti-doublon** (< 100 pips d'un trade SABR existant) :
  - [ ] Le numéro de signal du jour **n'est PAS incrémenté**.
  - [ ] Aucun message "Signal traité"/"Accepté".
  - [ ] Un **seul** message court `🛡️ Signal SABR bloqué (anti-doublon)` (acceptable).
- [ ] Signal SABR **accepté** (non bloqué) : numéro +1 + message "Signal traité" avec `🆔 Signal : N` + TP/SL corrects.
- [ ] Deux signaux SABR valides d'affilée → numéros consécutifs (1, 2) sans trou dû à un blocage intercalé.
- [ ] Signal **BLIND** ("Go buy") : toujours numéroté + message "EN APPROCHE" (non régressé).
- [ ] Canaux **non-SABR** (MDT/Space/Ligue/Perso) : messages "traité" puis "Accepté" inchangés.
- [ ] Réactivation d'une zone par numéro (`/reactivate`) cohérente avec la nouvelle numérotation.

---

## #8 — Bouton désactiver le drawdown (`backend` + `frontend`)
Recompiler l'exe **ET** redéployer le frontend (Vercel) avant test.

- [ ] Dashboard : bouton "Disjoncteur Drawdown" ON/OFF visible dans le bloc Sécurité & MM.
- [ ] Clic OFF → le bloc passe en rouge avec "⚠️ Protection désactivée" + message Telegram `📉 Disjoncteur Drawdown : 🔴 DÉSACTIVÉ`.
- [ ] Drawdown OFF : un nouveau trade au-delà de la limite de perte **passe quand même** (plus de blocage).
- [ ] Drawdown OFF : **pas** de fermeture d'urgence "URGENCE DD QUOTIDIEN" même si la perte dépasse la limite.
- [ ] Drawdown ON (par défaut) : comportement habituel (blocage + close d'urgence) inchangé.
- [ ] L'état ON/OFF **survit à un redémarrage** du bot (persisté dans settings.json).

---

## #3 — SABR autres paires + risque % par paire (`backend` + `frontend`)
Décision retenue : **OR = lots fixes (inchangé)** ; **hors-or = sizing par risque %** (basé sur le SL du signal), réglage `risk_sabr_other` au dashboard. Recompiler l'exe + redéployer le frontend.

- [ ] Signal SABR **OR** : toujours les lots fixes (FIXED_LOT_SABR_VIP/FREE) — comportement inchangé.
- [ ] Signal SABR **indice** (ex: US30/NAS100) avec SL : lot calculé sur le risque % (vérifier la perte estimée ≈ `risk_sabr_other` × capital dans le log `🧮 DÉTAIL DU CALCUL`).
- [ ] Signal SABR **forex** (ex: EURUSD) avec SL : idem, lot cohérent (pas un lot "or").
- [ ] Signal SABR **crypto** (BTCUSD) avec SL : idem.
- [ ] Dashboard : champ "Risque SABR autres paires (%)" modifie bien le lot des paires non-or (et persiste après redémarrage).
- [ ] Zone hors-or : la répartition 45/35/20 reste correcte sur le volume total calculé par le risque.
- [ ] ⚠️ **Détection du symbole** : vérifier que la paire envoyée par SABR est bien reconnue (sinon retombe sur GOLD). Si une paire n'est pas reconnue, l'ajouter à `SYMBOL_MAPPING` / `BASE_LOTS_FOR_01_PCT`.
- [ ] Paire **inconnue / sans SL** : fallback sûr (FIXED_LOT_OTHER), pas d'ordre aberrant.

---

## #9 — Sections fermées par défaut (`frontend/src/App.jsx`)
Redéployer le frontend avant test.

- [ ] À l'ouverture du dashboard, « Trades Actifs » est **fermée**.
- [ ] À l'ouverture du dashboard, « Panneau de Contrôle » est **fermée**.
- [ ] Le clic ouvre/ferme toujours correctement (animation OK).

---

## #10 — Toggles ON/OFF SL Suiveur + Soft SL Vente (`backend` + `frontend`)
Recompiler l'exe **ET** redéployer le frontend avant test.

- [ ] Deux boutons ON/OFF visibles **sous** le toggle Drawdown (SL Suiveur, Soft SL Vente).
- [ ] **SL Suiveur OFF** : le bot **ne déplace plus** le SL (ni BE, ni trailing, ni paliers) sur les trades non-SABR. Message Telegram `🔒 SL Suiveur : 🔴 DÉSACTIVÉ`.
- [ ] **SL Suiveur ON** : déplacement du SL comme avant.
- [ ] **Soft SL Vente OFF** : plus de clôture partielle sur repli (« REPLI DÉTECTÉ » ne se déclenche plus). Message `📉 Soft SL Vente : 🔴 DÉSACTIVÉ`.
- [ ] **Soft SL Vente ON** : clôture partielle sur repli comme avant.
- [ ] Les deux états **survivent à un redémarrage** (persistés dans settings.json).
- [ ] ⚠️ Ces toggles concernent le trailing **général** (hors SABR, qui garde son propre `SABR_TRAILING_ACTIVE`).

---

## #7 — Packs par client : canaux visibles (logs + dashboard) (`backend` + `frontend` + bot licence)
Filtre **purement cosmétique** (n'affecte PAS le trading). Recompiler l'exe **ET** redéployer le frontend **ET** copier-coller le bot licence sur le VPS.

### Bot de licence (`/gen` + `/channels`)
- [ ] `/gen Pierre 30 sabr` → ZIP généré, settings.json contient `"ALLOWED_CHANNELS": ["CANAL SABR_VIP","CANAL SABR_FREE"]` ; le récap affiche le pack.
- [ ] `/gen Pierre 30` (sans pack) → `ALLOWED_CHANNELS: []` ; récap « TOUS les canaux ».
- [ ] `/gen Pierre 30 SABR_VIP,PERSO` → liste sur-mesure correcte.
- [ ] `/gen Pierre sabr` (pas de jours) → validité illimitée + pack sabr.
- [ ] `/gen Pierre 30 xyz` (jeton inconnu) → message d'erreur listant profils/canaux, **pas** de génération.
- [ ] `/channels FTMO-XXXX mdt` sur une clé existante → réponse « PACK MIS À JOUR » ; le bot du client reçoit la commande et envoie `📦 Pack mis à jour` ; le dashboard se met à jour.
- [ ] `/channels` sur une clé dont le client n'a jamais lancé le bot → message « telegram_id absent ».
- [ ] `/channels FTMO-XXXX full` → tout réaffiché (sauf SABR_COPIE).
- [ ] La **validité** d'une clé reste modifiable comme avant (`/time`) sans impact sur le pack, et inversement.

### Dashboard (filtrage affichage)
- [ ] Client avec pack `sabr` : dans « Panneau de Contrôle », seuls les réglages SABR sont visibles ; **pas** de MDT/Space/Ligue/Perso (risque, lots, mute).
- [ ] Pack vide : **tout** s'affiche comme avant (rétro-compatible — tes clients actuels).
- [ ] La section « Par Canal » (stats) ne montre que les canaux du pack.
- [ ] Les réglages **globaux** (Or / Indices / Devises) restent toujours visibles.

### Logs Telegram (canal du client)
- [ ] Client avec pack `sabr` : aucun message « Signal traité / Accepté / TP / SL » d'un canal **non** SABR n'arrive dans son canal.
- [ ] Les SABR (de son pack) arrivent normalement.
- [ ] Pack vide : tous les logs arrivent comme avant.
- [ ] Les alertes **non liées à un canal** (session, nouveau jour, drawdown, licence, erreurs MT5) arrivent **toujours**, quel que soit le pack.

### ⚠️ Points de vigilance
- [ ] **SABR_COPIE** n'apparaît dans **aucun** profil (`sabr`, `full`…). Il ne s'assigne qu'à la main (`SABR_COPIE` dans la liste). À confirmer que c'est bien le comportement voulu.
- [ ] Le filtrage des logs est **cosmétique** : un trade d'un canal caché s'ouvre quand même et **reste visible dans « Trades Actifs »** (positions live, non filtrées). Normal (trading non touché).
- [ ] `/channels` n'agit que si le bot client tourne (commande Firebase appliquée sous ~3s, sinon au prochain démarrage).
