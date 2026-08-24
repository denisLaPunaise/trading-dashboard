# Plan de refactorisation Kairos

> **À lire en entier avant de commencer n'importe laquelle des étapes.**
> Ce fichier est la référence. Les issues GitHub en découlent et y renvoient ;
> l'issue chapeau **#69** porte l'ordre et le suivi.
> Établi le 2026-08-24 sur la base de la V15.7.0.

---

## Pourquoi ce plan existe

L'audit de structure du 2026-08-24 a mesuré le backend contre DRY, KISS et SRP :

| Principe | Verdict | Constat principal |
| -------- | ------- | ----------------- |
| **DRY**  | Partiel | 28 calculs de pips réécrits à la main, 17 garde-fous de pack copiés-collés, 26 marqueurs d'état en chaîne libre, 7 endroits à toucher pour ajouter un réglage |
| **KISS** | Faible  | 7 515 lignes dans un fichier, 5 fonctions de plus de 300 lignes, 198 lignes à 8 niveaux d'indentation ou plus, une instruction `global` de 759 caractères |
| **SRP**  | Faible  | `monitor_positions()` assume douze métiers distincts ; `admin_commands()` est une cascade de 30 `elif` sur 765 lignes |

Ce n'est pas un audit de style. **Quatre pannes de production remontent directement à ces
violations** : « FERMETURE PARTIELLE » qui fermait 100 % de la position, les 10 mises à BE
ignorées, le SL posé à 770 sur un US30 acheté à 53 879, et le garde-fou INDODOG qui
réassignait une variable de boucle et ne s'appliquait à rien. Dans les quatre cas, la logique
de trading était juste — c'est la forme du code qui a rendu la panne possible, et surtout
invisible jusqu'à ce qu'un log la révèle.

**Rythme de modification mesuré : 91 commits sur `bot_release13.py`, dont 51 sur les 30
derniers jours.** Ce n'est pas du code dormant : la facture de maintenance est payée chaque
semaine.

---

## Le fait qui commande tout : il n'y a aucun test

Zéro test automatisé dans le dépôt. Toute vérification passe aujourd'hui par « compiler,
lancer, regarder » — et le code de trading ne s'exécute que quand un signal arrive, donc il
n'est même pas déclenchable à volonté.

**Conséquence directe :** aucun refactor ne peut être *prouvé* sans effet. Il peut seulement
être espéré sans effet. C'est pour ça que l'étape 0 est un filet de tests et non une
amélioration de code.

---

## L'ordre, et pourquoi il compte

L'ordre n'est pas indicatif. Chaque étape rend la suivante plus courte ou plus sûre, et le
risque monte progressivement : on commence par ce qui ne change aucun comportement, on termine
par le découpage en modules.

| # | Étape | Issue | Difficulté | Risque | Durée |
| - | ----- | ----- | ---------- | ------ | ----- |
| 0 | Filet de tests sur les parseurs        | #63 | 2/5 | 0/5 | ½ journée |
| 1 | Les 26 marqueurs deviennent des constantes | #64 | 1/5 | 1/5 | 1 h |
| 2 | Deux fonctions pour le calcul de pips  | #65 | 2/5 | 2/5 | 2 h |
| 3 | Table de routage des commandes Telegram | #66 | 3/5 | 2/5 | ½ journée |
| 4 | Table unique des réglages             | #67 | 3/5 | 3/5 | ½ journée |
| 5 | Découper `monitor_positions`          | #68 | 4/5 | 4/5 | 1 journée |
| 6 | Découpage en modules                  | #1  | 5/5 | 3/5 | plusieurs jours |

### Le raisonnement derrière cet ordre

**Le découpage en modules (#1) est noté 3/5 en risque à la fin du plan — il serait à 5/5 s'il
était fait en premier.** Après les six étapes précédentes, il ne reste qu'à déplacer des
fonctions déjà petites et déjà isolées, avec un filet de tests pour attraper les régressions
de parsing. Fait tout de suite, il faudrait découper une fonction de 741 lignes à cheval sur
trois modules, et l'on transporterait au passage les 28 calculs de pips et les 17 garde-fous
dupliqués dans les neuf nouveaux fichiers.

**L'étape 4 (table des réglages) attaque le vrai obstacle du refactor.** L'issue #1 le dit
elle-même : le problème n'est pas de couper le fichier, c'est la masse de variables globales
mutées. Cette table en absorbe l'essentiel.

**Les étapes 0, 1 et 2 ne changent aucun comportement** et se relisent au diff. Ce sont les
seules à pouvoir être faites sans période d'observation en démo.

---

## Règle valable pour chaque étape

Avant de commencer une étape, **refaire l'analyse complète de la zone concernée**. Ce plan a
été établi sur la V15.7.0 ; le code bouge vite (51 commits en 30 jours). Les comptages, les
numéros de ligne et les fonctions citées peuvent avoir changé.

Concrètement, avant de toucher au code :

1. Relire ce fichier en entier.
2. Recompter ce que l'issue annonce (nombre d'occurrences, tailles de fonctions) — ne jamais
   faire confiance aux chiffres écrits ici sans les revérifier.
3. Vérifier qu'aucune étape précédente n'a déjà traité une partie du travail.
4. Chercher les cas particuliers que l'issue ne mentionne pas : ce plan a déjà contenu deux
   erreurs (l'extraction des pips annoncée comme « mécanique » alors qu'elle mêle distances et
   gains signés ; un décorateur proposé pour `admin_commands` qui n'est pas décomposée en
   fonctions). Il en contient probablement d'autres.
5. Annoncer ce qu'on a trouvé **avant** de modifier quoi que ce soit.

---

## Ce qu'il ne faut pas faire

**Ne pas réécrire le bot.** Ces 7 515 lignes contiennent des années de corrections justifiées
nulle part ailleurs que dans le code lui-même : le décalage d'une heure sur l'affichage MT5,
la reconnexion Telegram durcie, le plafonnement à la marge libre, l'anti-doublon croisé MDT,
la borne de zone normalisée par direction. Une réécriture les perdrait toutes, silencieusement,
et il faudrait les redécouvrir une par une — en production, avec de l'argent réel.

**Ne pas enchaîner deux étapes sans distribuer entre les deux.** Chaque étape doit être
vérifiable isolément. Si une régression apparaît trois étapes plus loin, on ne saura plus
laquelle l'a causée.

**Ne pas commencer par l'étape 5 ou 6 sous prétexte qu'elles ont le plus gros gain.** Ce sont
aussi les deux qui touchent le chemin critique d'exécution des ordres.

---

## Ce qui est déjà bien fait, et qu'il faut préserver

- **Source unique pour les stats** : `trades_detailed.csv` alimente les stats par canal,
  l'historique et la courbe du portefeuille. Ne jamais y supprimer de lignes.
- **Verrou de pack centralisé** : `execute_trade()` est le seul point d'entrée vers un ordre
  réel, et le contrôle y est unique. Trois appelants en bénéficient sans le savoir.
- **Moteur d'échelle mutualisé** : `is_ladder_channel()`, `ladder_pcts()`, `split_zone_lots()`
  — INDODOG et FTS partagent un moteur au lieu de le dupliquer.
- **Les commentaires expliquent le *pourquoi*** et citent souvent la panne qui a motivé la
  ligne. C'est ce qui rend ce refactor possible : ne jamais supprimer un commentaire sans
  avoir compris ce qu'il protège.
- **Réversibilité par défaut** : `/reset_stats` pose une date de coupure au lieu d'effacer.

---

## Rapport d'audit complet

Version illustrée, avec le détail des mesures et des pannes de production :
<https://claude.ai/code/artifact/2f248cdb-5ef6-4183-9605-58d3c356d423>
