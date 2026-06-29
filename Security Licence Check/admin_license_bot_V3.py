import asyncio
import io
import json
import os
import re
import zipfile
import gspread
import requests
import uuid
from datetime import datetime, timedelta
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

def fb_sync_license(key, user, status, ver_min="14.0.0", hwid="", date_fin=""):
    """Écrit/met à jour une licence dans Firestore /licenses/{key}"""
    if not FIREBASE_ENABLED or not _fb_db:
        return
    try:
        _fb_db.collection("licenses").document(key).set({
            "user": user,
            "status": status,
            "ver_min": ver_min,
            "hwid": hwid,
            "date_fin": date_fin,
            "updated_at": fb_firestore.SERVER_TIMESTAMP,
        }, merge=True)
    except Exception as e:
        print(f"⚠️ fb_sync_license error: {e}")


def fb_create_firebase_user(key):
    """Crée un compte Firebase Auth pour un nouveau client.
    UID = license_key, email = key@ftmobot.com, mot de passe aléatoire.
    Retourne (email, password) ou (None, None) en cas d'échec."""
    if not FIREBASE_ENABLED:
        return None, None
    import secrets, string
    alphabet = string.ascii_letters + string.digits + "!@#$%"
    password = "".join(secrets.choice(alphabet) for _ in range(20))
    email = f"{key.lower()}@ftmobot.com"
    try:
        # Si l'utilisateur existe déjà (re-génération), on le met à jour
        try:
            fb_auth.get_user(key)
            fb_auth.update_user(key, email=email, password=password, disabled=False)
        except fb_auth.UserNotFoundError:
            fb_auth.create_user(uid=key, email=email, password=password)
        # Stocke email/password dans Firestore pour référence admin
        if _fb_db:
            _fb_db.collection("licenses").document(key).set(
                {"firebase_email": email, "firebase_password": password},
                merge=True
            )
        return email, password
    except Exception as e:
        print(f"⚠️ fb_create_firebase_user error: {e}")
        return None, None


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

# Tes identifiants Telegram (Les mêmes que le bot ou un autre compte)
API_ID = 32708194            
API_HASH = '37b283f8c146b27dcd4915102cc50da8'

# Le nom EXACT de ton Google Sheet (celui affiché en haut de la page)
SHEET_NAME = "FTMO_BOT_LICENSES" # <--- METS LE VRAI NOM ICI

# ID du canal de sécurité où arrivent les messages (Check Licence)
SECURITY_CHANNEL_ID = -1003872071233

# Token du bot de sécurité — UNIQUEMENT sur le VPS, jamais dans l'exe distribué.
# À remplir avec le NOUVEAU token (après révocation de l'ancien via BotFather).
SECURITY_BOT_TOKEN = ""

# MAPPAGE DES COLONNES 
COL_USER = 1       # A: Nom du client
COL_KEY = 2        # B: La clé de licence
COL_STATUS = 3     # C: ACTIVE / BANNED
COL_VER = 4        # D: Version Min
COL_HWID = 5       # E: L'ID du PC
COL_EXP = 6        # F: Date Expiration (JJ/MM/AAAA)
COL_CONFIG_VER = 8 # H: Config Version min
ROW_CONFIG_VER = 2 # Ligne Config Version min

# ── Package client ────────────────────────────────────────────────────────────
# Chemin vers le .exe compilé (à mettre à jour à chaque nouvelle version)
BOT_EXE_PATH = os.path.join(os.path.dirname(__file__), "Bot_FTMO_V14.0.0.exe")

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
    "SABR_ANTI_DOUBLON_ACTIVE": True,
    "SABR_FORWARD_ACTIVE": True,
    "MUTED_CHANNELS": [],
    "CHANNEL_TP1_PCT": {},
    "TIME_PRIME_RANGES": [[8, 17]],
    "TIME_OFF_PEAK_FACTOR": 0.5,
    "DAY_RISK_FACTORS": {"0": 1.0, "1": 1.0, "2": 1.0, "3": 1.0, "4": 1.0, "5": 1.0, "6": 1.0}
}

def generate_client_package(client_name: str, key: str, fb_email: str, fb_password: str) -> tuple:
    """
    Génère un ZIP en mémoire contenant :
    - settings.json pré-rempli (clé + Firebase + valeurs par défaut)
    - Bot_FTMO_V14.0.0.exe (si disponible sur le VPS)

    Retourne (zip_bytes: io.BytesIO, filename: str, exe_included: bool)
    """
    settings = dict(SETTINGS_TEMPLATE)
    settings["USER_LICENSE_KEY"] = key
    settings["FIREBASE_EMAIL"]   = fb_email or ""
    settings["FIREBASE_PASSWORD"] = fb_password or ""

    settings_bytes = json.dumps(settings, indent=4, ensure_ascii=False).encode("utf-8")

    buf = io.BytesIO()
    exe_included = False
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("settings.json", settings_bytes)
        if os.path.isfile(BOT_EXE_PATH):
            zf.write(BOT_EXE_PATH, arcname="Bot_FTMO_V14.0.0.exe")
            exe_included = True
    buf.seek(0)

    safe_name = re.sub(r"[^a-zA-Z0-9_-]", "", client_name) or "client"
    filename = f"FTMO_Bot_{safe_name}.zip"
    buf.name = filename  # Telethon utilise .name pour nommer un fichier en mémoire
    return buf, filename, exe_included


# ==========================================
# 🔌 CONNEXION GOOGLE SHEET
# ==========================================
scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
creds = ServiceAccountCredentials.from_json_keyfile_name("service_account.json", scope)
client_google = gspread.authorize(creds)

def get_sheet():
    """Reconnexion robuste à chaque appel"""
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    creds = ServiceAccountCredentials.from_json_keyfile_name("service_account.json", scope)
    client = gspread.authorize(creds) # On recrée le client frais
    return client.open(SHEET_NAME).sheet1

def is_version_older(current_ver, min_ver):
    """
    Compare deux versions (ex: '10.1.1' et '7.2').
    Retourne True si current_ver est PLUS VIEILLE que min_ver.
    """
    try:
        # On découpe en liste d'entiers: '10.1.1' -> [10, 1, 1]
        c_parts = [int(x) for x in current_ver.split('.')]
        m_parts = [int(x) for x in min_ver.split('.')]
        
        # On compare élément par élément
        for i in range(max(len(c_parts), len(m_parts))):
            c = c_parts[i] if i < len(c_parts) else 0
            m = m_parts[i] if i < len(m_parts) else 0
            
            if c < m: return True  # Obsolète
            if c > m: return False # À jour ou plus récent
            
        return False # Exactement la même version
    except:
        return False # En cas d'erreur de format, on laisse passer

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
                             print(f"⌛ Clé {license_key} temps écoulé -> EXPIRED")
                        
                        return "EXPIRED"
                except: pass # Si format date invalide, on ignore

            if not current_hwid or current_hwid == "":
                sheet.update_cell(cell.row, COL_HWID, pc_id)
                fb_sync_license(license_key, row_vals[0] if 'row_vals' in dir() else "", sheet.cell(cell.row, COL_STATUS).value or "ACTIVE", hwid=pc_id)
                print(f"💾 SUCCÈS : HWID {pc_id} enregistré pour {license_key}")
                return "NEW_REGISTER"
            
            elif current_hwid == pc_id:
                print("ℹ️ Déjà enregistré (Même PC).")
                return "SAME"
            
            else:
                print(f"⚠️ CONFLIT ID !")
                return "CONFLICT"
        else:
            return "NOT_FOUND"

    except Exception as e:
        print(f"❌ Erreur Sheet : {e}")
        return "ERROR"
    
def get_current_default_version():
    """Lit la version par défaut dans la case H2"""
    try:
        sheet = get_sheet()
        # On lit la ligne 2, colonne 8 (H2)
        val = sheet.cell(ROW_CONFIG_VER, COL_CONFIG_VER).value
        # Si vide, on renvoie une valeur de sécurité
        if not val: return "7.2"
        return val.strip()
    except: return "7.2"

def set_default_version(new_ver):
    """Change la version par défaut dans H2"""
    try:
        sheet = get_sheet()
        # On écrit dans H1 le titre pour que tu t'y retrouves visuellement
        sheet.update_cell(1, COL_CONFIG_VER, "VERSION_MIN_DEFAUT")
        # On écrit la version dans H2
        sheet.update_cell(ROW_CONFIG_VER, COL_CONFIG_VER, new_ver)
        return True
    except Exception as e: return False
    
# --- FONCTIONS UTILITAIRES ---

def add_license_row(user, key, days=None):
    """Version modifiée pour lire la version dynamique"""
    try:
        sheet = get_sheet()
        if sheet.find(key): return False, "Clé existe déjà !"
        
        # 1. On récupère la version depuis le Sheet (H2)
        version_to_use = sheet.cell(ROW_CONFIG_VER, COL_CONFIG_VER).value
        if not version_to_use: version_to_use = "7.2" # Sécurité

        # 2. Calcul date fin
        date_fin = ""
        if days:
            fin = datetime.now() + timedelta(days=int(days))
            date_fin = fin.strftime("%d/%m/%Y")

        # 3. On inscrit la ligne avec la version dynamique
        row_data = [user, key, "ACTIVE", version_to_use, "", date_fin]
        sheet.append_row(row_data)
        fb_sync_license(key, user, "ACTIVE", version_to_use, "", date_fin)
        # 4. Création du compte Firebase Auth pour ce client
        fb_email, fb_password = fb_create_firebase_user(key)
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
        sheet.update_cell(cell.row, COL_STATUS, "BANNED")
        fb_sync_license(key, "", "BANNED")
        fb_disable_firebase_user(key)
        return True, "Licence BANNIE."
    except Exception as e: return False, str(e)

def get_license_info(key):
    try:
        sheet = get_sheet()
        cell = sheet.find(key)
        if not cell: return False, "Clé introuvable."
        
        row_vals = sheet.row_values(cell.row)
        # On complète la liste avec des vides si elle est trop courte
        while len(row_vals) < 6:
            row_vals.append("")
            
        info = (f"👤 **{row_vals[0]}**\n"
                f"🔑 `{row_vals[1]}`\n"
                f"📅 Statut: {row_vals[2]}\n"
                f"💻 ID: `{row_vals[4]}`\n")
        
        if row_vals[5]:
            info += f"⏳ Expire: {row_vals[5]}"
            
        return True, info
    except Exception as e: return False, str(e)

def update_license_time(key, value):
    """
    Modifie la date ET réactive la licence.
    value peut être :
    - "30" (ajoute 30 jours à partir d'aujourd'hui)
    - "01/01/2026" (met cette date précise)
    - "unlimited" (efface la date)
    """
    try:
        sheet = get_sheet()
        cell = sheet.find(key)
        if not cell: return False, "Clé introuvable."
        
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
        sheet.update_cell(cell.row, COL_EXP, new_date_str)
        sheet.update_cell(cell.row, COL_STATUS, "ACTIVE")
        fb_sync_license(key, "", "ACTIVE", date_fin=new_date_str)
        fb_enable_firebase_user(key)
        return True, new_date_str

    except Exception as e: return False, str(e)

def set_expired(key):
    """Force le statut EXPIRED pour une clé donnée"""
    try:
        sheet = get_sheet()
        cell = sheet.find(key)
        if not cell: return False
        # On écrit EXPIRED dans la colonne statut (3)
        sheet.update_cell(cell.row, COL_STATUS, "EXPIRED")
        return True
    except: return False

# ==========================================
# 👂 ÉCOUTE TELEGRAM
# ==========================================
client_tg = TelegramClient('admin_session', API_ID, API_HASH)

@client_tg.on(events.NewMessage(chats=SECURITY_CHANNEL_ID))
async def main_handler(event):
    msg = event.raw_text.strip()
    
    # --- 1. DETECTION CONNEXION AUTOMATIQUE ---
    if "CONNEXION RÉUSSIE" in msg:

        print("\n📨 Nouveau login détecté...")

        key_match = re.search(r"🔑 Clé:\s*[`]?([^`\n]+)[`]?", msg)
        hwid_match = re.search(r"💻 PC ID:\s*[`]?([^`\n]+)[`]?", msg)
        
        if key_match and hwid_match:
            licence = key_match.group(1).strip()
            hwid = hwid_match.group(1).strip()

            print(f"🔍 Traitement : {licence} | ID: {hwid}")
            
            # Appel ASYNC bloquant pour ne pas figer le bot
            result = await asyncio.to_thread(update_sheet, licence, hwid)
            
            if result == "NEW_REGISTER":
                await event.reply(f"🔒 **LICENCE VERROUILLÉE**\nAppareil lié.")
            elif result == "CONFLICT":
                await event.reply(f"🚨 **ALERTE VOL**\nID non reconnu !")
            elif result == "EXPIRED":
                await event.reply(f"⛔ **LICENCE EXPIRÉE**\nAccès refusé.")
            elif result == "NOT_FOUND":
                print(f"Clé inconnue: {licence}")

    # --- 2. GESTION DES COMMANDES ADMIN ---
    
    # A. Générateur de Clé (/gen User [Jours])
    elif msg.lower().startswith("/gen"):
        try:
            parts = msg.split()
            if len(parts) < 2:
                await event.reply("❌ Usage: `/gen Prenom [30]`")
                return
            
            user = parts[1]
            days = parts[2] if len(parts) > 2 else None
            
            # Génération d'une clé propre (FTMO-XXXX-XXXX)
            unique_id = str(uuid.uuid4()).split('-')[1].upper() + str(uuid.uuid4()).split('-')[2].upper()
            new_key = f"FTMO-{unique_id}"
            
            await event.reply("⏳ Création de la licence et du package client...")
            success, info, fb_email, fb_password = await asyncio.to_thread(add_license_row, user, new_key, days)

            if success:
                # Génération du ZIP
                zip_buf, zip_name, exe_included = generate_client_package(user, new_key, fb_email, fb_password)

                recap = (f"✅ **PACKAGE CLIENT GÉNÉRÉ**\n"
                         f"👤 Client : {user}\n"
                         f"🔑 Clé : `{new_key}`\n"
                         f"📝 {info}\n\n"
                         f"📦 Le ZIP contient :\n"
                         f"  • `settings.json` pré-rempli\n"
                         f"{'  • `Bot_FTMO_V14.0.0.exe`' if exe_included else '  ⚠️ exe absent du VPS (ajouter `Bot_FTMO_V14.0.0.exe` à côté du bot)'}\n\n"
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

    # B. Ajout Manuel (/add User Key [Jours])
    elif msg.lower().startswith("/add"):
        try:
            parts = msg.split()
            if len(parts) < 3:
                await event.reply("❌ Usage: `/add User Key [30]`")
                return
            
            days = parts[3] if len(parts) > 3 else None
            success, info, fb_email, fb_password = await asyncio.to_thread(add_license_row, parts[1], parts[2], days)

            if success:
                reply = f"✅ Ajouté: {info}"
                if fb_email:
                    reply += (f"\n\n🔐 **Firebase:** `{fb_email}` / `{fb_password}`")
                await event.reply(reply)
            else:
                await event.reply(f"❌ {info}")
        except: pass

    # C. Info Licence (/info Key)
    elif msg.lower().startswith("/info"):
        try:
            parts = msg.split()
            if len(parts) < 2: await event.reply("❌ Usage: `/info Key`"); return
            
            success, info = await asyncio.to_thread(get_license_info, parts[1])
            if success: await event.reply(info)
            else: await event.reply(f"❌ {info}")
        except: pass

    # C-bis. Provisionner un compte Firebase pour une clé EXISTANTE (/fbcreate Key)
    elif msg.lower().startswith("/fbcreate"):
        try:
            parts = msg.split()
            if len(parts) < 2:
                await event.reply("❌ Usage: `/fbcreate CLE`")
                return
            key = parts[1]
            # On vérifie d'abord que la clé existe dans la Sheet (évite les comptes orphelins)
            found, _ = await asyncio.to_thread(get_license_info, key)
            if not found:
                await event.reply(f"❌ Clé `{key}` introuvable dans la Sheet. Crée-la d'abord (`/gen` ou `/add`).")
                return
            await event.reply("⏳ Création du compte Firebase...")
            fb_email, fb_password = await asyncio.to_thread(fb_create_firebase_user, key)
            if fb_email:
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
        content = msg[5:].strip()
        if content:
            await event.reply(f"📢 **ANNONCE:**\n\n{content}")

    # E. Reset, Del, Ban (Classiques)
    elif msg.lower().startswith("/reset"):
        try:
            key = msg.split()[1]
            success, res = await asyncio.to_thread(reset_hwid, key)
            if success: await event.reply(f"🔄 Reset OK pour `{key}`")
            else: await event.reply(f"❌ {res}")
        except: await event.reply("❌ Usage: `/reset Key`")

    elif msg.lower().startswith("/del"):
        try:
            key = msg.split()[1]
            success, res = await asyncio.to_thread(delete_license_row, key)
            if success: await event.reply(f"🗑️ Supprimé: `{key}`")
            else: await event.reply(f"❌ {res}")
        except: pass

    elif msg.lower().startswith("/ban"):
        try:
            key = msg.split()[1]
            success, res = await asyncio.to_thread(ban_license, key)
            if success: await event.reply(f"🚫 Banni: `{key}`")
        except: pass

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
            
            await event.reply("⏳ Mise à jour...")
            success, res = await asyncio.to_thread(update_license_time, key, value)
            
            if success:
                await event.reply(f"✅ **MODIFIÉ & RÉACTIVÉ**\n\n🔑 Clé: `{key}`\n📅 Nouvelle fin: **{res}**\n🟢 Statut: ACTIVE")
            else:
                await event.reply(f"❌ Erreur: {res}")
                
        except Exception as e: await event.reply(f"❌ Crash: {e}")
        
    # G. Gestion Automatique Expiration (Réception du signal du Worker)
    elif "SIGNAL EXPIRATION" in msg:
        # Le worker a envoyé : "⌛ SIGNAL EXPIRATION \n Clé: FTMO-..."
        key_match = re.search(r"Clé:\s*[`]?([^`\n]+)[`]?", msg)
        if key_match:
            key_to_kill = key_match.group(1).strip()
            # On demande au sheet de mettre à jour
            await asyncio.to_thread(set_expired, key_to_kill)
            print(f"💀 Clé {key_to_kill} marquée comme EXPIRED (Signal reçu).")

    # H. Changer la version par défaut (/set_ver 7.2)
    elif msg.lower().startswith("/set_ver"):
        try:
            parts = msg.split()
            if len(parts) < 2:
                # Si pas d'argument, on affiche la version actuelle
                current = await asyncio.to_thread(get_current_default_version)
                await event.reply(f"ℹ️ Version actuelle par défaut : **{current}**")
                return
            
            new_ver = parts[1]
            await event.reply("⏳ Mise à jour de la config...")
            
            success = await asyncio.to_thread(set_default_version, new_ver)
            
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
        try:
            query = msg.split()[1].lower()
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
        except: await event.reply("❌ Usage: `/search Pierre`")

    # K. Mise à jour de version massive
    elif msg.lower().startswith("/push_ver"):
        try:
            new_ver = msg.split()[1]
            await event.reply(f"⚠️ **ATTENTION** : Je vais passer tous les clients ACTIFS en version **{new_ver}**.\nCela peut prendre du temps...")
            
            sheet = await asyncio.to_thread(get_sheet)
            all_rows = await asyncio.to_thread(sheet.get_all_values)
            
            updates = []
            count = 0
            
            # On parcourt (sauf entete)
            for i, row in enumerate(all_rows[1:], start=2):
                # Si le client est ACTIVE (Col C = index 2)
                if len(row) > 2 and row[2] == "ACTIVE":
                    # On prépare la mise à jour de la colonne D (Version = index 3, donc Col 4)
                    # Gspread update_cell est lent, on note juste les changements
                    # Pour faire simple ici on boucle (lent mais sûr)
                    await asyncio.to_thread(sheet.update_cell, i, COL_VER, new_ver)
                    count += 1
                    
            await event.reply(f"✅ **MIGRATION TERMINÉE**\n\n🔄 {count} clients mis à jour vers la v{new_ver}.")
            
        except Exception as e: await event.reply(f"❌ Erreur: {e}")

    # L. Aide
    elif msg.lower() == "/help":
        await event.reply("**🛠️ ADMIN BOT 2.0**\n\n\n"
                          "🔑 **GÉNÉRATION**\n\n"
                          "`/gen Pierre` : Clé illimitée\n"
                          "`/gen Pierre 30` : Clé 30 jours\n"
                          "`/add Pierre Clé` : Ajout manuel\n\n\n"
                          "🔧 **GESTION**\n\n"
                          "`/info Clé` : Voir détails (PC, Date...)\n"
                          "`/fbcreate Clé` : Créer compte Firebase (clé existante)\n"
                          "`/reset Clé` : Changer de PC\n"
                          "`/ban Clé` : Bloquer accès\n"
                          "`/del Clé` : Supprimer ligne\n"
                          "`/set_ver Version` : Changer version min\n"
                          "`/push_ver VersionX` : Forcer les clients en VersionX\n"
                          "`/search Terme` : Recherche terme dans licences\n\n\n"
                          "📊 **STATISTIQUES**\n\n"
                          "`/stats_users` : Nb actifs, bannis, expirés\n\n\n"
                          "📢 **AUTRE**\n\n"
                          "`/say Texte` : Faire une annonce\n"
                          "`/time Clé *Valeur` : Modifier la durée\n"
                          "*Valeur :\n"
                          "     - nb jour a ajouter (30)\n"
                          "     - date fin (25/12/2025)\n"
                          "     - 'unlimited' pour (illimité)")


async def security_events_relay():
    """Relaie les /security_events (écrits par les bots clients) vers le canal Telegram.
    Aucun token côté client : c'est le compte du bot de licence qui poste."""
    if not (FIREBASE_ENABLED and _fb_db):
        return
    print("📡 Relais des événements de sécurité activé.")
    seen = set()
    while True:
        try:
            def _fetch():
                docs = _fb_db.collection("security_events").where("relayed", "==", False).limit(20).stream()
                return [(d.id, d.to_dict()) for d in docs]
            for doc_id, data in await asyncio.to_thread(_fetch):
                if doc_id in seen:
                    continue
                seen.add(doc_id)
                msg = data.get("message", "Événement sécurité")
                details = []
                if data.get("user"):    details.append(f"👤 {data['user']}")
                if data.get("ip"):       details.append(f"🌐 IP : {data['ip']}")
                if data.get("pc_id"):    details.append(f"💻 PC : {data['pc_id']}")
                if data.get("got_hwid"): details.append(f"💻 Machine : {data['got_hwid']}")
                full = msg + ("\n" + "\n".join(details) if details else "")
                if not SECURITY_BOT_TOKEN:
                    print("⚠️ SECURITY_BOT_TOKEN non configuré — événement non relayé.")
                    seen.discard(doc_id)
                    continue
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
        await asyncio.sleep(5)


async def main():
    print("🤖 ADMIN BOT : Prêt à gérer les licences.")
    asyncio.create_task(security_events_relay())
    await client_tg.run_until_disconnected()

if __name__ == '__main__':
    client_tg.start()
    client_tg.loop.run_until_complete(main())