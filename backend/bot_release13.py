import asyncio
import math
import re
import time
import random
import os
import json
import csv
import sys
import uuid
from functools import partial
import logging
from logging.handlers import RotatingFileHandler
from typing import Any
import threading

# --- FIREBASE SYNC ---
import firebase_sync

# --- LE VIDEUR POUR LE FICHIER SETTINGS ---
SETTINGS_LOCK = threading.Lock() 

# --- IMPORTS FASTAPI ---
from fastapi import FastAPI, BackgroundTasks, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
from pydantic import BaseModel

# --- security ---
import hmac
import hashlib
from urllib.parse import parse_qsl
from fastapi import Request
from starlette.responses import JSONResponse

from telethon.tl.types import KeyboardButtonWebView

# Configuration du Logging (Fichiers max de 5Mo, on en garde 3 au maximum)
log_formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
log_handler = RotatingFileHandler("bot_ftmo.log", maxBytes=5*1024*1024, backupCount=3, encoding='utf-8')
log_handler.setFormatter(log_formatter)

logger = logging.getLogger()
logger.setLevel(logging.INFO)
logger.addHandler(log_handler)
logging.getLogger('telethon').setLevel(logging.WARNING)

# On ajoute aussi l'affichage dans la console pour que tu vois toujours ce qu'il se passe
console_handler = logging.StreamHandler()
console_handler.setFormatter(log_formatter)
logger.addHandler(console_handler)

async def run_bg(func, *args, **kwargs):
    """Wrapper magique pour rendre MT5 non-bloquant"""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, partial(func, *args, **kwargs))

try:
    import numpy
    print("--------------------------------------------------")
    print(f"🕵️ DIAGNOSTIC - VERSION NUMPY: {numpy.__version__}")
    print(f"🕵️ DIAGNOSTIC - DOSSIER NUMPY: {numpy.__file__}")
    print("--------------------------------------------------")
except Exception as e:
    logger.critical(f"💀 ERREUR CRITIQUE CHARGEMENT NUMPY: {e}")
import aiohttp
import requests # <--- Pour la licence
from datetime import datetime, timedelta, timezone
import MetaTrader5 as mt5
from telethon import TelegramClient, events, Button
from telethon.errors import MessageNotModifiedError, MessageIdInvalidError
import subprocess # <--- Pour la commande heure

# --- SÉCURITÉ ---
# On génère un code PIN aléatoire à 4 chiffres à chaque démarrage
SECURITY_PIN = str(random.randint(1000, 9999))

# ==========================================
# 🔐 CONFIGURATION DISTRIBUTION (LICENCE)
# ==========================================
VERSION_BOT = "14.0.0"
URL_LICENCE = "https://docs.google.com/spreadsheets/d/e/2PACX-1vR15Wf5C2MTeG_4pb9rCfrTpt-qWHa-Pp49LdKXtuTsx6Rq1YiVRDo4Zi2L9-w3d19ruenFNDBRaknm/pub?gid=0&single=true&output=csv"

# ==========================================
# ⚙️ CONFIGURATION GLOBALE
# ==========================================

# --- Telegram API ---
#API_ID = 32708194
#API_HASH = '37b283f8c146b27dcd4915102cc50da8'
#BOT_TOKEN = '8330960414:AAEBWKUyOODxVPPpwx3DppXjKX2gOrxT7W0'

ADMIN_SECURITY_TOKEN = '8330960414:AAEBWKUyOODxVPPpwx3DppXjKX2gOrxT7W0'

# --- SÉCURITÉ : Lecture des clés API depuis settings.json ---
API_ID = 0
API_HASH = ""
BOT_TOKEN = ""

if os.path.exists("settings.json"):
    try:
        with open("settings.json", "r", encoding="utf-8") as f:
            st_data = json.load(f)
            API_ID = st_data.get("TELEGRAM_API_ID", 0)
            API_HASH = st_data.get("TELEGRAM_API_HASH", "")
            BOT_TOKEN = st_data.get("TELEGRAM_BOT_TOKEN", "")
    except Exception as e:
        print(f"Impossible de lire les clés API Telegram : {e}")

# Vérification de sécurité avant de lancer Telegram (Assistant de Première Connexion)
if API_ID == 0 or API_HASH == "" or BOT_TOKEN == "" or API_HASH == "A_REMPLIR_PAR_LE_CLIENT":
    print("\n" + "█"*75)
    print(" 🛑 PREMIÈRE CONNEXION : CONFIGURATION REQUISE ".center(73, "█"))
    print("█"*75 + "\n")
    print(" Bienvenue ! Avant de démarrer le bot pour la première fois,")
    print(" vous devez configurer vos accès personnels.\n")
    print(" 👉 ÉTAPE 1 : Ouvrez le fichier 'settings.json' (clic droit > Modifier).")
    print(" 👉 ÉTAPE 2 : Remplissez les 4 lignes suivantes :")
    print("      - TELEGRAM_API_ID    (Vos chiffres, SANS guillemets)")
    print("      - TELEGRAM_API_HASH  (Vos lettres/chiffres, ENTRE guillemets)")
    print("      - TELEGRAM_BOT_TOKEN (Votre token BotFather, ENTRE guillemets)\n")
    print(" 💡 Lisez le fichier texte d'installation fourni avec le bot")
    print(" pour savoir comment obtenir ces codes en 2 minutes.\n")
    print(" 👉 ÉTAPE 3 : Sauvegardez le fichier settings.json.")
    print(" 👉 ÉTAPE 4 : Relancez ce programme.\n")
    print("█"*75 + "\n")
    # Le input() est VITAL pour empêcher la fenêtre de se fermer instantanément !
    input(" Appuyez sur ENTRÉE pour fermer cette fenêtre...")
    import sys
    sys.exit()

# Canal SABR VIP
ID_SABR_VIP = -1003684008703
ID_SABR_FREE = -1003639045748
ID_CANAL_SABR_COPIE = -1003738415220

# ⚠️ LISTE DES CANAUX SOURCES (MDT_VIP, MDT_FREE, PERSO, SPACE, LIGUE, SABR_FREE, SABR_VIP, SABR_COPIE)
CHANNEL_LIST = [-1002139883659, -1001890808461, -5093508256, -1002684710444, -1001753570693, -1003639045748, ID_SABR_VIP, ID_CANAL_SABR_COPIE]

# Ton Groupe de Logs
ADMIN_ID = 0 #-1003692316672
# Groupe gestion utilisation key
SECURITY_CHANNEL_ID = -1003872071233

# --- Identité du Bot ---
MAGIC_NUMBER = 999000          # ID unique des trades de ce bot

# --- Money Management ---
RISK_PERCENT_GOLD = 0.006     # 0.6% (Défaut OR)
RISK_PERCENT_OTHER = 0.006    # 0.6% (Défaut Autres)
RISK_PERCENT_INDICES = 0.008  # 0.8% (Indices US30/NAS100)

# --- 🕒 FILTRES TEMPORELS & JOURS ---
# Par défaut : 8h-17h = 100% du risque (1.0). Le reste du temps = 50% (0.5)
# --- NOUVELLES VARIABLES PLANNING ---
# 1.0 = 100% du risque réglé. 0.5 = on divise le risque par 2.
DAY_RISK_FACTORS = {0: 1.0, 1: 1.0, 2: 1.0, 3: 1.0, 4: 1.0, 5: 1.0, 6: 1.0} 
PRIME_HOURS = [9, 10, 11, 14, 15, 16, 17]
TIME_OFF_PEAK_FACTOR = 0.5

class PlanningUpdate(BaseModel):
    day_factors: dict
    prime_hours: list
    off_peak_factor: float

# --- MODÈLES DE DONNÉES ---
class SettingUpdate(BaseModel):
    key: str
    value: Any

# 👇 AJOUTE CES DEUX LIGNES (Initialisées à None = Mode Auto par défaut)
RISK_PERCENT_MDT_VIP = 0.006       # 0.6% par défaut
RISK_PERCENT_MDT_FREE = 0.008      # 0.8% par défaut
RISK_PERCENT_SPACE = 0.006         # 0.6% par défaut
RISK_PERCENT_LIGUE = 0.006         # 0.6% par défaut
RISK_PERCENT_PERSO = 0.006         # 0.6% par défaut  

FIXED_LOT_SABR_FREE = 0.06         # Lot TOTAL par défaut pour SABR Free
FIXED_LOT_SABR_VIP = 0.06          # Lot TOTAL par défaut pour SABR VIP

MAX_DAILY_DRAWDOWN = 0.040         # Drawdown max journalier (4%) 
MAX_SPREAD_PIPS = 30.0       
MAX_SLIPPAGE = 100
FIXED_LOT_GOLD = 0.06      
FIXED_LOT_OTHER = 0.2       

MUTED_CHANNELS = []
ID_CANAL_PERSO = -5093508256
CHANNEL_TP1_PCT = {}
SABR_ZONE_MIN_DIST = {} # Mémorise la distance la plus proche atteinte par le marché
SABR_DAILY_SIGNALS = {} # Stocke les signaux du jour {1: data, 2: data...}
SABR_SIGNAL_DAY = -1    # Mémorise le jour pour remettre à zéro à minuit

PROCESSED_MSG_IDS = set() # Mémorise les IDs des messages déjà traités

CHANNELS_STRAT_2 = ["CANAL MDT_VIP", "CANAL MDT_FREE"]

PENDING_ORDER_TICKETS = set() # Pour suivre les activation des ordres différés
MARKET_CLOSED_SYMBOLS = {}

# --- Configuration Heure ---
BROKER_TIME_OFFSET = 3

# --- Stratégie de Scaling (NOUVELLE STRATÉGIE) ---
TP1_PIPS = 30
TP2_PIPS =75
TP3_PIPS = 140
TP4_PIPS = 200
TRAILING_STOP_PIPS = 85
SOFT_SL_PERCENT = 0.30
BTC_ENTRY_OFFSET = 70
DEFAULT_TP1_PCT = 0.50

MAX_DEATH_ZONE_PIPS = 85
SABR_TRAILING_ACTIVE = True
SABR_ANTI_DOUBLON_ACTIVE = True

# --- Gestion des Resets Stats ---
RESET_DATE_GLOBAL = None 
RESET_DATE_TP1 = None 
RESET_DATE_TP2 = None
RESET_DATE_TP3 = None
RESET_DATE_TP4 = None
RESET_DATE_SL  = None 

# --- Mapping ---
SYMBOL_MAPPING = {
    # OR
    "GOLD": "XAUUSD", 
    "XAUUSD#": "XAUUSD",

    # CRYPTO
    "BTC": "BTCUSD", 
    "BITCOIN": "BTCUSD",

    # INDICES US
    "US30": "US30",
    "USD30": "US30",
    "DOWJONES": "US30",
    "DJ30": "US30",       # Ajouté
    "US30CASH": "US30",   # Ajouté
    "US30 CASH": "US30",   # Ajouté

    # NASDAQ (Ta demande) 👇
    "NAS100": "US100",
    "US100": "US100",
    "NAS": "US100",       # Le fameux "NAS" court
    "NASDAQ": "US100",
    "USTECH": "US100",    # Surnom fréquent
    "US100CASH": "US100", # Surnom cash
    "NAS100CASH": "US100",

    # INDICES EU
    "GER40": "DE40",
    "DAX": "DE40",
    "DAX40": "DE40"
}

BASE_LOTS_FOR_01_PCT = {
    "AUDJPY": 0.02,#
    "AUDNZD": 0.02,#
    "AUDUSD": 0.02,#

    "BTCUSD": 0.01,#

    "CADJPY": 0.02,#

    "EURAUD": 0.02,#
    "EURJPY": 0.01,#
    "EURUSD": 0.01,#

    "GBPCAD": 0.02,#
    "GBPNZD": 0.02,#
    "GBPUSD": 0.01,#
    "GBPAUD": 0.01,#

    "NZDCAD": 0.01,#
    "NZDCHF": 0.02,#
    "NZDJPY": 0.02,#
    "NZDUSD": 0.01,#

    "US30": 0.08,# 
    "US100": 0.08,#

    "USDCAD": 0.02,#
    "USDCHF": 0.01,#
    "USDJPY": 0.02,#

    "XAUUSD": 0.01,#
}

# --- Système ---
DATA_FILENAME = "ftmo_state.txt"
STATS_FILENAME = "daily_stats.json"
HISTORY_FILENAME = "history.csv"
MEMORY_FILENAME = "trades_memory.json"
MANUAL_FILENAME = "manual_trades.json"
SETTINGS_FILENAME = "settings.json"
STATS_PER_CHANNEL_FILE = "stats_channels.json"
SOURCES_MEMORY_FILE = "active_sources.json"
DETAILED_HISTORY_FILE = "trades_detailed.csv"

BLACKLIST_WORDS = [
    "FERMEZ", "CLOSE", "MOITIÉ", "DÉPLACEZ", "BREAKEVEN", "RESULTAT", "ANNULEZ",
    "NIVEAU", "VALEURS", "PIXEL", "POCHE", "LES", "DES"
]

# --- REGEX (RETOUR A LA VERSION SIMPLE ET EFFICACE) ---
# Format 1 : "XAUUSD ACHAT" (L'ancienne méthode V1)
REGEX_F1_ENTRY = re.compile(r"([A-Z0-9#]{3,10})\s+(ACHAT|VENTE|BUY|SELL)(?:\s+([\d\.,]+))?", re.IGNORECASE | re.MULTILINE)

# Format 2 : "BUY-LIMIT #XAUUSD" (Pour le canal secondaire)
#REGEX_F2_ENTRY = re.compile(r"(ACHAT|VENTE|BUY|SELL)(?:-LIMIT|-STOP)?\s+#?([A-Z0-9]{3,10})\s+(?:À|@|AT)?\s*([\d\.,]+)?", re.IGNORECASE | re.MULTILINE)
#REGEX_F2_ENTRY = re.compile(r"(ACHAT|VENTE|BUY|SELL)(?:[\s\w]*)?\s+#?([A-Z0-9]{3,10})\s+(?:À|@|AT)?\s*([\d\.,]+)?", re.IGNORECASE | re.MULTILINE)
#REGEX_F2_ENTRY = re.compile(r"(ACHAT|VENTE|BUY|SELL)[^#\n]*?#?([A-Z0-9]{3,10})\s+(?:.*?(?:À|@|AT)\s*)?([\d\.,]+)", re.IGNORECASE | re.MULTILINE)
REGEX_F2_ENTRY = re.compile(r"(ACHAT|VENTE|BUY|SELL|ACH[EÈ]TEZ?|VENDS?|VENDEZ)[^#\n]*?#?([A-Z0-9]{3,10})(?:\s+(?:.*?(?:À|@|AT)\s*)?([\d\.,]+))?", re.IGNORECASE | re.MULTILINE)

# SL et TP (Universel)
REGEX_SL = re.compile(r"(?:SL|STOP)\s*[:=]?\s*([\d\.,]+)", re.IGNORECASE)
REGEX_TP = re.compile(r"(?:TP|TARGET)\s*[:=]?\s*([\d\.,]+)", re.IGNORECASE)

# --- REGEX SPACE ---
REGEX_SPACE_ENTRY = re.compile(r"Entrée\s*[:]\s*([\d\.,]+)", re.IGNORECASE)
REGEX_SPACE_CAP = re.compile(r"Cap\s*[:]\s*(ACHAT|VENTE|BUY|SELL)", re.IGNORECASE)
REGEX_SPACE_TP_LINE = re.compile(r"TP\d+\s*[\s\d\.,a-zA-Z]+", re.IGNORECASE) # Capture toute la ligne TP

# --- REGEX LIGUE (Message 1) ---
# "Je sell btc 88000" ou "Je buy btc 88000"
REGEX_LIGUE_INIT = re.compile(r"Je\s+(buy|sell|achète|vend)\s+([a-z0-9]+)\s+([\d\.,]+)", re.IGNORECASE)

# --- REGEX SABR ---
#REGEX_SABR_SNIPER = re.compile(r"(?:Go|Gold|Je|On|Nous)?\s*(buy|sell|achète|vend)\s+([\d\.,]+)(?!\s*-)", re.IGNORECASE)
#REGEX_SABR_ZONE = re.compile(r"(?:Go|Gold|Je|On|Nous)?\s*(buy|sell|achète|vend)\s*([\d\.,]+)\s*-\s*([\d\.,]+)", re.IGNORECASE)
#REGEX_SABR_BLIND = re.compile(r"(?:Go|Gold|Je|On|Nous)\s*(buy|sell|achète|vend)(?!\s*\d)", re.IGNORECASE)
# --- REGEX SABR ---
REGEX_SABR_SNIPER = re.compile(r"(?:Go|Gold|Je|On|Nous)?\s*(buy|sell|achète|vend|achat|vente)[^\d]{0,80}?([\d\.,]{3,})(?!\s*-)", re.IGNORECASE)
REGEX_SABR_ZONE = re.compile(r"(?:Go|Gold|Je|On|Nous)?\s*(buy|sell|achète|vend|achat|vente)[^\d]{0,80}?([\d\.,]{3,})\s*-\s*([\d\.,]{2,})", re.IGNORECASE)
REGEX_SABR_BLIND = re.compile(r"(?:Go|Gold|Je|On|Nous)\s*(buy|sell|achète|vend|achat|vente)(?!\s*\d)", re.IGNORECASE)

# Correction pour attraper les fautes "SI" au lieu de "SL", et "Derniers" avec un S
REGEX_SABR_SL = re.compile(r"(?:S[LI]|Stop|Derniers?\s*(?:S[LI])?)\s+([\d\.,]+)", re.IGNORECASE)
REGEX_SABR_UPDATE_PRICE = re.compile(r"^([\d\.,]+)$", re.IGNORECASE)
REGEX_SABR_UPDATE_ZONE = re.compile(r"^([\d\.,]+)\s*-\s*([\d\.,]+)$", re.IGNORECASE)

# --- Variables Globales ---
SABR_FORWARD_ACTIVE = True  # Active ou désactive le transfert des messages SABR
daily_start_equity = 0.0
daily_start_balance = 0.0 
current_trade_day = -1
watched_tickets = set()
# Mémorise quels TPs ont déjà été touchés pour chaque ticket
# Format : { 123456: ["TP1"], 789012: ["TP1", "TP2"] }
TRADES_PROCESSED_LEVELS = {}
active_trades_max_pips = {} 
active_trades_min_pips = {}
TELEGRAM_TO_TICKET = {}
MANAGED_MANUAL_TICKETS = set()
NOTIFIED_MANUAL_TICKETS = set()
security_triggered = False
PENDING_MESSAGES = {} # Key: ID, Value: Texte du message
# --- Mémoire pour les canaux en 2 temps (Ligue) ---
# Stocke : { "channel_id": { "symbol": "BTCUSD", "action": "ACHAT", "price": 88000, "timestamp": 123456 } }
LIGUE_CONTEXT = {}
# Stocke le contexte SABR : { "last_action": "SELL", "time": 123456789, "status": "WAITING_UPDATE" }
SABR_CONTEXT = {}
# Stocke : { "CANAL VIP": {"count": 0, "profit": 0.0, "pips": 0.0} }
CHANNEL_STATS = {}
# Stocke : { ticket_id: "CANAL VIP" }
ACTIVE_TRADE_SOURCES = {}
# Format : { ticket_id: montant_deja_compte }
PARTIAL_PROFITS = {}

CACHE_SYMBOL_POINT = {}

INITIAL_VOLUMES = {} # Cache pour éviter de spammer l'historique MT5

DASHBOARD_MSG_ID = None

# ==========================================
# 🌐 INITIALISATION DE L'API WEB (FASTAPI)
# ==========================================
app = FastAPI(title="FTMO Bot API", version="1.0")

# 🛡️ CONFIGURATION CORS (Crucial pour la Web App)
# Pour l'instant on autorise tout ('*'), on sécurisera plus tard avec le domaine Vercel exact
# 🛡️ CONFIGURATION CORS STRICTE
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://trading-dashboard-three-beige.vercel.app"], # 👈 URL EXACTE ICI
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ==========================================
# 🛡️ LE VIGILE DE SÉCURITÉ TELEGRAM (ANTI-HACK & ANTI-REPLAY)
# ==========================================
@app.middleware("http")
async def telegram_auth_middleware(request: Request, call_next):
    # On laisse passer les requêtes de "pré-vérification" et la documentation
    if request.method == "OPTIONS" or request.url.path in ["/docs", "/openapi.json"]:
        return await call_next(request)

    # 1. On cherche la carte d'identité Telegram
    init_data = request.headers.get("x-telegram-init-data")
    if not init_data:
        return JSONResponse(status_code=401, content={"detail": "Accès refusé. Veuillez ouvrir l'application depuis Telegram."})

    try:
        parsed_data = dict(parse_qsl(init_data))
        if 'hash' not in parsed_data:
            return JSONResponse(status_code=401, content={"detail": "Signature manquante."})
        
        # --- ⏱️ NOUVEAU : VÉRIFICATION EXPIRATION (ANTI REPLAY ATTACK) ---
        if 'auth_date' not in parsed_data:
            return JSONResponse(status_code=401, content={"detail": "Date d'authentification manquante."})
            
        auth_date = int(parsed_data.get('auth_date', 0))
        # Si le lien a été généré il y a plus de 24h (86400 secondes), on bloque !
        if time.time() - auth_date > 86400:
            logger.warning("🚨 TENTATIVE DE REPLAY ATTACK BLOQUÉE (Signature expirée).")
            return JSONResponse(status_code=401, content={"detail": "Session expirée. Veuillez relancer l'application depuis Telegram."})
        # -----------------------------------------------------------------

        # 2. On vérifie la signature cryptographique
        received_hash = parsed_data.pop('hash')
        data_check_string = '\n'.join(f"{k}={v}" for k, v in sorted(parsed_data.items()))
        
        secret_key = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
        expected_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
        
        if expected_hash != received_hash:
            return JSONResponse(status_code=401, content={"detail": "Piratage détecté : Mauvaise signature."})
            
        # 3. On vérifie que c'est bien LE MAÎTRE du bot (Toi)
        user_data = json.loads(parsed_data.get('user', '{}'))
        user_id = user_data.get('id')
        
        if ADMIN_ID != 0 and user_id != ADMIN_ID:
            logger.warning(f"🚨 TENTATIVE D'INTRUSION BLOQUÉE ! L'utilisateur ID {user_id} a essayé d'utiliser votre lien Ngrok.")
            return JSONResponse(status_code=403, content={"detail": "Accès refusé : Vous n'êtes pas le propriétaire de ce bot."})
            
    except Exception as e:
        return JSONResponse(status_code=401, content={"detail": "Erreur de validation."})

    # Si tout est bon, on laisse la requête passer vers les boutons !
    return await call_next(request)

# 🛡️ GLOBAL SESSION POUR EVITER LE SEMAPHORE TIMEOUT
http_session = None

client = TelegramClient('ftmo_bot_session', API_ID, API_HASH)
bot_user = TelegramClient('bot_session', API_ID, API_HASH)

# Structure Stats (On garde les anciennes clés pour compatibilité CSV)
DEFAULT_STATS = {
    "total_trades": 0,
    "sl_full": 0, "sl_secure": 0, "sl_be": 0, "sl_30": 0, "sl_80": 0, "sl_130": 0,
    "tp_final": 0,      
    "tp1_reached": 0, "tp2_reached": 0, "tp3_reached": 0, "tp4_reached": 0,
    "pips_max_history": [],
    "pips_min_history": [],
    "pips_close_history": [],
    "weekly_pips": 0.0,
    "daily_pips": 0.0,
    "weekly_profit": 0.0,
    "daily_profit": 0.0
}
daily_stats = DEFAULT_STATS.copy()

# ==========================================
# ⚙️ CONFIGURATION DU WATCHDOG INTERNE
# ==========================================
WATCH_FILE = "alive.txt"
TIMEOUT_SECONDS = 300

# ==========================================
# 💓 FONCTION POULS (A mettre dans ta logique Bot)
# ==========================================
def update_pulse():
    """Le bot signale qu'il est vivant"""
    try:
        with open(WATCH_FILE, "w") as f:
            f.write(str(time.time()))
    except: pass

# ==========================================
# 🔇 CONFIGURATION DES LOGS
# ==========================================
MODE_SILENCIEUX = True  # <--- METTRE SUR True POUR NETTOYER LA CONSOLE

def log(msg, important=False):
    """
    Affiche et sauvegarde le message
    """
    if not MODE_SILENCIEUX or important:
        if important:
            logger.warning(msg) # Apparaîtra avec le tag WARNING (facile à chercher dans le fichier)
        else:
            logger.info(msg)    # Apparaîtra avec le tag INFO

def sync_windows_time():
    """Force la synchronisation de l'heure Windows (Nécessite Admin)"""
    logger.info("⏳ Synchronisation de l'heure Windows...")
    try:
        # On tente la commande standard Windows
        subprocess.run("w32tm /resync", shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        logger.info("✅ Heure synchronisée.")
    except:
        logger.warning("⚠️ Impossible de synchro l'heure (Lancer en Admin recommandé).")

def get_pc_fingerprint():
    """Génère un ID unique basé sur le matériel (Carte mère/Réseau)"""
    try:
        # On prend l'adresse MAC convertie en hexadécimal
        return ':'.join(['{:02x}'.format((uuid.getnode() >> ele) & 0xff)
        for ele in range(0,8*6,8)][::-1]).upper()
    except:
        return "UNKNOWN_ID"

def check_license_online(silent=False):
    """Vérifie la licence, le HWID et la Date d'expiration"""
    if not silent: logger.info("🌍 Vérification de la licence en cours...")
    
    # 1. Charger la clé locale
    my_key = ""
    if os.path.exists("settings.json"):
        try:
            with open("settings.json", "r") as f:
                data = json.load(f)
                my_key = data.get("USER_LICENSE_KEY", "").strip()
        except: pass
    
    if not my_key:
        if not silent: logger.error("❌ ERREUR: Aucune clé de licence trouvée dans settings.json")
        return False

    # 2. Générer l'ID Unique de ce PC
    current_pc_id = get_pc_fingerprint()

    # 3. Vérifier sur Google Sheet
    try:
        response = requests.get(URL_LICENCE)
        if response.status_code != 200:
            if not silent: logger.error("❌ Erreur de connexion au serveur de licence.")
            return False
            
        lines = response.text.splitlines()
        reader = csv.DictReader(lines)
        
        found = False
        user_name = "Inconnu"
        
        for row in reader:
            if row['CLE_LICENCE'] == my_key:
                found = True
                user_name = row['UTILISATEUR']
                status = row['STATUS']
                ver_min = row['VERSION_MIN']
                registered_hwid = row.get('HWID', '').strip()
                # On récupère la date (Colonne F). Adapte le nom 'DATE_FIN' si ton entête CSV est différent
                date_fin_str = row.get('DATE_FIN', '').strip() 
                
                # --- A. Vérification BAN ---
                if status != "ACTIVE":
                    if not silent: logger.error(f"⛔ ACCÈS REFUSÉ. Statut: {status}")
                    return False
                
                # --- B. Vérification Version (Seulement au démarrage, pas en silent) ---
                if not silent and is_version_older(VERSION_BOT, ver_min):
                    logger.error("\n" + "="*40)
                    logger.error(f"⛔ VERSION OBSOLÈTE DÉTECTÉE")
                    logger.error(f"📉 Votre version   : {VERSION_BOT}")
                    logger.error(f"📈 Version requise : {ver_min}")
                    logger.error("="*40)
                    logger.error("💡 Une mise à jour est disponible sur Telegram.")
                    logger.error("="*40 + "\n")
                    # On ne bloque pas forcément ici pour le watchdog, mais au démarrage oui
                    if not silent:
                        logger.info("Appuyez sur Entrée pour quitter...")
                        # input() # On évite le input() bloquant si c'est un restart auto
                        return False
                
                # --- C. VÉRIFICATION ID PC ---
                if registered_hwid != "" and registered_hwid != current_pc_id:
                    if not silent:
                        logger.error("⛔ ERREUR : Licence liée à un autre ordinateur.")
                        logger.error(f"ID Attendu : {registered_hwid}")
                        logger.error(f"Votre ID   : {current_pc_id}")
                        
                        # Alerte Vol (Seulement si pas silent)
                        try:
                            msg_vol = (f"🚨 **ALERTE SÉCURITÉ**\n"
                                       f"Tentative clé `{my_key}` ({user_name})\n"
                                       f"ID Voleur: `{current_pc_id}`")
                            url = f"https://api.telegram.org/bot{ADMIN_SECURITY_TOKEN}/sendMessage"
                            payload = {"chat_id": SECURITY_CHANNEL_ID, "text": msg_vol, "parse_mode": "Markdown"}            
                            requests.post(url, json=payload, timeout=5)
                        except: pass

                    return False

                # --- D. VÉRIFICATION DATE ---
                if date_fin_str and date_fin_str.lower() not in ["unlimited", "", "vie"]:
                    try:
                        exp_date = datetime.strptime(date_fin_str, "%d/%m/%Y")
                        exp_date = exp_date + timedelta(days=1)
                        
                        if datetime.now() > exp_date:
                            # 👇 LE SIGNAL DE LA MORT (Toujours envoyé, même en silent) 👇
                            try:
                                msg_die = (f"⌛ **SIGNAL EXPIRATION**\n"
                                           f"Clé: `{my_key}`")
                                url = f"https://api.telegram.org/bot{ADMIN_SECURITY_TOKEN}/sendMessage"
                                # On envoie le message au canal de sécurité
                                requests.post(url, json={"chat_id": SECURITY_CHANNEL_ID, "text": msg_die, "parse_mode": "Markdown"}, timeout=5)
                            except: pass
                            # 👆 FIN DU SIGNAL 👆

                            if not silent: 
                                logger.error(f"⛔ LICENCE EXPIRÉE LE {date_fin_str}")

                            return False
                    except: pass
                
        if not found:
            if not silent: logger.error(f"❌ Clé de licence inconnue : {my_key}")
            return False

        # --- E. LE MOUCHARD (Succès) ---
        # On n'envoie le message Telegram QUE si on n'est PAS en mode silencieux
        if not silent:
            try:
                ip_public = requests.get('https://api.ipify.org', timeout=5).text
                
                msg_spy = (f"🕵️ **CONNEXION RÉUSSIE**\n\n"
                           f"👤 Client: **{user_name}**\n"
                           f"🔑 Clé: `{my_key}`\n"
                           f"💻 PC ID: `{current_pc_id}`\n"
                           f"🌐 IP: {ip_public}")
                
                url = f"https://api.telegram.org/bot{ADMIN_SECURITY_TOKEN}/sendMessage"
                payload = {
                    "chat_id": SECURITY_CHANNEL_ID, 
                    "text": msg_spy, 
                    "parse_mode": "Markdown"
                }            
                requests.post(url, json=payload, timeout=5)
                
            except Exception as e:
                logger.error(f"Spy Error: {e}")

            logger.info(f"✅ Licence Validée. Bienvenue {user_name} !")
            
        return True

    except Exception as e:
        if not silent: logger.error(f"❌ Erreur critique licence : {e}")
        return False # Dans le doute, on bloque ou on laisse passer selon ta sévérité
    
def is_version_older(current_ver, min_ver):
    """Compare deux versions (ex: '10.1.1' et '7.2'). Retourne True si obsolète."""
    try:
        c_parts = [int(x) for x in current_ver.split('.')]
        m_parts = [int(x) for x in min_ver.split('.')]
        for i in range(max(len(c_parts), len(m_parts))):
            c = c_parts[i] if i < len(c_parts) else 0
            m = m_parts[i] if i < len(m_parts) else 0
            if c < m: return True
            if c > m: return False
        return False
    except: return False

# ==========================================
# 🔧 UTILITAIRES DE PARSING
# ==========================================

def clean_text(text):
    # On remplace les émojis
    t = text.replace("🔥", " ").replace("📈", " ").replace("❌", " ").replace("✅", " ").replace("👑", " ").replace("🆓", " ")
    t = t.replace("📉", " ").replace("➡️", " ").replace("📞", " ").replace("🗄", " ") # J'ai ajouté ceux de tes exemples
    t = t.replace("🌌", " ").replace("🚀", " ").replace("🪐", " ").replace("🛡️", " ").replace("🪙", " ")
    t = t.replace("🟢", " ").replace("🔴", " ")

    # Flèches et symboles
    t = t.replace("→", " ").replace("->", " ")

    # 2. PROTECTION INTELLIGENTE DU TIRET DE ZONE
    # On remplace "5070-80" ou "5070 - 80" par un marqueur SOLIDE (sans _ ni -)
    t = re.sub(r'(\d+)\s*-\s*(\d+)', r'\1###SEP###\2', t)

    # On efface les mots parasites qui gênent la lecture du prix
    t = t.replace("LIMITÉ", " ").replace("LIMITE", " ").replace("LIMIT", " ")
    t = t.replace("-LIMITÉ", " ").replace("-LIMITE", " ").replace("-LIMIT", " ")
    t = t.replace("-STOP", " ")
    t = t.replace("-", " ").replace("_", " ")
    t = t.replace("/", " ")
    t = t.replace("now", " ") # Pour le canal Ligue "sell btc now"
    t = re.sub(r'(?i)zone', ' ', t)

    t = t.replace("[SABR VIP]", " ").replace("[TEST]", " ")

    # 3. FUSION DES NOMS COMPOSÉS (La correction pour ton bug US30)
    # On transforme "US30 CASH" en "US30" tout court pour aider le Regex
    t = t.replace("US30 CASH", "US30").replace("US30CASH", "US30").replace("US 30", "US30").replace("DJ 30", "US30")
    t = t.replace("US100 CASH", "US100").replace("US100CASH", "US100")
    t = t.replace("NAS100 CASH", "US100")

    t = t.replace("###SEP###", "-")

    t = re.sub(r'(?<=\d)\s+(?=\d)', '', t)

    return t.strip()

def smart_adjust_price(ref_price_str, partial_price_str):
    """
    Reconstruit un prix partiel basé sur un prix de référence.
    Ex: Ref=5090, Part=10 -> Renvoie 5110 (et non 5010 ou 10)
    """
    try:
        ref = float(ref_price_str)
        part = float(partial_price_str)

        # Si le prix partiel est déjà grand (ex: > 500), c'est sans doute un prix complet
        if part > 500: return partial_price_str
        
        # Si le prix partiel contient une virgule/point, c'est souvent un prix complet (ex: 1.05)
        if "." in partial_price_str: return partial_price_str

        # --- LOGIQUE DES 3 CANDIDATS ---
        # On cherche à quelle centaine appartient ce "bout" de prix
        
        # Base des centaines du prix de référence (ex: 5090 -> 5000)
        base_hundreds = (int(ref) // 100) * 100
        
        # Candidat 1 : Dans la même centaine (5000 + 10 = 5010)
        c0 = base_hundreds + part
        
        # Candidat 2 : Centaine suivante (5010 + 100 = 5110)
        c_plus = c0 + 100
        
        # Candidat 3 : Centaine précédente (5010 - 100 = 4910)
        c_minus = c0 - 100
        
        # On garde celui qui est le plus proche du prix de référence
        candidates = [c0, c_plus, c_minus]
        best_price = min(candidates, key=lambda x: abs(x - ref))
        
        # Sécurité : Si l'écart est délirant (> 300 points), on ne touche à rien
        if abs(best_price - ref) > 300:
            return partial_price_str
            
        return str(best_price)

    except:
        return partial_price_str

def parse_signal(raw_text, channel_id=None):
    global LIGUE_CONTEXT, SABR_CONTEXT, ID_SABR_VIP, ID_CANAL_SABR_COPIE
    text = clean_text(raw_text)

    # ==========================================
    # 🧠 PARTIE 1 : MODULE SABR (INTELLIGENT)
    # ==========================================

    is_sabr_channel = channel_id in [ID_SABR_VIP, ID_SABR_FREE, ID_CANAL_SABR_COPIE]

    if is_sabr_channel:
        local_sl = "0.0"
        m_sl_local = REGEX_SABR_SL.search(text)
        if m_sl_local:
            local_sl = m_sl_local.group(1).replace(',', '.')

        # --- DÉTECTION DU SYMBOLE SABR ---
        detected_sabr_sym = "GOLD" # Valeur par défaut
        # On fouille le texte pour voir s'il y a un symbole connu
        for word in text.replace('\n', ' ').split():
            w = word.upper()
            if w in SYMBOL_MAPPING or w in BASE_LOTS_FOR_01_PCT or w in ["US30", "USDCAD", "NAS100", "DJ30", "GER40"]:
                if w not in ["GO", "GOLD"]: # On ignore 'GO' et 'GOLD' pour éviter les faux positifs
                    detected_sabr_sym = w
                    break
        # ---------------------------------

        # Détermination du prefixe (VIP ou FREE) pour SABR
        sabr_prefix = "SABR_FREE"
        if channel_id == ID_SABR_VIP:
            sabr_prefix = "SABR_VIP"
        elif channel_id == ID_CANAL_SABR_COPIE:
            sabr_prefix = "SABR_COPIE"

        # --- NOUVEAU : Fonction pour extraire le TP le plus éloigné (CORRIGÉE) ---
        def extract_sabr_tp(txt, act):
            tps = []
            # On cherche TP, TP1, TP 2, suivi ou non de :, -, =
            for m in re.finditer(r"(?:TP|TARGET)\s*(?:\d)?\s*[:=\-]?\s*([\d\.,]+)", txt, re.IGNORECASE):
                try: 
                    val = float(m.group(1).replace(',', '.'))
                    if val > 10: tps.append(val) # Vérifie que c'est un vrai prix et pas juste "TP 2"
                except: pass
            if not tps: return "0.0"
            return str(max(tps)) if act == "ACHAT" else str(min(tps))
        # --------------------------------------------------------------

        # 1. SCÉNARIO "ZONE" (Prioritaire pour attraper "Je buy un peu 4060-40")
        m_zone = REGEX_SABR_ZONE.search(text)
        if m_zone:
            action = "ACHAT" if "BUY" in m_zone.group(1).upper() or "ACHÈTE" in m_zone.group(1).upper() else "VENTE"
            p1 = m_zone.group(2).replace(',', '.')
            raw_p2 = m_zone.group(3).replace(',', '.')
            
            p2 = smart_adjust_price(p1, raw_p2) 
            price_combined = f"{p1}|{p2}"
            sabr_tp = extract_sabr_tp(raw_text, action) # <-- Récupère le meilleur TP

            if SABR_CONTEXT.get("blind"): del SABR_CONTEXT["blind"]
            SABR_CONTEXT["last"] = {"action": action, "time": time.time(), "type": "ZONE", "prefix": sabr_prefix}
            return detected_sabr_sym, action, price_combined, local_sl, sabr_tp, f"CANAL {sabr_prefix}_ZONE"

        # 2. SCÉNARIO "SNIPER" (Prix unique)
        m_snip = REGEX_SABR_SNIPER.search(text)
        if m_snip and "-" not in text:
            action = "ACHAT" if "BUY" in m_snip.group(1).upper() or "ACHÈTE" in m_snip.group(1).upper() else "VENTE"
            price = m_snip.group(2).replace(',', '.')
            sabr_tp = extract_sabr_tp(raw_text, action) # <-- Récupère le meilleur TP
            
            if SABR_CONTEXT.get("blind"): del SABR_CONTEXT["blind"]
            SABR_CONTEXT["last"] = {"action": action, "time": time.time(), "type": "SNIPER", "prefix": sabr_prefix}
            return detected_sabr_sym, action, price, local_sl, sabr_tp, f"CANAL {sabr_prefix}_SNIPER"

        # 3. SCÉNARIO "AVEUGLE" (Go buy / Je buy)
        m_blind = REGEX_SABR_BLIND.search(text)
        if m_blind and not m_zone and not m_snip:
            action = "ACHAT" if "BUY" in m_blind.group(1).upper() or "ACHÈTE" in m_blind.group(1).upper() else "VENTE"
            
            last_blind = SABR_CONTEXT.get("blind")
            if last_blind and (time.time() - last_blind["time"] < 10) and last_blind["action"] == action:
                return None, None, None, None, None, None

            SABR_CONTEXT["blind"] = {"action": action, "time": time.time(), "prefix": sabr_prefix}
            SABR_CONTEXT["last"] = {"action": action, "time": time.time(), "type": "BLIND", "prefix": sabr_prefix}
            return detected_sabr_sym, action, None, "0.0", "0.0", f"CANAL {sabr_prefix}_BLIND"

        # 4. SUITE SCÉNARIO AVEUGLE (Update Prix Seul)
        blind_ctx = SABR_CONTEXT.get("blind")
        if blind_ctx and (time.time() - blind_ctx["time"] < 180): 
            current_prefix = blind_ctx.get("prefix", "SABR_FREE") 
            action = blind_ctx["action"]
            sabr_tp = extract_sabr_tp(raw_text, action) # <-- Récupère le meilleur TP
            
            m_up_price = REGEX_SABR_UPDATE_PRICE.search(text)
            if m_up_price:
                price = m_up_price.group(1).replace(',', '.')
                del SABR_CONTEXT["blind"]
                return detected_sabr_sym, action, price, local_sl, sabr_tp, f"CANAL {current_prefix}_SNIPER"
                
            m_up_zone = REGEX_SABR_UPDATE_ZONE.search(text)
            if m_up_zone:
                p1 = m_up_zone.group(1).replace(',', '.')
                raw_p2 = m_up_zone.group(2).replace(',', '.')
                p2 = smart_adjust_price(p1, raw_p2)
                price_combined = f"{p1}|{p2}"
                del SABR_CONTEXT["blind"]
                return detected_sabr_sym, action, price_combined, local_sl, sabr_tp, f"CANAL {current_prefix}_ZONE"

        # 5. GESTION DU SL DIFFÉRÉ
        m_sl_sabr_only = REGEX_SABR_SL.search(text)
        if m_sl_sabr_only and not m_snip and not m_zone and not m_blind:
            last_ctx = SABR_CONTEXT.get("last")
            if last_ctx and (time.time() - last_ctx["time"] < 300):
                current_prefix = last_ctx.get("prefix", "SABR_FREE")
                sl_price = m_sl_sabr_only.group(1).replace(',', '.')
                return detected_sabr_sym, "UPDATE_SL", sl_price, "0.0", "0.0", f"CANAL {current_prefix}_SL"

    # ==========================================
    # 🧠 PARTIE 2 : MODULE SPACE
    # ==========================================
    if "ENTRÉE" in text.upper() and "CAP" in text.upper():
        try:
            lines = [l.strip() for l in text.split('\n') if l.strip()]
            first_line_words = lines[0].split()
            if not first_line_words: return None, None, None, None, None, None
            rsym = first_line_words[0].upper() 
            
            m_entry = REGEX_SPACE_ENTRY.search(text)
            m_cap = REGEX_SPACE_CAP.search(text)
            price = m_entry.group(1).replace(',', '.') if m_entry else None
            action_raw = m_cap.group(1).upper() if m_cap else ""
            action = "ACHAT" if "ACHAT" in action_raw or "BUY" in action_raw else "VENTE"
            m_sl = REGEX_SL.search(text)
            sl = m_sl.group(1).replace(',', '.') if m_sl else "0.0"
            tp_matches = REGEX_SPACE_TP_LINE.findall(text)
            final_tp = "0.0"
            for line in tp_matches:
                m_val = re.search(r"([\d\.,]+)", line)
                if m_val:
                    val_str = m_val.group(1).replace(',', '.')
                    try:
                        if float(val_str) > 10: final_tp = val_str
                    except: pass
            return rsym, action, price, sl, final_tp, "CANAL SPACE"
        except: pass

    # ==========================================
    # 🧠 PARTIE 3 : MODULE LIGUE
    # ==========================================
    m_ligue_init = REGEX_LIGUE_INIT.search(text)
    if m_ligue_init:
        act_raw = m_ligue_init.group(1).upper()
        rsym = m_ligue_init.group(2).upper()
        price = m_ligue_init.group(3).replace(',', '.')
        action = "ACHAT" if "BUY" in act_raw or "ACHÈTE" in act_raw else "VENTE"
        LIGUE_CONTEXT["last"] = {"symbol": rsym, "action": action, "price": price, "time": time.time()}
        return None, None, None, None, None, None 

    m_sl = REGEX_SL.search(text)
    m_tp = REGEX_TP.search(text) 
    
    if m_sl and (m_tp or "TP" in text.upper()):
        ctx = LIGUE_CONTEXT.get("last")
        if ctx and (time.time() - ctx["time"] < 120): 
            sl = m_sl.group(1).replace(',', '.')
            all_tps = re.findall(r"TP\d+\s*[:]\s*([\d\.,]+)", text, re.IGNORECASE)
            tp = all_tps[-1].replace(',', '.') if all_tps else "0.0"
            rsym = ctx["symbol"]; action = ctx["action"]; price = ctx["price"]
            del LIGUE_CONTEXT["last"]
            return rsym, action, price, sl, tp, "CANAL LIGUE"
        
    # ==========================================
    # 🧠 PARTIE 4 : MODULES CLASSIQUES (MDT VIP, MDT FREE)
    # ==========================================
    sl = m_sl.group(1).replace(',', '.') if m_sl else "0.0"
    
    # On cherche TOUS les TP dans le texte, et on prend le TOUT DERNIER (On force le : ou =)
    all_tps = re.findall(r"(?:TP|TARGET)\s*(?:\d+)?\s*[:=]\s*([\d\.,]+)", text, re.IGNORECASE)
    tp = all_tps[-1].replace(',', '.') if all_tps else "0.0"

    # Format 1
    m1 = REGEX_F1_ENTRY.search(text)
    if m1:
        rsym = m1.group(1).replace("#", "").upper()
        act_raw = m1.group(2).upper()
        price = m1.group(3)
        action = "ACHAT" if "ACHAT" in act_raw or "BUY" in act_raw else "VENTE"
        return rsym, action, price, sl, tp, "CANAL MDT_VIP" # <-- NOUVEAU NOM

    # Format 2
    m2 = REGEX_F2_ENTRY.search(text)
    if m2:
        act_raw = m2.group(1).upper()
        rsym = m2.group(2).replace("#", "").upper()
        price = m2.group(3)
        action = "ACHAT" if "ACHAT" in act_raw or "BUY" in act_raw else "VENTE"
        return rsym, action, price, sl, tp, "CANAL MDT_FREE" # <-- NOUVEAU NOM

    return None, None, None, None, None, None
    
def get_broker_time(timestamp_input):
    """Convertit un timestamp en heure formatée avec le décalage broker"""
    # Si c'est déjà un objet datetime, on l'utilise, sinon on convertit le timestamp
    if isinstance(timestamp_input, datetime):
        dt_obj = timestamp_input
    else:
        dt_obj = datetime.fromtimestamp(timestamp_input)
        
    # On ajoute le décalage
    adjusted_time = dt_obj + timedelta(hours=BROKER_TIME_OFFSET)
    return adjusted_time.strftime('%H:%M')

def get_server_time_tick():
    """Récupère l'heure serveur via BTC (24/7) ou EURUSD (Backup)"""
    priority_symbols = ["BTCUSD", "BITCOIN", "ETHUSD", "EURUSD"]
    for sym in priority_symbols:
        # On essaie de sélectionner le symbole (synchrone, c'est très rapide)
        if mt5.symbol_select(sym, True):
            # L'appel au serveur MT5 va en arrière-plan avec AWAIT
            tick = mt5.symbol_info_tick(sym)          
            if tick: 
                return tick
    return None# Si rien ne marche (Internet coupé ?)

# ==========================================
# 🔧 RESTE DES UTILITAIRES
# ==========================================

def get_cached_point(symbol):
    global CACHE_SYMBOL_POINT
    if symbol not in CACHE_SYMBOL_POINT:
        info = mt5.symbol_info(symbol)
        if info:
            CACHE_SYMBOL_POINT[symbol] = info.point
        else:
            return 0.00001 # Valeur de secours
    return CACHE_SYMBOL_POINT[symbol]

def find_tradable_symbol(symbol_input):
    s = symbol_input.upper().strip()
    s = SYMBOL_MAPPING.get(s, s)
    if mt5.symbol_info(s):
        mt5.symbol_select(s, True)
        return s
    for suffix in [".pro", ".cash", ".i", "c", "+", ".ecn"]:
        test_sym = s + suffix
        if mt5.symbol_info(test_sym):
            mt5.symbol_select(test_sym, True)
            return test_sym
    return None

def save_memory():
    # 1. Sauvegarde des Niveaux (TP1, TP2...)
    try:
        data = {str(k): list(v) for k, v in TRADES_PROCESSED_LEVELS.items()}
        with open(MEMORY_FILENAME, "w") as f:
            json.dump(data, f)
    except Exception as e: logger.warning(f"⚠️ Save Levels Error: {e}")

    # 2. Sauvegarde des Trades Manuels (Nouveau !)
    try:
        # On transforme le set en list pour le JSON
        manual_list = list(MANAGED_MANUAL_TICKETS)
        with open(MANUAL_FILENAME, "w") as f:
            json.dump(manual_list, f)
    except Exception as e: logger.warning(f"⚠️ Save Manual Error: {e}")

    # 3. 👇 NOUVEAU : Sauvegarde des profits déjà comptés (Anti-Doublon)
    try:
        with open("partial_memory.json", "w") as f:
            json.dump(PARTIAL_PROFITS, f)
    except Exception as e: logger.warning(f"⚠️ Save Partials Error: {e}")

def load_memory():
    global TRADES_PROCESSED_LEVELS, MANAGED_MANUAL_TICKETS, PARTIAL_PROFITS
    
    # 1. Chargement des Niveaux
    if os.path.exists(MEMORY_FILENAME):
        try:
            with open(MEMORY_FILENAME, "r") as f:
                data = json.load(f)
                TRADES_PROCESSED_LEVELS = {int(k): set(v) for k, v in data.items()}
            logger.info(f"🧠 Mémoire Niveaux restaurée ({len(TRADES_PROCESSED_LEVELS)} trades)")
        except Exception as e: logger.warning(f"⚠️ Load Levels Error: {e}")

    # 2. Chargement des Trades Manuels (Nouveau !)
    if os.path.exists(MANUAL_FILENAME):
        try:
            with open(MANUAL_FILENAME, "r") as f:
                data_list = json.load(f)
                # On reconvertit la liste en set
                MANAGED_MANUAL_TICKETS = set(data_list)
            logger.info(f"🤝 Mémoire Manuelle restaurée ({len(MANAGED_MANUAL_TICKETS)} suivis)")
        except Exception as e: logger.warning(f"⚠️ Load Manual Error: {e}")

    # 3. 👇 NOUVEAU : Chargement des profits déjà comptés
    if os.path.exists("partial_memory.json"):
        try:
            with open("partial_memory.json", "r") as f:
                data = json.load(f)
                # JSON stocke les clés en string, on reconvertit en int (ticket ID)
                PARTIAL_PROFITS = {int(k): v for k, v in data.items()}
            logger.info(f"💰 Mémoire Partiels restaurée ({len(PARTIAL_PROFITS)} en cours)")
        except Exception as e: logger.warning(f"⚠️ Load Partials Error: {e}")

def save_settings():
    """Sauvegarde les réglages de manière 100% sécurisée (Anti-Spam & Anti-Crash)"""
    # Le Videur : On force les requêtes à passer une par une
    with SETTINGS_LOCK:
        try:
            existing_data = {}
            if os.path.exists(SETTINGS_FILENAME):
                with open(SETTINGS_FILENAME, "r") as f:
                    try: 
                        existing_data = json.load(f)
                    except Exception as e: 
                        # 🛑 SÉCURITÉ ABSOLUE : Si le fichier est illisible à ce moment précis, 
                        # on annule la sauvegarde pour ne surtout pas écraser la clé de licence !
                        logger.error(f"⚠️ Fichier settings occupé, sauvegarde ignorée pour protéger la licence.")
                        return 

            # 2. On prépare les nouvelles valeurs à mettre à jour
            new_values = {
                "RISK_PERCENT_GOLD": RISK_PERCENT_GOLD,
                "RISK_PERCENT_OTHER": RISK_PERCENT_OTHER,
                "RISK_PERCENT_MDT_VIP": RISK_PERCENT_MDT_VIP,
                "RISK_PERCENT_MDT_FREE": RISK_PERCENT_MDT_FREE,
                "FIXED_LOT_SABR_FREE": FIXED_LOT_SABR_FREE,
                "FIXED_LOT_SABR_VIP": FIXED_LOT_SABR_VIP,
                "RISK_PERCENT_SPACE": RISK_PERCENT_SPACE,
                "RISK_PERCENT_LIGUE": RISK_PERCENT_LIGUE,
                "RISK_PERCENT_PERSO": RISK_PERCENT_PERSO,
                "MAX_DAILY_DRAWDOWN": MAX_DAILY_DRAWDOWN,
                "TRAILING_STOP_PIPS": TRAILING_STOP_PIPS,
                "SOFT_SL_PERCENT": SOFT_SL_PERCENT,
                "SABR_TRAILING_ACTIVE": SABR_TRAILING_ACTIVE,
                "SABR_ANTI_DOUBLON_ACTIVE": SABR_ANTI_DOUBLON_ACTIVE,
                "MUTED_CHANNELS": MUTED_CHANNELS,
                "CHANNEL_TP1_PCT": CHANNEL_TP1_PCT,
                "RISK_PERCENT_INDICES": RISK_PERCENT_INDICES,
                "PRIME_HOURS": PRIME_HOURS,
                "TIME_OFF_PEAK_FACTOR": TIME_OFF_PEAK_FACTOR,
                "SABR_FORWARD_ACTIVE": SABR_FORWARD_ACTIVE,
                "DAY_RISK_FACTORS": DAY_RISK_FACTORS,
                "TELEGRAM_DESTINATION_ID": TELEGRAM_DESTINATION_ID
            }
            
            # 3. On fusionne (Mise à jour)
            existing_data.update(new_values)

            # 4. On écrit le tout
            with open(SETTINGS_FILENAME, "w") as f:
                json.dump(existing_data, f, indent=4)
                
            # logger.info("💾 Réglages sauvegardés.") # Optionnel, on peut le masquer pour éviter le spam console
        except Exception as e: 
            logger.error(f"⚠️ Erreur Save Settings: {e}")


TELEGRAM_DESTINATION_ID = 0
FIREBASE_EMAIL    = ""
FIREBASE_PASSWORD = ""


def load_settings():
    """Charge les réglages au démarrage"""
    global RISK_PERCENT_GOLD, SABR_FORWARD_ACTIVE, RISK_PERCENT_OTHER, RISK_PERCENT_MDT_VIP, RISK_PERCENT_MDT_FREE, MAX_DAILY_DRAWDOWN, TRAILING_STOP_PIPS, RISK_PERCENT_SPACE, RISK_PERCENT_LIGUE, RISK_PERCENT_PERSO, SOFT_SL_PERCENT, MUTED_CHANNELS, CHANNEL_TP1_PCT, TELEGRAM_DESTINATION_ID, ADMIN_ID, RISK_PERCENT_INDICES, PRIME_HOURS, TIME_OFF_PEAK_FACTOR, DAY_RISK_FACTORS, FIXED_LOT_SABR_FREE, FIXED_LOT_SABR_VIP, SABR_TRAILING_ACTIVE, DASHBOARD_MSG_ID, FIREBASE_EMAIL, FIREBASE_PASSWORD

    if os.path.exists(SETTINGS_FILENAME):
        try:
            with open(SETTINGS_FILENAME, "r") as f:
                data = json.load(f)
                
                # On met à jour les variables globales si elles existent dans le fichier
                if "RISK_PERCENT_GOLD" in data: RISK_PERCENT_GOLD = data["RISK_PERCENT_GOLD"]
                if "RISK_PERCENT_OTHER" in data: RISK_PERCENT_OTHER = data["RISK_PERCENT_OTHER"]
                if "RISK_PERCENT_MDT_VIP" in data: RISK_PERCENT_MDT_VIP = data["RISK_PERCENT_MDT_VIP"]
                elif "RISK_PERCENT_VIP" in data: RISK_PERCENT_MDT_VIP = data["RISK_PERCENT_VIP"]
                if "RISK_PERCENT_MDT_FREE" in data: RISK_PERCENT_MDT_FREE = data["RISK_PERCENT_MDT_FREE"]
                elif "RISK_PERCENT_FREE" in data: RISK_PERCENT_MDT_FREE = data["RISK_PERCENT_FREE"]
                if "FIXED_LOT_SABR_FREE" in data: FIXED_LOT_SABR_FREE = data["FIXED_LOT_SABR_FREE"]
                if "FIXED_LOT_SABR_VIP" in data: FIXED_LOT_SABR_VIP = data["FIXED_LOT_SABR_VIP"]
                if "RISK_PERCENT_SPACE" in data: RISK_PERCENT_SPACE = data["RISK_PERCENT_SPACE"]
                if "RISK_PERCENT_LIGUE" in data: RISK_PERCENT_LIGUE = data["RISK_PERCENT_LIGUE"]
                if "RISK_PERCENT_PERSO" in data: RISK_PERCENT_PERSO = data["RISK_PERCENT_PERSO"]
                if "MAX_DAILY_DRAWDOWN" in data: MAX_DAILY_DRAWDOWN = data["MAX_DAILY_DRAWDOWN"]
                if "TRAILING_STOP_PIPS" in data: TRAILING_STOP_PIPS = data["TRAILING_STOP_PIPS"]
                if "SOFT_SL_PERCENT" in data: SOFT_SL_PERCENT = data["SOFT_SL_PERCENT"]
                if "SABR_TRAILING_ACTIVE" in data: SABR_TRAILING_ACTIVE = data["SABR_TRAILING_ACTIVE"] 
                if "MUTED_CHANNELS" in data: MUTED_CHANNELS = data["MUTED_CHANNELS"]
                if "CHANNEL_TP1_PCT" in data: CHANNEL_TP1_PCT = data["CHANNEL_TP1_PCT"]
                if "TELEGRAM_DESTINATION_ID" in data: 
                    TELEGRAM_DESTINATION_ID = data["TELEGRAM_DESTINATION_ID"]
                    ADMIN_ID = TELEGRAM_DESTINATION_ID
                if "RISK_PERCENT_INDICES" in data: RISK_PERCENT_INDICES = data["RISK_PERCENT_INDICES"]
                if "PRIME_HOURS" in data: PRIME_HOURS = data["PRIME_HOURS"]
                if "TIME_OFF_PEAK_FACTOR" in data: TIME_OFF_PEAK_FACTOR = data["TIME_OFF_PEAK_FACTOR"]
                if "SABR_FORWARD_ACTIVE" in data: SABR_FORWARD_ACTIVE = data["SABR_FORWARD_ACTIVE"]
                if "DAY_RISK_FACTORS" in data:
                    # JSON stocke les clés (jours) en string "4", on remet en int 4
                    loaded_days = data["DAY_RISK_FACTORS"]
                    DAY_RISK_FACTORS = {int(k): v for k, v in loaded_days.items()}
                if "FIREBASE_EMAIL" in data:    FIREBASE_EMAIL    = data["FIREBASE_EMAIL"]
                if "FIREBASE_PASSWORD" in data: FIREBASE_PASSWORD = data["FIREBASE_PASSWORD"]

            logger.info(f"⚙️ Ensemble des réglages chargés avec succès.")
            logger.info(f"🔕 Canaux Ignorés : {MUTED_CHANNELS}")
        except Exception as e: logger.error(f"⚠️ Erreur Load Settings: {e}")

def load_channel_data():
    global CHANNEL_STATS, ACTIVE_TRADE_SOURCES
    
    # 1. Stats cumulées
    if os.path.exists(STATS_PER_CHANNEL_FILE):
        try:
            with open(STATS_PER_CHANNEL_FILE, "r") as f:
                CHANNEL_STATS = json.load(f)
        except: pass
        
    # 2. Sources des trades en cours
    if os.path.exists(SOURCES_MEMORY_FILE):
        try:
            with open(SOURCES_MEMORY_FILE, "r") as f:
                # Convertir les clés (tickets) en int car JSON les met en string
                raw = json.load(f)
                ACTIVE_TRADE_SOURCES = {int(k): v for k, v in raw.items()}
        except: pass

def save_channel_data():
    try:
        with open(STATS_PER_CHANNEL_FILE, "w") as f: json.dump(CHANNEL_STATS, f)
        with open(SOURCES_MEMORY_FILE, "w") as f: json.dump(ACTIVE_TRADE_SOURCES, f)
    except: pass

def save_state_and_stats(day, balance_snapshot):
    # On sauvegarde le jour et la BALANCE de référence
    try:
        with open(DATA_FILENAME, "w") as f: 
            f.write(f"{day}|{balance_snapshot}")
    except: pass
    
    # Sauvegarde des stats (inchangé)
    try:
        data_to_save = daily_stats.copy()
        data_to_save["active_trades_max_pips"] = active_trades_max_pips
        data_to_save["active_trades_min_pips"] = active_trades_min_pips
        with open(STATS_FILENAME, "w") as f: json.dump(data_to_save, f)
    except: pass

def load_daily_state(server_day):
    global daily_stats, active_trades_max_pips, active_trades_min_pips
    saved_balance = None
    
    # Lecture du fichier ftmo_state.txt
    if os.path.exists(DATA_FILENAME):
        try:
            with open(DATA_FILENAME, "r") as f:
                c = f.read().strip()
                if "|" in c:
                    d, b = c.split('|')
                    # Si c'est bien le même jour qu'aujourd'hui, on récupère la balance de minuit
                    if int(d) == server_day: 
                        saved_balance = float(b)
        except: pass
        
    # Chargement des stats (inchangé)
    if os.path.exists(STATS_FILENAME) and saved_balance is not None:
        try:
            with open(STATS_FILENAME, "r") as f: 
                loaded = json.load(f)
                if "active_trades_max_pips" in loaded:
                    active_trades_max_pips = {int(k): v for k, v in loaded["active_trades_max_pips"].items()}
                    del loaded["active_trades_max_pips"]
                if "active_trades_min_pips" in loaded:
                    active_trades_min_pips = {int(k): v for k, v in loaded["active_trades_min_pips"].items()}
                    del loaded["active_trades_min_pips"]
                daily_stats.update(loaded)
        except: pass
    else: 
        daily_stats = DEFAULT_STATS.copy()
        
    return saved_balance

def archive_daily_stats_to_csv():
    file_exists = os.path.isfile(HISTORY_FILENAME)
    pips_sum = sum(daily_stats["pips_max_history"])
    pips_count = len(daily_stats["pips_max_history"])
    pips_min_sum = sum(daily_stats["pips_min_history"])
    pips_min_count = len(daily_stats["pips_min_history"])
    pips_close_sum = sum(daily_stats["pips_close_history"])
    pips_close_count = len(daily_stats["pips_close_history"])
    
    row = {
        "date": datetime.now().strftime("%Y-%m-%d"),
        "total_trades": daily_stats["total_trades"],
        "sl_full": daily_stats["sl_full"], "sl_secure": daily_stats["sl_secure"],
        "sl_be": daily_stats["sl_be"], "sl_30": daily_stats["sl_30"],
        "sl_80": daily_stats["sl_80"], "sl_130": daily_stats["sl_130"],
        "tp_final": daily_stats["tp_final"],
        "tp1_reached": daily_stats["tp1_reached"], "tp2_reached": daily_stats["tp2_reached"],
        "tp3_reached": daily_stats["tp3_reached"], "tp4_reached": daily_stats["tp4_reached"],
        "pips_max_sum": pips_sum, "pips_max_count": pips_count,
        "pips_min_sum": pips_min_sum, "pips_min_count": pips_min_count,
        "pips_close_sum": pips_close_sum, "pips_close_count": pips_close_count,
        "daily_pips": daily_stats["daily_pips"]
    }
    fieldnames = list(row.keys())
    try:
        with open(HISTORY_FILENAME, "a", newline='') as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            if not file_exists: writer.writeheader()
            writer.writerow(row)
        logger.info("✅ Stats archivées dans history.csv")
    except Exception as e: logger.error(f"⚠️ Erreur CSV: {e}")


def log_trade_detail(ticket, symbol, source, direction, open_time, close_time, duration, lots, profit, pips, mae, mfe, exit_reason):
    """Enregistre chaque trade individuellement pour analyse expert"""
    file_exists = os.path.isfile(DETAILED_HISTORY_FILE)
    
    # Conversion des timestamps en format lisible (Excel friendly) avec correction heure française
    ot_str = (datetime.fromtimestamp(open_time) - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
    ct_str = (datetime.fromtimestamp(close_time) - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
    
    row = {
        "Ticket": ticket,
        "Symbol": symbol,
        "Source": source,
        "Type": direction,
        "Open Time": ot_str,
        "Close Time": ct_str,
        "Duration (min)": int(duration / 60), # Durée en minutes
        "Lots": f"{lots:.2f}",
        "Profit": f"{profit:.2f}",
        "Pips": f"{pips:.1f}",
        "MAE (Drawdown)": f"{mae:.1f}", # Le point le plus bas atteint (ex: -15.0)
        "MFE (Max Peak)": f"{mfe:.1f}", # Le point le plus haut atteint (ex: +45.0)
        "Exit Reason": exit_reason
    }
    
    fieldnames = list(row.keys())
    
    try:
        with open(DETAILED_HISTORY_FILE, "a", newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            # On écrit l'en-tête seulement si le fichier est nouveau
            if not file_exists: writer.writeheader()
            writer.writerow(row)
            logger.info(f"📝 Trade #{ticket} archivé dans {DETAILED_HISTORY_FILE}")
    except Exception as e: logger.error(f"⚠️ Erreur CSV Detailed: {e}")

def get_long_term_stats_report():
    if not os.path.exists(HISTORY_FILENAME): return "Pas encore d'historique."
    
    data = {
        "global": {"hits": 0, "total": 0},
        "tp1": {"hits": 0, "total": 0}, "tp2": {"hits": 0, "total": 0},
        "tp3": {"hits": 0, "total": 0}, "tp4": {"hits": 0, "total": 0},
        "sl": {"hits_full": 0, "hits_secure": 0, "hits_be": 0, "hits_30": 0, "hits_80": 0, "hits_130": 0, "total": 0}
    }

    pips_max_sum = 0; pips_max_count = 0; days_count = 0
    pips_min_sum = 0; pips_min_count = 0
    pips_close_sum = 0; pips_close_count = 0
    total_pips_all_time = 0.0
    
    try:
        with open(HISTORY_FILENAME, "r") as f:
            reader = csv.DictReader(f)
            for row in reader:
                row_date = datetime.strptime(row["date"], "%Y-%m-%d")
                row_trades = int(float(row["total_trades"]))

                raw_pips = row.get("daily_pips")
                if raw_pips is None or raw_pips == "":
                    daily_p = 0.0
                else:
                    daily_p = float(raw_pips)     

                total_pips_all_time += daily_p
                
                # --- FILTRE PAR DATE (Optionnel si tu utilises les resets) ---
                if RESET_DATE_GLOBAL is None or row_date >= datetime.strptime(RESET_DATE_GLOBAL, "%Y-%m-%d"):
                    days_count += 1; data["global"]["total"] += row_trades
                    pips_max_sum += float(row["pips_max_sum"]); pips_max_count += int(float(row["pips_max_count"]))
                    
                    # 👇 GESTION SÉCURISÉE DES ANCIENNES LIGNES VIDES 👇
                    val_pips = row.get("daily_pips")
                    if val_pips and val_pips.strip(): # Si la valeur existe et n'est pas vide
                        total_pips_all_time += float(val_pips)

                    val_min_s = row.get("pips_min_sum")
                    val_min_c = row.get("pips_min_count")
                    if val_min_s and val_min_s.strip(): pips_min_sum += float(val_min_s)
                    if val_min_c and val_min_c.strip(): pips_min_count += int(float(val_min_c))

                    val_close_s = row.get("pips_close_sum")
                    val_close_c = row.get("pips_close_count")
                    if val_close_s and val_close_s.strip(): pips_close_sum += float(val_close_s)
                    if val_close_c and val_close_c.strip(): pips_close_count += int(float(val_close_c))
                    # -----

                if RESET_DATE_TP1 is None or row_date >= datetime.strptime(RESET_DATE_TP1, "%Y-%m-%d"):
                    data["tp1"]["hits"] += int(float(row["tp1_reached"])); data["tp1"]["total"] += row_trades
                if RESET_DATE_TP2 is None or row_date >= datetime.strptime(RESET_DATE_TP2, "%Y-%m-%d"):
                    data["tp2"]["hits"] += int(float(row["tp2_reached"])); data["tp2"]["total"] += row_trades
                if RESET_DATE_TP3 is None or row_date >= datetime.strptime(RESET_DATE_TP3, "%Y-%m-%d"):
                    data["tp3"]["hits"] += int(float(row["tp3_reached"])); data["tp3"]["total"] += row_trades
                if RESET_DATE_TP4 is None or row_date >= datetime.strptime(RESET_DATE_TP4, "%Y-%m-%d"):
                    data["tp4"]["hits"] += int(float(row["tp4_reached"])); data["tp4"]["total"] += row_trades
                if RESET_DATE_SL is None or row_date >= datetime.strptime(RESET_DATE_SL, "%Y-%m-%d"):
                    data["sl"]["total"] += row_trades
                    data["sl"]["hits_full"] += int(float(row["sl_full"])); data["sl"]["hits_secure"] += int(float(row["sl_secure"]))
                    data["sl"]["hits_be"] += int(float(row["sl_be"])); data["sl"]["hits_30"] += int(float(row["sl_30"]))
                    data["sl"]["hits_80"] += int(float(row["sl_80"])); data["sl"]["hits_130"] += int(float(row["sl_130"]))

    except Exception as e: return f"Erreur Lecture: {e}"

    if data["global"]["total"] == 0: return "Historique vide ou filtré."
    def pct(h, t): return (h / t * 100) if t > 0 else 0.0
    avg_pips = (pips_max_sum / pips_max_count) if pips_max_count > 0 else 0
    avg_min_pips = (pips_min_sum / pips_min_count) if pips_min_count > 0 else 0
    avg_close_pips = (pips_close_sum / pips_close_count) if pips_close_count > 0 else 0
    icon_all = "📈" if total_pips_all_time >= 0 else "📉"

    # MISE A JOUR DES LABELS DANS LE RAPPORT LONG TERME
    return (
        f"📅 **STATS LONG TERME ({days_count} Jours)**\n"
        f"\n"
        f"∑ Trades Global: {data['global']['total']}\n"
        f"\n"
        f"💰 **Total Pips: {icon_all} {total_pips_all_time:+.1f}**\n"
        f"\n"
        f"❌ **Stats SL:**\n"
        f"• SL Full: {pct(data['sl']['hits_full'], data['sl']['total']):.1f}%\n"
        f"• SL BE: {pct(data['sl']['hits_be'], data['sl']['total']):.1f}%\n"
        f"• SL +20p: {pct(data['sl']['hits_30'], data['sl']['total']):.1f}%\n"
        f"• SL +80p: {pct(data['sl']['hits_80'], data['sl']['total']):.1f}%\n"
        f"• SL +140p: {pct(data['sl']['hits_130'], data['sl']['total']):.1f}%\n"
        f"\n"
        f"✅ **Stats TP:**\n"
        f"• TP1 ({TP1_PIPS}p): {pct(data['tp1']['hits'], data['tp1']['total']):.1f}%\n"
        f"• TP2 ({TP2_PIPS}p): {pct(data['tp2']['hits'], data['tp2']['total']):.1f}%\n"
        f"• TP3 ({TP3_PIPS}p): {pct(data['tp3']['hits'], data['tp3']['total']):.1f}%\n"
        f"• TP4 ({TP4_PIPS}p): {pct(data['tp4']['hits'], data['tp4']['total']):.1f}%\n"
        f"\n"
        f"🔺 **Moyenne Max Pips:** {avg_pips:.1f} pips\n"
        f"🏁 **Moyenne Close Pips:** {avg_close_pips:.1f} pips\n"
        f"🔻 **Moyenne Min Pips:** {avg_min_pips:.1f} pips"
    )

def get_pip_divisor(symbol):
    sym = symbol.upper()
    
    # 1. CRYPTO (BTC, ETH...)
    # Sur FTMO, BTC a 2 décimales. $1 = 100 points.
    # Avec un diviseur de 1000 : 10$ de mouvement = 1 pip "bot".
    # Donc TP1 (30 pips) = 300$ de mouvement. C'est cohérent.
    if "BTC" in sym or "BITCOIN" in sym: 
        return 1000 
    
    if "ETH" in sym:
        return 100 # ETH bouge moins, 100 est souvent mieux (TP1 30 = 30$ move)

    # 2. INDICES (NASDAQ, US30, DAX...)
    # Sur FTMO, US100 a 2 décimales. 1 point d'indice = 100 points MT5.
    # Avec un diviseur de 100 : 1 point d'indice = 1 pip "bot".
    # Donc TP1 (30 pips) = 30 points d'indice. C'est cohérent.
    if "US100" in sym or "NAS100" in sym or "NASDAQ" in sym:
        return 100
        
    if "US30" in sym or "DOW" in sym or "DJ30" in sym:
        return 100 # Idem, 30 pips = 30 points de Dow Jones
        
    if "GER40" in sym or "DAX" in sym:
        return 100

    # 3. JPY (Paires en Yen)
    if "JPY" in sym: 
        return 10 

    # 4. LE RESTE (Forex Classique + XAUUSD)
    return 10

def get_daily_pnl():
    if daily_start_equity == 0: return 0.0, 0.0
    acc = mt5.account_info()
    if not acc: return 0.0, 0.0
    pnl_eur = acc.equity - daily_start_equity
    pnl_pct = (pnl_eur / daily_start_equity) * 100
    return pnl_eur, pnl_pct

def get_weekly_profit_live():
    """Calcule le PnL Réalisé de la semaine (Bot + Manuel) via l'historique"""
    if not mt5.initialize(): return 0.0
    
    # 1. Trouver le Lundi de cette semaine à 00:00
    # On utilise l'heure actuelle du serveur ou locale
    now = datetime.now()
    monday = now - timedelta(days=now.weekday())
    monday = monday.replace(hour=0, minute=0, second=0, microsecond=0)
    
    # 2. Récupérer l'historique depuis Lundi
    deals = mt5.history_deals_get(monday, now)
    
    total_profit = 0.0
    if deals:
        for d in deals:
            # On ne compte que les sorties (Entry Out) pour éviter les doublons
            if d.entry == mt5.DEAL_ENTRY_OUT or d.entry == mt5.DEAL_ENTRY_OUT_BY:
                total_profit += d.profit + d.swap + d.commission
                
    return total_profit

def get_accurate_daily_stats():
    # Connexion sécurité
    # (On ne print plus d'erreur ici pour ne pas spammer, on retourne juste 0)
    if not mt5.initialize(): return 0.0, 0.0, 0.0
    
    acc = mt5.account_info()
    if not acc: return 0.0, 0.0, 0.0

    # 1. ENCAISSÉ = On prend la somme accumulée (Fiable à 100%)
    realized_pnl = acc.balance - daily_start_balance

    # 2. FLOTTANT = Equity - Balance Actuelle (Classique)
    floating_pnl = acc.equity - acc.balance

    # 3. TOTAL JOUR
    total_daily_pnl = realized_pnl + floating_pnl
    
    # ZÉRO PRINT = TERMINAL PROPRE
    return total_daily_pnl, realized_pnl, floating_pnl

# ==========================================
# 💰 LOGIQUE JOURNALIÈRE
# ==========================================

async def init_daily_stats():
    global daily_start_balance, current_trade_day
    
    # Connexion simple
    if not mt5.initialize(): return False
    
    # On récupère le jour actuel du serveur
    tick = get_server_time_tick()
    if not tick:
        logger.error("❌ Impossible de récupérer l'heure serveur (Ni BTC, Ni EURUSD)")
        return False
    # Astuce : On utilise l'heure serveur pour définir "Aujourd'hui"
    #server_time = datetime.fromtimestamp(tick.time) - timedelta(hours=1)
    # Au lieu de get_server_time_tick(), on utilise l'horloge système + offset
    now_utc = datetime.now(timezone.utc)
    broker_now = now_utc + timedelta(hours=BROKER_TIME_OFFSET)
    day = broker_now.day
    current_trade_day = day
    
    acc = mt5.account_info()
    
    # On essaie de charger la balance de minuit depuis le fichier
    saved = load_daily_state(day)
    
    if saved is not None:
        # CAS 1 : On a déjà lancé le bot aujourd'hui, on reprend le point de référence
        daily_start_balance = saved
        await send_alert(f"🔄 **BOT RELANCÉ**\n\nSession restaurée.\nBalance Minuit Ref: {daily_start_balance:.2f}€")
    else:
        # CAS 2 : C'est la première fois aujourd'hui (ou changement de jour)
        # On prend la balance ACTUELLE comme référence pour la journée
        daily_start_balance = acc.balance
        save_state_and_stats(day, daily_start_balance)
        await send_alert(f"🆕 **SESSION DÉMARRÉE**\n\nNouvelle session, pas de sauvegarde trouvée aujourd'hui\nBalance Ref: {daily_start_balance:.2f}€")
    return True

async def send_daily_report(acc, pnl_eur, pnl_pct):
    global daily_stats, daily_start_balance
    
    # --- 1. CALCUL DU PNL JOURNALIER (ENCAISSÉ SEULEMENT) ---
    # On ignore les arguments pnl_eur/pct passés et on recalcule proprement
    # Basé strictement sur la différence de Balance
    realized_day_eur = acc.balance - daily_start_balance
    realized_day_pct = (realized_day_eur / daily_start_balance * 100) if daily_start_balance > 0 else 0.0
    
    icon = "🟢" if realized_day_eur >= 0 else "🔴"

    # --- 2. CALCUL DU PNL HEBDOMADAIRE (LIVE HISTORIQUE) ---
    # On appelle la nouvelle fonction qui scanne l'historique depuis lundi
    w_profit = get_weekly_profit_live()
    
    # Calcul du % Hebdo
    balance_start_week = acc.balance - w_profit
    w_pct = (w_profit / balance_start_week * 100) if balance_start_week > 0 else 0.0
    w_icon = "🔥" if w_profit >= 0 else "❄️"

    # --- 3. STATS TECHNIQUES (Inchangé) ---
    avg_pips = 0
    if len(daily_stats["pips_max_history"]) > 0:
        avg_pips = sum(daily_stats["pips_max_history"]) / len(daily_stats["pips_max_history"])

    avg_min = 0
    if len(daily_stats["pips_min_history"]) > 0:
        avg_min = sum(daily_stats["pips_min_history"]) / len(daily_stats["pips_min_history"])

    avg_close = 0
    if len(daily_stats["pips_close_history"]) > 0:
        avg_close = sum(daily_stats["pips_close_history"]) / len(daily_stats["pips_close_history"])

    # On garde les pips calculés par le bot (car difficile à extraire de l'historique manuel proprement)
    w_pips = daily_stats.get("weekly_pips", 0.0) 
    d_pips = daily_stats.get("daily_pips", 0.0)
    d_icon_pips = "🔥" if d_pips >= 0 else "❄️"
    
    # --- 4. LE MESSAGE (FORMAT EXACT D'ORIGINE) ---
    d_msg = (f"🌙 **RAPPORT JOURNALIER** {icon}\n"
             f"\n"
             f"💸 **Gains et Pertes:**\n"
             f" **P&L: {realized_day_eur:+.2f}€ ({realized_day_pct:+.2f}%)**\n" # <-- Ici c'est le Realized
             f" **P&L Week: {w_profit:+.2f}€ ({w_pct:+.2f}%)**\n"             # <-- Ici c'est le Live History
             f" **Trades: {daily_stats['total_trades']} | Moy. Max Pips: {avg_pips:.1f}**\n"
             f" **Pips Jour: {d_icon_pips} {d_pips:+.1f} pips**\n"
             f" **Pips Semaine: {w_icon} {w_pips:+.1f} pips**\n"
             f"\n"
             f"📊 **Stats:**\n"
             f" Moy. Max: {avg_pips:.1f}p\n Close: {avg_close:.1f}p\n Moy. Min: {avg_min:.1f}p\n"
             f"\n"
             f"🎯 **Objectifs Atteints:**\n"
             f"• TP1: {daily_stats['tp1_reached']} | TP2: {daily_stats['tp2_reached']}\n"
             f"• TP3: {daily_stats['tp3_reached']} | TP4: {daily_stats['tp4_reached']}\n"
             f"\n"
             f"🚪 **Types de Sorties (SL):**\n"
             f"• SL Full: {daily_stats['sl_full']} | SL BE: {daily_stats['sl_be']}\n"
             f"• SL +20p: {daily_stats['sl_30']} | SL +80p: {daily_stats['sl_80']}\n"
             f"• SL +140p: {daily_stats['sl_130']}")
             
    await send_alert(d_msg)
    
    #lt_msg = get_long_term_stats_report()
    await asyncio.sleep(1)
    #await send_alert(lt_msg)

async def update_daily_stats():
    global daily_start_balance, current_trade_day, daily_stats, active_trades_max_pips, NOTIFIED_MANUAL_TICKETS
    
    # On calcule l'heure Broker actuelle via l'horloge système (UTC + Offset)
    # C'est ce qui permet de fonctionner le week-end quand le marché est mort.
    now_utc = datetime.now(timezone.utc)
    broker_now = now_utc + timedelta(hours=BROKER_TIME_OFFSET)
    
    day = broker_now.day
    weekday = broker_now.weekday() 
    
    # On initialise MT5 juste pour les données de compte
    if not mt5.initialize(): return
    acc = mt5.account_info()
    if not acc: return

    # SI ON CHANGE DE JOUR (MINUIT PILE DÉTECTÉ)
    if day != current_trade_day:
        logger.info(f"🕛 MINUIT BROKER DÉTECTÉ ({broker_now.strftime('%H:%M:%S')})")
        
        final_pnl = acc.balance - daily_start_balance
        final_pct = (final_pnl / daily_start_balance * 100) if daily_start_balance > 0 else 0.0
        
        # 1. Archivage et Rapport
        archive_daily_stats_to_csv()
        await send_daily_report(acc, final_pnl, final_pct)

        await asyncio.sleep(2)
        await send_alert(get_long_term_stats_report())

        # 2. Sauvegarde du cumul semaine avant reset
        current_weekly_pips = daily_stats.get("weekly_pips", 0.0)
        current_weekly_profit = daily_stats.get("weekly_profit", 0.0)
        
        # 3. RESET TOTAL
        daily_start_balance = acc.balance 
        current_trade_day = day
        daily_stats = DEFAULT_STATS.copy()
        active_trades_max_pips = {}
        NOTIFIED_MANUAL_TICKETS = set() 
        
        # 4. Logique Hebdomadaire
        if weekday == 0: # Lundi
            daily_stats["weekly_pips"] = 0.0
            daily_stats["weekly_profit"] = 0.0 
        else:
            daily_stats["weekly_pips"] = current_weekly_pips
            daily_stats["weekly_profit"] = current_weekly_profit

        save_state_and_stats(day, daily_start_balance)
        await send_alert(f"📅 **NOUVEAU JOUR ({day})**\n\nCompteurs remis à zéro.\nBalance Ref: {daily_start_balance:.2f}€")
    else:
        # Sauvegarde silencieuse de l'état actuel
        save_state_and_stats(current_trade_day, daily_start_balance)

def check_daily_max_loss():
    # On récupère le vrai P&L calculé depuis l'historique serveur
    current_daily_pnl, _, _ = get_accurate_daily_stats()
    
    acc = mt5.account_info()
    if not acc: return False
    
    # Calcul de la limite en argent (ex: 4% de la balance actuelle)
    # Note : Pour être ultra précis FTMO, on devrait prendre la balance à minuit, 
    # mais acc.balance est suffisant car elle ne change que quand un trade est fermé.
    limit_amount = acc.balance * MAX_DAILY_DRAWDOWN 
    
    # Si on est en perte de plus de X (ex: -400€)
    if current_daily_pnl <= -limit_amount:
        return True
        
    return False

async def get_last_used_lot(symbol):
    # 1. Positions en cours (ASYNC)
    open_pos = await run_bg(mt5.positions_get, symbol=symbol)
    if open_pos and len(open_pos) > 0:
        return open_pos[-1].volume

    # 2. Historique (ASYNC)
    now = datetime.now()
    past = now - timedelta(days=30)
    
    # C'est ici que ça pouvait freezer :
    deals = await run_bg(mt5.history_deals_get, past, now, group=symbol)
    
    if deals and len(deals) > 0:
        for deal in reversed(deals):
            if deal.entry == mt5.DEAL_ENTRY_IN:
                return deal.volume
                
    return None

async def get_safe_lot_size(symbol, signal_type, sl_price, entry_price_ref, channel_source=None):
    # 1. Sélection Symbole (SYNCHRONE)
    await run_bg(mt5.symbol_select, "EURUSD", True)
    acc = await run_bg(mt5.account_info)
    if not acc: return 0.0, 0.0

    cap_multiplier = max(1, math.floor((acc.balance + 4000) / 10000))

    # 0. GESTION DES LOTS FIXES (SABR)
    if channel_source:
        if "SABR_VIP" in channel_source or "SABR_COPIE" in channel_source: return FIXED_LOT_SABR_VIP, 0.0
        if "SABR" in channel_source: return FIXED_LOT_SABR_FREE, 0.0

    # 1. CALCUL DU RISQUE DE BASE PROPRE
    base_risk_pct = 0.006 
    
    sym_up = symbol.upper()
    is_gold = "XAU" in sym_up or "GOLD" in sym_up
    is_index = "US30" in sym_up or "US100" in sym_up or "NAS" in sym_up or "DAX" in sym_up or "DE40" in sym_up

    # --- Override spécifique selon les canaux ---
    if channel_source == "CANAL MDT_VIP" and RISK_PERCENT_MDT_VIP is not None: base_risk_pct = RISK_PERCENT_MDT_VIP
    elif channel_source == "CANAL MDT_FREE" and RISK_PERCENT_MDT_FREE is not None: base_risk_pct = RISK_PERCENT_MDT_FREE
    elif channel_source == "CANAL SPACE" and RISK_PERCENT_SPACE is not None: base_risk_pct = RISK_PERCENT_SPACE
    elif channel_source == "CANAL LIGUE" and RISK_PERCENT_LIGUE is not None: base_risk_pct = RISK_PERCENT_LIGUE
    elif channel_source == "CANAL PERSO" and RISK_PERCENT_PERSO is not None: base_risk_pct = RISK_PERCENT_PERSO
    else:
        if is_gold: base_risk_pct = RISK_PERCENT_GOLD
        elif is_index: base_risk_pct = RISK_PERCENT_INDICES
        else: base_risk_pct = RISK_PERCENT_OTHER

    used_risk_pct = base_risk_pct
    if used_risk_pct < 0.0001: used_risk_pct = 0.0001

    # CAS A : SANS SL
    if sl_price == 0.0:
        clean_sym = symbol.replace(".pro", "").replace(".cash", "").replace("#", "")
        if clean_sym in BASE_LOTS_FOR_01_PCT:
            base_lot = BASE_LOTS_FOR_01_PCT[clean_sym]
            multiplier = used_risk_pct / 0.001 
            final_lot = base_lot * multiplier * cap_multiplier
            
            info = mt5.symbol_info(symbol)
            if info:
                step = info.volume_step
                final_lot = round(final_lot / step) * step
                final_lot = max(info.volume_min, min(final_lot, info.volume_max))
            
            return final_lot, base_risk_pct

        last_lot = await get_last_used_lot(symbol)
        if last_lot and last_lot > 0: return last_lot, base_risk_pct
        return ((FIXED_LOT_GOLD * cap_multiplier) if is_gold else (FIXED_LOT_OTHER * cap_multiplier)), base_risk_pct
    
    # CAS B : AVEC SL
    risk_eur = acc.balance * used_risk_pct
    otype = mt5.ORDER_TYPE_BUY if signal_type == "ACHAT" else mt5.ORDER_TYPE_SELL
    
    if (signal_type == "ACHAT" and sl_price >= entry_price_ref) or \
       (signal_type == "VENTE" and sl_price <= entry_price_ref): return 0.0, 0.0

    cal_lot = 0.0
    try:
        loss = mt5.order_calc_profit(otype, symbol, 1.0, entry_price_ref, sl_price)
        if not loss: return 0.0, 0.0
        cal_lot = risk_eur / abs(loss)
    except: return 0.0, 0.0

    try:
        marg = mt5.order_calc_margin(otype, symbol, cal_lot, entry_price_ref)
        if marg and marg > 0:
            max_l = acc.margin_free / marg
            if cal_lot > (max_l * 0.95): cal_lot = max_l * 0.95
    except: pass

    info = mt5.symbol_info(symbol)
    if info:
        step = info.volume_step
        lot = round(cal_lot / step) * step
        lot = max(info.volume_min, min(lot, info.volume_max))
        return min(lot, 200.0), base_risk_pct
    
    return 0.0, 0.0

# ==========================================
# 🚀 TRADING & MONITORING
# ==========================================

async def close_partial(pos, pct, tp_label, pips_gained, fixed_lot=None):
    global daily_stats, PARTIAL_PROFITS
    
    # 1. SÉCURITÉ PETITS COMPTES
    if round(pos.volume, 2) <= 0.01:
        return True
        
    # 2. CALCUL EXACT DU LOT (Avec respect du volume_step du broker)
    info_sym = await run_bg(mt5.symbol_info, pos.symbol)
    step = info_sym.volume_step if info_sym else 0.01
    
    if fixed_lot:
        raw_lot = fixed_lot
    else:
        raw_lot = pos.volume * pct
        
    # Arrondi mathématique strict selon le broker (ex: 0.01, 0.1, etc.)
    lot = round(raw_lot / step) * step
    
    # On laisse toujours au moins le "step" minimum pour garder le trade en vie
    if lot >= pos.volume:
        lot = pos.volume - step
        
    # 3. FORMATAGE ABSOLU (Tue le bug des floats 0.4000000002)
    lot = float(f"{lot:.2f}")
    
    if lot <= 0: return True
    
    otype = mt5.ORDER_TYPE_SELL if pos.type == mt5.ORDER_TYPE_BUY else mt5.ORDER_TYPE_BUY

    # 1. TICK ASYNC
    tick = mt5.symbol_info_tick(pos.symbol)
    if not tick: return False
    price = tick.bid if otype == mt5.ORDER_TYPE_SELL else tick.ask
    
    est_profit = 0.0
    try: est_profit = mt5.order_calc_profit(pos.type, pos.symbol, lot, pos.price_open, price)
    except: pass

    req = {
        "action": mt5.TRADE_ACTION_DEAL, "symbol": pos.symbol, "volume": lot, "type": otype,
        "position": pos.ticket, "price": price, "magic": MAGIC_NUMBER,
        "comment": f"Part {tp_label}"[:25], # <--- ON COUPE À 25 CARACTÈRES MAX
        "type_filling": mt5.ORDER_FILLING_IOC,
        "deviation": MAX_SLIPPAGE
    }
    
    # --- UTILISATION DU WRAPPER BLOQUANT ---
    res = await run_bg(_send_order_blocking, req)

    if res is None:
        logger.warning(f"⚠️ Timeout MT5 sur Close Partial {tp_label} (Ticket {pos.ticket})")
        return False # On retourne False pour dire "Ça a raté, réessaie à la seconde suivante !"
    
    if res.retcode == mt5.TRADE_RETCODE_DONE:
        current_partial = PARTIAL_PROFITS.get(pos.ticket, 0.0)
        PARTIAL_PROFITS[pos.ticket] = current_partial + est_profit

        open_time = (datetime.fromtimestamp(pos.time) - timedelta(hours=1)).strftime('%H:%M')
        src_raw = ACTIVE_TRADE_SOURCES.get(pos.ticket, "INCONNU")
        src_clean = src_raw.replace("CANAL ", "").replace("_", " ")

        day_total, day_realized, day_float = get_accurate_daily_stats()
        
        day_pct = (day_total / daily_start_balance * 100) if daily_start_balance > 0 else 0.0
        icon_day = "🟢" if day_total >= 0 else "🔴"
        
        acc = await run_bg(mt5.account_info)
        bal = acc.balance if acc else 0.0
        
        await send_alert(f"✅️ **{tp_label} VALIDÉ (+{pips_gained:.0f} pips)**\n\n"
                         f"Symbole: {pos.symbol} ({open_time})\n\n"
                         f"Gain Sécurisé: **+{est_profit:.2f}€** ({lot} lots)\n\n"
                         f"Source: {src_clean}\n\n"
                         f"📊 **P&L Jour: {icon_day} {day_total:+.2f}€** ({day_pct:+.2f}%)\n"
                         f"   ├─ Encaissé: {day_realized:+.2f}€\n"
                         f"   └─ Flottant: {day_float:+.2f}€\n\n"
                         f"🏦 **Portefeuille: {bal:.2f}€**")
        return True
        
    return False

def _send_order_blocking(request):
    """Fonction synchrone exécutée dans un thread séparé"""
    
    # 🛡️ VÉRIFICATION DU CACHE (MARCHÉ FERMÉ)
    sym = request.get('symbol')
    if sym and sym in MARKET_CLOSED_SYMBOLS:
        if time.time() < MARKET_CLOSED_SYMBOLS[sym]:
            # On crée un faux objet de réponse pour éviter les crashs
            class DummyRes:
                retcode = 10018
                comment = "Market Closed (Cached)"
                order = 0
            return DummyRes()
        else:
            del MARKET_CLOSED_SYMBOLS[sym] # Le délai de 5 min est passé
            
    for i in range(3):
        res = mt5.order_send(request)
        if res is None:
            # 🚨 DIAGNOSTIC EN CAS DE REJET INSTANTANÉ DE L'API MT5
            err = mt5.last_error()
            logger.error(f"⚠️ Rejet API MT5 (Essai {i+1}/3) - Erreur {err} | Lot: {request.get('volume')}")
            time.sleep(0.5) 
            continue
            
        if res.retcode == mt5.TRADE_RETCODE_DONE:
            return res
            
        # 🛡️ CAPTURE DE L'ERREUR 10018 (MARCHÉ FERMÉ)
        elif res.retcode == 10018:
            if sym:
                logger.warning(f"💤 Marché fermé pour {sym}. Mise en veille des requêtes pendant 5 minutes...")
                MARKET_CLOSED_SYMBOLS[sym] = time.time() + 300
            return res
            
        elif res.retcode in [10015, 10016]: # Prix invalide
            # On rafraichit le prix SEULEMENT si le symbole est dans la requête
            if 'symbol' in request:
                info = mt5.symbol_info_tick(request['symbol'])
                if info:
                    if request.get('type') == mt5.ORDER_TYPE_BUY: request['price'] = info.ask
                    elif request.get('type') == mt5.ORDER_TYPE_SELL: request['price'] = info.bid
            time.sleep(0.2)
            continue
        else:
            time.sleep(0.2)
    return res

def distribute_lots(total_vol, n_orders):
    """Divise un volume total en N ordres, en distribuant le reste sur les premiers."""
    if n_orders <= 0: return []
    
    # 1. Base par ordre (arrondi inférieur)
    base = math.floor((total_vol / n_orders) * 100) / 100
    lots = [base] * n_orders
    
    # 2. Calcul du reste
    current_total = sum(lots)
    remainder = round(total_vol - current_total, 2)
    
    # 3. Distribution des miettes (0.01 par 0.01)
    i = 0
    while remainder >= 0.009: # Marge float
        lots[i] += 0.01
        lots[i] = round(lots[i], 2)
        remainder -= 0.01
        i += 1
        if i >= n_orders: i = 0 
        
    return lots

async def execute_trade(symbol, action, sl, tp, price_signal, message_date, channel_source, custom_lot_modifier=1.0):
    global daily_stats
    
    # -----------------------------------------------------------
    # 🛠️ GESTION SPECIALE: MISE A JOUR SL SABR
    # -----------------------------------------------------------
    if action == "UPDATE_SL" and "SABR" in channel_source:
        try:
            new_sl = float(price_signal)
            count = 0
            # A. Positions ouvertes
            positions = await run_bg(mt5.positions_get, symbol="XAUUSD")
            if positions:
                for pos in positions:
                    if pos.magic == MAGIC_NUMBER and pos.ticket in ACTIVE_TRADE_SOURCES:
                        if "SABR" in ACTIVE_TRADE_SOURCES[pos.ticket]:
                            req = {"action": mt5.TRADE_ACTION_SLTP, "position": pos.ticket, "sl": new_sl, "tp": pos.tp}
                            if (await run_bg(_send_order_blocking, req)).retcode == mt5.TRADE_RETCODE_DONE: count += 1
            
            # B. Ordres en attente
            orders = await run_bg(mt5.orders_get, symbol="XAUUSD")
            if orders:
                for order in orders:
                    if order.magic == MAGIC_NUMBER:
                        req = {"action": mt5.TRADE_ACTION_MODIFY, "order": order.ticket, "sl": new_sl, "tp": order.tp}
                        await run_bg(_send_order_blocking, req)

            if count > 0: await send_alert(f"🔄 **SL SABR MIS À JOUR**\nNouveau SL: {new_sl}\nTrades modifiés: {count}")
            return None 
        except: return None

    # 1. Vérification Drawdown (Disjoncteur)
    if check_daily_max_loss(): 
        await send_alert("⛔ STOP: Limite perte atteinte.")
        return None

    # --- 🕒 NOUVEAU : CALCUL DU RISQUE DYNAMIQUE (DÉBUT) ---
    now = datetime.now()
    current_day = now.weekday()  # 0=Lundi, 6=Dimanche
    current_hour = now.hour

    # On récupère le multiplicateur du jour (ex: 0.5 pour le vendredi)
    day_multiplier = DAY_RISK_FACTORS.get(current_day, 1.0)

    # On vérifie si on est en "Heure Pleine"
    is_prime = current_hour in PRIME_HOURS # <-- CORRIGÉ ICI
    time_multiplier = 1.0 if is_prime else TIME_OFF_PEAK_FACTOR

    # Le multiplicateur final qui sera appliqué aux lots
    final_risk_multiplier = day_multiplier * time_multiplier
    # --- 🕒 NOUVEAU : CALCUL DU RISQUE DYNAMIQUE (FIN) ---

    # 2. Infos Symbole & Tick (On les charge avant pour l'anti-doublon)
    s_info = mt5.symbol_info(symbol)
    if not s_info:
        logger.error(f"❌ Erreur Symbole: {symbol}")
        return None
        
    pt = s_info.point
    div = get_pip_divisor(symbol)
    tick = mt5.symbol_info_tick(symbol)
    if not tick: return None
    
    curr_price = tick.ask if action == "ACHAT" else tick.bid
    current_spread_pips = s_info.spread / 10 if "JPY" in symbol else s_info.spread

    # On récupère les positions et ordres en cours UNE SEULE FOIS pour tous les anti-doublons
    existing_pos = await run_bg(mt5.positions_get, symbol=symbol)
    existing_orders = await run_bg(mt5.orders_get, symbol=symbol)

    # ==============================================================================
    # 🛡️ ANTI-DOUBLON SABR (BASÉ SUR LA DISTANCE < 100 PIPS)
    # Bloque le trade si une position SABR existe déjà à moins de 100 pips
    # ==============================================================================
    if "SABR" in channel_source and SABR_ANTI_DOUBLON_ACTIVE:
        is_duplicate = False
        
        # Détermination du prix de référence du NOUVEAU signal
        new_ref_price = curr_price # Par défaut au marché
        if price_signal:
            try:
                parts = price_signal.split('|')
                new_ref_price = float(parts[0].replace(',', '.'))
            except: pass
            
        # 1. Vérification des Positions Ouvertes
        if existing_pos:
            for p in existing_pos:
                if p.magic == MAGIC_NUMBER:
                    src = ACTIVE_TRADE_SOURCES.get(p.ticket, "")
                    sens_achat = (p.type == mt5.ORDER_TYPE_BUY and action == "ACHAT")
                    sens_vente = (p.type == mt5.ORDER_TYPE_SELL and action == "VENTE")
                    
                    if "SABR" in src and (sens_achat or sens_vente):
                        # Calcul de la distance avec le trade existant
                        dist_pips = (abs(p.price_open - new_ref_price) / pt) / div
                        if dist_pips < 100:
                            is_duplicate = True
                            break
                        
        # 2. Vérification des Ordres en Attente
        if not is_duplicate:
            if existing_orders:
                for o in existing_orders:
                    if o.magic == MAGIC_NUMBER:
                        src = ACTIVE_TRADE_SOURCES.get(o.ticket, "")
                        sens_achat = (o.type in [mt5.ORDER_TYPE_BUY_LIMIT, mt5.ORDER_TYPE_BUY_STOP] and action == "ACHAT")
                        sens_vente = (o.type in [mt5.ORDER_TYPE_SELL_LIMIT, mt5.ORDER_TYPE_SELL_STOP] and action == "VENTE")
                        
                        if "SABR" in src and (sens_achat or sens_vente):
                            dist_pips = (abs(o.price_open - new_ref_price) / pt) / div
                            if dist_pips < 100:
                                is_duplicate = True
                                break

        if is_duplicate:
            log(f"🛡️ Doublon SABR ignoré : {symbol} {action} (Écart < 100 pips)", important=True)
            return None

    try: sl_val = float(sl)
    except: sl_val = 0.0

    # ==============================================================================
    # 🛡️ ANTI-DOUBLON MDT (CROISÉ VIP/FREE AVEC VÉRIFICATION PRIX, SL ET TP)
    # ==============================================================================
    if "MDT" in channel_source:
        is_mdt_duplicate = False
        sens_achat = (action == "ACHAT")
        
        # 1. Détermination du prix d'entrée, SL et TP du nouveau signal
        mdt_ref_price = curr_price
        if price_signal:
            try: mdt_ref_price = float(price_signal.replace(',', '.'))
            except: pass
            
        try: mdt_tp = float(tp)
        except: mdt_tp = 0.0

        # Fonction interne pour vérifier si le trade est un "Vrai" doublon croisé
        def check_is_mdt_dup(trade_price, trade_sl, trade_tp, trade_ticket):
            src = ACTIVE_TRADE_SOURCES.get(trade_ticket, "")
            # Condition A : Le trade doit venir d'un AUTRE canal MDT (VIP vs FREE)
            if "MDT" in src and src != channel_source:
                # Condition B : Vérification des écarts en pips
                dist_entry = (abs(trade_price - mdt_ref_price) / pt) / div
                dist_sl = (abs(trade_sl - sl_val) / pt) / div if sl_val > 0 else 0
                dist_tp = (abs(trade_tp - mdt_tp) / pt) / div if mdt_tp > 0 else 0
                
                # Si l'entrée est à moins de 20 pips, et que le SL/TP sont quasi identiques (< 10 pips d'écart)
                if dist_entry <= 20 and dist_sl <= 10 and dist_tp <= 10:
                    return True
            return False

        # 2. Vérification des Positions Ouvertes
        if existing_pos:
            for p in existing_pos:
                if p.magic == MAGIC_NUMBER and p.symbol == symbol:
                    if (sens_achat and p.type == mt5.ORDER_TYPE_BUY) or (not sens_achat and p.type == mt5.ORDER_TYPE_SELL):
                        if check_is_mdt_dup(p.price_open, p.sl, p.tp, p.ticket):
                            is_mdt_duplicate = True; break
                        
        # 3. Vérification des Ordres en Attente
        if not is_mdt_duplicate and existing_orders:
            for o in existing_orders:
                if o.magic == MAGIC_NUMBER and o.symbol == symbol:
                    if sens_achat and o.type in [mt5.ORDER_TYPE_BUY_LIMIT, mt5.ORDER_TYPE_BUY_STOP]:
                        if check_is_mdt_dup(o.price_open, o.sl, o.tp, o.ticket):
                            is_mdt_duplicate = True; break
                    elif not sens_achat and o.type in [mt5.ORDER_TYPE_SELL_LIMIT, mt5.ORDER_TYPE_SELL_STOP]:
                        if check_is_mdt_dup(o.price_open, o.sl, o.tp, o.ticket):
                            is_mdt_duplicate = True; break
                        
        if is_mdt_duplicate:
            log(f"🛡️ Doublon croisé MDT ignoré : {symbol} {action} a déjà été pris via l'autre canal MDT !", important=True)
            return None

    # 3. Préparation des Lots et Prix (SABR vs CLASSIC)
    trades_to_execute = [] # Liste de tuples (TargetPrice, FixedLot) -> Si TargetPrice est None = Market
    
    if "SABR" in channel_source:
        # --- A. Détermination du volume TOTAL ---
        total_vol = FIXED_LOT_SABR_FREE
        if "VIP" in channel_source or "COPIE" in channel_source: total_vol = FIXED_LOT_SABR_VIP

        # --- APPLICATION DU MULTIPLICATEUR DE RÉACTIVATION ---
        total_vol = round(total_vol * custom_lot_modifier, 2)
        # Sécurité minimale pour pouvoir poser au moins un ordre
        if total_vol < 0.01: total_vol = 0.01

        # --- B. Logique BLIND (ATTENTE) ---
        if "BLIND" in channel_source:
            await send_alert(f"⏳ **SIGNAL SABR EN APPROCHE**\n\n"
                             f"Action : {action} {symbol} (À l'aveugle)\n\n"
                             f"Le bot est prêt et attend le prix exact.\n(Délai de veille: 3 minutes max...)")
            return None # On s'arrête ici, pas d'exécution.

        # --- C. Logique SNIPER (Hybride) ---
        elif "SNIPER" in channel_source:
            target = float(price_signal)
            
            # SL par défaut (Calculé proprement selon la paire, ex: 100 pips)
            if sl_val == 0.0:
                dist_sl = 100 * pt * (div/10)
                sl_val = target - dist_sl if action == "ACHAT" else target + dist_sl
                
            dist_pips = (abs(target - curr_price) / pt) / div
            
            # Cas 1 : Très proche (<= 20 pips) -> 100% Market
            if dist_pips <= 20:
                trades_to_execute.append((None, total_vol)) # None = Market
                
            # Cas 2 : Distance Moyenne (20 < x <= 50) -> 50% Market / 50% Pending
            elif 20 < dist_pips <= 50:
                lots = distribute_lots(total_vol, 2)
                if lots[0] > 0: trades_to_execute.append((None, lots[0]))   # Market (Miettes ici)
                if lots[1] > 0: trades_to_execute.append((target, lots[1])) # Pending
                
            # Cas 3 : Loin (> 50 pips) -> 100% Pending
            else:
                trades_to_execute.append((target, total_vol))

        # --- D. Logique ZONE (Hybride + SL Global) ---
        elif "ZONE" in channel_source:
            parts = price_signal.split('|') if '|' in price_signal else [price_signal, price_signal]
            p1 = float(parts[0]) # Début zone (Proche)
            p2 = float(parts[1]) # Fin zone (Loin)
            p_mid = (p1 + p2) / 2
            
            if sl_val == 0.0:
                dist_sl = 100 * pt * (div/10)
                sl_val = p2 - dist_sl if action == "ACHAT" else p2 + dist_sl

            # --- NOUVELLE RÉPARTITION : 45% - 35% - 20% ---
            lot1 = round(total_vol * 0.45, 2)
            lot2 = round(total_vol * 0.35, 2)
            lot3 = round(total_vol - lot1 - lot2, 2)
            
            if lot1 < 0.01: lot1 = 0.01
            if lot2 < 0.01: lot2 = 0.01
            if lot3 < 0.01: lot3 = 0.01

            # Vérification distance pour le premier ordre (P1)
            dist_p1_pips = (abs(p1 - curr_price) / pt) / div
            
            price_order_1 = None if dist_p1_pips <= 30 else p1
            
            if lot1 > 0: trades_to_execute.append((price_order_1, lot1))
            if lot2 > 0: trades_to_execute.append((p_mid, lot2))
            if lot3 > 0: trades_to_execute.append((p2, lot3))
        
        # === ⚙️ DEBUG SABR (EXÉCUTION) ===
        logger.info(f"\n{'='*50}\n"
                    f"⚙️ DIAGNOSTIC SABR (ÉTAPE 2 : EXÉCUTION)\n"
                    f"💰 Volume Total Calculé : {total_vol} lots\n"
                    f"🪓 Découpage (Prix, Lot) : {trades_to_execute}\n"
                    f"🛑 SL Global appliqué    : {sl_val}\n"
                    f"{'='*50}")
            
    else:
        # --- LOGIQUE CLASSIQUE (MDT, ETC) ---
        target = None
        if price_signal:
            try: target = float(price_signal.replace(',', '.'))
            except: pass
            # Offset BTC
            if target and ("BTC" in symbol.upper() or "BITCOIN" in symbol.upper()):
                if action == "ACHAT": target += BTC_ENTRY_OFFSET 
                else: target -= BTC_ENTRY_OFFSET
        
        final_px = target
        if target:
            gap_pips = (abs(target - curr_price) / pt) / div
            if gap_pips <= 5.0: final_px = None # Market si très proche
            
        trades_to_execute.append((final_px, 0.0)) # 0.0 = Calcul Auto Risk %

    # 4. BOUCLE D'EXÉCUTION
    tickets_opened = []
    
    for tgt_price, forced_vol in trades_to_execute:
        
        tick = mt5.symbol_info_tick(symbol)
        curr_p = tick.ask if action == "ACHAT" else tick.bid
        
        if tgt_price is None or abs(tgt_price - curr_p) < s_info.point * 20:
            final_px = curr_p
            tact = mt5.TRADE_ACTION_DEAL
            otype = mt5.ORDER_TYPE_BUY if action == "ACHAT" else mt5.ORDER_TYPE_SELL
        else:
            final_px = tgt_price
            tact = mt5.TRADE_ACTION_PENDING
            if action == "ACHAT":
                otype = mt5.ORDER_TYPE_BUY_LIMIT if tgt_price < curr_p else mt5.ORDER_TYPE_BUY_STOP
            else:
                otype = mt5.ORDER_TYPE_SELL_LIMIT if tgt_price > curr_p else mt5.ORDER_TYPE_SELL_STOP

        lot = forced_vol
        final_sl = sl_val
        applied_risk_pct = 0.0
        
        # Si lot auto (Classique : MDT, Space, Ligue, Perso...)
        if lot == 0.0:
            raw_lot, base_risk_pct = await get_safe_lot_size(symbol, action, sl_val, final_px, channel_source)
            applied_risk_pct = base_risk_pct * custom_lot_modifier * final_risk_multiplier
            
            # --- 🎯 APPLICATION DU PLANNING ---
            target_lot = raw_lot * custom_lot_modifier * final_risk_multiplier
            
            info_sym = mt5.symbol_info(symbol)
            if info_sym:
                step = info_sym.volume_step
                lot = round(target_lot / step) * step
                lot = max(info_sym.volume_min, min(lot, info_sym.volume_max))
            else:
                lot = target_lot
                
        if lot < 0.01: continue

        # --- 🧮 NOUVEAU : CALCUL ET LOG DU RISQUE EXACT ---
        projected_loss_eur = 0.0
        if final_sl > 0.0:
            try:
                loss = mt5.order_calc_profit(otype, symbol, lot, final_px, final_sl)
                if loss: projected_loss_eur = abs(loss)
            except: pass
            
        risk_str = f"{applied_risk_pct*100:.2f}%" if applied_risk_pct > 0 else "Fixe"
        
        logger.info(f"\n" + "="*50 +
                    f"\n🧮 DÉTAIL DU CALCUL DE RISQUE [{symbol}]\n"
                    f"Action      : {action} à {final_px}\n"
                    f"Stop Loss   : {final_sl}\n"
                    f"Risque Visé : {risk_str}\n"
                    f"Lot Final   : {lot:.2f} lots\n"
                    f"Perte Est.  : -{projected_loss_eur:.2f} €\n" +
                    "="*50 + "\n")

        # Calcul SL Auto Classique (Risk Based) pour les autres canaux
        if final_sl == 0.0 and lot > 0 and "SABR" not in channel_source:
             try:
                 risk_eur = mt5.account_info().balance * 0.005
                 val_pt = s_info.trade_tick_value * lot
                 if val_pt > 0:
                     dist = (risk_eur / val_pt) * s_info.trade_tick_size
                     final_sl = final_px - dist if action == "ACHAT" else final_px + dist
                     final_sl = round(final_sl / s_info.point) * s_info.point
             except: pass

        try: final_tp = float(tp)
        except: final_tp = 0.0
        
        if "SABR" in channel_source and final_tp == 0.0:
            dist_tp_points = 1000 * 10 * pt * (div/10)
            if action == "ACHAT": final_tp = final_px + dist_tp_points
            else: final_tp = final_px - dist_tp_points
                
            if final_tp > 0:
                final_tp = round(final_tp / s_info.point) * s_info.point

        source_clean = channel_source.replace("CANAL ", "") if channel_source else "INCONNU"
        
        req = {
            "action": tact, "symbol": symbol, "volume": float(f"{lot:.2f}"), "type": otype,
            "price": final_px, "sl": final_sl, "tp": final_tp, 
            "deviation": MAX_SLIPPAGE, "magic": MAGIC_NUMBER, 
            "comment": f"Bot - {source_clean}", "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC if tact == mt5.TRADE_ACTION_DEAL else mt5.ORDER_FILLING_RETURN,
        }

        res = await run_bg(_send_order_blocking, req)

        if res is None:
            logger.error("⚠️ Erreur Critique: MT5 ne répond pas.")
            continue

        if res.retcode == mt5.TRADE_RETCODE_DONE:
            daily_stats["total_trades"] += 1
            ACTIVE_TRADE_SOURCES[res.order] = channel_source
            await run_bg(save_channel_data)
                
            t_str = "MARKET" if tact == mt5.TRADE_ACTION_DEAL else "PENDING"
            log(f"🔥 ORDRE {t_str} ENVOYÉ: {symbol} (Source: {channel_source})", important=True)
                
            sig_time = get_broker_time(message_date)
            safe_source = channel_source.replace("_", " ") 
            sabr_type_line = ""

            if channel_source and "SABR" in channel_source:
                if "VIP" in channel_source:
                    safe_source = "CANAL SABR VIP"
                    raw_type = channel_source.replace("CANAL SABR_VIP_", "")
                else:
                    safe_source = "CANAL SABR FREE"
                    raw_type = channel_source.replace("CANAL SABR_FREE_", "")
                
                clean_type = raw_type.replace("_", " ")
                sabr_type_line = f"\nType: {clean_type}"
            
            # --- MODIFICATION TELEGRAM ICI ---
            await send_alert(
                f"🔥 **ORDRE {t_str}**\n\n{symbol} {action}\n\n"
                f"Prix: {final_px}\nSL: {final_sl} & TP: {final_tp}\n"
                f"Signal: {sig_time}\nLot: **{lot}** (Risque: {risk_str})\n"
                f"Source: {safe_source}{sabr_type_line}",
            )

            if tact == mt5.TRADE_ACTION_PENDING: PENDING_ORDER_TICKETS.add(res.order)
            tickets_opened.append(res.order)
            await asyncio.sleep(0.2) 
            
        elif res.retcode in [10015, 10016]: 
            logger.warning("⚠️ Prix invalide (trop proche?), tentative Market forcée...")
            req['action'] = mt5.TRADE_ACTION_DEAL
            req['type_filling'] = mt5.ORDER_FILLING_IOC
            new_t = mt5.symbol_info_tick(symbol)
            req['price'] = new_t.ask if action == "ACHAT" else new_t.bid
            
            res2 = await run_bg(_send_order_blocking, req)
            if res2 and res2.retcode == mt5.TRADE_RETCODE_DONE:
                ACTIVE_TRADE_SOURCES[res2.order] = channel_source
                await run_bg(save_channel_data)
                # --- MODIFICATION TELEGRAM SECOURS ICI ---
                await send_alert(f"🔥 **ORDRE MARKET (RESCUE)**\n{symbol} {action}\nLot: **{lot}** (Risque: {risk_str})\n\n🛑 SL: {final_sl} & 🎯 TP: {final_tp}")
                tickets_opened.append(res2.order)
        else:
            logger.error(f"❌ REJET MT5 ({res.retcode}) sur {symbol} : {res.comment}")
            await send_alert(f"❌ **ERREUR D'EXÉCUTION MT5**\nImpossible d'ouvrir {action} {symbol}\nRaison : {res.comment}\n*(Code {res.retcode} : Ton MT5 est allumé ? Algo Trading activé ?)*")

    return tickets_opened

async def send_alert(msg, buttons=None):
    """Envoie une alerte avec ou sans boutons interactifs"""
    global bot_user
    if ADMIN_ID == 0: return
    try:
        # On utilise le bot_user de Telethon au lieu de aiohttp pour supporter les boutons
        await bot_user.send_message(ADMIN_ID, msg, buttons=buttons, parse_mode='md')
    except Exception as e:
        logger.error(f"⚠️ Erreur Envoi Alerte: {e}")

async def check_closed_positions():
    global watched_tickets, daily_stats, active_trades_max_pips, TELEGRAM_TO_TICKET, TRADES_PROCESSED_LEVELS, ACTIVE_TRADE_SOURCES, CHANNEL_STATS
    
    curr = await run_bg(mt5.positions_get, group="*")

    if curr is None: curr = ()
    cur_tix = {p.ticket for p in curr if p.magic == MAGIC_NUMBER or p.ticket in MANAGED_MANUAL_TICKETS}
    closed = watched_tickets - cur_tix
    
    for t in closed:

        h = await run_bg(mt5.history_deals_get, position=t)

        if h and len(h) > 0:
            last_deal = h[-1]
            if last_deal.entry != mt5.DEAL_ENTRY_OUT and last_deal.entry != mt5.DEAL_ENTRY_OUT_BY:
                continue

            prof = sum(d.profit + d.swap + d.commission for d in h)
            entry, exit = h[0], h[-1]
            sym = exit.symbol
            pt = get_cached_point(sym)
            div = get_pip_divisor(sym)
            
            if entry.type == mt5.DEAL_TYPE_BUY: pips = ((exit.price - entry.price) / pt) / div
            else: pips = ((entry.price - exit.price) / pt) / div

            daily_stats["weekly_pips"] += pips
            daily_stats["daily_pips"] += pips

            # On récupère le profit TOTAL du trade (tous les deals inclus)
            total_trade_profit = prof 
            
            # On regarde si on avait déjà compté une partie (TP1)
            already_counted = PARTIAL_PROFITS.get(t, 0.0)
            
            # On n'ajoute QUE ce qui reste à ajouter
            real_profit_to_add = total_trade_profit - already_counted

            daily_stats["daily_profit"] += real_profit_to_add
            daily_stats["weekly_profit"] += real_profit_to_add

            if t in PARTIAL_PROFITS: del PARTIAL_PROFITS[t]
            if t in INITIAL_VOLUMES: del INITIAL_VOLUMES[t]

            daily_stats["pips_close_history"].append(pips)
            
            # --- NETTOYAGE (LE CORRECTIF EST ICI) ---
            for mid, tid in list(TELEGRAM_TO_TICKET.items()):
                if tid == t: del TELEGRAM_TO_TICKET[mid]; break
            if t in MANAGED_MANUAL_TICKETS: MANAGED_MANUAL_TICKETS.remove(t)
            
            # On supprime la mémoire des TP validés pour ce trade fini
            if t in TRADES_PROCESSED_LEVELS:
                del TRADES_PROCESSED_LEVELS[t]
                await run_bg(save_memory)
            
            # --- TON CODE DE STATS (AMÉLIORÉ) ---
            # Par défaut
            reason = "🤝 Clôture Manuelle/Autre" 

            # On demande à MT5 s'il a fermé le trade spécifiquement parce qu'il a touché le TP
            is_mt5_tp = (exit.reason == 5)
            is_mt5_sl = (exit.reason == 4)
            
            # 1. Priorité au Take Profit
            if is_mt5_tp or (prof > 0 and pips >= TP4_PIPS): 
                daily_stats["tp_final"] += 1
                reason = "🏆 Take Profit Atteint"
            
            # 2. Détection du BE (Si pips proches de 0 et que c'est un code SL de MT5)
            elif (is_mt5_sl or pips < 10) and pips > -10:
                daily_stats["sl_be"] += 1
                reason = "🛡️ Breakeven (BE)"
            
            # 3. Vrai Stop Loss Full
            elif is_mt5_sl or pips <= -20: 
                daily_stats["sl_full"] += 1
                reason = f"💥 Stop Loss Full ({int(pips)} pips)"
            
            # 4. Cas du GAIN PARTIEL / SL SUIVEUR (C'est ici qu'on change tout)
            elif pips >= 10:
                # A. On met à jour les STATISTIQUES (On range dans les cases)
                if pips > 135: daily_stats["sl_130"] += 1
                elif pips > 80: daily_stats["sl_80"] += 1
                else: daily_stats["sl_30"] += 1
                
                # B. On définit le MESSAGE (On affiche la valeur exacte)
                # Cela affichera : "💰 SL Suiveur (+122 pips)"
                reason = f"💰 SL Suiveur (+{int(pips)} pips)"
            
            # --- 👇 STATS EXPERTES (MAE / MFE) 👇 ---
            # MFE = Max Pips atteint (Le sommet)
            mfe = active_trades_max_pips.get(t, 0.0)
            if t in active_trades_max_pips: del active_trades_max_pips[t] # On supprime après lecture
            
            # MAE = Min Pips atteint (Le pire drawdown)
            mae = active_trades_min_pips.get(t, 0.0)
            if mae == 999.0: mae = 0.0 # Si ça n'a jamais baissé
            if t in active_trades_min_pips: del active_trades_min_pips[t] # On supprime après lecture

            # Sauvegarde MFE/Min dans l'historique journalier
            if mfe != 0.0: daily_stats["pips_max_history"].append(mfe)
            if mae != 0.0: daily_stats["pips_min_history"].append(mae)

            # --- MISE A JOUR STATS PAR CANAL ---
            raw_source = ACTIVE_TRADE_SOURCES.get(t, "MANUEL/INCONNU")

            # 🧹 Regroupement des sous-catégories SABR pour des stats propres
            if "SABR" in raw_source:
                # On met VIP et COPIE ensemble sous le nom SABR VIP
                if "VIP" in raw_source or "COPIE" in raw_source:
                    source = "CANAL SABR VIP"
                else:
                    source = "CANAL SABR FREE"
            else:
                source = raw_source
            
            if source != "MANUEL/INCONNU":
                # Initialisation si le canal n'existe pas encore
                if source not in CHANNEL_STATS:
                    CHANNEL_STATS[source] = {"count": 0, "profit": 0.0, "pips": 0.0}
                
                # Mise à jour
                CHANNEL_STATS[source]["count"] += 1
                CHANNEL_STATS[source]["profit"] += prof
                CHANNEL_STATS[source]["pips"] += pips
                
                # Nettoyage et Sauvegarde
                del ACTIVE_TRADE_SOURCES[t]
                await run_bg(save_channel_data)

            # --- 👇 GENERATION DU FICHIER CSV DETAILLE 👇 ---
            duration = exit.time - entry.time
            direction_str = "BUY" if entry.type == mt5.DEAL_TYPE_BUY else "SELL"
            
            await run_bg(
                log_trade_detail,
                ticket=t,
                symbol=sym,
                source=source,
                direction=direction_str,
                open_time=entry.time,
                close_time=exit.time,
                duration=duration,
                lots=entry.volume,
                profit=prof,
                pips=pips,
                mae=mae,
                mfe=mfe,
                exit_reason=reason
            )
            
            # --- RECUPERATION DONNEES EXACTES (Ton format) ---
            day_total, day_realized, day_float = get_accurate_daily_stats() 
            acc = mt5.account_info()
            #start_bal = acc.balance - day_realized
            #day_pct = (day_total / start_bal * 100) if start_bal > 0 else 0.0
            day_pct = (day_total / daily_start_balance * 100) if daily_start_balance > 0 else 0.0
            icon_day = "🟢" if day_total >= 0 else "🔴"
            
            head = "🟢 **GAIN**" if prof >= 0 else "🔴 **PERTE**"
            #open_time = datetime.fromtimestamp(entry.time).strftime('%H:%M')
            open_time = (datetime.fromtimestamp(entry.time) - timedelta(hours=1)).strftime('%H:%M')
            #open_time = (datetime.fromtimestamp(entry.time)).strftime('%H:%M')
            safe_source = source.replace("CANAL ", "").replace("_", " ")

            # --- TON MESSAGE EXACT ---
            msg = (f"{head}\n\n"
                   f"Symbol: {sym} ({open_time})\n\n"
                   f"Source: {safe_source}\n\n"
                   f"Type: {reason}\n"
                   f"\n"
                   f"🔺 Max: **{mfe:+.1f}p** | 🔻 Min: **{mae:+.1f}p**\n"
                   f"\n"
                   f"💵 **RÉSULTAT NET: {prof:+.2f}€**\n" 
                   f"\n"
                   f"📊 **P&L Jour: {icon_day} {day_total:+.2f}€** ({day_pct:+.2f}%)\n"
                   f"   ├─ Encaissé: {day_realized:+.2f}€\n"
                   f"   └─ Flottant: {day_float:+.2f}€\n"
                   f"\n"
                   f"🏦 **Portefeuille: {acc.balance:.2f}€**")
            
            log(f"🏁 CLÔTURE: {sym} | Profit: {prof:+.2f}€ | {reason}", important=True)
            
            await send_alert(msg)
            
    watched_tickets = cur_tix

# ==========================================
# 🕸️ FILET DE SÉCURITÉ (ANTI-COUPURE RÉSEAU)
# ==========================================
async def safety_net_scanner():
    """Vérifie toutes les 15s si un signal a été raté pendant une coupure réseau"""
    logger.info("🕸️ Filet de sécurité (Anti-Coupure) activé.")
    await asyncio.sleep(15) # On laisse le bot démarrer tranquillement
    
    while True:
        try:
            for chat_id in CHANNEL_LIST:
                # On demande poliment à Telegram les 2 derniers messages du canal
                messages = await client.get_messages(chat_id, limit=2)
                
                for msg in messages:
                    if msg.id not in PROCESSED_MSG_IDS:
                        # On marque immédiatement le message comme "vu" pour ne pas boucler dessus
                        PROCESSED_MSG_IDS.add(msg.id)
                        
                        # On vérifie l'âge du message (on ne trade que s'il a moins de 5 minutes)
                        # msg.date.timestamp() et time.time() sont tous les deux en UTC, c'est parfait
                        age_seconds = time.time() - msg.date.timestamp()
                        
                        if age_seconds < 300: # 300 secondes = 5 minutes
                            logger.warning(f"🕸️ MESSAGE RATÉ ATTRAPÉ (Coupure réseau) ! ID: {msg.id}")
                            
                            # Astuce Telethon : L'objet 'msg' (Message) possède les mêmes attributs 
                            # que 'event' (NewMessage.Event). On peut donc l'envoyer directement 
                            # dans le main_handler comme si de rien n'était !
                            await main_handler(msg)
                            
        except Exception as e:
            # Si le réseau est vraiment coupé au moment du scan, on ignore silencieusement
            pass 
        
        # On attend 15 secondes avant le prochain scan pour ne pas spammer les serveurs Telegram
        await asyncio.sleep(15)

# ==========================================
# 🖥️ LIVE DASHBOARD (MONITEUR TEMPS RÉEL)
# ==========================================

def calculate_pips(symbol: str, price_diff: float) -> float:
    # On utilise les mêmes règles de calcul que pour l'historique MT5
    pt = get_cached_point(symbol)
    div = get_pip_divisor(symbol)
    
    # Sécurité anti-crash
    if pt == 0 or div == 0: return 0.0
    
    return (price_diff / pt) / div

async def get_historical_stats(days=30, start_date_custom=None):
    from datetime import datetime, timedelta
    end_date = datetime.now()
    if start_date_custom:
        start_date = start_date_custom
    else:
        start_date = end_date - timedelta(days=days)
    
    history = await run_bg(mt5.history_deals_get, start_date, end_date)
    
    # On initialise un dictionnaire complet pour matcher la structure daily_stats
    stats = {
        "profit": 0.0, "pips": 0.0, "total_trades": 0, "winrate": 0,
        "tp1_reached": 0, "tp2_reached": 0, "tp3_reached": 0, "tp4_reached": 0, "tp_final": 0,
        "sl_full": 0, "sl_be": 0, "sl_30": 0, "sl_80": 0, "sl_130": 0
    }
    
    if not history: return stats

    wins = 0
    losses = 0

    for deal in history:
        if deal.entry in [mt5.DEAL_ENTRY_OUT, mt5.DEAL_ENTRY_OUT_BY]:
            comment = deal.comment.upper()
            profit = deal.profit + deal.commission + deal.swap
            stats["profit"] += profit
            stats["total_trades"] += 1
            
            if profit > 0: wins += 1
            else: losses += 1
            
            # --- On déduit les TPs/SLs via les commentaires de MT5 ---
            if "TP1" in comment: stats["tp1_reached"] += 1
            elif "TP2" in comment: stats["tp2_reached"] += 1
            elif "TP3" in comment: stats["tp3_reached"] += 1
            elif "TP4" in comment: stats["tp4_reached"] += 1
            
            if "TP FINAL" in comment or "TAKE PROFIT" in comment or deal.reason == 5:
                stats["tp_final"] += 1

            if "BE" in comment: stats["sl_be"] += 1
            elif "PALIER 20" in comment: stats["sl_30"] += 1
            elif "PALIER 80" in comment: stats["sl_80"] += 1
            elif "PALIER 140" in comment: stats["sl_130"] += 1
            elif deal.reason == 4: stats["sl_full"] += 1

    stats["winrate"] = round((wins / stats["total_trades"] * 100), 1) if stats["total_trades"] > 0 else 0
    return stats
    
# ==========================================
# 🔌 ROUTES DE L'API REST
# ==========================================

def _get_closed_trades_today_blocking():
    """Récupère et REGROUPE les deals de sortie depuis minuit par Position (Trade)"""
    now_utc = datetime.now(timezone.utc)
    broker_now = now_utc + timedelta(hours=BROKER_TIME_OFFSET)
    midnight = broker_now.replace(hour=0, minute=0, second=0, microsecond=0)
    
    deals = mt5.history_deals_get(midnight, broker_now)
    
    grouped_trades = {}
    
    if deals:
        for d in deals:
            # On ne prend que les sorties de trades
            if d.entry in [mt5.DEAL_ENTRY_OUT, mt5.DEAL_ENTRY_OUT_BY] and d.symbol != "":
                pos_id = d.position_id
                
                # Si c'est la première fois qu'on voit ce trade aujourd'hui, on l'initialise
                if pos_id not in grouped_trades:
                    # On récupère l'historique complet pour avoir le prix/volume d'entrée
                    pos_history = mt5.history_deals_get(position=pos_id)
                    if not pos_history or len(pos_history) == 0:
                        continue
                        
                    entry_deal = pos_history[0] # Le deal d'entrée
                    trade_type = "BUY" if entry_deal.type == mt5.DEAL_TYPE_BUY else "SELL"
                    
                    grouped_trades[pos_id] = {
                        "ticket": pos_id,
                        "symbol": d.symbol,
                        "type": trade_type,
                        "initial_volume": entry_deal.volume,
                        "closed_volume": 0.0,
                        "open_price": entry_deal.price,
                        "open_time": entry_deal.time,
                        "close_price": d.price, # Sera mis à jour avec la dernière sortie
                        "profit": 0.0,
                        "last_time": d.time
                    }
                
                # On cumule les profits et les volumes fermés
                prof = d.profit + d.commission + d.swap
                grouped_trades[pos_id]["profit"] += prof
                grouped_trades[pos_id]["closed_volume"] += d.volume
                
                # On garde le prix de clôture le plus récent
                if d.time > grouped_trades[pos_id]["last_time"]:
                    grouped_trades[pos_id]["close_price"] = d.price
                    grouped_trades[pos_id]["last_time"] = d.time

    # Formatage final pour React
    closed_list = []
    for pos_id, t in grouped_trades.items():
        pt = get_cached_point(t["symbol"])
        div = get_pip_divisor(t["symbol"])
        
        # Calcul des pips (du point d'entrée jusqu'au dernier point de sortie)
        if t["type"] == "BUY":
            pips = ((t["close_price"] - t["open_price"]) / pt) / div
        else:
            pips = ((t["open_price"] - t["close_price"]) / pt) / div
            
        # Formatage du volume (ex: "0.04/0.08" ou "0.08")
        vol_closed = round(t["closed_volume"], 2)
        vol_initial = round(t["initial_volume"], 2)
        
        # Si on n'a pas tout fermé (arrondi pour éviter les erreurs de virgule flottante)
        if vol_initial - vol_closed > 0.009:
            vol_str = f"{vol_closed}/{vol_initial}"
        else:
            vol_str = f"{vol_closed}"

        open_time_str = (datetime.fromtimestamp(t["open_time"]) - timedelta(hours=1)).strftime('%H:%M')
            
        closed_list.append({
            "ticket": t["ticket"],
            "symbol": t["symbol"],
            "type": t["type"],
            "volume": vol_str, # On envoie une chaîne de texte au lieu d'un nombre
            "open_price": t["open_price"],
            "close_price": t["close_price"],
            "profit": round(t["profit"], 2),
            "pips": round(pips, 1),
            "open_time": open_time_str,
            "last_time": t["last_time"]
        })
        
    # On trie du plus récent au plus ancien
    closed_list.sort(key=lambda x: x["last_time"], reverse=True)
    return closed_list

@app.get("/api/dashboard")
async def api_get_dashboard(): 
    try:
        acc = await run_bg(mt5.account_info)
        day_total, day_realized, day_float = get_accurate_daily_stats()
        
        start_bal = acc.balance - day_realized
        day_pct = (day_total / start_bal * 100) if start_bal > 0 else 0.0

        pos_list = await run_bg(mt5.positions_get)
        active_trades = []
        if pos_list:
            for p in pos_list:
                # 🛑 NOUVEAU : On vérifie si le bot connaît ce trade
                is_managed = (p.magic == MAGIC_NUMBER or p.ticket in MANAGED_MANUAL_TICKETS)
                
                otype = "BUY" if p.type == mt5.ORDER_TYPE_BUY else "SELL"
                tick = mt5.symbol_info_tick(p.symbol)
                current_p = tick.bid if otype == "BUY" else tick.ask
                pips = calculate_pips(p.symbol, abs(p.price_open - current_p))
                if (otype == "BUY" and current_p < p.price_open) or (otype == "SELL" and current_p > p.price_open):
                    pips = -pips

                open_time_str = (datetime.fromtimestamp(p.time) - timedelta(hours=1)).strftime('%H:%M')

                active_trades.append({
                    "ticket": p.ticket,
                    "symbol": p.symbol,
                    "type": otype,
                    "volume": p.volume,
                    "open_price": p.price_open,
                    "current_price": current_p,
                    "sl": p.sl,
                    "tp": p.tp,
                    "profit": p.profit,
                    "pips": round(pips, 1),
                    "open_time": open_time_str,
                    "source": ACTIVE_TRADE_SOURCES.get(p.ticket, "Inconnu"),
                    "managed": is_managed # <-- L'info cruciale pour le bouton "Suivre"
                })

        closed_trades_today = await run_bg(_get_closed_trades_today_blocking)

        return {
            "account": {
                "balance": acc.balance,
                "equity": acc.equity,
                "free_margin": acc.margin_free
            },
            "daily_stats": {
                **daily_stats,
                "total_pnl": day_total,
                "realized_pnl": day_realized,
                "floating_pnl": day_float,
                "percentage": day_pct
            },
            "settings": {
                "risk_gold": RISK_PERCENT_GOLD,
                "risk_indices": RISK_PERCENT_INDICES,
                "risk_other": RISK_PERCENT_OTHER,
                "risk_mdt_vip": RISK_PERCENT_MDT_VIP if RISK_PERCENT_MDT_VIP else 0,
                "risk_mdt_free": RISK_PERCENT_MDT_FREE if RISK_PERCENT_MDT_FREE else 0,
                "risk_space": RISK_PERCENT_SPACE if RISK_PERCENT_SPACE else 0,
                "risk_ligue": RISK_PERCENT_LIGUE if RISK_PERCENT_LIGUE else 0,
                "risk_perso": RISK_PERCENT_PERSO if RISK_PERCENT_PERSO else 0,
                "sabr_lot_vip": FIXED_LOT_SABR_VIP,
                "sabr_lot_free": FIXED_LOT_SABR_FREE,
                "sabr_anti_doublon": SABR_ANTI_DOUBLON_ACTIVE,
                "sabr_trailing": SABR_TRAILING_ACTIVE,
                "sabr_forward": SABR_FORWARD_ACTIVE,
                "max_dd": MAX_DAILY_DRAWDOWN,
                "sl_suiveur": TRAILING_STOP_PIPS,
                "sl_sell": SOFT_SL_PERCENT,
                "muted_channels": MUTED_CHANNELS,
                "security_triggered": security_triggered,
                "time_off_peak": TIME_OFF_PEAK_FACTOR,
                "prime_hours": PRIME_HOURS,
                "day_factor_0": DAY_RISK_FACTORS.get(0, 1.0),
                "day_factor_1": DAY_RISK_FACTORS.get(1, 1.0),
                "day_factor_2": DAY_RISK_FACTORS.get(2, 1.0),
                "day_factor_3": DAY_RISK_FACTORS.get(3, 1.0),
                "day_factor_4": DAY_RISK_FACTORS.get(4, 1.0),
                "day_factor_5": DAY_RISK_FACTORS.get(5, 1.0),
                "day_factor_6": DAY_RISK_FACTORS.get(6, 1.0)
            },
            "active_trades": active_trades,
            "closed_trades": closed_trades_today
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    
@app.get("/api/stats")
async def api_get_stats(days: str = "30"):
    global CHANNEL_STATS, daily_stats # <-- Ajout de daily_stats ici
    
    if days == "week":
        now = datetime.now()
        monday = now - timedelta(days=now.weekday())
        monday = monday.replace(hour=0, minute=0, second=0, microsecond=0)
        data_period = await get_historical_stats(start_date_custom=monday)
        
        # 👇 INJECTION MAGIQUE DES PIPS EN MÉMOIRE 👇
        data_period["pips"] = daily_stats.get("weekly_pips", 0.0)
    else:
        days_int = 9999 if days == "all" else int(days)
        data_period = await get_historical_stats(days=days_int)
        
    return {
        "channels_lifetime": CHANNEL_STATS,
        "global_period": data_period
    }

@app.post("/api/update_planning")
async def api_update_planning(data: PlanningUpdate):
    global DAY_RISK_FACTORS, PRIME_HOURS, TIME_OFF_PEAK_FACTOR
    DAY_RISK_FACTORS = {int(k): v for k, v in data.day_factors.items()}
    PRIME_HOURS = data.prime_hours # <--- CORRIGÉ ICI (C'était data.time_ranges)
    TIME_OFF_PEAK_FACTOR = data.off_peak_factor
    await run_bg(save_settings)
    return {"status": "success"}

# --- MODÈLES DE DONNÉES (Ce qu'on attend du React) ---
class RiskData(BaseModel):
    asset: str
    value: float

class ToggleData(BaseModel):
    setting: str

class TradeData(BaseModel):
    ticket: int

# --- ROUTES D'ACTION ---
@app.post("/api/update_risk")
async def api_update_risk(data: RiskData):
    global RISK_PERCENT_GOLD, RISK_PERCENT_INDICES, RISK_PERCENT_OTHER
    val = data.value / 100.0
    if data.asset == "risk_gold": RISK_PERCENT_GOLD = val
    elif data.asset == "risk_indices": RISK_PERCENT_INDICES = val
    elif data.asset == "risk_other": RISK_PERCENT_OTHER = val
    await run_bg(save_settings)
    return {"status": "success"}

@app.post("/api/toggle")
async def api_toggle(data: ToggleData):
    global SABR_ANTI_DOUBLON_ACTIVE, SABR_TRAILING_ACTIVE
    if data.setting == "sabr_anti_doublon": SABR_ANTI_DOUBLON_ACTIVE = not SABR_ANTI_DOUBLON_ACTIVE
    elif data.setting == "sabr_trailing": SABR_TRAILING_ACTIVE = not SABR_TRAILING_ACTIVE
    await run_bg(save_settings)
    return {"status": "success"}

@app.post("/api/close_trade")
async def api_close_trade(data: TradeData):
    # Logique pour fermer un trade depuis la Web App
    pos = await run_bg(mt5.positions_get, ticket=data.ticket)
    if pos:
        p = pos[0]
        otype = mt5.ORDER_TYPE_SELL if p.type == mt5.ORDER_TYPE_BUY else mt5.ORDER_TYPE_BUY
        tick_p = mt5.symbol_info_tick(p.symbol)
        price_p = tick_p.bid if otype == mt5.ORDER_TYPE_SELL else tick_p.ask
        req = {"action": mt5.TRADE_ACTION_DEAL, "symbol": p.symbol, "volume": p.volume, "type": otype, "position": data.ticket, "price": price_p, "magic": MAGIC_NUMBER, "type_filling": mt5.ORDER_FILLING_IOC}
        res = await run_bg(_send_order_blocking, req)
        if res and res.retcode == mt5.TRADE_RETCODE_DONE:
            return {"status": "success"}
    return {"status": "error"}

@app.post("/api/be_trade")
async def api_be_trade(data: TradeData):
    pos = await run_bg(mt5.positions_get, ticket=data.ticket)
    if pos:
        p = pos[0]
        # On déplace le SL au prix d'ouverture (BE)
        req = {"action": mt5.TRADE_ACTION_SLTP, "position": data.ticket, "sl": p.price_open, "tp": p.tp}
        res = await run_bg(_send_order_blocking, req)
        if res and res.retcode == mt5.TRADE_RETCODE_DONE:
            return {"status": "success"}
    return {"status": "error"}

# --- NOUVEAUX MODÈLES DE DONNÉES ---
class SettingUpdate(BaseModel):
    key: str
    value: Any

class StringUpdate(BaseModel):
    value: str

# --- ROUTES D'URGENCE ---
@app.post("/api/panic")
async def api_panic():
    pos_list = await run_bg(mt5.positions_get)
    if pos_list:
        for p in pos_list:
            if p.magic == MAGIC_NUMBER or p.ticket in MANAGED_MANUAL_TICKETS:
                otype = mt5.ORDER_TYPE_SELL if p.type == mt5.ORDER_TYPE_BUY else mt5.ORDER_TYPE_BUY
                req = {"action": mt5.TRADE_ACTION_DEAL, "symbol": p.symbol, "volume": p.volume, "type": otype, "position": p.ticket, "magic": MAGIC_NUMBER, "type_filling": mt5.ORDER_FILLING_IOC}
                await run_bg(_send_order_blocking, req)
    return {"status": "success"}

@app.post("/api/kill")
async def api_kill():
    # On écrit le fichier d'arrêt et on force la fermeture du serveur Python
    with open("STOP_ME", "w") as f: f.write("STOP")
    import os
    os._exit(0) 

@app.post("/api/unlock")
async def api_unlock():
    global security_triggered
    security_triggered = False
    return {"status": "success"}

# --- ROUTE DE SUIVI MANUEL ---
@app.post("/api/suivre")
async def api_suivre(data: TradeData):
    MANAGED_MANUAL_TICKETS.add(data.ticket)
    await run_bg(save_memory)
    ACTIVE_TRADE_SOURCES[data.ticket] = "CANAL PERSO"
    await run_bg(save_channel_data)
    return {"status": "success"}

# --- ROUTE UNIVERSELLE POUR LES RÉGLAGES (Curseurs et Champs) ---
@app.post("/api/update_setting")
async def api_update_setting(data: SettingUpdate):
    global RISK_PERCENT_GOLD, RISK_PERCENT_INDICES, RISK_PERCENT_OTHER
    global RISK_PERCENT_MDT_VIP, RISK_PERCENT_MDT_FREE, RISK_PERCENT_SPACE, RISK_PERCENT_LIGUE, RISK_PERCENT_PERSO
    global FIXED_LOT_SABR_VIP, FIXED_LOT_SABR_FREE
    global MAX_DAILY_DRAWDOWN, TRAILING_STOP_PIPS, SOFT_SL_PERCENT
    global PRIME_HOURS, TIME_OFF_PEAK_FACTOR, DAY_RISK_FACTORS # <-- NOUVEAU

    k = data.key
    v = data.value

    if k == "risk_gold": RISK_PERCENT_GOLD = float(v)
    elif k == "risk_indices": RISK_PERCENT_INDICES = float(v)
    elif k == "risk_other": RISK_PERCENT_OTHER = float(v)
    elif k == "risk_mdt_vip": RISK_PERCENT_MDT_VIP = float(v) if float(v) > 0 else None
    elif k == "risk_mdt_free": RISK_PERCENT_MDT_FREE = float(v) if float(v) > 0 else None
    elif k == "risk_space": RISK_PERCENT_SPACE = float(v) if float(v) > 0 else None
    elif k == "risk_ligue": RISK_PERCENT_LIGUE = float(v) if float(v) > 0 else None
    elif k == "risk_perso": RISK_PERCENT_PERSO = float(v) if float(v) > 0 else None
    elif k == "sabr_lot_vip": FIXED_LOT_SABR_VIP = float(v)
    elif k == "sabr_lot_free": FIXED_LOT_SABR_FREE = float(v)
    elif k == "max_dd": MAX_DAILY_DRAWDOWN = float(v)
    elif k == "sl_suiveur": TRAILING_STOP_PIPS = int(float(v))
    elif k == "sl_sell": SOFT_SL_PERCENT = float(v)
    
    # 👇 CE QU'ON AJOUTE POUR LE PLANNING 👇
    elif k == "time_off_peak": TIME_OFF_PEAK_FACTOR = float(v)
    elif k == "prime_hours": 
        # Sécurité : on force la conversion en liste d'entiers !
        if isinstance(v, list): PRIME_HOURS = [int(x) for x in v]
    elif k.startswith("day_factor_"):
        idx = int(k.split("_")[-1]) # Ex: "day_factor_4" -> donne 4
        DAY_RISK_FACTORS[idx] = float(v)

    await run_bg(save_settings)
    return {"status": "success"}

# --- ROUTE POUR LES BOUTONS MUTE ---
@app.post("/api/toggle_mute")
async def api_toggle_mute(data: StringUpdate):
    channel = data.value
    if channel in MUTED_CHANNELS:
        MUTED_CHANNELS.remove(channel)
    else:
        MUTED_CHANNELS.append(channel)
    await run_bg(save_settings)
    return {"status": "success"}

# ==========================================
# 💓 HEARTBEAT (BATTEMENT DE COEUR)
# ==========================================

async def heartbeat_monitor():
    logger.info("💓 Module Heartbeat & Pulse activé.")
    last_msg_hour = -1

    while True:
        update_pulse()
        now = datetime.now()
        
        if now.minute == 0 and now.hour % 4 == 0 and now.hour != last_msg_hour:
            try:
                # CORRECTION ICI : Ajout de await run_bg
                acc = await run_bg(mt5.account_info)
                if acc:
                    # CORRECTION ICI : Ajout de await run_bg
                    pos = await run_bg(mt5.positions_get, group="*")
                    
                    nb_open = len([p for p in pos if p.magic == MAGIC_NUMBER or p.ticket in MANAGED_MANUAL_TICKETS]) if pos else 0
                    
                    msg = (f"**🔆 SANTÉ**\n\n"
                           f"Heure: {now.strftime('%H:%M')}\n"
                           f"Balance: {acc.equity:.2f}€\n"
                           f"Trades en cours: {nb_open}")
                    await send_alert(msg)
                    last_msg_hour = now.hour
            except Exception as e: 
                logger.error(f"Heartbeat Error: {e}")

        await asyncio.sleep(10)

async def license_watchdog():
    logger.info("👮 Monitor Licence activé (Vérification toutes les 4h).")
    while True:
        # On attend 4 heures (14400 secondes) avant la prochaine vérif
        await asyncio.sleep(14400) 
        
        logger.info("👮 Vérification périodique de la licence...")
        # On appelle la fonction en mode silencieux (pas de print, pas de mouchard Telegram)
        is_valid = await run_bg(check_license_online, silent=True)
        
        if not is_valid:
            logger.critical("⛔ LICENCE RÉVOQUÉE OU EXPIRÉE EN COURS DE SESSION !")
            await send_alert("🚨 **URGENT : LICENCE INVALIDE**\n\nLe bot va s'arrêter immédiatement.")
            # On ferme proprement
            with open("STOP_ME", "w") as f: f.write("STOP")
            sys.exit() # Arrêt du script

async def monitor_positions():
    global watched_tickets, daily_stats, active_trades_max_pips, active_trades_min_pips, NOTIFIED_MANUAL_TICKETS, TRADES_PROCESSED_LEVELS, PENDING_ORDER_TICKETS, ACTIVE_TRADE_SOURCES, CHANNELS_STRAT_2, SOFT_SL_PERCENT, CHANNEL_TP1_PCT, DEFAULT_TP1_PCT, TRAILING_STOP_PIPS

    logger.info("⏳ Warm-up: Attente stabilisation Telegram (10s)...")
    await asyncio.sleep(10) 
    logger.info("🚀 Monitoring MT5 activé.")

    init = mt5.positions_get() # SYNCHRONE
    if init: 
        watched_tickets = {p.ticket for p in init if p.magic == MAGIC_NUMBER or p.ticket in MANAGED_MANUAL_TICKETS}
    
    last_stats_update = 0

    while True:
        try:
            if time.time() - last_stats_update > 60:
                await update_daily_stats()
                last_stats_update = time.time()
                
            await check_closed_positions()

            # 2. Gestion des Ordres en Attente (Activation & Nettoyage)
            
            # --- A. Vérification de l'activation des ordres ---
            if len(PENDING_ORDER_TICKETS) > 0:
                tickets_to_remove = set()
                for ticket in PENDING_ORDER_TICKETS:
                    active_pos = mt5.positions_get(ticket=ticket)
                    if active_pos:
                        pos = active_pos[0]
                        act_time = (datetime.fromtimestamp(pos.time) - timedelta(hours=1)).strftime('%H:%M')
                        src_raw = ACTIVE_TRADE_SOURCES.get(ticket, "INCONNU")
                        src_clean = src_raw.replace("CANAL ", "").replace("_", " ")
                        await send_alert(f"⚡ **ORDRE ACTIVÉ**\n\n"
                                         f"Symbole: {pos.symbol} ({act_time})\n"
                                         f"Source: {src_clean}\n"
                                         f"Ticket: {ticket}")                        
                        
                        tickets_to_remove.add(ticket)
                    else:
                        pending_ord = mt5.orders_get(ticket=ticket)
                        if not pending_ord: 
                            tickets_to_remove.add(ticket)
                PENDING_ORDER_TICKETS -= tickets_to_remove

            # --- B. Nettoyage intelligent des ordres (NOUVEAU) ---
            # On récupère l'heure serveur pour bloquer le spam le week-end
            server_t = await run_bg(get_server_time_tick)
            is_weekend = False
            if server_t:
                s_dt = datetime.fromtimestamp(server_t.time, timezone.utc)
                # 🛑 WEEK-END = Samedi entier (5) + Dimanche (6) avant 23h00
                is_weekend = s_dt.weekday() == 5 or (s_dt.weekday() == 6 and s_dt.hour < 23)
                
                # 🧹 NETTOYAGE VENDREDI SOIR (23h45) <--- CORRIGÉ ICI
                if s_dt.weekday() == 4 and s_dt.hour == 23 and s_dt.minute >= 45:
                    tag_we = f"WE_CLEAN_{s_dt.day}"
                    if tag_we not in TRADES_PROCESSED_LEVELS.get("SYS", set()):
                        if "SYS" not in TRADES_PROCESSED_LEVELS: TRADES_PROCESSED_LEVELS["SYS"] = set()
                        all_o = await run_bg(mt5.orders_get)
                        if all_o:
                            c_del = 0
                            for ord_to_del in all_o:
                                if ord_to_del.magic == MAGIC_NUMBER:
                                    res = await run_bg(_send_order_blocking, {"action": mt5.TRADE_ACTION_REMOVE, "order": ord_to_del.ticket})
                                    if res and res.retcode == mt5.TRADE_RETCODE_DONE: c_del += 1
                            if c_del > 0: await send_alert(f"🧹 **CLÔTURE MARCHÉ** : {c_del} ordres en attente purgés pour le week-end.")
                        TRADES_PROCESSED_LEVELS["SYS"].add(tag_we)
                        await run_bg(save_memory)

            active_orders = await run_bg(mt5.orders_get)
            # 🛑 ANTI-SPAM : On bloque tout le nettoyage classique si on est le week-end
            if active_orders and not is_weekend:
                sabr_pending_groups = {} # Pour grouper les trades SABR par zone
                
                for o in active_orders:
                    if o.magic != MAGIC_NUMBER: continue
                    
                    src = ACTIVE_TRADE_SOURCES.get(o.ticket, "INCONNU")
                    
                    # 1️⃣ Nettoyage des Autres Canaux (Plus de 12 Heures)
                    if "SABR" not in src:
                        current_time_sec = int(time.time())
                        # age = Heure Serveur Actuelle - Heure de Création de l'ordre
                        age_seconds = current_time_sec - o.time_setup
                        if age_seconds > 12 * 3600: # 12 heures = 43200 secondes
                            req = {"action": mt5.TRADE_ACTION_REMOVE, "order": o.ticket}
                            res = await run_bg(_send_order_blocking, req)
                            if res and res.retcode == mt5.TRADE_RETCODE_DONE:
                                src_clean = src.replace("CANAL ", "").replace("_", " ")
                                await send_alert(f"🗑️ **ORDRE EXPIRÉ (>12h)**\n\n"
                                                 f"Symbole: {o.symbol}\n\n"
                                                 f"Source: {src_clean}\n"
                                                 f"Ticket: {o.ticket}")
                    
                    # 2️⃣ Nettoyage SABR (Préparation des groupes)
                    else:
                        # On groupe les ordres en attente par Symbole, Sens et Source
                        key = (o.symbol, o.type, src)
                        if key not in sabr_pending_groups:
                            sabr_pending_groups[key] = []
                        sabr_pending_groups[key].append(o)
                        
                # 3️⃣ Traitement des ordres SABR ("Parti sans nous")
                for (sym, otype, src), pending_orders in sabr_pending_groups.items():
                    tick = mt5.symbol_info_tick(sym)
                    if not tick: continue
                    
                    info_sym = mt5.symbol_info(sym)
                    pt = info_sym.point if info_sym else 0.00001
                    div = get_pip_divisor(sym)
                    
                    is_buy = (otype == mt5.ORDER_TYPE_BUY_LIMIT or otype == mt5.ORDER_TYPE_BUY_STOP)
                    curr_price = tick.ask if is_buy else tick.bid
                    
                    related_active_prices = []
                    active_positions = mt5.positions_get(symbol=sym)

                    if active_positions:
                        for p in active_positions:
                            if p.magic == MAGIC_NUMBER and ACTIVE_TRADE_SOURCES.get(p.ticket, "") == src:
                                if (is_buy and p.type == mt5.ORDER_TYPE_BUY) or (not is_buy and p.type == mt5.ORDER_TYPE_SELL):
                                    related_active_prices.append(p.price_open)

                    all_setup_prices = [o.price_open for o in pending_orders] + related_active_prices
                    if not all_setup_prices: 
                        continue

                    # --- NOUVELLE LOGIQUE DE DISTANCE ET D'ANNULATION ---
                    key_dist = (sym, src)
                    if is_buy:
                        current_dist_to_zone = ((curr_price - max(all_setup_prices)) / pt) / div
                    else:
                        current_dist_to_zone = ((min(all_setup_prices) - curr_price) / pt) / div
                        
                    if key_dist not in SABR_ZONE_MIN_DIST:
                        SABR_ZONE_MIN_DIST[key_dist] = current_dist_to_zone
                    else:
                        SABR_ZONE_MIN_DIST[key_dist] = min(SABR_ZONE_MIN_DIST[key_dist], current_dist_to_zone)

                    min_dist_reached = SABR_ZONE_MIN_DIST[key_dist]
                    cancel_orders = False

                    # Condition A : On est entré dans le trade, et ça a pump de +150 pips
                    if len(related_active_prices) > 0 and current_dist_to_zone >= 150:
                        cancel_orders = True
                    # Condition B : On n'est jamais entré, on a frôlé la zone (<50p), et c'est parti sans nous (>400p)
                    elif len(related_active_prices) == 0 and min_dist_reached <= 50 and current_dist_to_zone >= 400:
                        cancel_orders = True

                    if cancel_orders:
                        src_clean = src.replace("CANAL ", "").replace("_", " ")
                        count_cancelled = 0
                        for o in pending_orders:
                            req = {"action": mt5.TRADE_ACTION_REMOVE, "order": o.ticket}
                            res = await run_bg(_send_order_blocking, req)
                            if res and res.retcode == mt5.TRADE_RETCODE_DONE:
                                count_cancelled += 1
                                
                        if count_cancelled > 0:
                            await send_alert(f"🗑️ **ORDRE SABR ANNULÉ**\n\n"
                                             f"Symbole: {sym}\nSource: {src_clean}\n"
                                             f"Raison: Trade activé (+150p) OU Frôlé puis envolé (+400p).\n"
                                             f"Ordres restants annulés: {count_cancelled}")
                        
                        if key_dist in SABR_ZONE_MIN_DIST: del SABR_ZONE_MIN_DIST[key_dist]
            
            # 3. Sécurité Drawdown
            if check_daily_max_loss():
                pos_panic = await run_bg(mt5.positions_get)
                if pos_panic:
                    await send_alert(f"🚨 **URGENCE : DD QUOTIDIEN DÉPASSÉ !**\nFermeture d'urgence.")
                    for p in pos_panic:
                        if p.magic == MAGIC_NUMBER or p.ticket in MANAGED_MANUAL_TICKETS:
                            otype = mt5.ORDER_TYPE_SELL if p.type == mt5.ORDER_TYPE_BUY else mt5.ORDER_TYPE_BUY
                            
                            # Ajout du prix obligatoire ici aussi !
                            tick_p = mt5.symbol_info_tick(p.symbol)
                            price_p = tick_p.bid if otype == mt5.ORDER_TYPE_SELL else tick_p.ask
                            
                            req = {
                                "action": mt5.TRADE_ACTION_DEAL, "symbol": p.symbol, "volume": p.volume, 
                                "type": otype, "position": p.ticket, "price": price_p, 
                                "magic": MAGIC_NUMBER, "type_filling": mt5.ORDER_FILLING_IOC
                            }
                            await run_bg(_send_order_blocking, req)
                    
                    orders_panic = await run_bg(mt5.orders_get)
                    if orders_panic:
                        for o in orders_panic:
                            if o.magic == MAGIC_NUMBER:
                                await run_bg(_send_order_blocking, {"action": mt5.TRADE_ACTION_REMOVE, "order": o.ticket})
                await asyncio.sleep(60)
                continue

            # 4. BOUCLE PRINCIPALE
            all_positions = await run_bg(mt5.positions_get)
            if all_positions is not None:
                
                sabr_groups = {} 
                
                for pos in all_positions:
                    # 🛑 LE BOUCLIER WEEK-END ABSOLU
                    sym_up = pos.symbol.upper()
                    if is_weekend and not ("BTC" in sym_up or "ETH" in sym_up or "BITCOIN" in sym_up):
                        continue # On ignore totalement ce trade jusqu'à l'ouverture !

                    is_bot_trade = (pos.magic == MAGIC_NUMBER)
                    is_managed_manual = (pos.ticket in MANAGED_MANUAL_TICKETS)
                    
                    if not is_bot_trade and not is_managed_manual:
                        if pos.ticket not in NOTIFIED_MANUAL_TICKETS:
                            await send_alert(f"👋 **TRADE MANUEL DÉTECTÉ**\n\nTicket: `{pos.ticket}` ({pos.symbol})\n\nRéponds `/suivi {pos.ticket}` pour gérer.")
                            NOTIFIED_MANUAL_TICKETS.add(pos.ticket)
                        continue 
                    
                    if pos.ticket not in TRADES_PROCESSED_LEVELS:
                        TRADES_PROCESSED_LEVELS[pos.ticket] = set()

                    src_trade = ACTIVE_TRADE_SOURCES.get(pos.ticket, "INCONNU")
                    
                    if "SABR" in src_trade:
                        if pos.symbol not in sabr_groups: sabr_groups[pos.symbol] = []
                        sabr_groups[pos.symbol].append(pos)
                        continue 
                    
                    # --- LOGIQUE STANDARD ---
                    sym = pos.symbol
                    curr = pos.price_current
                    pt = get_cached_point(sym)
                    div = get_pip_divisor(sym)
                    entry = pos.price_open

                    open_time = (datetime.fromtimestamp(pos.time) - timedelta(hours=1)).strftime('%H:%M')
                    src_clean = src_trade.replace("CANAL ", "").replace("_", " ")
                    
                    if pos.type == mt5.ORDER_TYPE_BUY:
                        pips = ((curr - entry) / pt) / div
                        sl_pips = ((pos.sl - entry) / pt) / div if pos.sl > 0 else -9999
                    else:
                        pips = ((entry - curr) / pt) / div
                        sl_pips = ((entry - pos.sl) / pt) / div if pos.sl > 0 else -9999
                    
                    cur_max = active_trades_max_pips.get(pos.ticket, 0)
                    if pips > cur_max: active_trades_max_pips[pos.ticket] = pips
                    cur_min = active_trades_min_pips.get(pos.ticket, 999.0)
                    if pips < cur_min: active_trades_min_pips[pos.ticket] = pips

                    is_strat_2 = (src_trade in CHANNELS_STRAT_2)
                    if is_strat_2: CFG_TP1_PCT, CFG_TP3_PCT = 0.50, 0.00
                    else: CFG_TP1_PCT, CFG_TP3_PCT = 0.50, 0.00
                    
                    processed = TRADES_PROCESSED_LEVELS[pos.ticket]

                    # === ☠️ SÉCURITÉ "ZONE DE LA MORT" ===
                    # Désactivée pour SABR et MDT_FREE
                    disable_death_zone = ("SABR" in src_trade.upper()) or ("MDT_FREE" in src_trade.upper())
                    
                    if not disable_death_zone and pips <= -MAX_DEATH_ZONE_PIPS:
                         tag_death = "DEATH_ZONE_KILL"
                         if tag_death not in TRADES_PROCESSED_LEVELS[pos.ticket]:
                             # 1. On verrouille
                             TRADES_PROCESSED_LEVELS[pos.ticket].add(tag_death)
                             save_memory()
                             
                             # 2. On ferme TOUT immédiatement
                             otype = mt5.ORDER_TYPE_SELL if pos.type == mt5.ORDER_TYPE_BUY else mt5.ORDER_TYPE_BUY
                             tick_k = mt5.symbol_info_tick(sym)
                             price_k = tick_k.bid if otype == mt5.ORDER_TYPE_SELL else tick_k.ask
                             
                             req_k = {
                                "action": mt5.TRADE_ACTION_DEAL, "symbol": sym, "volume": pos.volume, 
                                "type": otype, "position": pos.ticket, "price": price_k, 
                                "magic": MAGIC_NUMBER, "comment": "Death Zone Kill", 
                                "type_filling": mt5.ORDER_FILLING_IOC
                             }

                             src_clean = src_trade.replace("CANAL ", "").replace("_", " ")
                             
                             await send_alert(f"☠️ **ZONE DE LA MORT** ({sym})\n\n"
                                              f"Symbole: {sym} ({open_time})\n\n"
                                              f"Source: {src_clean}\n\n"
                                              f"Drawdown > {MAX_DEATH_ZONE_PIPS} pips.\n"
                                              f"Fermeture de sécurité immédiate.")
                                              
                             await run_bg(_send_order_blocking, req_k)
                             continue

                    # ==========================================
                    # 🚀 STRATÉGIE SPÉCIFIQUE : CANAL MDT_FREE
                    # ==========================================
                    if src_trade == "CANAL MDT_FREE":
                        if pips >= 60:
                            # 1. On sécurise le BE en priorité absolue
                            if "MDT_FREE_60_BE" not in processed:
                                be_price = entry
                                update_hard_sl = False
                                if pos.type == mt5.ORDER_TYPE_BUY and pos.sl < be_price: update_hard_sl = True
                                if pos.type == mt5.ORDER_TYPE_SELL and (pos.sl > be_price or pos.sl == 0): update_hard_sl = True
                                
                                if update_hard_sl:
                                    new_sl_price = round(be_price / pt) * pt
                                    req = {"action": mt5.TRADE_ACTION_SLTP, "symbol": sym, "position": pos.ticket, "sl": new_sl_price, "tp": pos.tp}
                                    res = await run_bg(_send_order_blocking, req)
                                    if res and res.retcode == mt5.TRADE_RETCODE_DONE:
                                        await send_alert(f"🛡️ **SL SÉCURISÉ BE (MDT FREE)**\n\nSymbole: {sym}\nTicket: {pos.ticket}")
                                        processed.add("MDT_FREE_60_BE")
                                        await run_bg(save_memory)

                            # 2. On tente de vendre 50% (Réessaie en boucle si le réseau Timeout)
                            if "MDT_FREE_60_PARTIAL" not in processed:
                                lbl_part = "TP 60p (MDT FREE)"
                                success = await close_partial(pos, 0.50, lbl_part, pips)
                                if success:
                                    processed.add("MDT_FREE_60_PARTIAL")
                                    await run_bg(save_memory)
                        
                        continue

                    # ==========================================
                    # 🚀 STRATÉGIE SPÉCIFIQUE : CANAL MDT_VIP
                    # ==========================================
                    if src_trade == "CANAL MDT_VIP":
                        if pips >= 30:
                            # 1. On sécurise le BE en priorité absolue
                            if "MDT_VIP_30_BE" not in processed:
                                be_price = entry
                                update_hard_sl = False
                                if pos.type == mt5.ORDER_TYPE_BUY and pos.sl < be_price: update_hard_sl = True
                                if pos.type == mt5.ORDER_TYPE_SELL and (pos.sl > be_price or pos.sl == 0): update_hard_sl = True
                                
                                if update_hard_sl:
                                    new_sl_price = round(be_price / pt) * pt
                                    req = {"action": mt5.TRADE_ACTION_SLTP, "symbol": sym, "position": pos.ticket, "sl": new_sl_price, "tp": pos.tp}
                                    res = await run_bg(_send_order_blocking, req)
                                    if res and res.retcode == mt5.TRADE_RETCODE_DONE:
                                        await send_alert(f"🛡️ **SL SÉCURISÉ BE (MDT VIP)**\n\nSymbole: {sym}\nTicket: {pos.ticket}")
                                        processed.add("MDT_VIP_30_BE")
                                        await run_bg(save_memory)

                            # 2. On tente de vendre 80% (Réessaie en boucle si le réseau Timeout)
                            if "MDT_VIP_30_PARTIAL" not in processed:
                                lbl_part = "TP 30p (MDT VIP - 80%)"
                                success = await close_partial(pos, 0.80, lbl_part, pips)
                                if success:
                                    processed.add("MDT_VIP_30_PARTIAL")
                                    await run_bg(save_memory)
                        
                        continue
                    
                    # --- GESTION TP (CORRIGÉE) ---
                    # On ne marque "Done" que si close_partial retourne True

                    # NIVEAU 1 
                    if pips >= TP1_PIPS and "TP1" not in processed:
                        lbl_part = f"TP1 ({TP1_PIPS} pips)"; spec_pct = CHANNEL_TP1_PCT.get(src_trade, None)
                        pct_close = spec_pct if spec_pct is not None else CFG_TP1_PCT
                        if await close_partial(pos, pct_close, lbl_part, pips):
                            daily_stats["tp1_reached"] += 1; processed.add("TP1"); await run_bg(save_memory)

                    # NIVEAU 2 
                    if pips >= TP2_PIPS and "TP2" not in processed:
                        # 1. On envoie ton message d'alerte classique
                        await send_alert(f"🚀 **TP2 ({TP2_PIPS} pips) ATTEINT (+{int(pips)} pips)**\n\nSymbole: {sym} ({open_time})\nSource: {src_clean}")
                        
                        # 2. On encaisse 50% du trade (ce qui enverra le ticket de caisse)
                        lbl_part = f"TP2 ({TP2_PIPS}p)"
                        if await close_partial(pos, 0.50, lbl_part, pips):
                            daily_stats["tp2_reached"] += 1; processed.add("TP2"); await run_bg(save_memory)

                    # NIVEAU 3 
                    if pips >= TP3_PIPS and "TP3" not in processed:
                        daily_stats["tp3_reached"] += 1; processed.add("TP3"); await run_bg(save_memory)
                        await send_alert(f"🚀 **TP3 ({TP3_PIPS} pips) ATTEINT (+{int(pips)} pips)**\n\nSymbole: {sym} ({open_time})\nSource: {src_clean}")

                    # NIVEAU 4 (140 pips)
                    if pips >= TP4_PIPS and "TP4" not in processed:
                        processed.add("TP4"); daily_stats["tp4_reached"] += 1; await run_bg(save_memory)
                        await send_alert(f"🚀 **TP4 ({TP4_PIPS} pips) ATTEINT (+{int(pips)} pips)**\n\nSymbole: {sym} ({open_time})\nSource: {src_clean}")

                    # NIVEAU 5 (200 pips)
                    #if pips >= TP5_PIPS and "TP5" not in processed:
                    #    processed.add("TP5"); await run_bg(save_memory)
                    #    await send_alert(f"🚀 **TP5 ({TP5_PIPS} pips) ATTEINT (+{int(pips)} pips)**\n\nSymbole: {sym} ({open_time})\nSource: {src_clean}")
                    
                    # TP ZONE (300+)
                    if pips >= 300:
                        hundreds = int(pips // 100) * 100
                        tag = f"TP_INF_{hundreds}"
                        if tag not in processed: 
                            processed.add(tag); await run_bg(save_memory)
                            await send_alert(f"🚀 **TP ZONE {hundreds} ATTEINT (+{int(pips)} pips)**\n\nSymbole: {sym} ({open_time})\nSource: {src_clean}")

                    # --- GESTION SL (CORRIGÉE) ---
                    new_sl_price = None; sl_reason = ""
                    
                    if TRAILING_STOP_PIPS > 0:
                        if pips >= 32:
                            be_dist = 20 * pt * (div/10)
                            potential_sl = entry + be_dist if pos.type == mt5.ORDER_TYPE_BUY else entry - be_dist
                            update_hard_sl = False
                            
                            # On ne bouge que si le SL actuel est "pire" que le BE (moins de 2 pips de profit sécurisé)
                            if sl_pips < 2: update_hard_sl = True 
                            
                            if update_hard_sl: new_sl_price = potential_sl; sl_reason = "Secu BE (Mode Soft Trail)"

                        current_max_pips = cur_max 
                        if current_max_pips >= TRAILING_STOP_PIPS:
                            soft_trigger_pips = current_max_pips - TRAILING_STOP_PIPS
                            if pips <= soft_trigger_pips:
                                tag = "SOFT_SL_HIT"
                                if tag not in TRADES_PROCESSED_LEVELS[pos.ticket]:
                                    TRADES_PROCESSED_LEVELS[pos.ticket].add(tag); await run_bg(save_memory)
                                    pct_txt = int(SOFT_SL_PERCENT * 100)
                                    
                                    await send_alert(f"📉 **REPLI DÉTECTÉ (Soft SL)**\n\n"
                                                     f"Symbole: {sym} ({open_time})\n\n"
                                                     f"Sommet: +{int(current_max_pips)}p | Actuel: +{int(pips)}p\n"
                                                     f"Action: Clôture {pct_txt}%. Le reste court avec SL à BE.")

                                    success = await close_partial(pos, SOFT_SL_PERCENT, "Soft Trailing", pips)
                                    # Si ça rate (sauf petit lot), on enlève le tag pour réessayer
                                    if not success and pos.volume > 0.01: 
                                        TRADES_PROCESSED_LEVELS[pos.ticket].remove(tag)
                    else:
                        # Mode Paliers (inchangé)
                        tgt_pips = None
                        if pips >= 200: tgt_pips = 140
                        elif pips >= 140: tgt_pips = 80
                        elif pips >= 80: tgt_pips = 20
                        elif pips >= 30: tgt_pips = 2
                        if tgt_pips:
                            dist_val = tgt_pips * 10 * pt * (div/10)
                            potential_sl = entry + dist_val if pos.type == mt5.ORDER_TYPE_BUY else entry - dist_val
                            update_needed = False
                            if sl_pips < tgt_pips - 1: update_needed = True
                            if update_needed: new_sl_price = potential_sl; sl_reason = f"Palier {tgt_pips} pips"

                    if new_sl_price:
                        info_sl = mt5.symbol_info(sym)
                        new_sl_price = round(new_sl_price / info_sl.point) * info_sl.point
                        
                        # 🛡️ LE BOUCLIER ANTI-SPAM MT5 (Évite la boucle infinie)
                        if abs(new_sl_price - pos.sl) > (info_sl.point / 2):
                            req = {"action": mt5.TRADE_ACTION_SLTP, "symbol": sym, "position": pos.ticket, "sl": new_sl_price, "tp": pos.tp}
                        
                        # --- CORRECTION CRITIQUE SL ---
                        # On utilise la fonction de retry _send_order_blocking pour être SÛR que le SL bouge
                        # même si le réseau est instable
                        res = await run_bg(_send_order_blocking, req)

                        if res is None:
                            logger.warning(f"⚠️ MT5 Timeout (SL) sur ticket {pos.ticket}")
                            # Pas de continue ici, on laisse la boucle tourner pour les autres trades
    
                        elif res.retcode == mt5.TRADE_RETCODE_DONE:
                            log(f"🛡️ SL DÉPLACÉ: {sym} -> {new_sl_price} ({sl_reason})", important=True)
                            if "Palier" in sl_reason or "Secu BE" in sl_reason: 
                                await send_alert(f"🛡️ **SL SÉCURISÉ** ({sym})\n\n"
                                                 f"Symbole: {sym} ({open_time})\n"
                                                 f"Source: {src_clean}\n"
                                                 f"Niveau : {sl_reason}")

                # --- SABR CASCADE (150 PIPS PAR PALIERS JUSQU'À 15%) ---
                for sym, positions in sabr_groups.items():
                    if not positions: continue
                    tick = mt5.symbol_info_tick(sym)
                    if not tick: continue
                    
                    first_pos = positions[0]
                    is_buy = (first_pos.type == mt5.ORDER_TYPE_BUY)
                    
                    curr_price = tick.bid if is_buy else tick.ask
                    pt = get_cached_point(sym)
                    div = get_pip_divisor(sym)
                    
                    if is_buy: max_pips_group = max(((curr_price - p.price_open) / pt) / div for p in positions)
                    else: max_pips_group = max(((p.price_open - curr_price) / pt) / div for p in positions)
                    
                    # Le premier palier après 100 est 250 (+150 pips)
                    if max_pips_group >= 250:
                        current_level_max = int((max_pips_group - 100) // 150)
                        
                        if current_level_max >= 1:
                            for level_idx in range(1, current_level_max + 1):
                                tag = f"SABR_CASCADE_{level_idx}"
                                
                                level_already_paid = any(tag in TRADES_PROCESSED_LEVELS.get(p.ticket, set()) for p in positions)
                                if level_already_paid: continue
                                
                                # On trie pour attaquer le trade le plus proche du prix
                                positions.sort(key=lambda p: abs(p.price_open - curr_price))
                                victim_found = False

                                for p in positions:
                                    # --- OPTIMISATION MAJEURE (CACHE MEMOIRE) ---
                                    if p.ticket not in INITIAL_VOLUMES:
                                        deals = await run_bg(mt5.history_deals_get, position=p.ticket)
                                        INITIAL_VOLUMES[p.ticket] = deals[0].volume if (deals and len(deals) > 0) else p.volume
                                
                                    initial_vol = INITIAL_VOLUMES[p.ticket]
                                    # --------------------------------------------
                                
                                    # Plancher de sécurité : 15% du volume initial
                                    min_floor = max(0.01, round(initial_vol * 0.15, 2))
                                    
                                    if round(p.volume, 2) <= min_floor:
                                        continue
                                    
                                    # Vente de 20%
                                    vol_to_close = round(initial_vol * 0.20, 2)
                                    if vol_to_close < 0.01: vol_to_close = 0.01
                                    
                                    # Si la vente brise le plancher, on vend juste la différence
                                    if round(p.volume - vol_to_close, 2) < min_floor:
                                        vol_to_close = round(p.volume - min_floor, 2)
                                    
                                    if vol_to_close >= 0.01:
                                        success = await close_partial(p, 0.0, f"SABR Cascade (Lvl {level_idx})", max_pips_group, fixed_lot=vol_to_close)
                                        
                                        if p.ticket not in TRADES_PROCESSED_LEVELS: TRADES_PROCESSED_LEVELS[p.ticket] = set()
                                        TRADES_PROCESSED_LEVELS[p.ticket].add(tag)
                                        await run_bg(save_memory)
                                        
                                        if success:
                                            victim_found = True
                                            break
                                            
                                # Si tous les trades sont déjà à 15%, on marque ce palier payé
                                if not victim_found:
                                    if positions[0].ticket not in TRADES_PROCESSED_LEVELS: TRADES_PROCESSED_LEVELS[positions[0].ticket] = set()
                                    TRADES_PROCESSED_LEVELS[positions[0].ticket].add(tag)

                    # --- GESTION INDIVIDUELLE (BE & TRAILING) ---
                    for p in positions:
                        current_sl = p.sl
                        new_sl = None
                        if is_buy: pips_indiv = ((curr_price - p.price_open) / pt) / div
                        else: pips_indiv = ((p.price_open - curr_price) / pt) / div

                        # Stats MAE/MFE
                        if pips_indiv > active_trades_max_pips.get(p.ticket, 0): active_trades_max_pips[p.ticket] = pips_indiv
                        if pips_indiv < active_trades_min_pips.get(p.ticket, 999): active_trades_min_pips[p.ticket] = pips_indiv

                        # --- TP1 GLOBAL À 100 PIPS (25% sur TOUS LES ORDRES) ---
                        if pips_indiv >= 100:
                            tag_100 = "SABR_TP_100"
                            if tag_100 not in TRADES_PROCESSED_LEVELS[p.ticket]:
                                deals = await run_bg(mt5.history_deals_get, position=p.ticket)
                                initial_vol = deals[0].volume if (deals and len(deals) > 0) else p.volume
                                
                                vol_to_close = round(initial_vol * 0.25, 2)
                                if vol_to_close < 0.01: vol_to_close = 0.01
                                
                                # On vend seulement si ça ne tue pas le trade
                                if round(p.volume - vol_to_close, 2) >= 0.01:
                                    success = await close_partial(p, 0.0, "SABR TP1 100p (25%)", pips_indiv, fixed_lot=vol_to_close)
                                    if success:
                                        TRADES_PROCESSED_LEVELS[p.ticket].add(tag_100)
                                        await run_bg(save_memory)
                                else:
                                    TRADES_PROCESSED_LEVELS[p.ticket].add(tag_100)
                        
                        # --- NOUVEAU REGLAGE BE : 150 PIPS ---
                        if pips_indiv >= 150:
                            be_p = p.price_open
                            if is_buy:
                                # On ne remonte que si le SL actuel est pire que le BE
                                if current_sl < be_p: new_sl = be_p
                            else:
                                # On ne descend que si le SL actuel est pire que le BE ou inexistant (0)
                                if current_sl > be_p or current_sl == 0: new_sl = be_p
                            
                            if new_sl:
                                new_sl = round(new_sl / pt) * pt
                                # 🛡️ LE BOUCLIER ANTI-SPAM MT5
                                if abs(new_sl - current_sl) > (pt / 2):
                                    req = {"action": mt5.TRADE_ACTION_SLTP, "symbol": sym, "position": p.ticket, "sl": new_sl, "tp": p.tp}
                                    res = await run_bg(_send_order_blocking, req) 
                                    if res and res.retcode == mt5.TRADE_RETCODE_DONE:
                                        p_open_time = (datetime.fromtimestamp(p.time) - timedelta(hours=1)).strftime('%H:%M')
                                        p_src_clean = ACTIVE_TRADE_SOURCES.get(p.ticket, "INCONNU").replace("CANAL ", "").replace("_", " ")
                                        await send_alert(f"☄️ **SABR: BE ACTIVÉ (+150p)**\n\n"
                                                         f"Symbole: {sym} ({p_open_time})\n"
                                                         f"Source: {p_src_clean}\n"
                                                         f"Ticket: {p.ticket}")

                        # --- TRAILING STOP (200 PIPS) ---
                        if SABR_TRAILING_ACTIVE and pips_indiv >= 200:
                            dist_points = 200 * pt * (div/10) * 10
                            if is_buy:
                                target_sl = curr_price - dist_points
                                if target_sl > current_sl + (30*pt): new_sl = target_sl
                            else:
                                target_sl = curr_price + dist_points
                                if (current_sl == 0) or (target_sl < current_sl - (30*pt)): new_sl = target_sl
                            
                            if new_sl:
                                new_sl = round(new_sl / pt) * pt
                                # 🛡️ LE BOUCLIER ANTI-SPAM MT5
                                if abs(new_sl - current_sl) > (pt / 2):
                                    req = {"action": mt5.TRADE_ACTION_SLTP, "symbol": sym, "position": p.ticket, "sl": new_sl, "tp": p.tp}
                                    res = await run_bg(_send_order_blocking, req)
                                    if res and res.retcode == mt5.TRADE_RETCODE_DONE:
                                        act_time = (datetime.fromtimestamp(p.time) - timedelta(hours=1)).strftime('%H:%M')
                                        log(f"⏫ SABR TRAIL: {sym} ({act_time}) | Ticket {p.ticket} -> {new_sl}", important=True)

        except Exception as e: 
            logger.error(f"Monitor Error: {e}")
            import traceback
            traceback.print_exc()
        
        await asyncio.sleep(1)

# ==========================================
# 🤝 APPAIRAGE SÉCURISÉ DU CLIENT
# ==========================================
@client.on(events.NewMessage())
async def pairing_handler(event):
    global TELEGRAM_DESTINATION_ID, ADMIN_ID
    
    raw = event.raw_text.strip()

    web_app_url = "https://trading-dashboard-three-beige.vercel.app"

    btn_webapp = KeyboardButtonWebView(text="☀️ Ouvrir le Dashboard", url=web_app_url)
    
    # CAS 1 : Si le bot attend son maître (ID = 0)
    if TELEGRAM_DESTINATION_ID == 0:
        if raw.startswith("/start "):
            pin_entered = raw.split()[-1]
            if pin_entered == SECURITY_PIN:
                TELEGRAM_DESTINATION_ID = event.sender_id
                ADMIN_ID = event.sender_id
                await run_bg(save_settings)

                # Démarrage Firebase avec l'ID personnel du client (juste capturé)
                if firebase_sync._firebase_initialized:
                    asyncio.create_task(_start_firebase_sync(str(event.sender_id)))

                await bot_user.send_message(
                    ADMIN_ID,
                    "✅ **BINGO !** Sécurité validée. Le bot est connecté.\n\nClique sur le bouton ci-dessous pour ouvrir ton interface si tu utilise telegram X :",
                    buttons=[[btn_webapp]]
                )
                await bot_user.send_message(
                    ADMIN_ID,
                    "Sinon utilise le bouton en bas a gauche !"
                )

                logger.info("✅ Appairage Telegram réussi !")
            else:
                await event.reply("❌ Code PIN incorrect.")
        elif raw.lower() == "/start":
            await event.reply(f"🔒 **Sécurité :** Veuillez d'abord lier votre compte en tapant : `/start CODE_PIN`")
            
    # CAS 2 : Si tu es déjà appairé et que tu tapes /start
    elif event.sender_id == ADMIN_ID and raw.lower() == "/start": # 👈 CORRECTION 3
        await bot_user.send_message(
            ADMIN_ID, # 👈 CORRECTION 4
            "✅ **Système Opérationnel !**\n\nClique sur le bouton ci-dessous pour ouvrir ton interface si tu utilise telegram X :",
                    buttons=[[btn_webapp]]
        )
        await bot_user.send_message(
            ADMIN_ID,
            "Sinon utilise le bouton en bas a gauche !"
        )

# ==========================================
# 🎮 ADMIN COMMANDS (VERSION FINALE 7.0)
# ==========================================

@client.on(events.NewMessage())
async def admin_commands(event):
    global RISK_PERCENT_GOLD, RISK_PERCENT_OTHER, security_triggered
    global RISK_PERCENT_MDT_VIP, RISK_PERCENT_MDT_FREE
    global FIXED_LOT_SABR_VIP, FIXED_LOT_SABR_FREE
    global RISK_PERCENT_SPACE, RISK_PERCENT_LIGUE, RISK_PERCENT_PERSO
    global TRAILING_STOP_PIPS, MANAGED_MANUAL_TICKETS, MAX_DAILY_DRAWDOWN, TELEGRAM_TO_TICKET
    global TRADES_PROCESSED_LEVELS, SOFT_SL_PERCENT, RISK_PERCENT_INDICES, MUTED_CHANNELS, CHANNEL_TP1_PCT
    global TIME_OFF_PEAK_FACTOR, DAY_RISK_FACTORS, PRIME_HOURS
    global SABR_TRAILING_ACTIVE, SABR_ANTI_DOUBLON_ACTIVE

    # 👉 LE BOUCLIER EST ICI : On bloque tout ce qui n'est pas le maître
    if ADMIN_ID == 0 or event.sender_id != ADMIN_ID: # 👈 CORRECTION 5 (sender_id)
        return

    text = event.raw_text.lower().strip()

# --- 1. STATUS & INFO ---
    if text == "/status" or text == "/balance" or text == "/info":
        if not await run_bg(mt5.initialize): await event.reply("❌ MT5 Off"); return
        acc = await run_bg(mt5.account_info)
        
        daily_total, daily_realized, daily_float = get_accurate_daily_stats()
        
        start_bal_theo = acc.balance - daily_realized
        daily_pct = (daily_total / start_bal_theo * 100) if start_bal_theo > 0 else 0.0
        
        pos = await run_bg(mt5.positions_get, group="*")
        nb = len([p for p in pos if p.magic == MAGIC_NUMBER or p.ticket in MANAGED_MANUAL_TICKETS]) if pos else 0
        
        risk_g = round(RISK_PERCENT_GOLD * 100, 2)
        risk_o = round(RISK_PERCENT_OTHER * 100, 2)
        risk_i = round(RISK_PERCENT_INDICES * 100, 2)

        def fmt_ch(val, ch_name, is_lot=False):
            if ch_name in MUTED_CHANNELS:
                return "🔇"
            if is_lot:
                return f"{val} lot"
            return f"{val*100:.2f}%" if val else "Auto"
        
        secu_state = "🔴 ACTIVÉE (STOP)" if security_triggered else "🟢 VEILLE (OK)"
        icon_day = "🟢" if daily_total >= 0 else "🔴"
        
        if TRAILING_STOP_PIPS and TRAILING_STOP_PIPS > 0:
            sl_info = f"Actif (**{TRAILING_STOP_PIPS} pips**)"
        else:
            sl_info = "🔒 Inactif (Mode Paliers)"

        soft_txt = f"{SOFT_SL_PERCENT*100:.0f}%"
        doublon_state = "🛡️ Actif" if SABR_ANTI_DOUBLON_ACTIVE else "⚠️ Désactivé (Risque Spam)"
        sabr_trail_state = "✅ Actif" if SABR_TRAILING_ACTIVE else "⏸️ Désactivé (BE seul)"

        # --- ⏳ Calcul du Statut Horaire Actuel ---
        current_h = datetime.now().hour
        is_prime = current_h in PRIME_HOURS
        time_status = "🌞 **Heure Pleine** (Risque normal)" if is_prime else f"🌙 **Heure Creuse** (Risque réduit x{TIME_OFF_PEAK_FACTOR})"
        
        # --- 📅 Formatage des Risques Journaliers ---
        days_abbr = {0: "L", 1: "M", 2: "M", 3: "J", 4: "V", 5: "S", 6: "D"}
        # Le ":g" permet d'afficher "1" au lieu de "1.0", mais garde "0.5" tel quel !
        risk_days_str = ", ".join([f"{days_abbr[k]}:{v:g}" for k, v in sorted(DAY_RISK_FACTORS.items())])

        await event.reply(f"👀 **INFOS COMPTE**\n\n"
                          f"🌙 Balance 00h: {daily_start_balance:.2f}€\n"
                          f"💰 Balance: {acc.balance:.2f}€\n"
                          f"📈 Equity: {acc.equity:.2f}€\n"
                          f"\n"
                          f"📊 **P&L Jour: {icon_day} {daily_total:+.2f}€** ({daily_pct:+.2f}%)\n"
                          f"   ├─ Encaissé: {daily_realized:+.2f}€\n"
                          f"   └─ Flottant: {daily_float:+.2f}€\n"
                          f"\n"
                          f"Trades Actifs: {nb}\n"
                          f"Sécurité DD: {secu_state}\n"
                          f"**SL Suiveur: {sl_info}** (Vente: {soft_txt})\n"
                          f"**Anti-Doublon SABR:** {doublon_state}\n"
                          f"**SABR Trailing:** {sabr_trail_state}\n"
                          f"\n"
                          f"Or: **{risk_g}%** | Indices: **{risk_i}%** | Autre: **{risk_o}%**\n"
                          f"MDT VIP: **{fmt_ch(RISK_PERCENT_MDT_VIP, 'CANAL MDT_VIP')}** | MDT Free: **{fmt_ch(RISK_PERCENT_MDT_FREE, 'CANAL MDT_FREE')}**\n"
                          f"SABR VIP: **{fmt_ch(FIXED_LOT_SABR_VIP, 'CANAL SABR_VIP', True)}** | SABR Free: **{fmt_ch(FIXED_LOT_SABR_FREE, 'CANAL SABR_FREE', True)}**\n"
                          f"Space: **{fmt_ch(RISK_PERCENT_SPACE, 'CANAL SPACE')}** | Ligue: **{fmt_ch(RISK_PERCENT_LIGUE, 'CANAL LIGUE')}** | Perso: **{fmt_ch(RISK_PERCENT_PERSO, 'CANAL PERSO')}**"
                          f"\n\n"
                          f"{time_status}\n\n"
                          f"📅 Jours : **{risk_days_str}**")

# --- 2. FERMETURE TOTALE ---
    elif text == "/closeall":
        pos_list = await run_bg(mt5.positions_get)
        c = 0
        if pos_list:
            for p in pos_list:
                if p.magic == MAGIC_NUMBER or p.ticket in MANAGED_MANUAL_TICKETS:
                    otype = mt5.ORDER_TYPE_SELL if p.type == mt5.ORDER_TYPE_BUY else mt5.ORDER_TYPE_BUY
                    req = {"action": mt5.TRADE_ACTION_DEAL, "symbol": p.symbol, "volume": p.volume, "type": otype, "position": p.ticket, "magic": MAGIC_NUMBER, "type_filling": mt5.ORDER_FILLING_IOC}
                    await run_bg(_send_order_blocking, req); c += 1
        await event.reply(f"🚨 **PANIC:** {c} trades fermés.")

# --- 3. FERMETURE CIBLÉE ---
    elif text.startswith("/close "):
        try:
            parts = text.split(); symbol_key = parts[1].upper()
            symbol_to_close = SYMBOL_MAPPING.get(symbol_key, symbol_key)
            pos_list = await run_bg(mt5.positions_get)
            count = 0
            if pos_list:
                for p in pos_list:
                    if symbol_to_close in p.symbol.upper() and (p.magic == MAGIC_NUMBER or p.ticket in MANAGED_MANUAL_TICKETS):
                        otype = mt5.ORDER_TYPE_SELL if p.type == mt5.ORDER_TYPE_BUY else mt5.ORDER_TYPE_BUY
                        req = {"action": mt5.TRADE_ACTION_DEAL, "symbol": p.symbol, "volume": p.volume, "type": otype, "position": p.ticket, "magic": MAGIC_NUMBER, "type_filling": mt5.ORDER_FILLING_IOC}
                        await run_bg(_send_order_blocking, req); count += 1
            await event.reply(f"✅ **CIBLÉ :** {count} trades fermés sur {symbol_to_close}.")
        except Exception as e: await event.reply(f"❌ Erreur: {e}")

# --- 4. GESTION DU RISQUE (GLOBAL) ---
    elif text.startswith("/risk_gold"):
        try:
            val = float(text.split()[1]); RISK_PERCENT_GOLD = val / 100 
            await run_bg(save_settings); await event.reply(f"🔵 **RISQUE OR MODIFIÉ** : **{val}%**")
        except: await event.reply("❌ Usage: /risk_gold 1.5")

    elif text.startswith("/risk_other"):
        try:
            val = float(text.split()[1]); RISK_PERCENT_OTHER = val / 100 
            await run_bg(save_settings); await event.reply(f"🔵 **RISQUE AUTRE MODIFIÉ** : **{val}%**")
        except: await event.reply("❌ Usage: /risk_other 0.5")
    
    elif text.startswith("/risk_indice"):
        try:
            val = float(text.split()[1]); RISK_PERCENT_INDICES = val / 100 
            await run_bg(save_settings); await event.reply(f"📈 **RISQUE INDICES MODIFIÉ** : **{val}%**")
        except: await event.reply("❌ Usage: /risk_indice 0.8")

    elif text.startswith("/risk_all"):
        try:
            val = float(text.split()[1]); RISK_PERCENT_GOLD = val/100; RISK_PERCENT_OTHER = val/100 
            await run_bg(save_settings); await event.reply(f"🔵 **RISQUE GLOBAL MODIFIÉ** : **{val}%**")
        except: await event.reply("❌ Usage: /risk_all 0.5")

# --- 5. GESTION DU RISQUE (CANAUX) ---
    
    # MDT VIP (Anciennement VIP)
    elif text.startswith("/risk_mdt_vip") or text.startswith("/risk_vip"):
        try:
            val_raw = text.split()[1]
            if val_raw.lower() == "auto": RISK_PERCENT_MDT_VIP = None
            else: RISK_PERCENT_MDT_VIP = float(val_raw) / 100 
            await run_bg(save_settings); await event.reply(f"💎 Risque MDT VIP : **{val_raw}%**")
        except: await event.reply("❌ Usage: /risk_mdt_vip 1.0 (ou 'auto')")

    # MDT FREE (Anciennement Gratuit)
    elif text.startswith("/risk_mdt_free") or text.startswith("/risk_gratuit"):
        try:
            val_raw = text.split()[1]
            if val_raw.lower() == "auto": RISK_PERCENT_MDT_FREE = None
            else: RISK_PERCENT_MDT_FREE = float(val_raw) / 100 
            await run_bg(save_settings); await event.reply(f"🎁 Risque MDT FREE : **{val_raw}%**")
        except: await event.reply("❌ Usage: /risk_mdt_free 1.0 (ou 'auto')")

    # SABR VIP (Lots Fixes)
    elif text.startswith("/risk_sabr_vip"):
        try:
            val_raw = text.split()[1]
            # Pas de division par 100 ici ! On prend la valeur brute (ex: 0.10)
            FIXED_LOT_SABR_VIP = float(val_raw) 
            await run_bg(save_settings)
            await event.reply(f"👑 Lot Fixe SABR VIP : **{FIXED_LOT_SABR_VIP} lots**")
        except: await event.reply("❌ Usage: /risk_sabr_vip 0.10")

    # SABR FREE (Lots Fixes)
    elif text.startswith("/risk_sabr_free") or text.startswith("/risk_sabr"):
        try:
            val_raw = text.split()[1]
            # Pas de division par 100 ici !
            FIXED_LOT_SABR_FREE = float(val_raw) 
            await run_bg(save_settings)
            await event.reply(f"🔫 Lot Fixe SABR FREE : **{FIXED_LOT_SABR_FREE} lots**")
        except: await event.reply("❌ Usage: /risk_sabr_free 0.06")

    # AUTRES CANAUX
    elif text.startswith("/risk_space"):
        try:
            val_raw = text.split()[1]
            if val_raw.lower() == "auto": RISK_PERCENT_SPACE = None
            else: RISK_PERCENT_SPACE = float(val_raw) / 100 
            await run_bg(save_settings); await event.reply(f"🌌 Risque Space : **{val_raw}%**")
        except: await event.reply("❌ Usage: /risk_space 1.0")

    elif text.startswith("/risk_ligue"):
        try:
            val_raw = text.split()[1]
            if val_raw.lower() == "auto": RISK_PERCENT_LIGUE = None
            else: RISK_PERCENT_LIGUE = float(val_raw) / 100 
            await run_bg(save_settings); await event.reply(f"🏆 Risque Ligue : **{val_raw}%**")
        except: await event.reply("❌ Usage: /risk_ligue 1.0")

    elif text.startswith("/risk_perso"):
        try:
            val_raw = text.split()[1]
            if val_raw.lower() == "auto": RISK_PERCENT_PERSO = None
            else: RISK_PERCENT_PERSO = float(val_raw) / 100 
            await run_bg(save_settings); await event.reply(f"👤 Risque Perso : **{val_raw}%**")
        except: await event.reply("❌ Usage: /risk_perso 1.0")

# --- 6. DÉVERROUILLAGE SÉCURITÉ ---
    elif text == "/unlock":
        security_triggered = False
        await event.reply(f"🔓 **SÉCURITÉ DÉVERROUILLÉE**")

# --- 7. SUIVI MANUEL ---
    elif text.startswith("/suivi"):
        try:
            parts = text.split(); ticket_id = int(parts[1])
            p_open = await run_bg(mt5.positions_get, ticket=ticket_id)
            if not p_open: await event.reply("❌ Ticket introuvable."); return
            MANAGED_MANUAL_TICKETS.add(ticket_id); await run_bg(save_memory)
            ACTIVE_TRADE_SOURCES[ticket_id] = "CANAL PERSO"; await run_bg(save_channel_data)
            await event.reply(f"✅ **SUIVI ACTIVÉ** (Trade #{ticket_id})")
        except: await event.reply("❌ Erreur ID")

# --- 8. ARRÊT TOTAL ---
    elif text == "/kill":
        with open("STOP_ME", "w") as f: f.write("STOP")
        await event.reply("💀 **ARRET DU BOT...** (Adieu)"); sys.exit()

# --- 9. REGLAGE SL SUIVEUR ---
    elif text.startswith("/sl_suiveur"):
        try:
            val_raw = text.split()[1]
            if val_raw.lower() == "auto" or val_raw == "0":
                TRAILING_STOP_PIPS = 0; await run_bg(save_settings); await event.reply("🔒 SL Suiveur : Mode **PALIERS**")
            else:
                val = int(val_raw); TRAILING_STOP_PIPS = val; await run_bg(save_settings); await event.reply(f"🔒 SL Suiveur : **{val} pips**")
        except: await event.reply("❌ Usage: /sl_suiveur 80")

# --- 10. REGLAGE SOFT SL VENTE ---
    elif text.startswith("/sl_sell"):
        try:
            val = float(text.split()[1]); 
            if val > 1: val = val/100
            SOFT_SL_PERCENT = val; await run_bg(save_settings); await event.reply(f"📉 Vente Soft SL : **{val*100:.0f}%**")
        except: await event.reply("❌ Usage: /sl_sell 20")

# --- 10.bis REGLAGE TRAILING SABR ---
    elif text.startswith("/trail_sabr"):
        try:
            val_raw = text.split()[1].lower()
            if val_raw in ["on", "oui", "true", "1"]:
                SABR_TRAILING_ACTIVE = True
                await run_bg(save_settings)
                await event.reply("⏫ **Trailing Stop SABR : ACTIVÉ**")
            elif val_raw in ["off", "non", "false", "0"]:
                SABR_TRAILING_ACTIVE = False
                await run_bg(save_settings)
                await event.reply("⏸️ **Trailing Stop SABR : DÉSACTIVÉ** (Seul le BE à 150p restera)")
            else:
                await event.reply("❌ Usage: `/trail_sabr on` ou `/trail_sabr off`")
        except: await event.reply("❌ Usage: `/trail_sabr off`")

# --- 10.ter REGLAGE ANTI-DOUBLON SABR ---
    elif text.startswith("/doublon_sabr"):
        try:
            val_raw = text.split()[1].lower()
            if val_raw in ["on", "oui", "true", "1"]:
                SABR_ANTI_DOUBLON_ACTIVE = True
                await run_bg(save_settings)
                await event.reply("🛡️ **Anti-Doublon SABR : ACTIVÉ** (Bloque les trades répétés < 30min)")
            elif val_raw in ["off", "non", "false", "0"]:
                SABR_ANTI_DOUBLON_ACTIVE = False
                await run_bg(save_settings)
                await event.reply("⚠️ **Anti-Doublon SABR : DÉSACTIVÉ** (Tous les signaux seront pris !)")
            else:
                await event.reply("❌ Usage: `/doublon_sabr on` ou `/doublon_sabr off`")
        except: await event.reply("❌ Usage: `/doublon_sabr off`")

# --- 10.quater REGLAGE FORWARD SABR ---
    elif text.startswith("/forward_sabr"):
        try:
            val_raw = text.split()[1].lower()
            if val_raw in ["on", "oui", "true", "1"]:
                SABR_FORWARD_ACTIVE = True
                await run_bg(save_settings)
                await event.reply("📤 **Transfert SABR : ACTIVÉ** (Les messages seront copiés dans ton canal)")
            elif val_raw in ["off", "non", "false", "0"]:
                SABR_FORWARD_ACTIVE = False
                await run_bg(save_settings)
                await event.reply("🔇 **Transfert SABR : DÉSACTIVÉ**")
            else:
                await event.reply("❌ Usage: `/forward_sabr on` ou `/forward_sabr off`")
        except: await event.reply("❌ Usage: `/forward_sabr off`")

# --- 10.5 RÉACTIVATION D'UNE ZONE SABR ---
    elif text.startswith("/reactivate") or text.startswith("/sabr_reactivate"):
        try:
            parts = text.split()
            # On s'attend à 3 parties : /reactivate [ID] [%]
            if len(parts) != 3:
                await event.reply("❌ Format incorrect. Usage: `/reactivate [ID] [%]`\nExemple: `/reactivate 1 50`")
                return

            sig_id = int(parts[1])
            pct_str = parts[2].replace('%', '')
            custom_pct = float(pct_str) / 100.0

            # On vérifie si ce numéro de signal existe aujourd'hui
            if sig_id not in SABR_DAILY_SIGNALS:
                await event.reply(f"❌ Signal ID {sig_id} introuvable aujourd'hui.\nSignaux dispos : {list(SABR_DAILY_SIGNALS.keys())}")
                return

            # On récupère les infos sauvegardées
            sig = SABR_DAILY_SIGNALS[sig_id]

            await event.reply(f"🔄 **RÉACTIVATION EN COURS (Signal #{sig_id})**\nSymbole: {sig['sym']} {sig['act']}\nTaille des lots: **{custom_pct*100:.0f}%**")
            
            # On renvoie le trade dans la machine avec le multiplicateur !
            await execute_trade(sig['sym'], sig['act'], sig['sl'], sig['tp'], sig['p_msg'], datetime.now(), sig['source'], custom_lot_modifier=custom_pct)
            
        except Exception as e:
            await event.reply(f"❌ Erreur: {e}\nUsage: `/reactivate 1 50`")

# --- 11. GESTION MUTE CANAUX (MIS À JOUR) ---
    elif text.startswith("/mute"):
        try:
            parts = text.split(); target = parts[1].upper()
            
            # Mapping pour faciliter la vie de l'utilisateur
            mapping_mute = {
                "VIP": "CANAL MDT_VIP", "MDT_VIP": "CANAL MDT_VIP",
                "FREE": "CANAL MDT_FREE", "GRATUIT": "CANAL MDT_FREE", "MDT_FREE": "CANAL MDT_FREE",
                "SPACE": "CANAL SPACE", "LIGUE": "CANAL LIGUE", "PERSO": "CANAL PERSO",
                "SABR": "CANAL SABR_FREE_SNIPER", # Mute tout le SABR FREE
                "SABR_VIP": "CANAL SABR_VIP_SNIPER", 
                "SABR_CP": "CANAL SABR_COPIE_SNIPER" # Mute tout le SABR VIP
            }
            
            # Astuce : Pour SABR, on mute le préfixe principal
            if "SABR" in target and "VIP" in target: full_name = "CANAL SABR_VIP" # D'abord le VIP
            elif "SABR" in target and ("COPIE" in target or "CP" in target): full_name = "CANAL SABR_COPIE"
            elif "SABR" in target: full_name = "CANAL SABR_FREE"
            elif target in mapping_mute: full_name = mapping_mute[target]
            else: full_name = f"CANAL {target}" # Tentative directe

            # On ajoute à la liste des mute (gestion partielle des préfixes SABR dans le main_handler)
            if full_name not in MUTED_CHANNELS:
                MUTED_CHANNELS.append(full_name)
                await run_bg(save_settings)
                await event.reply(f"🔇 **{full_name} est MUET.**")
            else:
                await event.reply(f"⚠️ {full_name} est déjà muet.")
        except: await event.reply("❌ Erreur commande /mute")

    elif text.startswith("/unmute"):
        try:
            parts = text.split(); target = parts[1].upper()
            # Même logique simplifiée
            full_name = f"CANAL {target}"
            # On essaie de trouver le nom exact dans la liste
            found = False
            for m in MUTED_CHANNELS:
                if target in m: # Ex: "VIP" dans "CANAL MDT_VIP"
                    MUTED_CHANNELS.remove(m); found = True; await run_bg(save_settings); await event.reply(f"🔊 **{m} est RÉACTIVÉ.**")
                    break
            if not found: await event.reply(f"⚠️ {target} n'était pas muet.")
        except: await event.reply("❌ Erreur commande")

# --- 12. REGLAGE POURCENTAGE CLOTURE TP1 (MIS À JOUR) ---
    elif text.startswith("/sell_lot"):
        try:
            parts = text.split()
            if len(parts) < 3: await event.reply("❌ Format: `/sell_lot CANAL %`"); return

            target = parts[1].upper()
            # Mapping intelligent
            if "VIP" in target and "SABR" not in target: full_name = "CANAL MDT_VIP"
            elif "FREE" in target or "GRATUIT" in target: full_name = "CANAL MDT_FREE"
            if "SABR" in target and "VIP" in target: full_name = "CANAL SABR_VIP" # D'abord le VIP
            elif "SABR" in target and ("COPIE" in target or "CP" in target): full_name = "CANAL SABR_COPIE"
            elif "SABR" in target: full_name = "CANAL SABR_FREE"
            else: full_name = f"CANAL {target}"

            val_raw = float(parts[2])
            if val_raw > 1: val_raw = val_raw / 100
            
            CHANNEL_TP1_PCT[full_name] = val_raw
            await run_bg(save_settings)
            await event.reply(f"📉 **TP1 {full_name}** : **{val_raw*100:.0f}%**")
        except: await event.reply("❌ Erreur de saisie.")

# --- 13. REGLAGE MAX DRAWDOWN ---
    elif text.startswith("/max_dd"):
        try:
            val = float(text.split()[1]); 
            if val > 1: val = val/100
            MAX_DAILY_DRAWDOWN = val; await run_bg(save_settings); await event.reply(f"📉 Max DD : **{val*100:.1f}%**")
        except: await event.reply("❌ Usage: /max_dd 5")

# --- 14. FILTRES TEMPORELS ---
    elif text.startswith("/risk_heure"):
        # Ex: /risk_heure 8-10 11-16 0.5
        try:
            parts = text.split()
            factor = float(parts[-1]) # Le dernier élément est le facteur
            ranges_str = parts[1:-1]  # Tout ce qu'il y a entre la commande et le facteur
            
            new_prime_hours = set() # On utilise un set pour éviter les doublons d'heures
            msg_ranges = ""
            
            for r in ranges_str:
                start, end = map(int, r.split('-'))
                # On ajoute chaque heure de la plage dans notre nouvelle liste
                for h in range(start, end):
                    new_prime_hours.add(h)
                msg_ranges += f"✅ {start}h à {end}h\n"
            
            if not new_prime_hours: raise ValueError("Pas de plage")
            
            # On met à jour nos nouvelles variables globales
            PRIME_HOURS = list(new_prime_hours) 
            TIME_OFF_PEAK_FACTOR = factor
            
            await run_bg(save_settings)
            await event.reply(f"⏰ **FILTRE HORAIRE**\n\n"
                              f"{msg_ranges}"
                              f"📉 Facteur Hors-Zone : **x{factor}**")
        except: await event.reply("❌ Usage: `/risk_heure 8-10 11-16 0.5`")

    elif text.startswith("/risk_day"):
        days_map = {"lundi":0, "mardi":1, "mercredi":2, "jeudi":3, "vendredi":4, "samedi":5, "dimanche":6}
        try:
            parts = text.split(); day_name = parts[1].lower(); factor = float(parts[2])
            if day_name in days_map:
                DAY_RISK_FACTORS[days_map[day_name]] = factor
                await run_bg(save_settings); await event.reply(f"📅 {day_name.upper()} : Risque x{factor}")
        except: await event.reply("❌ Usage: /risk_day vendredi 0.33")

# --- 15. RAPPORTS ---
    elif text == "/stats":
        if not await run_bg(mt5.initialize): await event.reply("❌ MT5 Off"); return
        acc = await run_bg(mt5.account_info)
        day_total, _, _ = get_accurate_daily_stats()
        start_bal = acc.balance - day_total # Approx
        day_pct = (day_total / start_bal * 100) if start_bal > 0 else 0.0
        await event.reply("⏳ Génération...")
        await send_daily_report(acc, day_total, day_pct)

# --- 16. STATISTIQUES PAR CANAL (VERSION DÉTAILLÉE) ---
    elif text == "/canal_stats":
        if not CHANNEL_STATS:
            await event.reply("⚠️ Aucune donnée enregistrée pour le moment.")
            return

        msg = "📊 **PERFORMANCE PAR CANAL (Focus Pips)**\n\n"
        
        # On parcourt chaque canal enregistré
        for chan, data in CHANNEL_STATS.items():
            cnt = data["count"]
            prof = data["profit"]
            pips_total = data["pips"]
            
            # Calcul Moyenne
            avg_pips = (pips_total / cnt) if cnt > 0 else 0.0
            
            # L'icône dépend désormais des pips
            icon = "🟢" if pips_total >= 0 else "🔴"
            
            # Sécurité Telegram (Pour éviter le bug "Bad Request" des underscores)
            chan_clean = chan.replace("_", " ") 
            
            msg += (f"🔹 **{chan_clean}** {icon}\n"
                    f"   📦 Trades : {cnt}\n"
                    f"   💰 Total Gain: {prof:+.2f}€\n"
                    f"   🎯 Total Pips : {pips_total:+.1f}\n"
                    f"   ⚖️ **Moyenne : {avg_pips:+.1f} pips/trade**\n"
                    f"\n")
        
        await event.reply(msg)

# --- 17. AIDE ---
    elif text == "/help":
        msg = ("**🤖 MENU COMMANDES V11.1**\n\n\n"
               "**📌 GÉNÉRALES :**\n\n"
               "`/info` : Bilan compte\n"
               "`/stats` | `/canal_stats` : Rapports\n"
               "`/suivi 12345` : Activer suivi manuel\n"
               "`/unlock` : Débloquer après DD\n"
               "`/kill` : Stop Bot (Urgence)\n"
               "`/closeall` : Fermer TOUT\n"
               "`/close XAUUSD` : Fermer par paire\n\n\n"
               "**⚙️ RISQUES (0.5 = 0.5%) :**\n\n"
               "`/risk_gold 0.6` \n"
               "`/risk_other 0.6`\n"
               "`/risk_indice 0.8` \n"
               "`/risk_all 0.5`\n"
               "`/risk_mdt_vip 1.0` \n"
               "`/risk_mdt_free 0.5`\n"
               "`/risk_sabr_vip 0.06` (Lots Fixes)\n"
               "`/risk_sabr_free 0.06` (Lots Fixes)\n"
               "`/risk_space 1.0` \n"
               "`/risk_ligue 1.0`\n"
               "`/risk_perso 1.0`\n\n\n"
               "**🛠️ GESTION & PARAMÈTRES :**\n\n"
               "`/max_dd 4` : Max Drawdown 4%\n"
               "`/sl_suiveur 85` : Trailing Pips (ou '0' pour paliers)\n"
               "`/sl_sell 30` : % Vente Soft SL\n"
               "`/sell_lot VIP 50` : % Vente TP1 (Tous canaux)\n"
               "`/reactivate 1 50` : Relancer signal SABR (ID: 1, 50%)\n"
               "`/trail_sabr on/off` : Trailing Stop SABR\n"
               "`/doublon_sabr on/off` : Anti-Doublon SABR\n"
               "`/forward_sabr on/off` : Transfert msg SABR\n"
               "`/risk_heure 8-10 11-16 0.5` : Diviseur horaire\n"
               "`/risk_day vendredi 0.33` : Diviseur journalier\n"
               "`/mute VIP` | `/unmute VIP` : Ignorer canal")
        await event.reply(msg)

# ==========================================
# 👂 LISTENER PRINCIPAL (MULTI-CANAUX)
# ==========================================

@client.on(events.NewMessage(chats=CHANNEL_LIST))
async def main_handler(event):
    # --- AJOUT DEBUG ---
    log(f"👀 J'ai vu un message ! ID: {event.id}")
    log(f"Texte brut: {event.raw_text}")

    global PROCESSED_MSG_IDS
    PROCESSED_MSG_IDS.add(event.id)

    # === 📤 TRANSFERT DES MESSAGES SABR (FORWARDER) ===
    if SABR_FORWARD_ACTIVE and event.chat_id in [ID_SABR_VIP, -5093508256]:
        try:
            global http_session
            # On envoie le message original vers ton canal de copie
            prefix_source = "👑 [SABR VIP]" if event.chat_id == ID_SABR_VIP else "🆓 [TEST]"
            msg_forward = f"{prefix_source}\n\n{event.raw_text}"
            
            # On utilise le Bot (via son Token) pour envoyer le message, comme pour les alertes
            bot_url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
            payload = {"chat_id": ID_CANAL_SABR_COPIE, "text": msg_forward}
            
            if http_session is None or http_session.closed:
                http_session = aiohttp.ClientSession()
            await http_session.post(bot_url, json=payload, ssl=False)
            
        except Exception as e:
            logger.error(f"⚠️ Erreur de transfert du message SABR : {e}")

    # ==================================================================
    # 👇 GESTION DES COMMANDES ADMIN (CLÔTURE & SUPPRESSION) 👇
    # ==================================================================
    if event.is_reply:
        txt_upper = event.raw_text.upper()
        
        # Mots-clés déclencheurs (Positions ET Ordres en attente)
        keywords_stop = [
            "FERMETURE", "CLOSE", "FERMER", "SORTIE", "CLÔTURE", "CLOTURE", # Classique
            "SUPPRIMER", "SUPPRIMEZ", "SUPPRIME", "ON SUPPRIME",            # Ta demande
            "ENLÈVE", "ENLEVE", "ON ENLÈVE", "ANNULE", "ANNULER"            # Ta demande
        ]

        keywords_ignore = ["PARTIEL", "PARTIELLE", "MOITIÉ", "MOITIE", "HALF", "PROFIT"]
        
        if any(bad_word in txt_upper for bad_word in keywords_ignore):
            return
        
        if any(k in txt_upper for k in keywords_stop):
            # 1. On récupère l'ID du message d'origine
            original_msg_id = event.reply_to_msg_id
            
            # 2. On vérifie si on a un trade lié
            if original_msg_id in TELEGRAM_TO_TICKET:
                tickets_to_manage = TELEGRAM_TO_TICKET[original_msg_id] # C'est une liste !
                logger.warning(f"🚨 COMMANDE STOP DÉTECTÉE SUR TICKET(S) {tickets_to_manage}")
                
                closed_count = 0
                for tk in tickets_to_manage:
                    # --- CAS A : C'EST UN ORDRE EN ATTENTE (PENDING) ? ---
                    orders_data = await run_bg(mt5.orders_get, ticket=tk)
                    if orders_data and len(orders_data) > 0:
                        # C'est un ordre -> ON SUPPRIME (REMOVE)
                        req = {
                            "action": mt5.TRADE_ACTION_REMOVE,
                            "order": tk,
                            "magic": MAGIC_NUMBER
                        }
                        res = await run_bg(_send_order_blocking, req)

                        if res is None:
                            await send_alert(f"⚠️ Timeout MT5: Impossible de supprimer l'ordre #{tk}")
                            continue
                        
                        if res.retcode == mt5.TRADE_RETCODE_DONE:
                            await send_alert(f"🗑️ **ORDRE SUPPRIMÉ**\n\nTicket #{tk} annulé.")
                            if tk in PENDING_ORDER_TICKETS: PENDING_ORDER_TICKETS.remove(tk)
                            closed_count += 1
                        else:
                            await send_alert(f"⚠️ Échec suppression ordre #{tk}: {res.comment}")
                        continue # On passe au ticket suivant

                    # --- CAS B : C'EST UNE POSITION ACTIVE ? ---
                    pos_data = await run_bg(mt5.positions_get, ticket=tk)
                    if pos_data and len(pos_data) > 0:
                        # C'est une position -> ON FERME (CLOSE)
                        pos = pos_data[0]
                        otype = mt5.ORDER_TYPE_SELL if pos.type == mt5.ORDER_TYPE_BUY else mt5.ORDER_TYPE_BUY
                        price = mt5.symbol_info_tick(pos.symbol).bid if otype == mt5.ORDER_TYPE_SELL else mt5.symbol_info_tick(pos.symbol).ask
                        
                        req = {
                            "action": mt5.TRADE_ACTION_DEAL, "symbol": pos.symbol, "volume": pos.volume,
                            "type": otype, "position": tk, "price": price,
                            "magic": MAGIC_NUMBER, "comment": "Bot - Stop Admin", "type_filling": mt5.ORDER_FILLING_IOC,
                        }
                        res = await run_bg(_send_order_blocking, req)

                        if res is None:
                            await send_alert(f"⚠️ Timeout MT5: Impossible de fermer la position #{tk}")
                            continue
                        
                        if res.retcode == mt5.TRADE_RETCODE_DONE:
                            await send_alert(f"✅ **POSITION FERMÉE**\n\nTicket #{tk} clôturé.")
                            if tk in MANAGED_MANUAL_TICKETS: MANAGED_MANUAL_TICKETS.remove(tk)
                            closed_count += 1
                        else:
                            await send_alert(f"⚠️ Échec fermeture position #{tk}: {res.comment}")
                        continue

                # Bilan de la commande
                if closed_count > 0:
                    del TELEGRAM_TO_TICKET[original_msg_id]
                else:
                    await send_alert(f"⚠️ Commande reçue mais tickets introuvables (Déjà fermés ?).")
                
                return # Fin de la gestion commande
    # ==================================================================

    if event.id in TELEGRAM_TO_TICKET: return

    raw_text = event.raw_text
    if any(w in raw_text.upper() for w in BLACKLIST_WORDS): return
    
    # DEBUG : On affiche ce qu'on reçoit
    logger.info(f"\n📩 MESSAGE REÇU: {raw_text[:50]}...") 

    PENDING_MESSAGES[event.id] = raw_text
    
    try:
        # Pause Humaine
        delay = random.uniform(2.0, 4.0)
        await asyncio.sleep(delay)
        
        # --- VERIFICATION ANTI-SUPPRESSION ---
        # Si le message n'est plus dans la liste, c'est qu'il a été supprimé pendant la sieste !
        if event.id not in PENDING_MESSAGES:
            logger.warning(f"🛑 Stop: Le message {event.id} a été supprimé pendant l'attente.")
            return # On arrête tout, pas de trade.
            
        final_text = PENDING_MESSAGES[event.id]
        # -------------------------------------

        # DEBUG : On affiche le texte nettoyé pour vérifier
        cleaned = clean_text(final_text)
        # print(f"🧹 Nettoyé: {cleaned}")

        channel_to_use = event.chat_id
        
        if event.chat_id == ID_CANAL_PERSO:
            txt_upper = final_text.upper()
            if "SABR" in txt_upper:
                channel_to_use = ID_SABR_VIP

        rsym, act, p_msg, sl, tp, format_detected = parse_signal(final_text, channel_to_use)
        
        # === 🕵️ DEBUG SABR (LECTURE) ===
        if format_detected and "SABR" in format_detected:
            # On met le texte brut sur une seule ligne pour que ce soit propre dans les logs
            flat_text = final_text.replace('\n', ' | ') 
            logger.info(f"\n{'='*50}\n"
                        f"🕵️ DIAGNOSTIC SABR (ÉTAPE 1 : LECTURE)\n"
                        f"📝 Texte reçu   : {flat_text}\n"
                        f"🧹 Texte propre : {cleaned}\n"
                        f"🎯 Type déduit  : {format_detected}\n"
                        f"🛒 Action       : {act} {rsym}\n"
                        f"💲 Prix lu(s)   : {p_msg}\n"
                        f"🛑 SL lu        : {sl}\n"
                        f"🎯 TP lu        : {tp}\n" # <--- AJOUTE CETTE LIGNE !
                        f"{'='*50}")
        # ===============================
        
        if rsym and act:
            log(f"✅ SIGNAL VALIDE: {act} {rsym}", important=True) # Confirmation visuelle
            
            sym = find_tradable_symbol(rsym)
            if not sym:
                # L'alerte Telegram a été retirée, on garde uniquement le log local
                logger.error(f"❌ Erreur: Symbole {rsym} non trouvé dans MT5")
                return
            
            log_p = p_msg if p_msg else "MARKET"

            final_source = format_detected

            if event.chat_id == ID_CANAL_PERSO:
                txt_upper = final_text.upper()
                # Forçage manuel si détection classique
                if "VIP" in txt_upper: final_source = "CANAL MDT_VIP"
                elif "FREE" in txt_upper or "GRATUIT" in txt_upper: final_source = "CANAL MDT_FREE"
                elif "SPACE" in txt_upper: final_source = "CANAL SPACE"
                elif "LIGUE" in txt_upper: final_source = "CANAL LIGUE"
                elif "SABR" in txt_upper: pass # Déjà géré par parse_signal via l'ID simulé
                elif final_source not in ["CANAL SPACE", "CANAL LIGUE", "CANAL SABR_VIP_SNIPER", "CANAL SABR_FREE_SNIPER"]:
                    final_source = "CANAL PERSO"

            # --- GESTION AFFICHAGE SABR (TYPE SÉPARÉ) ---
            display_source = final_source.replace("_", " ") # Par défaut (ex: MDT VIP)
            display_type_line = "" # Par défaut vide

            if "SABR" in final_source:
                # 1. On nettoie la Source
                if "VIP" in final_source:
                    display_source = "CANAL SABR VIP"
                    raw_type = final_source.replace("CANAL SABR_VIP_", "")
                elif "COPIE" in final_source:
                    display_source = "CANAL SABR COPIE"
                    raw_type = final_source.replace("CANAL SABR_COPIE_", "")
                else:
                    display_source = "CANAL SABR FREE"
                    raw_type = final_source.replace("CANAL SABR_FREE_", "")
                
                # 2. On formate le Type (ex: "UPDATE SNIPER")
                clean_type = raw_type.replace("_", " ")

                # --- NOUVEAU : ENREGISTREMENT DE L'ID DU SIGNAL ---
                global SABR_DAILY_SIGNALS, SABR_SIGNAL_DAY
                c_day = datetime.now().day
                # Si on change de jour, on vide la liste pour repartir à 1
                if c_day != SABR_SIGNAL_DAY:
                    SABR_DAILY_SIGNALS.clear()
                    SABR_SIGNAL_DAY = c_day
                
                # On attribue le prochain numéro (ex: 1, puis 2, puis 3...)
                sig_id = len(SABR_DAILY_SIGNALS) + 1
                SABR_DAILY_SIGNALS[sig_id] = {
                    "sym": sym, "act": act, "sl": sl, "tp": tp, 
                    "p_msg": p_msg, "source": final_source
                }
                
                # 2. On ajoute la ligne Type et l'ID dans le message Telegram
                display_type_line = f"\n\nType : **{clean_type}**\n🆔 **Signal : {sig_id}**"
                # --------------------------------------------------

            # --- NOUVELLE LOGIQUE (MUTE TOTAL INTELLIGENT) ---
            is_muted = any(m in final_source for m in MUTED_CHANNELS)
            if is_muted:
                logger.warning(f"🚫 SIGNAL IGNORÉ : Le canal {final_source} est bloqué par le mode MUTE.")
                return # On arrête tout ici : pas d'alerte, PAS DE TRADE.

            # Si on arrive ici, le canal n'est pas muet -> On alerte et on trade
            await send_alert(f"✔️ Signal traité ({display_source}) :{display_type_line}\n\n {act} {sym} à {log_p}\n\n🛑 SL: {sl} & 🎯 TP: {tp}")
            
            # NOUVELLE LOGIQUE D'ALERTE SANS SPAM
            opened_tickets = await execute_trade(sym, act, sl, tp, p_msg, event.date, final_source)          
            
            if opened_tickets: 
                TELEGRAM_TO_TICKET[event.id] = opened_tickets
                # On alerte UNIQUEMENT si le trade a bien été ouvert (non bloqué par le doublon)
                await send_alert(f"✔️ **Signal Accepté** ({display_source}){display_type_line}\n🛑 SL: {sl} | 🎯 TP: {tp}")
        else:
            logger.info(f"⚠️ Ignoré (Parsing échoué) sur : {cleaned}")

    except Exception as e:
        logger.error(f"❌ ERREUR HANDLER: {e}")
        import traceback
        traceback.print_exc()
        
    finally:
        PENDING_MESSAGES.pop(event.id, None)

# --- GESTION DES EDITS (MODIFIE TOUS LES ORDRES DE LA ZONE) ---
@client.on(events.MessageEdited(chats=CHANNEL_LIST))
async def edit_handler(event):
    if event.id in PENDING_MESSAGES:
        PENDING_MESSAGES[event.id] = event.raw_text
        return

    rsym, act, p_msg, sl, tp, format_detected = parse_signal(event.raw_text, event.chat_id)
    if not rsym: return 

    new_sl = float(sl) if sl else 0.0
    new_tp = float(tp) if tp else 0.0

    # ==========================================
    # 1. CAS DU SIGNAL AVEUGLE (BLIND -> PRECIS)
    # ==========================================
    # Si le message n'a pas encore de tickets liés, MAIS qu'il a maintenant un prix sans SL/TP :
    if event.id not in TELEGRAM_TO_TICKET and p_msg is not None and new_sl == 0.0 and new_tp == 0.0:
        # C'est un signal "Go buy" qui vient d'être mis à jour avec un prix !
        # On tente d'ouvrir le trade proprement, sans spammer.
        new_tix = await execute_trade(rsym, act, sl, tp, p_msg, event.date, str(format_detected))
        if new_tix: 
            TELEGRAM_TO_TICKET[event.id] = new_tix
            await send_alert(f"✔️ **Signal Aveugle Activé** ({format_detected})\n{act} {rsym} à {p_msg}")
        return

    # ==========================================
    # 2. MISE A JOUR SL / TP (NORMAL OU ORPHELIN)
    # ==========================================
    # Si pas de modification de SL ou TP, on ne fait rien
    if new_sl == 0.0 and new_tp == 0.0: return

    tickets_to_update = []

    if event.id in TELEGRAM_TO_TICKET:
        tickets_to_update = TELEGRAM_TO_TICKET[event.id]
    else:
        # Récupération des ORPHELINS (Bloqués par doublon initialement)
        sym = find_tradable_symbol(rsym)
        if not sym: return
        
        pos_list = await run_bg(mt5.positions_get, symbol=sym)
        if pos_list:
            for p in pos_list:
                if p.magic == MAGIC_NUMBER: tickets_to_update.append(p.ticket)
        
        ord_list = await run_bg(mt5.orders_get, symbol=sym)
        if ord_list:
            for o in ord_list:
                if o.magic == MAGIC_NUMBER: tickets_to_update.append(o.ticket)

    if not tickets_to_update: return

    success_count = 0
    for ticket in tickets_to_update:
        is_pending = False
        orders = await run_bg(mt5.orders_get, ticket=ticket)
        pos = None
        if orders and len(orders) > 0: is_pending = True
        else: pos = await run_bg(mt5.positions_get, ticket=ticket)

        if not is_pending and not pos: continue

        current_sl = orders[0].sl if is_pending else pos[0].sl
        current_tp = orders[0].tp if is_pending else pos[0].tp
        
        # Si le SL et le TP sont déjà les mêmes, on passe
        if abs(current_sl - new_sl) < 0.0001 and abs(current_tp - new_tp) < 0.0001:
            continue 

        req = {
            "action": mt5.TRADE_ACTION_MODIFY if is_pending else mt5.TRADE_ACTION_SLTP,
            "sl": new_sl, "tp": new_tp
        }
        
        if is_pending:
            req["order"] = ticket
            req["price"] = orders[0].price_open
        else:
            req["position"] = ticket
        
        res = await run_bg(_send_order_blocking, req)
        if res and res.retcode == mt5.TRADE_RETCODE_DONE:
            success_count += 1
            
    if success_count > 0:
        await send_alert(f"✏️ **TRADES MODIFIÉS** (Édition)\n\n{success_count} ordres/positions mis à jour sur {rsym}.\nNouveau SL: {new_sl}\nNouveau TP: {new_tp}")
        
# --- GESTION DES SUPPRESSIONS (NOUVEAU) ---
@client.on(events.MessageDeleted(chats=CHANNEL_LIST))
async def deletion_handler(event):
    # On parcourt les IDs des messages supprimés
    for msg_id in event.deleted_ids:
        
        # CAS 1 : C'est un message en attente de traitement (Buffer)
        if msg_id in PENDING_MESSAGES:
            logger.info(f"🗑️ Message {msg_id} supprimé du Buffer (Ne sera pas traité).")
            del PENDING_MESSAGES[msg_id]
            
        # CAS 2 : C'est un trade déjà actif (Ticket existant)
        if msg_id in TELEGRAM_TO_TICKET:
            tickets = TELEGRAM_TO_TICKET[msg_id] # Liste de tickets !
            
            for tk in tickets:
                # On vérifie si c'est un ORDRE EN ATTENTE (Pending)
                orders = await run_bg(mt5.orders_get, ticket=tk)
                if orders and len(orders) > 0:
                    # C'est un ordre pending -> ON LE SUPPRIME
                    req = {
                        "action": mt5.TRADE_ACTION_REMOVE,
                        "order": tk
                    }
                    res = await run_bg(_send_order_blocking, req)

                    if res is None:
                        logger.warning(f"⚠️ Timeout MT5: Echec suppression ordre #{tk} (Message supprimé)")
                        continue
                    
                    if res.retcode == mt5.TRADE_RETCODE_DONE:
                        await send_alert(f"🗑️ **SIGNAL SUPPRIMÉ (Canal source)**\n\nOrdre #{tk} annulé.")
            
            # Une fois la liste traitée, on nettoie la mémoire
            del TELEGRAM_TO_TICKET[msg_id]

async def risk_schedule_monitor():
    global PRIME_HOURS, TIME_OFF_PEAK_FACTOR
    last_state = None # Pour se souvenir de l'état précédent

    while True:
        try:
            now = datetime.now()
            # On ajoute le décalage broker pour être synchrone avec l'heure affichée
            # (Optionnel, mais plus propre si tu veux l'heure du bot)
            # broker_time = now + timedelta(hours=BROKER_TIME_OFFSET) 
            h = now.hour

            # 1. Détermination de l'état actuel
            # Est-ce qu'on est DANS la plage ?
            current_state = "PRIME" if h in PRIME_HOURS else "OFF"

            # 2. Détection du changement
            if last_state is None:
                # Premier démarrage : on ne dit rien, on note juste l'état
                last_state = current_state
            
            elif current_state != last_state:
                # C'EST ICI QUE ÇA A CHANGÉ !
                if current_state == "PRIME":
                    await send_alert(f"🌞 **OUVERTURE SESSION ({h}h00)**\n\n"
                                     f"✅ Risque : **100%** (Normal)\n\n"
                                     f"Bonne session !")
                else:
                    await send_alert(f"🌙 **FERMETURE SESSION ({h}h00)**\n\n"
                                     f"🛡️ Mode Nuit activé.\n\n"
                                     f"📉 Risque : **x{TIME_OFF_PEAK_FACTOR}** (Réduit)")
                
                # On met à jour la mémoire
                last_state = current_state

        except Exception as e: 
            logger.error(f"Schedule Error: {e}")

        # On vérifie toutes les 60 secondes pour ne pas rater l'heure pile
        await asyncio.sleep(60)

# ==========================================
# 🔥 FIREBASE — COLLECTE D'ÉTAT & HANDLER DE COMMANDES
# ==========================================

async def get_current_state_for_firebase() -> dict:
    """Collecte l'état complet du bot pour le push Firebase."""
    try:
        acc = await run_bg(mt5.account_info)
        if not acc:
            return None

        day_total, day_realized, day_float = get_accurate_daily_stats()
        start_bal = acc.balance - day_realized
        day_pct = (day_total / start_bal * 100) if start_bal > 0 else 0.0

        pos_list = await run_bg(mt5.positions_get)
        active_trades = []
        if pos_list:
            for p in pos_list:
                is_managed = (p.magic == MAGIC_NUMBER or p.ticket in MANAGED_MANUAL_TICKETS)
                otype = "BUY" if p.type == mt5.ORDER_TYPE_BUY else "SELL"
                tick = mt5.symbol_info_tick(p.symbol)
                current_p = tick.bid if otype == "BUY" else tick.ask
                pips = calculate_pips(p.symbol, abs(p.price_open - current_p))
                if (otype == "BUY" and current_p < p.price_open) or (otype == "SELL" and current_p > p.price_open):
                    pips = -pips
                open_time_str = (datetime.fromtimestamp(p.time) - timedelta(hours=1)).strftime('%H:%M')
                active_trades.append({
                    "ticket": p.ticket, "symbol": p.symbol, "type": otype,
                    "volume": p.volume, "open_price": p.price_open, "current_price": current_p,
                    "sl": p.sl, "tp": p.tp, "profit": p.profit, "pips": round(pips, 1),
                    "open_time": open_time_str,
                    "source": ACTIVE_TRADE_SOURCES.get(p.ticket, "Inconnu"),
                    "managed": is_managed,
                })

        closed_trades_today = await run_bg(_get_closed_trades_today_blocking)

        account = {
            "balance": acc.balance, "equity": acc.equity, "free_margin": acc.margin_free,
        }
        stats = {
            **daily_stats,
            "total_pnl": day_total, "realized_pnl": day_realized,
            "floating_pnl": day_float, "percentage": day_pct,
        }
        settings = {
            "risk_gold": RISK_PERCENT_GOLD, "risk_indices": RISK_PERCENT_INDICES,
            "risk_other": RISK_PERCENT_OTHER,
            "risk_mdt_vip": RISK_PERCENT_MDT_VIP or 0, "risk_mdt_free": RISK_PERCENT_MDT_FREE or 0,
            "risk_space": RISK_PERCENT_SPACE or 0, "risk_ligue": RISK_PERCENT_LIGUE or 0,
            "risk_perso": RISK_PERCENT_PERSO or 0,
            "sabr_lot_vip": FIXED_LOT_SABR_VIP, "sabr_lot_free": FIXED_LOT_SABR_FREE,
            "sabr_anti_doublon": SABR_ANTI_DOUBLON_ACTIVE, "sabr_trailing": SABR_TRAILING_ACTIVE,
            "sabr_forward": SABR_FORWARD_ACTIVE, "max_dd": MAX_DAILY_DRAWDOWN,
            "sl_suiveur": TRAILING_STOP_PIPS, "sl_sell": SOFT_SL_PERCENT,
            "muted_channels": MUTED_CHANNELS, "security_triggered": security_triggered,
            "time_off_peak": TIME_OFF_PEAK_FACTOR, "prime_hours": PRIME_HOURS,
            "day_factor_0": DAY_RISK_FACTORS.get(0, 1.0), "day_factor_1": DAY_RISK_FACTORS.get(1, 1.0),
            "day_factor_2": DAY_RISK_FACTORS.get(2, 1.0), "day_factor_3": DAY_RISK_FACTORS.get(3, 1.0),
            "day_factor_4": DAY_RISK_FACTORS.get(4, 1.0), "day_factor_5": DAY_RISK_FACTORS.get(5, 1.0),
            "day_factor_6": DAY_RISK_FACTORS.get(6, 1.0),
        }

        return firebase_sync.build_state_document(
            account, stats, settings, active_trades, closed_trades_today or [],
            extra={"channels_lifetime": CHANNEL_STATS}
        )
    except Exception as e:
        logger.error(f"Erreur collecte état Firebase : {e}")
        return None


async def handle_firebase_command(cmd_id: str, cmd_data: dict) -> None:
    """Reçoit une commande depuis Firestore et l'exécute sur MT5."""
    user_id = str(TELEGRAM_DESTINATION_ID)
    cmd_type = cmd_data.get("type", "")
    payload = cmd_data.get("payload", {})

    def set_status(status, result=None):
        firebase_sync.update_command_status(user_id, cmd_id, status, result)

    set_status("executing")
    try:
        if cmd_type == "close_trade":
            ticket = int(payload.get("ticket", 0))
            pos = await run_bg(mt5.positions_get, ticket=ticket)
            if pos:
                p = pos[0]
                otype = mt5.ORDER_TYPE_SELL if p.type == mt5.ORDER_TYPE_BUY else mt5.ORDER_TYPE_BUY
                tick_p = mt5.symbol_info_tick(p.symbol)
                price_p = tick_p.bid if otype == mt5.ORDER_TYPE_SELL else tick_p.ask
                req = {"action": mt5.TRADE_ACTION_DEAL, "symbol": p.symbol, "volume": p.volume,
                       "type": otype, "position": ticket, "price": price_p,
                       "magic": MAGIC_NUMBER, "type_filling": mt5.ORDER_FILLING_IOC}
                res = await run_bg(_send_order_blocking, req)
                if res and res.retcode == mt5.TRADE_RETCODE_DONE:
                    set_status("done", {"success": True})
                else:
                    set_status("error", {"success": False, "message": f"MT5 retcode: {res.retcode if res else 'None'}"})
            else:
                set_status("error", {"success": False, "message": "Trade introuvable"})

        elif cmd_type == "be_trade":
            ticket = int(payload.get("ticket", 0))
            pos = await run_bg(mt5.positions_get, ticket=ticket)
            if pos:
                p = pos[0]
                req = {"action": mt5.TRADE_ACTION_SLTP, "position": ticket, "sl": p.price_open, "tp": p.tp}
                res = await run_bg(_send_order_blocking, req)
                if res and res.retcode == mt5.TRADE_RETCODE_DONE:
                    set_status("done", {"success": True})
                else:
                    set_status("error", {"success": False, "message": f"MT5 retcode: {res.retcode if res else 'None'}"})
            else:
                set_status("error", {"success": False, "message": "Trade introuvable"})

        elif cmd_type == "panic":
            pos_list = await run_bg(mt5.positions_get)
            if pos_list:
                for p in pos_list:
                    if p.magic == MAGIC_NUMBER or p.ticket in MANAGED_MANUAL_TICKETS:
                        otype = mt5.ORDER_TYPE_SELL if p.type == mt5.ORDER_TYPE_BUY else mt5.ORDER_TYPE_BUY
                        req = {"action": mt5.TRADE_ACTION_DEAL, "symbol": p.symbol, "volume": p.volume,
                               "type": otype, "position": p.ticket, "magic": MAGIC_NUMBER,
                               "type_filling": mt5.ORDER_FILLING_IOC}
                        await run_bg(_send_order_blocking, req)
            set_status("done", {"success": True})

        elif cmd_type == "unlock":
            global security_triggered
            security_triggered = False
            set_status("done", {"success": True})

        elif cmd_type == "suivre":
            ticket = int(payload.get("ticket", 0))
            MANAGED_MANUAL_TICKETS.add(ticket)
            await run_bg(save_memory)
            ACTIVE_TRADE_SOURCES[ticket] = "CANAL PERSO"
            await run_bg(save_channel_data)
            set_status("done", {"success": True})

        elif cmd_type == "toggle_mute":
            channel = payload.get("channel", "")
            if channel in MUTED_CHANNELS:
                MUTED_CHANNELS.remove(channel)
            else:
                MUTED_CHANNELS.append(channel)
            await run_bg(save_settings)
            set_status("done", {"success": True})

        elif cmd_type == "update_setting":
            # Réutilise la logique existante via appel direct
            from pydantic import BaseModel as BM
            class _S(BM):
                key: str
                value: Any
            await api_update_setting(_S(key=payload.get("key", ""), value=payload.get("value")))
            set_status("done", {"success": True})

        elif cmd_type == "update_planning":
            class _P(BM):
                day_factors: dict
                prime_hours: list
                off_peak_factor: float
            await api_update_planning(_P(
                day_factors=payload.get("day_factors", {}),
                prime_hours=payload.get("prime_hours", []),
                off_peak_factor=payload.get("off_peak_factor", 0.5),
            ))
            set_status("done", {"success": True})

        elif cmd_type == "request_stats":
            days = payload.get("days", "30")
            if days == "week":
                now = datetime.now()
                monday = now - timedelta(days=now.weekday())
                monday = monday.replace(hour=0, minute=0, second=0, microsecond=0)
                stats = await get_historical_stats(start_date_custom=monday)
            else:
                days_int = 9999 if days == "all" else int(days)
                stats = await get_historical_stats(days=days_int)
            if stats and firebase_sync._firebase_initialized:
                field_key = f"global_period_{days}"
                loop = asyncio.get_event_loop()
                def _push_stats():
                    firebase_sync.get_db().collection("users").document(user_id).set(
                        {field_key: firebase_sync._safe(stats)}, merge=True
                    )
                await loop.run_in_executor(None, _push_stats)
            set_status("done", {"success": True})

        elif cmd_type == "kill":
            set_status("done", {"success": True})
            await mark_bot_offline_and_exit()

        else:
            set_status("error", {"success": False, "message": f"Type de commande inconnu : {cmd_type}"})

    except Exception as e:
        logger.error(f"Erreur exécution commande Firebase [{cmd_type}] : {e}")
        set_status("error", {"success": False, "message": str(e)})


async def _start_firebase_sync(user_id: str) -> None:
    """Démarre le push loop et le listener Firebase pour un user_id donné."""
    firebase_sync.start_command_listener(user_id, handle_firebase_command, asyncio.get_event_loop())
    asyncio.create_task(firebase_sync.firebase_push_loop(user_id, get_current_state_for_firebase, interval_seconds=4))
    asyncio.create_task(_firebase_push_stats_loop(user_id))
    logger.info(f"🔥 Firebase SaaS activé pour user {user_id}.")


async def _firebase_push_stats_loop(user_id: str) -> None:
    """Pousse les stats historiques (30j) vers Firestore toutes les 60 secondes."""
    while True:
        await asyncio.sleep(60)
        try:
            stats_30d = await get_historical_stats(days=30)
            if stats_30d and firebase_sync._firebase_initialized:
                loop = asyncio.get_event_loop()
                from firebase_admin import firestore as fs
                def _push():
                    firebase_sync.get_db().collection("users").document(user_id).set(
                        {"global_period_30": firebase_sync._safe(stats_30d)}, merge=True
                    )
                await loop.run_in_executor(None, _push)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.warning(f"Erreur push stats historiques : {e}")


async def mark_bot_offline_and_exit():
    await firebase_sync.mark_bot_offline(str(TELEGRAM_DESTINATION_ID))
    import os as _os
    _os.exit(0)


async def main():
    logger.info("\n" + "—"*50)
    logger.info("🔄 INITIALISATION DES COMPOSANTS...")
    
    if not mt5.initialize(): 
        logger.critical(f"❌ Erreur Critique : MetaTrader 5 fermé ou inaccessible.")
        return
    logger.info(f"✔️ Terminal MT5 Connecté (Compte: {mt5.account_info().login})")

    global bot_user
    await bot_user.start(bot_token=BOT_TOKEN)
    logger.info("✔️ Interface interactive Telegram chargée.")

    load_memory()
    load_settings()
    load_channel_data()

    if not await init_daily_stats(): 
        logger.error("❌ Erreur de chargement des statistiques.")
        return

    asyncio.create_task(risk_schedule_monitor())
    asyncio.create_task(monitor_positions())
    asyncio.create_task(license_watchdog())
    asyncio.create_task(safety_net_scanner())

    # ─── Firebase : initialisation ───
    if FIREBASE_EMAIL and FIREBASE_PASSWORD:
        if firebase_sync.init_firebase(FIREBASE_EMAIL, FIREBASE_PASSWORD):
            if TELEGRAM_DESTINATION_ID != 0:
                # Client déjà appairé : démarrage immédiat
                await _start_firebase_sync(str(TELEGRAM_DESTINATION_ID))
            else:
                logger.info("🔥 Firebase initialisé. En attente de l'appairage Telegram (/start PIN)...")
        else:
            logger.warning("⚠️ Firebase non disponible (identifiants invalides). Mode local uniquement.")
    else:
        logger.warning("⚠️ FIREBASE_EMAIL/PASSWORD manquants dans settings.json. Firebase désactivé.")

    # ==========================================
    # 🎨 AFFICHAGE PRO POUR L'UTILISATEUR
    # ==========================================
    if TELEGRAM_DESTINATION_ID == 0:
        # On récupère automatiquement le nom du bot du client via son Token !
        try:
            me = await bot_user.get_me()
            bot_username = f"@{me.username}" if me and me.username else "votre bot"
        except:
            bot_username = "le bot que vous avez créé avec BotFather"

        logger.warning("")
        logger.warning("█"*65)
        logger.warning(" 🔐 INITIALISATION : LIAISON TELEGRAM REQUISE ".center(64, "█"))
        logger.warning("█"*65)
        logger.warning("")
        logger.warning(" ✅ Succès : Le programme est connecté à MetaTrader 5.")
        logger.warning(" 👤 Il ne reste plus qu'à vous identifier comme le propriétaire.")
        logger.warning("")
        logger.warning(" 📱 ÉTAPE 1 : Ouvrez Telegram sur votre téléphone ou PC.")
        logger.warning(f" 🔍 ÉTAPE 2 : Cherchez votre bot : {bot_username}")
        logger.warning(" 💬 ÉTAPE 3 : Cliquez sur 'Démarrer' et envoyez cette commande :")
        logger.warning("")
        logger.warning(f"                         /start {SECURITY_PIN}")
        logger.warning("")
        logger.warning(" ⏳ Le système est en pause et attend votre message sur Telegram...")
        logger.warning("█"*65 + "\n")
    else:
        logger.warning("")
        logger.info("═"*50)
        logger.info("✅ SYSTÈME OPÉRATIONNEL ET SÉCURISÉ")
        logger.info("📡 En attente des signaux de trading...")
        logger.info("═"*50 + "\n")
    
    await client.run_until_disconnected()

# ==========================================
# 🚀 LAUNCHER INTÉGRÉ (ANTI-CRASH)
# ==========================================

async def run_bot_safe():
    """Wrapper pour lancer le bot proprement"""
    try:
        await main()
    except Exception as e:
        logger.critical(f"❌ CRASH DÉTECTÉ : {e}")
        # On pourrait ajouter un log d'erreur ici
        raise e # On relance l'erreur pour que le launcher la voit

# ==========================================
# 🐶 LA LOGIQUE DU WATCHDOG (Le Chef)
# ==========================================
def run_watchdog():
    # 🧹 Nettoie la console Windows au tout démarrage
    os.system('cls' if os.name == 'nt' else 'clear')
    
    logger.info("==================================================")
    logger.info("      🚀 DÉMARRAGE DU MOTEUR FTMO BOT 🚀          ")
    logger.info("==================================================")
    logger.info("🛡️ Système de surveillance (Watchdog) activé.")

    # Nettoyage du fichier de vie au démarrage
    if os.path.exists(WATCH_FILE): 
        try: os.remove(WATCH_FILE)
        except: pass

    while True:
        # On prépare la commande pour lancer l'Ouvrier (soi-même avec l'argument --worker)
        if getattr(sys, 'frozen', False):
            # Cas .exe (PyInstaller)
            cmd = [sys.executable, "--worker"]
        else:
            # Cas Script .py
            cmd = [sys.executable, sys.argv[0], "--worker"]

        logger.info("🚀 Démarrage du Worker (Le Bot)...")
        
        # On lance le bot dans une fenêtre séparée ou en arrière-plan
        # creationflags=subprocess.CREATE_NEW_CONSOLE permet d'avoir une fenêtre dédiée (optionnel)
        process = subprocess.Popen(cmd)

        while True:
            time.sleep(5) # Le Chef dort 5 secondes

            # A. Vérifier si l'ouvrier a crashé tout seul (Erreur code)
            if process.poll() is not None:
                logger.warning("⚠️ Le Worker s'est arrêté (Crash). Redémarrage immédiat...")
                break # On sort de la boucle pour relancer

            # B. Vérifier le Freeze (Le fichier alive.txt est-il vieux ?)
            if os.path.exists(WATCH_FILE):
                try:
                    content = open(WATCH_FILE, "r").read().strip()
                    if content:
                        last_beat = float(content)
                        age = time.time() - last_beat
                        
                        # Si le pouls est trop vieux (> 60s)
                        if age > TIMEOUT_SECONDS:
                            logger.critical(f"❄️ ALERTE FREEZE ! (Dernier signe il y a {int(age)}s)")
                            logger.warning("💀 Tueur de processus activé...")
                            
                            process.kill() # On tue l'ouvrier gelé
                            process.wait() # On s'assure qu'il est mort
                            
                            # On supprime le vieux fichier
                            if os.path.exists(WATCH_FILE): os.remove(WATCH_FILE)
                            break # On sort pour relancer un neuf
                except: pass
            
            # Si le fichier n'existe pas encore (démarrage), on attend gentiment.

# ==========================================
# 🚀 POINT D'ENTRÉE UNIQUE
# ==========================================
if __name__ == '__main__':
    # C'est ici que le programme décide qui il est
    
    if "--worker" in sys.argv:
        # 👷 JE SUIS L'OUVRIER (LE BOT)
        # Je fais le travail normal
        
        # 1. Synchronisation Heure
        sync_windows_time()
        
        # 2. Vérification Licence
        if not check_license_online():
            time.sleep(5)
            sys.exit()

        logger.info("🔵 Démarrage du Bot FTMO (Mode Worker)...")
        
        try:
            # On prévient le client pour l'étape du numéro de téléphone
            print("\n" + "═"*60)
            print(" 📱 CONNEXION À VOTRE COMPTE TELEGRAM (TELETHON)")
            print("═"*60)
            print(" Le bot a besoin de lire les canaux VIP auxquels vous êtes abonné.")
            print(" Si c'est votre première connexion, la console va vous demander :")
            print(" 1. Votre numéro de téléphone (avec l'indicatif, ex: +33612345678)")
            print(" 2. Le code à 5 chiffres que Telegram va vous envoyer sur votre app.")
            print(" (Note : Quand vous tapez le code, il s'affiche, c'est normal).")
            print("═"*60 + "\n")
            
            # On démarre le client Telegram (ça va mettre le script en pause pour lui demander son numéro)
            client.start()
            
            # On crée l'Event Loop asynchrone
            loop = asyncio.get_event_loop()
            
            # 1. On lance le bot de trading dans l'Event Loop
            loop.create_task(run_bot_safe())
            
            # 2. On configure Uvicorn (Le serveur web pour FastAPI)
            # host="0.0.0.0" permet d'écouter sur toutes les interfaces réseau
            # port=8000 est le port standard de l'API
            config = uvicorn.Config(app=app, host="0.0.0.0", port=8000, loop="asyncio", access_log=False)
            server = uvicorn.Server(config)
            
            logger.info("🌐 Lancement de l'API Web (FastAPI) sur le port 8000...")
            
            # On lance le serveur web qui va faire tourner la boucle asynchrone indéfiniment
            loop.run_until_complete(server.serve())
            
        except KeyboardInterrupt:
            pass

        except Exception as e:
            logger.critical(f"Crash Fatal Worker : {e}")
            time.sleep(5)
            
    else:
        # 👮 JE SUIS LE CHEF (WATCHDOG)
        # Je lance la surveillance
        try:
            run_watchdog()
        except KeyboardInterrupt:
            logger.info("Arrêt du système.")