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
