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
