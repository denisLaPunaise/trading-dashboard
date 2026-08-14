# Kairos — Guide Admin (canal sécurité + canal support)

Fiche de référence pour toi (l'admin). Couvre : comment tu donnes le bot à un nouveau
client, toutes les commandes du canal sécurité (gestion des licences), et toutes les
commandes du canal support (mailbox retour client).

---

## ⚠️ Pré-requis : ADMIN_IDS

Depuis le dernier lot de correctifs (2026-07-27), le bot de licence **n'accepte plus aucune
commande** tant que ton ID Telegram numérique n'est pas déclaré. Sans ça, tout ce qui suit
dans cette fiche ne répondra rien.

Dans `license_secrets.json` (à côté de `admin_license_bot_V3.py`, en local **et** sur le VPS) :

```json
{
  "API_ID": ...,
  "API_HASH": "...",
  "SECURITY_BOT_TOKEN": "...",
  "ADMIN_IDS": [TON_ID_TELEGRAM]
}
```

Récupère ton ID via **@userinfobot** sur Telegram. Tu peux mettre plusieurs ID dans la
liste si plusieurs personnes doivent administrer (ex: `[111111, 222222]`).

**Note importante** : le bot écoute techniquement les DEUX canaux (sécurité et support)
avec le même jeu de commandes — rien n'empêche de taper une commande licence dans le canal
support ou l'inverse. Par convention et pour garder un historique clair, utilise chaque
canal pour son propre usage (licences dans le canal sécurité, mailbox dans le canal support).

---

## 1. Comment tu donnes le bot à un nouveau client

### Étape 1 — Décider du pack et de la durée
Selon ce que le client a payé : quel(s) canal(aux) de signaux, combien de jours (ou
illimité).

### Étape 2 — Générer la licence (canal sécurité)
```
/gen Prenom 30 sabr
```
- `Prenom` : juste pour t'y retrouver (nom affiché dans `/info`, `/search`).
- `30` : optionnel, nombre de jours. Omis = illimité.
- `sabr` : optionnel, profil ou liste de canaux (détails section 2). Omis = tous les canaux.

Le bot répond avec un récap (clé, pack, version) **et envoie automatiquement un fichier
ZIP** `Kairos_Prenom.zip` dans le canal, contenant :
- `Kairos_V15.2.2.exe`
- `settings.json` **déjà pré-rempli** (clé de licence + identifiants Firebase générés)
- `INSTALLATION.txt` (guide pas-à-pas pour le client)

### Étape 3 — Envoyer le ZIP au client
Transfère ce ZIP au client (Telegram, mail, peu importe).

### Étape 4 — ⚠️ Ajouter le client aux canaux Telegram de signaux (MANUEL, pas automatisé)
Le système de licence ne gère que l'autorisation **logicielle** (quels canaux son bot a le
droit de trader/afficher). Il ne gère **pas** l'appartenance réelle aux canaux Telegram de
signaux (SABR VIP, MDT VIP, etc.). Il faut donc **toi-même** ajouter le client aux canaux
Telegram correspondant à son pack — sinon son bot tourne mais ne reçoit aucun signal.

### Étape 5 — Le client s'installe (via INSTALLATION.txt)
Il récupère son `TELEGRAM_API_ID`/`TELEGRAM_API_HASH` (my.telegram.org), crée son propre
bot Telegram (BotFather `/newbot`) pour le dashboard, vérifie le chemin MT5, puis lance
l'exe. Au tout premier lancement : Telethon lui demande son numéro + le code SMS reçu (une
seule fois, une session est sauvegardée ensuite) ; puis un code PIN à envoyer à son propre
bot dashboard (`/start <PIN>`) pour lier le dashboard web.

### Étape 6 — Confirmation
Tu reçois une notification dans le canal sécurité dès la première connexion réussie du
client (relayée automatiquement, cf. section 2).

### Cas particulier : profil `dev`
```
/gen MonTest dev
```
Débloque TOUS les canaux (y compris `SABR_COPIE`, jamais inclus ailleurs) + validité
illimitée + console détaillée (`DISPLAY_MODE=dev`). **Réservé à toi/tes tests, jamais à un
vrai client payant.**

### Ajouter une licence déjà existante (rare)
```
/add Prenom Clé [30]
```
N'envoie pas de ZIP — juste la ligne Sheet + le compte Firebase. Le bot te redonne
l'email/mot de passe Firebase générés à copier toi-même dans un `settings.json`.

---

## 2. Canal sécurité — gestion des licences

### Génération
| Commande | Effet |
|---|---|
| `/gen Prenom` | Clé illimitée, tous canaux |
| `/gen Prenom 30` | Clé 30 jours, tous canaux |
| `/gen Prenom 30 sabr` | 30 jours, pack prédéfini |
| `/gen Prenom 30 SABR_VIP,PERSO` | 30 jours, canaux sur-mesure |
| `/add Prenom Clé [30]` | Ajout manuel d'une clé (pas de ZIP envoyé) |

**Profils disponibles** : `sabr`, `mdt`, `space`, `ligue`, `perso`, `full` (tout sauf
SABR_COPIE), `none` (suspend tout), `dev` (interne uniquement, cf. ci-dessus).

**Canaux valides (sur-mesure)** : `SABR_VIP`, `SABR_FREE`, `SABR_COPIE` (⚠️ jamais dans un
pack normal, assignation manuelle uniquement), `MDT_VIP`, `MDT_FREE`, `SPACE`, `LIGUE`,
`PERSO`.

### Pack de canaux (changer après coup)
```
/channels Clé sabr,mdt          → change le pack à distance
/channels Clé SABR_VIP,PERSO    → canaux sur-mesure
/channels Clé none              → suspend TOUS les canaux (pas "tout débloqué")
/channels Clé full               → tout débloquer
```
Appliqué immédiatement si le dashboard du client est déjà lié, sinon au prochain
lancement/check de licence du bot (au démarrage ou sous 4h). **Ne modifie jamais la durée
de validité.**

### Consultation
| Commande | Effet |
|---|---|
| `/info Clé` | Détails : nom, statut, HWID (miroir Sheet, ≤1h de retard), pack, expiration |
| `/history Clé` | Historique : connexions, conflits HWID, resets, bans/unbans |
| `/search Terme` | Recherche (nom, clé, HWID...) dans toute la base |
| `/stats_users` | Nombre de licences actives / bannies / expirées |

### Gestion du cycle de vie
| Commande | Effet |
|---|---|
| `/ban Clé confirm` | Bloque tout accès (bot + dashboard). Confirmation obligatoire. |
| `/unban Clé` | Débannit **sans** toucher à la date d'expiration |
| `/del Clé confirm` | Supprime définitivement la ligne. Confirmation obligatoire, ⚠️ irréversible. |
| `/reset Clé` | Autorise un changement de PC (efface le HWID). Refusé si déjà fait il y a moins d'1h — `/reset Clé force` pour outrepasser. |
| `/time Clé 30` | Ajoute 30 jours + réactive automatiquement la licence |
| `/time Clé 25/12/2026` | Fixe une date de fin précise + réactive |
| `/time Clé unlimited` | Passe en illimité + réactive |
| `/time Clé Valeur force` | Idem, même si la clé est actuellement BANNIE |

### Comptes Firebase (dashboard client)
| Commande | Effet |
|---|---|
| `/fbcreate Clé` | Crée le compte Firebase d'une clé existante (sans effet si déjà provisionné) |
| `/fbcreate Clé force` | Régénère le mot de passe (⚠️ casse le `settings.json` déjà distribué — le client doit le remettre à jour). Refusé si la licence est BANNIE. |

### Version minimale
| Commande | Effet |
|---|---|
| `/set_ver 7.5` | Change la version minimale par défaut pour les **prochaines** clés `/gen` |
| `/push_ver 7.5 confirm` | Force **TOUS** les clients actifs à passer en v7.5. Confirmation obligatoire, peut prendre du temps. |

### Autre
| Commande | Effet |
|---|---|
| `/say Texte` | Répond juste dans le canal (pas un vrai broadcast vers les clients) |
| `/help` | Rappel de toutes les commandes |

### Ce qui arrive automatiquement (pas des commandes)
- **Notification de connexion** dès qu'un client démarre son bot avec succès pour la
  première fois (ou change de machine avec ton accord).
- **Conflit HWID détecté → licence bannie automatiquement**, avec un message indiquant la
  clé et le HWID en conflit. Si c'est un changement de machine légitime (pas un partage) :
  `/unban Clé` puis `/reset Clé`.
- **Expiration automatique** détectée (Sheet + Firestore + compte Firebase/dashboard
  désactivés).
- Le HWID affiché dans la Google Sheet (colonne HWID) se resynchronise automatiquement
  (visuel, jusqu'à 1h de retard — Firestore reste toujours la source de vérité réelle,
  utilisée par le bot du client).
- Un journal local `admin_audit.log` (à côté du script, sur le VPS) garde une trace de
  toutes les commandes tapées.

---

## 3. Canal support — mailbox retour client

Le client signale un bug/suggestion depuis son dashboard (bouton "Signaler un bug /
suggestion") : le message est automatiquement relayé ici avec un numéro de ticket. Limite
côté client : 1 message toutes les 15 minutes.

| Commande | Effet |
|---|---|
| `/message` | Liste tous les tickets **pas encore corrigés** (triage rapide) |
| `/vu 1234` | Marque le ticket #1234 comme vu (visible côté client : badge "Vu ✓") |
| `/corrige 1234` | Marque le ticket #1234 comme corrigé (badge "Corrigé ✓") |
| `/rep 1234 Merci, corrigé dans la prochaine version !` | Répond au client (visible sous le ticket dans son dashboard). Ne change **pas** le statut — combinable librement avec `/vu`/`/corrige`, dans n'importe quel ordre. |

`/vu`, `/corrige` et `/rep` sont trois actions indépendantes : tu peux répondre sans
changer le statut, changer le statut sans répondre, ou faire les deux dans l'ordre que tu
veux.

---

## Récapitulatif express (pense-bête)

```
Nouveau client  →  /gen Prenom [jours] [pack]  →  envoyer le ZIP  →  l'ajouter aux canaux Telegram de signaux
Changement PC   →  /reset Clé
Client fraude   →  déjà géré automatiquement (auto-ban) ; /unban + /reset si c'était légitime
Client à bannir →  /ban Clé confirm
Erreur de ban   →  /unban Clé
Prolonger       →  /time Clé 30
Nouveau pack    →  /channels Clé profil|canaux
Ticket support  →  /message  →  /vu ou /corrige  →  /rep si besoin
```
