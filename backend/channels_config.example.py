"""
Identifiants des canaux Telegram — MODÈLE (à committer, sans vraies valeurs).

Copier ce fichier en « channels_config.py » (ignoré par git) et renseigner les
identifiants de VOS propres canaux. Un ID de canal ne donne aucun accès en soi
(il faut être membre), mais on ne versionne pas les vrais : ils révéleraient les
canaux de signaux surveillés. Sans « channels_config.py », les IDs valent 0 et le
bot démarre sans surveiller aucun canal.

Tous les champs sont des entiers (ID de chat Telegram, négatifs pour un canal/groupe).
"""

# ── Canaux sources de signaux ────────────────────────────────────────────────
ID_MDT_VIP          = 0   # ex. -1000000000001
ID_MDT_FREE         = 0
ID_SABR_VIP         = 0
ID_SABR_FREE        = 0
ID_CANAL_SABR_COPIE = 0
ID_INDODOG          = 0
INDODOG_TOPIC_ID    = 0   # groupe à sujets : numéro du sujet à surveiller
ID_FTS              = 0
ID_SPACE            = 0
ID_LIGUE            = 0
ID_CANAL_PERSO      = 0

# ── Canaux d'administration (hors sources de signaux) ────────────────────────
ID_SECURITY_CHANNEL = 0   # canal licence / sécurité
ID_FEEDBACK_CHANNEL = 0   # canal support / mailbox
ID_ADMIN_LOG        = 0   # groupe de logs admin
