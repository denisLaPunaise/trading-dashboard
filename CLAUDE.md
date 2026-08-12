# Conventions du projet Kairos

## Versionnage de l'exe (validé le 2026-08-12)

Format : `Kairos_V<MAJEUR>.<MINEUR>.<PATCH>.exe` — SemVer, où le niveau est choisi
selon **l'impact sur le client**, pas sur le code.

| Niveau      | Quand l'incrémenter                                                                                                             | Exemples                                                                    |
| ----------- | ------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------- |
| **MAJEUR**  | Le client doit **agir** : nouvelle procédure d'installation, réappairage Telegram, format `settings.json` cassé, MAJ forcée via `ver_min` | V14 (migration Firebase), V15 (audit sécurité v2)                            |
| **MINEUR**  | Nouvelle **fonctionnalité visible**, installation inchangée                                                                       | partiel 30 % MDT VIP, stats par canal périodisées, toggle anti-doublon        |
| **PATCH**   | Correctif **invisible**, aucun nouveau réglage                                                                                    | formatage des lots à 2 décimales, taille de la console, calcul des pips       |

Règle rapide : **s'il faut écrire une ligne au client pour annoncer le changement,
c'est au minimum un MINEUR.**

### Contrainte technique

`VERSION_BOT` doit rester **purement numérique `X.Y.Z`**. Il est comparé au `ver_min`
de Firestore par `is_version_older()` (`backend/bot_release13.py`), qui fait `int(x)` sur
chaque partie : un suffixe (`-beta`, `_test`…) lève une `ValueError` avalée par le
`except: return False`, ce qui **désactive silencieusement le blocage de version**.

### Endroits à mettre à jour à chaque bump

1. `backend/bot_release13.py` → `VERSION_BOT`
2. `Security Licence Check/admin_license_bot_V3.py` → `EXE_FILENAME`
   (⚠️ implique de recopier ce fichier sur le VPS, cf. workflow manuel)
3. `backend/Kairos_V<version>.spec` → nom du fichier + champ `name=`
4. `Security Licence Check/INSTALLATION.txt` et `GUIDE_ADMIN.md`
5. Le binaire compilé dans `Security Licence Check/`

### Historique

- **V15.0.0** — audit sécurité v2 (issues #46-#62). Deux builds portent ce nom : le
  PyInstaller d'origine (36,7 Mo, conservé en `.pyinstaller.bak.exe`) et le premier build
  Nuitka (33,1 Mo). C'est cette collision qui a motivé la convention.
