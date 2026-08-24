import asyncio
import io
import json
import os
import re
import secrets
import threading
import zipfile
import gspread
import requests
from datetime import datetime, timedelta, timezone
from oauth2client.service_account import ServiceAccountCredentials
from telethon import TelegramClient, events
from telethon.tl.types import DocumentAttributeFilename

# --- FIREBASE ADMIN SDK (pour sync licences vers Firestore + création comptes Auth) ---
try:
    import firebase_admin
    from firebase_admin import credentials as fb_creds, firestore as fb_firestore, auth as fb_auth
    _fb_cred = fb_creds.Certificate("serviceAccountKey.json")
    firebase_admin.initialize_app(_fb_cred)
    _fb_db = fb_firestore.client()
    FIREBASE_ENABLED = True
    print("✅ Firebase Admin initialisé.")
except Exception as e:
    FIREBASE_ENABLED = False
    _fb_db = None
    print(f"⚠️ Firebase non disponible (mode dégradé) : {e}")

def fb_sync_license(key, user, status, ver_min=None, hwid=None, date_fin=None, allowed_channels=None):
    """Écrit/met à jour une licence dans Firestore /licenses/{key}.

    user/status : toujours écrits (obligatoires pour tous les appelants).
    ver_min/hwid/date_fin/allowed_channels : None (défaut) = ne touche PAS au champ
    existant -- avant ce fix, ver_min/hwid/date_fin avaient des défauts "vides" TOUJOURS
    envoyés, donc /ban et /time (qui ne les passent pas) écrasaient silencieusement le
    HWID enregistré et la version min à chaque appel. Passer explicitement "" pour
    effacer un champ volontairement (ex: hwid="" à la création d'une clé neuve)."""
    if not FIREBASE_ENABLED or not _fb_db:
        return
    try:
        payload = {
            "user": user,
            "status": status,
            "updated_at": fb_firestore.SERVER_TIMESTAMP,
        }
        if ver_min is not None: payload["ver_min"] = ver_min
        if hwid is not None: payload["hwid"] = hwid
        if date_fin is not None: payload["date_fin"] = date_fin
        if allowed_channels is not None:
            payload["allowed_channels"] = list(allowed_channels)
        _fb_db.collection("licenses").document(key).set(payload, merge=True)
    except Exception as e:
        print(f"⚠️ fb_sync_license error: {e}")


def fb_create_firebase_user(key, force=False):
    """Crée un compte Firebase Auth pour un nouveau client.
    UID = license_key, email = key@ftmobot.com, mot de passe aléatoire.

    (#37) Si le compte existe DÉJÀ et force=False (défaut) : on ne touche à RIEN -- avant,
    relancer cette fonction sur un client déjà provisionné régénérait silencieusement un
    nouveau mot de passe (invalidant celui déjà dans le settings.json distribué au client)
    ET forçait disabled=False (annulant silencieusement un ban/expiration). force=True
    reproduit l'ancien comportement, pour un vrai besoin de reset explicite.

    Retourne (email, password, already_existed)."""
    if not FIREBASE_ENABLED:
        return None, None, False
    import string
    alphabet = string.ascii_letters + string.digits + "!@#$%"
    password = "".join(secrets.choice(alphabet) for _ in range(20))
    email = f"{key.lower()}@ftmobot.com"
    try:
        try:
            fb_auth.get_user(key)
            if not force:
                return None, None, True
            fb_auth.update_user(key, email=email, password=password, disabled=False)
        except fb_auth.UserNotFoundError:
            fb_auth.create_user(uid=key, email=email, password=password)
        # (#56) On ne stocke QUE l'email pour référence -- le mot de passe n'est écrit nulle
        # part ailleurs que l'unique message Telegram/settings.json de délivrance : le stocker
        # une 3e fois en clair dans Firestore n'a aucun usage (rien ne relit ce champ, cf.
        # /fbcreate qui refuse de le régénérer sans force) et n'est qu'un risque supplémentaire.
        if _fb_db:
            _fb_db.collection("licenses").document(key).set(
                {"firebase_email": email}, merge=True
            )
        return email, password, False
    except Exception as e:
        print(f"⚠️ fb_create_firebase_user error: {e}")
        return None, None, False


def fb_disable_firebase_user(key):
    """Désactive le compte Firebase Auth d'un client banni."""
    if not FIREBASE_ENABLED:
        return
    try:
        fb_auth.update_user(key, disabled=True)
    except Exception as e:
        print(f"⚠️ fb_disable_firebase_user error: {e}")


def fb_enable_firebase_user(key):
    """Réactive le compte Firebase Auth d'un client."""
    if not FIREBASE_ENABLED:
        return
    try:
        fb_auth.update_user(key, disabled=False)
    except Exception as e:
        print(f"⚠️ fb_enable_firebase_user error: {e}")


def _license_telegram_id(key):
    """Récupère le telegram_id lié à une licence (pour agir sur le compte dashboard)."""
    if not (FIREBASE_ENABLED and _fb_db):
        return None
    try:
        d = _fb_db.collection("licenses").document(key).get()
        if d.exists:
            tg = d.to_dict().get("telegram_id")
            return str(tg) if tg else None
    except Exception as e:
        print(f"⚠️ _license_telegram_id: {e}")
    return None


def fb_push_set_channels(telegram_id, channels):
    """(#7) Dépose une commande set_allowed_channels dans /users/{tg}/commands.
    Le bot du client la récupère (polling 3s) et met à jour son pack à chaud."""
    if not (FIREBASE_ENABLED and _fb_db):
        return False
    try:
        _fb_db.collection("users").document(str(telegram_id)).collection("commands").add({
            "type": "set_allowed_channels",
            "payload": {"channels": list(channels)},
            "status": "pending",
            "created_at": fb_firestore.SERVER_TIMESTAMP,
            # (#12) purge TTL Firestore. datetime.utcnow() est deprecie depuis Python 3.12 :
            # il renvoyait un datetime NAIF que le client Firestore reinterpretait en UTC.
            # now(timezone.utc) donne le meme instant, mais explicitement date en UTC.
            "expire_at": datetime.now(timezone.utc) + timedelta(days=3),
        })
        return True
    except Exception as e:
        print(f"⚠️ fb_push_set_channels error: {e}")
        return False


def fb_disable_dashboard(key):
    """Désactive + révoque le compte dashboard (uid=telegram_id) — ferme le ban-gap."""
    if not FIREBASE_ENABLED:
        return
    tg = _license_telegram_id(key)
    if not tg:
        return
    try:
        fb_auth.update_user(tg, disabled=True)
        fb_auth.revoke_refresh_tokens(tg)
    except fb_auth.UserNotFoundError:
        pass  # le dashboard ne s'est jamais connecté
    except Exception as e:
        print(f"⚠️ fb_disable_dashboard: {e}")


def fb_enable_dashboard(key):
    """Réactive le compte dashboard (uid=telegram_id) lié à cette licence."""
    if not FIREBASE_ENABLED:
        return
    tg = _license_telegram_id(key)
    if not tg:
        return
    try:
        fb_auth.update_user(tg, disabled=False)
    except fb_auth.UserNotFoundError:
        pass
    except Exception as e:
        print(f"⚠️ fb_enable_dashboard: {e}")

# --- PATCH COMPATIBILITÉ PYTHON < 3.9 ---
if not hasattr(asyncio, 'to_thread'):
    import contextvars, functools
    async def to_thread(func, *args, **kwargs):
        loop = asyncio.get_running_loop()
        ctx = contextvars.copy_context()
        func_call = functools.partial(ctx.run, func, *args, **kwargs)
        return await loop.run_in_executor(None, func_call)
    asyncio.to_thread = to_thread
# ----------------------------------------

# ==========================================
# ⚙️ CONFIGURATION ADMIN
# ==========================================

# Identifiants Telegram + token du bot de sécurité — chargés depuis un fichier local NON
# commité (license_secrets.json, à côté de ce script sur le VPS). Ainsi une modif du code
# (par Claude ou toi) n'efface jamais ces valeurs : plus besoin de les recopier à chaque
# relance, le fichier reste intact tant que tu n'y touches pas.
API_ID = 0
API_HASH = ""
SECURITY_BOT_TOKEN = ""
ADMIN_IDS = set()
try:
    with open("license_secrets.json", "r", encoding="utf-8") as _sf:
        _secrets = json.load(_sf)
    API_ID = int(_secrets.get("API_ID", 0))
    API_HASH = _secrets.get("API_HASH", "")
    SECURITY_BOT_TOKEN = _secrets.get("SECURITY_BOT_TOKEN", "")
    # (#48) Liste blanche des Telegram user_id autorisés à exécuter des commandes admin.
    # Avant ce fix, seul le CANAL était vérifié (SECURITY_CHANNEL_ID/FEEDBACK_CHANNEL_ID) --
    # n'importe quel membre de ces canaux pouvait exécuter /ban, /del, /gen, /fbcreate force,
    # etc. Récupère ton ID numérique via @userinfobot sur Telegram, ex: "ADMIN_IDS": [123456789].
    ADMIN_IDS = set(int(x) for x in _secrets.get("ADMIN_IDS", []))
except Exception as _e:
    print(f"ATTENTION: license_secrets.json introuvable ou invalide ({_e}). Cree-le avec API_ID + API_HASH + SECURITY_BOT_TOKEN + ADMIN_IDS.")

if not ADMIN_IDS:
    print("⚠️ ADMIN_IDS vide dans license_secrets.json : par sécurité, AUCUNE commande admin "
          "ne sera acceptée tant que tu n'y as pas ajouté ton ID Telegram (@userinfobot).")

# Le nom EXACT de ton Google Sheet (celui affiché en haut de la page)
SHEET_NAME = "FTMO_BOT_LICENSES" # <--- METS LE VRAI NOM ICI

# ID du canal de sécurité où arrivent les messages (Check Licence)
SECURITY_CHANNEL_ID = -1003872071233

# (#29) ID du canal dédié à la mailbox retour client (bugs/suggestions).
FEEDBACK_CHANNEL_ID = -1004458498840

# (SECURITY_BOT_TOKEN est maintenant chargé depuis license_secrets.json, cf. plus haut --
# UNIQUEMENT sur le VPS, jamais dans l'exe distribué. Réutilisé par le relais feedback (#29)
# -- ce bot doit aussi être admin du canal FEEDBACK_CHANNEL_ID, en plus de SECURITY_CHANNEL_ID.)

# MAPPAGE DES COLONNES
COL_USER = 1       # A: Nom du client
COL_KEY = 2        # B: La clé de licence
COL_STATUS = 3     # C: ACTIVE / BANNED
COL_VER = 4        # D: Version Min
COL_HWID = 5       # E: L'ID du PC
COL_EXP = 6        # F: Date Expiration (JJ/MM/AAAA)
COL_PACK = 7       # G: (#26) Pack / canaux autorisés (ex: "SABR_VIP,PERSO" ou vide = tous)
COL_CONFIG_VER = 8 # H: Config Version min
ROW_CONFIG_VER = 2 # Ligne Config Version min

# ── Package client ────────────────────────────────────────────────────────────
# (#30) Nom du produit distribué au client (exe, ZIP, textes) -- renommé depuis "Bot FTMO"
PRODUCT_NAME = "Kairos"

# (#66) Le nom de l'exe était écrit en dur ici, ce qui obligeait à recopier ce script sur le VPS
# à CHAQUE nouvelle version. Oublier cette étape ne produisait aucune erreur visible : /gen et
# /maj cherchaient l'ancien nom, ne le trouvaient pas, et envoyaient un ZIP sans le programme.
# On détecte désormais le binaire présent dans le dossier -- déposer l'exe suffit.
#
# Le motif est ancré (^...$) pour ne PAS attraper les sauvegardes du type
# "Kairos_V15.0.0.pyinstaller.bak.exe", qui sinon pourraient être distribuées à un client.
EXE_PATTERN = re.compile(rf"^{re.escape(PRODUCT_NAME)}_V(\d+(?:\.\d+){{0,3}})\.exe$", re.IGNORECASE)


def find_bot_exe():
    """Retourne (chemin, nom, version) de l'exe le plus RÉCENT du dossier, ('', '', '') si aucun.

    Le tri se fait sur le numéro de version et non sur la date du fichier : recopier une
    ancienne version sur le VPS lui donnerait une date récente et la ferait passer devant.
    """
    folder = os.path.dirname(os.path.abspath(__file__))
    best = None
    try:
        for name in os.listdir(folder):
            m = EXE_PATTERN.match(name)
            if not m:
                continue
            parts = tuple(int(p) for p in m.group(1).split("."))
            if best is None or parts > best[0]:
                best = (parts, name, m.group(1))
    except Exception as e:
        print(f"⚠️ find_bot_exe : {e}")
    if best is None:
        return "", "", ""
    return os.path.join(folder, best[1]), best[1], best[2]
# Fichier texte d'installation fourni dans chaque ZIP client (cf. generate_client_package)
INSTALL_GUIDE_PATH = os.path.join(os.path.dirname(__file__), "INSTALLATION.txt")

# ── (#7) Packs par client : canaux visibles (logs + dashboard) ─────────────────
# Jeton tapé au /gen   ->   nom interne du canal
VALID_CHANNEL_TOKENS = {
    "SABR_VIP":   "CANAL SABR_VIP",
    "SABR_FREE":  "CANAL SABR_FREE",
    "SABR_COPIE": "CANAL SABR_COPIE",   # ⚠️ canal perso : assignable À LA MAIN uniquement, JAMAIS dans un pack
    "MDT_VIP":    "CANAL MDT_VIP",
    "MDT_FREE":   "CANAL MDT_FREE",
    "SPACE":      "CANAL SPACE",
    "INDODOG":    "CANAL INDODOG",
    "INDO":       "CANAL INDODOG",
    "FTS":        "CANAL FTS",
    "LIGUE":      "CANAL LIGUE",
    "PERSO":      "CANAL PERSO",
}

# Profils prédéfinis (packs). SABR_COPIE volontairement exclu de TOUS les profils.
CHANNEL_PROFILES = {
    "sabr":  ["CANAL SABR_VIP", "CANAL SABR_FREE"],
    "mdt":   ["CANAL MDT_VIP", "CANAL MDT_FREE"],
    "space": ["CANAL SPACE"],
    "indodog": ["CANAL INDODOG"],
    "fts": ["CANAL FTS"],
    "ligue": ["CANAL LIGUE"],
    "perso": ["CANAL PERSO"],
    # 'full' = tout SAUF SABR_COPIE (qui reste réservé à une assignation manuelle)
    "full":  ["CANAL SABR_VIP", "CANAL SABR_FREE", "CANAL MDT_VIP", "CANAL MDT_FREE",
              "CANAL SPACE", "CANAL LIGUE", "CANAL PERSO", "CANAL INDODOG", "CANAL FTS"],
    # 'dev' = version complète : tous les canaux (SABR_COPIE inclus) + validité illimitée d'office
    "dev":   ["CANAL SABR_VIP", "CANAL SABR_FREE", "CANAL SABR_COPIE",
              "CANAL MDT_VIP", "CANAL MDT_FREE",
              "CANAL SPACE", "CANAL LIGUE", "CANAL PERSO", "CANAL INDODOG", "CANAL FTS"],
}


def resolve_channels(arg):
    """(#7) Convertit l'argument /gen|/channels en liste de canaux internes.
    arg = nom de profil (ex 'sabr') OU jetons séparés par des virgules (ex 'SABR_VIP,PERSO').
    Retourne (channels: list, error: str|None). channels=[] => pas de filtre (tout visible).
    (#39) channels=['NONE'] => sentinel explicite "aucun canal" (pack suspendu), distinct de []."""
    if not arg:
        return [], None
    key = arg.strip().lower()
    if key in ("none", "aucun", "rien"):
        return ["NONE"], None
    if key in CHANNEL_PROFILES:
        return list(CHANNEL_PROFILES[key]), None
    out, unknown = [], []
    for raw in arg.split(","):
        tok = raw.strip().upper()
        if not tok:
            continue
        if tok in VALID_CHANNEL_TOKENS:
            ch = VALID_CHANNEL_TOKENS[tok]
            if ch not in out:
                out.append(ch)
        else:
            unknown.append(raw.strip())
    if unknown:
        valid = ", ".join(sorted(VALID_CHANNEL_TOKENS.keys()))
        profs = ", ".join(sorted(CHANNEL_PROFILES.keys()))
        return None, (f"Jeton(s) inconnu(s) : {', '.join(unknown)}.\n"
                      f"Profils dispo : {profs}\nCanaux dispo : {valid}")
    return out, None


def channels_to_pack_text(channels):
    """(#26) Liste de canaux internes -> texte stocké en colonne G (ex: 'SABR_VIP,PERSO')."""
    return ",".join(c.replace("CANAL ", "") for c in (channels or []))


SETTINGS_TEMPLATE = {
    "USER_LICENSE_KEY": "",
    "FIREBASE_EMAIL": "",
    "FIREBASE_PASSWORD": "",
    "TELEGRAM_API_ID": 0,
    "TELEGRAM_API_HASH": "",
    "TELEGRAM_BOT_TOKEN": "",
    "TELEGRAM_DESTINATION_ID": 0,
    "MT5_PATH": "C:\\Program Files\\MetaTrader 5\\terminal64.exe",
    "RISK_PERCENT_GOLD": 0.008,
    "RISK_PERCENT_OTHER": 0.008,
    "RISK_PERCENT_VIP": 0.008,
    "RISK_PERCENT_FREE": 0.008,
    "RISK_PERCENT_SPACE": 0.008,
    "FIXED_LOT_INDODOG": 0.10,
    "FIXED_LOT_FTS": 0.10,
    "RISK_PERCENT_LIGUE": 0.008,
    "RISK_PERCENT_PERSO": 0.008,
    "RISK_PERCENT_INDICES": 0.008,
    "RISK_PERCENT_MDT_VIP": 0.008,
    "RISK_PERCENT_MDT_FREE": 0.008,
    "FIXED_LOT_SABR_FREE": 0.06,
    "FIXED_LOT_SABR_VIP": 0.06,
    "MAX_DAILY_DRAWDOWN": 0.05,
    "TRAILING_STOP_PIPS": 85,
    "SOFT_SL_PERCENT": 0.3,
    "SABR_TRAILING_ACTIVE": False,
    "SABR_TRAILING_PIPS": 100,
    "BE_OFFSET_PIPS": 0.0,
    "SABR_ANTI_DOUBLON_ACTIVE": True,
    "SABR_FORWARD_ACTIVE": True,
    "MUTED_CHANNELS": [],
    "ALLOWED_CHANNELS": [],
    "CHANNEL_TP1_PCT": {},
    "TIME_PRIME_RANGES": [[8, 17]],
    "TIME_OFF_PEAK_FACTOR": 0.5,
    "DAY_RISK_FACTORS": {"0": 1.0, "1": 1.0, "2": 1.0, "3": 1.0, "4": 1.0, "5": 1.0, "6": 1.0},
    # (#30) "dev" = console detaillee (reserve a l'admin) ; "client" = console epuree
    # (logo + statut, tout continue d'etre journalise dans kairos.log).
    "DISPLAY_MODE": "client",
}

def generate_client_package(client_name: str, key: str, fb_email: str, fb_password: str,
                             allowed_channels=None, is_dev: bool = False) -> tuple:
    """
    Génère un ZIP en mémoire contenant :
    - settings.json pré-rempli (clé + Firebase + valeurs par défaut)
    - l'exe détecté dans le dossier (si présent sur le VPS), cf. find_bot_exe()
    - INSTALLATION.txt (si présent à côté de ce script)

    allowed_channels : (#7) liste blanche des canaux visibles du client (vide = tous).
    is_dev : (#30) True uniquement pour le profil 'dev' -- écrit DISPLAY_MODE="dev"
    (console détaillée). Tous les autres packs reçoivent la console épurée par défaut.

    Retourne (zip_bytes: io.BytesIO, filename: str, exe_included: bool)
    """
    settings = dict(SETTINGS_TEMPLATE)
    settings["USER_LICENSE_KEY"] = key
    settings["FIREBASE_EMAIL"]   = fb_email or ""
    settings["FIREBASE_PASSWORD"] = fb_password or ""
    settings["ALLOWED_CHANNELS"] = list(allowed_channels or [])
    settings["DISPLAY_MODE"] = "dev" if is_dev else "client"

    settings_bytes = json.dumps(settings, indent=4, ensure_ascii=False).encode("utf-8")

    buf = io.BytesIO()
    exe_included = False
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("settings.json", settings_bytes)
        exe_path, exe_name, _ = find_bot_exe()
        if exe_path and os.path.isfile(exe_path):
            zf.write(exe_path, arcname=exe_name)
            exe_included = True
        if os.path.isfile(INSTALL_GUIDE_PATH):
            zf.write(INSTALL_GUIDE_PATH, arcname="INSTALLATION.txt")

    buf.seek(0)

    safe_name = re.sub(r"[^a-zA-Z0-9_-]", "", client_name) or "client"
    filename = f"{PRODUCT_NAME}_{safe_name}.zip"
    buf.name = filename  # Telethon utilise .name pour nommer un fichier en mémoire
    return buf, filename, exe_included


def exe_version_from_filename():
    """(#64) Version de l'exe présent dans le dossier ("15.2.1"), "" si aucun.
    Source unique de la version distribuée : le nom du binaire, pas une constante en double."""
    return find_bot_exe()[2]


UPDATE_NOTICE = """MISE A JOUR DE {product}
=========================================

Cette archive ne contient QUE le programme, volontairement.

A FAIRE
-------
1. Ferme completement {product} (la fenetre noire doit etre fermee).
2. Ouvre le dossier ou tu l'avais installe.
3. Remplace l'ancien {old_hint} par le {exe} de cette archive.
   -> Tu peux supprimer l'ancien fichier .exe une fois la mise a jour verifiee.
4. Relance le nouveau {exe}.

NE TOUCHE A RIEN D'AUTRE
------------------------
Garde tous les autres fichiers de ton dossier, en particulier :
  - settings.json               (ta cle de licence et tes reglages)
  - ftmo_bot_session.session    (ta connexion Telegram)
  - bot_session.session         (ton bot Telegram)
  - trades_detailed.csv         (ton historique de trades)

Si tu les supprimes, il faudra refaire l'appairage Telegram (numero, SMS, code PIN)
et tu perdras tes statistiques.

SI WINDOWS AFFICHE UN AVERTISSEMENT
-----------------------------------
"Windows a protege votre ordinateur" -> clique "Informations complementaires",
puis "Executer quand meme". C'est normal pour chaque nouvelle version.
"""


def generate_update_package(client_name: str) -> tuple:
    """(#64) ZIP de MISE A JOUR : l'exe seul, sans settings.json.

    Contrairement a generate_client_package(), on n'inclut surtout pas settings.json : le
    client en a deja un, contenant sa cle, ses identifiants Firebase, ses API Telegram et ses
    reglages. L'ecraser le forcerait a tout reconfigurer et a refaire l'appairage Telegram.

    Retourne (zip_bytes, filename, exe_included)."""
    exe_path, exe_name, ver = find_bot_exe()
    buf = io.BytesIO()
    exe_included = False
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        if exe_path and os.path.isfile(exe_path):
            zf.write(exe_path, arcname=exe_name)
            exe_included = True
        zf.writestr("LIRE_MOI_MAJ.txt", UPDATE_NOTICE.format(
            product=PRODUCT_NAME, exe=exe_name or f"{PRODUCT_NAME}_V....exe",
            old_hint=f"{PRODUCT_NAME}_V....exe").encode("utf-8"))
    buf.seek(0)
    safe_name = re.sub(r"[^a-zA-Z0-9_-]", "", client_name) or "client"
    ver = ver or "MAJ"
    filename = f"{PRODUCT_NAME}_MAJ_v{ver}_{safe_name}.zip"
    buf.name = filename
    return buf, filename, exe_included


def set_client_version(key, new_ver):
    """(#64) Fixe le ver_min d'UN SEUL client (Sheet + Firestore), sans toucher aux autres.

    Le pendant unitaire de push_version_to_all() : sert a bloquer l'ancien exe d'un client
    precis juste apres lui avoir envoye sa mise a jour."""
    try:
        sheet = get_sheet()
        cell = sheet.find(key)
        if not cell: return False, "Clé introuvable."
        user = sheet.cell(cell.row, COL_USER).value or ""
        status = sheet.cell(cell.row, COL_STATUS).value or "ACTIVE"
        sheet.update_cell(cell.row, COL_VER, sheet_text(new_ver))
        # Le bot client ne lit ver_min QUE depuis Firestore -- sans cette ligne, la Sheet
        # afficherait la nouvelle version sans qu'aucun blocage soit réellement appliqué.
        fb_sync_license(key, user, status, new_ver)
        return True, user
    except Exception as e:
        return False, str(e)


def find_license_row(key):
    """Retourne (nom_client, statut) pour une clé, ou (None, None) si introuvable."""
    try:
        sheet = get_sheet()
        cell = sheet.find(key)
        if not cell: return None, None
        return (sheet.cell(cell.row, COL_USER).value or "",
                sheet.cell(cell.row, COL_STATUS).value or "")
    except Exception:
        return None, None


def escape_md(text):
    """(#40) Échappe les caractères spéciaux Markdown (Telethon parse par défaut) avant
    d'injecter du texte non fiable (message client) dans un event.reply() -- sinon un *,
    _ ou ` dans un message client peut casser le formatage ou faire échouer l'envoi entier."""
    text = str(text or "")
    for ch in ('\\', '`', '*', '_', '['):
        text = text.replace(ch, '\\' + ch)
    return text


VERSION_RE = re.compile(r"^\d+(?:\.\d+){0,3}$")


def sheet_text(value):
    """(#63) Force Google Sheets à stocker la valeur comme du TEXTE.

    gspread écrit en valueInputOption=USER_ENTERED, donc Sheets interprète la saisie comme si
    elle était tapée à la main : "15.1.0" est lu comme une date (15 janvier de l'an 0) et
    stocké comme tel, puis réaffiché "15.1.2000" dans une locale JJ.MM.AAAA. L'apostrophe de
    tête est la convention Sheets pour « ceci est du texte » ; elle n'apparaît ni dans la
    cellule ni à la relecture."""
    s = str(value or "").strip()
    return ("'" + s) if s else s


def parse_version_cell(raw, default="7.2"):
    """(#63) Valide une version relue de la Sheet. Retourne (version, erreur_ou_None).

    Une cellule convertie en date par Sheets se relit "15.1.2000" : la forme reste valide, mais
    aucun numéro de version n'a un segment >= 1900. Sans ce garde-fou, cette valeur partait dans
    le ver_min Firestore d'un nouveau client, qui se voyait alors refusé au démarrage pour
    version obsolète (0 < 2000)."""
    s = str(raw or "").strip().lstrip("'")
    if not s:
        return default, None
    if not VERSION_RE.match(s):
        return default, f"format inattendu : {s!r}"
    if any(int(p) >= 1900 for p in s.split(".")):
        return default, f"valeur convertie en date par Google Sheets : {s!r}"
    return s, None


def sheet_safe(value):
    """(#39) Préfixe d'une apostrophe toute valeur commençant par =/+/-/@ avant écriture dans
    la Sheet -- ces caractères sont interprétés comme le début d'une formule par Google Sheets.
    Le PC-ID/HWID vient in fine du client (bot distribué), donc pas fiable à 100%."""
    s = str(value or "")
    if s and s[0] in ("=", "+", "-", "@"):
        return "'" + s
    return s


# (#51) Sérialise toutes les opérations d'écriture Sheet (find -> update_cell/delete_rows).
# ban_license/unban_license/update_license_time/reset_hwid/set_expired/update_sheet_pack
# font tous un sheet.find(key) puis, après un aller-retour réseau, un sheet.update_cell()
# sur cell.row -- sans lock, une suppression concurrente (/del) décale les lignes et une
# autre commande déjà en vol écrit sur la mauvaise ligne (capturée avant la suppression).
SHEET_WRITE_LOCK = threading.Lock()


async def sheet_write(func, *args, **kwargs):
    """Dispatch une fonction d'écriture Sheet sur un thread, sous SHEET_WRITE_LOCK."""
    def _locked():
        with SHEET_WRITE_LOCK:
            return func(*args, **kwargs)
    return await asyncio.to_thread(_locked)


# ==========================================
# 🕵️ HISTORIQUE HWID PAR CLÉ (#50)
# ==========================================
# Fichier local simple (pas besoin de Firestore ici, c'est un journal admin) : garde une
# trace persistante des connexions/conflits/resets par clé, pour pouvoir répondre à "cette
# clé a-t-elle tourné sur plusieurs machines dans le temps ?" -- avant ce fix, seule la
# DERNIÈRE valeur HWID était connue (Sheet/Firestore), la preuve d'un partage disparaissait
# dès la connexion suivante.
HWID_HISTORY_PATH = "hwid_history.json"
HWID_HISTORY_LOCK = threading.Lock()


def _load_hwid_history():
    try:
        with open(HWID_HISTORY_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def record_hwid_event(key, event_type, detail=""):
    """Ajoute un événement à l'historique de la clé (connection/theft/reset/ban/unban)."""
    try:
        with HWID_HISTORY_LOCK:
            history = _load_hwid_history()
            history.setdefault(key, []).append({
                "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "type": event_type,
                "detail": detail,
            })
            # On garde les 50 derniers événements par clé (fichier local, pas la peine de
            # conserver un historique illimité)
            history[key] = history[key][-50:]
            with open(HWID_HISTORY_PATH, "w", encoding="utf-8") as f:
                json.dump(history, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"⚠️ record_hwid_event error: {e}")


def get_hwid_history(key, limit=15):
    history = _load_hwid_history()
    events = history.get(key, [])
    return events[-limit:]


# ==========================================
# 🔌 CONNEXION GOOGLE SHEET
# ==========================================
scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
# (#42) Valide service_account.json dès l'import (échec rapide si absent/invalide) --
# get_sheet() ci-dessous recrée son propre client à chaque appel, celui-ci n'est jamais
# réutilisé (l'ancien client_google, mort, a été retiré).
creds = ServiceAccountCredentials.from_json_keyfile_name("service_account.json", scope)

def get_sheet():
    """Reconnexion robuste à chaque appel"""
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    creds = ServiceAccountCredentials.from_json_keyfile_name("service_account.json", scope)
    client = gspread.authorize(creds) # On recrée le client frais
    return client.open(SHEET_NAME).sheet1

def update_sheet(license_key, pc_id):
    try:
        sheet = get_sheet()
        cell = sheet.find(license_key)
        
        if cell:
            print(f"✅ Clé trouvée ligne {cell.row}")
            current_hwid = sheet.cell(cell.row, COL_HWID).value
            
            # Vérification Expiration (Nouveau)
            exp_date_str = sheet.cell(cell.row, COL_EXP).value
            if exp_date_str:
                try:
                    exp_date = datetime.strptime(exp_date_str, "%d/%m/%Y")
                    # On ajoute un jour pour que ça expire le lendemain à 00:00 (optionnel, mais sympa)
                    exp_date = exp_date + timedelta(days=1) 
                    
                    if datetime.now() > exp_date:
                        # On récupère le statut actuel pour ne pas écrire pour rien
                        current_status = sheet.cell(cell.row, COL_STATUS).value

                        # Si le statut est encore ACTIF, on le passe en EXPIRED
                        if current_status == "ACTIVE":
                             sheet.update_cell(cell.row, COL_STATUS, "EXPIRED")
                             # (#59) Manquait : contrairement à set_expired() (déclenché par le
                             # worker via "SIGNAL EXPIRATION"), cette détection passive (au
                             # moment d'une connexion) ne synchronisait QUE la Sheet -- si le
                             # worker proactif est en retard/en panne, licenses/{key}.status
                             # restait "ACTIVE" en Firestore et le compte Firebase/dashboard du
                             # client restait pleinement fonctionnel malgré l'expiration.
                             current_user = sheet.cell(cell.row, COL_USER).value or ""
                             fb_sync_license(license_key, current_user, "EXPIRED")
                             fb_disable_firebase_user(license_key)
                             fb_disable_dashboard(license_key)
                             print(f"⌛ Clé {license_key} temps écoulé -> EXPIRED (Sheet + Firestore + Firebase)")

                        return "EXPIRED"
                except: pass # Si format date invalide, on ignore

            if not current_hwid or current_hwid == "":
                sheet.update_cell(cell.row, COL_HWID, sheet_safe(pc_id))
                fb_sync_license(license_key, sheet.cell(cell.row, COL_USER).value or "", sheet.cell(cell.row, COL_STATUS).value or "ACTIVE", hwid=pc_id)
                record_hwid_event(license_key, "connection", f"1ere inscription : {pc_id}")
                print(f"💾 SUCCÈS : HWID {pc_id} enregistré pour {license_key}")
                return "NEW_REGISTER"

            elif current_hwid == pc_id:
                print("ℹ️ Déjà enregistré (Même PC).")
                return "SAME"

            else:
                record_hwid_event(license_key, "theft", f"attendu={current_hwid} recu={pc_id}")
                print(f"⚠️ CONFLIT ID !")
                return "CONFLICT"
        else:
            return "NOT_FOUND"

    except Exception as e:
        print(f"❌ Erreur Sheet : {e}")
        return "ERROR"
    
def update_sheet_pack(key, allowed_channels):
    """(#26) Écrit le pack en colonne G + Firestore (licenses/{key}.allowed_channels).

    N'écrit JAMAIS hwid/status/ver_min/date_fin -- la Sheet n'est PAS une source fiable
    pour ces champs (register_hwid() écrit le HWID directement en Firestore, jamais dans
    la Sheet, et /push_ver met à jour la Sheet sans resynchroniser Firestore -- relire ces
    colonnes ici et les repousser via fb_sync_license écraserait Firestore avec des valeurs
    périmées/vides -- c'est exactement ce qui a cassé un HWID valide la première fois)."""
    try:
        sheet = get_sheet()
        cell = sheet.find(key)
        if not cell:
            return False
        sheet.update_cell(cell.row, COL_PACK, channels_to_pack_text(allowed_channels))
        if FIREBASE_ENABLED and _fb_db:
            _fb_db.collection("licenses").document(key).set(
                {"allowed_channels": list(allowed_channels or [])}, merge=True)
        return True
    except Exception as e:
        print(f"⚠️ update_sheet_pack error: {e}")
        return False


def get_current_default_version():
    """Lit la version par défaut dans la case H2"""
    try:
        sheet = get_sheet()
        # On lit la ligne 2, colonne 8 (H2)
        val = sheet.cell(ROW_CONFIG_VER, COL_CONFIG_VER).value
        # Si vide, on renvoie une valeur de sécurité
        if not val: return "7.2"
        version, err = parse_version_cell(val)
        if err:
            print(f"⚠️ VERSION_MIN_DEFAUT (H2) inutilisable -- {err}. Repli sur {version}. "
                  f"Relance `/set_ver <version>` pour réparer la case.")
        return version
    except: return "7.2"

def set_default_version(new_ver):
    """Change la version par défaut dans H2"""
    try:
        sheet = get_sheet()
        # On écrit dans H1 le titre pour que tu t'y retrouves visuellement
        sheet.update_cell(1, COL_CONFIG_VER, "VERSION_MIN_DEFAUT")
        # On écrit la version dans H2, en TEXTE (sinon Sheets la convertit en date)
        sheet.update_cell(ROW_CONFIG_VER, COL_CONFIG_VER, sheet_text(new_ver))
        return True
    except Exception as e: return False
    
# --- FONCTIONS UTILITAIRES ---

def add_license_row(user, key, days=None, allowed_channels=None):
    """Version modifiée pour lire la version dynamique"""
    try:
        sheet = get_sheet()
        if sheet.find(key): return False, "Clé existe déjà !", None, None  # (#34) 4-tuple, comme tous les autres retours

        # 1. On récupère la version depuis le Sheet (H2)
        # (#63) Même garde-fou que get_current_default_version() : une case H2 convertie en date
        # par Sheets partait telle quelle dans le ver_min Firestore du nouveau client, qui se
        # retrouvait bloqué au démarrage pour "version obsolète".
        version_to_use, ver_err = parse_version_cell(sheet.cell(ROW_CONFIG_VER, COL_CONFIG_VER).value)
        if ver_err:
            print(f"⚠️ /gen : VERSION_MIN_DEFAUT (H2) inutilisable -- {ver_err}. "
                  f"Repli sur {version_to_use}.")

        # 2. Calcul date fin
        date_fin = ""
        if days:
            fin = datetime.now() + timedelta(days=int(days))
            date_fin = fin.strftime("%d/%m/%Y")

        # 3. On inscrit la ligne avec la version dynamique + le pack (#26, colonne G)
        pack_text = channels_to_pack_text(allowed_channels)
        # (#63) PAS de sheet_text() ici, contrairement aux update_cell() : append_row écrit en
        # valueInputOption=RAW, qui stocke la valeur telle quelle sans jamais l'interpréter --
        # donc aucune conversion en date à empêcher, et l'apostrophe se retrouvait affichée
        # littéralement dans la Sheet ("'15.2.1"). Le mode est passé explicitement pour ne pas
        # dépendre du défaut de gspread, qui diffère justement d'une méthode à l'autre.
        row_data = [user, key, "ACTIVE", version_to_use, "", date_fin, pack_text]
        sheet.append_row(row_data, value_input_option="RAW")
        fb_sync_license(key, user, "ACTIVE", version_to_use, "", date_fin, allowed_channels=allowed_channels or [])
        # 4. Création du compte Firebase Auth pour ce client (clé neuve -> force sans effet)
        fb_email, fb_password, _ = fb_create_firebase_user(key, force=True)
        msg_fin = f" (Expire le {date_fin})" if date_fin else " (Illimité)"
        return True, f"Licence ajoutée [v{version_to_use}]{msg_fin}.", fb_email, fb_password
    except Exception as e: return False, str(e), None, None

def delete_license_row(key):
    try:
        sheet = get_sheet()
        cell = sheet.find(key)
        if not cell: return False, "Clé introuvable."
        sheet.delete_rows(cell.row)
        return True, "Licence supprimée."
    except Exception as e: return False, str(e)

def reset_hwid(key):
    try:
        sheet = get_sheet()
        cell = sheet.find(key)
        if not cell: return False, "Clé introuvable."
        sheet.update_cell(cell.row, COL_HWID, "")
        # Efface aussi le HWID dans Firestore (sinon le bot resterait bloqué « autre PC »)
        if FIREBASE_ENABLED and _fb_db:
            _fb_db.collection("licenses").document(key).set({"hwid": ""}, merge=True)
        return True, "HWID Reset OK (Sheet + Firestore)."
    except Exception as e: return False, str(e)

def ban_license(key):
    try:
        sheet = get_sheet()
        cell = sheet.find(key)
        if not cell: return False, "Clé introuvable."
        current_user = sheet.cell(cell.row, COL_USER).value or ""
        sheet.update_cell(cell.row, COL_STATUS, "BANNED")
        fb_sync_license(key, current_user, "BANNED")
        fb_disable_firebase_user(key)
        fb_disable_dashboard(key)
        return True, "Licence BANNIE."
    except Exception as e: return False, str(e)

def unban_license(key):
    """(#45) Débannit sans toucher à la date d'expiration -- contrairement à /time, qui
    réactive aussi mais force une nouvelle durée. Ne fait rien si la clé n'est pas bannie."""
    try:
        sheet = get_sheet()
        cell = sheet.find(key)
        if not cell: return False, "Clé introuvable."
        current_status = sheet.cell(cell.row, COL_STATUS).value or ""
        if current_status != "BANNED":
            return False, f"Cette clé n'est pas bannie (statut actuel : {current_status})."
        current_user = sheet.cell(cell.row, COL_USER).value or ""
        sheet.update_cell(cell.row, COL_STATUS, "ACTIVE")
        fb_sync_license(key, current_user, "ACTIVE")
        fb_enable_firebase_user(key)
        fb_enable_dashboard(key)
        return True, "Licence débannie (ACTIVE)."
    except Exception as e: return False, str(e)

def get_license_status(key):
    """Retourne le statut brut (ACTIVE/BANNED/EXPIRED) d'une clé, ou None si introuvable."""
    try:
        sheet = get_sheet()
        cell = sheet.find(key)
        if not cell:
            return None
        return sheet.cell(cell.row, COL_STATUS).value or ""
    except Exception:
        return None


def get_license_info(key):
    try:
        sheet = get_sheet()
        cell = sheet.find(key)
        if not cell: return False, "Clé introuvable."
        
        row_vals = sheet.row_values(cell.row)
        # On complète la liste avec des vides si elle est trop courte
        while len(row_vals) < 7:
            row_vals.append("")

        info = (f"👤 **{row_vals[0]}**\n"
                f"🔑 `{row_vals[1]}`\n"
                f"📅 Statut: {row_vals[2]}\n"
                # (#42) La Sheet n'est qu'un miroir du HWID, resynchronisé au plus une fois/heure
                # (security_events_relay) -- Firestore (licenses/{key}.hwid) fait toujours foi.
                f"💻 ID (Sheet, ≤1h de retard) : `{row_vals[4]}`\n"
                f"📦 Pack: {row_vals[6] if row_vals[6] else 'TOUS les canaux'}\n")

        if row_vals[5]:
            info += f"⏳ Expire: {row_vals[5]}"

        return True, info
    except Exception as e: return False, str(e)

def update_license_time(key, value, force=False):
    """
    Modifie la date ET réactive la licence.
    value peut être :
    - "30" (ajoute 30 jours à partir d'aujourd'hui)
    - "01/01/2026" (met cette date précise)
    - "unlimited" (efface la date)

    (#40) Si la clé est actuellement BANNIE, refuse de la réactiver silencieusement --
    une faute de frappe/mix-up sur la clé ne doit pas lever un ban par accident.
    force=True pour réactiver volontairement une clé bannie.
    """
    try:
        sheet = get_sheet()
        cell = sheet.find(key)
        if not cell: return False, "Clé introuvable."

        current_status = sheet.cell(cell.row, COL_STATUS).value or ""
        if current_status == "BANNED" and not force:
            return False, (f"Cette clé est actuellement BANNIE. Utilise `/time {key} {value} force` "
                            f"si tu veux VRAIMENT la réactiver.")

        new_date_str = ""
        
        # Cas 1 : Nombre de jours (ex: "30")
        if value.isdigit():
            future = datetime.now() + timedelta(days=int(value))
            new_date_str = future.strftime("%d/%m/%Y")
            
        # Cas 2 : Illimité (ex: "vie", "unlimited")
        elif value.lower() in ["unlimited", "vie", "illimité", "infini"]:
            new_date_str = ""
            
        # Cas 3 : Date spécifique (ex: "25/12/2025")
        else:
            # On vérifie juste si ça ressemble à une date pour éviter les bêtises
            try:
                datetime.strptime(value, "%d/%m/%Y")
                new_date_str = value
            except:
                return False, "Format invalide. Utilise '30' (jours) ou '25/12/2025'."

        # Mise à jour DATE (Col 6)
        current_user = sheet.cell(cell.row, COL_USER).value or ""
        sheet.update_cell(cell.row, COL_EXP, new_date_str)
        sheet.update_cell(cell.row, COL_STATUS, "ACTIVE")
        fb_sync_license(key, current_user, "ACTIVE", date_fin=new_date_str)
        fb_enable_firebase_user(key)
        fb_enable_dashboard(key)
        return True, new_date_str

    except Exception as e: return False, str(e)

def push_version_to_all(new_ver):
    """(#51, #61) Migre tous les clients ACTIFS vers new_ver (Sheet + Firestore), en une seule
    fonction dispatchée sous SHEET_WRITE_LOCK -- avant, chaque update_cell() partait
    individuellement via asyncio.to_thread sans lock, et une erreur en cours de boucle
    (quota/timeout Sheets) perdait le compteur de progression : l'admin ne savait jamais
    combien de clients avaient réellement basculé avant l'échec. Retourne (count, errors)."""
    sheet = get_sheet()
    all_rows = sheet.get_all_values()
    count = 0
    errors = []
    for i, row in enumerate(all_rows[1:], start=2):
        if len(row) > 2 and row[2] == "ACTIVE":
            key = row[1] if len(row) > 1 else ""
            user = row[0] if len(row) > 0 else ""
            try:
                sheet.update_cell(i, COL_VER, sheet_text(new_ver))
                # (#33) Le bot client ne lit ver_min QUE depuis Firestore (jamais la Sheet) --
                # sans cette ligne, la Sheet affichait la nouvelle version mais aucun client
                # n'était réellement forcé de mettre à jour.
                if key:
                    fb_sync_license(key, user, "ACTIVE", new_ver)
                count += 1
            except Exception as e:
                errors.append(f"{key or f'ligne {i}'}: {e}")
    return count, errors


def set_expired(key):
    """Force le statut EXPIRED pour une clé donnée"""
    try:
        sheet = get_sheet()
        cell = sheet.find(key)
        if not cell: return False
        # On écrit EXPIRED dans la colonne statut (3)
        sheet.update_cell(cell.row, COL_STATUS, "EXPIRED")
        # (#37) Manquait : sans ça, licenses/{key}.status restait "ACTIVE" en Firestore -- pour
        # une licence "vie"/illimitée (pas de date_fin), le bot client ne vérifie la date que
        # si date_fin est renseigné, donc il continuait de tourner malgré l'expiration forcée.
        current_user = sheet.cell(cell.row, COL_USER).value or ""
        fb_sync_license(key, current_user, "EXPIRED")
        fb_disable_firebase_user(key)
        fb_disable_dashboard(key)
        return True
    except: return False

# ==========================================
# 👂 ÉCOUTE TELEGRAM
# ==========================================
client_tg = TelegramClient('admin_session', API_ID, API_HASH)

def audit_log(sender_id, text):
    """(#45) Journal simple des commandes admin traitées -- qui a tapé quoi, quand."""
    try:
        with open("admin_audit.log", "a", encoding="utf-8") as f:
            f.write(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | {sender_id} | {text}\n")
    except Exception as e:
        print(f"⚠️ audit_log: {e}")


@client_tg.on(events.NewMessage(chats=[SECURITY_CHANNEL_ID, FEEDBACK_CHANNEL_ID]))  # (#29) /vu depuis le canal feedback
async def main_handler(event):
    msg = event.raw_text.strip()
    # (#42) client_tg est le compte PERSONNEL de l'admin (pas un bot séparé) -- un `incoming=True`
    # sur le décorateur bloquait donc AUSSI les vraies commandes tapées par l'admin depuis ce même
    # compte (elles sont "sortantes" de son point de vue), pas seulement les réponses automatiques
    # du bot (event.reply plus bas). Le bot n'envoie lui-même jamais de message commençant par "/",
    # donc un message sortant qui commence par "/" est forcément tapé par un humain : on ne
    # court-circuite que le reste (messages automatiques du bot rejoués en boucle).
    if event.out and not msg.startswith("/"):
        return
    if msg.startswith("/"):
        audit_log(event.sender_id, msg)
        # (#48) Avant ce fix, seul le CANAL était vérifié (décorateur ci-dessus) -- n'importe
        # quel membre de SECURITY_CHANNEL_ID/FEEDBACK_CHANNEL_ID pouvait exécuter TOUTES les
        # commandes admin. Ne bloque QUE les commandes ("/..."), jamais les messages
        # automatiques du bot lui-même (CONNEXION RÉUSSIE, SIGNAL EXPIRATION, plus bas).
        if event.sender_id not in ADMIN_IDS:
            print(f"⛔ Commande refusée (sender_id {event.sender_id} non autorisé) : {msg}")
            return

    # --- 1. DETECTION CONNEXION AUTOMATIQUE ---
    if "CONNEXION RÉUSSIE" in msg:
        try:
            print("\n📨 Nouveau login détecté...")

            key_match = re.search(r"🔑 Clé:\s*[`]?([^`\n]+)[`]?", msg)
            hwid_match = re.search(r"💻 PC ID:\s*[`]?([^`\n]+)[`]?", msg)

            if key_match and hwid_match:
                licence = key_match.group(1).strip()
                hwid = hwid_match.group(1).strip()

                print(f"🔍 Traitement : {licence} | ID: {hwid}")

                # Appel ASYNC bloquant pour ne pas figer le bot
                result = await sheet_write(update_sheet, licence, hwid)

                if result == "NEW_REGISTER":
                    await event.reply(f"🔒 **LICENCE VERROUILLÉE**\nAppareil lié.\n🔑 Clé : `{licence}`")
                elif result == "CONFLICT":
                    # (#50) Message enrichi -- avant, ni la clé ni le HWID n'étaient mentionnés,
                    # obligeant à remonter l'historique du canal pour identifier de quelle clé
                    # il s'agissait.
                    await event.reply(
                        f"🚨 **ALERTE VOL**\nID non reconnu !\n"
                        f"🔑 Clé : `{licence}`\n💻 Nouveau PC ID : `{hwid}`\n\n"
                        f"`/history {licence}` pour voir l'historique de cette clé."
                    )
                elif result == "EXPIRED":
                    await event.reply(f"⛔ **LICENCE EXPIRÉE**\nAccès refusé.")
                elif result == "NOT_FOUND":
                    print(f"Clé inconnue: {licence}")
        except Exception as e:
            print(f"⚠️ Erreur détection connexion: {e}")

    # --- 2. GESTION DES COMMANDES ADMIN ---
    
    # A. Générateur de Clé (/gen User [Jours] [profil|canaux])
    elif msg.lower().startswith("/gen"):
        try:
            parts = msg.split()
            if len(parts) < 2:
                await event.reply("❌ Usage: `/gen Prenom [jours] [profil|canaux]`\nEx: `/gen Pierre 30 sabr` ou `/gen Pierre 30 SABR_VIP,PERSO`")
                return

            user = parts[1]
            # (#7) parsing : 2e mot = jours si nombre, sinon c'est le pack (validité illimitée)
            rest = parts[2:]
            days = None
            chan_parts = rest
            if rest and rest[0].isdigit():
                days = rest[0]
                chan_parts = rest[1:]
            # (#42) join sur "," (pas "") -- sinon "SABR_VIP PERSO" (espace, sans virgule, faute de
            # frappe courante) s'écrasait silencieusement en un jeton bogus "SABR_VIPPERSO".
            # Une virgule double ("SABR_VIP, PERSO" -> "SABR_VIP,,PERSO") reste sans risque :
            # resolve_channels() ignore les jetons vides entre deux virgules.
            chan_arg = ",".join(chan_parts).strip()
            # (#13) profil 'dev' → validité illimitée forcée
            is_dev = chan_arg.strip().lower() == "dev"
            if is_dev:
                days = None
            allowed, ch_err = resolve_channels(chan_arg)
            if ch_err:
                await event.reply(f"❌ {ch_err}")
                return
            pack_txt = ("🛠️ DEV (tous canaux + illimitée)" if is_dev
                        else (", ".join(c.replace("CANAL ", "") for c in allowed) if allowed else "TOUS les canaux"))

            # (#61) secrets.token_hex (déjà utilisé pour les mots de passe Firebase) au lieu de
            # concaténer des fragments d'UUIDv4 -- le format UUIDv4 impose un nibble de version
            # fixe dans un des groupes utilisés, réduisant l'entropie réelle de 32 à 28 bits.
            new_key = f"FTMO-{secrets.token_hex(4).upper()}"

            await event.reply("⏳ Création de la licence et du package client...")
            success, info, fb_email, fb_password = await sheet_write(add_license_row, user, new_key, days, allowed)

            if success:
                # Génération du ZIP
                zip_buf, zip_name, exe_included = generate_client_package(
                    user, new_key, fb_email, fb_password, allowed_channels=allowed, is_dev=is_dev)

                recap = (f"✅ **PACKAGE CLIENT GÉNÉRÉ**\n"
                         f"👤 Client : {user}\n"
                         f"🔑 Clé : `{new_key}`\n"
                         f"📦 Pack (canaux visibles) : {pack_txt}\n"
                         f"🖥️ Console : {'détaillée (dev)' if is_dev else 'épurée (client)'}\n"
                         f"📝 {info}\n\n"
                         f"📦 Le ZIP contient :\n"
                         f"  • `settings.json` pré-rempli\n"
                         f"{'  • `' + find_bot_exe()[1] + '`' if exe_included else '  ⚠️ AUCUN exe trouvé sur le VPS : dépose un `' + PRODUCT_NAME + '_Vx.y.z.exe` à côté du bot de licence'}\n"
                         f"  • `INSTALLATION.txt`\n\n"
                         f"📋 **Ce que le client doit remplir dans settings.json :**\n"
                         f"  • `TELEGRAM_API_ID` et `TELEGRAM_API_HASH` → my.telegram.org\n"
                         f"  • `TELEGRAM_BOT_TOKEN` → BotFather `/newbot`")

                await event.reply(recap)
                await event.client.send_file(
                    event.chat_id,
                    zip_buf,
                    attributes=[DocumentAttributeFilename(zip_name)],
                    force_document=True,
                    caption=f"📦 Package prêt pour {user}"
                )
            else:
                await event.reply(f"❌ Erreur: {info}")
            
        except Exception as e: await event.reply(f"❌ Crash: {e}")

    # A-ter. (#64) Paquet de MISE A JOUR pour un client existant (/maj CLE [force])
    elif msg.lower().startswith("/maj"):
        try:
            parts = msg.split()
            if len(parts) < 2:
                await event.reply("❌ Usage: `/maj CLE [force]`\n"
                                  "Envoie le ZIP de mise à jour (exe seul, sans settings.json).\n"
                                  "`force` bloque en plus son ancienne version au démarrage.")
                return

            key = parts[1].strip()
            do_force = len(parts) > 2 and parts[2].lower() == "force"

            client_name, status = await asyncio.to_thread(find_license_row, key)
            if client_name is None:
                await event.reply(f"❌ Clé `{key}` introuvable dans la Sheet.")
                return

            zip_buf, zip_name, exe_included = generate_update_package(client_name)
            _, exe_name, ver = find_bot_exe()
            if not exe_included:
                await event.reply(f"❌ Aucun exe trouvé sur le VPS : rien à envoyer.\n"
                                  f"Dépose un `{PRODUCT_NAME}_Vx.y.z.exe` à côté de "
                                  f"`admin_license_bot_V3.py`.")
                return

            forced_txt = ""
            if do_force:
                # Le blocage n'est posé QU'APRÈS la construction du ZIP : inutile de couper
                # l'accès à un client si l'exe n'était même pas disponible à l'envoi.
                if not ver:
                    await event.reply(f"⚠️ Version illisible dans `{exe_name}`, "
                                      f"`force` ignoré. Le ZIP part quand même.")
                else:
                    ok, res = await sheet_write(set_client_version, key, ver)
                    forced_txt = (f"\n🔒 Version minimale passée à **{ver}** : son ancien exe "
                                  f"refusera de démarrer."
                                  if ok else f"\n⚠️ Blocage non appliqué : {res}")

            await event.reply(
                f"📦 **PAQUET DE MISE À JOUR**\n"
                f"👤 Client : {client_name}\n"
                f"🔑 Clé : `{key}`\n"
                f"📋 Statut : {status}\n"
                f"🆕 Version : **{ver or '?'}**\n\n"
                f"Le ZIP contient :\n"
                f"  • `{exe_name}`\n"
                f"  • `LIRE_MOI_MAJ.txt`\n\n"
                f"⚠️ **Pas de `settings.json`**, volontairement : le client garde sa clé, "
                f"ses identifiants et son appairage Telegram.{forced_txt}")

            await event.client.send_file(
                event.chat_id, zip_buf,
                attributes=[DocumentAttributeFilename(zip_name)],
                force_document=True,
                caption=f"🆕 Mise à jour {ver or ''} pour {client_name}")

        except Exception as e:
            await event.reply(f"❌ Crash: {e}")

    # A-bis. (#7) Changer le pack d'un client EXISTANT à distance (/channels CLE profil|canaux)
    elif msg.lower().startswith("/channels"):
        try:
            parts = msg.split()
            if len(parts) < 3:
                profs = ", ".join(sorted(CHANNEL_PROFILES.keys()))
                toks = ", ".join(sorted(VALID_CHANNEL_TOKENS.keys()))
                await event.reply(f"❌ Usage: `/channels CLE profil|canaux`\n"
                                  f"Ex: `/channels FTMO-AB12 sabr,mdt` ou `/channels FTMO-AB12 SABR_VIP,PERSO`\n"
                                  f"Profils: {profs}\nCanaux: {toks}\n"
                                  f"💡 `/channels CLE full` = tout afficher.")
                return
            key = parts[1]
            chan_arg = ",".join(parts[2:]).strip()  # (#42) cf. /gen : "," évite d'écraser "SABR_VIP PERSO" en un jeton bogus
            allowed, ch_err = resolve_channels(chan_arg)
            if ch_err:
                await event.reply(f"❌ {ch_err}")
                return
            # La clé doit exister
            found, _info = await asyncio.to_thread(get_license_info, key)
            if not found:
                await event.reply(f"❌ Clé `{key}` introuvable dans la Sheet.")
                return
            pack_txt = ", ".join(c.replace("CANAL ", "") for c in allowed) if allowed else "TOUS les canaux"

            # (#26) Sheet (colonne G) + Firestore licenses/{key} = source de vérité, lue par
            # le bot client à CHAQUE check de licence -- s'applique dans tous les cas, même
            # si le client n'a jamais lié son dashboard.
            sheet_ok = await sheet_write(update_sheet_pack, key, allowed)
            if not sheet_ok:
                await event.reply("❌ Échec de l'écriture dans la Sheet/Firestore.")
                return

            # Hot-push dashboard (nécessite un telegram_id lié) : mise à jour immédiate en plus
            # de la vérification licence -- purement pour l'affichage instantané du dashboard.
            tg = await asyncio.to_thread(_license_telegram_id, key)
            pushed = await asyncio.to_thread(fb_push_set_channels, tg, allowed) if tg else False

            reply = (f"✅ **PACK MIS À JOUR** pour `{key}`\n"
                     f"📦 Canaux visibles : {pack_txt}\n\n")
            if pushed:
                reply += "⏳ Appliqué immédiatement (dashboard + bot). La validité n'est PAS modifiée."
            else:
                reply += ("⏳ Appliqué au prochain check de licence du bot (démarrage ou sous 4h). "
                          "Dashboard non mis à jour à chaud (client pas encore lié). "
                          "La validité n'est PAS modifiée.")
            await event.reply(reply)
        except Exception as e:
            await event.reply(f"❌ Crash: {e}")

    # (#29) Marquer un feedback comme lu, avec réponse optionnelle -- visible sur le
    # dashboard du client (jamais renvoyée en DM Telegram, décidé par l'utilisateur).
    elif msg.lower().startswith("/vu") or msg.lower().startswith("/corrige") or msg.lower().startswith("/rep"):
        try:
            if not (FIREBASE_ENABLED and _fb_db):
                await event.reply("❌ Firebase indisponible.")
                return
            from google.cloud.firestore_v1.base_query import FieldFilter

            def _find_feedback(ticket_num):
                docs = list(_fb_db.collection("feedback").where(
                    filter=FieldFilter("ticket", "==", ticket_num)).limit(1).stream())
                return docs[0] if docs else None

            # (#31) 3 commandes independantes -- avant, /vu faisait a la fois le statut ET
            # la reponse en un seul appel. Desormais statut (/vu, /corrige) et reponse (/rep)
            # sont decouples, combinables dans n'importe quel ordre.
            if msg.lower().startswith("/rep"):
                parts = msg.split(maxsplit=2)  # ["/rep", "<ticket>", "<message libre>"]
                if len(parts) < 3 or not parts[1].isdigit():
                    await event.reply("❌ Usage: `/rep <numero_ticket> <message>`")
                    return
                ticket_num = int(parts[1])
                reponse = parts[2]

                doc = await asyncio.to_thread(_find_feedback, ticket_num)
                if not doc:
                    await event.reply(f"❌ Ticket #{ticket_num} introuvable.")
                    return

                await asyncio.to_thread(lambda: doc.reference.update({"reply": reponse}))
                await event.reply(f"✅ Réponse enregistrée sur le ticket #{ticket_num} (visible sur son dashboard).")
                return

            # /vu ou /corrige : change uniquement le statut
            new_status = "lu" if msg.lower().startswith("/vu") else "corrige"
            cmd_name = "/vu" if new_status == "lu" else "/corrige"
            label = "vu" if new_status == "lu" else "corrigé"

            parts = msg.split()
            if len(parts) < 2 or not parts[1].isdigit():
                await event.reply(f"❌ Usage: `{cmd_name} <numero_ticket>`")
                return
            ticket_num = int(parts[1])

            doc = await asyncio.to_thread(_find_feedback, ticket_num)
            if not doc:
                await event.reply(f"❌ Ticket #{ticket_num} introuvable.")
                return

            await asyncio.to_thread(lambda: doc.reference.update({"status": new_status}))
            await event.reply(f"✅ Ticket #{ticket_num} marqué {label}.")
        except Exception as e:
            await event.reply(f"❌ Crash: {e}")

    # (#32) Liste tous les tickets mailbox pas encore corrigés (statut != 'corrige'),
    # triés par numéro de ticket -- vue de triage rapide dans le canal support.
    elif msg.lower().strip() == "/message":
        try:
            if not (FIREBASE_ENABLED and _fb_db):
                await event.reply("❌ Firebase indisponible.")
                return

            def _fetch_all():
                return [d.to_dict() for d in _fb_db.collection("feedback").stream()]

            all_fb = await asyncio.to_thread(_fetch_all)
            pending = [f for f in all_fb if f.get("status") != "corrige"]

            if not pending:
                await event.reply("✅ Aucun message en attente — tout est corrigé.")
                return

            pending.sort(key=lambda f: f.get("ticket") or 0)
            type_icons = {"bug": "🐛", "suggestion": "💡", "autre": "💬"}
            status_labels = {"nouveau": "Envoyé", "lu": "Vu"}

            lines = [f"📋 **MESSAGES NON CORRIGÉS** ({len(pending)})\n"]
            for f in pending:
                icon = type_icons.get(f.get("type", "autre"), "💬")
                ticket = f.get("ticket")
                ticket_txt = f"#{ticket}" if ticket else "(en attente)"
                status_txt = status_labels.get(f.get("status"), f.get("status", "?"))
                user = escape_md(f.get("user_uid", "Inconnu"))
                message = escape_md((f.get("message") or "")[:150])
                lines.append(f"{icon} {ticket_txt} [{status_txt}] — {user}\n{message}\n")

            full_msg = "\n".join(lines)
            if len(full_msg) > 3800:  # Sécurité limite Telegram (4096)
                full_msg = full_msg[:3800] + "\n… (tronqué -- corrige certains tickets pour voir le reste)"
            await event.reply(full_msg)
        except Exception as e:
            await event.reply(f"❌ Crash: {e}")

    # B. Ajout Manuel (/add User Key [Jours])
    elif msg.lower().startswith("/add"):
        try:
            parts = msg.split()
            if len(parts) < 3:
                await event.reply("❌ Usage: `/add User Key [30]`")
                return
            
            days = parts[3] if len(parts) > 3 else None
            success, info, fb_email, fb_password = await sheet_write(add_license_row, parts[1], parts[2], days)

            if success:
                reply = f"✅ Ajouté: {info}"
                if fb_email:
                    reply += (f"\n\n🔐 **Firebase:** `{fb_email}` / `{fb_password}`")
                await event.reply(reply)
            else:
                await event.reply(f"❌ {info}")
        except Exception as e:
            # (#61) Le bare `except: pass` masquait toute erreur Google Sheets (quota/timeout)
            # -- l'admin ne voyait alors RIEN, pas même un message d'erreur.
            await event.reply(f"❌ Crash `/add` : {e}")

    # C. Info Licence (/info Key)
    elif msg.lower().startswith("/info"):
        parts = msg.split()
        if len(parts) < 2:
            await event.reply("❌ Usage: `/info Key`")
            return
        try:
            success, info = await asyncio.to_thread(get_license_info, parts[1])
            if success: await event.reply(info)
            else: await event.reply(f"❌ {info}")
        except Exception as e:
            await event.reply(f"❌ Crash `/info` : {e}")

    # C-ter. (#50) Historique HWID d'une clé (connexions/conflits/resets/bans)
    elif msg.lower().startswith("/history"):
        parts = msg.split()
        if len(parts) < 2:
            await event.reply("❌ Usage: `/history Clé`")
            return
        key = parts[1]
        events = await asyncio.to_thread(get_hwid_history, key)
        if not events:
            await event.reply(f"ℹ️ Aucun historique enregistré pour `{key}` (ou clé jamais vue depuis ce fix).")
            return
        icons = {"connection": "🔌", "theft": "🚨", "reset": "🔄", "ban": "🚫", "unban": "✅"}
        lines = [f"📜 **HISTORIQUE** `{key}` ({len(events)} derniers événements)\n"]
        for e in events:
            icon = icons.get(e["type"], "•")
            lines.append(f"{icon} {e['ts']} — {e['type']} : {e['detail']}")
        await event.reply("\n".join(lines))

    # C-bis. Provisionner un compte Firebase pour une clé EXISTANTE (/fbcreate Key)
    elif msg.lower().startswith("/fbcreate"):
        try:
            parts = msg.split()
            if len(parts) < 2:
                await event.reply("❌ Usage: `/fbcreate CLE` (ou `/fbcreate CLE force` pour régénérer un compte existant)")
                return
            key = parts[1]
            force = len(parts) > 2 and parts[2].lower() == "force"
            # On vérifie d'abord que la clé existe dans la Sheet (évite les comptes orphelins)
            found, _ = await asyncio.to_thread(get_license_info, key)
            if not found:
                await event.reply(f"❌ Clé `{key}` introuvable dans la Sheet. Crée-la d'abord (`/gen` ou `/add`).")
                return
            # (#59) force=True réactivait (disabled=False) un compte Firebase désactivé sans
            # jamais vérifier que la licence n'était pas BANNED -- un admin régénérant un mot
            # de passe perdu pouvait réactiver sans s'en rendre compte le compte d'une licence
            # bannie, contrairement à /time et /unban qui ont déjà cette garde.
            if force:
                status = await asyncio.to_thread(get_license_status, key)
                if status == "BANNED":
                    await event.reply(f"⛔ `{key}` est actuellement BANNIE. `/unban {key}` d'abord si c'est volontaire.")
                    return
            await event.reply("⏳ Création du compte Firebase...")
            fb_email, fb_password, already_existed = await asyncio.to_thread(fb_create_firebase_user, key, force)
            if already_existed:
                # (#37) Ne régénère plus silencieusement -- casserait le settings.json déjà
                # distribué au client (mot de passe changé) et annulerait un ban/expiration.
                await event.reply(
                    f"⚠️ Un compte Firebase existe déjà pour `{key}` -- rien n'a été modifié.\n"
                    f"Utilise `/fbcreate {key} force` si tu veux VRAIMENT régénérer le mot de passe "
                    f"(le client devra re-télécharger un settings.json à jour, et ça réactive le compte s'il était désactivé)."
                )
            elif fb_email:
                await event.reply(
                    f"✅ **Compte Firebase provisionné**\n"
                    f"🔑 Clé : `{key}`\n\n"
                    f"À mettre dans le `settings.json` :\n"
                    f"`FIREBASE_EMAIL` : `{fb_email}`\n"
                    f"`FIREBASE_PASSWORD` : `{fb_password}`"
                )
            else:
                await event.reply("❌ Échec (Firebase désactivé ou erreur). Voir les logs du bot.")
        except Exception as e:
            await event.reply(f"❌ Crash: {e}")

    # D. Broadcast (/say Message) -> Affiche le message dans le canal
    elif msg.lower().startswith("/say"):
        # Cette commande est simple : elle répète le message.
        # Utile si tu ajoutes le bot Admin dans le canal des clients pour faire des annonces.
        # Ici, elle répond juste dans le canal de sécurité pour tester.
        try:
            content = msg[5:].strip()
            if content:
                await event.reply(f"📢 **ANNONCE:**\n\n{content}")
        except Exception as e:
            await event.reply(f"❌ Crash: {e}")

    # E. Reset, Del, Ban (Classiques)
    elif msg.lower().startswith("/reset"):
        parts = msg.split()
        if len(parts) < 2:
            await event.reply("❌ Usage: `/reset Clé` (ou `/reset Clé force` pour ignorer la limite de fréquence)")
            return
        key = parts[1]
        force = len(parts) > 2 and parts[2].lower() == "force"
        try:
            # (#50) Limite de fréquence : un changement de machine légitime est rare -- une
            # succession rapide de /reset sur la même clé est un signal d'abus (partage de
            # licence en train d'être couvert) qui méritait d'être détecté, pas juste exécuté
            # silencieusement à chaque demande.
            if not force:
                recent_resets = [e for e in get_hwid_history(key) if e["type"] == "reset"]
                if recent_resets:
                    last_ts = datetime.strptime(recent_resets[-1]["ts"], "%Y-%m-%d %H:%M:%S")
                    if datetime.now() - last_ts < timedelta(hours=1):
                        await event.reply(
                            f"⚠️ `{key}` a déjà été reset il y a moins d'1h ({recent_resets[-1]['ts']}).\n"
                            f"`/reset {key} force` pour forcer quand même."
                        )
                        return
            success, res = await sheet_write(reset_hwid, key)
            if success:
                record_hwid_event(key, "reset", "reset manuel par admin")
                await event.reply(f"🔄 Reset OK pour `{key}`")
            else:
                await event.reply(f"❌ {res}")
        except Exception as e:
            await event.reply(f"❌ Crash `/reset` : {e}")

    elif msg.lower().startswith("/del"):
        try:
            parts = msg.split()
            if len(parts) < 2:
                await event.reply("❌ Usage: `/del Clé confirm` (confirmation requise, suppression définitive)")
                return
            key = parts[1]
            # (#45) Suppression définitive et irréversible -- exige une confirmation explicite.
            if len(parts) < 3 or parts[2].lower() != "confirm":
                await event.reply(f"⚠️ Tape `/del {key} confirm` pour confirmer la suppression DÉFINITIVE.")
                return
            success, res = await sheet_write(delete_license_row, key)
            if success: await event.reply(f"🗑️ Supprimé: `{key}`")
            else: await event.reply(f"❌ {res}")
        except Exception as e:
            await event.reply(f"❌ Crash: {e}")

    elif msg.lower().startswith("/ban"):
        try:
            parts = msg.split()
            if len(parts) < 2:
                await event.reply("❌ Usage: `/ban Clé confirm` (confirmation requise, action destructrice)")
                return
            key = parts[1]
            # (#45) Confirmation explicite exigée pour une action destructrice -- évite un ban
            # accidentel sur une faute de frappe/mix-up de clé.
            if len(parts) < 3 or parts[2].lower() != "confirm":
                await event.reply(f"⚠️ Tape `/ban {key} confirm` pour confirmer le bannissement.")
                return
            success, res = await sheet_write(ban_license, key)
            if success:
                record_hwid_event(key, "ban", f"banni manuellement par {event.sender_id}")
                await event.reply(f"🚫 Banni: `{key}`")
            else:
                await event.reply(f"❌ {res}")
        except Exception as e:
            await event.reply(f"❌ Crash: {e}")

    # E-bis. (#45) Débannir sans toucher à la date (contrairement à /time)
    elif msg.lower().startswith("/unban"):
        try:
            parts = msg.split()
            if len(parts) < 2:
                await event.reply("❌ Usage: `/unban Clé`")
                return
            key = parts[1]
            success, res = await sheet_write(unban_license, key)
            if success:
                record_hwid_event(key, "unban", f"débanni manuellement par {event.sender_id}")
                await event.reply(f"✅ `{key}` : {res}")
            else:
                await event.reply(f"❌ {res}")
        except Exception as e:
            await event.reply(f"❌ Crash: {e}")

    # F. Modifier le temps (/time Clé Valeur)
    elif msg.lower().startswith("/time"):
        try:
            parts = msg.split()
            # Usage : /time FTMO-KEY 30
            if len(parts) < 3:
                await event.reply("❌ Usage:\n`/time Clé 30` (Ajoute 30j)\n`/time Clé 01/01/2026` (Date précise)\n`/time Clé vie` (Illimité)")
                return
            
            key = parts[1]
            value = parts[2]
            force = len(parts) > 3 and parts[3].lower() == "force"

            await event.reply("⏳ Mise à jour...")
            success, res = await sheet_write(update_license_time, key, value, force)
            
            if success:
                await event.reply(f"✅ **MODIFIÉ & RÉACTIVÉ**\n\n🔑 Clé: `{key}`\n📅 Nouvelle fin: **{res}**\n🟢 Statut: ACTIVE")
            else:
                await event.reply(f"❌ Erreur: {res}")
                
        except Exception as e: await event.reply(f"❌ Crash: {e}")
        
    # G. Gestion Automatique Expiration (Réception du signal du Worker)
    elif "SIGNAL EXPIRATION" in msg:
        # Le worker a envoyé : "⌛ SIGNAL EXPIRATION \n Clé: FTMO-..."
        try:
            key_match = re.search(r"Clé:\s*[`]?([^`\n]+)[`]?", msg)
            if key_match:
                key_to_kill = key_match.group(1).strip()
                # On demande au sheet de mettre à jour
                ok = await sheet_write(set_expired, key_to_kill)
                if ok:
                    print(f"💀 Clé {key_to_kill} marquée comme EXPIRED (Signal reçu).")
                else:
                    print(f"⚠️ Échec du marquage EXPIRED pour {key_to_kill} (clé introuvable ou erreur Sheet/Firestore).")
        except Exception as e:
            print(f"⚠️ Erreur traitement SIGNAL EXPIRATION: {e}")

    # H. Changer la version par défaut (/set_ver 7.2)
    elif msg.lower().startswith("/set_ver"):
        try:
            parts = msg.split()
            if len(parts) < 2:
                # Si pas d'argument, on affiche la version actuelle
                current = await asyncio.to_thread(get_current_default_version)
                await event.reply(f"ℹ️ Version actuelle par défaut : **{current}**")
                return
            
            new_ver = parts[1].strip().lstrip("'")
            # (#63) Un suffixe non numérique ("15.1.0-beta") ferait lever is_version_older()
            # côté client, dont le `except: return False` désactive alors SILENCIEUSEMENT tout
            # le blocage de version. On refuse en amont plutôt que de le découvrir trop tard.
            if not VERSION_RE.match(new_ver):
                await event.reply(f"❌ Version invalide : `{new_ver}`\nAttendu : uniquement des "
                                  f"chiffres et des points, par exemple `15.1.0`.")
                return
            await event.reply("⏳ Mise à jour de la config...")
            
            success = await sheet_write(set_default_version, new_ver)
            
            if success:
                await event.reply(f"✅ **VERSION PAR DÉFAUT MISE À JOUR**\n\nNouvelle valeur: **{new_ver}**\nLes prochaines clés `/gen` auront cette version.")
            else:
                await event.reply("❌ Erreur lors de l'écriture dans le Sheet.")
                
        except Exception as e: await event.reply(f"❌ Crash: {e}")

    # I. Statistiques Globales
    elif msg.lower() == "/stats_users":
        try:
            await event.reply("⏳ Calcul en cours...")
            rows = await asyncio.to_thread(lambda: get_sheet().get_all_values())
            
            # On ignore la ligne d'en-tête (ligne 1)
            data = rows[1:] 
            
            total = len(data)
            active = sum(1 for r in data if len(r) > 2 and r[2] == "ACTIVE")
            banned = sum(1 for r in data if len(r) > 2 and r[2] == "BANNED")
            expired = sum(1 for r in data if len(r) > 2 and r[2] == "EXPIRED")
            
            msg_stats = (f"📊 **RAPPORT CLIENTS**\n\n"
                         f"👥 Total Base: **{total}**\n"
                         f"🟢 Actifs: **{active}**\n"
                         f"🔴 Bannis: **{banned}**\n"
                         f"💀 Expirés: **{expired}**")
            
            await event.reply(msg_stats)
        except Exception as e: await event.reply(f"❌ Erreur: {e}")

    # J. Recherche intelligente
    elif msg.lower().startswith("/search"):
        parts = msg.split()
        if len(parts) < 2:
            await event.reply("❌ Usage: `/search Pierre`")
            return
        try:
            query = parts[1].lower()
            sheet = await asyncio.to_thread(get_sheet)
            # On cherche dans tout le fichier
            found_cells = await asyncio.to_thread(sheet.findall, re.compile(f"(?i){query}"))
            
            if not found_cells:
                await event.reply("❌ Aucun résultat.")
                return

            msg_res = f"🔎 **RÉSULTATS POUR '{query}'**:\n\n"
            processed_rows = set()
            
            for cell in found_cells:
                if cell.row in processed_rows or cell.row == 1: continue # Eviter doublons et entete
                processed_rows.add(cell.row)
                
                # On récupère la ligne entière
                row_vals = await asyncio.to_thread(sheet.row_values, cell.row)
                msg_res += f"👤 **{row_vals[0]}** (Ligne {cell.row})\n🔑 `{row_vals[1]}`\nSTATUS: {row_vals[2]}\n\n"
                
                if len(msg_res) > 3500: # Sécurité limite Telegram
                    msg_res += "... (Trop de résultats)"
                    break
            
            await event.reply(msg_res)
        except Exception as e:
            await event.reply(f"❌ Crash `/search` : {e}")

    # K. Mise à jour de version massive
    elif msg.lower().startswith("/push_ver"):
        try:
            parts = msg.split()
            if len(parts) < 2:
                await event.reply("❌ Usage: `/push_ver VersionX confirm`")
                return
            new_ver = parts[1].strip().lstrip("'")
            # (#63) cf. /set_ver : une version non numérique désactive silencieusement le
            # blocage côté client. Ici l'enjeu est pire, la commande touche TOUS les clients.
            if not VERSION_RE.match(new_ver):
                await event.reply(f"❌ Version invalide : `{new_ver}`\nAttendu : uniquement des "
                                  f"chiffres et des points, par exemple `15.1.0`.")
                return
            # (#45) Action groupée irréversible (touche TOUS les clients actifs) -- confirmation
            # explicite exigée, comme /ban et /del.
            if len(parts) < 3 or parts[2].lower() != "confirm":
                await event.reply(f"⚠️ Tape `/push_ver {new_ver} confirm` pour confirmer -- passe TOUS les clients ACTIFS en version {new_ver}.")
                return
            await event.reply(f"⚠️ **ATTENTION** : Je vais passer tous les clients ACTIFS en version **{new_ver}**.\nCela peut prendre du temps...")

            count, errors = await sheet_write(push_version_to_all, new_ver)

            reply = f"✅ **MIGRATION TERMINÉE**\n\n🔄 {count} clients mis à jour vers la v{new_ver}."
            if errors:
                shown = errors[:10]
                reply += f"\n\n⚠️ {len(errors)} échec(s) :\n" + "\n".join(shown)
                if len(errors) > 10:
                    reply += f"\n… (+{len(errors) - 10} autres)"
            await event.reply(reply)

        except Exception as e: await event.reply(f"❌ Erreur: {e}")

    # L. Aide
    elif msg.lower() == "/help":
        await event.reply("**🛠️ ADMIN BOT 2.0**\n\n\n"
                          "🔑 **GÉNÉRATION**\n\n"
                          "`/gen Pierre` : Clé illimitée (tous canaux)\n"
                          "`/gen Pierre 30` : Clé 30 jours (tous canaux)\n"
                          "`/gen Pierre 30 sabr` : 30 jours, pack sabr\n"
                          "`/gen Pierre 30 SABR_VIP,PERSO` : canaux sur-mesure\n"
                          "`/add Pierre Clé [30]` : Ajout manuel (jours optionnels, illimité sinon)\n\n\n"
                          "📦 **PACK CANAUX (visibles : logs + dashboard)**\n\n"
                          "`/channels Clé profil|canaux` : change le pack à distance\n"
                          "`/channels Clé none` : suspend tous les canaux (pack vidé, pas \"tout débloqué\")\n"
                          "Profils : sabr, mdt, space, ligue, perso, full, none\n"
                          "⚠️ `dev` : profil réservé/interne -- inclut SABR_COPIE (seule exception) + validité illimitée. "
                          "Ne l'assigne JAMAIS à un vrai client payant via /channels.\n"
                          "Canaux : SABR_VIP, SABR_FREE, SABR_COPIE, MDT_VIP, MDT_FREE, INDODOG, SPACE, LIGUE, PERSO\n"
                          "⚠️ SABR_COPIE jamais dans un pack normal (à mettre à la main, sauf dev)\n\n\n"
                          "🔧 **GESTION**\n\n"
                          "`/info Clé` : Voir détails (PC, Date...)\n"
                          "`/history Clé` : Historique HWID (connexions/conflits/resets/bans)\n"
                          "`/fbcreate Clé` : Créer compte Firebase (clé existante, sans effet si déjà provisionné)\n"
                          "`/fbcreate Clé force` : Régénère le mot de passe d'un compte existant (⚠️ casse le settings.json déjà distribué, refusé si BANNED)\n"
                          "`/reset Clé` : Changer de PC (refusé si un reset a eu lieu il y a moins d'1h, sauf `/reset Clé force`)\n"
                          "`/ban Clé confirm` : Bloquer accès (confirmation requise)\n"
                          "`/unban Clé` : Réactive sans toucher à la date (contrairement à /time)\n"
                          "`/del Clé confirm` : Supprimer ligne (⚠️ définitif, confirmation requise)\n"
                          "`/maj Clé` : Envoie le ZIP de mise à jour (exe seul, garde ses réglages)\n"
                          "`/maj Clé force` : Idem + bloque son ancienne version au démarrage\n"
                          "`/set_ver Version` : Changer version min par défaut (prochaines clés /gen)\n"
                          "`/push_ver VersionX confirm` : Forcer TOUS les clients actifs en VersionX (confirmation requise)\n"
                          "`/search Terme` : Recherche terme dans licences\n\n\n"
                          "📊 **STATISTIQUES**\n\n"
                          "`/stats_users` : Nb actifs, bannis, expirés\n\n\n"
                          "📢 **AUTRE**\n\n"
                          "`/say Texte` : Faire une annonce\n"
                          "`/time Clé Valeur` : Modifier la durée. Valeur :\n"
                          "     - nb jour a ajouter (30)\n"
                          "     - date fin (25/12/2025)\n"
                          "     - 'unlimited' pour (illimité)\n"
                          "`/time Clé Valeur force` : Idem, même si la clé est bannie\n\n\n"
                          "💬 **MAILBOX (canal feedback)**\n\n"
                          "`/vu 1234` : Marquer le ticket #1234 comme vu\n"
                          "`/corrige 1234` : Marquer le ticket #1234 comme corrigé\n"
                          "`/rep 1234 Merci, corrigé !` : Répondre (visible sur son dashboard, sans changer le statut)\n"
                          "`/message` : Lister tous les tickets pas encore corrigés")


async def security_events_relay():
    """Relaie les /security_events (écrits par les bots clients) vers le canal Telegram,
    via SECURITY_BOT_TOKEN qui reste sur le VPS (jamais dans l'exe distribué)."""
    if not (FIREBASE_ENABLED and _fb_db):
        return
    if not SECURITY_BOT_TOKEN:
        print("⚠️ SECURITY_BOT_TOKEN vide — relais en pause. Remplis-le puis relance le bot.")
        return
    from google.cloud.firestore_v1.base_query import FieldFilter
    print("📡 Relais des événements de sécurité activé.")
    seen = set()
    while True:
        fetched_count = 0
        try:
            def _fetch():
                docs = _fb_db.collection("security_events").where(
                    filter=FieldFilter("relayed", "==", False)).limit(20).stream()
                return [(d.id, d.to_dict()) for d in docs]
            batch = await asyncio.to_thread(_fetch)
            fetched_count = len(batch)
            for doc_id, data in batch:
                if doc_id in seen:
                    continue
                seen.add(doc_id)
                msg = data.get("message", "Événement sécurité")
                event_type = data.get("type", "")
                license_key = data.get("license_key")
                # (#50) La clé et le HWID attendu manquaient dans le message relayé -- l'admin
                # devait remonter l'historique du canal pour identifier de quelle clé il
                # s'agissait à chaque alerte.
                details = []
                if license_key:               details.append(f"🔑 Clé : {license_key}")
                if data.get("user"):          details.append(f"👤 {data['user']}")
                if data.get("ip"):            details.append(f"🌐 IP : {data['ip']}")
                if data.get("pc_id"):         details.append(f"💻 PC : {data['pc_id']}")
                if data.get("expected_hwid"): details.append(f"💾 HWID attendu : {data['expected_hwid']}")
                if data.get("got_hwid"):      details.append(f"💻 Machine : {data['got_hwid']}")
                full = msg + ("\n" + "\n".join(details) if details else "")

                # Notification console : jusqu'ici les événements n'étaient visibles QUE dans
                # le canal Telegram, donc invisibles quand on surveille le VPS directement.
                _icon = {"connection": "🟢 CONNEXION", "theft": "🚨 CONFLIT HWID",
                         "expiry": "⌛ EXPIRATION"}.get(event_type, f"ℹ️ {event_type or 'EVENT'}")
                _now = datetime.now().strftime("%H:%M:%S")
                print(f"\n[{_now}] {_icon}")
                for _d in details:
                    print(f"    {_d}")

                # (#27) Le HWID n'est écrit qu'en Firestore par le bot client (jamais dans la
                # Sheet) -- on le recopie ici pour garder un visuel à jour. Seulement sur un
                # "connection" (licence acceptée sur cette machine) : un "theft" est un refus,
                # le HWID enregistré n'a pas changé, y toucher afficherait la mauvaise machine.
                if event_type == "connection" and license_key and data.get("pc_id"):
                    def _sync_hwid_sheet(_key=license_key, _pc=data["pc_id"]):
                        try:
                            sheet = get_sheet()
                            cell = sheet.find(_key)
                            if cell:
                                sheet.update_cell(cell.row, COL_HWID, sheet_safe(_pc))
                        except Exception as e:
                            print(f"⚠️ sync HWID Sheet: {e}")
                    await sheet_write(_sync_hwid_sheet)
                    record_hwid_event(license_key, "connection", f"pc_id={data['pc_id']}")

                # (#50) Un conflit HWID ("theft") n'était jusqu'ici que NOTIFIÉ, sans aucune
                # action -- un partage de licence pouvait continuer indéfiniment tant que
                # l'admin ne remarquait pas l'alerte et ne tapait pas /ban lui-même. On bannit
                # désormais la licence automatiquement ; si c'est un changement de machine
                # légitime (pas un partage), l'admin lève le ban en un message (/unban +
                # /reset) au lieu de devoir agir vite pour COUVRIR un vrai partage en cours.
                if event_type == "theft" and license_key:
                    record_hwid_event(
                        license_key, "theft",
                        f"attendu={data.get('expected_hwid')} recu={data.get('got_hwid')}")
                    try:
                        ban_ok, _ban_res = await sheet_write(ban_license, license_key)
                    except Exception as e:
                        ban_ok = False
                        print(f"⚠️ auto-ban échoué pour {license_key}: {e}")
                    if ban_ok:
                        record_hwid_event(license_key, "ban", "auto-ban sur conflit HWID")
                        full += (f"\n\n🚫 Licence bannie AUTOMATIQUEMENT.\n"
                                 f"Si c'est un changement de machine légitime (pas un partage) : "
                                 f"`/unban {license_key}` puis `/reset {license_key}`.")
                try:
                    def _send(_t=full):
                        requests.post(
                            f"https://api.telegram.org/bot{SECURITY_BOT_TOKEN}/sendMessage",
                            json={"chat_id": SECURITY_CHANNEL_ID, "text": _t}, timeout=10)
                    await asyncio.to_thread(_send)
                except Exception as e:
                    print(f"⚠️ relais envoi : {e}")
                    seen.discard(doc_id)  # on réessaiera au prochain tour
                    continue
                await asyncio.to_thread(
                    lambda _id=doc_id: _fb_db.collection("security_events").document(_id).update({"relayed": True})
                )
        except Exception as e:
            print(f"⚠️ security_events_relay : {e}")
        # Le blocage reel (licence sur mauvaise machine) est deja instantane cote bot client
        # (return False synchrone) -- ce relais ne sert qu'a notifier l'admin, pas a proteger.
        # 1h de delai ne laisse donc aucune fenetre de risque, et evite d'epuiser le quota
        # Firestore gratuit (5s -> ~17000 lectures/jour rien que pour cette boucle).
        # (#40) Sauf juste apres une coupure : si le lot etait plein (20, la limite), il reste
        # probablement un backlog -- on enchaine tout de suite au lieu d'attendre 1h par 20.
        await asyncio.sleep(5 if fetched_count >= 20 else 3600)


def _next_feedback_ticket():
    """(#29) Incrémente et retourne le prochain numéro de ticket feedback, de façon
    atomique (transaction Firestore -- infra déjà disponible via le SDK Admin réel)."""
    counter_ref = _fb_db.collection("counters").document("feedback_ticket")
    transaction = _fb_db.transaction()

    @fb_firestore.transactional
    def _increment(tx):
        snap = counter_ref.get(transaction=tx)
        current = snap.get("value") if snap.exists else 0
        new_value = current + 1
        tx.set(counter_ref, {"value": new_value})
        return new_value

    return _increment(transaction)


async def feedback_relay():
    """(#29) Relaie les nouveaux messages de la mailbox (collection Firestore `feedback`,
    écrite par le dashboard client) vers FEEDBACK_CHANNEL_ID, avec un numéro de ticket
    court assigné ici. Poll rapide (contrairement au relais sécurité) car c'est un
    retour utilisateur qu'on veut voir arriver vite, pas juste une notification de secours."""
    if not (FIREBASE_ENABLED and _fb_db):
        return
    if not SECURITY_BOT_TOKEN:
        print("⚠️ SECURITY_BOT_TOKEN vide — relais feedback en pause. Remplis-le puis relance le bot.")
        return
    from google.cloud.firestore_v1.base_query import FieldFilter
    print("📬 Relais de la mailbox feedback activé.")
    seen = set()
    type_icons = {"bug": "🐛", "suggestion": "💡", "autre": "💬"}
    # (#62) Contrairement à security_events_relay (déjà en backoff 5s/1h), cette boucle
    # retentait toutes les 20s sans relâche même en cas de 429 (quota Firestore dépassé) --
    # martelait l'API inutilement ~4300 fois/jour sur un quota déjà épuisé. Backoff
    # exponentiel plafonné à 1h, réinitialisé au premier succès.
    consecutive_failures = 0
    while True:
        try:
            def _fetch():
                docs = _fb_db.collection("feedback").where(
                    filter=FieldFilter("relayed", "==", False)).limit(20).stream()
                return [(d.id, d.to_dict()) for d in docs]
            for doc_id, data in await asyncio.to_thread(_fetch):
                if doc_id in seen:
                    continue
                seen.add(doc_id)

                ticket_num = await asyncio.to_thread(_next_feedback_ticket)
                icon = type_icons.get(data.get("type", "autre"), "💬")
                cat = (data.get("type") or "autre").capitalize()
                user = data.get("user_uid", "Inconnu")
                message = data.get("message", "")
                full = f"{icon} #{ticket_num} ({cat}) — {user} :\n{message}"

                try:
                    def _send(_t=full):
                        requests.post(
                            f"https://api.telegram.org/bot{SECURITY_BOT_TOKEN}/sendMessage",
                            json={"chat_id": FEEDBACK_CHANNEL_ID, "text": _t}, timeout=10)
                    await asyncio.to_thread(_send)
                except Exception as e:
                    print(f"⚠️ relais feedback envoi : {e}")
                    seen.discard(doc_id)
                    continue

                await asyncio.to_thread(
                    lambda _id=doc_id, _tk=ticket_num: _fb_db.collection("feedback").document(_id).update(
                        {"relayed": True, "ticket": _tk})
                )
            consecutive_failures = 0
        except Exception as e:
            print(f"⚠️ feedback_relay : {e}")
            consecutive_failures += 1
        if consecutive_failures > 0:
            await asyncio.sleep(min(20 * (2 ** consecutive_failures), 3600))
        else:
            await asyncio.sleep(20)


async def main():
    print("🤖 ADMIN BOT : Prêt à gérer les licences.")
    asyncio.create_task(security_events_relay())
    asyncio.create_task(feedback_relay())
    await client_tg.run_until_disconnected()

if __name__ == '__main__':
    client_tg.start()
    client_tg.loop.run_until_complete(main())