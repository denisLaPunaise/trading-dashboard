# Conventions du projet Kairos

## Refactorisation en cours — lire avant d'y toucher

Un plan de refactorisation en 7 étapes existe : **[`REFACTOR_PLAN.md`](REFACTOR_PLAN.md)** à la
racine. Issues GitHub #63 à #68 plus #1, suivi dans l'issue chapeau **#69**.

**L'ordre des étapes n'est pas indicatif** — chacune rend la suivante plus sûre, et le
découpage en modules (#1) passe de 5/5 à 3/5 en risque selon qu'il est fait en premier ou en
dernier.

Avant de commencer n'importe laquelle de ces étapes : lire `REFACTOR_PLAN.md` en entier, puis
**refaire l'analyse complète de la zone concernée**. Le plan date de la V15.7.0 et le code
bouge vite (51 commits en 30 jours) ; les comptages et numéros de ligne qu'il cite sont à
revérifier systématiquement. Le plan a déjà contenu deux erreurs factuelles.

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
- **V15.1.0** — stats par canal fiabilisées (source unique `trades_detailed.csv`, moyenne
  pips/trade, comptage par signal), reprise d'historique via `stats_seed.json`, partiel 30 %
  MDT VIP, bouton Dashboard automatique. Jamais distribuée.
- **V15.2.0** — graphique d'évolution du portefeuille dans l'onglet Global, trades manuels
  rattachés au canal PERSO. Côté bot de licence (pas de rebuild) : commande `/maj` et
  correctif de la conversion en date par Google Sheets.
- **V15.2.1** — courbe du portefeuille : la dotation initiale d'un compte prop (deal de type
  BALANCE) était comptée comme un mouvement, la courbe démarrait donc au solde d'avant
  financement puis bondissait à la dotation.
- **V15.2.2** — deux correctifs trouvés grâce à `debug_mdt.log` :
  1. les signaux MDT FREE étaient attribués à MDT VIP. Le canal se déduisait du FORMAT du
     message (prix collé à l'action = VIP, prix après « À » = FREE) ; MDT FREE ayant changé sa
     mise en forme, tout basculait en VIP. Le canal d'origine Telegram fait désormais foi.
  2. un espace comme séparateur de milliers (« SL : 53 770.0 ») était lu comme deux valeurs,
     et la règle « garder la dernière » retenait **770**. Observé en production sur un US30
     acheté à 53 879 (ticket 517701435), soit une position sans protection réelle.
  Côté bot de licence (pas de rebuild) : détection auto de l'exe, apostrophe `/gen`.
  Ajouté ensuite : alerte au démarrage si le trading algorithmique est désactivé dans MT5.
- **V15.2.3** — zone SABR : un prix abrégé de 3 chiffres était mal reconstruit.
  `smart_adjust_price()` raisonnait toujours en centaines, donc « sell 4395-400 » donnait une
  borne à 4600 au lieu de 4400. Elle s'aligne désormais sur le nombre de chiffres écrits.
- **V15.4.0** — nouveau canal **INDODOG** (groupe à sujets, sujet 64 uniquement ; signaux
  présentés en « menu » indonésien, 3 à 5 TP). Entrée en zone à 3 ordres comme SABR, échelle
  de partiels 30 %/20 % calculée sur le volume TOTAL du signal, annulation des ordres en
  attente au TP1. Plus les 9 correctifs issus de l'analyse des logs de production : partiels
  fermés en totalité (« FERMETURE PARTIELLE » déclenchait une clôture complète), BE ignoré sur
  « SL au NIVEAU d'entrée », symbole résolu avant d'annoncer un signal valide, lot plafonné à
  la marge libre, reconnexion Telegram durcie, faux crash au Ctrl+C, `edit_handler`, journaux
  trompeurs.
- **V15.4.1** — trois irritants de saisie et de découpage.
  1. Les champs numériques du dashboard se battaient avec l'utilisateur : effacer le chiffre
     présent envoyait `parseFloat('') = NaN` et la valeur par défaut se réaffichait en plein
     milieu de la frappe. Ils gardent maintenant un brouillon local pendant la saisie
     (`NumField`), et `updateSetting()` refuse les non-finis.
  2. Le décalage BE n'acceptait pas de valeur négative : le pavé numérique du mobile n'a pas
     de touche moins. Bouton **±** ajouté — le backend acceptait déjà les négatifs.
  3. `split_zone_lots()` centralise le découpage de zone (SABR **et** INDODOG) et garantit que
     la somme des 3 ordres vaut toujours exactement le lot réglé. Le mode manuel remontait au
     pas minimum tout ordre trop petit *après* le calcul, ce qui gonflait le total (0.03 lot
     en 90/5/5 posait 0.05). Le mode égal, lui, était déjà correct. Au passage : le garde-fou
     INDODOG réassignait la variable de boucle et ne s'appliquait à rien, et l'arrondi suit
     désormais les décimales du pas broker au lieu d'un `round(x, 2)` en dur.
  Rappel utile trouvé à cette occasion : le champ « Lots SABR VIP/Free » ne s'applique
  **qu'à l'or**. Tout autre symbole passe par « Risque SABR autres (%) ».
- **V15.5.0** — nouveau canal **FTS** (« Forex VIP SpaceFX »), et généralisation du moteur
  INDODOG en *canaux à échelle de TP*.
  - FTS : prix d'entrée unique, un SL, 3 TP ou plus **non numérotés** — leur ordre
    d'apparition fait foi, un tri par valeur inverserait l'échelle d'un SELL. Le format
    « Sl : 4606 ( 80 pip ) » contient deux nombres : la regex exige que le prix suive
    immédiatement le mot SL, sinon la taille du stop serait lue comme un second prix (même
    piège qu'en V15.2.2). Échelle uniforme 20 % par palier, reliquat au dernier TP.
  - Moteur mutualisé (`is_ladder_channel`, `ladder_pcts`, `ladder_fixed_lot`,
    `ladder_be_at_tp1`) plutôt que dupliqué : un correctif profite aux deux canaux. La mise
    à BE au TP1 reste propre à INDODOG, seul canal qui la recommande dans ses messages.
  - Entrée au **marché** si — et seulement si — on est à moins de 50 pips du prix d'entrée
    **et** que le cours l'a touché dans les 5 dernières minutes (lu sur les bougies M1, pas
    sur le tick courant). Historique indisponible = ordre en attente, jamais de marché par
    défaut.
  - **Péremption** d'un ordre en attente : prix passé à moins de 30 pips sans déclencher
    puis TP2 atteint (l'approche est un fait historique, mémorisé quand on l'observe), ou
    24 h d'attente — âge calculé en heure **serveur**, `time_setup` étant daté par le broker.
  - **Comptage** : `indodog_stats.json` et `fts_stats.json`, une ligne par signal (jamais par
    ordre), paliers cumulatifs = taux de survie direct. Commandes `/stats_indodog` et
    `/stats_fts`. INDODOG passe de 30 % à **10 %** au TP1.
- **V15.6.0** — `/reset_stats <CANAL>` : remise à zéro des stats par canal, **non
  destructive**. `trades_detailed.csv` étant aussi la source de l'historique des trades et de
  la courbe du portefeuille, on n'y supprime rien : `stats_reset.json` mémorise une date de
  coupure par canal et les stats ignorent ce qui est clôturé avant. `annuler` retire la date
  et tout revient, à n'importe quel moment. Deux pièges traités : la coupure est testée
  *après* le rattachement des trades manuels à PERSO (sinon un reset PERSO ne toucherait
  rien), et un seed étant un agrégat indécoupable, il est largué en entier dès que la coupure
  tombe après le début de sa plage. Les libellés acceptés sont ceux des **stats** — SABR y est
  regroupé sous « CANAL SABR VIP », avec une espace, pas sous son nom de pack.
- **V15.7.0** — deux points, dont un vu en production.
  - **INDODOG a cessé de numéroter ses TP.** « Lauk 1 : 4668 » au lieu de « Lauk 1 : 4665
    (TP1) », et un plat inédit (« Menu Sultan »). La regex s'ancrait sur `(TPn)` : zéro palier
    lu, donc aucun partiel **et** un TP à 0 envoyé à MT5. Constaté sur les tickets
    526368935/38/45. `parse_indodog_tps()` accepte désormais les deux formats ; le format non
    numéroté raisonne par **exclusion** (tout `libellé : nombre` est un palier sauf Harga
    Makan / Warteg Tutup / Menu Utama) — s'ancrer sur les trois lignes stables plutôt que sur
    une liste de plats évite de reprendre ce bug au prochain nom inventé. Garde-fous : le
    nombre doit suivre immédiatement le deux-points, et rester entre 0,5× et 2× le SL.
    Nouveau filet : zéro TP lu sur un canal à paliers déclenche une alerte Telegram et
    recrache le message brut en console — la panne était **silencieuse**.
  - **Toggle « Mise à BE SABR »** : *Classique* (historique, le SL remonte à l'entrée à
    +100 pips) ou *Partiel* (le SL ne bouge pas ; si le prix revient toucher l'entrée, on vend
    `SABR_BE_PARTIEL_PCT` du volume **encore ouvert**). La clôture des 60 % à +100 pips est
    inchangée dans les deux modes. Le déclencheur est le prix d'entrée **brut** :
    `BE_OFFSET_PIPS` ne décale que le SL d'une vraie mise à BE, l'y appliquer déplacerait le
    seuil en douce. Le % porte sur le restant et non sur l'initial — sur l'initial, toute
    valeur ≥ 40 % soldait la position sans que le libellé le laisse deviner.
- **V15.8.0** — deux points.
  - **Périodes de l'onglet « Par Canal »** : `24h · 1S · 14j · 1M · 3M · 1 An · Max`. 6M retiré.
    « 24h » et « 1S » sont calés sur des bornes **calendaires** et non sur un nombre de jours
    glissant, même convention que l'Historique Trades — 24h = aujourd'hui depuis 00h00, 1S =
    depuis lundi 00h00, donc la semaine *en cours*. 3M conservé après mesure : à 375 px, sept
    boutons donnent 42 px chacun, sans troncature ni retour à la ligne (« Semaine » écrit en
    toutes lettres, lui, débordait — d'où « 1S »). Le backend savait déjà traiter `"week"`,
    mais la branche n'était jamais atteinte, le dashboard envoyant `'7'`.
  - **`SABR_ANTI_DOUBLON_ACTIVE` ne survivait pas à un redémarrage.** `save_settings()`
    l'écrivait dans `settings.json`, `load_settings()` ne le relisait jamais et ne le déclarait
    pas en `global` : le réglage repartait de son défaut (`True`) à chaque démarrage — d'où
    l'anti-doublon qui « se réactivait tout seul ». Seul réglage du fichier dans ce cas,
    vérifié par comparaison automatique des deux fonctions (36 écrits, 35 relus). Aucune action
    client : le `settings.json` distribué porte déjà la bonne valeur.
- **V15.9.0** — **détail par type d'actif** dans les stats par canal. Un clic sur une carte
  ouvre une carte sur fond flouté : Or, Crypto, Indices, Devises, Argent, Pétrole, Autre.
  `classe_actif()` suit le même vocabulaire de symboles que `get_pip_divisor()`. Les
  sous-totaux sont alimentés en parallèle du total, donc leur somme redonne exactement le total
  du canal — vérifié zone SABR comprise (1 signal / 3 ordres). Le seed étant un agrégat sans
  symbole, il a sa propre ligne « Historique repris » : sans elle, la somme des classes serait
  inférieure au total sans explication. Ouvert à **tous** les canaux, pas seulement MDT — un
  canal mono-actif affiche simplement une ligne.
  Piège trouvé en vérifiant : le flou ne s'appliquait pas (`blur(0px)` mesuré). L'animation
  `fadeIn` partagée anime `backdrop-filter` depuis `blur(0px)`, et **une animation l'emporte
  sur le style inline** — onglet en arrière-plan, rendu suspendu ou `prefers-reduced-motion`
  suffisaient à laisser le fond net. D'où `ouvreDetail`, qui n'anime que l'opacité.
- **V15.10.0** — **verrou drawdown** : il survit désormais à un redémarrage *et* se lève au
  changement de jour. `security_triggered` ne vivait qu'en mémoire — relancer le bot suffisait
  à lever une sécurité censée ne céder qu'à un déverrouillage explicite. Il est écrit dans le
  fichier de stats et relu au démarrage, mais **uniquement dans la branche « même jour »** de
  `load_daily_state()` : une relance le lendemain repart donc sans verrou, sans code dédié.
  Symétriquement, le bloc de minuit le remet à zéro avec les autres compteurs — il manquait,
  et le bot pouvait rester bloqué tout un week-end sans que personne le remarque. L'alerte
  « NOUVEAU JOUR » le signale, seulement s'il était actif.
  Piège rencontré : l'affectation dans `update_daily_stats()` créait une variable **locale**,
  faute de `global` — le verrou ne serait jamais tombé. `py_compile` ne voit pas ça ; c'est un
  contrôle statique (toute fonction affectant le drapeau doit le déclarer) qui l'a attrapé.
  Même famille que le bug de l'anti-doublon en V15.8.0, et exactement ce que l'étape 4 du
  [`REFACTOR_PLAN.md`](REFACTOR_PLAN.md) rendrait impossible.
- **V15.3.0** — décalage du SL à la mise à BE (`BE_OFFSET_PIPS`, signé : positif = SL dans le
  gain), SL suiveur propre à SABR (interrupteur + distance ; `SABR_TRAILING_ACTIVE` existait
  dans les configs distribuées mais n'était lu nulle part), et section « Historique Trades »
  avec sélecteur de période calé sur des journées entières. Côté dashboard uniquement : thème
  forcé en sombre — la palette suivait le thème du téléphone, d'où du texte noir sur fond noir
  chez un client en mode clair.
