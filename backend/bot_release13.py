import asyncio
import math
import re
import time
import random
import secrets
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

from pydantic import BaseModel

from telethon.tl.types import KeyboardButtonWebView

# --- (#30) MODE D'AFFICHAGE : "dev" = logs complets en console (comme avant),
# "client" (défaut) = console épurée (logo + statut), logs complets toujours écrits
# dans kairos.log pour le support. Lu tôt, avant la config du logging ci-dessous.
DISPLAY_MODE = "client"
# (#99) Ajout manuel de resultats aux stats par canal. Reglage SEPARE de DISPLAY_MODE :
# l'affichage de la console et l'acces a cette fonction n'ont rien a voir, et les coupler
# obligeait a subir les logs detailles pour avoir le bouton. Absent du settings.json d'un
# client, donc desactive chez lui. Volontairement lu ici et JAMAIS ecrit par save_settings :
# il ne doit pas pouvoir etre bascule depuis le dashboard.
AJOUT_MANUEL_STATS = False
try:
    with open("settings.json", "r", encoding="utf-8") as _dm_f:
        _cfg_demarrage = json.load(_dm_f)
        DISPLAY_MODE = _cfg_demarrage.get("DISPLAY_MODE", "client")
        AJOUT_MANUEL_STATS = bool(_cfg_demarrage.get("AJOUT_MANUEL_STATS", False))
except Exception:
    pass

# Configuration du Logging (Fichiers max de 5Mo, on en garde 3 au maximum)
log_formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
log_handler = RotatingFileHandler("kairos.log", maxBytes=5*1024*1024, backupCount=3, encoding='utf-8')
log_handler.setFormatter(log_formatter)

logger = logging.getLogger()
logger.setLevel(logging.INFO)
logger.addHandler(log_handler)
logging.getLogger('telethon').setLevel(logging.WARNING)

# En mode "dev" seulement : on affiche aussi les logs bruts dans la console. En mode
# "client", la console reste épurée (logo + statut via client_status()) -- rien n'est
# perdu pour autant, tout continue d'être écrit dans kairos.log.
# (#45) console_handler est toujours créé (objet existe dans les deux modes) mais seulement
# ATTACHÉ en mode dev -- permet à /display_mode de basculer à chaud sans redémarrage.
console_handler = logging.StreamHandler()
console_handler.setFormatter(log_formatter)
if DISPLAY_MODE == "dev":
    logger.addHandler(console_handler)


def client_status(msg):
    """Affiche un message epure dans la console -- uniquement en mode "client" (le mode
    "dev" voit deja l'equivalent, en detaille, via le logger+console_handler ci-dessus)."""
    if DISPLAY_MODE != "dev":
        print(msg)


def resize_console_window():
    """Force une taille de fenêtre console compacte au démarrage (l'exe s'ouvrait sinon
    maximisé/plein écran par défaut). Hauteur calée sur la séquence COMPLÈTE affichée au
    lancement en mode client (déjà appairé), sans qu'aucune ligne ne défile hors champ :
      - print_kairos_logo()                                    : 9 lignes
      - "Licence validée — bienvenue ..." (check_license_online) : 1 ligne
      - "MetaTrader 5 connecté (compte ...)"                     : 1 ligne
      - "Interface Telegram chargée."                            : 1 ligne
      - bandeau final "SYSTÈME OPÉRATIONNEL..." (cf. client_status() dans main())
        : 5 lignes (ligne vide + bordure "═"*50 + 2 lignes de texte + bordure)
      = 17 lignes -> 20 lignes de marge. Largeur (58) laisse un peu d'air à la ligne
      "Licence validée" dont le nom du client peut dépasser les 50 de la bordure.
    `mode con` seul ne suffit pas : il redimensionne le buffer de caractères mais ne sort
    PAS une fenêtre de son état "maximisé" (ex: si Windows Terminal est le terminal par
    défaut, ou si Windows a mémorisé un état maximisé pour cette fenêtre). On force donc
    en plus l'état "restauré" (non maximisé) au niveau Win32 -- c'est LUI, pas mode con,
    qui gère la taille finale : un MoveWindow en pixels avant aurait été écrasé par le
    mode con qui suit."""
    if os.name != 'nt':
        return
    try:
        import ctypes
        SW_RESTORE = 9
        hwnd = ctypes.windll.kernel32.GetConsoleWindow()
        if hwnd:
            ctypes.windll.user32.ShowWindow(hwnd, SW_RESTORE)
    except Exception:
        pass
    try:
        os.system('mode con: cols=58 lines=20')
    except Exception:
        pass

def print_kairos_logo():
    W = 45
    def _l(txt=""):
        print("║" + txt.center(W) + "║")
    print()
    print("╔" + "═" * W + "╗")
    _l()
    _l("K A I R O S")
    _l("─" * 11)
    _l(f"Trading Automation Engine — v{VERSION_BOT}")
    _l()
    print("╚" + "═" * W + "╝")
    print()

async def run_bg(func, *args, **kwargs):
    """Wrapper magique pour rendre MT5 non-bloquant"""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, partial(func, *args, **kwargs))

# (#58) L'API MetaTrader5 n'est pas garantie thread-safe pour des accès concurrents à la
# même connexion terminal. run_bg() envoie chaque appel sur un thread du ThreadPoolExecutor
# par défaut sans aucune coordination : monitor_positions, execute_trade, admin_commands,
# edit_handler et le handler de commandes Firebase peuvent tous taper l'API MT5 au même
# instant depuis des threads différents. run_mt5() sérialise ces appels avec un sémaphore.
MT5_SEMAPHORE = asyncio.Semaphore(1)

async def run_mt5(func, *args, **kwargs):
    """Comme run_bg(), mais sérialise l'accès à l'API MetaTrader5 (#58)."""
    async with MT5_SEMAPHORE:
        return await run_bg(func, *args, **kwargs)

try:
    import numpy
    if DISPLAY_MODE == "dev":
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
from telethon import TelegramClient, events
import subprocess # <--- Pour la commande heure

# --- SÉCURITÉ ---
# Code PIN à 6 chiffres, source cryptographique (secrets), régénéré à chaque démarrage
SECURITY_PIN = str(secrets.randbelow(900000) + 100000)

# ==========================================
# 🔐 CONFIGURATION DISTRIBUTION (LICENCE)
# ==========================================
VERSION_BOT = "15.16.0"
# (#113) Ce que CET exe sait faire, annonce au dashboard dans chaque push d'etat. Le dashboard
# n'affiche une fonction que si le bot la declare : sans ca, un client sur un exe plus ancien
# verrait un bouton que son bot ignore -- ou pire, auquel il repond autre chose sans le dire
# (une periode inconnue retombait en silence sur 30 jours). Une liste plutot qu'une
# comparaison de versions : elle survit a la fusion de deux lignes de developpement, comme
# celle de la V15.13.0 et de la V15.14.0. Un nom publie ne se retire ni ne change de sens.
FONCTIONS_BOT = [
    "periodes_v2",   # (#108) periodes au minuit du client ; "week" accepte par l'historique
    "plage_perso",   # (#107) request_stats days="custom" + debut/fin -> cle stats_plage
    "historique_canal",  # (#109) request_history + canal -> cle trades_history_filtre
    "echelle_tp",    # (#110) update_setting echelle_indodog / echelle_fts (liste de %)
]
# (URL_LICENCE retire — licence verifiee via Firestore, Phase 4)

# ==========================================
# ⚙️ CONFIGURATION GLOBALE
# ==========================================

# --- Telegram API ---
# (Clés lues depuis settings.json — jamais en dur dans le code)
# (ADMIN_SECURITY_TOKEN retire de l'exe — alertes relayees par le VPS, Phase 4)

# --- SÉCURITÉ : Lecture des clés API depuis settings.json ---
API_ID = 0
API_HASH = ""
BOT_TOKEN = ""
USER_LICENSE_KEY = ""
# (#103) MT5_PATH etait livre dans settings.json et documente dans INSTALLATION.txt
# ("le champ MT5_PATH doit pointer vers ton terminal64.exe"), mais AUCUNE ligne du code ne le
# lisait : les appels a mt5.initialize() etaient tous nus. Un client avec plusieurs terminaux
# installes pouvait corriger ce chemin pendant des heures sans le moindre effet. Il est lu ici,
# avec les autres cles, parce que load_settings() ne tourne qu'APRES le test de connexion.
MT5_PATH = ""
ALERTE_CHAT_ID = 0

if os.path.exists("settings.json"):
    try:
        with open("settings.json", "r", encoding="utf-8") as f:
            st_data = json.load(f)
            API_ID = st_data.get("TELEGRAM_API_ID", 0)
            API_HASH = st_data.get("TELEGRAM_API_HASH", "")
            BOT_TOKEN = st_data.get("TELEGRAM_BOT_TOKEN", "")
            USER_LICENSE_KEY = st_data.get("USER_LICENSE_KEY", "")
            MT5_PATH = (st_data.get("MT5_PATH", "") or "").strip()
            # (#106) Destinataire des alertes, connu AVANT la connexion MT5 : ADMIN_ID n'est
            # charge que par load_settings(), qui tourne apres. Sert a prevenir le client
            # quand MetaTrader reste injoignable (cf. connexion_mt5).
            ALERTE_CHAT_ID = int(st_data.get("TELEGRAM_DESTINATION_ID", 0) or 0)
    except Exception as e:
        print(f"Impossible de lire les clés API Telegram : {e}")

# Vérification de sécurité avant de lancer Telegram (Assistant de Première Connexion)
if API_ID == 0 or API_HASH == "" or BOT_TOKEN == "" or API_HASH == "A_REMPLIR_PAR_LE_CLIENT":
    print_kairos_logo()
    print("\n" + "█"*75)
    print(" 🛑 PREMIÈRE CONNEXION : CONFIGURATION REQUISE ".center(73, "█"))
    print("█"*75 + "\n")
    print(" Bienvenue ! Avant de démarrer le bot pour la première fois,")
    print(" vous devez configurer vos accès personnels.\n")
    print(" 👉 ÉTAPE 1 : Ouvrez le fichier 'settings.json' (clic droit > Modifier).")
    print(" 👉 ÉTAPE 2 : Remplissez les 3 lignes suivantes :")
    print("      - TELEGRAM_API_ID    (Vos chiffres, SANS guillemets)")
    print("      - TELEGRAM_API_HASH  (Vos lettres/chiffres, ENTRE guillemets)")
    print("      - TELEGRAM_BOT_TOKEN (Votre token BotFather, ENTRE guillemets)\n")
    print(" 💡 Lisez INSTALLATION.txt (fourni avec le bot) pour savoir comment")
    print(" obtenir ces codes en 2 minutes.\n")
    print(" 👉 ÉTAPE 3 : Sauvegardez le fichier settings.json.")
    print(" 👉 ÉTAPE 4 : Relancez ce programme.\n")
    print("█"*75 + "\n")
    # Le input() est VITAL pour empêcher la fenêtre de se fermer instantanément !
    input(" Appuyez sur ENTRÉE pour fermer cette fenêtre...")
    sys.exit()

# Canal SABR VIP
ID_SABR_VIP = -1003684008703
ID_SABR_FREE = -1003639045748
ID_CANAL_SABR_COPIE = -1003738415220

# Canaux MDT (nommés explicitement : servent au journal de debug MDT ci-dessous)
ID_MDT_VIP = -1002139883659
ID_MDT_FREE = -1001890808461

# (#84) INDODOG -- signaux presentes sous forme de "menu de restaurant" indonesien, mais de
# structure tres reguliere : action + symbole, prix d'entree (parfois une zone), SL, puis de
# 3 a 5 TP numerotes. Gestion en echelle : INDO_ECHELLE_TP donne le % de la position INITIALE
# ferme a chaque palier, et le reliquat est ferme au dernier TP du signal.
ID_INDODOG = -1003720161775
# Le canal est un groupe A SUJETS (comme des salons Discord) : tous les salons partagent le
# MEME identifiant de groupe, seul le numero de sujet les distingue. Sans ce filtre, le bot
# lirait aussi le salon de discussion generale et tenterait d'y trouver des signaux.
INDODOG_TOPIC_ID = 64

# (#90) FTS -- "Forex VIP SpaceFX". Signaux simples et tres reguliers :
#     XAUUSD BUY NOW 4635
#     Sl : 4606  ( 80 pip )
#     Tp : 4620
#     Tp : 4625
#     Tp : 4630
# Prix d'entree UNIQUE (jamais de zone), un SL, puis 3 TP ou plus NON numerotes : c'est leur
# ordre d'apparition qui donne le palier. Meme moteur d'echelle qu'INDODOG (FTS_ECHELLE_TP,
# reliquat ferme au dernier TP) et memes regles d'entree / de peremption.
ID_FTS = -1001957768371

# ⚠️ LISTE DES CANAUX SOURCES (MDT_VIP, MDT_FREE, PERSO, SPACE, LIGUE, SABR_FREE, SABR_VIP, SABR_COPIE, INDODOG, FTS)
CHANNEL_LIST = [ID_MDT_VIP, ID_MDT_FREE, -5093508256, -1002684710444, -1001753570693, -1003639045748, ID_SABR_VIP, ID_CANAL_SABR_COPIE, ID_INDODOG, ID_FTS]

# Ton Groupe de Logs
ADMIN_ID = 0 #-1003692316672
# Groupe gestion utilisation key
# (SECURITY_CHANNEL_ID retire — gere cote VPS)

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

FIXED_LOT_SABR_FREE = 0.06         # Lot TOTAL par défaut pour SABR Free (OR uniquement)
FIXED_LOT_SABR_VIP = 0.06          # Lot TOTAL par défaut pour SABR VIP (OR uniquement)
# (#84) INDODOG : lot fixe, comme SABR. 0.10 par defaut et non 0.06, parce que l'echelle de TP
# retire 20 % de la position INITIALE a chaque palier : sous 0.10, avec un pas de volume de
# 0.01, les arrondis consomment tout le reliquat avant le dernier TP.
FIXED_LOT_INDODOG = 0.10
# Repartition de l'echelle : 10 % au TP1 puis 20 % aux suivants. Le DERNIER TP du signal ferme
# tout ce qui reste, quel que soit le nombre de paliers -- c'est ce qui evite de depasser
# 100 % quand un signal comporte 5 TP.
# Le 10 % du TP1 remplace le 30 % d'origine : indodog_stats.json compte desormais jusqu'ou
# vont reellement les signaux, de quoi regler cette echelle sur des donnees et non au doigt.
# (#110) Reglable par le client (dashboard, /echelle_indodog), un % PAR PALIER : TP1, TP2...
# Le dernier chiffre vaut pour tous les paliers suivants, donc la meme echelle s'adapte a un
# signal de 3 TP comme de 5. Defaut = l'echelle d'avant, a l'identique.
INDO_ECHELLE_TP = [10.0, 20.0]
# (#88) Entree au MARCHE plutot qu'en ordre en attente. Les DEUX conditions sont requises :
#   - moins de INDO_MARKET_MAX_PIPS du prix d'entree annonce  (garde-fou de proximite)
#   - le cours a REELLEMENT touche ce prix dans les INDO_MARKET_LOOKBACK_MIN dernieres minutes
# La proximite seule ne suffit pas : a 45 pips d'une zone jamais atteinte, entrer au marche
# revient a prendre un prix que le canal n'a pas donne. Le contact recent seul ne suffit pas
# non plus : apres un rebond violent, le prix peut avoir touche la zone puis s'en eloigner.
INDO_MARKET_MAX_PIPS = 50.0
INDO_MARKET_LOOKBACK_MIN = 5
# (#89) Peremption d'un ordre INDODOG encore EN ATTENTE. Deux motifs independants :
#   - le prix s'est approche a moins de INDO_APPROACH_PIPS du prix d'entree SANS nous
#     declencher, puis est alle jusqu'au TP2 : le mouvement a eu lieu sans nous, l'ordre
#     n'attend plus qu'un retour qui, s'il vient, se fera a contre-courant.
#   - l'ordre dort depuis plus de INDO_ORDER_EXPIRY_H heures : le signal n'est plus frais.
INDO_APPROACH_PIPS = 30.0
INDO_ORDER_EXPIRY_H = 24.0
# (#104) Repli INDODOG quand le signal n'annonce ni SL ni TP, ou que le format a change et
# qu'on n'a rien su lire. Sans ces valeurs, la position partait SANS protection : le canal a
# deja cesse de numeroter ses TP une fois (V15.7.0), il recommencera.
#   - SL a 100 pips : une position sans stop est le seul risque reellement inacceptable.
#   - echelle de TP a 50/100/150/200 pips, pour que les partiels aient quand meme lieu.
#   - mise a BE des 30 pips, AVANT le premier palier : sur un signal qu'on n'a pas compris,
#     on securise plus tot que d'habitude.
INDO_SL_DEFAUT_PIPS = 100
INDO_TP_DEFAUT_PALIERS = [50, 100, 150, 200]
INDO_BE_DEFAUT_PIPS = 30
# Marqueur pose sur le signal quand ce repli a servi -- il declenche le BE anticipe ci-dessus.
TAG_SIGNAL_DEFAUT = "SIGNAL_DEFAUT"
# (#90) FTS : lot fixe et echelle UNIFORME -- 20 % de la position initiale a chaque palier,
# reliquat ferme au dernier TP. Les regles d'entree au marche et de peremption des ordres en
# attente sont partagees avec INDODOG (constantes INDO_* ci-dessus).
FIXED_LOT_FTS = 0.10
# (#110) Meme format qu'INDO_ECHELLE_TP (un % par palier, le dernier se repete).
FTS_ECHELLE_TP = [20.0]
# (#3) SABR hors-or : sizing par risque % (basé sur le SL du signal), réglable au dashboard.
RISK_PERCENT_SABR_OTHER = 0.006    # 0.6% par défaut pour les paires SABR autres que l'or
# False = répartition manuelle (SABR_ZONE_PCT1/PCT2 ci-dessous, 45%/35%/20% par défaut,
# gros lot proche du marché) ; True = répartition égale sur les 3 ordres de la zone.
# Togglable au dashboard (sous "Lots SABR").
SABR_ZONE_EQUAL_SPLIT = False
# (#102) Bornes de la zone SABR reellement prises. Trois = comportement historique, et c'est
# le defaut : personne ne voit son comportement changer sans l'avoir demande. On peut n'en
# retenir que deux, ou une seule. Les pourcentages de repartition s'adaptent au nombre.
# Propre a SABR : INDODOG et FTS gardent leurs trois ordres.
SABR_ZONE_BORNES = ["debut", "milieu", "fin"]
# Répartition manuelle réglable au dashboard : % de l'ordre 1 (proche/gros lot) et de
# l'ordre 2 (milieu). L'ordre 3 (loin/petit lot) prend toujours le reliquat (1 - PCT1 - PCT2)
# -- jamais stocké directement, pour garantir que la somme fasse toujours 100%.
SABR_ZONE_PCT1 = 0.45
SABR_ZONE_PCT2 = 0.35

MAX_DAILY_DRAWDOWN = 0.040         # Drawdown max journalier (4%)
DRAWDOWN_ACTIVE = True             # (#8) Disjoncteur drawdown ON/OFF (désactivable au dashboard)
MAX_SPREAD_PIPS = 30.0       
MAX_SLIPPAGE = 100
FIXED_LOT_GOLD = 0.06      
FIXED_LOT_OTHER = 0.2       

MUTED_CHANNELS = []
# (#7) Pack du client : liste blanche des canaux VISIBLES (logs + dashboard).
# Vide = tout visible (rétro-compatible). Purement cosmétique : ne touche pas au trading.
ALLOWED_CHANNELS = []
# (#25) Verrouillage RÉEL par pack : canaux absents du pack, calculés automatiquement
# (recompute_pack_lock()) -- jamais modifié à la main, distinct de MUTED_CHANNELS pour
# qu'un /unmute ne puisse jamais lever un verrouillage imposé par le pack.
ALL_PACK_CHANNELS = ["CANAL SABR_VIP", "CANAL SABR_FREE", "CANAL SABR_COPIE",
                     "CANAL MDT_VIP", "CANAL MDT_FREE", "CANAL SPACE", "CANAL LIGUE", "CANAL PERSO",
                     "CANAL INDODOG", "CANAL FTS"]
PACK_LOCKED_CHANNELS = []
# (#26) Pack source de vérité = Firestore (licenses/{key}.allowed_channels), pas le
# settings.json local (sinon un client peut éditer le fichier pour tout débloquer).
# None = licence pas encore vérifiée en ligne -> on retombe sur ALLOWED_CHANNELS local.
SERVER_PACK_CHANNELS = None
# (#47) Nombre d'échecs consécutifs de check_license_online(silent=True) tolérés avant de
# passer en fail-closed. Sans ça, bloquer juste les domaines Firebase (sans couper tout
# Internet) rendait une licence révoquée valide indéfiniment.
LICENSE_CHECK_FAIL_COUNT = 0
LICENSE_CHECK_MAX_FAILS = 2
ID_CANAL_PERSO = -5093508256
CHANNEL_TP1_PCT = {}
SABR_ZONE_MIN_DIST = {} # Mémorise la distance la plus proche atteinte par le marché
SABR_DAILY_SIGNALS = {} # Stocke les signaux du jour {1: data, 2: data...}
SABR_SIGNAL_DAY = -1    # Mémorise le jour pour remettre à zéro à minuit

# (#4) Sentinelle renvoyée par execute_trade quand un signal SABR est bloqué par
# l'anti-doublon : permet de distinguer ce cas des autres return None (blind, drawdown...).
SABR_DUPLICATE = "SABR_DUPLICATE"

PROCESSED_MSG_IDS = set() # Mémorise les IDs des messages déjà traités

CHANNELS_STRAT_2 = ["CANAL MDT_VIP", "CANAL MDT_FREE"]

PENDING_ORDER_TICKETS = set() # Pour suivre les activation des ordres différés
MARKET_CLOSED_SYMBOLS = {}

# --- Configuration Heure ---
BROKER_TIME_OFFSET = 3
# (#108) Ecart REEL heure serveur MT5 - UTC, en secondes, mesure sur les ticks (cf.
# mesurer_decalage_serveur). None tant qu'aucune mesure fiable : on retombe alors sur
# BROKER_TIME_OFFSET, qui reste par ailleurs la reference de la journee du drawdown.
DECALAGE_SERVEUR_S = None
_TICKS_DECALAGE = {}
# (#108) Fuseau du CLIENT, donne par son telephone via le dashboard. tz_min = minutes d'avance
# sur UTC (120 = Paris en ete). None tant que le dashboard ne l'a jamais envoye.
FUSEAU_CLIENT = {"tz_min": None, "tz": ""}

# --- Stratégie de Scaling (NOUVELLE STRATÉGIE) ---
TP1_PIPS = 30
TP2_PIPS =75
TP3_PIPS = 140
TP4_PIPS = 200
# (#101) TP de repli pour un signal MDT qui n'en annonce aucun : sans lui la position partait
# sans cible et ne pouvait sortir que par le SL ou un partiel. Les paliers de prise (30/75/140
# /200 pips) s'appliquent par-dessus, c'est juste un filet.
MDT_TP_DEFAUT_PIPS = 200
TRAILING_STOP_PIPS = 85
SOFT_SL_PERCENT = 0.30
# (#10) Interrupteurs ON/OFF (dashboard) pour la gestion du SL général (hors SABR)
SL_SUIVEUR_ACTIVE = True   # False -> le bot ne déplace plus le SL (BE/trailing/paliers)
SOFT_SL_ACTIVE = True      # False -> pas de clôture partielle sur repli (Soft SL Vente)

# (#72) Décalage du SL lors d'une mise à BE, en pips, réglable au dashboard.
#   0   -> SL exactement au prix d'entrée (comportement historique)
#   > 0 -> SL AU-DELÀ de l'entrée, dans le sens du gain : le trade est déjà gagnant si le SL
#          est touché (couvre le spread et les frais)
#   < 0 -> SL EN DEÇÀ de l'entrée : on tolère une petite perte plutôt que de se faire sortir
#          par une mèche sur le prix d'entrée exact
# Le signe vaut pour les deux sens : sur une VENTE, un décalage positif place le SL SOUS
# l'entrée. On raisonne en gain/perte, pas en haut/bas.
BE_OFFSET_PIPS = 0.0

# (#73) SL suiveur propre aux positions SABR. Elles sont gérées à part (BE +100p et prises
# partielles) et n'ont jamais eu de trailing : SABR_TRAILING_ACTIVE existait dans les fichiers
# de configuration distribués mais n'était lu nulle part.
SABR_TRAILING_ACTIVE = False   # False -> aucun SL suiveur sur SABR (comportement historique)
SABR_TRAILING_PIPS = 100       # distance en pips entre le prix courant et le SL suiveur
# (#93) Deux facons de gerer le "breakeven" SABR a +100 pips :
#   False = BE CLASSIQUE (historique) : le SL remonte au prix d'entree. Le trade ne peut plus
#           perdre, mais un simple retour sur l'entree le sort avant que le mouvement reparte.
#   True  = BE PARTIEL : le SL ne bouge PAS, on laisse le trade respirer avec son stop
#           d'origine. En echange, si le prix revient toucher le prix d'entree, on vend
#           SABR_BE_PARTIEL_PCT du volume INITIAL pour alleger le risque.
# Dans les deux cas, la cloture des 60 % a +100 pips reste inchangee.
# Le pourcentage porte sur le volume ENCORE OUVERT au moment du retour, pas sur le volume
# initial : a ce stade 60 % sont deja partis a +100 pips, et raisonner sur l'initial rendait
# le reglage trompeur (toute valeur >= 40 % soldait la position sans le dire). 100 % = tout
# fermer, 50 % = la moitie de ce qui reste.
SABR_BE_PARTIEL_ACTIVE = False
SABR_BE_PARTIEL_PCT = 0.50
BTC_ENTRY_OFFSET = 70
DEFAULT_TP1_PCT = 0.50

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
# (#106) Reference de minuit PAR COMPTE (cf. save_state_and_stats). ftmo_state.txt reste ecrit
# pour la compatibilite descendante, mais c'est ce fichier-ci qui fait foi.
REFS_FILENAME = "daily_refs.json"
STATS_FILENAME = "daily_stats.json"
HISTORY_FILENAME = "history.csv"
MEMORY_FILENAME = "trades_memory.json"
MANUAL_FILENAME = "manual_trades.json"
SETTINGS_FILENAME = "settings.json"
# (#stats) stats_channels.json n'est plus lu ni écrit : trades_detailed.csv est la source
# unique des stats par canal. Un fichier résiduel sur un poste client est simplement ignoré.
SOURCES_MEMORY_FILE = "active_sources.json"
SIGNALS_MEMORY_FILE = "active_signals.json"
INDO_TP_FILENAME = "indodog_tp.json"
# (#87) Comptage INDODOG : jusqu'ou va REELLEMENT chaque signal (SL, TP1, TP2... TP final).
# Sert a regler INDO_ECHELLE_TP sur des donnees. Un signal = une ligne, jamais
# une par ordre : une entree en zone en pose trois, les compter separement tripleraient tout.
INDO_STATS_FILENAME = "indodog_stats.json"
FTS_STATS_FILENAME = "fts_stats.json"
DETAILED_HISTORY_FILE = "trades_detailed.csv"
# (#stats) Historique agrégé repris d'avant la refonte des stats. Optionnel : absent chez un
# nouveau client, il ne fait qu'ajouter un total aux périodes qui couvrent sa plage de dates.
STATS_SEED_FILE = "stats_seed.json"
# (#98) Ajouts manuels aux stats par canal : trades rates pendant une coupure, resultats
# rattrapes a la main... Une LIGNE DATEE par ajout, jamais videe. La date suffit a faire
# sortir la ligne de "24h" le lendemain tout en la gardant dans "1S" et "Max" -- pas de
# fichier a remettre a zero, donc pas de vidage qui peut echouer ou se rejouer.
# Active par AJOUT_MANUEL_STATS dans settings.json : le bot d'un client ignore ce fichier.
MANUAL_STATS_FILE = "stats_manuel.json"
# (#91) Remise a zero des stats par canal. On n'efface RIEN : trades_detailed.csv sert aussi
# a l'historique des trades et a la courbe du portefeuille, y supprimer des lignes detruirait
# ces deux-la. On memorise juste une date de coupure par canal, et les stats ignorent ce qui
# est anterieur. Reversible : retirer la date fait tout revenir.
STATS_RESET_FILE = "stats_reset.json"
# (#108) Fuseau horaire du client, appris du dashboard (cf. noter_fuseau_client).
FUSEAU_FILENAME = "fuseau_client.json"
# (#109) Dernier historique filtre par canal demande par le dashboard ({period, canal, t}) :
# la boucle de stats le rafraichit comme elle rafraichit "Jour", tant qu'il a moins d'une heure.
# Un dict modifie sur place, jamais reaffecte : pas de `global` a oublier.
DERNIER_HISTORIQUE_FILTRE = {}

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

# Format 3 (nouveau format MDT_FREE, 2026-07) : "XAUUSD SELL-LIMIT À 4055" / "XAUUSD BUY À 3997"
# -- symbole d'abord comme le Format 1, mais prix introduit par À/@/AT comme le Format 2.
REGEX_F3_ENTRY = re.compile(r"#?([A-Z0-9]{3,10})\s+(ACHAT|VENTE|BUY|SELL|ACH[EÈ]TEZ?|VENDS?|VENDEZ)[^\n]*?(?:À|@|AT)\s*([\d\.,]+)", re.IGNORECASE | re.MULTILINE)

# SL et TP (Universel)
REGEX_SL = re.compile(r"(?:SL|STOP)\s*[:=]?\s*([\d\.,]+)", re.IGNORECASE)
REGEX_TP = re.compile(r"(?:TP|TARGET)\s*[:=]?\s*([\d\.,]+)", re.IGNORECASE)

# --- REGEX INDODOG (#84) ---
# Le canal presente ses signaux comme un menu de restaurant indonesien. Les libelles changent
# d'un plat a l'autre ("Lauk 1", "Lauk Spesial", "Paket Sultan", "Menu Sultan"...) : on
# s'accroche donc au marqueur "(TPn)" en fin de ligne, qui lui ne bouge jamais, plutot qu'aux
# noms de plats.
REGEX_INDO_ACTION = re.compile(
    r"Menu\s+Utama\s*:?\s*(BUY|SELL|ACHAT|VENTE)(?:\s+NOW)?\s+#?([A-Z0-9]{3,10})", re.IGNORECASE)
# "Harga Makan : 4585" ou une ZONE "4516 - 4510" (le canal ecrit un tiret long)
REGEX_INDO_ENTRY = re.compile(
    r"Harga\s+Makan\s*:?\s*([\d.,]+)(?:\s*[-‐-―]\s*([\d.,]+))?", re.IGNORECASE)
REGEX_INDO_SL = re.compile(r"Warteg\s+Tutup\s*:?\s*([\d.,]+)", re.IGNORECASE)
REGEX_INDO_TP = re.compile(r":?\s*([\d.,]+)\s*\(\s*TP\s*(\d+)\s*\)", re.IGNORECASE)

# --- REGEX FTS (#90) ---
# "XAUUSD BUY NOW 4635", parfois suivi d'un emoji. Le symbole precede l'action.
REGEX_FTS_ACTION = re.compile(
    r"#?([A-Za-z0-9]{3,12})\s+(BUY|SELL)\s*(?:NOW)?\s*[:=]?\s*([\d]+(?:[.,]\d+)?)", re.IGNORECASE)
# "Sl : 4606  ( 80 pip )" -- le nombre doit suivre IMMEDIATEMENT le mot SL. Sans cette
# contrainte, la taille du stop entre parentheses serait lue comme un second prix, et la
# vieille regle "garder la derniere valeur" aurait pose un SL a 80 sur un or a 4606 (#68).
REGEX_FTS_SL = re.compile(r"\bSL\s*[:=]?\s*([\d]+(?:[.,]\d+)?)", re.IGNORECASE)
# "Tp : 4620" repete, sans numero.
REGEX_FTS_TP = re.compile(r"\bTP\s*\d*\s*[:=]?\s*([\d]+(?:[.,]\d+)?)", re.IGNORECASE)


def parse_fts_tps(raw_text):
    """(#90) TP d'un signal FTS, dans leur ORDRE D'APPARITION.

    Le canal ne les numerote pas : trois lignes "Tp : ..." de suite. C'est la position dans
    le message qui donne le palier. On ne trie donc surtout pas par valeur -- sur un SELL,
    les TP sont decroissants (4610, 4605, 4600) et un tri croissant inverserait l'echelle.
    """
    out = []
    for m in REGEX_FTS_TP.finditer(raw_text or ""):
        try:
            v = float(m.group(1).replace(",", "."))
        except Exception:
            continue
        if v > 0 and v not in out:
            out.append(v)
    return out


# ==========================================
# 🪜 CANAUX A ECHELLE DE TP (#90)
# ==========================================
# INDODOG et FTS partagent le meme moteur : lot fixe, entree au marche sous conditions,
# partiels par palier sur le volume INITIAL du signal, peremption des ordres en attente.
# Seuls changent les pourcentages, le lot et la mise a BE. Tout le reste est mutualise --
# dupliquer ce moteur serait la garantie de ne corriger un bug que d'un cote.

def is_ladder_channel(src):
    """True si ce canal se gere en echelle de TP par signal."""
    s = src or ""
    return ("INDODOG" in s) or ("FTS" in s)


def ladder_echelle(src):
    """(#110) Echelle de TP en vigueur pour ce canal : % du volume INITIAL, palier par palier."""
    return list(FTS_ECHELLE_TP if "FTS" in (src or "") else INDO_ECHELLE_TP)


def ladder_pct(echelle, idx):
    """(#110) Fraction du volume initial a fermer au palier `idx` (1 = TP1).

    Au-dela de la liste, son dernier chiffre se repete : « 10 20 » = 10 % au TP1 puis 20 % a
    chaque palier suivant, quel que soit le nombre de TP du signal. Le dernier TP, lui, n'est
    jamais lu ici : il ferme le reliquat (TP de la position dans MT5)."""
    if not echelle:
        return 0.0
    return float(echelle[min(idx, len(echelle)) - 1]) / 100.0


def lire_echelle(valeur):
    """(#110) Valide une echelle saisie : liste de nombres, ou texte « 10 20 » / « 10, 20 ».

    Retourne (liste, None) ou (None, message d'erreur). La virgule SEPARE les paliers (« 10, 20 »
    est la saisie naturelle) : une decimale s'ecrit avec un point. De 1 a 8 paliers, chacun
    strictement positif et au plus 100 %.
    """
    if isinstance(valeur, (list, tuple)):
        brut = list(valeur)
    else:
        brut = [x for x in re.split(r"[\s,;/]+", str(valeur or "").strip()) if x]
    if not brut:
        return None, "Échelle vide : donne au moins un pourcentage, ex. « 10 20 »."
    if len(brut) > 8:
        return None, "Huit paliers au plus."
    try:
        vals = [round(float(str(x).replace("%", "").strip()), 1) for x in brut]
    except (TypeError, ValueError):
        return None, "Valeurs illisibles : donne des pourcentages, ex. « 10 20 »."
    if any(not (0 < v <= 100) for v in vals):
        return None, "Chaque palier doit être compris entre 1 et 100 %."
    return vals, None


def _fmt_pct(p):
    return ("%g" % round(p, 1)).replace(".", ",") + " %"


def decrire_echelle(echelle, n_tp):
    """(#110) « TP1 10 % · TP2 20 % · TP3 le reste (70 %) » pour un signal a n_tp TP."""
    parts, cumul = [], 0.0
    for i in range(1, n_tp):
        part = min(ladder_pct(echelle, i) * 100, max(0.0, 100 - cumul))
        cumul += part
        parts.append(f"TP{i} {_fmt_pct(part)}")
    parts.append(f"TP{n_tp} le reste ({_fmt_pct(max(0.0, 100 - cumul))})")
    return " · ".join(parts)


def alerte_echelle(echelle, n_max=5):
    """(#110) Message si l'echelle ferme TOUT avant le dernier TP d'un signal a n_max TP.

    Accepte quand meme (« 50 50 » = moitie au TP1, le reste au TP2, peut etre voulu), mais
    dit : sur un signal plus long, les TP suivants ne servent plus a rien. n_max = 5, le
    maximum annonce par INDODOG : au-dela, meme l'echelle par defaut (10 puis 20) a tout
    ferme au TP6, ce qui etait deja le cas avant qu'elle soit reglable.
    """
    cumul = 0.0
    for i in range(1, n_max):
        cumul += ladder_pct(echelle, i)
        if cumul >= 0.999:
            return (f"⚠️ Tout est fermé dès le TP{i} : sur un signal qui annonce plus de "
                    f"{i} TP, les suivants ne servent plus.")
    return None


def regler_echelle(src, valeur):
    """(#110) Change l'echelle d'un canal. -> (echelle, None) ou (None, erreur).

    Les signaux deja poses gardent l'echelle de leur ouverture (INDO_ECHELLE_BY_SIGNAL) :
    changer l'echelle en plein trade melangerait deux reglages sur un meme signal. A
    l'appelant d'enregistrer (save_settings).
    """
    global INDO_ECHELLE_TP, FTS_ECHELLE_TP
    vals, err = lire_echelle(valeur)
    if err:
        return None, err
    if "FTS" in (src or ""):
        FTS_ECHELLE_TP = vals
    else:
        INDO_ECHELLE_TP = vals
    return vals, None


def ladder_fixed_lot(src):
    """Lot fixe du canal, avant multiplicateur de reactivation."""
    return FIXED_LOT_FTS if "FTS" in (src or "") else FIXED_LOT_INDODOG


def ladder_be_at_tp1(src):
    """Mise a BE au TP1. Vrai pour INDODOG seulement : c'est SON canal qui la recommande
    dans chaque message. FTS n'en parle pas, on ne l'impose donc pas d'office."""
    return "INDODOG" in (src or "")


# (#92) Libelles qui ne sont PAS des paliers. Tout le reste du "menu" en est un.
# Raisonner par EXCLUSION et non par inclusion : les noms de plats changent au gre de
# l'humeur du canal ("Lauk Spesial", "Paket Sultan", "Menu Sultan"...), les trois lignes
# structurantes, elles, sont stables.
INDO_LIGNES_NON_TP = ("HARGA MAKAN", "WARTEG TUTUP", "MENU UTAMA", "MENU HARI INI")


def parse_indodog_tps(raw_text):
    """(#84/#92) Renvoie les TP du signal, dans l'ordre de l'echelle.

    Deux formats coexistent, le canal ayant cesse de numeroter ses paliers en cours de route :

      A. "Lauk 1 : 4665 (TP1)"  -> l'index ecrit fait foi, on trie dessus.
      B. "Lauk 1 : 4668"        -> plus aucun marqueur : c'est l'ORDRE D'APPARITION des
                                   lignes qui donne l'echelle.

    Le format B est apparu sans prevenir et a coute un signal entier : aucun TP lu, donc
    aucun partiel pris ET un TP a 0 envoye a MT5. On ne s'ancre donc plus sur "(TPn)".

    En B, tout "libelle : nombre" est un palier SAUF les trois lignes structurantes. Le
    nombre doit suivre IMMEDIATEMENT le deux-points (sinon "( 80 pip )" et consorts
    passeraient pour des prix), et rester dans un ordre de grandeur plausible face au SL --
    ce qui elimine les pourcentages et autres nombres glisses dans la prose.
    """
    # --- Format A : les index ecrits font foi
    out = {}
    for m in REGEX_INDO_TP.finditer(raw_text or ""):
        try:
            idx = int(m.group(2))
            val = float(m.group(1).replace(",", "."))
            if val > 0:
                out[idx] = val
        except Exception:
            continue
    if len(out) >= 2:
        return [out[k] for k in sorted(out)]

    # --- Format B : ordre d'apparition
    ref = None
    m_sl = REGEX_INDO_SL.search(raw_text or "")
    if m_sl:
        try:
            ref = float(m_sl.group(1).replace(",", "."))
        except Exception:
            ref = None

    tps = []
    for ligne in (raw_text or "").splitlines():
        if ":" not in ligne:
            continue
        libelle, _, valeur = ligne.partition(":")
        if any(mot in libelle.upper() for mot in INDO_LIGNES_NON_TP):
            continue
        m = re.match(r"\s*([\d]+(?:[.,]\d+)?)", valeur)
        if not m:
            continue
        try:
            v = float(m.group(1).replace(",", "."))
        except Exception:
            continue
        if v <= 0 or v in tps:
            continue
        if ref and not (ref * 0.5 <= v <= ref * 2.0):
            continue  # hors de portee du SL : ce n'est pas un prix de ce signal
        tps.append(v)
    return tps


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
# (#106) Compte MT5 auquel se rapporte daily_start_balance. Il n'etait lu qu'au demarrage, pour
# l'afficher : un changement de compte en cours de route passait totalement inapercu.
CURRENT_ACCOUNT_LOGIN = 0
# (#106) {"date": "AAAA-MM-JJ", "comptes": {"<login>": {"ref": float, "verrou": bool}}}
DAILY_REFS = {"date": "", "comptes": {}}
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
# (#103) Alerte de fermeture d'urgence : envoyée UNE fois par épisode de verrou, pas à chaque
# tour de boucle. Remis à False partout où security_triggered retombe (déverrouillage manuel,
# changement de jour) -- sinon un client dont aucune position n'est gérée par le bot recevait
# le même message toutes les 60 s indéfiniment, la boucle ne pouvant jamais vider pos_panic.
DD_PANIC_NOTIFIED = False
PENDING_MESSAGES = {} # Key: ID, Value: Texte du message
# --- Mémoire pour les canaux en 2 temps (Ligue) ---
# Stocke : { "channel_id": { "symbol": "BTCUSD", "action": "ACHAT", "price": 88000, "timestamp": 123456 } }
LIGUE_CONTEXT = {}
# Stocke le contexte SABR : { "last_action": "SELL", "time": 123456789, "status": "WAITING_UPDATE" }
SABR_CONTEXT = {}
# (#stats) Il n'y a plus de compteur cumulatif en mémoire : trades_detailed.csv est la SEULE
# source des stats par canal. Le compteur ne portait aucune date, donc il ne pouvait pas
# répondre à "les 30 derniers jours" -- il fallait de toute façon relire le CSV pour ça, et
# faire vivre les deux en parallèle les faisait diverger. "Max" remplace l'ancien "lifetime".
# Stocke : { ticket_id: "CANAL VIP" }
ACTIVE_TRADE_SOURCES = {}
# (#stats) Stocke : { ticket_id: "signal_id" } -- un appel à execute_trade() = un signal, même
# s'il produit plusieurs ordres (zone SABR). Permet de compter 1 trade et non 3.
ACTIVE_TRADE_SIGNALS = {}
# Format : { ticket_id: montant_deja_compte }
PARTIAL_PROFITS = {}
# (#22) Prix TP1 réel lu dans le signal MDT_FREE (ticket_id: prix) -- permet de fermer
# la partielle exactement au TP1 annoncé par l'admin plutôt qu'à un seuil de pips fixe.
TP1_PRICE_BY_TICKET = {}
# (#84) Echelle de TP d'un trade INDODOG : { ticket: [tp1, tp2, ...] }, dans l'ordre du signal.
INDO_TP_BY_TICKET = {}
# (#84) Paliers deja pris et volume de reference, indexes par SIGNAL : une zone pose plusieurs
# ordres, mais l'echelle de TP est commune a tout le signal.
INDO_LEVELS_BY_SIGNAL = {}
INDO_BASE_VOL_BY_SIGNAL = {}
# (#110) Echelle de TP FIGEE a la pose du signal : { signal: [10.0, 20.0] }. Sans elle, un
# changement de reglage s'appliquerait aussi aux paliers restants des signaux en cours, et un
# meme signal melangerait deux echelles. Persistee avec les paliers (INDO_TP_FILENAME).
INDO_ECHELLE_BY_SIGNAL = {}
# (#87/#90) Comptage par signal, un dictionnaire par canal a echelle :
# { "CANAL INDODOG": { signal_id: {...} }, "CANAL FTS": {...} }.
LADDER_STATS = {"CANAL INDODOG": {}, "CANAL FTS": {}}
# (#89) Tickets d'ordres INDODOG en attente ayant deja vu le prix passer a moins de
# INDO_APPROACH_PIPS. C'est un fait HISTORIQUE : il ne se deduit pas du prix courant, donc il
# doit etre memorise au moment ou on l'observe, et survivre a un redemarrage.
INDO_APPROACHED = set()

CACHE_SYMBOL_POINT = {}

INITIAL_VOLUMES = {} # Cache pour éviter de spammer l'historique MT5

DASHBOARD_MSG_ID = None

# --- Ancien serveur FastAPI/Web (port 8000) retire en Phase 3 — le dashboard passe par Firebase ---

# 🛡️ GLOBAL SESSION POUR EVITER LE SEMAPHORE TIMEOUT
http_session = None

# (#79) Reconnexion durcie. Les logs montrent 272 "MESSAGE RATÉ ATTRAPÉ (coupure réseau)" en
# un mois, soit 10 à 20 par jour : la connexion Telegram tombe souvent. Rien n'est perdu (le
# filet de sécurité rattrape), mais il ne balaie que toutes les 15 s, donc un signal peut
# partir avec autant de retard -- ce qui se paie en slippage sur l'or.
# Par défaut Telethon abandonne après 5 tentatives de reconnexion ; sur une liaison instable,
# le client peut alors rester déconnecté jusqu'au prochain événement. On lui laisse plus de
# marge et un délai un peu plus long entre deux essais.
# À noter : ceci limite les dégâts, la cause reste la qualité réseau du VPS.
_TELETHON_KW = dict(connection_retries=10, retry_delay=2, timeout=20,
                    request_retries=10, auto_reconnect=True)
client = TelegramClient('ftmo_bot_session', API_ID, API_HASH, **_TELETHON_KW)
bot_user = TelegramClient('bot_session', API_ID, API_HASH, **_TELETHON_KW)

# URL du dashboard web (une seule source : bouton du message d'appairage + bouton de menu
# permanent configuré au démarrage, cf. setup_bot_menu_button).
DASHBOARD_URL = "https://trading-dashboard-three-beige.vercel.app"


def setup_bot_menu_button():
    """Configure le bouton de menu permanent du bot client (celui à côté du champ de saisie).

    Ce réglage appartient au BOT, pas au code : il devait être fait à la main dans BotFather
    pour chaque bot, donc aucun client ne l'avait jamais (chacun crée le sien via /newbot).
    On le pose nous-mêmes au démarrage via l'API Bot -- un bot a le droit de configurer son
    propre menu. Sans chat_id, c'est le bouton par défaut pour toutes les conversations.
    Purement cosmétique : un échec (réseau, token invalide) ne doit jamais bloquer le bot."""
    if not BOT_TOKEN:
        return
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/setChatMenuButton",
            json={"menu_button": {"type": "web_app", "text": "Dashboard",
                                  "web_app": {"url": DASHBOARD_URL}}},
            timeout=10,
        )
        if r.ok and r.json().get("ok"):
            logger.info("✔️ Bouton Dashboard configuré sur le bot Telegram.")
        else:
            logger.warning(f"⚠️ Bouton Dashboard non configuré : {r.text[:200]}")
    except Exception as e:
        logger.warning(f"⚠️ Bouton Dashboard non configuré : {e}")

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
    """Le bot signale qu'il est vivant.

    (#103) Ecriture ATOMIQUE. `open(WATCH_FILE, "w")` tronque le fichier AVANT d'ecrire : une
    lecture du watchdog tombant dans cet intervalle renvoie une valeur TRONQUEE -- "1757260"
    au lieu de "1757260000.12" -- soit un horodatage de 1970, donc un age enorme, donc un
    process.kill() sur un worker qui va parfaitement bien.

    Ni le `if content:` ni le `except: pass` du watchdog ne rattrapent ce cas : une troncature
    parse tres bien en float. La course existait deja (pouls toutes les 10 s, relecture toutes
    les 5 s) ; la boucle de reprise MT5 ecrivant plus souvent, autant la fermer pour de bon.
    os.replace() est atomique sous Windows comme sous POSIX.
    """
    try:
        tmp = WATCH_FILE + ".tmp"
        with open(tmp, "w") as f:
            f.write(str(time.time()))
        os.replace(tmp, WATCH_FILE)
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
    """Empreinte machine stable basée sur le MachineGuid Windows (repli : adresse MAC)."""
    import hashlib as _hl
    src = None
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Cryptography") as _k:
            src, _ = winreg.QueryValueEx(_k, "MachineGuid")
    except Exception:
        src = None
    if not src:
        try:
            src = str(uuid.getnode())
        except Exception:
            return "UNKNOWN_ID"
    return _hl.sha256(str(src).encode()).hexdigest()[:32].upper()


def check_license_online(silent=False):
    """Vérifie la licence via Firestore (statut, version, HWID, expiration).

    Les alertes (connexion / vol / expiration) sont écrites dans Firestore et relayées
    sur Telegram par le bot de licence (VPS) — aucun token Telegram n'est embarqué dans l'exe.
    """
    global ALLOWED_CHANNELS, SERVER_PACK_CHANNELS, LICENSE_CHECK_FAIL_COUNT

    if not silent:
        logger.info("🌍 Vérification de la licence en cours...")

    # 1. Clé + identifiants Firebase depuis settings.json
    my_key = fb_email = fb_password = ""
    if os.path.exists("settings.json"):
        try:
            with open("settings.json", "r", encoding="utf-8") as f:
                data = json.load(f)
                my_key = data.get("USER_LICENSE_KEY", "").strip()
                fb_email = data.get("FIREBASE_EMAIL", "").strip()
                fb_password = data.get("FIREBASE_PASSWORD", "").strip()
        except Exception:
            pass

    if not my_key:
        if not silent: logger.error("❌ ERREUR : aucune clé de licence dans settings.json")
        return False
    if not fb_email or not fb_password:
        if not silent: logger.error("❌ ERREUR : identifiants Firebase manquants dans settings.json")
        return False

    # 2. Authentification Firebase (échec = compte désactivé = licence bannie)
    if not firebase_sync._firebase_initialized:
        if not firebase_sync.init_firebase(fb_email, fb_password):
            if not silent:
                logger.error("⛔ ACCÈS REFUSÉ : authentification Firebase échouée (licence bannie ou identifiants invalides).")
            return False

    # 3. Lecture de la licence dans Firestore
    lic = firebase_sync.get_license(my_key)
    if lic is None:
        if silent:
            # (#47) Tolère un incident réseau ponctuel, mais fail-closed après
            # LICENSE_CHECK_MAX_FAILS échecs consécutifs -- sinon bloquer juste les domaines
            # Firebase (sans couper tout Internet) rendait une licence révoquée valide
            # indéfiniment tant que Firestore restait injoignable.
            LICENSE_CHECK_FAIL_COUNT += 1
            if LICENSE_CHECK_FAIL_COUNT <= LICENSE_CHECK_MAX_FAILS:
                logger.warning(
                    f"⚠️ Licence injoignable ({LICENSE_CHECK_FAIL_COUNT}/{LICENSE_CHECK_MAX_FAILS} — tolérance incident réseau).")
                return True
            logger.critical("⛔ Licence injoignable depuis trop longtemps — passage en fail-closed.")
            return False
        logger.error(f"❌ Licence introuvable ou serveur injoignable : {my_key}")
        return False

    # Firestore a bien répondu : on réinitialise le compteur d'échecs réseau.
    LICENSE_CHECK_FAIL_COUNT = 0

    user_name       = lic.get("user", "Inconnu") or "Inconnu"
    status          = lic.get("status", "")
    ver_min         = str(lic.get("ver_min", "") or "")
    registered_hwid = str(lic.get("hwid", "") or "").strip()
    date_fin_str    = str(lic.get("date_fin", "") or "").strip()
    current_pc_id   = get_pc_fingerprint()

    # --- A. Statut (BAN / EXPIRED / ...) ---
    if status != "ACTIVE":
        if not silent: logger.error(f"⛔ ACCÈS REFUSÉ. Statut : {status}")
        return False

    # --- B. Version minimale ---
    if not silent and ver_min and is_version_older(VERSION_BOT, ver_min):
        logger.error("\n" + "=" * 40)
        logger.error("⛔ VERSION OBSOLÈTE DÉTECTÉE")
        logger.error(f"📉 Votre version   : {VERSION_BOT}")
        logger.error(f"📈 Version requise : {ver_min}")
        logger.error("=" * 40)
        logger.error("💡 Une mise à jour est disponible sur Telegram.")
        logger.error("=" * 40 + "\n")
        return False

    # --- C. Expiration ---
    if date_fin_str and date_fin_str.lower() not in ["unlimited", "", "vie"]:
        try:
            exp_date = datetime.strptime(date_fin_str, "%d/%m/%Y") + timedelta(days=1)
            if datetime.now() > exp_date:
                firebase_sync.write_security_event(
                    "expiry", my_key, f"⌛ Licence expirée le {date_fin_str}", {"user": user_name})
                if not silent: logger.error(f"⛔ LICENCE EXPIRÉE LE {date_fin_str}")
                return False
        except Exception:
            pass

    # --- D-bis. (#26) Pack de canaux : Firestore fait autorité, jamais le settings.json local ---
    srv_pack = lic.get("allowed_channels")
    if srv_pack is not None:
        SERVER_PACK_CHANNELS = list(srv_pack)
        ALLOWED_CHANNELS = list(srv_pack)
        recompute_pack_lock()

    # --- D. HWID ---
    if registered_hwid == "":
        # Première inscription de cette machine
        firebase_sync.register_hwid(my_key, current_pc_id)
        if not silent: logger.info("💾 HWID enregistré pour cette machine.")
    elif registered_hwid != current_pc_id:
        firebase_sync.write_security_event(
            "theft", my_key,
            f"🚨 Tentative sur une autre machine — clé `{my_key}` ({user_name})",
            {"expected_hwid": registered_hwid, "got_hwid": current_pc_id})
        if not silent:
            logger.error("⛔ ERREUR : licence liée à un autre ordinateur.")
        return False

    # --- E. Mouchard de connexion (succès, au démarrage uniquement) ---
    if not silent:
        try:
            ip_public = requests.get("https://api.ipify.org", timeout=5).text
        except Exception:
            ip_public = "?"
        firebase_sync.write_security_event(
            "connection", my_key, f"🕵️ Connexion réussie — {user_name}",
            {"user": user_name, "pc_id": current_pc_id, "ip": ip_public})
        logger.info(f"✅ Licence Validée. Bienvenue {user_name} !")
        client_status(f"✅ Licence validée — bienvenue {user_name} !")

    return True

    
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

def compute_be_price(entry_price, is_buy, symbol):
    """(#72) Prix de mise à BE, décalé de BE_OFFSET_PIPS.

    Le décalage est exprimé en GAIN, pas en direction : un décalage positif éloigne le SL de
    l'entrée du côté du profit, quel que soit le sens du trade. Sur un ACHAT il monte le SL,
    sur une VENTE il le descend. Un décalage négatif fait l'inverse et tolère une petite perte,
    ce qui évite de se faire sortir par une mèche pile sur le prix d'entrée.
    """
    try:
        if not BE_OFFSET_PIPS:
            return entry_price
        pt = get_cached_point(symbol)
        div = get_pip_divisor(symbol)
        offset = BE_OFFSET_PIPS * pt * div
        return entry_price + offset if is_buy else entry_price - offset
    except Exception:
        return entry_price


def smart_adjust_price(ref_price_str, partial_price_str):
    """
    Reconstruit un prix partiel basé sur un prix de référence.
    Ex: Ref=5090, Part=10 -> Renvoie 5110 (et non 5010 ou 10)
    """
    try:
        ref = float(ref_price_str)
        part_str = str(partial_price_str).strip()
        part = float(part_str)

        # Si le prix partiel contient une virgule/point, c'est souvent un prix complet (ex: 1.05)
        if "." in part_str: return part_str

        # (#70) L'abrégé est un SUFFIXE du prix de référence : on s'aligne sur le NOMBRE DE
        # CHIFFRES écrits, pas sur une centaine codée en dur. L'ancienne version raisonnait
        # toujours en centaines, ce qui marchait pour "4060-40" (2 chiffres) mais se trompait
        # dès 3 chiffres : "4395-400" testait 4700/4800/4600 et retenait 4600, alors que le
        # trader voulait 4400. Observé en production le 2026-08-14 (signal SABR VIP).
        int_ref = str(abs(int(ref)))
        int_part = part_str.lstrip("+-")
        # Autant (ou plus) de chiffres que la référence -> c'est déjà un prix complet
        if len(int_part) >= len(int_ref):
            return part_str

        step = 10 ** len(int_part)          # 2 chiffres -> 100, 3 chiffres -> 1000
        base = (int(ref) // step) * step    # on remplace les N derniers chiffres du prix de réf
        candidates = [base + part, base + part + step, base + part - step]
        best_price = min(candidates, key=lambda x: abs(x - ref))

        # Sécurité : un écart supérieur à la moitié du pas signifierait qu'aucun candidat ne
        # colle vraiment -- on préfère alors ne rien inventer.
        if abs(best_price - ref) > step / 2:
            return part_str

        return str(float(best_price))

    except:
        return partial_price_str

def parse_signal(raw_text, channel_id=None):
    text = clean_text(raw_text)

    # ==========================================
    # 🧠 PARTIE 0 : MODULE INDODOG (#84)
    # ==========================================
    # Branche fermee : elle retourne TOUJOURS, meme en cas d'echec. Un canal dedie ne doit
    # jamais retomber sur les formats generiques MDT -- c'est exactement ce qui faisait lire
    # "SUR" ou "4392" comme un symbole et perdait de vrais signaux (#77).
    if channel_id == ID_INDODOG:
        m_act = REGEX_INDO_ACTION.search(raw_text or "")
        if not m_act:
            return None, None, None, None, None, None

        act_raw = m_act.group(1).upper()
        action = "ACHAT" if act_raw in ("BUY", "ACHAT") else "VENTE"
        rsym = m_act.group(2).replace("#", "").upper()

        m_sl = REGEX_INDO_SL.search(raw_text)
        sl = m_sl.group(1).replace(",", ".") if m_sl else "0.0"

        price = None
        m_e = REGEX_INDO_ENTRY.search(raw_text)
        if m_e:
            p_a = m_e.group(1).replace(",", ".")
            p_b = (m_e.group(2) or "").replace(",", ".")
            if p_b:
                # Entree donnee en ZONE ("4516 - 4510") : meme traitement que SABR, on renvoie
                # les DEUX bornes separees par "|". execute_trade se charge de repartir le lot
                # sur trois ordres etages dans la zone.
                price = "%s|%s" % (p_a, p_b)
            else:
                price = p_a

        # Le TP envoye a MT5 est le DERNIER palier du signal : c'est le seul qui doit fermer
        # la position dans son entier. Les paliers intermediaires sont geres en partiels par
        # monitor_positions, a partir de INDO_TP_BY_TICKET.
        tps = parse_indodog_tps(raw_text)
        tp = str(tps[-1]) if tps else "0.0"

        return rsym, action, price, sl, tp, "CANAL INDODOG"

    # ==========================================
    # 🚀 CANAL FTS (#90)
    # ==========================================
    # Branche fermee elle aussi : un canal dedie ne retombe jamais sur les formats generiques.
    if channel_id == ID_FTS:
        m_act = REGEX_FTS_ACTION.search(raw_text or "")
        if not m_act:
            return None, None, None, None, None, None

        rsym = m_act.group(1).replace("#", "").upper()
        action = "ACHAT" if m_act.group(2).upper() == "BUY" else "VENTE"
        price = m_act.group(3).replace(",", ".")

        m_sl = REGEX_FTS_SL.search(raw_text or "")
        sl = m_sl.group(1).replace(",", ".") if m_sl else "0.0"

        # Le TP transmis a MT5 est le DERNIER palier : lui seul ferme la position en entier.
        # Les paliers intermediaires sont pris en partiels par monitor_positions.
        tps = parse_fts_tps(raw_text)
        tp = str(tps[-1]) if tps else "0.0"

        return rsym, action, price, sl, tp, "CANAL FTS"

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
            # NB: l'index du TP (\d) n'est consommé que s'il est suivi d'un séparateur ou d'un espace
            # (lookahead), sinon il mordait sur le 1er chiffre du prix ("Tp 4750" -> 750).
            for m in re.finditer(r"(?:TP|TARGET)\s*(?:\d(?=[\s:=\-]))?\s*[:=\-]?\s*([\d\.,]+)", txt, re.IGNORECASE):
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
    # (#67) Le CANAL D'ORIGINE fait foi. Avant, l'attribution VIP/FREE se déduisait uniquement
    # du FORMAT du message : prix collé à l'action -> VIP, prix introduit par À/@ -> FREE. Le
    # jour où MDT FREE a cessé d'écrire le "À", tous ses signaux ont donc basculé en VIP --
    # avec le risque % de VIP, sa gestion (filet 60 pips + partiel 30 %) et ses stats.
    # Le format ne sert plus qu'à EXTRAIRE symbole/action/prix, plus à décider du canal.
    mdt_source = None
    if channel_id == ID_MDT_VIP:
        mdt_source = "CANAL MDT_VIP"
    elif channel_id == ID_MDT_FREE:
        mdt_source = "CANAL MDT_FREE"
    # SL lu sur le texte BRUT (pas le texte nettoyé) : un message édité peut afficher
    # "SL : 3992 3987" (ancienne valeur barrée + nouvelle soulignée) -- clean_text() fusionnerait
    # les deux nombres espacés en un seul bloc illisible (ex: "39923987"). On relit donc ici sur
    # raw_text et on garde le DERNIER nombre trouvé après SL/STOP (= la valeur à jour).
    m_sl_raw = re.search(r"(?:SL|STOP)\s*[:=]?[ \t]*([\d.,]+)(?:[ \t]+([\d.,]+))?", raw_text, re.IGNORECASE)
    sl = "0.0"
    if m_sl_raw:
        sl_a = m_sl_raw.group(1).replace(',', '.')
        sl_b = (m_sl_raw.group(2) or "").replace(',', '.')
        if not sl_b:
            sl = sl_a
        elif "." not in sl_a and len(sl_b.split(".")[0]) == 3 and len(sl_a) <= 3:
            # (#68) "SL : 53 770.0" -- l'espace est un SÉPARATEUR DE MILLIERS, pas deux valeurs.
            # Garder le dernier nombre donnait un SL de 770 sur un US30 acheté à 53 879, soit
            # aucune protection réelle. Observé en production le 2026-08-12 (ticket 517701435).
            # Le motif est reconnaissable : 2e bloc de 3 chiffres exactement, 1er sans décimale
            # et d'au plus 3 chiffres. Un message édité ("SL : 3992 3987" = ancienne valeur
            # barrée + nouvelle) ne rentre pas dans ce moule, il garde donc la dernière valeur.
            sl = sl_a + sl_b
        else:
            sl = sl_b

    # On cherche TOUS les TP dans le texte, et on prend le TOUT DERNIER (On force le : ou =)
    all_tps = re.findall(r"(?:TP|TARGET)\s*(?:\d+)?\s*[:=]\s*([\d\.,]+)", text, re.IGNORECASE)
    tp = all_tps[-1].replace(',', '.') if all_tps else "0.0"

    # Format 1 : "SYMBOLE ACTION PRIX" direct, sans mot de liaison (ex: "XAUUSD ACHAT 1950") -> MDT_VIP
    m1 = REGEX_F1_ENTRY.search(text)
    if m1:
        # Si aucun prix n'est capturé directement ICI mais qu'un prix existe plus loin après
        # À/@/AT (nouveau format MDT_FREE, ex: "XAUUSD SELL-LIMIT À 4055"), ce n'est pas VIP :
        # on laisse la main au Format 3 plutôt que de renvoyer un signal sans prix par erreur.
        has_price_via_a = re.search(r"(?:À|@|AT)\s*[\d.,]+", text, re.IGNORECASE)
        if m1.group(3) or not has_price_via_a:
            rsym = m1.group(1).replace("#", "").upper()
            act_raw = m1.group(2).upper()
            price = m1.group(3)
            action = "ACHAT" if "ACHAT" in act_raw or "BUY" in act_raw else "VENTE"
            return rsym, action, price, sl, tp, mdt_source or "CANAL MDT_VIP"

    # Format 3 (nouveau format MDT_FREE, 2026-07) : "SYMBOLE ACTION[-LIMIT/-STOP] À PRIX"
    # ex: "XAUUSD SELL-LIMIT À 4055", "XAUUSD BUY À 3997"
    m3 = REGEX_F3_ENTRY.search(text)
    if m3:
        rsym = m3.group(1).replace("#", "").upper()
        act_raw = m3.group(2).upper()
        price = m3.group(3)
        action = "ACHAT" if "ACHAT" in act_raw or "BUY" in act_raw else "VENTE"
        return rsym, action, price, sl, tp, mdt_source or "CANAL MDT_FREE"

    # Format 2
    m2 = REGEX_F2_ENTRY.search(text)
    if m2:
        act_raw = m2.group(1).upper()
        rsym = m2.group(2).replace("#", "").upper()
        price = m2.group(3)
        # Si un symbole préfixé par '#' existe dans le texte, il est plus fiable que celui
        # capturé par la regex générique (qui peut confondre un mot comme "MAINTENANT" avec
        # un symbole quand il n'y a pas de '#' juste avant, ex: "VENDS MAINTENANT #XAUUSD").
        m_hash_sym = re.search(r"#([A-Z0-9]{3,10})", text, re.IGNORECASE)
        if m_hash_sym:
            rsym = m_hash_sym.group(1).upper()
        action = "ACHAT" if "ACHAT" in act_raw or "BUY" in act_raw else "VENTE"
        return rsym, action, price, sl, tp, mdt_source or "CANAL MDT_FREE"

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

    # 4. (#84) Echelle de TP INDODOG. Sans persistance, un redemarrage en cours de trade
    # perdrait les paliers : les partiels restants ne seraient jamais pris et la position
    # irait d'un coup jusqu'au TP final.
    try:
        with open(INDO_TP_FILENAME, "w") as f:
            json.dump({"tp": {str(k): v for k, v in INDO_TP_BY_TICKET.items()},
                       "levels": {k: list(v) for k, v in INDO_LEVELS_BY_SIGNAL.items()},
                       "base": INDO_BASE_VOL_BY_SIGNAL,
                       "echelle": INDO_ECHELLE_BY_SIGNAL,   # (#110)
                       "approached": list(INDO_APPROACHED)}, f)
    except Exception as e: logger.warning(f"⚠️ Save INDODOG TP Error: {e}")

def load_memory():
    global TRADES_PROCESSED_LEVELS, MANAGED_MANUAL_TICKETS, PARTIAL_PROFITS, INDO_TP_BY_TICKET
    global INDO_LEVELS_BY_SIGNAL, INDO_BASE_VOL_BY_SIGNAL, INDO_APPROACHED, INDO_ECHELLE_BY_SIGNAL
    
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

    # 4. (#84) Échelle de TP INDODOG. Sans elle, un redémarrage en cours de trade perdrait les
    # paliers : les partiels restants ne seraient jamais pris et la position irait d'un seul
    # coup jusqu'au TP final.
    if os.path.exists(INDO_TP_FILENAME):
        try:
            with open(INDO_TP_FILENAME, "r") as f:
                _d = json.load(f)
            # Ancien format (dict plat ticket -> paliers) accepte pour ne rien perdre lors
            # d'une mise a jour en cours de trade.
            if "tp" in _d:
                INDO_TP_BY_TICKET = {int(k): v for k, v in _d.get("tp", {}).items()}
                INDO_LEVELS_BY_SIGNAL = {k: set(v) for k, v in _d.get("levels", {}).items()}
                INDO_BASE_VOL_BY_SIGNAL = _d.get("base", {})
                # (#110) Absent d'un fichier ecrit par un exe anterieur : ces signaux-la
                # recevront l'echelle en vigueur au premier passage du moniteur.
                INDO_ECHELLE_BY_SIGNAL = {k: list(v) for k, v in _d.get("echelle", {}).items()
                                          if isinstance(v, list) and v}
                INDO_APPROACHED = set(int(t) for t in _d.get("approached", []))
            else:
                INDO_TP_BY_TICKET = {int(k): v for k, v in _d.items()}
            logger.info(f"🍽 Paliers INDODOG restaurés ({len(INDO_TP_BY_TICKET)} trades)")
        except Exception as e: logger.warning(f"⚠️ Load INDODOG TP Error: {e}")

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
                    except Exception:
                        # 🛑 SÉCURITÉ ABSOLUE : Si le fichier est illisible à ce moment précis,
                        # on annule la sauvegarde pour ne surtout pas écraser la clé de licence !
                        logger.error("⚠️ Fichier settings occupé, sauvegarde ignorée pour protéger la licence.")
                        return

            # 2. On prépare les nouvelles valeurs à mettre à jour
            new_values = {
                "RISK_PERCENT_GOLD": RISK_PERCENT_GOLD,
                "RISK_PERCENT_OTHER": RISK_PERCENT_OTHER,
                "RISK_PERCENT_MDT_VIP": RISK_PERCENT_MDT_VIP,
                "RISK_PERCENT_MDT_FREE": RISK_PERCENT_MDT_FREE,
                "FIXED_LOT_SABR_FREE": FIXED_LOT_SABR_FREE,
                "FIXED_LOT_SABR_VIP": FIXED_LOT_SABR_VIP,
                "FIXED_LOT_INDODOG": FIXED_LOT_INDODOG,
                "FIXED_LOT_FTS": FIXED_LOT_FTS,
                "INDO_ECHELLE_TP": INDO_ECHELLE_TP,   # (#110)
                "FTS_ECHELLE_TP": FTS_ECHELLE_TP,     # (#110)
                "RISK_PERCENT_SABR_OTHER": RISK_PERCENT_SABR_OTHER,
                "SABR_ZONE_EQUAL_SPLIT": SABR_ZONE_EQUAL_SPLIT,
                "SABR_ZONE_BORNES": SABR_ZONE_BORNES,
                "SABR_BE_PARTIEL_ACTIVE": SABR_BE_PARTIEL_ACTIVE,
                "SABR_BE_PARTIEL_PCT": SABR_BE_PARTIEL_PCT,
                "SABR_ZONE_PCT1": SABR_ZONE_PCT1,
                "SABR_ZONE_PCT2": SABR_ZONE_PCT2,
                "RISK_PERCENT_SPACE": RISK_PERCENT_SPACE,
                "RISK_PERCENT_LIGUE": RISK_PERCENT_LIGUE,
                "RISK_PERCENT_PERSO": RISK_PERCENT_PERSO,
                "MAX_DAILY_DRAWDOWN": MAX_DAILY_DRAWDOWN,
                "DRAWDOWN_ACTIVE": DRAWDOWN_ACTIVE,
                "TRAILING_STOP_PIPS": TRAILING_STOP_PIPS,
                "SOFT_SL_PERCENT": SOFT_SL_PERCENT,
                "SL_SUIVEUR_ACTIVE": SL_SUIVEUR_ACTIVE,
                "SOFT_SL_ACTIVE": SOFT_SL_ACTIVE,
                "BE_OFFSET_PIPS": BE_OFFSET_PIPS,
                "SABR_TRAILING_ACTIVE": SABR_TRAILING_ACTIVE,
                "SABR_TRAILING_PIPS": SABR_TRAILING_PIPS,
                "SABR_ANTI_DOUBLON_ACTIVE": SABR_ANTI_DOUBLON_ACTIVE,
                "MUTED_CHANNELS": MUTED_CHANNELS,
                "ALLOWED_CHANNELS": ALLOWED_CHANNELS,
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
    global RISK_PERCENT_GOLD, SABR_FORWARD_ACTIVE, RISK_PERCENT_OTHER, RISK_PERCENT_MDT_VIP, RISK_PERCENT_MDT_FREE, MAX_DAILY_DRAWDOWN, DRAWDOWN_ACTIVE, TRAILING_STOP_PIPS, RISK_PERCENT_SPACE, RISK_PERCENT_LIGUE, RISK_PERCENT_PERSO, SOFT_SL_PERCENT, MUTED_CHANNELS, ALLOWED_CHANNELS, CHANNEL_TP1_PCT, TELEGRAM_DESTINATION_ID, ADMIN_ID, RISK_PERCENT_INDICES, PRIME_HOURS, TIME_OFF_PEAK_FACTOR, DAY_RISK_FACTORS, FIXED_LOT_SABR_FREE, FIXED_LOT_SABR_VIP, RISK_PERCENT_SABR_OTHER, SABR_ZONE_EQUAL_SPLIT, SABR_ZONE_BORNES, SABR_ZONE_PCT1, SABR_ZONE_PCT2, SL_SUIVEUR_ACTIVE, SOFT_SL_ACTIVE, FIREBASE_EMAIL, FIREBASE_PASSWORD, BE_OFFSET_PIPS, SABR_TRAILING_ACTIVE, SABR_TRAILING_PIPS, FIXED_LOT_INDODOG, FIXED_LOT_FTS, SABR_BE_PARTIEL_ACTIVE, SABR_BE_PARTIEL_PCT, SABR_ANTI_DOUBLON_ACTIVE
    global INDO_ECHELLE_TP, FTS_ECHELLE_TP   # (#110) cf. le piege de l'anti-doublon (V15.8.0)

    if os.path.exists(SETTINGS_FILENAME):
        try:
            # (#43) save_settings() écrit toujours sous SETTINGS_LOCK -- la lecture doit
            # utiliser le même verrou pour éviter de lire un fichier à moitié écrit.
            with SETTINGS_LOCK, open(SETTINGS_FILENAME, "r") as f:
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
                if "RISK_PERCENT_SABR_OTHER" in data: RISK_PERCENT_SABR_OTHER = data["RISK_PERCENT_SABR_OTHER"]
                if "SABR_ZONE_EQUAL_SPLIT" in data: SABR_ZONE_EQUAL_SPLIT = data["SABR_ZONE_EQUAL_SPLIT"]
                if "SABR_ZONE_BORNES" in data:
                    _b = [x for x in (data["SABR_ZONE_BORNES"] or []) if x in ("debut", "milieu", "fin")]
                    if _b: SABR_ZONE_BORNES = _b
                if "SABR_BE_PARTIEL_ACTIVE" in data: SABR_BE_PARTIEL_ACTIVE = bool(data["SABR_BE_PARTIEL_ACTIVE"])
                if "SABR_BE_PARTIEL_PCT" in data: SABR_BE_PARTIEL_PCT = float(data["SABR_BE_PARTIEL_PCT"])
                if "SABR_ZONE_PCT1" in data: SABR_ZONE_PCT1 = data["SABR_ZONE_PCT1"]
                if "SABR_ZONE_PCT2" in data: SABR_ZONE_PCT2 = data["SABR_ZONE_PCT2"]
                if "RISK_PERCENT_SPACE" in data: RISK_PERCENT_SPACE = data["RISK_PERCENT_SPACE"]
                if "RISK_PERCENT_LIGUE" in data: RISK_PERCENT_LIGUE = data["RISK_PERCENT_LIGUE"]
                if "RISK_PERCENT_PERSO" in data: RISK_PERCENT_PERSO = data["RISK_PERCENT_PERSO"]
                if "MAX_DAILY_DRAWDOWN" in data: MAX_DAILY_DRAWDOWN = data["MAX_DAILY_DRAWDOWN"]
                if "DRAWDOWN_ACTIVE" in data: DRAWDOWN_ACTIVE = data["DRAWDOWN_ACTIVE"]
                if "TRAILING_STOP_PIPS" in data: TRAILING_STOP_PIPS = data["TRAILING_STOP_PIPS"]
                if "SOFT_SL_PERCENT" in data: SOFT_SL_PERCENT = data["SOFT_SL_PERCENT"]
                if "SL_SUIVEUR_ACTIVE" in data: SL_SUIVEUR_ACTIVE = data["SL_SUIVEUR_ACTIVE"]
                if "BE_OFFSET_PIPS" in data: BE_OFFSET_PIPS = float(data["BE_OFFSET_PIPS"])
                if "FIXED_LOT_INDODOG" in data: FIXED_LOT_INDODOG = float(data["FIXED_LOT_INDODOG"])
                if "FIXED_LOT_FTS" in data: FIXED_LOT_FTS = float(data["FIXED_LOT_FTS"])
                # (#110) Une echelle illisible (fichier edite a la main) garde le defaut plutot
                # que de faire echouer tout le chargement des reglages.
                for _cle in ("INDO_ECHELLE_TP", "FTS_ECHELLE_TP"):
                    if _cle in data:
                        _ech, _err = lire_echelle(data[_cle])
                        if _err:
                            logger.warning(f"⚠️ settings.json : {_cle} ignoré ({_err})")
                        elif _cle == "INDO_ECHELLE_TP":
                            INDO_ECHELLE_TP = _ech
                        else:
                            FTS_ECHELLE_TP = _ech
                if "SABR_TRAILING_ACTIVE" in data: SABR_TRAILING_ACTIVE = bool(data["SABR_TRAILING_ACTIVE"])
                if "SABR_TRAILING_PIPS" in data: SABR_TRAILING_PIPS = int(float(data["SABR_TRAILING_PIPS"]))
                if "SOFT_SL_ACTIVE" in data: SOFT_SL_ACTIVE = data["SOFT_SL_ACTIVE"]
                if "MUTED_CHANNELS" in data: MUTED_CHANNELS = data["MUTED_CHANNELS"]
                # (#26) Le pack local n'est qu'un repli avant la 1ère vérif licence en ligne --
                # dès que le serveur a répondu (SERVER_PACK_CHANNELS non None), il fait autorité.
                if SERVER_PACK_CHANNELS is not None:
                    ALLOWED_CHANNELS = list(SERVER_PACK_CHANNELS)
                elif "ALLOWED_CHANNELS" in data:
                    ALLOWED_CHANNELS = data["ALLOWED_CHANNELS"]  # (#7) pack du client (repli hors-ligne)
                if "CHANNEL_TP1_PCT" in data: CHANNEL_TP1_PCT = data["CHANNEL_TP1_PCT"]
                if "TELEGRAM_DESTINATION_ID" in data: 
                    TELEGRAM_DESTINATION_ID = data["TELEGRAM_DESTINATION_ID"]
                    ADMIN_ID = TELEGRAM_DESTINATION_ID
                if "RISK_PERCENT_INDICES" in data: RISK_PERCENT_INDICES = data["RISK_PERCENT_INDICES"]
                if "PRIME_HOURS" in data: PRIME_HOURS = data["PRIME_HOURS"]
                if "TIME_OFF_PEAK_FACTOR" in data: TIME_OFF_PEAK_FACTOR = data["TIME_OFF_PEAK_FACTOR"]
                if "SABR_FORWARD_ACTIVE" in data: SABR_FORWARD_ACTIVE = data["SABR_FORWARD_ACTIVE"]
                # (#94) Manquait ici : save_settings() l'ecrivait bien dans settings.json, mais
                # load_settings() ne le relisait jamais. Le reglage revenait donc a son defaut
                # (True) a CHAQUE redemarrage du bot -- l'anti-doublon "se reactivait tout seul".
                if "SABR_ANTI_DOUBLON_ACTIVE" in data: SABR_ANTI_DOUBLON_ACTIVE = bool(data["SABR_ANTI_DOUBLON_ACTIVE"])
                if "DAY_RISK_FACTORS" in data:
                    # JSON stocke les clés (jours) en string "4", on remet en int 4
                    loaded_days = data["DAY_RISK_FACTORS"]
                    DAY_RISK_FACTORS = {int(k): v for k, v in loaded_days.items()}
                if "FIREBASE_EMAIL" in data:    FIREBASE_EMAIL    = data["FIREBASE_EMAIL"]
                if "FIREBASE_PASSWORD" in data: FIREBASE_PASSWORD = data["FIREBASE_PASSWORD"]

            recompute_pack_lock()  # (#25) verrouille réellement les canaux hors pack
            logger.info("⚙️ Ensemble des réglages chargés avec succès.")
            logger.info(f"🔕 Canaux Ignorés : {MUTED_CHANNELS}")
            logger.info(f"📦 Pack client (canaux visibles) : {ALLOWED_CHANNELS if ALLOWED_CHANNELS else 'TOUS'}")
        except Exception as e: logger.error(f"⚠️ Erreur Load Settings: {e}")

def load_channel_data():
    global ACTIVE_TRADE_SOURCES, ACTIVE_TRADE_SIGNALS

    # 0. (#stats) Archive un trades_detailed.csv resté à l'ancien format avant toute lecture
    rotate_detailed_csv_if_outdated()

    # 1. Sources des trades en cours
    if os.path.exists(SOURCES_MEMORY_FILE):
        try:
            with open(SOURCES_MEMORY_FILE, "r") as f:
                # Convertir les clés (tickets) en int car JSON les met en string
                raw = json.load(f)
                ACTIVE_TRADE_SOURCES = {int(k): v for k, v in raw.items()}
        except: pass

    # 2. (#stats) Signal d'origine des trades en cours (1 signal = N positions)
    if os.path.exists(SIGNALS_MEMORY_FILE):
        try:
            with open(SIGNALS_MEMORY_FILE, "r") as f:
                raw = json.load(f)
                ACTIVE_TRADE_SIGNALS = {int(k): v for k, v in raw.items()}
        except: pass

def save_channel_data():
    try:
        with open(SOURCES_MEMORY_FILE, "w") as f: json.dump(ACTIVE_TRADE_SOURCES, f)
        with open(SIGNALS_MEMORY_FILE, "w") as f: json.dump(ACTIVE_TRADE_SIGNALS, f)
    except: pass

def _date_broker():
    """(#106) Date du jour en heure BROKER, au format AAAA-MM-JJ."""
    return (datetime.now(timezone.utc) + timedelta(hours=BROKER_TIME_OFFSET)).strftime("%Y-%m-%d")


def save_state_and_stats(day, balance_snapshot):
    global DAILY_REFS
    # (#106) Reference de minuit PAR COMPTE. ftmo_state.txt ne gardait que "jour|balance" :
    # ni le compte, ni le mois. Au redemarrage, la balance etait reprise des que le numero du
    # jour concordait, QUEL QUE SOIT le compte connecte -- passer d'un compte de 10 000 EUR a un
    # compte de 50 000 EUR dans la journee faisait croire a +40 000 EUR de gain et neutralisait
    # le drawdown jusqu'a minuit, sans aucun symptome ; dans l'autre sens, verrou instantane.
    # On garde une reference par compte, verrou compris, pour la journee : un aller-retour
    # prop -> demo -> prop retrouve la perte du matin au lieu de repartir de zero, ce qui
    # aurait autorise a reperdre la totalite du seuil une seconde fois.
    aujourd_hui = _date_broker()
    if DAILY_REFS.get("date") != aujourd_hui:
        DAILY_REFS = {"date": aujourd_hui, "comptes": {}}   # nouveau jour : on repart a vide
    if CURRENT_ACCOUNT_LOGIN:
        DAILY_REFS["comptes"][str(CURRENT_ACCOUNT_LOGIN)] = {
            "ref": float(balance_snapshot), "verrou": bool(security_triggered)}
    try:
        with open(REFS_FILENAME, "w") as f:
            json.dump(DAILY_REFS, f)
    except: pass

    # Format historique conserve : un exe plus ancien relance par-dessus retrouve ses petits.
    try:
        with open(DATA_FILENAME, "w") as f: 
            f.write(f"{day}|{balance_snapshot}")
    except: pass
    
    # Sauvegarde des stats (inchangé)
    try:
        data_to_save = daily_stats.copy()
        data_to_save["active_trades_max_pips"] = active_trades_max_pips
        data_to_save["active_trades_min_pips"] = active_trades_min_pips
        # (#97) Le verrou drawdown doit survivre a un redemarrage : sans ca, il vivait
        # uniquement en memoire et repartait a False au lancement -- relancer le bot suffisait
        # donc a lever une securite censee ne ceder qu'a un deverrouillage explicite.
        data_to_save["security_triggered"] = security_triggered
        with open(STATS_FILENAME, "w") as f: json.dump(data_to_save, f)
    except: pass

def load_daily_state(server_day):
    global daily_stats, active_trades_max_pips, active_trades_min_pips, security_triggered, DAILY_REFS
    saved_balance = None
    meme_jour = False       # (#106) les COMPTEURS du jour sont communs a tous les comptes
    verrou_compte = None    # (#106) le VERROU, lui, appartient au compte

    # (#106) Source de verite : la reference du compte connecte, pour la date complete du jour.
    refs = None
    if os.path.exists(REFS_FILENAME):
        try:
            with open(REFS_FILENAME, "r") as f:
                refs = json.load(f)
        except Exception:
            refs = None
    if isinstance(refs, dict):
        if refs.get("date") == _date_broker():
            DAILY_REFS = {"date": refs["date"], "comptes": dict(refs.get("comptes") or {})}
            meme_jour = True
            entree = DAILY_REFS["comptes"].get(str(CURRENT_ACCOUNT_LOGIN))
            if entree:
                saved_balance = float(entree["ref"])
                verrou_compte = bool(entree.get("verrou"))
            else:
                # Compte jamais vu aujourd'hui : nouvelle reference, et aucun verrou herite
                # d'un autre compte.
                verrou_compte = False
    # Repli : premier demarrage apres la mise a jour, seul l'ancien format existe. Le numero du
    # jour seul ne dit ni le mois ni le compte, d'ou son remplacement -- mais l'ignorer ici
    # fausserait la journee de chaque client au moment precis ou il installe la mise a jour.
    elif os.path.exists(DATA_FILENAME):
        try:
            with open(DATA_FILENAME, "r") as f:
                c = f.read().strip()
                if "|" in c:
                    d, b = c.split('|')[:2]
                    # Si c'est bien le même jour qu'aujourd'hui, on récupère la balance de minuit
                    if int(d) == server_day: 
                        saved_balance = float(b)
                        meme_jour = True
        except: pass
        
    # Chargement des stats
    if os.path.exists(STATS_FILENAME) and meme_jour:
        try:
            with open(STATS_FILENAME, "r") as f: 
                loaded = json.load(f)
                if "active_trades_max_pips" in loaded:
                    active_trades_max_pips = {int(k): v for k, v in loaded["active_trades_max_pips"].items()}
                    del loaded["active_trades_max_pips"]
                if "active_trades_min_pips" in loaded:
                    active_trades_min_pips = {int(k): v for k, v in loaded["active_trades_min_pips"].items()}
                    del loaded["active_trades_min_pips"]
                # (#97) Verrou drawdown restaure UNIQUEMENT ici, c'est-a-dire dans la branche
                # "meme jour" (saved_balance non nul). Si le bot est reste eteint au passage de
                # minuit, on ne repasse pas par ce chemin : le verrou tombe de lui-meme, ce qui
                # est exactement la regle voulue -- il saute au changement de jour.
                if "security_triggered" in loaded:
                    # (#106) Le verrou du COMPTE connecte prime : daily_stats.json garde celui du
                    # dernier compte actif, qui n'est pas forcement le meme.
                    security_triggered = (verrou_compte if verrou_compte is not None
                                          else bool(loaded["security_triggered"]))
                    del loaded["security_triggered"]
                    if security_triggered:
                        logger.warning("🔒 Verrou drawdown restaure : le trading reste bloque "
                                       "jusqu'au deverrouillage manuel ou au changement de jour.")
                daily_stats.update(loaded)
        except: pass
    else:
        daily_stats = DEFAULT_STATS.copy()

    # (#106) Filet si daily_stats.json n'a pas la cle (fichier ancien ou corrompu) : le verrou
    # memorise pour CE compte s'applique quand meme.
    if verrou_compte is not None:
        security_triggered = verrou_compte

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


DETAILED_CSV_COLUMNS = [
    "Ticket", "Signal ID", "Symbol", "Source", "Type", "Open Time", "Close Time",
    "Duration (min)", "Lots", "Profit", "Pips", "EUR/pip",
    "MAE (Drawdown)", "MFE (Max Peak)", "Exit Reason",
]


def rotate_detailed_csv_if_outdated(fieldnames=None):
    """(#stats) Archive trades_detailed.csv si son en-tête n'est plus celui du format courant.

    Appelé au DÉMARRAGE (et non pas seulement à la première écriture) pour deux raisons :
    appender des lignes à 15 champs sous un en-tête de 13 produirait un CSV décalé, et surtout
    l'ancien historique cohabiterait avec stats_seed.json jusqu'au premier trade clôturé --
    donc une période "Max" comptant deux fois les mêmes résultats. Retourne True si rotation.
    """
    fieldnames = fieldnames or DETAILED_CSV_COLUMNS
    if not os.path.isfile(DETAILED_HISTORY_FILE):
        return False
    try:
        with open(DETAILED_HISTORY_FILE, "r", encoding="utf-8") as f:
            old_header = (f.readline() or "").strip().split(",")
        if old_header == fieldnames:
            return False
        bak = DETAILED_HISTORY_FILE.replace(".csv", "") + \
              f".v1_{datetime.now().strftime('%Y%m%d_%H%M%S')}.bak.csv"
        os.rename(DETAILED_HISTORY_FILE, bak)
        logger.warning(f"📦 Ancien format de {DETAILED_HISTORY_FILE} archivé -> {bak}")
        return True
    except Exception as e:
        logger.error(f"⚠️ Rotation CSV detailed : {e}")
        return False


def normalize_channel_source(source):
    """(#stats) Regroupe les sous-catégories SABR sous deux libellés stables.

    Appliqué à l'ÉCRITURE (check_closed_positions) ET à la LECTURE
    (get_channel_stats_period) : sans ça, un historique écrit par une version antérieure au
    regroupement se retrouve éclaté en 8 lignes distinctes dans l'onglet "Par Canal"
    (CANAL SABR_VIP_BLIND, CANAL SABR_FREE_UPDATE_ZONE...) alors que le cumul lifetime, lui,
    n'en montre que 2. Pire, _channel_base_token() ne sait pas rattacher ces variantes à un
    pack, donc elles disparaissaient purement et simplement chez un client SABR."""
    s = (source or "").strip()
    if "SABR" not in s.upper():
        return s
    return "CANAL SABR VIP" if ("VIP" in s.upper() or "COPIE" in s.upper()) else "CANAL SABR FREE"


def log_trade_detail(ticket, symbol, source, direction, open_time, close_time, duration, lots,
                     profit, pips, mae, mfe, exit_reason, signal_id="", eur_per_pip=0.0):
    """Enregistre chaque trade individuellement pour analyse expert"""
    file_exists = os.path.isfile(DETAILED_HISTORY_FILE)

    # Conversion des timestamps en format lisible (Excel friendly) avec correction heure française
    # (#108) Format conservé tel quel, il sert aussi à l'analyse dans Excel : la relecture en
    # instant réel se fait dans epoch_csv(), qui refait ce calcul à l'envers. Ne pas le changer
    # sans adapter epoch_csv -- toutes les périodes des stats et de l'historique en dépendent.
    ot_str = (datetime.fromtimestamp(open_time) - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
    ct_str = (datetime.fromtimestamp(close_time) - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
    
    row = {
        "Ticket": ticket,
        "Signal ID": signal_id,      # (#stats) 1 signal = N positions (zone SABR = 3 ordres)
        "Symbol": symbol,
        "Source": source,
        "Type": direction,
        "Open Time": ot_str,
        "Close Time": ct_str,
        "Duration (min)": int(duration / 60), # Durée en minutes
        "Lots": f"{lots:.2f}",
        "Profit": f"{profit:.2f}",
        "Pips": f"{pips:.1f}",
        "EUR/pip": f"{eur_per_pip:.2f}",  # valeur d'un pip pour 1.00 lot sur ce symbole
        "MAE (Drawdown)": f"{mae:.1f}", # Le point le plus bas atteint (ex: -15.0)
        "MFE (Max Peak)": f"{mfe:.1f}", # Le point le plus haut atteint (ex: +45.0)
        "Exit Reason": exit_reason
    }

    fieldnames = list(row.keys())
    if rotate_detailed_csv_if_outdated(fieldnames):
        file_exists = False

    try:
        with open(DETAILED_HISTORY_FILE, "a", newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            # On écrit l'en-tête seulement si le fichier est nouveau
            if not file_exists: writer.writeheader()
            writer.writerow(row)
            logger.info(f"📝 Trade #{ticket} archivé dans {DETAILED_HISTORY_FILE}")
    except Exception as e: logger.error(f"⚠️ Erreur CSV Detailed: {e}")

# ==========================================
# 🕐 HEURES ET PERIODES (#108)
# ==========================================
# Trois horloges se croisent ici, et les confondre decalait les periodes d'une a deux heures :
#   - l'heure du SERVEUR MT5 : tous les horodatages MT5 (deal.time, position.time, tick.time)
#     sont l'heure murale du serveur, codee comme si c'etait de l'UTC. Souvent GMT+2 l'hiver,
#     GMT+3 l'ete ;
#   - l'heure de la MACHINE du bot (PC ou VPS), celle de datetime.now() ;
#   - l'heure du CLIENT, celle de son telephone : la seule qui ait un sens pour "aujourd'hui".
# Avant, l'historique et les stats comparaient des heures du CSV (heure serveur vue par la
# machine, moins 1 h) a des minuits de la machine. Quel que soit le fuseau de la machine, le
# CSV avait donc (ecart du serveur - 1) h d'avance sur son horloge, soit 2 h l'ete : un trade
# ferme a 22h30 tombait dans la journee du lendemain. Tout est desormais compare en INSTANTS
# REELS (epoch UTC), et les minuits sont ceux du client.
# trades_detailed.csv garde son format (cf. log_trade_detail) : on le relit en instants reels
# plutot que de reecrire le fichier, qui sert aussi a l'analyse dans Excel.

def mesurer_decalage_serveur():
    """(#108) Mesure l'ecart heure serveur MT5 - UTC sur un tick FRAIS. Bloquant (MT5).

    Un tick ne donne l'heure du serveur que s'il vient d'arriver : le dernier tick EURUSD d'un
    samedi date du vendredi soir. Une mesure n'est donc retenue que si le tick a AVANCE depuis
    la lecture precedente, et si l'ecart tombe a dix minutes pres sur une heure ronde -- les
    serveurs MT5 sont a l'heure pleine (GMT+2, GMT+3...), le reste est de la latence ou de la
    derive d'horloge. Sinon on garde la mesure precedente, ou a defaut BROKER_TIME_OFFSET (cf.
    decalage_serveur_s) : une horloge de PC fausse de plus de dix minutes ne produit jamais de
    mesure, plutot qu'un fuseau farfelu. Le passage heure d'ete / heure d'hiver du serveur est
    ainsi suivi tout seul, ce que le reglage fixe ne sait pas faire.
    """
    global DECALAGE_SERVEUR_S
    for sym in ("BTCUSD", "BITCOIN", "ETHUSD", "XAUUSD", "EURUSD"):
        try:
            if not mt5.symbol_select(sym, True):
                continue
            tick = mt5.symbol_info_tick(sym)
        except Exception:
            continue
        t_ms = getattr(tick, "time_msc", 0) if tick else 0
        if not t_ms:
            continue
        precedent = _TICKS_DECALAGE.get(sym)
        _TICKS_DECALAGE[sym] = t_ms
        if precedent is None or t_ms == precedent:
            continue  # premiere lecture, ou tick fige (marche ferme) : rien de sur a en tirer
        brut = t_ms / 1000.0 - time.time()
        arrondi = int(round(brut / 3600.0)) * 3600
        if abs(brut - arrondi) > 600 or not (-12 * 3600 <= arrondi <= 14 * 3600):
            continue
        if arrondi != DECALAGE_SERVEUR_S:
            logger.info(f"🕐 Heure du serveur MT5 : UTC{arrondi / 3600:+g} (mesuree sur {sym})")
            DECALAGE_SERVEUR_S = arrondi
        return DECALAGE_SERVEUR_S
    return DECALAGE_SERVEUR_S


def decalage_serveur_s():
    """(#108) Ecart heure serveur - UTC en secondes : la derniere mesure, sinon le reglage."""
    if DECALAGE_SERVEUR_S is not None:
        return DECALAGE_SERVEUR_S
    return int(BROKER_TIME_OFFSET * 3600)


def epoch_mt5(ts_serveur):
    """(#108) Horodatage MT5 (heure murale du serveur, codee en UTC) -> instant reel (epoch)."""
    return float(ts_serveur) - decalage_serveur_s()


def epoch_csv(texte):
    """(#108) Heure ecrite dans trades_detailed.csv -> instant reel (epoch UTC), None si illisible.

    log_trade_detail ecrit `datetime.fromtimestamp(t_serveur) - 1 h` : on refait le chemin a
    l'envers -- mktime est l'inverse exact de fromtimestamp sur la meme machine -- puis on
    retire l'ecart du serveur. Limite connue : l'ecart est celui d'AUJOURD'HUI, donc une ligne
    ecrite dans l'autre saison (heure d'ete / d'hiver) est relue a une heure pres.
    """
    try:
        dt = datetime.strptime(str(texte).strip(), "%Y-%m-%d %H:%M:%S")
        return time.mktime((dt + timedelta(hours=1)).timetuple()) - decalage_serveur_s()
    except Exception:
        return None


def tz_client_min():
    """(#108) Avance du client sur UTC, en minutes. A defaut, celle de la machine du bot."""
    tz = FUSEAU_CLIENT.get("tz_min")
    if isinstance(tz, int):
        return tz
    off = datetime.now().astimezone().utcoffset()
    return int(off.total_seconds() // 60) if off is not None else 0


def heure_client(ts_serveur, fmt="%H:%M"):
    """(#108) Heure a AFFICHER pour un horodatage MT5, dans le fuseau du client.

    Sans fuseau connu (dashboard jamais ouvert depuis l'installation), l'ancien calcul est
    garde a l'identique : `fromtimestamp - 1 h`. Il est juste sur un VPS regle en UTC face a un
    serveur en GMT+2/+3, decale partout ailleurs -- c'est ce que le fuseau du client corrige.
    """
    try:
        tz = FUSEAU_CLIENT.get("tz_min")
        if isinstance(tz, int):
            return datetime.fromtimestamp(epoch_mt5(ts_serveur) + tz * 60,
                                          timezone.utc).strftime(fmt)
        return (datetime.fromtimestamp(ts_serveur) - timedelta(hours=1)).strftime(fmt)
    except Exception:
        return "--:--"


def charger_fuseau_client():
    """(#108) Relit le fuseau appris lors d'une session precedente."""
    global FUSEAU_CLIENT
    if not os.path.exists(FUSEAU_FILENAME):
        return
    try:
        with open(FUSEAU_FILENAME, "r", encoding="utf-8") as f:
            d = json.load(f) or {}
        tz = d.get("tz_min")
        if isinstance(tz, int) and -720 <= tz <= 840:
            FUSEAU_CLIENT = {"tz_min": tz, "tz": str(d.get("tz") or "")[:64]}
    except Exception as e:
        logger.warning(f"⚠️ Lecture {FUSEAU_FILENAME} : {e}")


def noter_fuseau_client(payload):
    """(#108) Retient le fuseau que le dashboard joint a une demande de stats ou d'historique.

    Renvoye a chaque demande : le passage a l'heure d'ete ou d'hiver est donc rattrape a la
    prochaine ouverture du dashboard, sans rien a regler. Ecrit sur disque seulement s'il
    change. Retourne l'avance en minutes a utiliser pour CETTE demande.
    """
    global FUSEAU_CLIENT
    try:
        tz = int(payload.get("tz_min"))
    except (TypeError, ValueError, AttributeError):
        return tz_client_min()
    if not -720 <= tz <= 840:
        return tz_client_min()
    nom = str(payload.get("tz") or "")[:64]
    if FUSEAU_CLIENT.get("tz_min") != tz or FUSEAU_CLIENT.get("tz") != nom:
        FUSEAU_CLIENT = {"tz_min": tz, "tz": nom}
        try:
            with open(FUSEAU_FILENAME, "w", encoding="utf-8") as f:
                json.dump(FUSEAU_CLIENT, f)
        except Exception as e:
            logger.warning(f"⚠️ Ecriture {FUSEAU_FILENAME} : {e}")
        logger.info(f"🕐 Fuseau du client : UTC{tz / 60:+g} ({nom or 'sans nom'})")
    return tz


def bornes_periode(periode, tz_min=None, maintenant=None):
    """(#108) Bornes d'une periode des stats ou de l'historique, en instants reels (epoch UTC).

    Une seule regle pour tous les ecrans, calee sur le minuit du CLIENT :
      "today" et "1"  -> aujourd'hui depuis 00h00 ("Jour")
      "week"          -> depuis lundi 00h00 ("1S", "Cette Semaine")
      "N" (entier)    -> N journees entieres, aujourd'hui compris ("2j", "14j", "1M" = 30...)
      "all"           -> tout ("Max")
    Avant, les stats comptaient N x 24 h a l'heure pres et l'historique N journees entieres :
    "1M" ne couvrait pas les memes trades dans les deux ecrans.

    Retourne {"debut", "fin"} en epoch (None = pas de borne ; fin exclue) et {"jour_debut",
    "jour_fin"} en AAAA-MM-JJ du client, pour les lignes seulement datees (ajouts manuels,
    historique repris). None pour une periode inconnue : a l'appelant de choisir son repli.
    """
    tz = tz_client_min() if tz_min is None else int(tz_min)
    decal = timedelta(minutes=tz)
    now_utc = maintenant or datetime.now(timezone.utc)
    minuit = (now_utc + decal).replace(tzinfo=None, hour=0, minute=0, second=0, microsecond=0)
    p = str(periode).strip().lower()
    if p == "all":
        return {"debut": None, "fin": None, "jour_debut": None, "jour_fin": None}
    if p == "today":
        debut = minuit
    elif p == "week":
        debut = minuit - timedelta(days=minuit.weekday())
    else:
        try:
            n = int(p)
        except ValueError:
            return None
        if n < 1:
            return None
        if n >= 9999:  # ancienne valeur de "Max"
            return {"debut": None, "fin": None, "jour_debut": None, "jour_fin": None}
        debut = minuit - timedelta(days=n - 1)
    return {"debut": (debut - decal).replace(tzinfo=timezone.utc).timestamp(), "fin": None,
            "jour_debut": debut.strftime("%Y-%m-%d"), "jour_fin": None}


def bornes_plage(payload, tz_min=None):
    """(#107) Bornes d'une plage choisie au dashboard : du `debut` 00:00 au `fin` 23:59:59 inclus.

    Les dates sont celles du telephone (AAAA-MM-JJ). Le dashboard joint aussi les instants
    exacts qu'il a calcules (debut_ts, fin_ts) : eux tiennent compte d'un passage a l'heure
    d'ete ou d'hiver DANS la plage, ce que l'avance unique tz_min ne sait pas faire. Ils ne sont
    retenus que s'ils collent aux dates a trois heures pres ; un payload incoherent retombe sur
    le calcul par tz_min. Retourne None pour une plage invalide.
    """
    try:
        d1 = datetime.strptime(str(payload.get("debut", "")).strip(), "%Y-%m-%d")
        d2 = datetime.strptime(str(payload.get("fin", "")).strip(), "%Y-%m-%d")
    except Exception:
        return None
    if d2 < d1 or (d2 - d1).days > 3700:
        return None
    tz = tz_client_min() if tz_min is None else int(tz_min)
    debut = (d1 - timedelta(minutes=tz)).replace(tzinfo=timezone.utc).timestamp()
    fin = (d2 + timedelta(days=1) - timedelta(minutes=tz)).replace(tzinfo=timezone.utc).timestamp()
    try:
        dts, fts = float(payload.get("debut_ts")), float(payload.get("fin_ts"))
        if abs(dts - debut) <= 3 * 3600 and abs(fts - fin) <= 3 * 3600 and dts < fts:
            debut, fin = dts, fts
    except (TypeError, ValueError):
        pass
    return {"debut": debut, "fin": fin,
            "jour_debut": d1.strftime("%Y-%m-%d"), "jour_fin": d2.strftime("%Y-%m-%d")}


def dans_bornes(epoch, bornes):
    """(#108) Vrai si l'instant tombe dans la periode : debut inclus, fin exclue."""
    if epoch is None:
        return False
    if bornes.get("debut") is not None and epoch < bornes["debut"]:
        return False
    if bornes.get("fin") is not None and epoch >= bornes["fin"]:
        return False
    return True


def bornes_serveur(bornes):
    """(#108) Bornes d'une requete d'historique MT5, en heure SERVEUR (entiers, epoch).

    Passer un datetime naif a history_deals_get laissait la bibliotheque le convertir avec le
    fuseau de la machine : la fenetre se retrouvait decalee de l'ecart du serveur, et les
    trades des 2-3 dernieres heures sortaient de "Cette Semaine" et des stats globales. En
    entiers, la comparaison se fait directement avec deal.time.
    """
    dec = decalage_serveur_s()
    debut = bornes.get("debut")
    fin = bornes.get("fin")
    # Plancher en 2000 plutot qu'en 1970 : un 0 n'a jamais ete teste face au terminal.
    debut_srv = int(debut + dec) if debut is not None else 946684800
    # Sans borne de fin : un peu au-dela de maintenant, rien ne peut exister dans le futur.
    # (#107) Avec une borne : MT5 inclut ses deux bornes, la notre est exclue -- d'ou le -1, sans
    # lequel un deal tombe pile a minuit compterait dans deux plages consecutives.
    fin_srv = int(fin + dec) - 1 if fin is not None else int(time.time() + dec + 3600)
    return debut_srv, fin_srv


def get_trades_history(bornes, max_rows=100, canal=None):
    """(#74) Liste des trades clôturés sur la période, du plus récent au plus ancien.

    Lue depuis trades_detailed.csv et non depuis l'historique MT5 : c'est la seule source qui
    porte le CANAL d'origine et la RAISON de sortie, deux colonnes attendues dans un historique.
    En contrepartie, "all" ne remonte qu'à ce que le bot a enregistré.

    (#108) `bornes` vient de bornes_periode(). Chaque ligne porte `close_ts` (instant réel) :
    le dashboard l'affiche à l'heure du téléphone. `close_time` est la même heure, dans le
    fuseau du client connu du bot, pour un dashboard qui ne lirait pas close_ts.

    Plafonné à max_rows : au-delà le document Firestore gonflerait sans que personne ne fasse
    défiler autant de lignes.

    (#109) `canal` (libellé des stats, ex. "CANAL SABR VIP") filtre AVANT le plafond : filtrer
    à l'écran ne chercherait que parmi les 100 trades les plus récents tous canaux confondus,
    et un canal peu actif paraîtrait vide sur « 1M » ou « Max ».
    """
    out = []
    if not os.path.exists(DETAILED_HISTORY_FILE):
        return out
    tz = tz_client_min()
    try:
        with open(DETAILED_HISTORY_FILE, "r", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                closed_ts = epoch_csv(row.get("Close Time", ""))
                if not dans_bornes(closed_ts, bornes):
                    continue
                src = normalize_channel_source(row.get("Source"))
                if src == "MANUEL/INCONNU":
                    src = "CANAL PERSO"
                if not channel_allowed(src):
                    continue
                if canal and src != canal:  # (#109) même rattachement que les stats par canal
                    continue
                def _f(key):
                    try: return float(row.get(key) or 0)
                    except Exception: return 0.0
                out.append({
                    "ticket": row.get("Ticket", ""),
                    "symbol": row.get("Symbol", ""),
                    "type": row.get("Type", ""),
                    "source": src.replace("CANAL ", ""),
                    "lots": _f("Lots"),
                    "profit": round(_f("Profit"), 2),
                    "pips": round(_f("Pips"), 1),
                    "close_time": datetime.fromtimestamp(closed_ts + tz * 60, timezone.utc)
                                          .strftime("%Y-%m-%d %H:%M:%S"),
                    "close_ts": int(closed_ts),
                    "reason": row.get("Exit Reason", ""),
                })
    except Exception as e:
        logger.warning(f"⚠️ get_trades_history : {e}")

    out.sort(key=lambda t: t["close_ts"], reverse=True)
    return out[:max_rows]


# (#91) { canal normalise : "YYYY-MM-DD HH:MM:SS" } -- tout ce qui est CLOTURE avant cette
# date est ignore dans les stats par canal.
STATS_RESET = {}


def load_stats_reset():
    global STATS_RESET
    if not os.path.exists(STATS_RESET_FILE):
        return
    try:
        with open(STATS_RESET_FILE, "r", encoding="utf-8") as f:
            STATS_RESET = json.load(f) or {}
        if STATS_RESET:
            logger.info("📊 Coupures de stats actives : %s"
                        % ", ".join(c.replace("CANAL ", "") for c in STATS_RESET))
    except Exception as e:
        logger.warning(f"⚠️ Lecture {STATS_RESET_FILE} : {e}")
        STATS_RESET = {}


def save_stats_reset():
    try:
        with open(STATS_RESET_FILE, "w", encoding="utf-8") as f:
            json.dump(STATS_RESET, f, indent=2, ensure_ascii=False)
    except Exception as e:
        logger.error(f"⚠️ Ecriture {STATS_RESET_FILE} : {e}")


def stats_reset_cutoff(src):
    """Date de coupure d'un canal, ou None. `src` doit deja etre normalise."""
    txt = STATS_RESET.get(src)
    if not txt:
        return None
    try:
        return datetime.strptime(txt, "%Y-%m-%d %H:%M:%S")
    except Exception:
        return None


# (#98) Libelles de canaux tels qu'ils apparaissent DANS LES STATS. Source unique : sert a
# valider un ajout manuel ET a remplir le menu deroulant du dashboard, pour qu'aucun des deux
# ne puisse deriver de l'autre. Attention, SABR y est regroupe avec une ESPACE
# ("CANAL SABR VIP") et non avec l'underscore de son nom de pack.
STATS_CHANNELS = [
    "CANAL MDT_VIP", "CANAL MDT_FREE",
    "CANAL SABR VIP", "CANAL SABR FREE",
    "CANAL INDODOG", "CANAL FTS",
    "CANAL SPACE", "CANAL LIGUE", "CANAL PERSO",
]


def charge_stats_manuel():
    """(#98) Lit les ajouts manuels. Liste vide si le fichier est absent ou hors profil dev.

    Le garde-fou porte sur AJOUT_MANUEL_STATS et non sur l'existence du fichier : un client
    qui devinerait le nom et le format n'obtiendrait rien, son bot ne lit tout simplement pas.
    """
    if not AJOUT_MANUEL_STATS or not os.path.exists(MANUAL_STATS_FILE):
        return []
    try:
        with open(MANUAL_STATS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception as e:
        logger.warning(f"⚠️ {MANUAL_STATS_FILE} illisible : {e}")
        return []


def sauve_stats_manuel(lignes):
    try:
        with open(MANUAL_STATS_FILE, "w", encoding="utf-8") as f:
            json.dump(lignes, f, indent=2, ensure_ascii=False)
        return True
    except Exception as e:
        logger.error(f"⚠️ Ecriture {MANUAL_STATS_FILE} : {e}")
        return False


def _merge_stats_manuel(out, bornes):
    """(#98) Injecte les ajouts manuels dans les stats par canal.

    Une ligne datee entre dans une periode si sa date tombe apres la borne de debut -- la
    meme regle que les vrais trades. Elle sort donc de "24h" toute seule le lendemain, tout
    en restant dans "1S" et "Max". C'est ce qui evite d'avoir un fichier a vider chaque nuit,
    donc un vidage qui pourrait echouer (bot eteint a minuit) ou se rejouer.

    (#108) Les dates saisies au dashboard sont celles du telephone : on les compare donc aux
    jours du CLIENT (bornes["jour_debut"] / ["jour_fin"]), pas a la date de la machine.

    Ces lignes n'alimentent QUE les stats par canal : ni l'historique des trades, ni la courbe
    du portefeuille, qui doivent rester le reflet exact du compte MT5.
    """
    for ligne in charge_stats_manuel():
        if not isinstance(ligne, dict):
            continue
        canal = (ligne.get("canal") or "").strip()
        if canal not in STATS_CHANNELS:
            logger.warning(f"⚠️ Ajout manuel ignore : canal inconnu {canal!r}")
            continue
        try:
            jour = datetime.strptime(str(ligne.get("date", "")).strip(), "%Y-%m-%d")
        except Exception:
            logger.warning(f"⚠️ Ajout manuel ignore ({canal}) : date invalide "
                           f"{ligne.get('date')!r}, attendu AAAA-MM-JJ")
            continue
        # Comparaison au JOUR pres : une ligne datee du jour de la borne doit entrer.
        jour_txt = jour.strftime("%Y-%m-%d")
        if bornes.get("jour_debut") and jour_txt < bornes["jour_debut"]:
            continue
        if bornes.get("jour_fin") and jour_txt > bornes["jour_fin"]:
            continue
        # Une remise a zero du canal masque aussi les ajouts manuels anterieurs a la coupure.
        _rst = stats_reset_cutoff(canal)
        if _rst and jour.date() < _rst.date():
            continue

        try:
            signaux = int(ligne.get("signaux", 1))
            ordres = int(ligne.get("ordres", signaux))
            profit = float(ligne.get("profit", 0.0))
            pips = float(ligne.get("pips", 0.0))
        except Exception:
            logger.warning(f"⚠️ Ajout manuel ignore ({canal}) : valeurs illisibles")
            continue

        entry = out.setdefault(canal, {"count": 0, "positions": 0, "profit": 0.0,
                                       "pips": 0.0, "avg_pips": 0.0})
        entry["count"] += signaux
        entry["positions"] += ordres
        entry["profit"] += profit
        entry["pips"] += pips

        # (#96) Detail par type d'actif : avec un symbole, la ligne rejoint sa vraie classe ;
        # sans symbole, elle a sa propre entree pour que la somme redonne le total du canal.
        sym = (ligne.get("symbole") or "").strip()
        sous = _entree_actif(entry, classe_actif(sym) if sym else "Ajout manuel")
        sous["count"] += signaux
        sous["positions"] += ordres
        sous["profit"] += profit
        sous["pips"] += pips


def _merge_stats_seed(out, bornes, fin_seed=None, exclus=None):
    """(#stats) Injecte un historique REPRIS d'avant la refonte des stats (stats_seed.json).

    Le CSV détaillé contient une ligne par trade ; un total agrégé ne peut donc pas y être
    écrit sans inventer des trades qui n'ont jamais existé sous cette forme. Ce fichier
    optionnel porte le total tel qu'il avait été mesuré, avec la plage de dates qu'il couvre.

    Il n'est ajouté QUE si la période demandée englobe entièrement cette plage (début de la
    période <= 'from'). Un chevauchement partiel est ignoré : on ne sait pas découper un
    agrégat, et l'inclure quand même gonflerait par exemple un "14 jours" avec des mois de
    résultats.

    (#107) Une plage personnalisée a aussi une FIN, qui doit dépasser celle du seed. Le seed ne
    note que son début ; sa fin est le champ 'to' s'il existe, sinon `fin_seed` -- le premier
    jour du CSV, puisque le seed a été créé quand le CSV repartait de zéro. Un seed écarté
    alors que la plage le chevauche est noté dans `exclus`, pour que le dashboard explique un
    total plus bas qu'attendu.
    """
    if not os.path.exists(STATS_SEED_FILE):
        return
    try:
        with open(STATS_SEED_FILE, "r", encoding="utf-8") as f:
            seed = json.load(f)
    except Exception as e:
        logger.warning(f"⚠️ stats_seed.json illisible : {e}")
        return

    for chan, data in seed.items():
        if chan.startswith("_") or not isinstance(data, dict):
            continue  # clés de documentation (_README, _CALCUL_...)
        try:
            date_from = datetime.strptime(data["from"], "%Y-%m-%d")
        except Exception:
            logger.warning(f"⚠️ stats_seed.json : champ 'from' manquant/invalide pour {chan}")
            continue
        # Comparaison au JOUR près, en jours du client (#108).
        jd, jf = bornes.get("jour_debut"), bornes.get("jour_fin")
        de = date_from.strftime("%Y-%m-%d")
        a = str(data.get("to") or fin_seed or "") or None
        couvre = (not jd or jd <= de) and (not jf or (a is not None and jf >= a))
        if not couvre:
            # (#107) Chevauchement partiel : écarté, mais signalé.
            if exclus is not None and (not jf or jf >= de) and (not jd or a is None or jd <= a):
                exclus.append({"canal": normalize_channel_source(chan), "debut": de, "fin": a})
            continue  # la période demandée ne couvre pas toute la plage du seed
        # (#91) Un seed est un AGREGAT : impossible d'en retirer la partie anterieure à une
        # remise à zéro. Si le canal a été remis à zéro après le début du seed, on le laisse
        # donc entièrement de côté -- c'est bien ce que veut dire "repartir de zéro".
        _rst = stats_reset_cutoff(normalize_channel_source(chan))
        if _rst and _rst.date() > date_from.date():
            continue
        entry = out.setdefault(normalize_channel_source(chan),
                               {"count": 0, "positions": 0, "profit": 0.0, "pips": 0.0,
                                "avg_pips": 0.0})
        entry["count"] += int(data.get("count", 0))
        entry["positions"] += int(data.get("positions", data.get("count", 0)))
        entry["profit"] += float(data.get("profit", 0.0))
        entry["pips"] += float(data.get("pips", 0.0))
        # (#96) Le seed est un TOTAL agrege : il ne porte aucun symbole, donc il ne peut pas
        # etre ventile par classe d'actifs. On lui donne sa propre ligne dans le detail plutot
        # que de le passer sous silence -- sans elle, la somme des classes serait inferieure au
        # total du canal sans que rien ne l'explique.
        seed_ligne = _entree_actif(entry, "Historique repris")
        seed_ligne["count"] += int(data.get("count", 0))
        seed_ligne["positions"] += int(data.get("positions", data.get("count", 0)))
        seed_ligne["profit"] += float(data.get("profit", 0.0))
        seed_ligne["pips"] += float(data.get("pips", 0.0))


def get_channel_stats_period(bornes, info=None):
    """Stats par canal SUR UNE PÉRIODE, reconstruites depuis trades_detailed.csv.

    SOURCE UNIQUE des stats par canal, tous comptes MT5 confondus. Il existait avant un
    compteur cumulatif en mémoire (stats_channels.json) qui, faute de dates, ne pouvait pas
    répondre à "les 30 derniers jours" : il fallait de toute façon relire le CSV, et les deux
    finissaient par diverger. La période "Max" (days=9999) remplace l'ancien "lifetime".

    Retourne {canal: {count, positions, profit, pips, avg_pips}}.

    (#stats) `count` compte les SIGNAUX (une zone SABR = 1 signal / 3 positions), et `avg_pips`
    est la moyenne simple pips/trade -- cf. _finalize_channel_stats() pour ses limites.

    (#108) `bornes` vient de bornes_periode() (ou de bornes_plage(), #107) : memes journees, et
    meme minuit, que l'historique des trades. `info`, s'il est fourni, recoit "seed_exclu"."""
    out = {}
    seen_signals = {}
    reset_ts = {}  # canal -> instant de la remise a zero, calcule une fois par canal
    premier_ts = None  # (#107) plus ancienne cloture du CSV, toutes periodes confondues
    try:
        with open(DETAILED_HISTORY_FILE, "r", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                # Ligne corrompue / en-tête d'un ancien format -> epoch None -> ignorée.
                closed_ts = epoch_csv(row.get("Close Time", ""))
                if closed_ts is not None and (premier_ts is None or closed_ts < premier_ts):
                    premier_ts = closed_ts
                if not dans_bornes(closed_ts, bornes):
                    continue
                src = normalize_channel_source(row.get("Source"))
                if not src:
                    continue
                # (#stats) Un trade ouvert à la main dans MT5, ou dont le canal d'origine a été
                # perdu (redémarrage avant l'écriture d'active_sources.json), est rattaché au
                # canal PERSO plutôt qu'ignoré : ce sont de vraies positions du compte, avec un
                # vrai résultat, elles doivent apparaître quelque part.
                if src == "MANUEL/INCONNU":
                    src = "CANAL PERSO"
                # (#91) Coupure testee APRES le rattachement a PERSO, sinon une remise a zero
                # de PERSO ne s'appliquerait pas aux trades manuels, qui en sont l'essentiel.
                # (#108) La coupure est une heure de la MACHINE (datetime.now() a la remise a
                # zero) : comparee en instant reel, comme la cloture.
                if src not in reset_ts:
                    _rst = stats_reset_cutoff(src)
                    reset_ts[src] = time.mktime(_rst.timetuple()) if _rst else None
                if reset_ts[src] is not None and closed_ts < reset_ts[src]:
                    continue
                entry = out.setdefault(src, {"count": 0, "positions": 0, "profit": 0.0,
                                             "pips": 0.0, "avg_pips": 0.0})
                # (#96) Detail par classe d'actifs, alimente en parallele du total du canal.
                sous = _entree_actif(entry, classe_actif(row.get("Symbol")))

                entry["positions"] += 1
                sous["positions"] += 1
                # Un signal peut avoir produit plusieurs positions : on ne le compte qu'une fois.
                # Sans Signal ID (lignes d'avant ce format), on retombe sur le ticket = 1 trade.
                sig = (row.get("Signal ID") or "").strip() or f"_tk{row.get('Ticket')}"
                if sig not in seen_signals.setdefault(src, set()):
                    seen_signals[src].add(sig)
                    entry["count"] += 1
                    # Un signal ne porte que sur un symbole : le compter dans sa seule classe
                    # suffit, la somme des classes redonne exactement le total du canal.
                    sous["count"] += 1
                try:
                    _p = float(row.get("Profit") or 0)
                    entry["profit"] += _p
                    sous["profit"] += _p
                except Exception: pass
                try:
                    _pi = float(row.get("Pips") or 0)
                    entry["pips"] += _pi
                    sous["pips"] += _pi
                except Exception: pass
    except FileNotFoundError:
        pass  # aucun trade enregistre : seuls l'historique repris et les ajouts manuels comptent
    except Exception as e:
        logger.warning(f"⚠️ get_channel_stats_period : {e}")

    # (#107) Fusions APRES la lecture du CSV : la fin de l'historique repris s'en deduit.
    fin_seed = (datetime.fromtimestamp(premier_ts + tz_client_min() * 60, timezone.utc)
                .strftime("%Y-%m-%d") if premier_ts is not None else None)
    exclus = []
    _merge_stats_seed(out, bornes, fin_seed, exclus)
    _merge_stats_manuel(out, bornes)   # (#98)
    if info is not None:
        info["seed_exclu"] = exclus
    return _finalize_channel_stats(out)


def classe_actif(symbole):
    """(#96) Range un symbole dans une classe d'actifs, pour le detail des stats par canal.

    Les tests suivent l'ordre de get_pip_divisor() -- meme vocabulaire de symboles, donc une
    paire reconnue la-bas l'est ici. "Autre" est un filet : mieux vaut une ligne "Autre" dans
    le detail qu'un trade qui disparait du total.
    """
    s = (symbole or "").upper()
    if not s:
        return "Autre"
    if "XAU" in s or "GOLD" in s:
        return "Or"
    if any(c in s for c in ("BTC", "BITCOIN", "ETH", "XRP", "SOL", "DOGE", "LTC", "ADA")):
        return "Crypto"
    if any(i in s for i in ("US100", "NAS100", "NASDAQ", "US30", "DOW", "DJ30",
                            "GER40", "DAX", "US500", "SPX", "SP500", "UK100", "JP225")):
        return "Indices"
    if "XAG" in s or "SILVER" in s:
        return "Argent"
    if any(e in s for e in ("USOIL", "UKOIL", "WTI", "BRENT")):
        return "Petrole"
    # Forex : deux codes devise de 3 lettres accoles.
    if len(s) >= 6 and s[:6].isalpha():
        return "Devises"
    return "Autre"


def _entree_actif(entry, classe):
    """Sous-total d'une classe d'actifs dans l'entree d'un canal, cree a la demande."""
    return entry.setdefault("actifs", {}).setdefault(
        classe, {"count": 0, "positions": 0, "profit": 0.0, "pips": 0.0, "avg_pips": 0.0})


def _finalize_channel_stats(out):
    """avg_pips = moyenne SIMPLE, somme des pips / nombre de trades (choix produit assumé).

    Une moyenne pondérée par le volume serait plus fidèle au résultat en euros -- sur un canal
    où les lots varient beaucoup (MDT VIP), la moyenne simple peut être négative alors que le
    canal gagne de l'argent, parce qu'un petit lot perdant pèse autant qu'un gros lot gagnant.
    L'affichage annonce "pips/trade", donc c'est bien une moyenne par trade qui est calculée.
    """
    for entry in out.values():
        entry.pop("_lots", None)
        entry.pop("_wpips", None)
        cnt = entry.get("count", 0)
        entry["avg_pips"] = round(entry["pips"] / cnt, 1) if cnt > 0 else 0.0
        entry["pips"] = round(entry["pips"], 1)
        entry["profit"] = round(entry["profit"], 2)
        # (#96) Meme traitement pour chaque classe d'actifs, puis tri par profit decroissant
        # pour que le dashboard n'ait rien a recalculer.
        actifs = entry.get("actifs")
        if actifs:
            for sous in actifs.values():
                c = sous.get("count", 0)
                sous["avg_pips"] = round(sous["pips"] / c, 1) if c > 0 else 0.0
                sous["pips"] = round(sous["pips"], 1)
                sous["profit"] = round(sous["profit"], 2)
            entry["actifs"] = dict(sorted(actifs.items(),
                                          key=lambda kv: kv[1]["profit"], reverse=True))
    return out


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
    global daily_start_balance, current_trade_day, CURRENT_ACCOUNT_LOGIN
    
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
    # (#106) Le compte doit etre connu AVANT de relire la reference : elle est rangee par compte.
    CURRENT_ACCOUNT_LOGIN = acc.login if acc else 0
    
    # On essaie de charger la balance de minuit depuis le fichier
    saved = load_daily_state(day)
    
    if saved is not None:
        # CAS 1 : On a déjà lancé le bot aujourd'hui, on reprend le point de référence
        daily_start_balance = saved
        # (#106) Reecrit tout de suite : au premier demarrage apres la mise a jour, c'est ce qui
        # convertit l'ancien ftmo_state.txt au nouveau format, sans attendre la boucle (60 s).
        save_state_and_stats(day, daily_start_balance)
        await send_alert(f"🔄 **BOT RELANCÉ**\n\nSession restaurée (compte {CURRENT_ACCOUNT_LOGIN}).\nBalance Minuit Ref: {daily_start_balance:.2f}€")
    else:
        # CAS 2 : C'est la première fois aujourd'hui (ou changement de jour)
        # On prend la balance ACTUELLE comme référence pour la journée
        daily_start_balance = acc.balance
        save_state_and_stats(day, daily_start_balance)
        await send_alert(f"🆕 **SESSION DÉMARRÉE**\n\nNouvelle session, pas de sauvegarde trouvée aujourd'hui\nBalance Ref: {daily_start_balance:.2f}€")
    return True

async def send_daily_report(acc, pnl_eur, pnl_pct):
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
    global daily_start_balance, current_trade_day, daily_stats, active_trades_max_pips, NOTIFIED_MANUAL_TICKETS, security_triggered, DD_PANIC_NOTIFIED
    
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
    _verifier_changement_compte(acc)   # (#106) au cas ou le DD serait coupe

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

        # (#97) Le verrou drawdown saute avec les compteurs : la perte du jour repart de zero,
        # le disjoncteur n'a plus de raison de tenir. Il ne manquait pas ici par hasard -- il
        # etait volontairement permanent, mais ca laissait le bot bloque tout un week-end sans
        # que personne s'en apercoive. Il reste bien sur en place pendant toute la journee, et
        # il survit desormais a un redemarrage (cf. save_state_and_stats).
        _verrou_leve = security_triggered
        security_triggered = False
        DD_PANIC_NOTIFIED = False   # (#103) nouveau jour = nouvel épisode possible
        
        # 4. Logique Hebdomadaire
        if weekday == 0: # Lundi
            daily_stats["weekly_pips"] = 0.0
            daily_stats["weekly_profit"] = 0.0 
        else:
            daily_stats["weekly_pips"] = current_weekly_pips
            daily_stats["weekly_profit"] = current_weekly_profit

        save_state_and_stats(day, daily_start_balance)
        await send_alert(f"📅 **NOUVEAU JOUR ({day})**\n\nCompteurs remis à zéro.\n"
                         f"Balance Ref: {daily_start_balance:.2f}€"
                         + ("\n\n🔓 **Sécurité Drawdown levée** — le trading reprend."
                            if _verrou_leve else ""))
    else:
        # Sauvegarde silencieuse de l'état actuel
        save_state_and_stats(current_trade_day, daily_start_balance)

def _verifier_changement_compte(acc):
    """(#106) Detecte un changement de compte MT5 et bascule la reference du jour. True si bascule.

    Appelee en TETE de check_daily_max_loss(), qui tourne chaque seconde, et pas seulement dans
    update_daily_stats(), qui ne tourne qu'une fois par minute : pendant cette minute, le
    drawdown se serait calcule avec la reference de l'ANCIEN compte. Vers un compte plus petit,
    il voyait une perte enorme, posait le verrou, et la fermeture d'urgence fermait les
    positions du bot sur le NOUVEAU compte.
    """
    global CURRENT_ACCOUNT_LOGIN, daily_start_balance, security_triggered, DD_PANIC_NOTIFIED
    if not CURRENT_ACCOUNT_LOGIN or not acc or not getattr(acc, "login", 0):
        return False
    if acc.login == CURRENT_ACCOUNT_LOGIN:
        return False

    ancien = CURRENT_ACCOUNT_LOGIN
    # 1. On fige l'ancien compte (reference + verrou) : il retrouvera son etat s'il revient.
    save_state_and_stats(current_trade_day, daily_start_balance)

    # 2. Bascule : reference deja connue pour ce compte aujourd'hui, sinon balance actuelle.
    CURRENT_ACCOUNT_LOGIN = acc.login
    entree = DAILY_REFS.get("comptes", {}).get(str(acc.login))
    if entree:
        daily_start_balance = float(entree["ref"])
        security_triggered = bool(entree.get("verrou"))
        origine = "reprise de sa référence du jour"
    else:
        daily_start_balance = acc.balance
        security_triggered = False
        origine = "nouvelle référence"
    DD_PANIC_NOTIFIED = False
    save_state_and_stats(current_trade_day, daily_start_balance)

    msg = (f"🔄 **CHANGEMENT DE COMPTE DÉTECTÉ**\n\n"
           f"{ancien} → **{acc.login}**\n"
           f"Balance de référence du jour : **{daily_start_balance:.2f}€** ({origine})\n"
           f"Sécurité drawdown : {'🔴 verrouillée sur ce compte' if security_triggered else '🟢 libre'}")
    logger.warning(f"🔄 Changement de compte MT5 : {ancien} -> {acc.login} "
                   f"(ref {daily_start_balance:.2f}, {origine}, verrou={security_triggered})")
    try:
        asyncio.get_event_loop().create_task(send_alert(msg))
    except Exception:
        pass
    return True


def check_daily_max_loss():
    """Retourne True si la perte du jour dépasse la limite ACTUELLE (recalculée à chaque
    appel avec le MAX_DAILY_DRAWDOWN courant). Effet de bord : passe security_triggered à
    True au moment où la limite est franchie -- ce verrou est ensuite persistant (ne
    redescend JAMAIS tout seul, même si on relève le Max DD et que cette fonction se remet
    à retourner False) : seul un déverrouillage manuel explicite (bouton dashboard/commande
    Telegram) peut le lever, et ce déverrouillage lui-même refuse tant que cette fonction
    retourne encore True. Avant ce correctif, security_triggered n'était jamais mis à True
    nulle part -- augmenter le Max DD suffisait donc, seul, à réactiver le trading."""
    global security_triggered
    # (#106) AVANT tout calcul : une reference appartenant a un autre compte fausserait le
    # resultat, et un verrou pose sur cette base declencherait la fermeture d'urgence.
    _verifier_changement_compte(mt5.account_info())

    # (#8) Disjoncteur désactivé -> on ne bloque rien (ni nouveaux trades ni close d'urgence)
    if not DRAWDOWN_ACTIVE:
        return False

    # On récupère le vrai P&L calculé depuis l'historique serveur
    current_daily_pnl, _, _ = get_accurate_daily_stats()

    acc = mt5.account_info()
    if not acc: return False

    # (#11) Limite basée sur la balance de MINUIT (base FTMO correcte), pas la balance
    # courante : sinon un gain en journée relève la limite au-delà de 4% du départ.
    # Fallback sur acc.balance si la référence de minuit n'est pas encore initialisée.
    base_bal = daily_start_balance if (daily_start_balance and daily_start_balance > 0) else acc.balance
    limit_amount = base_bal * MAX_DAILY_DRAWDOWN
    
    # Si on est en perte de plus de X (ex: -400€)
    if current_daily_pnl <= -limit_amount:
        security_triggered = True
        return True

    return False

async def get_last_used_lot(symbol):
    # 1. Positions en cours (ASYNC)
    open_pos = await run_mt5(mt5.positions_get, symbol=symbol)
    if open_pos and len(open_pos) > 0:
        return open_pos[-1].volume

    # 2. Historique (ASYNC)
    now = datetime.now()
    past = now - timedelta(days=30)
    
    # C'est ici que ça pouvait freezer :
    deals = await run_mt5(mt5.history_deals_get, past, now, group=symbol)
    
    if deals and len(deals) > 0:
        for deal in reversed(deals):
            if deal.entry == mt5.DEAL_ENTRY_IN:
                return deal.volume
                
    return None

def cap_lot_to_free_margin(symbol, action, lot, price, otype=None):
    """(#78) Réduit le lot pour qu'il tienne dans la marge libre du compte.

    Sans SL, la taille vient d'une table de lots par symbole (BASE_LOTS_FOR_01_PCT) qui ne
    consulte jamais le solde : le 2026-08-09, un signal BTCUSD sans SL a produit 0.04 lot et
    MT5 a répondu `REJET (10019) : No money`. Le signal était alors perdu, alors qu'une taille
    plus petite serait passée.

    Retourne le lot d'origine s'il tient, une version réduite sinon, ou 0.0 si même le lot
    minimum du broker ne rentre pas -- auquel cas il vaut mieux ne pas envoyer d'ordre du tout
    plutôt que de se faire rejeter.
    """
    try:
        acc = mt5.account_info()
        info = mt5.symbol_info(symbol)
        if not acc or not info or lot <= 0:
            return lot
        if otype is None:
            otype = mt5.ORDER_TYPE_BUY if action == "ACHAT" else mt5.ORDER_TYPE_SELL
        need = mt5.order_calc_margin(otype, symbol, lot, price)
        if not need or need <= 0:
            return lot
        # 90 % de la marge libre : on ne colle pas au plafond, sinon la moindre variation de
        # prix entre le calcul et l'exécution refait passer l'ordre en rejet.
        usable = acc.margin_free * 0.90
        if need <= usable:
            return lot

        step = info.volume_step if info.volume_step > 0 else 0.01
        capped = math.floor((lot * (usable / need)) / step) * step
        capped = round(min(capped, info.volume_max), 2)
        if capped < info.volume_min:
            logger.error(f"❌ Marge insuffisante sur {symbol} : même {info.volume_min} lot "
                         f"ne passe pas (libre {acc.margin_free:.2f}). Ordre abandonné.")
            return 0.0
        logger.warning(f"⚠️ Lot réduit sur {symbol} : {lot:.2f} -> {capped:.2f} "
                       f"(marge requise {need:.2f} > libre {acc.margin_free:.2f})")
        return capped
    except Exception as e:
        logger.warning(f"⚠️ cap_lot_to_free_margin ({symbol}) : {e}")
        return lot


async def get_safe_lot_size(symbol, signal_type, sl_price, entry_price_ref, channel_source=None, risk_override=None):
    # 1. Sélection Symbole (SYNCHRONE)
    await run_mt5(mt5.symbol_select, "EURUSD", True)
    acc = await run_mt5(mt5.account_info)
    if not acc: return 0.0, 0.0

    cap_multiplier = max(1, math.floor((acc.balance + 4000) / 10000))

    # 0. GESTION DES LOTS FIXES (SABR)
    if channel_source:
        if "SABR_VIP" in channel_source or "SABR_COPIE" in channel_source: return FIXED_LOT_SABR_VIP, 0.0
        if "SABR" in channel_source: return FIXED_LOT_SABR_FREE, 0.0
        # (#84) INDODOG : lot fixe, comme SABR. L'echelle de TP retire des pourcentages de la
        # position INITIALE, donc la taille ne doit pas dependre du solde du compte.
        if "INDODOG" in channel_source: return FIXED_LOT_INDODOG, 0.0

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

    # (#3) Risque imposé (ex: SABR hors-or) -> prime sur la logique par classe/canal
    if risk_override is not None:
        base_risk_pct = risk_override

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

async def close_partial(pos, pct, tp_label, pips_gained, fixed_lot=None, etape=None, pips_tp=None):
    """tp_label sert aux alertes Telegram et au journal -- du texte lisible, libre.
    etape/pips_tp servent au commentaire MT5, qui lui est standardise et court (#100).
    Deux publics differents, deux formats : c'est voulu."""
    # 1. SÉCURITÉ PETITS COMPTES
    if round(pos.volume, 2) <= 0.01:
        return True
        
    # 2. CALCUL EXACT DU LOT (Avec respect du volume_step du broker)
    info_sym = await run_mt5(mt5.symbol_info, pos.symbol)
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
        "comment": commentaire_mt5(ACTIVE_TRADE_SOURCES.get(pos.ticket, ""), etape, pips_tp),
        "type_filling": mt5.ORDER_FILLING_IOC,
        "deviation": MAX_SLIPPAGE
    }
    
    # --- UTILISATION DU WRAPPER BLOQUANT ---
    res = await run_mt5(_send_order_blocking, req)

    # Journal : on trace le RÉSULTAT de chaque partiel (succès comme échec) -- un partiel
    # raté silencieusement est exactement le genre de chose qu'on cherche à repérer en relisant.
    # (#111) Tous les canaux : les paliers INDODOG / FTS passent aussi par ici.
    _tag_dbg = tag_canal(ACTIVE_TRADE_SOURCES.get(pos.ticket, ""))
    if res is None:
        await noter(_tag_dbg, f"❌ PARTIEL ÉCHOUÉ (timeout MT5) — {tp_label} | ticket {pos.ticket} | {lot:.2f} lots")
    elif res.retcode == mt5.TRADE_RETCODE_DONE:
        await noter(_tag_dbg, f"✅ PARTIEL OK — {tp_label} | ticket {pos.ticket} | {lot:.2f} lots fermés | {pips_gained:.0f} pips")
    else:
        await noter(_tag_dbg, f"❌ PARTIEL REFUSÉ (retcode {res.retcode}) — {tp_label} | ticket {pos.ticket} | {lot:.2f} lots")

    if res is None:
        logger.warning(f"⚠️ Timeout MT5 sur Close Partial {tp_label} (Ticket {pos.ticket})")
        return False # On retourne False pour dire "Ça a raté, réessaie à la seconde suivante !"

    if res.retcode == mt5.TRADE_RETCODE_DONE:
        current_partial = PARTIAL_PROFITS.get(pos.ticket, 0.0)
        PARTIAL_PROFITS[pos.ticket] = current_partial + est_profit

        open_time = heure_client(pos.time)  # (#108)
        src_raw = ACTIVE_TRADE_SOURCES.get(pos.ticket, "INCONNU")
        src_clean = src_raw.replace("CANAL ", "").replace("_", " ")

        day_total, day_realized, day_float = get_accurate_daily_stats()
        
        day_pct = (day_total / daily_start_balance * 100) if daily_start_balance > 0 else 0.0
        icon_day = "🟢" if day_total >= 0 else "🔴"
        
        acc = await run_mt5(mt5.account_info)
        bal = acc.balance if acc else 0.0
        
        await send_channel_alert(f"✅️ **{tp_label} VALIDÉ (+{pips_gained:.0f} pips)**\n\n"
                         f"Symbole: {pos.symbol} ({open_time})\n\n"
                         f"Gain Sécurisé: **+{est_profit:.2f}€** ({lot:.2f} lots)\n\n"
                         f"Source: {src_clean}\n\n"
                         f"📊 **P&L Jour: {icon_day} {day_total:+.2f}€** ({day_pct:+.2f}%)\n"
                         f"   ├─ Encaissé: {day_realized:+.2f}€\n"
                         f"   └─ Flottant: {day_float:+.2f}€\n\n"
                         f"🏦 **Portefeuille: {bal:.2f}€**", src_raw)
        return True

    return False

# (#111) Journal « message reçu -> action du bot » de TOUS les canaux. Successeur de
# debug_mdt.log, qui ne couvrait que MDT : SABR, INDODOG et FTS n'avaient aucune trace dédiée,
# alors que leurs pannes récentes étaient justement de cette nature (annulation SABR jugée sur
# le signal d'un autre, édition MDT qui réécrivait des SL SABR, TP INDODOG non lus).
JOURNAL_CANAUX_FILE = "journal_canaux.log"
JOURNAL_MAX_OCTETS = 5 * 1024 * 1024
JOURNAL_COPIES = 5
NON_COMPRIS_FILE = "signaux_non_compris.log"
# Les écritures viennent de threads (run_bg) : la rotation ne doit jamais se croiser avec une
# écriture, sinon une ligne part dans le fichier qu'on est en train de renommer.
_JOURNAL_VERROU = threading.Lock()


def _ecrire_avec_rotation(chemin, texte, max_octets, copies):
    """(#111) Ajoute `texte` au fichier ; au-delà de max_octets, chemin -> chemin.1 -> ... ->
    chemin.<copies>, le plus ancien étant supprimé. debug_mdt.log et signaux_non_compris.log
    n'avaient aucune limite : sur un VPS qui tourne des mois, ils grossissaient sans fin."""
    with _JOURNAL_VERROU:
        try:
            if os.path.exists(chemin) and os.path.getsize(chemin) + len(texte.encode("utf-8")) > max_octets:
                for i in range(copies - 1, 0, -1):
                    if os.path.exists(f"{chemin}.{i}"):
                        os.replace(f"{chemin}.{i}", f"{chemin}.{i + 1}")
                os.replace(chemin, f"{chemin}.1")
        except Exception as e:
            # Une rotation ratée (fichier ouvert ailleurs sous Windows) ne doit pas faire
            # perdre la ligne : on écrit quand même, le fichier dépassera un peu.
            logger.error(f"⚠️ Rotation {chemin} : {e}")
        try:
            with open(chemin, "a", encoding="utf-8") as f:
                f.write(texte)
        except Exception as e:
            logger.error(f"⚠️ Journal {chemin} : {e}")


# (#111) Canaux suivis, identifiés par leur groupe Telegram. Les deux sans constante nommée
# suivent l'ordre documenté de CHANNEL_LIST (SPACE puis LIGUE).
_TAG_PAR_CHAT = {
    ID_MDT_VIP: "MDT_VIP", ID_MDT_FREE: "MDT_FREE",
    ID_SABR_VIP: "SABR_VIP", ID_SABR_FREE: "SABR_FREE", ID_CANAL_SABR_COPIE: "SABR_COPIE",
    ID_INDODOG: "INDODOG", ID_FTS: "FTS", ID_CANAL_PERSO: "PERSO",
    -1002684710444: "SPACE", -1001753570693: "LIGUE",
}


def tag_canal(source=None, chat_id=None):
    """(#111) Étiquette courte d'un canal pour le journal : MDT_VIP, SABR_VIP, INDODOG...

    Depuis le groupe Telegram quand on l'a (message reçu, édité, supprimé), sinon depuis la
    source du trade ("CANAL SABR_VIP_BLIND" -> SABR_VIP). Les sous-types SABR sont regroupés,
    comme dans les stats : on cherche un canal dans le journal, pas un type de signal.
    """
    if chat_id in _TAG_PAR_CHAT:
        return _TAG_PAR_CHAT[chat_id]
    s = str(source or "").upper().replace("CANAL ", "").strip()
    if "SABR" in s:
        return "SABR_COPIE" if "COPIE" in s else ("SABR_VIP" if "VIP" in s else "SABR_FREE")
    for nom in ("MDT_VIP", "MDT_FREE", "INDODOG", "FTS", "SPACE", "LIGUE", "PERSO"):
        if nom in s:
            return nom
    return s.replace(" ", "_") or (f"CHAT_{chat_id}" if chat_id else "?")


def journal_canal(tag, msg):
    """Journal chronologique des canaux : messages reçus ET actions du bot, dans UN SEUL
    fichier pour pouvoir relire la séquence complète et vérifier que chaque message a bien
    été suivi de l'effet attendu (ou de la raison de son absence).

    Tous les canaux partagent le fichier volontairement : chaque ligne porte son étiquette
    ([MDT_VIP], [SABR_VIP], [INDODOG]...), et les avoir dans le même ordre chronologique est
    justement ce qui permet de corréler un message avec l'action qu'il a déclenchée -- y
    compris d'un canal à l'autre. (#111) Format des lignes MDT inchangé."""
    _ecrire_avec_rotation(JOURNAL_CANAUX_FILE,
                          f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] [{tag}] {msg}\n",
                          JOURNAL_MAX_OCTETS, JOURNAL_COPIES)


async def noter(tag, msg):
    """(#111) journal_canal() depuis du code asynchrone (écriture disque hors boucle)."""
    await run_bg(journal_canal, tag, msg)


def log_unclear_signal(raw_text, tickets):
    """(#20) Trace les messages de suivi (reply à un signal) qu'on n'a pas su interpréter,
    pour revue manuelle -- ex: 'JE GARDE QUE CELLE DE X', messages purement informatifs/
    optionnels ('les traders satisfaits peuvent fermer'), nouveaux tours de phrase du canal.
    (#111) Avec rotation, comme le journal des canaux."""
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    _ecrire_avec_rotation(NON_COMPRIS_FILE,
                          f"[{ts}] Ticket(s) {tickets} -- aucune action prise :\n{raw_text}\n{'-'*60}\n",
                          2 * 1024 * 1024, 2)

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

def split_zone_lots(total_vol, step, equal, pct1, pct2, n_ordres=3):
    """(#85/#102) Decoupe un volume total en N ordres etages sur une zone.

    N vaut 3 par defaut (SABR historique, INDODOG, FTS) et peut descendre a 2 ou 1 quand le
    reglage SABR_ZONE_BORNES ne retient qu'une partie des bornes.

    INVARIANT : la somme des N lots vaut TOUJOURS exactement total_vol. On raisonne en UNITES
    du pas broker (jamais en flottants), on arrondit chaque part vers le BAS, puis on donne les
    unites restantes aux PREMIERS ordres (debut > milieu > fin).
    Ex. 0.13 lot, pas 0.01, 3 ordres : 0.05 / 0.04 / 0.04 -- jamais 0.05 / 0.05 / 0.05.

    Avant, le mode manuel remontait au pas minimum tout ordre trop petit APRES le calcul, ce
    qui gonflait le total : 0.03 lot reparti en 90/5/5 posait 0.03 + 0.01 + 0.01 = 0.05, soit
    66 % de plus que le lot regle au dashboard.

    L'arrondi suit le nombre de decimales du pas broker : un `round(x, 2)` en dur perdait ou
    inventait du volume chez un courtier au pas de 0.001.
    """
    n = max(1, min(3, int(n_ordres)))
    if step <= 0:
        step = 0.01
    dec = len(str(step).split(".")[1]) if "." in str(step) else 0
    dec = max(0, min(8, dec))
    n_units = int(round(total_vol / step))

    if n_units <= 0:
        return [0.0] * n
    # Moins d'unites que d'ordres : on ne finance que les premiers plutot que de forcer chaque
    # montant au pas minimum, ce qui gonflerait le total.
    if n_units < n:
        return [round(step, dec)] * n_units + [0.0] * (n - n_units)

    if equal:
        base_u, rest_u = divmod(n_units, n)
        units = [base_u + (1 if i < rest_u else 0) for i in range(n)]
    else:
        # Securite : des pourcentages valides et positifs meme si un reglage dashboard mal
        # saisi a ete sauvegarde. A 1 ordre la question ne se pose pas, il prend tout.
        if n == 1:
            pcts = [1.0]
        elif n == 2:
            p1 = max(0.01, min(0.99, pct1))
            pcts = [p1, 1.0 - p1]
        else:
            p1 = max(0.01, min(0.98, pct1))
            p2 = max(0.01, min(0.98 - p1, pct2))
            pcts = [p1, p2, max(0.0, 1.0 - p1 - p2)]

        units = [int(n_units * p) for p in pcts]
        # 1. Chaque ordre doit valoir au moins une unite.
        units = [u if u > 0 else 1 for u in units]
        # 2. Si ce plancher a fait deborder, on REPREND sur le plus gros -- jamais en
        #    laissant le total depasser le lot demande.
        while sum(units) > n_units:
            j = units.index(max(units))
            if units[j] <= 1:
                break
            units[j] -= 1
        # 3. Le reliquat d'arrondi va aux PREMIERS ordres (debut > milieu > fin).
        i = 0
        while sum(units) < n_units:
            units[i % n] += 1
            i += 1
    return [round(u * step, dec) for u in units]


# ==========================================
# 🪜 COMPTAGE DES CANAUX A ECHELLE (#87/#90)
# ==========================================
# Une ligne par SIGNAL (jamais par ordre : une entree en zone en pose trois). On note jusqu'ou
# le prix est reellement alle -- palier le plus loin atteint -- et comment le signal s'est
# termine. C'est la matiere pour regler les pourcentages de l'echelle sur des donnees : si
# 90 % des signaux touchent le TP1 mais 20 % seulement le TP3, vendre gros au TP1 se defend ;
# si la plupart vont au bout, c'est l'inverse.
# Un fichier par canal : INDODOG et FTS n'ont ni le meme rythme ni la meme echelle, les
# melanger rendrait les deux illisibles.

LADDER_STATS_FILES = {
    "CANAL INDODOG": INDO_STATS_FILENAME,
    "CANAL FTS": FTS_STATS_FILENAME,
}


def _ladder_key(src):
    """Normalise une source en cle de canal a echelle. None si ce n'est pas l'un d'eux."""
    s = src or ""
    if "FTS" in s:
        return "CANAL FTS"
    if "INDODOG" in s:
        return "CANAL INDODOG"
    return None


def _ladder_stats_resume(src):
    """Agrege les signaux d'un canal : combien ont atteint TP1, TP2... et comment ils finissent.

    Les paliers sont CUMULATIFS (un signal qui atteint le TP3 compte aussi dans TP1 et TP2),
    c'est ce qui donne directement le taux de survie palier par palier.
    """
    cle = _ladder_key(src)
    sigs = [s for s in LADDER_STATS.get(cle, {}).values() if isinstance(s, dict)]
    paliers, issues, en_cours = {}, {}, 0
    for s in sigs:
        for n in range(1, int(s.get("tp_atteint") or 0) + 1):
            k = "tp%d" % n
            paliers[k] = paliers.get(k, 0) + 1
        issue = s.get("issue")
        if not issue:
            en_cours += 1
        else:
            issues[issue] = issues.get(issue, 0) + 1
    return {
        "signaux": len(sigs),
        "termines": len(sigs) - en_cours,
        "en_cours": en_cours,
        "paliers_atteints": dict(sorted(paliers.items(), key=lambda kv: int(kv[0][2:]))),
        "issues": dict(sorted(issues.items())),
    }


def save_ladder_stats(src):
    cle = _ladder_key(src)
    if not cle:
        return
    chemin = LADDER_STATS_FILES[cle]
    try:
        with open(chemin, "w", encoding="utf-8") as f:
            json.dump({"canal": cle.replace("CANAL ", ""),
                       "resume": _ladder_stats_resume(cle),
                       "signaux": LADDER_STATS[cle]}, f, indent=2, ensure_ascii=False)
    except Exception as e:
        logger.error(f"⚠️ Ecriture {chemin} : {e}")


def load_ladder_stats():
    """Recharge les deux fichiers au demarrage. Un fichier absent est normal (canal jamais
    utilise, ou client qui n'a pas ce pack)."""
    for cle, chemin in LADDER_STATS_FILES.items():
        if not os.path.exists(chemin):
            continue
        try:
            with open(chemin, "r", encoding="utf-8") as f:
                LADDER_STATS[cle] = json.load(f).get("signaux", {}) or {}
            logger.info("🪜 Comptage %s restaure (%d signaux)"
                        % (cle.replace("CANAL ", ""), len(LADDER_STATS[cle])))
        except Exception as e:
            logger.error(f"⚠️ Lecture {chemin} : {e}")
            LADDER_STATS[cle] = {}

    # (#104) Rattrapage des signaux restes "en cours" alors que plus rien ne les porte. Deux
    # causes : l'annulation d'un ordre jamais declenche ne notait aucune issue (corrige a la
    # source), et une cloture survenue bot eteint n'est vue par personne. Appele APRES
    # load_channel_data(), donc ACTIVE_TRADE_SIGNALS reflete ce qui vit encore.
    # Depart : aucun palier atteint = le signal n'a jamais rien ouvert -> ANNULE. Un palier
    # atteint prouve qu'une position a existe, mais on ne sait plus comment elle est sortie.
    vivants = set(ACTIVE_TRADE_SIGNALS.values())
    for cle in LADDER_STATS_FILES:
        repares = 0
        for sig_key, s in LADDER_STATS.get(cle, {}).items():
            if not isinstance(s, dict) or s.get("issue") or sig_key in vivants:
                continue
            s["issue"] = "ANNULE" if int(s.get("tp_atteint") or 0) == 0 else "AUTRE"
            s["ferme"] = s.get("ferme") or datetime.now().strftime("%Y-%m-%d %H:%M")
            repares += 1
        if repares:
            logger.warning("🪜 %s : %d signaux sans issue rattrapes (plus aucun ordre ni "
                           "position ne les porte)" % (cle.replace("CANAL ", ""), repares))
            save_ladder_stats(cle)


def ladder_stats_open(src, sig_key, symbol, nb_tp):
    """Nouveau signal. Idempotent : les 3 ordres d'une zone partagent le meme signal_id."""
    cle = _ladder_key(src)
    if not cle or not sig_key or str(sig_key) in LADDER_STATS[cle]:
        return
    LADDER_STATS[cle][str(sig_key)] = {
        "symbole": symbol,
        "ouvert": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "nb_tp": int(nb_tp),
        "tp_atteint": 0,
        "issue": None,
        "ferme": None,
    }


def ladder_stats_tp(src, sig_key, idx):
    """Palier franchi. On ne retient que le plus loin : les paliers sont ordonnes."""
    cle = _ladder_key(src)
    s = LADDER_STATS.get(cle, {}).get(str(sig_key)) if cle else None
    if s and int(idx) > int(s.get("tp_atteint") or 0):
        s["tp_atteint"] = int(idx)


def ladder_stats_close(src, sig_key, issue):
    """Issue definitive du signal, ecrite une seule fois (le premier appel fait foi)."""
    cle = _ladder_key(src)
    s = LADDER_STATS.get(cle, {}).get(str(sig_key)) if cle else None
    if not s or s.get("issue"):
        return
    s["issue"] = issue
    s["ferme"] = datetime.now().strftime("%Y-%m-%d %H:%M")
    # Le dernier TP est ferme par MT5 lui-meme (champ tp de l'ordre), il ne passe donc jamais
    # par la boucle des paliers : sans ca, un signal alle au bout resterait compte a nb_tp - 1.
    if issue == "TP_FINAL":
        s["tp_atteint"] = int(s.get("nb_tp") or s.get("tp_atteint") or 0)


async def _ladder_close_si_plus_rien(src, sig_key):
    """(#104) Note un signal a echelle comme ANNULE si plus AUCUN ordre ni position ne le porte.

    Un signal dont les trois ordres expirent sans jamais se declencher ne passe jamais par
    check_closed_positions() -- il n'y a pas de position a fermer. Sa ligne restait donc a
    `issue: None`, c'est-a-dire comptee "en cours" indefiniment : 19 des 72 signaux INDODOG
    etaient dans ce cas alors que plus rien n'etait ouvert.

    ladder_stats_close() ecrit la premiere issue et ignore les suivantes : appeler ceci apres
    l'annulation d'un ordre alors qu'une position du meme signal vit encore (cas normal au TP1)
    n'ecrase donc jamais le vrai resultat -- et le garde-fou ci-dessous l'evite deja.
    """
    if not _ladder_key(src) or not sig_key:
        return
    if any(sg == sig_key for sg in ACTIVE_TRADE_SIGNALS.values()):
        return          # il reste un ordre en attente ou une position sur ce signal
    ladder_stats_close(src, sig_key, "ANNULE")
    # (#110) Plus rien ne porte ce signal : son echelle figee ne servira plus.
    INDO_ECHELLE_BY_SIGNAL.pop(sig_key, None)
    await run_bg(save_ladder_stats, src)


def _price_touched_recently_blocking(symbol, target, minutes):
    """(#88) True si une bougie M1 des `minutes` dernieres minutes a englobe `target`.

    On lit les BOUGIES et pas le tick courant : entre deux passages du bot, le prix peut
    parfaitement avoir touche le niveau et en etre reparti sans qu'aucun tick observe ne le
    montre. Le high/low d'une M1 capture ce passage, un tick ponctuel non.

    Retourne None si MT5 ne fournit pas l'historique -- l'appelant traite ce cas comme
    "pas de contact", donc en ordre en attente : on ne prend jamais un marche par defaut.
    """
    try:
        rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M1, 0, int(minutes) + 1)
    except Exception as e:
        logger.warning(f"⚠️ Historique M1 indisponible sur {symbol} : {e}")
        return None
    if rates is None or len(rates) == 0:
        return None
    for r in rates:
        if float(r["low"]) <= target <= float(r["high"]):
            return True
    return False


async def indo_should_market(symbol, target, curr_price, pt, div):
    """(#88) INDODOG : faut-il entrer au MARCHE plutot qu'en ordre en attente ?

    Les deux conditions doivent etre vraies (cf. INDO_MARKET_MAX_PIPS / _LOOKBACK_MIN).
    Retourne (marche: bool, ecart_pips: float, contact: bool|None) -- les deux dernieres
    valeurs ne servent qu'au journal, pour comprendre apres coup pourquoi le bot a tranche
    dans un sens ou dans l'autre.
    """
    ecart = (abs(target - curr_price) / pt) / div
    if ecart > INDO_MARKET_MAX_PIPS:
        return False, ecart, None          # trop loin : inutile d'interroger l'historique
    contact = await run_mt5(_price_touched_recently_blocking, symbol, target,
                            INDO_MARKET_LOOKBACK_MIN)
    return (contact is True), ecart, contact


def _indo_log_entree(canal, target, ecart, contact, marche):
    """Journal d'une decision d'entree sur un canal a paliers, en clair."""
    if contact is None and ecart > INDO_MARKET_MAX_PIPS:
        motif = "trop loin (> %.0f pips)" % INDO_MARKET_MAX_PIPS
    elif contact is None:
        motif = "historique M1 indisponible"
    elif contact:
        motif = "contact < %d min" % INDO_MARKET_LOOKBACK_MIN
    else:
        motif = "aucun contact < %d min" % INDO_MARKET_LOOKBACK_MIN
    logger.info("🪜 %s entree %s : ecart %.1f pips, %s -> %s"
                % (canal, target, ecart, motif, "MARCHE" if marche else "ORDRE EN ATTENTE"))

async def execute_trade(symbol, action, sl, tp, price_signal, message_date, channel_source, custom_lot_modifier=1.0, tp1_price=None, tp_ladder=None):
    # (#32) Verrou de pack centralisé : execute_trade() est le SEUL point d'entrée vers un vrai
    # ordre MT5, quel que soit l'appelant (main_handler, edit_handler, /reactivate, UPDATE_SL...).
    # Avant, ce check n'existait que dans main_handler -- edit_handler (signal "blind" complété
    # par une édition de message) et /reactivate (rejoue un signal déjà en mémoire) pouvaient
    # donc faire exécuter un trade hors pack payé ou sur un canal mute en contournant main_handler.
    _tag_x = tag_canal(channel_source)   # (#111) chaque refus ci-dessous est noté au journal
    if any(m in channel_source for m in MUTED_CHANNELS) or any(m in channel_source for m in PACK_LOCKED_CHANNELS):
        logger.warning(f"🚫 TRADE BLOQUÉ (execute_trade) : canal {channel_source} mute ou hors pack.")
        await noter(_tag_x, f"🔇 AUCUN ORDRE ({symbol}) : canal coupé (mute) ou hors du pack")
        return None

    # (#stats) Un appel = un signal, même s'il débouche sur plusieurs ordres (zone SABR en
    # place 3). Tous les tickets créés ci-dessous partagent cet identifiant, ce qui permet de
    # compter 1 trade et non 3 dans les stats par canal.
    signal_id = f"{symbol}-{int(datetime.now().timestamp() * 1000)}"

    # -----------------------------------------------------------
    # 🛠️ GESTION SPECIALE: MISE A JOUR SL SABR
    # -----------------------------------------------------------
    if action == "UPDATE_SL" and "SABR" in channel_source:
        try:
            new_sl = float(price_signal)
            count = 0
            # A. Positions ouvertes
            positions = await run_mt5(mt5.positions_get, symbol="XAUUSD")
            if positions:
                for pos in positions:
                    if pos.magic == MAGIC_NUMBER and pos.ticket in ACTIVE_TRADE_SOURCES:
                        if "SABR" in ACTIVE_TRADE_SOURCES[pos.ticket]:
                            req = {"action": mt5.TRADE_ACTION_SLTP, "position": pos.ticket, "sl": new_sl, "tp": pos.tp}
                            res = await run_mt5(_send_order_blocking, req)
                            # (#44) res peut être None (timeout MT5) -- .retcode dessus levait une
                            # AttributeError avalée par le bare except plus bas, sans alerter l'admin.
                            if res is None:
                                logger.warning(f"⚠️ MT5 Timeout (UPDATE_SL SABR) sur ticket {pos.ticket}")
                            elif res.retcode == mt5.TRADE_RETCODE_DONE:
                                count += 1

            # B. Ordres en attente
            orders = await run_mt5(mt5.orders_get, symbol="XAUUSD")
            if orders:
                for order in orders:
                    if order.magic == MAGIC_NUMBER:
                        req = {"action": mt5.TRADE_ACTION_MODIFY, "order": order.ticket, "sl": new_sl, "tp": order.tp}
                        await run_mt5(_send_order_blocking, req)

            if count > 0: await send_alert(f"🔄 **SL SABR MIS À JOUR**\nNouveau SL: {new_sl}\nTrades modifiés: {count}")
            await noter(_tag_x, f"🔄 SL SABR MIS À JOUR ({symbol}) — nouveau SL {new_sl} | "
                                f"{count} position(s) modifiée(s)")
            return None
        except Exception as e:
            logger.error(f"⚠️ Erreur UPDATE_SL SABR : {e}")
            return None

    # 1. Vérification Drawdown (Disjoncteur)
    # (#DD) On appelle check_daily_max_loss() pour mettre à jour security_triggered si une
    # nouvelle limite vient d'être franchie, mais le blocage lui-même se base sur le verrou
    # PERSISTANT (security_triggered), pas sur son retour direct -- sinon relever le Max DD
    # suffirait à lui seul à réautoriser les trades, sans déverrouillage manuel explicite.
    check_daily_max_loss()
    if security_triggered:
        await send_alert("⛔ STOP: Sécurité Drawdown active -- augmente le Max DD puis déverrouille manuellement pour reprendre.")
        await noter(_tag_x, f"⛔ AUCUN ORDRE ({symbol}) : sécurité drawdown active")
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
        await noter(_tag_x, f"❌ AUCUN ORDRE : symbole {symbol} inconnu de MT5")
        return None
        
    pt = s_info.point
    div = get_pip_divisor(symbol)

    # (#11) Filtre spread : ignore le signal si le spread actuel est trop large
    # (evite les entrees en conditions degradees -- ex: news, faible liquidite)
    current_spread_pips = s_info.spread / div
    if current_spread_pips > MAX_SPREAD_PIPS:
        logger.warning(f"⏸️ Signal ignoré ({symbol}) : spread trop large ({current_spread_pips:.1f} > {MAX_SPREAD_PIPS} pips)")
        await noter(_tag_x, f"⏸️ AUCUN ORDRE ({symbol}) : spread trop large "
                            f"({current_spread_pips:.1f} > {MAX_SPREAD_PIPS} pips)")
        return None

    tick = mt5.symbol_info_tick(symbol)
    if not tick:
        await noter(_tag_x, f"❌ AUCUN ORDRE ({symbol}) : aucune cotation (marché fermé ?)")
        return None
    
    curr_price = tick.ask if action == "ACHAT" else tick.bid

    # On récupère les positions et ordres en cours UNE SEULE FOIS pour tous les anti-doublons
    existing_pos = await run_mt5(mt5.positions_get, symbol=symbol)
    existing_orders = await run_mt5(mt5.orders_get, symbol=symbol)

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
            await noter(_tag_x, f"🛡️ AUCUN ORDRE ({symbol} {action}) : doublon SABR "
                                f"(trade déjà ouvert à moins de 100 pips)")
            return SABR_DUPLICATE  # (#4) signale au handler : pas de numéro, pas de spam

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
            await noter(_tag_x, f"🛡️ AUCUN ORDRE ({symbol} {action}) : déjà pris via l'autre canal MDT")
            return None

    # 3. Préparation des Lots et Prix (SABR vs CLASSIC)
    trades_to_execute = [] # Liste de tuples (TargetPrice, FixedLot) -> Si TargetPrice est None = Market
    
    signal_defaut = False   # (#104) vrai si le repli SL/TP a servi (INDODOG et FTS)
    if is_ladder_channel(channel_source):
        # (#84/#90) Canaux a echelle de TP : lot fixe. INDODOG peut donner une ZONE, traitee
        # comme SABR (trois ordres etages) ; FTS donne toujours un prix unique, la branche zone
        # ci-dessous lui est donc naturellement inerte. Les paliers de TP porteront ensuite sur
        # le volume TOTAL reellement pris (cf. gestion par signal dans monitor_positions), et
        # non sur chaque ordre isolement.
        _nom_canal = channel_source.replace("CANAL ", "")
        total_vol = round(ladder_fixed_lot(channel_source) * custom_lot_modifier, 2)
        if total_vol < 0.01: total_vol = 0.01

        # (#104) Repli des canaux a echelle : SL et echelle de TP par defaut quand le signal
        # n'en donne pas. INDODOG et FTS ont la meme exposition -- un signal illisible partait
        # sans stop, ou avec un TP a 0 -- et le repli ne depend d'aucune particularite de canal.
        # Le prix de reference est la borne PROCHE d'une zone, sinon le prix annonce, sinon le
        # marche -- jamais le milieu d'une zone, qui donnerait un SL trop court d'un cote.
        _ref = curr_price
        if price_signal:
            try:
                _ref = float(str(price_signal).split("|")[0].replace(",", "."))
            except Exception:
                _ref = curr_price
        if sl_val == 0.0:
            _d_sl = INDO_SL_DEFAUT_PIPS * pt * div
            sl_val = _ref - _d_sl if action == "ACHAT" else _ref + _d_sl
            sl_val = round(sl_val / s_info.point) * s_info.point
            logger.warning("🛑 %s sans SL : repli a %d pips -> %s"
                           % (_nom_canal, INDO_SL_DEFAUT_PIPS, sl_val))
            await send_channel_alert(
                ("⚠️ **%s : SL ABSENT DU SIGNAL**" % _nom_canal) + chr(10) + chr(10) +
                ("Stop posé par défaut à %d pips (%s)." % (INDO_SL_DEFAUT_PIPS, sl_val)),
                channel_source)
        if not tp_ladder:
            tp_ladder = []
            for _p in INDO_TP_DEFAUT_PALIERS:
                _v = _ref + _p * pt * div if action == "ACHAT" else _ref - _p * pt * div
                tp_ladder.append(round(_v / s_info.point) * s_info.point)
            signal_defaut = True
            logger.warning("🎯 %s sans TP : echelle par defaut %s pips -> %s"
                           % (_nom_canal, INDO_TP_DEFAUT_PALIERS, tp_ladder))
            await send_channel_alert(
                ("⚠️ **%s : AUCUN TP DANS LE SIGNAL**" % _nom_canal) + chr(10) + chr(10) +
                ("Échelle par défaut : %s pips." % ", ".join(str(x) for x in INDO_TP_DEFAUT_PALIERS)) + chr(10) +
                ("Mise à BE anticipée dès +%d pips." % INDO_BE_DEFAUT_PIPS),
                channel_source)

        if price_signal and "|" in str(price_signal):
            parts_i = str(price_signal).split("|")
            try:
                pa, pb = float(parts_i[0]), float(parts_i[1])
            except Exception:
                pa = pb = curr_price
            # Normalisation : p1 = borne proche du marche (gros lot), p2 = borne eloignee.
            hi_i, lo_i = max(pa, pb), min(pa, pb)
            p1_i, p2_i = (hi_i, lo_i) if action == "ACHAT" else (lo_i, hi_i)
            pmid_i = (p1_i + p2_i) / 2

            # (#85) Meme decoupage que SABR, toggle Manuel/Egal du dashboard compris. L'ancien
            # bloc reassignait la variable de boucle (`for _v in (...)`), donc le garde-fou
            # "au moins un pas par ordre" ne s'appliquait a rien du tout.
            step_i = s_info.volume_step if s_info.volume_step > 0 else 0.01
            l1, l2, l3 = split_zone_lots(total_vol, step_i, SABR_ZONE_EQUAL_SPLIT,
                                         SABR_ZONE_PCT1, SABR_ZONE_PCT2)

            # (#88) Premiere borne au marche seulement si on est proche ET que le cours a
            # reellement touche ce prix recemment. Les deux autres bornes restent en attente.
            marche_i, ecart_i, contact_i = await indo_should_market(symbol, p1_i, curr_price, pt, div)
            _indo_log_entree(_nom_canal, p1_i, ecart_i, contact_i, marche_i)
            px1_i = None if marche_i else p1_i
            if l1 > 0: trades_to_execute.append((px1_i, l1))
            if l2 > 0: trades_to_execute.append((pmid_i, l2))
            if l3 > 0: trades_to_execute.append((p2_i, l3))
        else:
            tgt_i = None
            if price_signal:
                try: tgt_i = float(str(price_signal).replace(",", "."))
                except Exception: pass
            if tgt_i:
                # (#88) Meme regle que pour la zone : proximite ET contact recent.
                marche_s, ecart_s, contact_s = await indo_should_market(symbol, tgt_i, curr_price, pt, div)
                _indo_log_entree(_nom_canal, tgt_i, ecart_s, contact_s, marche_s)
                if marche_s: tgt_i = None          # None = ordre au marche
            trades_to_execute.append((tgt_i, total_vol))

        logger.info("🪜 %s : %.2f lot(s) -> %s" % (_nom_canal, total_vol, trades_to_execute))

    elif "SABR" in channel_source:
        # --- A. Détermination du volume TOTAL ---
        sym_up_sabr = symbol.upper()
        is_gold_sabr = "XAU" in sym_up_sabr or "GOLD" in sym_up_sabr

        if is_gold_sabr:
            # OR : lots fixes historiques (SABR Free / VIP / Copie)
            total_vol = FIXED_LOT_SABR_FREE
            if "VIP" in channel_source or "COPIE" in channel_source: total_vol = FIXED_LOT_SABR_VIP
        else:
            # (#3) HORS-OR : sizing par risque % basé sur le SL du signal.
            # Référence d'entrée = borne "gros lot" (p1), la plus éloignée du SL -> risque conservateur.
            ref_entry = curr_price
            if price_signal:
                try:
                    ref_entry = float(price_signal.split('|')[0].replace(',', '.'))
                except: pass
            risk_lot, _ = await get_safe_lot_size(
                symbol, action, sl_val, ref_entry,
                channel_source=None, risk_override=RISK_PERCENT_SABR_OTHER
            )
            # Fallback si MT5 ne peut pas calculer (paire inconnue / SL manquant non géré par la table)
            total_vol = risk_lot if (risk_lot and risk_lot > 0) else FIXED_LOT_OTHER
            log(f"📐 SABR hors-or {symbol} : lot risque {RISK_PERCENT_SABR_OTHER*100:.2f}% -> {total_vol:.2f}", important=True)

        # --- APPLICATION DU MULTIPLICATEUR DE RÉACTIVATION ---
        total_vol = round(total_vol * custom_lot_modifier, 2)
        # Sécurité minimale pour pouvoir poser au moins un ordre
        if total_vol < 0.01: total_vol = 0.01

        # --- B. Logique BLIND (ATTENTE) ---
        if "BLIND" in channel_source:
            await send_channel_alert(f"⏳ **SIGNAL SABR EN APPROCHE**\n\n"
                             f"Action : {action} {symbol} (À l'aveugle)\n\n"
                             f"Le bot est prêt et attend le prix exact.\n(Délai de veille: 3 minutes max...)", channel_source)
            await noter(_tag_x, f"⏳ SIGNAL AVEUGLE ({action} {symbol}) : aucun ordre tant que le "
                                f"canal n'a pas donné son prix (édition du message attendue)")
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

            # --- NORMALISATION DIRECTIONNELLE (#6) ---
            # La répartition gros lot -> petit lot ne doit PAS dépendre de l'ordre d'écriture
            # des prix ("buy 4005-4000" et "buy 4000-4005" doivent donner le même découpage).
            # p1 = borne "début/gros lot" (proche du marché) ; p2 = borne "fin/petit lot" (loin).
            # ACHAT  -> gros lot sur le prix le plus HAUT (zone sous le marché, touchée par le haut).
            # VENTE  -> gros lot sur le prix le plus BAS  (zone au-dessus, touchée par le bas).
            hi, lo = max(p1, p2), min(p1, p2)
            p1, p2 = (hi, lo) if action == "ACHAT" else (lo, hi)

            p_mid = (p1 + p2) / 2
            
            if sl_val == 0.0:
                dist_sl = 100 * pt * (div/10)
                sl_val = p2 - dist_sl if action == "ACHAT" else p2 + dist_sl

            # --- BORNES RETENUES (#102) ---
            # Le client choisit au dashboard lesquelles des trois bornes il veut prendre. Les
            # prix sont toujours calculés tous les trois : c'est la SÉLECTION qui change, pas
            # la géométrie de la zone. On conserve l'ordre proche -> loin, dont dépend la
            # priorité d'attribution des lots.
            bornes_dispo = [("debut", p1), ("milieu", p_mid), ("fin", p2)]
            bornes = [(nom, prix) for nom, prix in bornes_dispo if nom in SABR_ZONE_BORNES]
            if not bornes:
                # Réglage vide ou corrompu : on retombe sur les trois bornes plutôt que de ne
                # rien poser du tout. Un signal valide ne doit jamais être perdu sur un réglage.
                logger.warning("⚠️ SABR_ZONE_BORNES vide, repli sur les 3 bornes.")
                bornes = bornes_dispo

            # --- RÉPARTITION : MANUELLE (% réglables au dashboard) OU ÉGALE (toggle) ---
            # (#85) Découpage centralisé dans split_zone_lots() : la somme des ordres vaut
            # toujours EXACTEMENT le lot réglé, en mode égal comme en mode manuel. Les unités
            # qui ne tombent pas juste vont aux premiers ordres (début > milieu > fin).
            min_step_zone = s_info.volume_step if s_info.volume_step > 0 else 0.01
            lots_zone = split_zone_lots(total_vol, min_step_zone, SABR_ZONE_EQUAL_SPLIT,
                                        SABR_ZONE_PCT1, SABR_ZONE_PCT2, n_ordres=len(bornes))

            # (#102) L'entrée au marché porte sur la borne SÉLECTIONNÉE la plus proche du
            # marché, et non plus sur "début" en dur : si le client ne retient que milieu+fin,
            # comparer à une borne qu'il n'utilise pas n'aurait aucun sens.
            for idx_b, (nom_b, prix_b) in enumerate(bornes):
                lot_b = lots_zone[idx_b] if idx_b < len(lots_zone) else 0.0
                if lot_b <= 0:
                    continue
                if idx_b == 0:
                    dist_b = (abs(prix_b - curr_price) / pt) / div
                    trades_to_execute.append((None if dist_b <= 30 else prix_b, lot_b))
                else:
                    trades_to_execute.append((prix_b, lot_b))

            logger.info("🪓 Zone SABR : bornes %s -> lots %s"
                        % ([n for n, _ in bornes], lots_zone))
        
        # === ⚙️ DEBUG SABR (EXÉCUTION) ===
        logger.info(f"\n{'='*50}\n"
                    f"⚙️ DIAGNOSTIC SABR (ÉTAPE 2 : EXÉCUTION)\n"
                    f"💰 Volume Total Calculé : {total_vol:.2f} lots\n"
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

        # (#78) Dernier filtre avant l'envoi : la taille calculée doit tenir dans la marge
        # libre. Sans ça, un lot trop gros partait quand même et MT5 le rejetait ("No money"),
        # le signal étant alors purement perdu.
        lot = await run_mt5(cap_lot_to_free_margin, symbol, action, lot, final_px, otype)
        if lot < 0.01: continue

        # --- 🧮 NOUVEAU : CALCUL ET LOG DU RISQUE EXACT ---
        projected_loss_eur = 0.0
        if final_sl > 0.0:
            try:
                loss = mt5.order_calc_profit(otype, symbol, lot, final_px, final_sl)
                if loss: projected_loss_eur = abs(loss)
            except: pass
            
        risk_str = f"{applied_risk_pct*100:.2f}%" if applied_risk_pct > 0 else "Fixe"
        
        logger.info("\n" + "="*50 +
                    f"\n🧮 DÉTAIL DU CALCUL DE RISQUE [{symbol}]\n"
                    f"Action      : {action} à {final_px}\n"
                    f"Stop Loss   : {final_sl}\n"
                    f"Risque Visé : {risk_str}\n"
                    f"Lot Final   : {lot:.2f} lots\n"
                    f"Perte Est.  : -{projected_loss_eur:.2f} €\n" +
                    "="*50 + "\n")

        # Calcul SL Auto Classique (Risk Based) pour les autres canaux
        #
        # (#104) Le pourcentage était figé à 0,5 % du solde, sans aucun rapport avec le risque
        # réglé pour le canal. Un signal MDT FREE sans SL prenait donc son lot sur 0,10 % puis
        # posait son stop à 0,5 % : cinq fois le risque demandé, en silence. Vu le 2026-09-01
        # sur le ticket 532870669 -- stop à 502 pips, -511 € quand toutes les autres pertes du
        # mois tenaient sous 90 €. On reprend le risque réellement appliqué à ce trade ; 0,5 %
        # ne sert plus que de secours quand le lot est fixe et qu'aucun pourcentage n'existe.
        if final_sl == 0.0 and lot > 0 and "SABR" not in channel_source:
             try:
                 pct_sl = applied_risk_pct if applied_risk_pct > 0 else 0.005
                 risk_eur = mt5.account_info().balance * pct_sl
                 val_pt = s_info.trade_tick_value * lot
                 if val_pt > 0:
                     dist = (risk_eur / val_pt) * s_info.trade_tick_size
                     final_sl = final_px - dist if action == "ACHAT" else final_px + dist
                     final_sl = round(final_sl / s_info.point) * s_info.point
                     logger.warning("🛑 %s sans SL : stop auto a %.0f pips (%.2f %% du solde) -> %s"
                                    % (channel_source, abs(final_px - final_sl) / (pt * div),
                                       pct_sl * 100, final_sl))
             except: pass

        try: final_tp = float(tp)
        except: final_tp = 0.0
        
        # (#101) MDT : un signal sans TP partait sans aucune cible. On en pose une a
        # MDT_TP_DEFAUT_PIPS le temps que le canal en annonce une -- les paliers de partiels
        # (60/140/200/300 pips) continuent de s'appliquer par-dessus, ce TP n'est qu'un filet
        # pour que la position ne coure pas indefiniment.
        if "MDT" in channel_source and final_tp == 0.0:
            dist_mdt = MDT_TP_DEFAUT_PIPS * pt * div
            final_tp = final_px + dist_mdt if action == "ACHAT" else final_px - dist_mdt
            final_tp = round(final_tp / s_info.point) * s_info.point
            log(f"🎯 {channel_source} sans TP : cible par defaut a {MDT_TP_DEFAUT_PIPS} pips "
                f"-> {final_tp}", important=True)

        if "SABR" in channel_source and final_tp == 0.0:
            dist_tp_points = 1000 * 10 * pt * (div/10)
            if action == "ACHAT": final_tp = final_px + dist_tp_points
            else: final_tp = final_px - dist_tp_points
                
            if final_tp > 0:
                final_tp = round(final_tp / s_info.point) * s_info.point

        req = {
            "action": tact, "symbol": symbol, "volume": float(f"{lot:.2f}"), "type": otype,
            "price": final_px, "sl": final_sl, "tp": final_tp,
            "deviation": MAX_SLIPPAGE, "magic": MAGIC_NUMBER,
            "comment": commentaire_mt5(channel_source), "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC if tact == mt5.TRADE_ACTION_DEAL else mt5.ORDER_FILLING_RETURN,
        }

        # (#28) Petit délai aléatoire supplémentaire juste avant l'envoi MT5, en plus de
        # la "pause humaine" (2-4s) déjà appliquée à la réception du message -- chaque
        # client fait tourner sa propre instance, ça désynchronise encore un peu plus
        # le moment exact de l'ordre entre comptes (anti-détection "groupe" FTMO).
        await asyncio.sleep(random.uniform(0.0, 1.5))

        res = await run_mt5(_send_order_blocking, req)

        if res is None:
            logger.error("⚠️ Erreur Critique: MT5 ne répond pas.")
            await noter(_tag_x, f"❌ ORDRE NON ENVOYÉ ({symbol}) : MetaTrader ne répond pas")
            continue

        if res.retcode == mt5.TRADE_RETCODE_DONE:
            daily_stats["total_trades"] += 1
            ACTIVE_TRADE_SOURCES[res.order] = channel_source
            ACTIVE_TRADE_SIGNALS[res.order] = signal_id
            await run_bg(save_channel_data)
                
            t_str = "MARKET" if tact == mt5.TRADE_ACTION_DEAL else "PENDING"
            log(f"🔥 ORDRE {t_str} ENVOYÉ: {symbol} (Source: {channel_source})", important=True)

            # (#111) Tous les canaux.
            await noter(tag_canal(channel_source),
                        f"🔥 TRADE OUVERT — ticket {res.order} | {symbol} {action} {t_str} | "
                        f"{lot:.2f} lots @ {final_px} | SL {final_sl} | TP {final_tp}")
                
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
            await send_channel_alert(
                f"🔥 **ORDRE {t_str}**\n\n{symbol} {action}\n\n"
                f"Prix: {final_px}\nSL: {final_sl} & TP: {final_tp}\n"
                f"Signal: {sig_time}\nLot: **{lot:.2f}** (Risque: {risk_str})\n"
                f"Source: {safe_source}{sabr_type_line}",
                channel_source,
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
            
            res2 = await run_mt5(_send_order_blocking, req)
            if res2 and res2.retcode == mt5.TRADE_RETCODE_DONE:
                ACTIVE_TRADE_SOURCES[res2.order] = channel_source
                ACTIVE_TRADE_SIGNALS[res2.order] = signal_id
                await run_bg(save_channel_data)
                # --- MODIFICATION TELEGRAM SECOURS ICI ---
                await send_channel_alert(f"🔥 **ORDRE MARKET (RESCUE)**\n{symbol} {action}\nLot: **{lot:.2f}** (Risque: {risk_str})\n\n🛑 SL: {final_sl} & 🎯 TP: {final_tp}", channel_source)
                tickets_opened.append(res2.order)
                await noter(_tag_x, f"🔥 TRADE OUVERT au marché (secours, prix refusé) — ticket "
                                    f"{res2.order} | {symbol} {action} | {lot:.2f} lots | "
                                    f"SL {final_sl} | TP {final_tp}")
            else:
                await noter(_tag_x, f"❌ ORDRE REJETÉ par MT5 ({symbol} {action}) — prix refusé "
                                    f"(code {res.retcode}), puis secours au marché refusé aussi"
                                    + (f" (code {res2.retcode} : {res2.comment})" if res2 else ""))
        else:
            logger.error(f"❌ REJET MT5 ({res.retcode}) sur {symbol} : {res.comment}")
            await noter(_tag_x, f"❌ ORDRE REJETÉ par MT5 ({symbol} {action}) — code {res.retcode} : {res.comment}")
            await send_alert(f"❌ **ERREUR D'EXÉCUTION MT5**\nImpossible d'ouvrir {action} {symbol}\nRaison : {res.comment}\n*(Code {res.retcode} : Ton MT5 est allumé ? Algo Trading activé ?)*")

    if tp1_price:
        for tk in tickets_opened:
            TP1_PRICE_BY_TICKET[tk] = tp1_price

    # (#84) Echelle de TP INDODOG : memorisee par ticket pour que monitor_positions puisse
    # fermer, a chaque palier, le % de la position INITIALE prevu par l'echelle du canal.
    if tp_ladder:
        for tk in tickets_opened:
            INDO_TP_BY_TICKET[tk] = list(tp_ladder)
        # (#87) Une seule ligne de comptage pour tout le signal. La cle est construite comme
        # dans monitor_positions, sinon les paliers se noteraient sur une autre ligne.
        if tickets_opened:
            _sig = ACTIVE_TRADE_SIGNALS.get(tickets_opened[0]) or ("_tk%d" % tickets_opened[0])
            ladder_stats_open(channel_source, _sig, symbol, len(tp_ladder))
            # (#110) Le signal garde l'echelle de sa pose, meme si le reglage change ensuite.
            INDO_ECHELLE_BY_SIGNAL[_sig] = ladder_echelle(channel_source)
            # (#104) Le marqueur voyage dans INDO_LEVELS_BY_SIGNAL, deja indexe par signal et
            # deja persiste : pas de nouvel etat a sauvegarder ni a recharger.
            if signal_defaut:
                INDO_LEVELS_BY_SIGNAL.setdefault(_sig, set()).add(TAG_SIGNAL_DEFAUT)
            await run_bg(save_ladder_stats, channel_source)
        await run_bg(save_memory)

    return tickets_opened

async def send_alert(msg, buttons=None):
    """Envoie une alerte avec ou sans boutons interactifs"""
    if ADMIN_ID == 0: return
    try:
        # On utilise le bot_user de Telethon au lieu de aiohttp pour supporter les boutons
        await bot_user.send_message(ADMIN_ID, msg, buttons=buttons, parse_mode='md')
    except Exception as e:
        logger.error(f"⚠️ Erreur Envoi Alerte: {e}")


# (#100) Le champ commentaire de MqlTradeRequest tient 31 caracteres. Au-dela, le courtier
# tronque sans prevenir -- d'ou des libelles coupes en plein milieu dans MT5.
MT5_COMMENT_MAX = 31


def commentaire_mt5(canal, etape=None, pips=None):
    """(#100) Commentaire MT5 standardise : "B - SABR VIP TP1 (100 pips)".

    B pour "bot", puis le canal, puis l'etape, puis la distance du palier quand le canal
    l'annonce en pips (les canaux a echelle donnent des PRIX, pas des pips : on n'invente rien).

    La coupe a MT5_COMMENT_MAX est faite ICI, une seule fois, au lieu d'un [:25] recopie a
    chaque appel -- c'est ce qui garantit que tous les commentaires ont la meme forme.
    """
    nom = _channel_base_token(canal).replace("_", " ").strip()
    txt = "B - %s" % (nom or "?")
    if etape:
        txt += " %s" % etape
    if pips:
        txt += " (%d pips)" % int(pips)
    return txt[:MT5_COMMENT_MAX]


def _channel_base_token(name):
    """(#7) Normalise un nom de canal en jeton de base pour comparaison.
    Ex: 'CANAL SABR_VIP_SNIPER' / 'CANAL SABR VIP' -> 'SABR_VIP'."""
    if not name:
        return ""
    t = str(name).upper().replace("CANAL", "").strip().replace(" ", "_")
    while "__" in t:
        t = t.replace("__", "_")
    for suf in ("_SNIPER", "_ZONE", "_BLIND"):
        if t.endswith(suf):
            t = t[: -len(suf)]
    return t.strip("_")


def channel_allowed(source):
    """(#7/#25) True si le canal source fait partie du pack de ce client -- utilisé à la
    fois pour l'affichage (send_channel_alert) ET, depuis #25, pour verrouiller réellement
    les commandes admin ciblant un canal (channel_allowed n'affecte pas le trading lui-même,
    c'est PACK_LOCKED_CHANNELS + MUTED_CHANNELS qui s'en chargent, cf. main_handler).
    ALLOWED_CHANNELS vide = tout visible (rétro-compatible). Source inconnue = visible (sécurité).
    (#39) ALLOWED_CHANNELS == ["NONE"] est un sentinel explicite "aucun canal" (pack suspendu) --
    distinct de la liste vide, qui elle reste "pas de pack configuré -> tout visible"."""
    try:
        if ALLOWED_CHANNELS == ["NONE"]:
            return False
        if not ALLOWED_CHANNELS:
            return True
        base = _channel_base_token(source)
        if not base:
            return True
        allowed = {_channel_base_token(c) for c in ALLOWED_CHANNELS}
        return base in allowed
    except Exception:
        return True


def pack_uses_risk_engine():
    """(#pack) True si le pack du client contient au moins un canal NON-SABR.

    Les canaux SABR n'utilisent NI les risques % globaux (or/indices/devises), NI le
    planning heure/jour, NI le SL suiveur :
      - lot SABR or = lot fixe (FIXED_LOT_SABR_*), hors-or = RISK_PERCENT_SABR_OTHER qui
        court-circuite les % globaux (risk_override dans get_safe_lot_size) ;
      - le multiplicateur heure/jour n'est appliqué que dans la branche "lot auto"
        (lot == 0.0), or SABR passe toujours un volume forcé ;
      - les positions SABR sont déviées vers sabr_groups AVANT le bloc SL suiveur.
    Ces trois réglages sont donc purement décoratifs pour un pack 100% SABR : on les
    masque (dashboard + /info) pour ne pas laisser croire qu'ils agissent sur les trades.
    Pack vide = "tous les canaux" -> True (on affiche tout, rétro-compatible)."""
    try:
        if not ALLOWED_CHANNELS:
            return True
        if ALLOWED_CHANNELS == ["NONE"]:
            return False
        return any(not _channel_base_token(c).startswith("SABR") for c in ALLOWED_CHANNELS)
    except Exception:
        return True


def recompute_pack_lock():
    """(#25) Recalcule PACK_LOCKED_CHANNELS = les canaux de ALL_PACK_CHANNELS absents du
    pack courant. Appelée au chargement des settings et à chaque mise à jour à chaud du
    pack (commande Firestore set_allowed_channels). Ne modifie jamais MUTED_CHANNELS."""
    global PACK_LOCKED_CHANNELS
    if ALLOWED_CHANNELS == ["NONE"]:  # (#39) sentinel "aucun canal" -> tout est verrouillé
        PACK_LOCKED_CHANNELS = list(ALL_PACK_CHANNELS)
    elif not ALLOWED_CHANNELS:
        PACK_LOCKED_CHANNELS = []
    else:
        allowed_bases = {_channel_base_token(c) for c in ALLOWED_CHANNELS}
        PACK_LOCKED_CHANNELS = [c for c in ALL_PACK_CHANNELS if _channel_base_token(c) not in allowed_bases]


def resolve_channel_arg(target):
    """(#25) Résout un argument texte libre (ex: 'VIP', 'SABR_VIP', 'SPACE') en nom de
    canal interne ('CANAL MDT_VIP'...). Logique unique partagée par /mute, /unmute,
    /sell_lot et le filtre de pack -- remplace 3 résolutions différentes et incohérentes
    (dont un bug sur /sell_lot VIP/FREE seuls, cf. issue #25)."""
    target = target.upper()
    if "SABR" in target and "VIP" in target: return "CANAL SABR_VIP"
    if "SABR" in target and ("COPIE" in target or "CP" in target): return "CANAL SABR_COPIE"
    if "SABR" in target: return "CANAL SABR_FREE"
    mapping = {
        "VIP": "CANAL MDT_VIP", "MDT_VIP": "CANAL MDT_VIP",
        "FREE": "CANAL MDT_FREE", "GRATUIT": "CANAL MDT_FREE", "MDT_FREE": "CANAL MDT_FREE",
        "SPACE": "CANAL SPACE", "LIGUE": "CANAL LIGUE", "PERSO": "CANAL PERSO",
        "INDODOG": "CANAL INDODOG", "INDO": "CANAL INDODOG",
        "FTS": "CANAL FTS",
    }
    return mapping.get(target, f"CANAL {target}")


async def send_channel_alert(msg, source, buttons=None):
    """(#7) Comme send_alert, mais n'envoie au canal du client que si le canal source
    fait partie de son pack (ALLOWED_CHANNELS). N'affecte JAMAIS le trading."""
    if channel_allowed(source):
        await send_alert(msg, buttons=buttons)

async def check_closed_positions():
    global watched_tickets

    curr = await run_mt5(mt5.positions_get, group="*")

    if curr is None: curr = ()
    cur_tix = {p.ticket for p in curr if p.magic == MAGIC_NUMBER or p.ticket in MANAGED_MANUAL_TICKETS}
    closed = watched_tickets - cur_tix
    
    for t in closed:

        h = await run_mt5(mt5.history_deals_get, position=t)

        if h and len(h) > 0:
            last_deal = h[-1]
            if last_deal.entry != mt5.DEAL_ENTRY_OUT and last_deal.entry != mt5.DEAL_ENTRY_OUT_BY:
                continue

            prof = sum(d.profit + d.swap + d.commission for d in h)
            entry, exit = h[0], h[-1]
            sym = exit.symbol
            pt = get_cached_point(sym)
            div = get_pip_divisor(sym)

            # (#pips) Prix de sortie moyen PONDÉRÉ par le volume de chaque clôture (TP1/TP2/
            # trailing/final), pas juste le prix du DERNIER deal. Sinon un trade qui a sécurisé
            # 60%+ du volume à +100/+150 pips sur les partiels puis fini le reliquat (souvent le
            # step minimum) en BE/légèrement négatif affichait des pips proches de 0 ou négatifs
            # alors que le profit € réel était largement positif (partiels non reflétés).
            out_deals = [d for d in h if d.entry in (mt5.DEAL_ENTRY_OUT, mt5.DEAL_ENTRY_OUT_BY)]
            total_close_vol = sum(d.volume for d in out_deals)
            weighted_exit_price = (sum(d.price * d.volume for d in out_deals) / total_close_vol) if total_close_vol > 0 else exit.price

            if entry.type == mt5.DEAL_TYPE_BUY: pips = ((weighted_exit_price - entry.price) / pt) / div
            else: pips = ((entry.price - weighted_exit_price) / pt) / div

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
            
            # --- NETTOYAGE ---
            # (#104) `tid` est la LISTE des tickets ouverts par ce message (une entrée en zone
            # en pose trois), `t` un ticket. Le `tid == t` d'origine comparait une liste à un
            # entier : toujours faux, donc le nettoyage n'avait jamais lieu. On retire le
            # ticket fermé, et on ne jette l'entrée qu'une fois le dernier parti -- sinon une
            # édition du message ne toucherait plus les ordres encore vivants de la zone.
            for mid, tid in list(TELEGRAM_TO_TICKET.items()):
                if not isinstance(tid, list):
                    if tid == t: del TELEGRAM_TO_TICKET[mid]
                    continue
                if t in tid:
                    tid.remove(t)
                    if not tid: del TELEGRAM_TO_TICKET[mid]
                    break
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
            source = normalize_channel_source(raw_source)

            signal_id = ACTIVE_TRADE_SIGNALS.pop(t, "") or f"_tk{t}"
            # Reste-t-il d'autres ordres/positions rattaches au MEME signal ? Sert au nettoyage
            # de l'etat INDODOG plus bas : une zone pose plusieurs ordres, on ne purge qu'au
            # dernier. Le ticket courant vient d'etre retire, la reponse est donc exacte.
            still_open = any(sg == signal_id for sg in ACTIVE_TRADE_SIGNALS.values())

            # (#stats) Valeur d'un pip pour 1.00 lot : permet de repondérer proprement les pips
            # a posteriori, et d'expliquer un canal qui gagne en € tout en perdant en pips
            # (1 pip d'AUDCAD vaut ~10 pips d'US100).
            eur_per_pip = 0.0
            try:
                s_info = mt5.symbol_info(sym)
                if s_info and s_info.trade_tick_size > 0:
                    eur_per_pip = s_info.trade_tick_value * ((pt * div) / s_info.trade_tick_size)
            except Exception:
                pass

            # Plus rien à cumuler ici : la ligne écrite juste en dessous dans
            # trades_detailed.csv est la seule trace, et get_channel_stats_period() la réagrège
            # à la demande sur la période choisie. Le nettoyage est inconditionnel (.pop) : un
            # trade sans source connue n'a rien à supprimer dans ACTIVE_TRADE_SOURCES, mais
            # laissait auparavant traîner son entrée dans TP1_PRICE_BY_TICKET.
            ACTIVE_TRADE_SOURCES.pop(t, None)
            TP1_PRICE_BY_TICKET.pop(t, None)
            INDO_TP_BY_TICKET.pop(t, None)
            INDO_APPROACHED.discard(t)   # (#89) l'ordre a fini par se declencher
            # (#84) L'etat INDODOG est indexe par SIGNAL : on ne le purge qu'une fois le
            # DERNIER ordre de ce signal referme, sinon les paliers deja pris seraient oublies
            # et les partiels rejoues sur les positions encore ouvertes de la meme zone.
            if not still_open:
                INDO_LEVELS_BY_SIGNAL.pop(signal_id, None)
                INDO_BASE_VOL_BY_SIGNAL.pop(signal_id, None)
                INDO_ECHELLE_BY_SIGNAL.pop(signal_id, None)   # (#110)
                # (#87/#90) Issue du signal, notee une fois le DERNIER ordre referme. On
                # reprend `reason`, deja calcule au-dessus, pour rester coherent avec
                # trades_detailed.csv.
                if is_ladder_channel(source):
                    if "Take Profit" in reason:      _issue = "TP_FINAL"
                    elif "Stop Loss Full" in reason: _issue = "SL"
                    elif "Breakeven" in reason:      _issue = "BE"
                    elif "SL Suiveur" in reason:     _issue = "SL_SUIVEUR"
                    else:                            _issue = "AUTRE"
                    ladder_stats_close(source, signal_id, _issue)
                    await run_bg(save_ladder_stats, source)
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
                exit_reason=reason,
                signal_id=signal_id,
                eur_per_pip=eur_per_pip
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
            open_time = heure_client(entry.time)  # (#108)
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

            # (#111) Tous les canaux.
            await noter(tag_canal(source),
                        f"🏁 TRADE CLÔTURÉ — ticket {t} | {sym} | {prof:+.2f}€ | "
                        f"{pips:+.1f} pips | max {mfe:+.1f}p / min {mae:+.1f}p | {reason}")

            await send_channel_alert(msg, source)

    watched_tickets = cur_tix

# ==========================================
# 🕸️ FILET DE SÉCURITÉ (ANTI-COUPURE RÉSEAU)
# ==========================================
async def safety_net_scanner():
    """Vérifie toutes les 15s si un signal a été raté pendant une coupure réseau"""
    logger.info("🕸️ Filet de sécurité (Anti-Coupure) activé.")
    await asyncio.sleep(15) # On laisse le bot démarrer tranquillement
    last_purge = time.time()

    while True:
        try:
            # (#61) PROCESSED_MSG_IDS ne sert qu'à couvrir la fenêtre de rattrapage de 5 min
            # ci-dessous -- sans purge il grossissait indéfiniment (fuite mémoire lente sur
            # des semaines d'uptime). 1h de rétention est largement suffisant.
            if time.time() - last_purge > 3600:
                PROCESSED_MSG_IDS.clear()
                last_purge = time.time()

            for chat_id in CHANNEL_LIST:
                # On demande poliment à Telegram les 2 derniers messages du canal
                messages = await client.get_messages(chat_id, limit=2)
                
                for msg in messages:
                    if msg.id not in PROCESSED_MSG_IDS:
                        # (#52) Le marquage "vu" est désormais fait par main_handler() lui-même,
                        # tout en tête, de façon atomique avec le check -- pas ici avant l'appel.
                        # Sinon, si le flux live traitait ENCORE ce même event.id au moment de ce
                        # scan (retard réseau plutôt que perte réelle), les deux exécutions de
                        # main_handler tournaient en parallèle sur le même signal.
                        #
                        # On vérifie l'âge du message (on ne trade que s'il a moins de 5 minutes)
                        # msg.date.timestamp() et time.time() sont tous les deux en UTC, c'est parfait
                        age_seconds = time.time() - msg.date.timestamp()

                        if age_seconds < 300: # 300 secondes = 5 minutes
                            logger.warning(f"🕸️ MESSAGE RATÉ ATTRAPÉ (Coupure réseau) ! ID: {msg.id}")

                            # Astuce Telethon : L'objet 'msg' (Message) possède les mêmes attributs
                            # que 'event' (NewMessage.Event). On peut donc l'envoyer directement
                            # dans le main_handler comme si de rien n'était !
                            await main_handler(msg)
                        else:
                            # Trop vieux pour être rejoué : on le marque quand même comme vu pour
                            # ne pas recalculer son âge à chaque scan de 15s.
                            PROCESSED_MSG_IDS.add(msg.id)

        except Exception:
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

async def get_equity_curve(bornes, max_points=180):
    """(#65) Courbe de BALANCE du compte sur la période, pour le graphique de l'onglet Global.

    Reconstruite depuis l'historique MT5 (et non depuis trades_detailed.csv) : c'est la seule
    source qui couvre TOUT le compte -- trades du bot, trades manuels, et l'antériorité à
    l'installation. La courbe est donc déjà remplie au premier lancement.

    Méthode : on somme les mouvements de la période, on en déduit la balance de DÉPART
    (balance actuelle - total), puis on rejoue les journées en avançant. Partir de la balance
    actuelle et remonter évite d'avoir à connaître un capital initial, que MT5 n'expose pas.

    Balance RÉALISÉE : les positions ouvertes ne sont pas comptées, la courbe ne bouge qu'à la
    clôture.

    (#65) Seuls les deals de TRADING sont comptés. La dotation initiale d'un compte prop est un
    deal de type BALANCE, exactement comme un dépôt : l'inclure faisait démarrer la courbe au
    solde d'AVANT le financement (≈ 0, à quelques euros de frais près), suivi d'un bond vertical
    jusqu'à la dotation qui écrasait toute la performance en une ligne plate. En l'excluant, la
    courbe part du capital réellement disponible au début de la période.

    Retourne [{"d": "2026-08-13", "v": 10236.69}, ...] trié par date, [] si indisponible.

    (#108) `bornes` vient de bornes_periode(). Les deals sont rangés par JOUR DU CLIENT : avant,
    un trade fermé le soir pouvait marquer la courbe du lendemain.

    (#107) Pour une plage passée (bornes_plage), la courbe s'arrête au dernier jour de la plage,
    à la balance de CE jour-là : la balance actuelle moins tout ce qui a été réalisé depuis.
    """
    tz_s = tz_client_min() * 60
    dec = decalage_serveur_s()

    def _jour(epoch):
        return datetime.fromtimestamp(epoch + tz_s, timezone.utc).strftime("%Y-%m-%d")

    def _montant(deal):
        # Dotation, dépôts, retraits, crédits, corrections : ce sont des mouvements de compte,
        # pas des résultats de trading. Ils ne doivent pas dessiner de marche dans la courbe.
        if deal.type not in (mt5.DEAL_TYPE_BUY, mt5.DEAL_TYPE_SELL):
            return 0.0
        return deal.profit + deal.swap + deal.commission

    acc = await run_mt5(mt5.account_info)
    if acc is None:
        return []
    balance_now = float(acc.balance)

    debut_srv, fin_srv = bornes_serveur(bornes)
    history = await run_mt5(mt5.history_deals_get, debut_srv, fin_srv)

    per_day = {}
    total = 0.0
    for deal in (history or []):
        amount = _montant(deal)
        if amount == 0:
            continue
        day = _jour(deal.time - dec)
        per_day[day] = per_day.get(day, 0.0) + amount
        total += amount

    # (#107) Balance a la FIN de la periode : celle d'aujourd'hui, sauf pour une plage passee.
    balance_fin = balance_now
    jour_final = _jour(time.time())
    if bornes.get("fin") is not None:
        depuis = await run_mt5(mt5.history_deals_get, fin_srv + 1, int(time.time() + dec + 3600))
        balance_fin -= sum(_montant(d) for d in (depuis or []))
        jour_final = bornes.get("jour_fin") or jour_final

    balance_start = balance_fin - total
    # Premier point : le premier jour de la période. Pour "Max", qui n'a pas de début, le jour
    # du premier trade -- et non une date arbitraire vingt ans en arrière.
    jour_debut = bornes.get("jour_debut") or (min(per_day) if per_day else _jour(time.time()))
    points = [{"d": jour_debut, "v": round(balance_start, 2)}]
    running = balance_start
    for day in sorted(per_day):
        running += per_day[day]
        points.append({"d": day, "v": round(running, 2)})

    # Point final à aujourd'hui : sans lui, une courbe dont le dernier trade date d'une semaine
    # s'arrêterait au milieu du graphique, ce qui se lit comme un arrêt d'activité.
    # (#107) Ou au dernier jour d'une plage passée.
    if points[-1]["d"] != jour_final:
        points.append({"d": jour_final, "v": round(balance_fin, 2)})

    # Sous-échantillonnage pour "Max" (des années de trading = des milliers de points, illisible
    # et lourd à pousser). Les extrémités sont toujours conservées : ce sont elles qui portent la
    # performance affichée sous le graphique.
    if len(points) > max_points:
        stride = len(points) / float(max_points - 1)
        kept = [points[int(i * stride)] for i in range(max_points - 1)]
        kept.append(points[-1])
        points = kept

    return points


async def get_historical_stats(bornes):
    # (#108) `bornes` vient de bornes_periode() : même règle que les stats par canal et
    # l'historique des trades, et une requête MT5 en heure serveur (cf. bornes_serveur).
    debut_srv, fin_srv = bornes_serveur(bornes)
    history = await run_mt5(mt5.history_deals_get, debut_srv, fin_srv)
    
    # On initialise un dictionnaire complet pour matcher la structure daily_stats
    stats = {
        "profit": 0.0, "pips": 0.0, "total_trades": 0, "winrate": 0,
        "tp1_reached": 0, "tp2_reached": 0, "tp3_reached": 0, "tp4_reached": 0, "tp_final": 0,
        "sl_full": 0, "sl_be": 0, "sl_30": 0, "sl_80": 0, "sl_130": 0
    }
    
    if not history: return stats

    # (#pips) Regroupement par POSITION : un trade avec clôtures partielles (TP1/TP2/
    # trailing/final) génère PLUSIEURS deals de sortie. Les compter un par un gonflait
    # total_trades et faussait le winrate (une tranche partielle en gain ne dit rien du
    # résultat net du trade entier, et inversement) -- même famille de bug que les pips
    # de check_closed_positions/_get_closed_trades_today_blocking, "pips" n'était par
    # ailleurs jamais rempli ici (toujours 0 côté dashboard, onglets Semaine/Global).
    positions = {}
    entry_by_pos = {}
    for deal in history:
        if deal.entry == mt5.DEAL_ENTRY_IN:
            entry_by_pos.setdefault(deal.position_id, deal)
        elif deal.entry in (mt5.DEAL_ENTRY_OUT, mt5.DEAL_ENTRY_OUT_BY):
            p = positions.setdefault(deal.position_id, {
                "profit": 0.0, "weighted_price_sum": 0.0, "closed_volume": 0.0,
                "last_deal": deal, "comment": "",
            })
            p["profit"] += deal.profit + deal.commission + deal.swap
            p["weighted_price_sum"] += deal.price * deal.volume
            p["closed_volume"] += deal.volume
            p["comment"] += " " + deal.comment.upper()
            if deal.time >= p["last_deal"].time:
                p["last_deal"] = deal

    wins = 0
    for pos_id, p in positions.items():
        entry_deal = entry_by_pos.get(pos_id)
        if entry_deal is None:
            # Position ouverte avant le début de la période demandée -> on va chercher
            # son vrai deal d'entrée (hors filtre de date).
            full_hist = await run_mt5(mt5.history_deals_get, position=pos_id)
            if not full_hist or len(full_hist) == 0: continue
            entry_deal = full_hist[0]

        pt = get_cached_point(entry_deal.symbol)
        div = get_pip_divisor(entry_deal.symbol)
        weighted_exit = (p["weighted_price_sum"] / p["closed_volume"]) if p["closed_volume"] > 0 else entry_deal.price
        if entry_deal.type == mt5.DEAL_TYPE_BUY:
            pips = ((weighted_exit - entry_deal.price) / pt) / div
        else:
            pips = ((entry_deal.price - weighted_exit) / pt) / div

        stats["profit"] += p["profit"]
        stats["pips"] += pips
        stats["total_trades"] += 1
        if p["profit"] > 0: wins += 1

        comment = p["comment"]
        if "TP1" in comment: stats["tp1_reached"] += 1
        if "TP2" in comment: stats["tp2_reached"] += 1
        if "TP3" in comment: stats["tp3_reached"] += 1
        if "TP4" in comment: stats["tp4_reached"] += 1

        last = p["last_deal"]
        is_mt5_tp = (last.reason == 5)
        is_mt5_sl = (last.reason == 4)
        if is_mt5_tp or (p["profit"] > 0 and pips >= TP4_PIPS):
            stats["tp_final"] += 1
        elif (is_mt5_sl or pips < 10) and pips > -10:
            stats["sl_be"] += 1
        elif is_mt5_sl or pips <= -20:
            stats["sl_full"] += 1
        elif pips >= 10:
            if pips > 135: stats["sl_130"] += 1
            elif pips > 80: stats["sl_80"] += 1
            else: stats["sl_30"] += 1

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
                        "weighted_price_sum": 0.0,
                        "open_price": entry_deal.price,
                        "open_time": entry_deal.time,
                        "close_price": d.price, # Sera mis à jour avec la dernière sortie (affichage uniquement)
                        "profit": 0.0,
                        "last_time": d.time
                    }

                # On cumule les profits et les volumes fermés
                prof = d.profit + d.commission + d.swap
                grouped_trades[pos_id]["profit"] += prof
                grouped_trades[pos_id]["closed_volume"] += d.volume
                # (#pips) Pondéré par volume -- sert au calcul des pips (voir plus bas), pour ne
                # pas ignorer les clôtures partielles (TP1/TP2/trailing) faites à un meilleur prix
                # que la dernière tranche fermée.
                grouped_trades[pos_id]["weighted_price_sum"] += d.price * d.volume

                # On garde le prix de clôture le plus récent (uniquement pour l'affichage "close_price")
                if d.time > grouped_trades[pos_id]["last_time"]:
                    grouped_trades[pos_id]["close_price"] = d.price
                    grouped_trades[pos_id]["last_time"] = d.time

    # Formatage final pour React
    closed_list = []
    for pos_id, t in grouped_trades.items():
        pt = get_cached_point(t["symbol"])
        div = get_pip_divisor(t["symbol"])
        
        # Calcul des pips : prix de sortie moyen PONDÉRÉ par volume (pas juste la dernière
        # tranche fermée), sinon un trade sécurisé à +100/+150p sur des partiels puis fini en
        # BE sur le reliquat affichait des pips proches de 0 alors que le profit € était positif.
        weighted_close_price = (t["weighted_price_sum"] / t["closed_volume"]) if t["closed_volume"] > 0 else t["close_price"]
        if t["type"] == "BUY":
            pips = ((weighted_close_price - t["open_price"]) / pt) / div
        else:
            pips = ((t["open_price"] - weighted_close_price) / pt) / div
            
        # Formatage du volume (ex: "0.04/0.08" ou "0.08")
        vol_closed = round(t["closed_volume"], 2)
        vol_initial = round(t["initial_volume"], 2)
        
        # Si on n'a pas tout fermé (arrondi pour éviter les erreurs de virgule flottante)
        if vol_initial - vol_closed > 0.009:
            vol_str = f"{vol_closed}/{vol_initial}"
        else:
            vol_str = f"{vol_closed}"

        open_time_str = heure_client(t["open_time"])  # (#108)
            
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

# ===== Logique reglages reutilisee par le handler de commandes Firebase =====
async def api_update_planning(data: PlanningUpdate):
    global DAY_RISK_FACTORS, PRIME_HOURS, TIME_OFF_PEAK_FACTOR
    DAY_RISK_FACTORS = {int(k): v for k, v in data.day_factors.items()}
    PRIME_HOURS = data.prime_hours # <--- CORRIGÉ ICI (C'était data.time_ranges)
    TIME_OFF_PEAK_FACTOR = data.off_peak_factor
    await run_bg(save_settings)
    return {"status": "success"}

async def api_update_setting(data: SettingUpdate):
    global RISK_PERCENT_GOLD, RISK_PERCENT_INDICES, RISK_PERCENT_OTHER
    global RISK_PERCENT_MDT_VIP, RISK_PERCENT_MDT_FREE, RISK_PERCENT_SPACE, RISK_PERCENT_LIGUE, RISK_PERCENT_PERSO
    global FIXED_LOT_SABR_VIP, FIXED_LOT_SABR_FREE, RISK_PERCENT_SABR_OTHER, SABR_ZONE_EQUAL_SPLIT, SABR_ZONE_PCT1, SABR_ZONE_PCT2, SABR_ZONE_BORNES
    global MAX_DAILY_DRAWDOWN, TRAILING_STOP_PIPS, SOFT_SL_PERCENT
    global BE_OFFSET_PIPS, SABR_TRAILING_PIPS, FIXED_LOT_INDODOG, FIXED_LOT_FTS
    global SABR_BE_PARTIEL_ACTIVE, SABR_BE_PARTIEL_PCT, SABR_FORWARD_ACTIVE
    global PRIME_HOURS, TIME_OFF_PEAK_FACTOR

    k = data.key
    v = data.value

    # (#44) Même verrou de pack que les commandes Telegram équivalentes (ex: /risk_mdt_vip) --
    # sans ça, le dashboard laissait configurer un réglage pour un canal hors du pack payé du
    # client, alors que le bot Telegram le refuse explicitement pour la même action.
    CHANNEL_SETTING_KEYS = {
        "risk_mdt_vip": "CANAL MDT_VIP", "risk_mdt_free": "CANAL MDT_FREE",
        "risk_space": "CANAL SPACE", "risk_ligue": "CANAL LIGUE", "risk_perso": "CANAL PERSO",
        "sabr_lot_vip": "CANAL SABR_VIP", "sabr_lot_free": "CANAL SABR_FREE",
        "lot_indodog": "CANAL INDODOG", "lot_fts": "CANAL FTS",
        "echelle_indodog": "CANAL INDODOG", "echelle_fts": "CANAL FTS",   # (#110)
    }
    if k in CHANNEL_SETTING_KEYS and not channel_allowed(CHANNEL_SETTING_KEYS[k]):
        return {"status": "error", "message": f"{k} : canal hors de ton pack."}
    if k in ("risk_sabr_other", "sabr_zone_equal_split", "sabr_zone_pct1", "sabr_zone_pct2",
             "sabr_be_partiel", "sabr_be_partiel_pct", "sabr_zone_bornes") and not (
        channel_allowed("CANAL SABR_VIP") or channel_allowed("CANAL SABR_FREE") or channel_allowed("CANAL SABR_COPIE")
    ):
        return {"status": "error", "message": f"{k} : SABR hors de ton pack."}

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
    elif k == "risk_sabr_other": RISK_PERCENT_SABR_OTHER = float(v)
    elif k == "sabr_zone_equal_split": SABR_ZONE_EQUAL_SPLIT = bool(v)
    elif k == "sabr_zone_bornes":
        # (#102) Liste filtree et REORDONNEE proche -> loin : l'ordre porte la priorite
        # d'attribution des lots, il ne doit pas dependre de l'ordre de clic du client.
        _b = [x for x in ("debut", "milieu", "fin") if x in (v or [])]
        if _b: SABR_ZONE_BORNES = _b
    elif k == "sabr_be_partiel": SABR_BE_PARTIEL_ACTIVE = bool(v)
    elif k == "sabr_be_partiel_pct": SABR_BE_PARTIEL_PCT = max(0.01, min(1.0, float(v)))
    # (#103) sabr_forward etait pousse au dashboard mais n'avait aucun chemin d'ecriture :
    # ni cle update_setting, ni commande toggle_. Il n'etait donc reglable que par
    # /forward_sabr. On passe par update_setting plutot que par une nouvelle commande, pour
    # ne pas avoir a toucher la liste blanche des regles Firestore.
    elif k == "sabr_forward": SABR_FORWARD_ACTIVE = bool(v)
    elif k == "sabr_zone_pct1": SABR_ZONE_PCT1 = max(0.01, min(0.98, float(v)))
    elif k == "sabr_zone_pct2": SABR_ZONE_PCT2 = max(0.01, min(0.98, float(v)))
    elif k == "max_dd": MAX_DAILY_DRAWDOWN = float(v)
    elif k == "sl_suiveur": TRAILING_STOP_PIPS = int(float(v))
    # (#72) Decalage du BE : signe libre, positif = SL dans le gain, negatif = petite
    # perte toleree. Borne a +/-500 pips pour eviter une saisie aberrante qui poserait
    # le SL tres loin du prix et rendrait la position ingerable.
    elif k == "be_offset_pips": BE_OFFSET_PIPS = max(-500.0, min(500.0, float(v)))
    elif k == "sabr_trailing_pips": SABR_TRAILING_PIPS = max(1, int(float(v)))
    elif k == "lot_indodog": FIXED_LOT_INDODOG = max(0.01, float(v))
    elif k == "lot_fts": FIXED_LOT_FTS = max(0.01, float(v))
    # (#110) Echelle de TP : refusee avec un message clair plutot que corrigee en douce.
    elif k in ("echelle_indodog", "echelle_fts"):
        _ech, _err = regler_echelle("CANAL FTS" if k == "echelle_fts" else "CANAL INDODOG", v)
        if _err:
            return {"status": "error", "message": _err}
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
                acc = await run_mt5(mt5.account_info)
                if acc:
                    # CORRECTION ICI : Ajout de await run_bg
                    pos = await run_mt5(mt5.positions_get, group="*")
                    
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

async def horloge_serveur_monitor():
    """(#108) Mesure l'heure du serveur MT5 chaque minute (cf. mesurer_decalage_serveur).

    Les deux premières lectures sont rapprochées : une mesure exige un tick qui a avancé entre
    deux lectures, on l'obtient ainsi dès le démarrage plutôt qu'au bout d'une minute.
    """
    premiere = True
    while True:
        try:
            await run_mt5(mesurer_decalage_serveur)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.debug(f"Mesure de l'heure serveur : {e}")
        await asyncio.sleep(5 if premiere else 60)
        premiere = False


async def license_watchdog():
    logger.info("👮 Monitor Licence activé (Vérification toutes les 4h).")
    while True:
        try:
            # On attend 4 heures (14400 secondes) avant la prochaine vérif
            await asyncio.sleep(14400)

            logger.info("👮 Vérification périodique de la licence...")
            # On appelle la fonction en mode silencieux (pas de print, pas de mouchard Telegram)
            is_valid = await run_bg(check_license_online, silent=True)

            if not is_valid:
                logger.critical("⛔ LICENCE RÉVOQUÉE OU EXPIRÉE EN COURS DE SESSION !")
                await send_alert("🚨 **URGENT : LICENCE INVALIDE**\n\nLe bot va s'arrêter immédiatement.")
                # On ferme proprement -- STOP_ME signale un arrêt VOLONTAIRE au watchdog
                # externe (run_watchdog), pour qu'il ne relance pas le worker (#46).
                with open("STOP_ME", "w") as f: f.write("STOP")
                os._exit(0)
        except Exception as e:
            # (#47) Sans ce try/except, une exception imprévue tuait cette tâche
            # silencieusement : plus aucune revérification de licence pour le reste de la
            # session, sans log ni alerte.
            logger.error(f"license_watchdog error: {e}")

async def cancel_pending_of_signal(sig_key):
    """(#84) Annule les ordres encore EN ATTENTE appartenant a ce signal.

    Appele des le premier TP d'une entree en zone : les bornes non remplies n'ont plus lieu
    d'etre, le mouvement est deja parti. Sans ca, un ordre restant pourrait se declencher plus
    tard, a contre-courant, en rouvrant une position que le signal considere comme terminee.
    """
    annules = 0
    try:
        orders = await run_mt5(mt5.orders_get)
        for o in (orders or []):
            if o.magic != MAGIC_NUMBER:
                continue
            if ACTIVE_TRADE_SIGNALS.get(o.ticket) != sig_key:
                continue
            res = await run_mt5(_send_order_blocking,
                                {"action": mt5.TRADE_ACTION_REMOVE, "order": o.ticket,
                                 "magic": MAGIC_NUMBER})
            if res and res.retcode == mt5.TRADE_RETCODE_DONE:
                annules += 1
                _src_o = ACTIVE_TRADE_SOURCES.get(o.ticket)
                ACTIVE_TRADE_SIGNALS.pop(o.ticket, None)
                ACTIVE_TRADE_SOURCES.pop(o.ticket, None)
                logger.warning("🗑 Ordre %s annule (zone INDODOG terminee)" % o.ticket)
                await noter(tag_canal(_src_o), f"🗑 ORDRE ANNULÉ — ticket {o.ticket} : TP1 atteint, "
                                               f"le reste de la zone ne sert plus")   # (#111)
                # (#104) Filet : si TOUS les ordres du signal finissent annules sans qu'aucune
                # position ne s'ouvre, plus rien ne passera par check_closed_positions() et le
                # signal resterait "en cours" a vie. ladder_stats_close() ne fait rien si une
                # issue est deja notee, donc ce cas normal (un ordre annule alors qu'un autre
                # est deja en position) n'ecrase jamais le vrai resultat.
                await _ladder_close_si_plus_rien(_src_o, sig_key)
    except Exception as e:
        logger.warning("cancel_pending_of_signal : %s" % e)
    return annules


async def cancel_indo_stale_orders():
    """(#89) Annule les ordres INDODOG encore EN ATTENTE devenus caducs.

    Deux motifs independants :
      1. Le prix s'est approche a moins de INDO_APPROACH_PIPS du prix d'entree SANS nous
         declencher, puis est alle jusqu'au TP2. Le mouvement a eu lieu sans nous : garder
         l'ordre, c'est esperer un retour qui se ferait a contre-courant du signal.
      2. L'ordre attend depuis plus de INDO_ORDER_EXPIRY_H heures.

    L'approche est un fait HISTORIQUE (INDO_APPROACHED) : au moment ou le TP2 est atteint, le
    prix est deja loin de l'entree, on ne peut plus la constater. Il faut donc la noter quand
    on la voit.

    Cadencee a 10 s : la boucle appelante tourne chaque seconde, et interroger orders_get()
    aussi souvent n'apporte rien sur une bande de 30 pips.
    """
    maintenant = time.time()
    if maintenant - getattr(cancel_indo_stale_orders, "_dernier", 0.0) < 10.0:
        return
    cancel_indo_stale_orders._dernier = maintenant

    try:
        orders = await run_mt5(mt5.orders_get)
    except Exception as e:
        logger.warning("cancel_indo_stale_orders : %s" % e)
        return

    # L'age se calcule en heure SERVEUR : o.time_setup est date par le broker, alors que
    # time.time() est de l'UTC local. Chez un broker en UTC+3, les comparer directement
    # vieillirait chaque ordre de 3 h d'un coup.
    _srv = await run_mt5(get_server_time_tick)
    ref_horloge = float(_srv.time) if _srv else maintenant

    touche_memoire = False
    for o in (orders or []):
        if o.magic != MAGIC_NUMBER:
            continue
        if not is_ladder_channel(ACTIVE_TRADE_SOURCES.get(o.ticket)):
            continue
        _c = ACTIVE_TRADE_SOURCES.get(o.ticket, "").replace("CANAL ", "")

        motif = None

        # --- 1. Peremption pure
        age_h = (ref_horloge - float(getattr(o, "time_setup", ref_horloge))) / 3600.0
        if age_h >= INDO_ORDER_EXPIRY_H:
            motif = "en attente depuis %.0f h" % age_h

        # --- 2. Approche sans declenchement, puis TP2 atteint
        if motif is None:
            ladder = INDO_TP_BY_TICKET.get(o.ticket) or []
            tick = await run_mt5(mt5.symbol_info_tick, o.symbol) if len(ladder) >= 2 else None
            if tick:
                is_buy = o.type in (mt5.ORDER_TYPE_BUY_LIMIT, mt5.ORDER_TYPE_BUY_STOP)
                curr = tick.bid if is_buy else tick.ask
                pt_o = get_cached_point(o.symbol)
                div_o = get_pip_divisor(o.symbol)
                ecart = (abs(o.price_open - curr) / pt_o) / div_o
                if ecart <= INDO_APPROACH_PIPS and o.ticket not in INDO_APPROACHED:
                    INDO_APPROACHED.add(o.ticket)
                    touche_memoire = True
                    logger.info("🪜 %s : ordre %s frole a %.1f pips sans declencher."
                                % (_c, o.ticket, ecart))
                tp2 = ladder[1]
                tp2_atteint = (curr >= tp2) if is_buy else (curr <= tp2)
                if o.ticket in INDO_APPROACHED and tp2_atteint:
                    motif = ("approche a moins de %.0f pips sans declencher, puis TP2 atteint"
                             % INDO_APPROACH_PIPS)

        if not motif:
            continue

        # Le canal doit etre lu AVANT le nettoyage : send_channel_alert() s'en sert pour
        # router l'alerte et respecter le mute, et les .pop() ci-dessous l'effacent.
        src_o = ACTIVE_TRADE_SOURCES.get(o.ticket, "CANAL INDODOG")
        sig_o = ACTIVE_TRADE_SIGNALS.get(o.ticket)
        res = await run_mt5(_send_order_blocking,
                            {"action": mt5.TRADE_ACTION_REMOVE, "order": o.ticket,
                             "magic": MAGIC_NUMBER})
        if res and res.retcode == mt5.TRADE_RETCODE_DONE:
            ACTIVE_TRADE_SIGNALS.pop(o.ticket, None)
            ACTIVE_TRADE_SOURCES.pop(o.ticket, None)
            INDO_TP_BY_TICKET.pop(o.ticket, None)
            INDO_APPROACHED.discard(o.ticket)
            touche_memoire = True
            logger.warning("🗑 %s : ordre %s annule -- %s" % (_c, o.ticket, motif))
            await noter(tag_canal(src_o), f"🗑 ORDRE ANNULÉ — ticket {o.ticket} | "
                                          f"{o.symbol} @ {o.price_open} : {motif}")   # (#111)
            await send_channel_alert(
                ("🗑 **%s : ORDRE ANNULE**" % _c) + chr(10) + chr(10) +
                ("%s @ %s" % (o.symbol, o.price_open)) + chr(10) +
                ("Motif : %s" % motif) + chr(10) + chr(10) +
                "Le signal n'est plus valide.", src_o)
            # (#104) Voir _ladder_close_si_plus_rien : sans ca, un signal dont tous les ordres
            # sont annules sans jamais se declencher reste compte "en cours" pour toujours.
            await _ladder_close_si_plus_rien(src_o, sig_o)
            await run_bg(save_channel_data)
        else:
            logger.warning("🗑 %s : annulation refusee sur %s (%s)"
                           % (_c, o.ticket, getattr(res, "retcode", "?")))

    if touche_memoire:
        await run_bg(save_memory)


async def close_volume_across(positions, vol_target, label, pips, etape_mt5=None):
    """(#84) Ferme vol_target lots repartis sur plusieurs positions d'un meme signal.

    Les paliers INDODOG portent sur le volume TOTAL du signal, pas sur une position : il faut
    donc puiser dans les positions successives jusqu'a atteindre la quantite voulue. On sert
    les plus GROSSES d'abord, pour eviter de reduire une petite position sous le volume minimum
    du courtier et de la rendre infermable par tranches.

    Retourne le volume reellement ferme.
    """
    restant = round(vol_target, 2)
    ferme = 0.0
    for p in sorted(positions, key=lambda x: x.volume, reverse=True):
        if restant < 0.01:
            break
        info = mt5.symbol_info(p.symbol)
        vmin = info.volume_min if info else 0.01
        # On ne laisse jamais un residu inferieur au minimum du courtier : dans ce cas on
        # prend la position entiere plutot que de la rendre inexploitable.
        prise = min(p.volume, restant)
        if round(p.volume - prise, 2) < vmin:
            prise = p.volume
        prise = round(prise, 2)
        if prise < vmin:
            continue
        if await close_partial(p, 0.0, label, pips, fixed_lot=prise, etape=etape_mt5):
            ferme = round(ferme + prise, 2)
            restant = round(restant - prise, 2)
    return ferme


async def monitor_positions():
    # (#103) DD_PANIC_NOTIFIED doit être déclaré global : sans lui l'affectation plus bas
    # créerait une variable LOCALE et l'alerte repartirait à chaque tour, exactement le
    # piège du verrou drawdown en V15.10.0.
    global watched_tickets, PENDING_ORDER_TICKETS, DD_PANIC_NOTIFIED

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
                        act_time = heure_client(pos.time)  # (#108)
                        src_raw = ACTIVE_TRADE_SOURCES.get(ticket, "INCONNU")
                        src_clean = src_raw.replace("CANAL ", "").replace("_", " ")
                        await send_channel_alert(f"⚡ **ORDRE ACTIVÉ**\n\n"
                                         f"Symbole: {pos.symbol} ({act_time})\n"
                                         f"Source: {src_clean}\n"
                                         f"Ticket: {ticket}", src_raw)
                        await noter(tag_canal(src_raw), f"⚡ ORDRE DÉCLENCHÉ — ticket {ticket} | "
                                                        f"{pos.symbol} @ {pos.price_open}")   # (#111)
                        
                        tickets_to_remove.add(ticket)
                    else:
                        pending_ord = mt5.orders_get(ticket=ticket)
                        if not pending_ord: 
                            tickets_to_remove.add(ticket)
                PENDING_ORDER_TICKETS -= tickets_to_remove

            # --- B. Nettoyage intelligent des ordres (NOUVEAU) ---
            # On récupère l'heure serveur pour bloquer le spam le week-end
            server_t = await run_mt5(get_server_time_tick)
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
                        all_o = await run_mt5(mt5.orders_get)
                        if all_o:
                            c_del = 0
                            for ord_to_del in all_o:
                                if ord_to_del.magic == MAGIC_NUMBER:
                                    res = await run_mt5(_send_order_blocking, {"action": mt5.TRADE_ACTION_REMOVE, "order": ord_to_del.ticket})
                                    if res and res.retcode == mt5.TRADE_RETCODE_DONE:
                                        c_del += 1
                                        await noter(tag_canal(ACTIVE_TRADE_SOURCES.get(ord_to_del.ticket, "")),
                                                    f"🧹 ORDRE PURGÉ — ticket {ord_to_del.ticket} : fermeture "
                                                    f"du marché pour le week-end")   # (#111)
                            if c_del > 0: await send_alert(f"🧹 **CLÔTURE MARCHÉ** : {c_del} ordres en attente purgés pour le week-end.")
                        TRADES_PROCESSED_LEVELS["SYS"].add(tag_we)
                        await run_bg(save_memory)

            active_orders = await run_mt5(mt5.orders_get)
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
                            res = await run_mt5(_send_order_blocking, req)
                            if res and res.retcode == mt5.TRADE_RETCODE_DONE:
                                src_clean = src.replace("CANAL ", "").replace("_", " ")
                                await send_channel_alert(f"🗑️ **ORDRE EXPIRÉ (>12h)**\n\n"
                                                 f"Symbole: {o.symbol}\n\n"
                                                 f"Source: {src_clean}\n"
                                                 f"Ticket: {o.ticket}", src)
                    
                    # 2️⃣ Nettoyage SABR (Préparation des groupes)
                    else:
                        # (#104) Le groupe est le SIGNAL, pas (symbole, sens, canal). Avec
                        # l'ancienne clé, deux zones SABR VIP achat sur l'or n'en faisaient
                        # qu'une : les ordres tout juste posés d'un nouveau signal étaient jugés
                        # avec la position d'un signal précédent, et se faisaient annuler dans la
                        # foulée. C'est le "trade SABR supprimé quelques secondes après avoir été
                        # posé". INDODOG raisonne déjà par signal (cancel_pending_of_signal).
                        sig = ACTIVE_TRADE_SIGNALS.get(o.ticket) or ("_tk%d" % o.ticket)
                        key = (o.symbol, o.type, src, sig)
                        if key not in sabr_pending_groups:
                            sabr_pending_groups[key] = []
                        sabr_pending_groups[key].append(o)

                # 3️⃣ Traitement des ordres SABR ("Parti sans nous")
                for (sym, otype, src, sig), pending_orders in sabr_pending_groups.items():
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
                            # (#104) Même signal, et pas seulement même canal : une position
                            # ouverte par un signal antérieur n'a rien à dire sur les ordres
                            # d'un nouveau. C'est elle qui déclenchait la condition A (+150 pips)
                            # sur des ordres à peine posés.
                            if p.magic == MAGIC_NUMBER and \
                               (ACTIVE_TRADE_SIGNALS.get(p.ticket) or ("_tk%d" % p.ticket)) == sig:
                                if (is_buy and p.type == mt5.ORDER_TYPE_BUY) or (not is_buy and p.type == mt5.ORDER_TYPE_SELL):
                                    related_active_prices.append(p.price_open)

                    all_setup_prices = [o.price_open for o in pending_orders] + related_active_prices
                    if not all_setup_prices: 
                        continue

                    # --- LOGIQUE DE DISTANCE ET D'ANNULATION ---
                    # (#104) La mémoire du "frôlement" est propre au SIGNAL. Sous l'ancienne clé
                    # (symbole, canal) elle n'était effacée qu'à une annulation : un signal
                    # suivant héritait donc d'un min_dist ≤ 50 laissé par le précédent, et une
                    # nouvelle zone posée à plus de 400 pips satisfaisait la condition B dès le
                    # premier passage -- annulation immédiate, sans que le marché ait rien fait.
                    key_dist = (sym, src, sig)
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
                        # (#104) Motif exact, et surtout TRACÉ dans kairos.log : cette annulation
                        # n'existait qu'en alerte Telegram. Quand un ordre disparaissait à tort,
                        # le journal n'en gardait aucune trace et l'enquête partait de rien.
                        motif = ("position ouverte sur ce signal et +%.0f pips au-dessus de la zone"
                                 % current_dist_to_zone) if related_active_prices else (
                                "zone frôlée à %.0f pips puis partie à %.0f pips"
                                % (min_dist_reached, current_dist_to_zone))
                        count_cancelled = 0
                        for o in pending_orders:
                            req = {"action": mt5.TRADE_ACTION_REMOVE, "order": o.ticket}
                            res = await run_mt5(_send_order_blocking, req)
                            if res and res.retcode == mt5.TRADE_RETCODE_DONE:
                                count_cancelled += 1
                                logger.warning("🗑 SABR : ordre %s annule (%s) -- signal %s, %s"
                                               % (o.ticket, src_clean, sig, motif))
                                await noter(tag_canal(src), f"🗑 ORDRE ANNULÉ — ticket {o.ticket} | "
                                                            f"{sym} : {motif}")   # (#111)

                        if count_cancelled > 0:
                            await send_channel_alert(f"🗑️ **ORDRE SABR ANNULÉ**\n\n"
                                             f"Symbole: {sym}\nSource: {src_clean}\n"
                                             f"Raison: {motif}.\n"
                                             f"Ordres restants annulés: {count_cancelled}", src)
                        
                        if key_dist in SABR_ZONE_MIN_DIST: del SABR_ZONE_MIN_DIST[key_dist]

                # (#104) La clé porte maintenant le signal : sans purge, le dictionnaire
                # gagnerait une entrée définitive par signal SABR au lieu d'une par symbole.
                # Un signal dont plus aucun ordre n'est en attente n'a plus de distance à
                # mémoriser -- il est rempli, annulé ou expiré.
                _vivants = {(s, c, g) for (s, _t, c, g) in sabr_pending_groups}
                for _k in [k for k in SABR_ZONE_MIN_DIST if k not in _vivants]:
                    del SABR_ZONE_MIN_DIST[_k]

            # 3. Sécurité Drawdown
            # (#DD) Même logique que le gate de nouveau trade : check_daily_max_loss() met à
            # jour security_triggered, mais c'est ce verrou PERSISTANT qui décide si on reste
            # en pause -- une fois déclenché, relever le Max DD ne suffit plus à lui seul à
            # sortir de cette boucle.
            check_daily_max_loss()
            if security_triggered:
                pos_panic = await run_mt5(mt5.positions_get)

                # (#103) Le tri se fait AVANT l'alerte. L'ancienne version annonçait
                # "Fermeture d'urgence" dès qu'UNE position existait, sans regarder si le bot
                # avait le droit d'y toucher. Sur un compte où les positions sont manuelles
                # (magic != MAGIC_NUMBER et jamais adoptées via /suivi), il annonçait donc une
                # fermeture qu'il ne faisait pas -- et comme ces positions restent ouvertes,
                # pos_panic ne se vidait jamais et le message repartait toutes les 60 s.
                a_fermer, hors_bot = [], []
                for p in (pos_panic or []):
                    gere = (p.magic == MAGIC_NUMBER or p.ticket in MANAGED_MANUAL_TICKETS)
                    (a_fermer if gere else hors_bot).append(p)

                if not DD_PANIC_NOTIFIED:
                    DD_PANIC_NOTIFIED = True
                    lignes = ["🚨 **URGENCE : DD QUOTIDIEN DÉPASSÉ !**", ""]
                    if a_fermer:
                        lignes.append(f"Fermeture d'urgence de **{len(a_fermer)}** position(s).")
                    if hors_bot:
                        syms = ", ".join(sorted({p.symbol for p in hors_bot}))
                        lignes += [
                            "",
                            f"⚠️ **{len(hors_bot)} position(s) hors bot** ({syms}) : elles n'ont "
                            "pas été ouvertes par Kairos, il n'a pas le droit d'y toucher. "
                            "Elles restent **OUVERTES**.",
                            "👉 Ferme-les toi-même dans MetaTrader 5, ou envoie "
                            "`/suivi <ticket>` pour que le bot les prenne en charge.",
                        ]
                    if not a_fermer and not hors_bot:
                        lignes.append("Aucune position ouverte.")
                    # (#103) Ne pas se contenter de "/unlock pour reprendre" : le
                    # deverrouillage REFUSE tant que la perte du jour depasse le seuil, ce qui
                    # est precisement le cas quand des positions hors bot restent ouvertes en
                    # perte -- le bot ne pouvant pas les fermer, la perte ne redescend pas
                    # toute seule. Envoyer le client sur une commande qui va echouer, c'est le
                    # mur sur lequel il vient de perdre une matinee. On donne la sequence qui
                    # marche, en une fois.
                    lignes += ["", "**POUR REPRENDRE LE TRADING**"]
                    if hors_bot:
                        lignes += [
                            "`/unlock` sera **refusé** tant que ces positions restent ouvertes "
                            "en perte (le bot ne peut pas les fermer, donc la perte du jour ne "
                            "redescend pas).",
                            "",
                            "1. Ferme-les dans MetaTrader 5 — ou laisse-les si tu les assumes",
                            "2. `/dd off`   (coupe le disjoncteur pour aujourd'hui)",
                            "3. `/unlock`",
                            "",
                            "_Puis `/dd on` demain, une fois le compteur du jour remis à zéro._",
                        ]
                    else:
                        lignes += [
                            "`/unlock` — s'il refuse, la perte du jour dépasse encore le seuil :",
                            "`/dd off` puis `/unlock`, et `/dd on` demain.",
                        ]
                    await send_alert("\n".join(lignes))

                for p in a_fermer:
                    otype = mt5.ORDER_TYPE_SELL if p.type == mt5.ORDER_TYPE_BUY else mt5.ORDER_TYPE_BUY

                    # Ajout du prix obligatoire ici aussi !
                    tick_p = mt5.symbol_info_tick(p.symbol)
                    if not tick_p:
                        continue
                    price_p = tick_p.bid if otype == mt5.ORDER_TYPE_SELL else tick_p.ask

                    req = {
                        "action": mt5.TRADE_ACTION_DEAL, "symbol": p.symbol, "volume": p.volume,
                        "type": otype, "position": p.ticket, "price": price_p,
                        "magic": MAGIC_NUMBER, "type_filling": mt5.ORDER_FILLING_IOC
                    }
                    await run_mt5(_send_order_blocking, req)

                orders_panic = await run_mt5(mt5.orders_get)
                if orders_panic:
                    for o in orders_panic:
                        if o.magic == MAGIC_NUMBER:
                            await run_mt5(_send_order_blocking, {"action": mt5.TRADE_ACTION_REMOVE, "order": o.ticket})
                await asyncio.sleep(60)
                continue

            # (#89) Peremption des ordres INDODOG en attente. Hors de la boucle sur les
            # positions : un ordre non declenche n'a justement PAS de position associee.
            # La fonction se cadence elle-meme (10 s).
            await cancel_indo_stale_orders()

            # 4. BOUCLE PRINCIPALE
            all_positions = await run_mt5(mt5.positions_get)
            if all_positions is not None:
                
                sabr_groups = {} 
                indo_groups = {}   # (#84) canaux a echelle groupes par SIGNAL, pas par symbole
                indo_srcs = {}     # (#90) canal d'origine de chaque groupe (INDODOG ou FTS)
                
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

                    # (#84/#90) Canaux a echelle : gestion PAR SIGNAL et non par position. Une
                    # entree en zone pose 3 ordres ; les paliers de TP se calculent sur le
                    # volume TOTAL reellement pris, donc il faut voir le groupe entier avant
                    # de decider. On memorise le canal du groupe : les pourcentages et la mise
                    # a BE en dependent.
                    if is_ladder_channel(src_trade):
                        sig_key = ACTIVE_TRADE_SIGNALS.get(pos.ticket) or f"_tk{pos.ticket}"
                        indo_groups.setdefault(sig_key, []).append(pos)
                        indo_srcs[sig_key] = src_trade
                        continue
                    
                    # --- LOGIQUE STANDARD ---
                    sym = pos.symbol
                    curr = pos.price_current
                    pt = get_cached_point(sym)
                    div = get_pip_divisor(sym)
                    entry = pos.price_open

                    open_time = heure_client(pos.time)  # (#108)
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

                    CFG_TP1_PCT = 0.50

                    processed = TRADES_PROCESSED_LEVELS[pos.ticket]

                    # ==========================================
                    # 🚀 STRATÉGIE SPÉCIFIQUE : CANAL MDT_FREE (issue #22)
                    # ==========================================
                    # Le bot suit maintenant le TP1 réellement annoncé dans le signal
                    # (lu à l'ouverture, cf. TP1_PRICE_BY_TICKET) plutôt qu'un seuil de
                    # pips fixe : au TP1, on ferme 40% et on sécurise le SL au point
                    # d'entrée (BE). Le reste (60%) continue vers le TP2 (déjà le TP
                    # broker de la position) ou ressort au BE. Si le TP1 n'a pas pu être
                    # lu dans le signal (ancien format, parsing raté...), on retombe sur
                    # l'ancien filet de sécurité à +60 pips pour ne pas laisser le trade
                    # totalement sans gestion.
                    if src_trade == "CANAL MDT_FREE":
                        tp1_target = TP1_PRICE_BY_TICKET.get(pos.ticket)
                        if tp1_target:
                            hit_tp1 = (pos.type == mt5.ORDER_TYPE_BUY and curr >= tp1_target) or \
                                      (pos.type == mt5.ORDER_TYPE_SELL and curr <= tp1_target)
                        else:
                            hit_tp1 = pips >= 60

                        if hit_tp1:
                            # 1. On sécurise le BE en priorité absolue
                            if "MDT_FREE_TP1_BE" not in processed:
                                be_price = compute_be_price(entry, pos.type == mt5.ORDER_TYPE_BUY, sym)
                                update_hard_sl = False
                                if pos.type == mt5.ORDER_TYPE_BUY and pos.sl < be_price: update_hard_sl = True
                                if pos.type == mt5.ORDER_TYPE_SELL and (pos.sl > be_price or pos.sl == 0): update_hard_sl = True

                                if update_hard_sl:
                                    new_sl_price = round(be_price / pt) * pt
                                    req = {"action": mt5.TRADE_ACTION_SLTP, "symbol": sym, "position": pos.ticket, "sl": new_sl_price, "tp": pos.tp}
                                    res = await run_mt5(_send_order_blocking, req)
                                    if res and res.retcode == mt5.TRADE_RETCODE_DONE:
                                        await send_channel_alert(f"🛡️ **SL SÉCURISÉ BE (MDT FREE - TP1)**\n\nSymbole: {sym}\nTicket: {pos.ticket}", src_trade)
                                        processed.add("MDT_FREE_TP1_BE")
                                        await run_bg(save_memory)

                            # 2. On ferme 40% (Réessaie en boucle si le réseau Timeout)
                            if "MDT_FREE_TP1_PARTIAL" not in processed:
                                lbl_part = "TP1 (signal, 40%)"
                                success = await close_partial(pos, 0.40, lbl_part, pips, etape="TP1")
                                if success:
                                    processed.add("MDT_FREE_TP1_PARTIAL")
                                    await run_bg(save_memory)

                        continue

                    # ==========================================
                    # 🚀 STRATÉGIE SPÉCIFIQUE : CANAL MDT_VIP (issue #20)
                    # ==========================================
                    # Le bot suit désormais les instructions du canal lui-même (clôture
                    # partielle/totale, mise à BE) via les réponses Telegram au signal
                    # d'origine -- cf. section "GESTION DES COMMANDES ADMIN" plus bas.
                    # Le seul automatisme qui reste ici est un filet de sécurité : si le
                    # trade atteint +60 pips SANS qu'aucune instruction de BE n'ait été
                    # reçue (message manqué/supprimé), on sécurise quand même le SL au
                    # point d'entrée ET on encaisse 30% du volume, en partant du principe
                    # qu'on a loupé l'instruction.
                    if src_trade == "CANAL MDT_VIP":
                        if pips >= 60 and "VIP_BE_DONE" not in processed:
                            be_price = compute_be_price(entry, pos.type == mt5.ORDER_TYPE_BUY, sym)
                            update_hard_sl = False
                            if pos.type == mt5.ORDER_TYPE_BUY and pos.sl < be_price: update_hard_sl = True
                            if pos.type == mt5.ORDER_TYPE_SELL and (pos.sl > be_price or pos.sl == 0): update_hard_sl = True

                            if update_hard_sl:
                                new_sl_price = round(be_price / pt) * pt
                                req = {"action": mt5.TRADE_ACTION_SLTP, "symbol": sym, "position": pos.ticket, "sl": new_sl_price, "tp": pos.tp}
                                res = await run_mt5(_send_order_blocking, req)
                                if res and res.retcode == mt5.TRADE_RETCODE_DONE:
                                    await send_channel_alert(f"🛡️ **SL SÉCURISÉ BE (MDT VIP - filet de sécurité 60p, aucune instruction reçue)**\n\nSymbole: {sym}\nTicket: {pos.ticket}", src_trade)

                            # Prise partielle 30%, avec son propre marqueur.
                            partial_done = "VIP_PARTIAL60_DONE" in processed
                            if not partial_done and await close_partial(pos, 0.30, "MDT VIP 60p (30%)", pips,
                                                                    etape="TP1", pips_tp=60):
                                processed.add("VIP_PARTIAL60_DONE")
                                partial_done = True

                            # VIP_BE_DONE verrouille TOUT ce bloc (y compris pour le handler
                            # d'instruction manuelle) : on ne le pose donc qu'une fois le
                            # partiel réussi, sinon un rejet MT5 ponctuel le perdrait
                            # définitivement. Rejouer le BE ci-dessus est sans effet (il ne
                            # déplace le SL que s'il est moins bon que le point d'entrée).
                            if partial_done:
                                processed.add("VIP_BE_DONE")
                            await run_bg(save_memory)

                        continue

                    # --- GESTION TP (CORRIGÉE) ---
                    # On ne marque "Done" que si close_partial retourne True

                    # NIVEAU 1 
                    if pips >= TP1_PIPS and "TP1" not in processed:
                        lbl_part = f"TP1 ({TP1_PIPS} pips)"; spec_pct = CHANNEL_TP1_PCT.get(src_trade, None)
                        pct_close = spec_pct if spec_pct is not None else CFG_TP1_PCT
                        if await close_partial(pos, pct_close, lbl_part, pips, etape="TP1", pips_tp=TP1_PIPS):
                            daily_stats["tp1_reached"] += 1; processed.add("TP1"); await run_bg(save_memory)

                    # NIVEAU 2 
                    if pips >= TP2_PIPS and "TP2" not in processed:
                        # 1. On envoie ton message d'alerte classique
                        await send_channel_alert(f"🚀 **TP2 ({TP2_PIPS} pips) ATTEINT (+{int(pips)} pips)**\n\nSymbole: {sym} ({open_time})\nSource: {src_clean}", src_trade)
                        
                        # 2. On encaisse 50% du trade (ce qui enverra le ticket de caisse)
                        lbl_part = f"TP2 ({TP2_PIPS}p)"
                        if await close_partial(pos, 0.50, lbl_part, pips, etape="TP2", pips_tp=TP2_PIPS):
                            daily_stats["tp2_reached"] += 1; processed.add("TP2"); await run_bg(save_memory)

                    # NIVEAU 3 
                    if pips >= TP3_PIPS and "TP3" not in processed:
                        daily_stats["tp3_reached"] += 1; processed.add("TP3"); await run_bg(save_memory)
                        await send_channel_alert(f"🚀 **TP3 ({TP3_PIPS} pips) ATTEINT (+{int(pips)} pips)**\n\nSymbole: {sym} ({open_time})\nSource: {src_clean}", src_trade)

                    # NIVEAU 4 (140 pips)
                    if pips >= TP4_PIPS and "TP4" not in processed:
                        processed.add("TP4"); daily_stats["tp4_reached"] += 1; await run_bg(save_memory)
                        await send_channel_alert(f"🚀 **TP4 ({TP4_PIPS} pips) ATTEINT (+{int(pips)} pips)**\n\nSymbole: {sym} ({open_time})\nSource: {src_clean}", src_trade)

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
                            await send_channel_alert(f"🚀 **TP ZONE {hundreds} ATTEINT (+{int(pips)} pips)**\n\nSymbole: {sym} ({open_time})\nSource: {src_clean}", src_trade)

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
                        if SOFT_SL_ACTIVE and current_max_pips >= TRAILING_STOP_PIPS:  # (#10) toggle Soft SL
                            soft_trigger_pips = current_max_pips - TRAILING_STOP_PIPS
                            if pips <= soft_trigger_pips:
                                tag = "SOFT_SL_HIT"
                                if tag not in TRADES_PROCESSED_LEVELS[pos.ticket]:
                                    TRADES_PROCESSED_LEVELS[pos.ticket].add(tag); await run_bg(save_memory)
                                    pct_txt = int(SOFT_SL_PERCENT * 100)

                                    # (#103) Le message annoncait "Le reste court avec SL a BE"
                                    # dans TOUS les cas. Or la mise a BE est posee plus bas et
                                    # uniquement si SL_SUIVEUR_ACTIVE : avec le SL Suiveur sur
                                    # OFF, le client lisait que son reliquat etait protege alors
                                    # que son stop n'avait pas bouge d'un pip.
                                    if not SL_SUIVEUR_ACTIVE:
                                        suite_sl = ("⚠️ SL Suiveur désactivé : le stop n'a **pas** "
                                                    "été déplacé, le reste court avec son SL d'origine.")
                                    elif new_sl_price:
                                        suite_sl = "Le reste court avec SL à BE."
                                    else:
                                        suite_sl = "Le reste court avec son SL actuel."

                                    await send_channel_alert(f"📉 **REPLI DÉTECTÉ (Soft SL)**\n\n"
                                                     f"Symbole: {sym} ({open_time})\n\n"
                                                     f"Sommet: +{int(current_max_pips)}p | Actuel: +{int(pips)}p\n"
                                                     f"Action: Clôture {pct_txt}%. {suite_sl}", src_trade)

                                    success = await close_partial(pos, SOFT_SL_PERCENT, "Soft Trailing", pips, etape="SL suiv")
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

                    if new_sl_price and SL_SUIVEUR_ACTIVE:  # (#10) toggle SL Suiveur
                        info_sl = mt5.symbol_info(sym)
                        new_sl_price = round(new_sl_price / info_sl.point) * info_sl.point
                        
                        # 🛡️ LE BOUCLIER ANTI-SPAM MT5 (Évite la boucle infinie)
                        # (#31) req n'existe QUE dans ce bloc -- l'envoi de l'ordre doit rester
                        # dedans, sinon on utilise un `req` non défini (NameError) ou périmé
                        # (mauvais ticket/SL d'une itération précédente).
                        if abs(new_sl_price - pos.sl) > (info_sl.point / 2):
                            req = {"action": mt5.TRADE_ACTION_SLTP, "symbol": sym, "position": pos.ticket, "sl": new_sl_price, "tp": pos.tp}

                            # --- CORRECTION CRITIQUE SL ---
                            # On utilise la fonction de retry _send_order_blocking pour être SÛR que le SL bouge
                            # même si le réseau est instable
                            res = await run_mt5(_send_order_blocking, req)

                            if res is None:
                                logger.warning(f"⚠️ MT5 Timeout (SL) sur ticket {pos.ticket}")
                                # Pas de continue ici, on laisse la boucle tourner pour les autres trades

                            elif res.retcode == mt5.TRADE_RETCODE_DONE:
                                log(f"🛡️ SL DÉPLACÉ: {sym} -> {new_sl_price} ({sl_reason})", important=True)
                                if "Palier" in sl_reason or "Secu BE" in sl_reason:
                                    await send_channel_alert(f"🛡️ **SL SÉCURISÉ** ({sym})\n\n"
                                                     f"Symbole: {sym} ({open_time})\n"
                                                     f"Source: {src_clean}\n"
                                                     f"Niveau : {sl_reason}", src_trade)

                # --- (#26) GESTION SABR VIP/Free/Copie : BE + 60% à 100p, +10% à 150p ---
                # (remplace l'ancien systeme TP1@100/25%, BE@150, trailing@200, cascade@250+)
                for sym, positions in sabr_groups.items():
                    if not positions: continue
                    tick = mt5.symbol_info_tick(sym)
                    if not tick: continue

                    first_pos = positions[0]
                    is_buy = (first_pos.type == mt5.ORDER_TYPE_BUY)

                    curr_price = tick.bid if is_buy else tick.ask
                    pt = get_cached_point(sym)
                    div = get_pip_divisor(sym)

                    for p in positions:
                        current_sl = p.sl
                        if is_buy: pips_indiv = ((curr_price - p.price_open) / pt) / div
                        else: pips_indiv = ((p.price_open - curr_price) / pt) / div

                        # Stats MAE/MFE
                        if pips_indiv > active_trades_max_pips.get(p.ticket, 0): active_trades_max_pips[p.ticket] = pips_indiv
                        if pips_indiv < active_trades_min_pips.get(p.ticket, 999): active_trades_min_pips[p.ticket] = pips_indiv

                        # --- 100 PIPS : BE + CLÔTURE 60% DU VOLUME INITIAL ---
                        if pips_indiv >= 100:
                            tag_100 = "SABR_100_BE_60"
                            if tag_100 not in TRADES_PROCESSED_LEVELS[p.ticket]:
                                # 1. BE (priorité absolue, comme avant)
                                # (#93) En mode BE PARTIEL, on ne touche PAS au SL : on se
                                # contente d'armer le retour sur l'entrée, traité plus bas.
                                # La clôture des 60 % ci-dessous, elle, a lieu dans les deux modes.
                                be_p = compute_be_price(p.price_open, is_buy, sym)
                                new_sl = None
                                if SABR_BE_PARTIEL_ACTIVE:
                                    TRADES_PROCESSED_LEVELS[p.ticket].add("SABR_BE_ARME")
                                else:
                                    if is_buy:
                                        if current_sl < be_p: new_sl = be_p
                                    else:
                                        if current_sl > be_p or current_sl == 0: new_sl = be_p

                                if new_sl:
                                    new_sl = round(new_sl / pt) * pt
                                    if abs(new_sl - current_sl) > (pt / 2):
                                        req = {"action": mt5.TRADE_ACTION_SLTP, "symbol": sym, "position": p.ticket, "sl": new_sl, "tp": p.tp}
                                        res = await run_mt5(_send_order_blocking, req)
                                        if res and res.retcode == mt5.TRADE_RETCODE_DONE:
                                            p_open_time = heure_client(p.time)  # (#108)
                                            p_src_clean = ACTIVE_TRADE_SOURCES.get(p.ticket, "INCONNU").replace("CANAL ", "").replace("_", " ")
                                            await send_channel_alert(f"☄️ **SABR: BE ACTIVÉ (+100p)**\n\n"
                                                             f"Symbole: {sym} ({p_open_time})\n"
                                                             f"Source: {p_src_clean}\n"
                                                             f"Ticket: {p.ticket}", ACTIVE_TRADE_SOURCES.get(p.ticket, ""))
                                            await noter(tag_canal(ACTIVE_TRADE_SOURCES.get(p.ticket, "")),
                                                        f"☄️ SL AU BE (+100 pips) — ticket {p.ticket} | "
                                                        f"SL -> {new_sl}")   # (#111)
                                            # Le SL suiveur ci-dessous compare a current_sl :
                                            # sans cette mise a jour il repartirait de la
                                            # valeur d'avant le BE et renverrait un ordre
                                            # identique dans la foulee.
                                            current_sl = new_sl

                                # 2. Clôture 60% du volume initial
                                deals = await run_mt5(mt5.history_deals_get, position=p.ticket)
                                initial_vol = deals[0].volume if (deals and len(deals) > 0) else p.volume

                                vol_to_close = round(initial_vol * 0.60, 2)
                                if vol_to_close < 0.01: vol_to_close = 0.01

                                if round(p.volume - vol_to_close, 2) >= 0.01:
                                    success = await close_partial(p, 0.0, "SABR 100p (BE+60%)", pips_indiv, fixed_lot=vol_to_close,
                                                                  etape="TP1", pips_tp=100)
                                    if success:
                                        TRADES_PROCESSED_LEVELS[p.ticket].add(tag_100)
                                        await run_bg(save_memory)
                                else:
                                    TRADES_PROCESSED_LEVELS[p.ticket].add(tag_100)

                        # --- (#93) BE PARTIEL : RETOUR SUR LE PRIX D'ENTRÉE ---
                        # Armé à +100 pips (le SL n'a pas bougé). Si le prix revient toucher
                        # l'entrée, on vend une part du volume INITIAL et on laisse courir le
                        # reste avec son stop d'origine. Une seule fois par position.
                        # Le seuil est le prix d'entrée BRUT : BE_OFFSET_PIPS décale le SL lors
                        # d'une mise à BE, or ici on n'en fait pas -- l'y appliquer déplacerait
                        # silencieusement le déclencheur.
                        if (SABR_BE_PARTIEL_ACTIVE
                                and "SABR_BE_ARME" in TRADES_PROCESSED_LEVELS[p.ticket]
                                and "SABR_BE_PARTIEL_FAIT" not in TRADES_PROCESSED_LEVELS[p.ticket]):
                            revenu = (curr_price <= p.price_open) if is_buy else (curr_price >= p.price_open)
                            if revenu:
                                # Le pourcentage porte sur le volume ENCORE OUVERT (p.volume),
                                # pas sur le volume initial : les 60 % de +100 pips sont deja
                                # partis, et raisonner sur l'initial rendait tout reglage
                                # >= 40 % equivalent a une cloture totale, sans le dire.
                                pct_be = max(0.01, min(1.0, SABR_BE_PARTIEL_PCT))
                                vol_be = round(p.volume * pct_be, 2)
                                if vol_be < 0.01: vol_be = 0.01

                                if round(p.volume - vol_be, 2) >= 0.01:
                                    ok_be = await close_partial(p, 0.0,
                                                                "SABR retour BE (%d%%)" % int(pct_be * 100),
                                                                pips_indiv, fixed_lot=vol_be, etape="BE")
                                    reste_be = round(p.volume - vol_be, 2)
                                else:
                                    # Le reliquat serait sous le lot minimum du courtier : on
                                    # solde la position plutôt que de la rendre inexploitable.
                                    vol_be = p.volume
                                    ok_be = await close_partial(p, 0.0,
                                                                "SABR retour BE (solde)",
                                                                pips_indiv, fixed_lot=vol_be, etape="BE")
                                    reste_be = 0.0
                                if ok_be:
                                    TRADES_PROCESSED_LEVELS[p.ticket].add("SABR_BE_PARTIEL_FAIT")
                                    await run_bg(save_memory)
                                    # (#104) Italique Telethon = __texte__. Un seul souligné
                                    # n'est pas reconnu et s'affiche tel quel chez le client.
                                    _suite = (f"__Le SL d'origine est conservé sur les "
                                              f"{reste_be:.2f} lot(s) restants.__" if reste_be > 0
                                              else "__Position soldée.__")
                                    await send_channel_alert(
                                        f"🩹 **SABR : RETOUR SUR L'ENTRÉE**\n\n"
                                        f"Symbole: {sym}\nTicket: {p.ticket}\n"
                                        f"Vendu: **{vol_be:.2f}** lot(s) ({int(pct_be * 100)} % du volume restant)\n\n"
                                        f"{_suite}",
                                        ACTIVE_TRADE_SOURCES.get(p.ticket, ""))

                        # --- 150 PIPS : +10% SUPPLÉMENTAIRES DU VOLUME INITIAL ---
                        if pips_indiv >= 150:
                            tag_150 = "SABR_150_PLUS10"
                            if tag_150 not in TRADES_PROCESSED_LEVELS[p.ticket]:
                                deals = await run_mt5(mt5.history_deals_get, position=p.ticket)
                                initial_vol = deals[0].volume if (deals and len(deals) > 0) else p.volume

                                vol_to_close = round(initial_vol * 0.10, 2)
                                if vol_to_close < 0.01: vol_to_close = 0.01

                                if round(p.volume - vol_to_close, 2) >= 0.01:
                                    success = await close_partial(p, 0.0, "SABR 150p (+10%)", pips_indiv, fixed_lot=vol_to_close,
                                                                  etape="TP2", pips_tp=150)
                                    if success:
                                        TRADES_PROCESSED_LEVELS[p.ticket].add(tag_150)
                                        await run_bg(save_memory)
                                else:
                                    TRADES_PROCESSED_LEVELS[p.ticket].add(tag_150)

                        # --- (#73) SL SUIVEUR SABR (optionnel) ---
                        # Les positions SABR n'avaient jusqu'ici aucun trailing : uniquement un
                        # BE a +100p et deux prises partielles. Le SL ne se declenche qu'a
                        # partir de SABR_TRAILING_PIPS de gain, ce qui garantit qu'il ne peut
                        # jamais etre pose dans le rouge : a l'instant du declenchement il vaut
                        # exactement le prix d'entree, puis il ne fait que suivre.
                        if SABR_TRAILING_ACTIVE and SABR_TRAILING_PIPS > 0 and pips_indiv >= SABR_TRAILING_PIPS:
                            dist_trail = SABR_TRAILING_PIPS * pt * div
                            target_sl = curr_price - dist_trail if is_buy else curr_price + dist_trail
                            target_sl = round(target_sl / pt) * pt
                            # Un SL ne recule JAMAIS : on ne bouge que si la nouvelle valeur
                            # protege davantage. current_sl == 0 signifie "aucun SL pose".
                            better = (target_sl > current_sl) if is_buy else (target_sl < current_sl or current_sl == 0)
                            if better and abs(target_sl - current_sl) > (pt / 2):
                                req_tr = {"action": mt5.TRADE_ACTION_SLTP, "symbol": sym,
                                          "position": p.ticket, "sl": target_sl, "tp": p.tp}
                                res_tr = await run_mt5(_send_order_blocking, req_tr)
                                if res_tr and res_tr.retcode == mt5.TRADE_RETCODE_DONE:
                                    current_sl = target_sl
                                    p_src_tr = ACTIVE_TRADE_SOURCES.get(p.ticket, "INCONNU").replace("CANAL ", "").replace("_", " ")
                                    await send_channel_alert(
                                        f"📈 **SABR : SL SUIVEUR** (+{int(pips_indiv)} pips)\n\n"
                                        f"Symbole: {sym}\nSource: {p_src_tr}\n"
                                        f"Ticket: {p.ticket}\nNouveau SL: {target_sl}",
                                        ACTIVE_TRADE_SOURCES.get(p.ticket, ""))

                # ==========================================
                # 🪜 GESTION DES CANAUX A ECHELLE PAR SIGNAL (#84/#90)
                # ==========================================
                # INDODOG et FTS. Une entree en zone pose 3 ordres. Les paliers de TP se
                # calculent sur le volume TOTAL reellement pris (donc seulement les ordres
                # REMPLIS), et non position par position : c'est la regle voulue cote canal.
                for sig_key, positions in indo_groups.items():
                    if not positions: continue
                    ladder = INDO_TP_BY_TICKET.get(positions[0].ticket) or []
                    if len(ladder) < 2: continue

                    src_ladder = indo_srcs.get(sig_key, "CANAL INDODOG")
                    nom_ladder = src_ladder.replace("CANAL ", "")
                    # (#110) Echelle figee a la pose. Un signal pose par un exe anterieur n'en a
                    # pas : il recoit celle en vigueur, figee a son tour des maintenant.
                    echelle = INDO_ECHELLE_BY_SIGNAL.get(sig_key)
                    if not echelle:
                        echelle = INDO_ECHELLE_BY_SIGNAL[sig_key] = ladder_echelle(src_ladder)
                        await run_bg(save_memory)

                    ref = positions[0]
                    sym_i = ref.symbol
                    is_buy_i = (ref.type == mt5.ORDER_TYPE_BUY)
                    tick_i = mt5.symbol_info_tick(sym_i)
                    if not tick_i: continue
                    curr_i = tick_i.bid if is_buy_i else tick_i.ask
                    pt_i = get_cached_point(sym_i); div_i = get_pip_divisor(sym_i)

                    # Les marqueurs de palier sont poses sur le SIGNAL, pas sur un ticket :
                    # sinon chaque ordre de la zone reprendrait l'echelle depuis le debut.
                    done = INDO_LEVELS_BY_SIGNAL.setdefault(sig_key, set())

                    # Volume de reference = somme des positions ouvertes du signal, FIGEE au
                    # premier palier. Sans ce gel, l'annulation des ordres en attente ferait
                    # fondre la base et les 20 % suivants porteraient sur un total plus petit.
                    base_vol = INDO_BASE_VOL_BY_SIGNAL.get(sig_key)
                    if base_vol is None:
                        base_vol = round(sum(p.volume for p in positions), 2)

                    # (#104) BE anticipé sur un signal dont on n'a pas su lire les TP : le
                    # premier palier de repli est à 50 pips, on sécurise dès 30. Ne s'applique
                    # QU'AUX signaux marqués, jamais aux signaux normaux -- eux gardent leur
                    # mise à BE au TP1, comme le canal le recommande.
                    if TAG_SIGNAL_DEFAUT in done and "INDO_BE" not in done:
                        pips_ref = (((curr_i - ref.price_open) / pt_i) / div_i if is_buy_i
                                    else ((ref.price_open - curr_i) / pt_i) / div_i)
                        if pips_ref >= INDO_BE_DEFAUT_PIPS:
                            for p in positions:
                                be_d = compute_be_price(p.price_open, is_buy_i, sym_i)
                                besoin = (is_buy_i and p.sl < be_d) or \
                                         ((not is_buy_i) and (p.sl > be_d or p.sl == 0))
                                if besoin:
                                    await run_mt5(_send_order_blocking,
                                                  {"action": mt5.TRADE_ACTION_SLTP, "symbol": sym_i,
                                                   "position": p.ticket,
                                                   "sl": round(be_d / pt_i) * pt_i, "tp": p.tp})
                            done.add("INDO_BE")
                            await run_bg(save_memory)
                            await noter(tag_canal(src_ladder), f"🛡 SL AU BE (+{INDO_BE_DEFAUT_PIPS} pips, "
                                                               f"signal sans TP lisible) — signal {sig_key}")   # (#111)
                            await send_channel_alert(
                                ("🛡 **%s : SL AU BE** (+%d pips)" % (nom_ladder, INDO_BE_DEFAUT_PIPS))
                                + chr(10) + chr(10) +
                                ("Symbole: %s" % sym_i) + chr(10) +
                                "__Signal sans TP : sécurisation anticipée.__", src_ladder)

                    for idx, tp_price in enumerate(ladder[:-1], start=1):
                        tag = "INDO_TP%d" % idx
                        if tag in done: continue
                        atteint = (curr_i >= tp_price) if is_buy_i else (curr_i <= tp_price)
                        if not atteint: break   # paliers ordonnes : inutile de tester la suite

                        # --- Au PREMIER palier : on fige la base et on annule le reste de la zone
                        if idx == 1:
                            base_vol = round(sum(p.volume for p in positions), 2)
                            INDO_BASE_VOL_BY_SIGNAL[sig_key] = base_vol
                            annules = await cancel_pending_of_signal(sig_key)
                            if annules:
                                await send_channel_alert(
                                    ("🗑 **%s : ZONE NETTOYEE**" % nom_ladder) + chr(10) + chr(10) +
                                    ("%d ordre(s) en attente annule(s) apres le TP1." % annules),
                                    src_ladder)

                        pct_i = ladder_pct(echelle, idx)   # (#110)
                        # Arrondi a l'INFERIEUR sur le pas du courtier. Avec un arrondi au plus
                        # proche, 20 % de 0.08 donne 0.016 -> 0.02, et les quatre paliers
                        # consommaient 100 % du volume : il ne restait plus rien a fermer au TP
                        # final, alors que la regle veut qu'une part y arrive toujours.
                        info_i = mt5.symbol_info(sym_i)
                        step_z = info_i.volume_step if (info_i and info_i.volume_step > 0) else 0.01
                        vol_cible = math.floor((base_vol * pct_i) / step_z) * step_z
                        vol_cible = round(vol_cible, 2)
                        if vol_cible < step_z: vol_cible = round(step_z, 2)

                        pips_i = ((curr_i - ref.price_open) / pt_i) / div_i if is_buy_i                                  else ((ref.price_open - curr_i) / pt_i) / div_i

                        ferme = await close_volume_across(positions, vol_cible,
                                                          "%s TP%d (%g%%)" % (nom_ladder, idx, round(pct_i * 100, 1)),
                                                          pips_i, etape_mt5="TP%d" % idx)
                        if ferme > 0:
                            done.add(tag)
                            ladder_stats_tp(src_ladder, sig_key, idx)          # (#87/#90)
                            await run_bg(save_ladder_stats, src_ladder)
                            await run_bg(save_memory)

                            # BE au TP1 : recommande par INDODOG dans chaque message. FTS n'en
                            # parle pas, on ne l'impose donc pas (cf. ladder_be_at_tp1).
                            if idx == 1 and ladder_be_at_tp1(src_ladder) and "INDO_BE" not in done:
                                for p in positions:
                                    be_i = compute_be_price(p.price_open, is_buy_i, sym_i)
                                    besoin = (is_buy_i and p.sl < be_i) or                                              ((not is_buy_i) and (p.sl > be_i or p.sl == 0))
                                    if besoin:
                                        req_i = {"action": mt5.TRADE_ACTION_SLTP, "symbol": sym_i,
                                                 "position": p.ticket,
                                                 "sl": round(be_i / pt_i) * pt_i, "tp": p.tp}
                                        await run_mt5(_send_order_blocking, req_i)
                                done.add("INDO_BE")
                                await noter(tag_canal(src_ladder), f"🛡 SL AU BE (TP1 atteint) — signal {sig_key}")   # (#111)
                                await send_channel_alert(
                                    ("🛡 **%s : SL AU BE** (TP1 atteint)" % nom_ladder) + chr(10) + chr(10) +
                                    ("Symbole: %s" % sym_i), src_ladder)
                        break   # un seul palier par cycle : on relit les volumes au suivant

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

    btn_webapp = KeyboardButtonWebView(text="☀️ Ouvrir le Dashboard", url=DASHBOARD_URL)
    
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
            await event.reply("🔒 **Sécurité :** Veuillez d'abord lier votre compte en tapant : `/start CODE_PIN`")
            
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
    global RISK_PERCENT_GOLD, RISK_PERCENT_OTHER, security_triggered, DD_PANIC_NOTIFIED
    global RISK_PERCENT_MDT_VIP, RISK_PERCENT_MDT_FREE
    global FIXED_LOT_SABR_VIP, FIXED_LOT_SABR_FREE
    global RISK_PERCENT_SPACE, RISK_PERCENT_LIGUE, RISK_PERCENT_PERSO
    global TRAILING_STOP_PIPS, MAX_DAILY_DRAWDOWN
    global SOFT_SL_PERCENT, RISK_PERCENT_INDICES
    global TIME_OFF_PEAK_FACTOR, PRIME_HOURS
    global SABR_ANTI_DOUBLON_ACTIVE, SABR_FORWARD_ACTIVE  # (#11) SABR_FORWARD_ACTIVE manquait -> /forward_sabr ne faisait rien

    # 👉 LE BOUCLIER EST ICI : On bloque tout ce qui n'est pas le maître
    if ADMIN_ID == 0 or event.sender_id != ADMIN_ID: # 👈 CORRECTION 5 (sender_id)
        return

    # (#30) Le fix précédent comparait event.chat_id à TELEGRAM_DESTINATION_ID (capturé depuis
    # event.sender_id lors du /start PIN en DM -- deux valeurs différentes dans ce contexte),
    # ce qui bloquait TOUTES les commandes partout. Cette fois : liste noire explicite des
    # canaux du bot de licence (où il répond déjà lui-même), pour éviter le double /help sans
    # toucher au reste. ID en dur (pas de dépendance à admin_license_bot_V3.py, process séparé).
    LICENSE_BOT_CHANNELS = {
        -1003872071233,  # SECURITY_CHANNEL_ID (canal de licence/sécurité)
        -1004458498840,  # FEEDBACK_CHANNEL_ID (canal de support/mailbox)
    }
    if event.chat_id in LICENSE_BOT_CHANNELS:
        return

    text = event.raw_text.lower().strip()

# --- 1. STATUS & INFO ---
    if text == "/status" or text == "/balance" or text == "/info":
        if not await run_mt5(mt5.initialize): await event.reply("❌ MT5 Off"); return
        acc = await run_mt5(mt5.account_info)
        
        daily_total, daily_realized, daily_float = get_accurate_daily_stats()
        
        start_bal_theo = acc.balance - daily_realized
        daily_pct = (daily_total / start_bal_theo * 100) if start_bal_theo > 0 else 0.0
        
        pos = await run_mt5(mt5.positions_get, group="*")
        nb = len([p for p in pos if p.magic == MAGIC_NUMBER or p.ticket in MANAGED_MANUAL_TICKETS]) if pos else 0
        
        risk_g = round(RISK_PERCENT_GOLD * 100, 2)
        risk_o = round(RISK_PERCENT_OTHER * 100, 2)
        risk_i = round(RISK_PERCENT_INDICES * 100, 2)

        # (#pack) Un canal hors du pack du client ne doit plus apparaître DU TOUT (avant : ligne
        # affichée avec un "🔇", ce qui exposait l'existence de canaux non payés). Un canal du
        # pack simplement mis en sourdine garde lui sa ligne, avec le 🔇 -- c'est une info utile.
        def fmt_ch(val, ch_name, is_lot=False):
            if ch_name in MUTED_CHANNELS:
                return "🔇"
            if is_lot:
                return f"{val} lot"
            return f"{val*100:.2f}%" if val else "Auto"

        def line_if_allowed(*pairs):
            """Construit 'A: x | B: y' en ne gardant que les canaux du pack. '' si aucun."""
            kept = [f"{label}: **{fmt_ch(val, ch, is_lot)}**"
                    for label, val, ch, is_lot in pairs if channel_allowed(ch)]
            return (" | ".join(kept) + "\n") if kept else ""

        show_risk_engine = pack_uses_risk_engine()

        secu_state = "🔴 ACTIVÉE (STOP)" if security_triggered else "🟢 VEILLE (OK)"
        icon_day = "🟢" if daily_total >= 0 else "🔴"
        
        if TRAILING_STOP_PIPS and TRAILING_STOP_PIPS > 0:
            sl_info = f"Actif (**{TRAILING_STOP_PIPS} pips**)"
        else:
            sl_info = "🔒 Inactif (Mode Paliers)"

        soft_txt = f"{SOFT_SL_PERCENT*100:.0f}%"
        doublon_state = "🛡️ Actif" if SABR_ANTI_DOUBLON_ACTIVE else "⚠️ Désactivé (Risque Spam)"

        # --- ⏳ Calcul du Statut Horaire Actuel ---
        current_h = datetime.now().hour
        is_prime = current_h in PRIME_HOURS
        time_status = "🌞 **Heure Pleine** (Risque normal)" if is_prime else f"🌙 **Heure Creuse** (Risque réduit x{TIME_OFF_PEAK_FACTOR})"
        
        # --- 📅 Formatage des Risques Journaliers ---
        days_abbr = {0: "L", 1: "M", 2: "M", 3: "J", 4: "V", 5: "S", 6: "D"}
        # Le ":g" permet d'afficher "1" au lieu de "1.0", mais garde "0.5" tel quel !
        risk_days_str = ", ".join([f"{days_abbr[k]}:{v:g}" for k, v in sorted(DAY_RISK_FACTORS.items())])

        # (#pack) SL suiveur, % globaux (or/indices/devises) et planning heure/jour ne
        # s'appliquent JAMAIS aux canaux SABR (lots fixes / risk_override / positions déviées
        # avant le bloc trailing) -- on les masque pour un pack 100% SABR, cf.
        # pack_uses_risk_engine().
        sl_block = f"**SL Suiveur: {sl_info}** (Vente: {soft_txt})\n" if show_risk_engine else ""
        globals_block = f"Or: **{risk_g}%** | Indices: **{risk_i}%** | Autre: **{risk_o}%**\n" if show_risk_engine else ""
        planning_block = f"\n{time_status}\n\n📅 Jours : **{risk_days_str}**" if show_risk_engine else ""
        doublon_block = f"**Anti-Doublon SABR:** {doublon_state}\n" if (
            channel_allowed("CANAL SABR_VIP") or channel_allowed("CANAL SABR_FREE")) else ""

        channels_block = (
            line_if_allowed(("MDT VIP", RISK_PERCENT_MDT_VIP, "CANAL MDT_VIP", False),
                            ("MDT Free", RISK_PERCENT_MDT_FREE, "CANAL MDT_FREE", False))
            + line_if_allowed(("SABR VIP", FIXED_LOT_SABR_VIP, "CANAL SABR_VIP", True),
                              ("SABR Free", FIXED_LOT_SABR_FREE, "CANAL SABR_FREE", True))
            + line_if_allowed(("Space", RISK_PERCENT_SPACE, "CANAL SPACE", False),
                              ("Ligue", RISK_PERCENT_LIGUE, "CANAL LIGUE", False),
                              ("Perso", RISK_PERCENT_PERSO, "CANAL PERSO", False))
        )

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
                          f"{sl_block}"
                          f"{doublon_block}"
                          f"\n"
                          f"{globals_block}"
                          f"{channels_block}"
                          f"{planning_block}")

# --- 2. FERMETURE TOTALE ---
    elif text == "/closeall":
        pos_list = await run_mt5(mt5.positions_get)
        c = 0
        if pos_list:
            for p in pos_list:
                if p.magic == MAGIC_NUMBER or p.ticket in MANAGED_MANUAL_TICKETS:
                    otype = mt5.ORDER_TYPE_SELL if p.type == mt5.ORDER_TYPE_BUY else mt5.ORDER_TYPE_BUY
                    req = {"action": mt5.TRADE_ACTION_DEAL, "symbol": p.symbol, "volume": p.volume, "type": otype, "position": p.ticket, "magic": MAGIC_NUMBER, "type_filling": mt5.ORDER_FILLING_IOC}
                    await run_mt5(_send_order_blocking, req); c += 1
        await event.reply(f"🚨 **PANIC:** {c} trades fermés.")

# --- 3. FERMETURE CIBLÉE ---
    elif text.startswith("/close "):
        try:
            parts = text.split(); symbol_key = parts[1].upper()
            symbol_to_close = SYMBOL_MAPPING.get(symbol_key, symbol_key)
            pos_list = await run_mt5(mt5.positions_get)
            count = 0
            if pos_list:
                for p in pos_list:
                    if symbol_to_close in p.symbol.upper() and (p.magic == MAGIC_NUMBER or p.ticket in MANAGED_MANUAL_TICKETS):
                        otype = mt5.ORDER_TYPE_SELL if p.type == mt5.ORDER_TYPE_BUY else mt5.ORDER_TYPE_BUY
                        req = {"action": mt5.TRADE_ACTION_DEAL, "symbol": p.symbol, "volume": p.volume, "type": otype, "position": p.ticket, "magic": MAGIC_NUMBER, "type_filling": mt5.ORDER_FILLING_IOC}
                        await run_mt5(_send_order_blocking, req); count += 1
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
        if not channel_allowed("CANAL MDT_VIP"): await event.reply("❌ MDT_VIP ne fait pas partie de ton pack."); return
        try:
            val_raw = text.split()[1]
            if val_raw.lower() == "auto": RISK_PERCENT_MDT_VIP = None
            else: RISK_PERCENT_MDT_VIP = float(val_raw) / 100 
            await run_bg(save_settings); await event.reply(f"💎 Risque MDT VIP : **{val_raw}%**")
        except: await event.reply("❌ Usage: /risk_mdt_vip 1.0 (ou 'auto')")

    # MDT FREE (Anciennement Gratuit)
    elif text.startswith("/risk_mdt_free") or text.startswith("/risk_gratuit"):
        if not channel_allowed("CANAL MDT_FREE"): await event.reply("❌ MDT_FREE ne fait pas partie de ton pack."); return
        try:
            val_raw = text.split()[1]
            if val_raw.lower() == "auto": RISK_PERCENT_MDT_FREE = None
            else: RISK_PERCENT_MDT_FREE = float(val_raw) / 100 
            await run_bg(save_settings); await event.reply(f"🎁 Risque MDT FREE : **{val_raw}%**")
        except: await event.reply("❌ Usage: /risk_mdt_free 1.0 (ou 'auto')")


    # (#84) INDODOG : lot fixe, comme SABR (pas de division par 100, c'est un volume)
    elif text.startswith("/lot_indodog"):
        global FIXED_LOT_INDODOG
        if not channel_allowed("CANAL INDODOG"):
            await event.reply("❌ INDODOG ne fait pas partie de ton pack."); return
        try:
            FIXED_LOT_INDODOG = max(0.01, float(text.split()[1]))
            await run_bg(save_settings)
            await event.reply(f"🍽 Lot Fixe INDODOG : **{FIXED_LOT_INDODOG} lots**\n"
                              f"Échelle (signal à 5 TP) : "
                              f"{decrire_echelle(ladder_echelle('CANAL INDODOG'), 5)}\n"
                              f"__La changer : `/echelle_indodog 10 20`__")
        except: await event.reply("❌ Usage: /lot_indodog 0.10")

    # (#93) Mode de mise a BE pour SABR : classique (SL au BE) ou partiel (SL inchange, on
    # vend une part si le prix revient sur l'entree).
    elif text.startswith("/be_partiel"):
        global SABR_BE_PARTIEL_ACTIVE, SABR_BE_PARTIEL_PCT
        if not (channel_allowed("CANAL SABR_VIP") or channel_allowed("CANAL SABR_FREE")
                or channel_allowed("CANAL SABR_COPIE")):
            await event.reply("❌ SABR ne fait pas partie de ton pack."); return
        arg = text.split()[1].lower() if len(text.split()) > 1 else ""
        if arg in ("off", "classique", "0"):
            SABR_BE_PARTIEL_ACTIVE = False
        elif arg in ("on", "partiel"):
            SABR_BE_PARTIEL_ACTIVE = True
        elif arg:
            try:
                SABR_BE_PARTIEL_PCT = max(0.01, min(1.0, float(arg.replace(",", ".")) / 100.0))
                SABR_BE_PARTIEL_ACTIVE = True
            except Exception:
                await event.reply("❌ Usage: `/be_partiel on|off` ou `/be_partiel 50` (%)"); return
        if arg:
            await run_bg(save_settings)
        if SABR_BE_PARTIEL_ACTIVE:
            await event.reply(f"🩹 **BE SABR : PARTIEL**\n\n"
                              f"À +100 pips le SL **ne bouge pas**.\n"
                              f"Si le prix revient sur l'entrée, on vend "
                              f"**{int(SABR_BE_PARTIEL_PCT * 100)} %** de ce qui est **encore "
                              f"ouvert** à ce moment-là.\n\n"
                              f"__Changer la part : `/be_partiel 50` · 100 % = tout fermer__")
        else:
            await event.reply("☄️ **BE SABR : CLASSIQUE**\n\n"
                              "À +100 pips le SL remonte au prix d'entrée (comportement "
                              "historique).\n\n_Passer en partiel : `/be_partiel on`_")

    elif text.startswith("/lot_fts"):
        global FIXED_LOT_FTS
        if not channel_allowed("CANAL FTS"):
            await event.reply("❌ FTS ne fait pas partie de ton pack."); return
        try:
            FIXED_LOT_FTS = max(0.01, float(text.split()[1]))
            await run_bg(save_settings)
            await event.reply(f"🚀 Lot Fixe FTS : **{FIXED_LOT_FTS} lots**\n"
                              f"Échelle (signal à 5 TP) : "
                              f"{decrire_echelle(ladder_echelle('CANAL FTS'), 5)}\n"
                              f"__La changer : `/echelle_fts 20`__")
        except: await event.reply("❌ Usage: /lot_fts 0.10")

    # (#110) Echelle de TP des canaux a paliers : % de la position INITIALE ferme a chaque TP.
    # Sans valeur : affiche l'echelle. Les signaux deja poses gardent la leur.
    elif text.startswith("/echelle_indodog") or text.startswith("/echelle_fts"):
        _src = "CANAL FTS" if text.startswith("/echelle_fts") else "CANAL INDODOG"
        _nom = _src.replace("CANAL ", "")
        _cmd = "/echelle_fts" if _src == "CANAL FTS" else "/echelle_indodog"
        if not channel_allowed(_src):
            await event.reply(f"❌ {_nom} ne fait pas partie de ton pack."); return
        _args = text.split(maxsplit=1)[1] if len(text.split(maxsplit=1)) > 1 else ""
        if _args:
            _ech, _err = regler_echelle(_src, _args)
            if _err:
                await event.reply(f"❌ {_err}\nUsage : `{_cmd} 10 20` — un % par TP, "
                                  f"le dernier vaut pour les suivants."); return
            await run_bg(save_settings)
        _ech = ladder_echelle(_src)
        _alerte = alerte_echelle(_ech)
        await event.reply(
            f"🪜 **ÉCHELLE {_nom}**{' modifiée' if _args else ''}\n\n"
            f"Réglage : **{' · '.join(_fmt_pct(v) for v in _ech)}**\n"
            f"__% de la position initiale, palier par palier ; le dernier chiffre vaut pour "
            f"les paliers suivants, et le DERNIER TP du signal ferme toujours le reste.__\n\n"
            f"Signal à 3 TP : {decrire_echelle(_ech, 3)}\n"
            f"Signal à 5 TP : {decrire_echelle(_ech, 5)}"
            + (f"\n\n{_alerte}" if _alerte else "")
            + ("\n\n__Les signaux déjà posés gardent leur échelle.__" if _args
               else f"\n\n__Changer : `{_cmd} 10 20`__"))

    # (#87/#90) Jusqu'ou vont reellement les signaux -- de quoi regler l'echelle de TP.
    elif text.startswith("/stats_indodog") or text.startswith("/stats_fts"):
        _src = "CANAL FTS" if text.startswith("/stats_fts") else "CANAL INDODOG"
        _nom = _src.replace("CANAL ", "")
        if not channel_allowed(_src):
            await event.reply(f"❌ {_nom} ne fait pas partie de ton pack."); return
        r = _ladder_stats_resume(_src)
        total = r["signaux"]
        if total == 0:
            await event.reply(f"🪜 **{_nom}** : aucun signal enregistré pour l'instant.\n"
                              f"Le comptage démarre au premier signal "
                              f"(`{LADDER_STATS_FILES[_src]}`).")
            return
        lignes = []
        for cle, n in r["paliers_atteints"].items():
            lignes.append(f"  TP{int(cle[2:])} : **{n}**/{total}  ({100.0 * n / total:.0f} %)")
        paliers_txt = "\n".join(lignes) if lignes else "  __aucun palier atteint__"
        # Libelles parlants : "TP_FINAL" ou "SL" ne disent rien a la lecture.
        _NOMS_ISSUE = {
            "TP_FINAL":   "🏆 Dernier TP atteint",
            "SL":         "💥 Stop Loss",
            "BE":         "🛡 Breakeven",
            "SL_SUIVEUR": "💰 SL suiveur (en gain)",
            "ANNULE":     "🗑 Jamais déclenché (ordre annulé)",
            "AUTRE":      "❔ Autre (clôture manuelle…)",
        }
        issues_txt = "\n".join(f"  {_NOMS_ISSUE.get(k, k)} : **{v}**"
                               for k, v in r["issues"].items()) or "  __aucun signal terminé__"
        # (#110) L'echelle est desormais un % par palier : decrite sur un signal a 5 TP.
        echelle = decrire_echelle(ladder_echelle(_src), 5) + " (signal à 5 TP)"
        # (#104) L'italique Telethon s'ecrit __texte__ ; avec un seul souligne les caracteres
        # s'affichaient tels quels ("_Echelle actuelle : ..._").
        await event.reply(
            f"🪜 **STATS {_nom}**\n\n"
            f"Signaux : **{total}** ({r['termines']} terminés, {r['en_cours']} en cours)\n"
            f"__« en cours » = position encore ouverte à cet instant.__\n\n"
            f"**Paliers atteints** (cumulatif — un signal allé au TP3 compte aussi en TP1 et TP2,\n"
            f"donc la colonne se lit comme un taux de survie)\n{paliers_txt}\n\n"
            f"**Comment ils se terminent**\n{issues_txt}\n\n"
            f"__Échelle actuelle : {echelle}.__")

    # (#91) Remise a zero des stats par canal du dashboard. NON DESTRUCTIF : on pose une date
    # de coupure, trades_detailed.csv n'est jamais touche -- il sert aussi a l'historique des
    # trades et a la courbe du portefeuille, y supprimer des lignes casserait les deux.
    elif text.startswith("/reset_stats"):
        # Libelles tels qu'ils apparaissent DANS LES STATS : SABR y est regroupe sous
        # "CANAL SABR VIP" / "CANAL SABR FREE" (avec une espace), pas sous son nom de pack.
        libelles = {
            "MDT_VIP": "CANAL MDT_VIP", "VIP": "CANAL MDT_VIP",
            "MDT_FREE": "CANAL MDT_FREE", "FREE": "CANAL MDT_FREE",
            "SABR_VIP": "CANAL SABR VIP", "SABR": "CANAL SABR VIP",
            "SABR_FREE": "CANAL SABR FREE",
            "INDODOG": "CANAL INDODOG", "INDO": "CANAL INDODOG", "FTS": "CANAL FTS",
            "SPACE": "CANAL SPACE", "LIGUE": "CANAL LIGUE", "PERSO": "CANAL PERSO",
        }
        parts_rs = text.split()
        if len(parts_rs) < 2:
            actifs = "\n".join(f"  {c.replace('CANAL ', '')} : depuis le {d}"
                               for c, d in sorted(STATS_RESET.items())) or "  _aucune_"
            await event.reply(
                "📊 **REMISE À ZÉRO DES STATS PAR CANAL**\n\n"
                "`/reset_stats CANAL` : ce canal repart de zéro\n"
                "`/reset_stats CANAL annuler` : rétablit tout son historique\n"
                "`/reset_stats TOUS` : tous les canaux de ton pack\n\n"
                f"Canaux : {', '.join(sorted(set(libelles)))}\n\n"
                f"**Coupures actives**\n{actifs}\n\n"
                "_Aucun trade n'est supprimé : l'historique et la courbe du portefeuille "
                "restent complets, seul l'onglet « Par Canal » ignore l'avant._")
            return

        jeton = parts_rs[1].upper()
        annule = len(parts_rs) > 2 and parts_rs[2].lower() in ("annuler", "annule", "undo")

        if jeton in ("TOUS", "TOUT", "ALL"):
            cibles = sorted({c for c in libelles.values() if channel_allowed(c)})
            if not cibles:
                await event.reply("❌ Aucun canal dans ton pack."); return
        elif jeton in libelles:
            if not channel_allowed(libelles[jeton]):
                await event.reply(f"❌ {jeton} ne fait pas partie de ton pack."); return
            cibles = [libelles[jeton]]
        else:
            await event.reply(f"❌ Canal inconnu : `{jeton}`\n"
                              f"Canaux : {', '.join(sorted(set(libelles)))}")
            return

        horodatage = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        for c in cibles:
            if annule:
                STATS_RESET.pop(c, None)
            else:
                STATS_RESET[c] = horodatage
        await run_bg(save_stats_reset)

        noms = ", ".join(c.replace("CANAL ", "") for c in cibles)
        if annule:
            await event.reply(f"↩️ **HISTORIQUE RÉTABLI** — {noms}\n\n"
                              f"Les stats réaffichent la totalité des trades.")
        else:
            await event.reply(f"📊 **STATS REMISES À ZÉRO** — {noms}\n\n"
                              f"Coupure posée au {horodatage}. Tout ce qui est clôturé avant "
                              f"est désormais ignoré dans l'onglet « Par Canal ».\n\n"
                              f"Rien n'a été supprimé : l'historique des trades et la courbe "
                              f"du portefeuille restent complets.\n\n"
                              f"↩️ Pour revenir en arrière : `/reset_stats {jeton} annuler`")

    elif text.startswith("/risk_sabr_vip"):
        if not channel_allowed("CANAL SABR_VIP"): await event.reply("❌ SABR_VIP ne fait pas partie de ton pack."); return
        try:
            val_raw = text.split()[1]
            # Pas de division par 100 ici ! On prend la valeur brute (ex: 0.10)
            FIXED_LOT_SABR_VIP = float(val_raw) 
            await run_bg(save_settings)
            await event.reply(f"👑 Lot Fixe SABR VIP : **{FIXED_LOT_SABR_VIP} lots**")
        except: await event.reply("❌ Usage: /risk_sabr_vip 0.10")

    # SABR FREE (Lots Fixes)
    elif text.startswith("/risk_sabr_free") or text.startswith("/risk_sabr"):
        if not channel_allowed("CANAL SABR_FREE"): await event.reply("❌ SABR_FREE ne fait pas partie de ton pack."); return
        try:
            val_raw = text.split()[1]
            # Pas de division par 100 ici !
            FIXED_LOT_SABR_FREE = float(val_raw) 
            await run_bg(save_settings)
            await event.reply(f"🔫 Lot Fixe SABR FREE : **{FIXED_LOT_SABR_FREE} lots**")
        except: await event.reply("❌ Usage: /risk_sabr_free 0.06")

    # AUTRES CANAUX
    elif text.startswith("/risk_space"):
        if not channel_allowed("CANAL SPACE"): await event.reply("❌ SPACE ne fait pas partie de ton pack."); return
        try:
            val_raw = text.split()[1]
            if val_raw.lower() == "auto": RISK_PERCENT_SPACE = None
            else: RISK_PERCENT_SPACE = float(val_raw) / 100 
            await run_bg(save_settings); await event.reply(f"🌌 Risque Space : **{val_raw}%**")
        except: await event.reply("❌ Usage: /risk_space 1.0")

    elif text.startswith("/risk_ligue"):
        if not channel_allowed("CANAL LIGUE"): await event.reply("❌ LIGUE ne fait pas partie de ton pack."); return
        try:
            val_raw = text.split()[1]
            if val_raw.lower() == "auto": RISK_PERCENT_LIGUE = None
            else: RISK_PERCENT_LIGUE = float(val_raw) / 100 
            await run_bg(save_settings); await event.reply(f"🏆 Risque Ligue : **{val_raw}%**")
        except: await event.reply("❌ Usage: /risk_ligue 1.0")

    elif text.startswith("/risk_perso"):
        if not channel_allowed("CANAL PERSO"): await event.reply("❌ PERSO ne fait pas partie de ton pack."); return
        try:
            val_raw = text.split()[1]
            if val_raw.lower() == "auto": RISK_PERCENT_PERSO = None
            else: RISK_PERCENT_PERSO = float(val_raw) / 100 
            await run_bg(save_settings); await event.reply(f"👤 Risque Perso : **{val_raw}%**")
        except: await event.reply("❌ Usage: /risk_perso 1.0")

# --- 6. DÉVERROUILLAGE SÉCURITÉ ---
    elif text == "/unlock":
        # (#DD) Refuse le déverrouillage si la perte du jour dépasse ENCORE le Max DD actuel
        # -- il faut d'abord relever le seuil, sinon on ne ferait que ré-autoriser une
        # seconde puis re-déclencher la fermeture d'urgence au cycle suivant.
        if check_daily_max_loss():
            await event.reply("⛔ Augmente d'abord le **Max DD** (`/max_dd 10`) ou coupe le "
                              "disjoncteur (`/dd off`) -- la perte du jour dépasse toujours "
                              "la limite actuelle.")
        else:
            security_triggered = False
            DD_PANIC_NOTIFIED = False   # (#103) ré-arme l'alerte pour le prochain épisode
            await event.reply("🔓 **SÉCURITÉ DÉVERROUILLÉE**")

# --- 6 bis. DISJONCTEUR DRAWDOWN ---
    # (#103) Le disjoncteur n'existait QU'au dashboard (commande Firestore toggle_drawdown).
    # Quand le dashboard ne répond pas -- ou quand son toggle n'atteint pas le bot, ce qui
    # était silencieux avant ce correctif -- le client n'avait aucun moyen de le couper, donc
    # aucun moyen de lever un verrou déclenché. Ici l'état est LU puis ÉCRIT explicitement
    # (`on`/`off`), jamais basculé à l'aveugle : deux appels successifs donnent le même
    # résultat, contrairement au `not DRAWDOWN_ACTIVE` du dashboard.
    elif text.startswith("/dd"):
        global DRAWDOWN_ACTIVE
        arg = text.split()[1].lower() if len(text.split()) > 1 else ""
        if arg in ("on", "1", "actif"):
            DRAWDOWN_ACTIVE = True
            await run_bg(save_settings)
        elif arg in ("off", "0", "inactif"):
            DRAWDOWN_ACTIVE = False
            await run_bg(save_settings)
        elif arg:
            await event.reply("❌ Usage: `/dd on` ou `/dd off` (sans argument : état actuel)")
            return
        if DRAWDOWN_ACTIVE:
            await event.reply(
                f"📉 **Disjoncteur drawdown : 🟢 ACTIF**\n\n"
                f"Seuil : **{MAX_DAILY_DRAWDOWN*100:.1f} %** de la balance de minuit.\n"
                f"Verrou en cours : {'🔴 OUI' if security_triggered else '🟢 non'}\n\n"
                f"_Le couper : `/dd off` · changer le seuil : `/max_dd 10`_")
        else:
            await event.reply(
                "📉 **Disjoncteur drawdown : 🔴 COUPÉ**\n\n"
                "Plus aucune fermeture d'urgence ni blocage sur perte journalière.\n"
                f"Verrou en cours : {'🔴 OUI -- fais `/unlock` maintenant' if security_triggered else '🟢 non'}\n\n"
                "⚠️ _N'oublie pas de le remettre : `/dd on`_")

# --- 7. SUIVI MANUEL ---
    elif text.startswith("/suivi"):
        try:
            parts = text.split(); ticket_id = int(parts[1])
            p_open = await run_mt5(mt5.positions_get, ticket=ticket_id)
            if not p_open: await event.reply("❌ Ticket introuvable."); return
            MANAGED_MANUAL_TICKETS.add(ticket_id); await run_bg(save_memory)
            ACTIVE_TRADE_SOURCES[ticket_id] = "CANAL PERSO"
            ACTIVE_TRADE_SIGNALS[ticket_id] = f"MANUEL-{ticket_id}"
            await run_bg(save_channel_data)
            await event.reply(f"✅ **SUIVI ACTIVÉ** (Trade #{ticket_id})")
        except: await event.reply("❌ Erreur ID")

# --- 8. ARRÊT TOTAL ---
    elif text == "/kill":
        # STOP_ME signale un arrêt VOLONTAIRE au watchdog externe (#46) ; os._exit() (et pas
        # sys.exit(), qui ne fait que lever SystemExit dans CETTE tâche asyncio sans jamais
        # arrêter le process) termine réellement le worker immédiatement.
        with open("STOP_ME", "w") as f: f.write("STOP")
        await event.reply("💀 **ARRET DU BOT...** (Adieu)"); os._exit(0)

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
            SOFT_SL_PERCENT = val; await run_bg(save_settings)
            # (#103) Meme avertissement que le toggle : a SL Suiveur = 0, ce reglage dort.
            avert = ("\n\n⚠️ Sans effet tant que **SL Suiveur** vaut 0 (`/sl_suiveur 85`) : "
                     "ce champ active le mode ET fixe la distance de repli."
                     ) if TRAILING_STOP_PIPS <= 0 else ""
            await event.reply(f"📉 Vente Soft SL : **{val*100:.0f}%**{avert}")
        except: await event.reply("❌ Usage: /sl_sell 20")

# --- 10.ter REGLAGE ANTI-DOUBLON SABR ---
    elif text.startswith("/doublon_sabr"):
        if not (channel_allowed("CANAL SABR_VIP") or channel_allowed("CANAL SABR_FREE") or channel_allowed("CANAL SABR_COPIE")):
            await event.reply("❌ SABR ne fait pas partie de ton pack."); return
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
        if not (channel_allowed("CANAL SABR_VIP") or channel_allowed("CANAL SABR_FREE") or channel_allowed("CANAL SABR_COPIE")):
            await event.reply("❌ SABR ne fait pas partie de ton pack."); return
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
            full_name = resolve_channel_arg(target)  # (#25) résolveur centralisé

            if not channel_allowed(full_name):
                await event.reply(f"❌ {full_name.replace('CANAL ', '')} ne fait pas partie de ton pack.")
                return

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
            full_name = resolve_channel_arg(target)  # (#25) résolveur centralisé

            # (#25) Un canal verrouillé par le pack ne peut jamais être démuté --
            # message dédié pour ne pas laisser croire que /unmute a fonctionné.
            if _channel_base_token(full_name) in {_channel_base_token(c) for c in PACK_LOCKED_CHANNELS}:
                await event.reply(f"❌ {full_name.replace('CANAL ', '')} ne fait pas partie de ton pack -- contacte l'administrateur.")
                return

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
            full_name = resolve_channel_arg(target)  # (#25) résolveur centralisé, corrige le bug VIP/FREE seuls

            if not channel_allowed(full_name):
                await event.reply(f"❌ {full_name.replace('CANAL ', '')} ne fait pas partie de ton pack.")
                return

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
    # (#111) Récupérer les journaux sans aller fouiller le dossier du bot : envoyés dans cette
    # conversation, pour être transférés tels quels au support. Ils contiennent le texte des
    # canaux suivis et des numéros de tickets -- aucun secret (ni jeton, ni mot de passe, ni
    # clé de licence : rien de settings.json n'y est écrit).
    elif text == "/journaux":
        candidats = [JOURNAL_CANAUX_FILE, JOURNAL_CANAUX_FILE + ".1", NON_COMPRIS_FILE,
                     "debug_mdt.log"]   # l'ancien journal MDT, s'il existe encore
        fichiers = [f for f in candidats if os.path.exists(f) and os.path.getsize(f) > 0]
        if not fichiers:
            await event.reply("📭 Aucun journal pour l'instant : il se remplit au premier "
                              "message reçu des canaux.")
            return
        await event.reply(f"📤 Envoi de {len(fichiers)} fichier(s)… À transférer tels quels "
                          f"au support.")
        for f in fichiers:
            try:
                await client.send_file(event.chat_id, f, force_document=True,
                                       caption=f"🗂 {f} ({os.path.getsize(f) / 1024:.0f} Ko)")
            except Exception as e:
                await event.reply(f"❌ Envoi de {f} impossible : {e}")

    elif text == "/stats":
        if not await run_mt5(mt5.initialize): await event.reply("❌ MT5 Off"); return
        acc = await run_mt5(mt5.account_info)
        day_total, _, _ = get_accurate_daily_stats()
        start_bal = acc.balance - day_total # Approx
        day_pct = (day_total / start_bal * 100) if start_bal > 0 else 0.0
        await event.reply("⏳ Génération...")
        await send_daily_report(acc, day_total, day_pct)

# --- 16. STATISTIQUES PAR CANAL (VERSION DÉTAILLÉE) ---
    elif text == "/canal_stats" or text.startswith("/canal_stats "):
        # (#stats) Recalculé depuis trades_detailed.csv, source unique. Sans argument on prend
        # tout l'historique ; `/canal_stats 30` limite aux N derniers jours.
        # (#108) Le test était `text == "/canal_stats"` : la variante avec un nombre n'arrivait
        # jamais ici. Mêmes journées entières que le dashboard (cf. bornes_periode).
        parts = text.split()
        try: nb_days = int(parts[1]) if len(parts) > 1 else 9999
        except ValueError: nb_days = 9999
        if nb_days < 1: nb_days = 9999

        stats_chan = await run_bg(get_channel_stats_period, bornes_periode(str(nb_days)))
        stats_chan = {c: v for c, v in stats_chan.items() if channel_allowed(c)}
        if not stats_chan:
            await event.reply("⚠️ Aucune donnée enregistrée pour le moment.")
            return

        titre = "TOUT L'HISTORIQUE" if nb_days >= 9999 else f"{nb_days} DERNIERS JOURS"
        msg = f"📊 **PERFORMANCE PAR CANAL — {titre}**\n\n"

        for chan, data in sorted(stats_chan.items(), key=lambda x: -x[1]["profit"]):
            cnt = data["count"]
            nb_ordres = data.get("positions", cnt)
            prof = data["profit"]
            pips_total = data["pips"]
            avg_pips = data.get("avg_pips", 0.0)

            icon = "🟢" if prof >= 0 else "🔴"

            # Sécurité Telegram (Pour éviter le bug "Bad Request" des underscores)
            chan_clean = chan.replace("_", " ")
            detail_ordres = f" ({nb_ordres} ordres)" if nb_ordres != cnt else ""

            msg += (f"🔹 **{chan_clean}** {icon}\n"
                    f"   📦 Trades : {cnt}{detail_ordres}\n"
                    f"   💰 Total Gain: {prof:+.2f}€\n"
                    f"   🎯 Total Pips : {pips_total:+.1f}\n"
                    f"   ⚖️ **Moyenne : {avg_pips:+.1f} pips/trade**\n"
                    f"\n")

        await event.reply(msg)

    # (#45) Bascule DISPLAY_MODE à chaud, sans redémarrage -- persisté dans settings.json.
    elif text.startswith("/display_mode"):
        global DISPLAY_MODE
        parts = text.split()
        if len(parts) < 2 or parts[1] not in ("dev", "client"):
            await event.reply(f"ℹ️ Mode actuel : **{DISPLAY_MODE}**\nUsage: `/display_mode dev` ou `/display_mode client`")
            return
        new_mode = parts[1]
        if new_mode == DISPLAY_MODE:
            await event.reply(f"ℹ️ Déjà en mode **{new_mode}**.")
            return
        DISPLAY_MODE = new_mode
        if new_mode == "dev":
            if console_handler not in logger.handlers:
                logger.addHandler(console_handler)
        elif console_handler in logger.handlers:
            logger.removeHandler(console_handler)
        try:
            with SETTINGS_LOCK:
                _s = {}
                if os.path.exists(SETTINGS_FILENAME):
                    with open(SETTINGS_FILENAME, "r", encoding="utf-8") as f:
                        _s = json.load(f)
                _s["DISPLAY_MODE"] = new_mode
                with open(SETTINGS_FILENAME, "w", encoding="utf-8") as f:
                    json.dump(_s, f, indent=4)
        except Exception as e:
            logger.error(f"Erreur sauvegarde DISPLAY_MODE: {e}")
        await event.reply(f"✅ Mode console : **{new_mode}** ({'détaillée' if new_mode == 'dev' else 'épurée'})")

# --- 17. AIDE ---
    elif text == "/help":
        show_risk_engine = pack_uses_risk_engine()
        # (#25) Section RISQUES filtrée par pack : chaque ligne liée à un canal précis
        # n'apparaît que si ce canal fait partie du pack du client.
        risk_lines = [
            (None, "`/risk_gold 0.6`"),
            (None, "`/risk_other 0.6`"),
            (None, "`/risk_indice 0.8`"),
            (None, "`/risk_all 0.5`"),
            ("CANAL MDT_VIP", "`/risk_mdt_vip 1.0`"),
            ("CANAL MDT_FREE", "`/risk_mdt_free 0.5`"),
            ("CANAL SABR_VIP", "`/risk_sabr_vip 0.06` (Lots Fixes)"),
            ("CANAL SABR_FREE", "`/risk_sabr_free 0.06` (Lots Fixes)"),
            ("CANAL INDODOG", "`/lot_indodog 0.10` (Lots Fixes) · `/echelle_indodog 10 20` (% par TP) · `/stats_indodog` (paliers atteints)"),
            ("CANAL FTS", "`/lot_fts 0.10` (Lots Fixes) · `/echelle_fts 20` (% par TP) · `/stats_fts` (paliers atteints)"),
            ("CANAL SPACE", "`/risk_space 1.0`"),
            ("CANAL LIGUE", "`/risk_ligue 1.0`"),
            ("CANAL PERSO", "`/risk_perso 1.0`"),
        ]
        # (#pack) Les lignes "globales" (None) ne sont plus inconditionnelles : les risques %
        # par classe d'actif ne servent qu'au moteur de risque, inutilisé par un pack SABR
        # (lots fixes / RISK_PERCENT_SABR_OTHER), cf. pack_uses_risk_engine().
        risk_txt = "\n".join(
            line for ch, line in risk_lines
            if (channel_allowed(ch) if ch else show_risk_engine)
        )

        has_sabr = (channel_allowed("CANAL SABR_VIP") or channel_allowed("CANAL SABR_FREE")
                    or channel_allowed("CANAL SABR_COPIE"))

        # (#pack) Chaque ligne n'est listée que si elle a un effet réel pour ce client :
        # afficher une commande inopérante (ex: SL suiveur sur un pack SABR) donne l'illusion
        # d'un réglage disponible et génère des questions au support.
        gestion_lines = [
            (True,             "`/reset_stats` : Remettre à zéro les stats d'un canal"),
            (True,             "`/max_dd 4` : Max Drawdown 4%"),
            (show_risk_engine, "`/sl_suiveur 85` : Trailing Pips (ou '0' pour paliers)"),
            (show_risk_engine, "`/sl_sell 30` : % Vente Soft SL"),
            (True,             "`/sell_lot VIP 50` : % Vente TP1 (Tous canaux)"),
            (has_sabr,         "`/be_partiel on|off|50` : Mode BE SABR (classique / partiel)"),
            (has_sabr,         "`/reactivate 1 50` : Relancer signal SABR (ID: 1, 50%)"),
            (has_sabr,         "`/doublon_sabr on/off` : Anti-Doublon SABR"),
            (channel_allowed("CANAL SABR_COPIE"), "`/forward_sabr on/off` : Transfert msg SABR"),
            (show_risk_engine, "`/risk_heure 8-10 11-16 0.5` : Diviseur horaire"),
            (show_risk_engine, "`/risk_day vendredi 0.33` : Diviseur journalier"),
            (True,             "`/mute VIP` | `/unmute VIP` : Ignorer canal"),
        ]
        gestion_txt = "\n".join(line for cond, line in gestion_lines if cond)

        risk_block = f"**⚙️ RISQUES (0.5 = 0.5%) :**\n\n{risk_txt}\n\n\n" if risk_txt else ""

        msg = (f"**🤖 MENU COMMANDES v{VERSION_BOT}**\n\n\n"
               "**📌 GÉNÉRALES :**\n\n"
               "`/info` : Bilan compte\n"
               "`/stats` | `/canal_stats` : Rapports\n"
               "`/journaux` : Recevoir les journaux des canaux (pour le support)\n"
               "`/display_mode dev|client` : Console détaillée ou épurée, à chaud\n"
               "`/suivi 12345` : Activer suivi manuel\n"
               "`/unlock` : Débloquer après DD\n"
               "`/kill` : Stop Bot (Urgence)\n"
               "`/closeall` : Fermer TOUT\n"
               "`/close XAUUSD` : Fermer par paire\n\n\n"
               f"{risk_block}"
               "**🛠️ GESTION & PARAMÈTRES :**\n\n"
               f"{gestion_txt}")
        await event.reply(msg)

# ==========================================
# 👂 LISTENER PRINCIPAL (MULTI-CANAUX)
# ==========================================

def _hors_sujet_indodog(event):
    """(#84) INDODOG est un groupe a SUJETS : seul le salon des signaux nous interesse. Dans
    Telegram, un message d'un sujet porte le numero de celui-ci dans reply_to_top_id ; quand il
    repond directement au message racine du sujet, c'est reply_to_msg_id qui le porte. On teste
    donc les deux. Un message sans reply_to vient de la discussion generale.
    Retourne le numero du sujet si le message est HORS du salon suivi, sinon None.
    (#111) Partage avec edit_handler, qui n'avait pas ce filtre."""
    if event.chat_id != ID_INDODOG:
        return None
    _r = getattr(event, "reply_to", None)
    _topic = (getattr(_r, "reply_to_top_id", None) or
              getattr(_r, "reply_to_msg_id", None)) if _r else None
    return None if _topic == INDODOG_TOPIC_ID else (_topic or "general")


@client.on(events.NewMessage(chats=CHANNEL_LIST))
async def main_handler(event):
    # (#52) Garde de ré-entrance posée EN PREMIER, avant tout traitement (y compris le
    # forward SABR ci-dessous) : safety_net_scanner() rejoue les messages ratés en appelant
    # main_handler() directement avec le même event.id que le flux live. Sans ce garde tout
    # en tête, les deux exécutions pouvaient tourner en parallèle sur le même signal et
    # ouvrir deux fois le même trade réel (l'ancien marquage `PROCESSED_MSG_IDS.add()` plus
    # bas ne protégeait de rien, il n'était même jamais vérifié avant écriture).
    if event.id in PROCESSED_MSG_IDS:
        return
    PROCESSED_MSG_IDS.add(event.id)

    # (#84) INDODOG : seul le salon des signaux est suivi (cf. _hors_sujet_indodog). Pas de
    # ligne au journal pour les autres salons : c'est de la discussion, et elle le noierait.
    _salon = _hors_sujet_indodog(event)
    if _salon is not None:
        logger.info(f"↩️ INDODOG : message du salon {_salon} ignoré "
                    f"(seul le sujet {INDODOG_TOPIC_ID} est suivi).")
        return

    # --- AJOUT DEBUG ---
    log(f"👀 J'ai vu un message ! ID: {event.id}")
    log(f"Texte brut: {event.raw_text}")

    # Journal : on capture le message BRUT dès l'entrée, avant toute interprétation --
    # c'est ce qui permet ensuite de distinguer "message jamais reçu" de "reçu mais mal
    # interprété". reply_to_msg_id est essentiel : les instructions du canal (BE, partiel,
    # clôture) sont des réponses au message du signal d'origine.
    # (#111) Tous les canaux, et chaque sortie de ce handler note ensuite ce qui a été fait du
    # message -- ou pourquoi rien ne l'a été.
    _tag = tag_canal(chat_id=event.chat_id)
    _reply_to = getattr(event, "reply_to_msg_id", None)
    _reply_txt = f" (réponse au msg {_reply_to})" if _reply_to else ""
    await noter(_tag, f"📩 MESSAGE REÇU (id={event.id}){_reply_txt} :\n{event.raw_text}")

    # === 📤 TRANSFERT DES MESSAGES SABR (FORWARDER) ===
    # (#5) Si le canal copie est mute, on coupe aussi la recopie des messages.
    # (#53) Ne transfère que si le canal SABR VIP source lui-même fait partie du pack du
    # client -- avant ce fix, un client sans SABR VIP dans son pack recevait quand même le
    # texte intégral des signaux (zone/prix/action) via son canal SABR_COPIE, alors même que
    # le trading dessus était bloqué : fuite d'information malgré l'isolation pack correcte
    # côté exécution.
    copie_muted = any("SABR_COPIE" in m for m in MUTED_CHANNELS)
    sabr_vip_allowed = channel_allowed("CANAL SABR_VIP")
    if SABR_FORWARD_ACTIVE and not copie_muted and sabr_vip_allowed and event.chat_id in [ID_SABR_VIP, -5093508256]:
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
    # 👇 (#26) MESSAGE "PARTIEL" SUR UN CANAL SABR -> +10% DU VOLUME INITIAL 👇
    # ==================================================================
    # Priorité absolue sur la gestion générique ci-dessous (qui interprète aussi
    # "PARTIEL" mais avec une logique différente, pensée pour MDT_VIP) -- on
    # traite ici et on sort, que le message soit une réponse ou non.
    if event.chat_id in (ID_SABR_VIP, ID_SABR_FREE, ID_CANAL_SABR_COPIE) and "PARTIEL" in (event.raw_text or "").upper():
        target_msg_id = None
        if event.is_reply and event.reply_to_msg_id in TELEGRAM_TO_TICKET:
            target_msg_id = event.reply_to_msg_id
        else:
            last_ctx = SABR_CONTEXT.get("last")
            if last_ctx and "msg_id" in last_ctx:
                target_msg_id = last_ctx["msg_id"]

        if target_msg_id and target_msg_id in TELEGRAM_TO_TICKET:
            for tk in TELEGRAM_TO_TICKET[target_msg_id]:
                pos_data = await run_mt5(mt5.positions_get, ticket=tk)
                if not pos_data: continue
                pos = pos_data[0]

                deals = await run_mt5(mt5.history_deals_get, position=tk)
                initial_vol = deals[0].volume if (deals and len(deals) > 0) else pos.volume
                vol_to_close = round(initial_vol * 0.10, 2)
                if vol_to_close < 0.01: vol_to_close = 0.01

                pt_p = get_cached_point(pos.symbol); div_p = get_pip_divisor(pos.symbol)
                if pos.type == mt5.ORDER_TYPE_BUY:
                    pips_now = ((pos.price_current - pos.price_open) / pt_p) / div_p
                else:
                    pips_now = ((pos.price_open - pos.price_current) / pt_p) / div_p

                if round(pos.volume - vol_to_close, 2) >= 0.01:
                    await close_partial(pos, 0.0, "SABR PARTIEL (+10%)", pips_now, fixed_lot=vol_to_close, etape="TP2")
                else:
                    await noter(_tag, f"ℹ️ PARTIEL SABR sans effet — ticket {tk} : il ne resterait rien "
                                      f"après {vol_to_close:.2f} lot(s)")
        else:
            await noter(_tag, "ℹ️ PARTIEL SABR sans effet : aucun signal ouvert auquel le rattacher")
        return

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

        # (#20) Suivi des instructions du canal (clôture partielle/totale, mise à BE).
        # Détectés indépendamment (pas de if/elif exclusif) : un message cumule souvent
        # "moitié" + "SL au point d'entrée" (ex: "FERMEZ LA MOITIÉ ... DÉPLACEZ LE SL
        # AU POINT D'ENTRÉE"). Avant, tout message contenant "PROFIT"/"MOITIÉ"/"PARTIEL"
        # était ignoré (y compris des clôtures totales du type "Fermeture totale avec
        # +10 pips de profit") -- corrigé ici.
        is_half = ("MOITI" in txt_upper) or ("50%" in txt_upper) or ("HALF" in txt_upper)
        # (#75) Le canal écrit indifféremment "PARTIELLEMENT" et "UNE PARTIE". Seul "PARTIEL"
        # était reconnu, donc "FERMEZ UNE PARTIE DE LA POSITION : PLUS DE 150 PIPS" ne
        # déclenchait rien (observé 6 fois). On exige un verbe de clôture juste avant "PARTIE"
        # pour ne pas confondre avec un "faire partie de" anodin.
        _partie = re.search(r"(?:FERM|CL[OÔ]TUR|PREN|ENCAISS|S[EÉ]CURIS)\w*\s+(?:UNE\s+)?PARTIE",
                            txt_upper)
        is_generic_partial = (("PARTIEL" in txt_upper) or bool(_partie)) and not is_half
        # (#75) Un ordre de PARTIEL prime sur la détection de clôture totale. "FERMETURE
        # PARTIELLE" contient "FERMETURE", qui est un mot-clé de clôture : le bot fermait donc
        # 100 % de la position alors que le canal demandait d'en garder une partie. Constaté
        # deux fois en production (tickets 518280447 le 13/08 et 522831991 le 19/08), le
        # reliquat qui devait continuer à courir étant liquidé à chaque fois.
        is_full_close = (any(k in txt_upper for k in keywords_stop)
                         and not (is_half or is_generic_partial))
        # (#76) La détection exigeait littéralement le mot "POINT". Sur 64 consignes de BE
        # relevées dans les logs, 53 disent "au POINT d'entrée", 9 "au NIVEAU d'entrée" et
        # 1 "au PRIX d'entrée" : ces 10 dernières étaient ignorées, le partiel se faisait mais
        # le stop restait en place. Les trois formulations sont désormais acceptées.
        is_be = ("BREAKEVEN" in txt_upper) or ("BE" == txt_upper.strip()) or \
                (any(w in txt_upper for w in ("POINT", "NIVEAU", "PRIX"))
                 and "ENTR" in txt_upper and "SL" in txt_upper)

        original_msg_id = event.reply_to_msg_id

        if original_msg_id in TELEGRAM_TO_TICKET:
            tickets_to_manage = TELEGRAM_TO_TICKET[original_msg_id] # C'est une liste !
            action_taken = False

            if is_full_close:
                logger.warning(f"🚨 COMMANDE STOP DÉTECTÉE SUR TICKET(S) {tickets_to_manage}")

                closed_count = 0
                for tk in tickets_to_manage:
                    # --- CAS A : C'EST UN ORDRE EN ATTENTE (PENDING) ? ---
                    orders_data = await run_mt5(mt5.orders_get, ticket=tk)
                    if orders_data and len(orders_data) > 0:
                        # C'est un ordre -> ON SUPPRIME (REMOVE)
                        req = {
                            "action": mt5.TRADE_ACTION_REMOVE,
                            "order": tk,
                            "magic": MAGIC_NUMBER
                        }
                        res = await run_mt5(_send_order_blocking, req)

                        if res is None:
                            await send_alert(f"⚠️ Timeout MT5: Impossible de supprimer l'ordre #{tk}")
                            continue

                        if res.retcode == mt5.TRADE_RETCODE_DONE:
                            await send_alert(f"🗑️ **ORDRE SUPPRIMÉ**\n\nTicket #{tk} annulé.")
                            if tk in PENDING_ORDER_TICKETS: PENDING_ORDER_TICKETS.remove(tk)
                            closed_count += 1
                            await noter(_tag, f"🗑 ORDRE ANNULÉ (instruction du canal) — ticket {tk}")
                        else:
                            await send_alert(f"⚠️ Échec suppression ordre #{tk}: {res.comment}")
                            await noter(_tag, f"❌ ANNULATION REFUSÉE — ticket {tk} : {res.comment}")
                        continue # On passe au ticket suivant

                    # --- CAS B : C'EST UNE POSITION ACTIVE ? ---
                    pos_data = await run_mt5(mt5.positions_get, ticket=tk)
                    if pos_data and len(pos_data) > 0:
                        # C'est une position -> ON FERME (CLOSE)
                        pos = pos_data[0]
                        otype = mt5.ORDER_TYPE_SELL if pos.type == mt5.ORDER_TYPE_BUY else mt5.ORDER_TYPE_BUY
                        price = mt5.symbol_info_tick(pos.symbol).bid if otype == mt5.ORDER_TYPE_SELL else mt5.symbol_info_tick(pos.symbol).ask

                        req = {
                            "action": mt5.TRADE_ACTION_DEAL, "symbol": pos.symbol, "volume": pos.volume,
                            "type": otype, "position": tk, "price": price,
                            "magic": MAGIC_NUMBER, "comment": "B - STOP ADMIN", "type_filling": mt5.ORDER_FILLING_IOC,
                        }
                        res = await run_mt5(_send_order_blocking, req)

                        if res is None:
                            await send_alert(f"⚠️ Timeout MT5: Impossible de fermer la position #{tk}")
                            continue

                        if res.retcode == mt5.TRADE_RETCODE_DONE:
                            await send_alert(f"✅ **POSITION FERMÉE**\n\nTicket #{tk} clôturé.")
                            if tk in MANAGED_MANUAL_TICKETS: MANAGED_MANUAL_TICKETS.remove(tk)
                            closed_count += 1
                            await noter(_tag, f"✂️ CLÔTURE DEMANDÉE PAR LE CANAL — ticket {tk} fermé")
                        else:
                            await send_alert(f"⚠️ Échec fermeture position #{tk}: {res.comment}")
                            await noter(_tag, f"❌ CLÔTURE REFUSÉE — ticket {tk} : {res.comment}")

                if closed_count > 0:
                    del TELEGRAM_TO_TICKET[original_msg_id]
                    action_taken = True
                else:
                    await send_alert("⚠️ Commande reçue mais tickets introuvables (Déjà fermés ?).")
                    # (#68) L'instruction A été comprise, il n'y avait simplement plus rien à
                    # fermer. Sans cette trace, le journal affichait "INSTRUCTION NON COMPRISE",
                    # ce qui laissait croire à un défaut de parsing lors de la relecture.
                    await noter(_tag, f"ℹ️ ORDRE DE CLÔTURE COMPRIS mais plus aucun ticket ouvert "
                                      f"parmi {tickets_to_manage} :\n{event.raw_text}")
                    action_taken = True

            else:
                # --- (#20) Clôture partielle (moitié explicite, ou "partiellement" sans %
                # précisé -> 50% du restant par défaut) ---
                if is_half or is_generic_partial:
                    label = "Clôture 50% (signal canal)" if is_half else "Clôture partielle 50% (signal canal, % non précisé)"
                    for tk in tickets_to_manage:
                        pos_data = await run_mt5(mt5.positions_get, ticket=tk)
                        if not pos_data: continue
                        pos = pos_data[0]
                        pt = get_cached_point(pos.symbol); div = get_pip_divisor(pos.symbol)
                        if pos.type == mt5.ORDER_TYPE_BUY:
                            pips_now = ((pos.price_current - pos.price_open) / pt) / div
                        else:
                            pips_now = ((pos.price_open - pos.price_current) / pt) / div
                        if await close_partial(pos, 0.5, label, pips_now, etape="TP1"):
                            action_taken = True

                # --- (#20) Mise à BE (SL au point d'entrée) ---
                if is_be:
                    for tk in tickets_to_manage:
                        pos_data = await run_mt5(mt5.positions_get, ticket=tk)
                        if not pos_data: continue
                        pos = pos_data[0]
                        pt = get_cached_point(pos.symbol)
                        be_price = compute_be_price(pos.price_open, pos.type == mt5.ORDER_TYPE_BUY, pos.symbol)
                        move_sl = (pos.type == mt5.ORDER_TYPE_BUY and pos.sl < be_price) or \
                                  (pos.type == mt5.ORDER_TYPE_SELL and (pos.sl > be_price or pos.sl == 0))
                        if move_sl:
                            new_sl_price = round(be_price / pt) * pt
                            req = {"action": mt5.TRADE_ACTION_SLTP, "symbol": pos.symbol, "position": tk, "sl": new_sl_price, "tp": pos.tp}
                            res = await run_mt5(_send_order_blocking, req)
                            if res and res.retcode == mt5.TRADE_RETCODE_DONE:
                                await send_alert(f"🛡️ **SL SÉCURISÉ AU BE** (signal canal)\n\nTicket #{tk}")
                                action_taken = True
                                await noter(tag_canal(ACTIVE_TRADE_SOURCES.get(tk, "")),
                                            f"🛡️ BE APPLIQUÉ (instruction du canal) — ticket {tk} | SL -> {new_sl_price}")
                            else:
                                await noter(_tag, f"❌ BE REFUSÉ — ticket {tk}"
                                                  + (f" : {res.comment}" if res else " : MT5 ne répond pas"))
                        else:
                            # (#111) Sans cette ligne, une consigne de BE déjà satisfaite ne
                            # laissait aucune trace : on la croyait ignorée à la relecture.
                            await noter(_tag, f"ℹ️ BE déjà en place — ticket {tk} : le SL protège "
                                              f"déjà au moins l'entrée")
                        # On marque BE comme géré même si le SL n'a pas dû bouger (déjà mieux
                        # que le BE) -- évite que le filet de sécurité 60 pips (#20) ne repasse dessus.
                        TRADES_PROCESSED_LEVELS.setdefault(tk, set()).add("VIP_BE_DONE")

            if not action_taken:
                # (#82) Deux situations très différentes aboutissaient au même message alarmant.
                # Si une règle a RECONNU l'instruction (partiel, moitié ou BE) mais qu'aucun
                # ticket n'était encore ouvert, il n'y a aucun défaut d'analyse : la consigne
                # est simplement arrivée après la clôture. Le correctif (#68) ne couvrait que
                # les clôtures totales ; les partiels et les BE affichaient encore
                # "INSTRUCTION NON COMPRISE" et faisaient soupçonner un bug inexistant.
                comprise = is_half or is_generic_partial or is_be
                if comprise:
                    await noter(_tag, f"ℹ️ INSTRUCTION COMPRISE mais sans effet (plus aucune "
                                      f"position ouverte parmi {tickets_to_manage}) :\n{event.raw_text}")
                else:
                    await run_bg(log_unclear_signal, event.raw_text, tickets_to_manage)
                    # Le cas le plus important à repérer en relecture : une instruction du canal
                    # reçue mais qu'aucune règle n'a su interpréter -> aucune action sur le trade.
                    await noter(_tag, f"⚠️ INSTRUCTION NON COMPRISE — aucune action sur {tickets_to_manage} :\n{event.raw_text}")

            return # Fin de la gestion commande
    # ==================================================================

    if event.id in TELEGRAM_TO_TICKET: return

    # (#22) Un message TRANSFÉRÉ (repost d'un autre canal, ex: "Forwarded from ... Canal VIP")
    # n'est pas un signal généré par ce canal -- on l'ignore pour ne pas trader une vitrine/repost.
    if event.fwd_from:
        logger.info(f"↪️ Message transféré ignoré (id={event.id}) -- pas traité comme signal.")
        await noter(_tag, f"↪️ IGNORÉ (id={event.id}) : message transféré d'un autre canal")
        return

    raw_text = event.raw_text
    _exclu = next((w for w in BLACKLIST_WORDS if w in raw_text.upper()), None)
    if _exclu:
        # (#111) Sans cette ligne, le cas le plus déroutant en relecture : un message reçu,
        # puis rien. « LES », « DES » font partie des mots exclus.
        await noter(_tag, f"🚫 IGNORÉ (id={event.id}) : contient le mot exclu « {_exclu} »")
        return
    
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
            await noter(_tag, f"🗑 IGNORÉ (id={event.id}) : supprimé par le canal avant d'être traité")
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
            # (#77) Le symbole est résolu AVANT d'annoncer un signal valide. L'ordre inverse
            # affichait "✅ SIGNAL VALIDE: VENTE SUR" puis une erreur : un vrai signal perdu
            # ressemblait donc à un succès et se noyait dans les 108 échecs de symbole du log.
            sym = find_tradable_symbol(rsym)

            # Rattrapage : l'expression d'extraction prend le premier mot de 3 à 10 caractères
            # après le verbe d'action, donc elle attrape parfois un mot de liaison ("SUR",
            # "NOUVEAU") ou le prix lui-même. Si le message nomme l'or en toutes lettres, on
            # sait quoi trader malgré tout. Vérifié sur "Je me replace en vente sur l'or now"
            # (2026-08-06), signal complet avec SL et TP qui n'avait jamais été exécuté.
            if not sym and re.search(r"\bL['’]?OR\b|\bGOLD\b|\bXAU", final_text, re.IGNORECASE):
                # (#104) Ce rattrapage récupère un vrai signal quand l'expression a attrapé un
                # mot de liaison au lieu du symbole ('SUR', 'LIMIT'). Mais il déduit le symbole
                # d'une simple mention de l'or : si le message n'est pas un signal du tout, il
                # transforme du commentaire en ordre au marché.
                #
                # Arrivé le 2026-09-01 sur MDT FREE : « J'ai déjà mon prix d'achat et il va
                # partir directement dans le VIP. [...] 🔴 SELL EN ATTENTE : 4409 ». Le mot
                # 'achat' a donné le sens, 'partir' le symbole, et le bot a acheté l'or au
                # marché sans SL ni TP (ticket 532870669, -511 €).
                #
                # Puisqu'on en est réduit à DEVINER le symbole, on exige au moins que le sens
                # soit net : un texte qui contient les deux directions n'est pas un signal.
                # Vérifié sur les 12 déclenchements de ce rattrapage en six semaines -- seul
                # celui du 1er septembre porte les deux sens, les 11 autres sont de vrais
                # signaux et passent toujours.
                _a = re.search(r"\b(ACHAT|ACH[EÈ]TE[ZR]?|BUY)\b", final_text, re.IGNORECASE)
                _v = re.search(r"\b(VENTE|VEND[SRE]?Z?|SELL)\b", final_text, re.IGNORECASE)
                if _a and _v:
                    logger.error("🛑 Signal ignoré — symbole deviné (%s) sur un message qui "
                                 "contient les DEUX sens (%s / %s). Message : %s"
                                 % (rsym, _a.group(0), _v.group(0),
                                    final_text[:160].replace(chr(10), " | ")))
                    await send_alert(
                        "🛑 **SIGNAL AMBIGU IGNORÉ**" + chr(10) + chr(10) +
                        "Le symbole a dû être deviné (l'or) et le message contient "
                        + ("**%s** et **%s**." % (_a.group(0).upper(), _v.group(0).upper()))
                        + chr(10) + chr(10) + "Aucun ordre passé. Message :" + chr(10)
                        + ("`%s`" % final_text[:250].replace("`", "'")))
                    await noter(_tag, f"🛑 IGNORÉ (id={event.id}) : signal ambigu — symbole deviné "
                                      f"(l'or) et les deux sens dans le texte "
                                      f"({_a.group(0)} / {_v.group(0)})")
                    return
                sym = find_tradable_symbol("XAUUSD")
                if sym:
                    logger.warning(f"⚠️ Symbole '{rsym}' invalide, mais le message parle de "
                                   f"l'or -> {sym}")

            if not sym:
                # L'alerte Telegram a été retirée, on garde uniquement le log local
                logger.error(f"❌ Signal ignoré — symbole '{rsym}' introuvable dans MT5. "
                             f"Message : {final_text[:120].replace(chr(10), ' | ')}")
                await noter(_tag, f"❌ IGNORÉ (id={event.id}) : {act} {rsym} — symbole introuvable dans MT5")
                return

            log(f"✅ SIGNAL VALIDE: {act} {sym}", important=True) # Confirmation visuelle
            
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
            is_sabr_signal = "SABR" in final_source
            clean_type = ""

            if is_sabr_signal:
                # On nettoie la Source + on formate le Type (la NUMÉROTATION est faite
                # plus bas, uniquement si le signal n'est PAS bloqué par l'anti-doublon).
                if "VIP" in final_source:
                    display_source = "CANAL SABR VIP"
                    raw_type = final_source.replace("CANAL SABR_VIP_", "")
                elif "COPIE" in final_source:
                    display_source = "CANAL SABR COPIE"
                    raw_type = final_source.replace("CANAL SABR_COPIE_", "")
                else:
                    display_source = "CANAL SABR FREE"
                    raw_type = final_source.replace("CANAL SABR_FREE_", "")
                clean_type = raw_type.replace("_", " ")

            # --- NOUVELLE LOGIQUE (MUTE TOTAL INTELLIGENT) ---
            # (#25) PACK_LOCKED_CHANNELS bloque réellement le trading sur un canal absent
            # du pack du client (avant, ALLOWED_CHANNELS ne faisait que filtrer l'affichage).
            # (#111) Ce qui a été compris, avant toute décision : si l'ordre ne part pas, la ligne
            # suivante dira pourquoi (execute_trade note chacun de ses refus).
            await noter(_tag, f"✅ SIGNAL COMPRIS (id={event.id}) — {act} {sym} | entrée {log_p} | "
                              f"SL {sl} | TP {tp} | type {final_source.replace('CANAL ', '')}")

            is_muted = any(m in final_source for m in MUTED_CHANNELS) or any(m in final_source for m in PACK_LOCKED_CHANNELS)
            if is_muted:
                logger.warning(f"🚫 SIGNAL IGNORÉ : Le canal {final_source} est bloqué par le mode MUTE ou hors pack.")
                await noter(_tag, "🔇 AUCUN ORDRE : canal coupé (mute) ou hors du pack")
                return # On arrête tout ici : pas d'alerte, PAS DE TRADE.

            if is_sabr_signal:
                # (#3/#4) On EXÉCUTE d'abord : un signal bloqué par l'anti-doublon ne doit
                # ni incrémenter le numéro de signal ni envoyer de message (sauf "bloqué").
                opened_tickets = await execute_trade(sym, act, sl, tp, p_msg, event.date, final_source)

                if opened_tickets == SABR_DUPLICATE:
                    await send_channel_alert(f"🛡️ Signal SABR bloqué (anti-doublon) : {act} {sym}", final_source)
                    # (la ligne « doublon » est écrite par execute_trade)
                else:
                    # Attribution du numéro du jour UNIQUEMENT si non bloqué
                    global SABR_SIGNAL_DAY
                    c_day = datetime.now().day
                    if c_day != SABR_SIGNAL_DAY:
                        SABR_DAILY_SIGNALS.clear()
                        SABR_SIGNAL_DAY = c_day
                    sig_id = len(SABR_DAILY_SIGNALS) + 1
                    SABR_DAILY_SIGNALS[sig_id] = {
                        "sym": sym, "act": act, "sl": sl, "tp": tp,
                        "p_msg": p_msg, "source": final_source
                    }
                    display_type_line = f"\n\nType : **{clean_type}**\n🆔 **Signal : {sig_id}**"

                    await send_channel_alert(f"✔️ Signal traité ({display_source}) :{display_type_line}\n\n {act} {sym} à {log_p}\n\n🛑 SL: {sl} & 🎯 TP: {tp}", final_source)

                    if opened_tickets:
                        TELEGRAM_TO_TICKET[event.id] = opened_tickets
                        # (#26) Mémorise le message d'origine pour qu'un futur message
                        # "PARTIEL" (sans reply) sache à quel signal s'appliquer.
                        SABR_CONTEXT.setdefault("last", {})["msg_id"] = event.id
            else:
                # --- CANAUX NON-SABR : comportement inchangé ---
                await send_channel_alert(f"✔️ Signal traité ({display_source}) :{display_type_line}\n\n {act} {sym} à {log_p}\n\n🛑 SL: {sl} & 🎯 TP: {tp}", final_source)

                # (#22) MDT_FREE : on lit le vrai prix TP1 du signal pour fermer la partielle
                # exactement à ce niveau-là (au lieu d'un seuil de pips fixe -- cf. issue).
                tp1_price = None
                if final_source == "CANAL MDT_FREE":
                    m_tp1 = re.search(r"TP1\s*[:=]\s*([\d.,]+)", final_text, re.IGNORECASE)
                    if m_tp1:
                        try: tp1_price = float(m_tp1.group(1).replace(',', '.'))
                        except: pass

                # (#84) INDODOG : on transmet l'echelle complete des TP du signal (3 a 5
                # paliers selon les jours) pour la gestion en partiels.
                tp_ladder = (parse_indodog_tps(final_text) if final_source == "CANAL INDODOG"
                             else parse_fts_tps(final_text) if final_source == "CANAL FTS" else None)

                # (#92) Filet : sur un canal a echelle, zero TP lu veut dire que le format du
                # canal a change. Sans alerte, ca passe inapercu -- le trade s'ouvre avec un TP
                # a 0 et aucun palier ne sera jamais pris, ce qui ne se voit que des jours plus
                # tard en fouillant indodog_tp.json. C'est exactement ce qui est arrive quand le
                # canal a cesse d'ecrire "(TP1)".
                if is_ladder_channel(final_source) and not tp_ladder:
                    logger.error("⚠️ %s : AUCUN TP lu dans le signal -- format du canal modifie ?"
                                 % final_source)
                    logger.error("Message brut :\n%s" % final_text)
                    await noter(_tag, "⚠️ AUCUN TP LU dans le signal : trade pris avec son SL, mais "
                                      "aucun palier ne sera géré (format du canal changé ?)")
                    await send_channel_alert(
                        "⚠️ **AUCUN TP LU DANS LE SIGNAL**" + chr(10) + chr(10) +
                        "Le trade est pris et le SL est posé, mais **aucun palier ne sera "
                        "géré** et MetaTrader n'aura pas de TP." + chr(10) + chr(10) +
                        "Le format du canal a probablement changé. Le message brut est dans "
                        "la console.", final_source)

                opened_tickets = await execute_trade(sym, act, sl, tp, p_msg, event.date, final_source,
                                                     tp1_price=tp1_price, tp_ladder=tp_ladder)

                if opened_tickets:
                    TELEGRAM_TO_TICKET[event.id] = opened_tickets
                    await send_channel_alert(f"✔️ **Signal Accepté** ({display_source}){display_type_line}\n🛑 SL: {sl} | 🎯 TP: {tp}", final_source)
        else:
            logger.info(f"⚠️ Ignoré (Parsing échoué) sur : {cleaned}")
            # (#111) Le cas le plus fréquent : discussion, publicité, résultat du jour. La ligne
            # prouve que le message a été lu ET jugé -- un signal « manqué » qui apparaît ici
            # est un défaut de lecture ; un signal absent du journal n'est jamais arrivé.
            await noter(_tag, f"💬 PAS UN SIGNAL (id={event.id}) : aucun ordre reconnu")

    except Exception as e:
        logger.error(f"❌ ERREUR HANDLER: {e}")
        await noter(_tag, f"💥 ERREUR en traitant le message (id={event.id}) : {e}")
        import traceback
        traceback.print_exc()
        
    finally:
        PENDING_MESSAGES.pop(event.id, None)

# --- GESTION DES EDITS (MODIFIE TOUS LES ORDRES DE LA ZONE) ---
@client.on(events.MessageEdited(chats=CHANNEL_LIST))
async def edit_handler(event):
    # (#111) Même filtre de salon que main_handler : sans lui, l'édition d'un message de la
    # discussion INDODOG était lue comme un signal, et pouvait réécrire SL/TP des trades INDODOG.
    if _hors_sujet_indodog(event) is not None:
        return
    _tag = tag_canal(chat_id=event.chat_id)

    if event.id in PENDING_MESSAGES:
        PENDING_MESSAGES[event.id] = event.raw_text
        await noter(_tag, f"✏️ MESSAGE ÉDITÉ (id={event.id}) avant d'être traité : c'est cette "
                          f"version qui sera lue :\n{event.raw_text}")
        return

    await noter(_tag, f"✏️ MESSAGE ÉDITÉ (id={event.id}) :\n{event.raw_text}")
    rsym, act, p_msg, sl, tp, format_detected = parse_signal(event.raw_text, event.chat_id)
    if not rsym:
        await noter(_tag, "💬 ÉDITION sans signal : rien à modifier")
        return

    # (#81) `sl`/`tp` sortent d'une expression régulière : ils peuvent valoir une chaîne non
    # convertible ("." par exemple, vu deux fois le 2026-08-11), ce qui faisait remonter
    # "Unhandled exception on edit_handler / ValueError: could not convert string to float".
    # L'édition du message était alors purement ignorée -- un SL corrigé par le canal n'était
    # donc pas appliqué.
    def _to_price(v):
        try:
            return float(v)
        except (TypeError, ValueError):
            logger.warning(f"⚠️ Édition ignorée pour la valeur illisible {v!r} "
                           f"(message {event.id})")
            return 0.0

    new_sl = _to_price(sl) if sl else 0.0
    new_tp = _to_price(tp) if tp else 0.0

    # ==========================================
    # 1. CAS DU SIGNAL AVEUGLE (BLIND -> PRECIS)
    # ==========================================
    # Si le message n'a pas encore de tickets liés, MAIS qu'il a maintenant un prix sans SL/TP :
    if event.id not in TELEGRAM_TO_TICKET and p_msg is not None and new_sl == 0.0 and new_tp == 0.0:
        # C'est un signal "Go buy" qui vient d'être mis à jour avec un prix !
        # On tente d'ouvrir le trade proprement, sans spammer.
        await noter(_tag, f"✏️ SIGNAL AVEUGLE COMPLÉTÉ — {act} {rsym} à {p_msg} : ouverture tentée")
        new_tix = await execute_trade(rsym, act, sl, tp, p_msg, event.date, str(format_detected))
        if new_tix and new_tix != SABR_DUPLICATE:
            TELEGRAM_TO_TICKET[event.id] = new_tix
            await send_alert(f"✔️ **Signal Aveugle Activé** ({format_detected})\n{act} {rsym} à {p_msg}")
        return

    # ==========================================
    # 2. MISE A JOUR SL / TP (NORMAL OU ORPHELIN)
    # ==========================================
    # Si pas de modification de SL ou TP, on ne fait rien
    if new_sl == 0.0 and new_tp == 0.0:
        await noter(_tag, "✏️ ÉDITION sans SL ni TP lisible : rien à modifier")
        return

    tickets_to_update = []

    if event.id in TELEGRAM_TO_TICKET:
        tickets_to_update = TELEGRAM_TO_TICKET[event.id]
    else:
        # Récupération des ORPHELINS (bloqués par doublon, ou ouverts avant un redémarrage :
        # TELEGRAM_TO_TICKET ne vit qu'en mémoire).
        #
        # (#104) Ce ramassage était aveugle : il prenait TOUTES les positions et TOUS les ordres
        # du bot sur le symbole, quel que soit leur canal -- tous partagent MAGIC_NUMBER. Une
        # édition d'un vieux message MDT sur XAUUSD réécrivait donc le SL et le TP des positions
        # SABR, INDODOG et FTS ouvertes sur l'or. Constaté les 2 et 3 septembre sur une position
        # SABR : « le bot a modifié le SL et le TP alors que SABR n'a rien dit », puis le TP
        # modifié une seconde fois le lendemain. On n'accepte plus que les trades issus du MÊME
        # canal que le message édité.
        sym = find_tradable_symbol(rsym)
        if not sym:
            await noter(_tag, f"✏️ ÉDITION ignorée : symbole {rsym} introuvable dans MT5")
            return

        base_edit = _channel_base_token(format_detected)
        if not base_edit:
            logger.warning("✏️ Édition ignorée : canal indéterminé (message %s)" % event.id)
            await noter(_tag, "✏️ ÉDITION ignorée : canal indéterminé")
            return

        def _meme_canal(ticket):
            return _channel_base_token(ACTIVE_TRADE_SOURCES.get(ticket)) == base_edit

        ignores = 0
        for lot_mt5 in (await run_mt5(mt5.positions_get, symbol=sym),
                        await run_mt5(mt5.orders_get, symbol=sym)):
            for x in (lot_mt5 or []):
                if x.magic != MAGIC_NUMBER:
                    continue
                if _meme_canal(x.ticket):
                    tickets_to_update.append(x.ticket)
                else:
                    ignores += 1
        if ignores:
            logger.info("✏️ Édition %s (%s) : %d trade(s) d'un autre canal laissé(s) intacts"
                        % (event.id, base_edit, ignores))
            await noter(_tag, f"✏️ ÉDITION : {ignores} trade(s) d'un autre canal sur {sym} laissé(s) intact(s)")

    if not tickets_to_update:
        await noter(_tag, f"✏️ ÉDITION sans effet : aucun trade de ce canal sur {rsym}")
        return

    success_count = 0
    deja_fermes, inchanges, refuses = 0, 0, 0   # (#111) pour dire au journal pourquoi rien
    for ticket in tickets_to_update:
        is_pending = False
        orders = await run_mt5(mt5.orders_get, ticket=ticket)
        pos = None
        if orders and len(orders) > 0: is_pending = True
        else: pos = await run_mt5(mt5.positions_get, ticket=ticket)

        if not is_pending and not pos:
            deja_fermes += 1
            continue

        current_sl = orders[0].sl if is_pending else pos[0].sl
        current_tp = orders[0].tp if is_pending else pos[0].tp
        
        # Si le SL et le TP sont déjà les mêmes, on passe
        if abs(current_sl - new_sl) < 0.0001 and abs(current_tp - new_tp) < 0.0001:
            inchanges += 1
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
        
        res = await run_mt5(_send_order_blocking, req)
        if res and res.retcode == mt5.TRADE_RETCODE_DONE:
            success_count += 1
            # (#104) Tracé dans kairos.log, pas seulement en alerte Telegram : quand une
            # position se faisait réécrire à tort, le journal n'en gardait aucune trace et
            # l'enquête partait de rien.
            logger.warning("✏️ Édition msg %s (%s) : ticket %s  SL %s -> %s  TP %s -> %s"
                           % (event.id, _channel_base_token(format_detected), ticket,
                              current_sl, new_sl, current_tp, new_tp))
            await noter(_tag, f"✏️ SL/TP MODIFIÉS — ticket {ticket} | SL {current_sl} -> {new_sl} | "
                              f"TP {current_tp} -> {new_tp}")
        else:
            refuses += 1
            await noter(_tag, f"❌ MODIFICATION REFUSÉE — ticket {ticket}"
                              + (f" : {res.comment}" if res else " : MT5 ne répond pas"))

    if success_count == 0 and not refuses:
        await noter(_tag, "✏️ ÉDITION sans effet : "
                          + ", ".join(x for x in (
                              f"{inchanges} trade(s) déjà à ces SL/TP" if inchanges else "",
                              f"{deja_fermes} trade(s) déjà clôturé(s)" if deja_fermes else "") if x))

    if success_count > 0:
        await send_alert(f"✏️ **TRADES MODIFIÉS** (Édition)\n\n"
                         f"Canal : **{format_detected}**\n"
                         f"{success_count} ordres/positions mis à jour sur {rsym}.\n"
                         f"Nouveau SL: {new_sl}\nNouveau TP: {new_tp}")
        
# --- GESTION DES SUPPRESSIONS (NOUVEAU) ---
@client.on(events.MessageDeleted(chats=CHANNEL_LIST))
async def deletion_handler(event):
    _tag = tag_canal(chat_id=getattr(event, "chat_id", None))   # (#111)
    # On parcourt les IDs des messages supprimés
    for msg_id in event.deleted_ids:
        
        # CAS 1 : C'est un message en attente de traitement (Buffer)
        if msg_id in PENDING_MESSAGES:
            logger.info(f"🗑️ Message {msg_id} supprimé du Buffer (Ne sera pas traité).")
            del PENDING_MESSAGES[msg_id]
            # (la ligne « supprimé avant d'être traité » est écrite par main_handler)
            
        # CAS 2 : C'est un trade déjà actif (Ticket existant)
        if msg_id in TELEGRAM_TO_TICKET:
            tickets = TELEGRAM_TO_TICKET[msg_id] # Liste de tickets !
            await noter(_tag, f"🗑 SIGNAL SUPPRIMÉ PAR LE CANAL (id={msg_id}) — tickets {tickets} : "
                              f"ordres en attente annulés, positions ouvertes conservées")
            
            for tk in tickets:
                # On vérifie si c'est un ORDRE EN ATTENTE (Pending)
                orders = await run_mt5(mt5.orders_get, ticket=tk)
                if orders and len(orders) > 0:
                    # C'est un ordre pending -> ON LE SUPPRIME
                    req = {
                        "action": mt5.TRADE_ACTION_REMOVE,
                        "order": tk
                    }
                    res = await run_mt5(_send_order_blocking, req)

                    if res is None:
                        logger.warning(f"⚠️ Timeout MT5: Echec suppression ordre #{tk} (Message supprimé)")
                        continue
                    
                    if res.retcode == mt5.TRADE_RETCODE_DONE:
                        await send_alert(f"🗑️ **SIGNAL SUPPRIMÉ (Canal source)**\n\nOrdre #{tk} annulé.")
                        await noter(_tag, f"🗑 ORDRE ANNULÉ (signal supprimé par le canal) — ticket {tk}")
            
            # Une fois la liste traitée, on nettoie la mémoire
            del TELEGRAM_TO_TICKET[msg_id]

async def risk_schedule_monitor():
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
        acc = await run_mt5(mt5.account_info)
        if not acc:
            return None

        day_total, day_realized, day_float = get_accurate_daily_stats()
        start_bal = acc.balance - day_realized
        day_pct = (day_total / start_bal * 100) if start_bal > 0 else 0.0

        pos_list = await run_mt5(mt5.positions_get)
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
                open_time_str = heure_client(p.time)  # (#108)
                active_trades.append({
                    "ticket": p.ticket, "symbol": p.symbol, "type": otype,
                    "volume": p.volume, "open_price": p.price_open, "current_price": current_p,
                    "sl": p.sl, "tp": p.tp, "profit": p.profit, "pips": round(pips, 1),
                    "open_time": open_time_str,
                    # (#108) Instant réel : le dashboard l'affiche à l'heure du téléphone.
                    "open_ts": int(epoch_mt5(p.time)),
                    "source": ACTIVE_TRADE_SOURCES.get(p.ticket, "Inconnu"),
                    "managed": is_managed,
                })

        closed_trades_today = await run_mt5(_get_closed_trades_today_blocking)

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
            "risk_sabr_other": RISK_PERCENT_SABR_OTHER,
            "sabr_zone_equal_split": SABR_ZONE_EQUAL_SPLIT,
            "sabr_zone_bornes": SABR_ZONE_BORNES,
            "sabr_be_partiel": SABR_BE_PARTIEL_ACTIVE,
            "sabr_be_partiel_pct": SABR_BE_PARTIEL_PCT,
            "sabr_zone_pct1": SABR_ZONE_PCT1, "sabr_zone_pct2": SABR_ZONE_PCT2,
            "sabr_anti_doublon": SABR_ANTI_DOUBLON_ACTIVE,
            "sabr_forward": SABR_FORWARD_ACTIVE, "max_dd": MAX_DAILY_DRAWDOWN,
            "drawdown_active": DRAWDOWN_ACTIVE,
            "sl_suiveur": TRAILING_STOP_PIPS, "sl_sell": SOFT_SL_PERCENT,
            "be_offset_pips": BE_OFFSET_PIPS,
            "lot_indodog": FIXED_LOT_INDODOG,
            "lot_fts": FIXED_LOT_FTS,
            "echelle_indodog": INDO_ECHELLE_TP, "echelle_fts": FTS_ECHELLE_TP,   # (#110)
            "sabr_trailing_active": SABR_TRAILING_ACTIVE, "sabr_trailing_pips": SABR_TRAILING_PIPS,
            "sl_suiveur_active": SL_SUIVEUR_ACTIVE, "soft_sl_active": SOFT_SL_ACTIVE,
            "muted_channels": MUTED_CHANNELS, "allowed_channels": ALLOWED_CHANNELS,
            # (#98/#99) Le dashboard n'affiche le bouton "Ajout manuel" que si ce drapeau est
            # vrai. C'est du confort d'affichage, PAS une securite : le refus qui compte est
            # cote bot, dans le handler de la commande.
            "ajout_manuel": AJOUT_MANUEL_STATS,
            "stats_channels": STATS_CHANNELS,
            "stats_manuel": charge_stats_manuel(),
            # (#pack) False = pack 100% SABR -> le dashboard masque les % globaux
            # (or/indices/devises) et le planning heure/jour, sans effet sur SABR.
            "uses_risk_engine": pack_uses_risk_engine(),
            "security_triggered": security_triggered,
            "time_off_peak": TIME_OFF_PEAK_FACTOR, "prime_hours": PRIME_HOURS,
            "day_factor_0": DAY_RISK_FACTORS.get(0, 1.0), "day_factor_1": DAY_RISK_FACTORS.get(1, 1.0),
            "day_factor_2": DAY_RISK_FACTORS.get(2, 1.0), "day_factor_3": DAY_RISK_FACTORS.get(3, 1.0),
            "day_factor_4": DAY_RISK_FACTORS.get(4, 1.0), "day_factor_5": DAY_RISK_FACTORS.get(5, 1.0),
            "day_factor_6": DAY_RISK_FACTORS.get(6, 1.0),
        }

        # (#stats) `channels_lifetime` a disparu : les stats par canal sont désormais toutes
        # calculées depuis trades_detailed.csv et poussées sous channels_period_<X> (cf.
        # _firebase_push_stats_loop et request_stats). Ce push-ci tourne toutes les 4s, on n'y
        # relit pas un CSV.
        return firebase_sync.build_state_document(
            account, stats, settings, active_trades, closed_trades_today or [],
            # (#57) license_key n'est écrit ici QUE quand ce push a lieu, c'est-à-dire quand ce
            # bot tourne avec une licence active -- firestore.rules s'en sert pour vérifier,
            # via un get() sur licenses/{license_key}, que la licence est toujours ACTIVE avant
            # d'autoriser le dashboard à créer une commande (panic/kill/close_trade/...),
            # indépendamment de l'identité Telegram du client qui reste valide même banni.
            extra={"license_key": USER_LICENSE_KEY,
                   # (#113) Version et fonctions de cet exe, cf. FONCTIONS_BOT.
                   "bot_version": VERSION_BOT, "fonctions": FONCTIONS_BOT}
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
            pos = await run_mt5(mt5.positions_get, ticket=ticket)
            if pos:
                p = pos[0]
                otype = mt5.ORDER_TYPE_SELL if p.type == mt5.ORDER_TYPE_BUY else mt5.ORDER_TYPE_BUY
                tick_p = mt5.symbol_info_tick(p.symbol)
                price_p = tick_p.bid if otype == mt5.ORDER_TYPE_SELL else tick_p.ask
                req = {"action": mt5.TRADE_ACTION_DEAL, "symbol": p.symbol, "volume": p.volume,
                       "type": otype, "position": ticket, "price": price_p,
                       "magic": MAGIC_NUMBER, "type_filling": mt5.ORDER_FILLING_IOC}
                res = await run_mt5(_send_order_blocking, req)
                if res and res.retcode == mt5.TRADE_RETCODE_DONE:
                    set_status("done", {"success": True})
                else:
                    set_status("error", {"success": False, "message": f"MT5 retcode: {res.retcode if res else 'None'}"})
            else:
                set_status("error", {"success": False, "message": "Trade introuvable"})

        elif cmd_type == "be_trade":
            ticket = int(payload.get("ticket", 0))
            pos = await run_mt5(mt5.positions_get, ticket=ticket)
            if pos:
                p = pos[0]
                req = {"action": mt5.TRADE_ACTION_SLTP, "position": ticket, "sl": p.price_open, "tp": p.tp}
                res = await run_mt5(_send_order_blocking, req)
                if res and res.retcode == mt5.TRADE_RETCODE_DONE:
                    set_status("done", {"success": True})
                else:
                    set_status("error", {"success": False, "message": f"MT5 retcode: {res.retcode if res else 'None'}"})
            else:
                set_status("error", {"success": False, "message": "Trade introuvable"})

        elif cmd_type == "panic":
            # (#103) "TOUT COUPER" ne ferme que ce que le bot gère -- c'est voulu, il n'a pas
            # à toucher aux trades manuels du client. Mais il renvoyait success:True même
            # quand il n'avait fermé RIEN DU TOUT : le client cliquait, aucune position ne
            # bougeait, et aucun message ne le lui disait. On compte désormais les deux cas
            # et on remonte le détail.
            pos_list = await run_mt5(mt5.positions_get)
            fermees, ignorees, echecs = 0, [], 0
            for p in (pos_list or []):
                if not (p.magic == MAGIC_NUMBER or p.ticket in MANAGED_MANUAL_TICKETS):
                    ignorees.append(p)
                    continue
                otype = mt5.ORDER_TYPE_SELL if p.type == mt5.ORDER_TYPE_BUY else mt5.ORDER_TYPE_BUY
                req = {"action": mt5.TRADE_ACTION_DEAL, "symbol": p.symbol, "volume": p.volume,
                       "type": otype, "position": p.ticket, "magic": MAGIC_NUMBER,
                       "type_filling": mt5.ORDER_FILLING_IOC}
                res = await run_mt5(_send_order_blocking, req)
                if res and getattr(res, "retcode", None) == mt5.TRADE_RETCODE_DONE:
                    fermees += 1
                else:
                    echecs += 1

            if ignorees:
                syms = ", ".join(sorted({p.symbol for p in ignorees}))
                msg = (f"{fermees} position(s) fermée(s). "
                       f"⚠️ {len(ignorees)} position(s) non gérée(s) par le bot ({syms}) "
                       f"n'ont PAS été touchées : ferme-les dans MetaTrader 5, ou adopte-les "
                       f"avec /suivi <ticket>.")
            elif echecs:
                msg = f"{fermees} position(s) fermée(s), {echecs} refusée(s) par le courtier."
            elif fermees:
                msg = f"{fermees} position(s) fermée(s)."
            else:
                msg = "Aucune position à fermer."
            set_status("done", {"success": echecs == 0, "message": msg,
                                "fermees": fermees, "ignorees": len(ignorees)})

        # (#98) Ajout / suppression d'une ligne de stats manuelle, depuis le dashboard.
        # Le refus est fait ICI : la liste blanche des regles Firestore autorise le type de
        # commande pour tout le monde, et le masquage du bouton cote dashboard n'est que de
        # l'affichage. C'est ce test qui protege reellement.
        elif cmd_type in ("add_manual_stat", "del_manual_stat"):
            if not AJOUT_MANUEL_STATS:
                set_status("error", {"success": False, "message": "Fonction non activée sur ce bot."})
            elif cmd_type == "del_manual_stat":
                lignes = charge_stats_manuel()
                idx = payload.get("index", -1)
                if not isinstance(idx, int) or not (0 <= idx < len(lignes)):
                    set_status("error", {"success": False, "message": "Ligne introuvable."})
                else:
                    retiree = lignes.pop(idx)
                    if sauve_stats_manuel(lignes):
                        logger.info("🧾 Ajout manuel supprime : %s" % retiree)
                        set_status("done", {"success": True, "stats_manuel": lignes})
                    else:
                        set_status("error", {"success": False, "message": "Écriture impossible."})
            else:
                canal = (payload.get("canal") or "").strip()
                date_txt = (payload.get("date") or "").strip()
                if canal not in STATS_CHANNELS:
                    set_status("error", {"success": False, "message": f"Canal inconnu : {canal}"})
                else:
                    try:
                        datetime.strptime(date_txt, "%Y-%m-%d")
                    except Exception:
                        set_status("error", {"success": False, "message": "Date attendue au format AAAA-MM-JJ."})
                        return
                    try:
                        signaux = max(1, int(payload.get("signaux", 1)))
                        ordres = max(signaux, int(payload.get("ordres", signaux) or signaux))
                        profit = float(payload.get("profit", 0.0))
                        pips = float(payload.get("pips", 0.0))
                    except (TypeError, ValueError):
                        set_status("error", {"success": False, "message": "Valeurs numériques invalides."})
                        return
                    ligne = {"date": date_txt, "canal": canal, "signaux": signaux,
                             "ordres": ordres, "profit": round(profit, 2), "pips": round(pips, 1)}
                    sym = (payload.get("symbole") or "").strip().upper()
                    if sym: ligne["symbole"] = sym
                    note = (payload.get("note") or "").strip()
                    if note: ligne["note"] = note[:120]

                    lignes = charge_stats_manuel()
                    lignes.append(ligne)
                    lignes.sort(key=lambda l: str(l.get("date", "")), reverse=True)
                    if sauve_stats_manuel(lignes):
                        logger.info("🧾 Ajout manuel enregistre : %s" % ligne)
                        await send_alert(f"🧾 **AJOUT MANUEL AUX STATS**\n\n"
                                         f"{canal.replace('CANAL ', '')} · {date_txt}\n"
                                         f"{signaux} signal(s) · {profit:+.2f} € · {pips:+.1f} pips")
                        set_status("done", {"success": True, "stats_manuel": lignes})
                    else:
                        set_status("error", {"success": False, "message": "Écriture impossible."})

        elif cmd_type == "unlock":
            global security_triggered, DD_PANIC_NOTIFIED
            # (#DD) Refuse le déverrouillage si la perte du jour dépasse ENCORE le Max DD
            # actuel -- il faut d'abord relever le seuil (dashboard), sinon on ne ferait que
            # ré-autoriser une seconde puis re-déclencher la fermeture d'urgence juste après.
            if check_daily_max_loss():
                set_status("error", {"success": False, "message": "Augmente d'abord le Max DD -- la perte du jour dépasse toujours la limite actuelle. Ou coupe le disjoncteur (/dd off)."})
            else:
                security_triggered = False
                DD_PANIC_NOTIFIED = False   # (#103) ré-arme l'alerte pour le prochain épisode
                set_status("done", {"success": True})

        elif cmd_type == "suivre":
            ticket = int(payload.get("ticket", 0))
            MANAGED_MANUAL_TICKETS.add(ticket)
            await run_bg(save_memory)
            ACTIVE_TRADE_SOURCES[ticket] = "CANAL PERSO"
            ACTIVE_TRADE_SIGNALS[ticket] = f"MANUEL-{ticket}"
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

        elif cmd_type == "toggle_drawdown":
            # (#8) Active/désactive le disjoncteur drawdown
            global DRAWDOWN_ACTIVE
            DRAWDOWN_ACTIVE = not DRAWDOWN_ACTIVE
            await run_bg(save_settings)
            await send_alert(f"📉 Disjoncteur Drawdown : {'🟢 ACTIVÉ' if DRAWDOWN_ACTIVE else '🔴 DÉSACTIVÉ'}")
            set_status("done", {"success": True})

        elif cmd_type == "toggle_sl_suiveur":
            # (#10) Active/désactive le déplacement du SL (BE/trailing/paliers)
            global SL_SUIVEUR_ACTIVE
            SL_SUIVEUR_ACTIVE = not SL_SUIVEUR_ACTIVE
            await run_bg(save_settings)
            await send_alert(f"🔒 SL Suiveur : {'🟢 ACTIVÉ' if SL_SUIVEUR_ACTIVE else '🔴 DÉSACTIVÉ'}")
            set_status("done", {"success": True})

        elif cmd_type == "toggle_soft_sl":
            # (#10) Active/désactive la clôture partielle sur repli (Soft SL Vente)
            global SOFT_SL_ACTIVE
            SOFT_SL_ACTIVE = not SOFT_SL_ACTIVE
            await run_bg(save_settings)
            # (#103) Le Soft SL vit DANS la branche `if TRAILING_STOP_PIPS > 0`, et se
            # declenche a TRAILING_STOP_PIPS sous le sommet : a 0, la branche n'est jamais
            # prise et le reglage ne peut rien faire. Le dashboard presentant les deux lignes
            # comme independantes, un client pouvait l'activer et attendre indefiniment.
            avert = ("\n\n⚠️ **Sans effet tant que « SL Suiveur (pips) » vaut 0** : "
                     "c'est ce champ qui active le mode ET qui fixe la distance de repli."
                     ) if (SOFT_SL_ACTIVE and TRAILING_STOP_PIPS <= 0) else ""
            await send_alert(f"📉 Soft SL Vente : {'🟢 ACTIVÉ' if SOFT_SL_ACTIVE else '🔴 DÉSACTIVÉ'}{avert}")
            set_status("done", {"success": True, "message": avert.strip() or None})

        elif cmd_type == "toggle_sabr_doublon":
            # (#pack) Équivalent dashboard de /doublon_sabr -- l'état était déjà poussé au
            # dashboard (et affiché dans /info) mais sans aucun moyen de le changer là-bas.
            global SABR_ANTI_DOUBLON_ACTIVE
            if not (channel_allowed("CANAL SABR_VIP") or channel_allowed("CANAL SABR_FREE")
                    or channel_allowed("CANAL SABR_COPIE")):
                set_status("error", {"success": False, "message": "SABR hors de ton pack."})
            else:
                SABR_ANTI_DOUBLON_ACTIVE = not SABR_ANTI_DOUBLON_ACTIVE
                await run_bg(save_settings)
                await send_alert(f"🛡️ Anti-Doublon SABR : {'🟢 ACTIVÉ' if SABR_ANTI_DOUBLON_ACTIVE else '🔴 DÉSACTIVÉ'}")
                set_status("done", {"success": True})

        elif cmd_type == "toggle_sabr_trailing":
            # (#73) Interrupteur du SL suiveur SABR. Même verrou de pack que l'anti-doublon :
            # inutile d'exposer un réglage SABR à un client qui n'a pas ce canal.
            global SABR_TRAILING_ACTIVE
            if not (channel_allowed("CANAL SABR_VIP") or channel_allowed("CANAL SABR_FREE")
                    or channel_allowed("CANAL SABR_COPIE")):
                set_status("error", {"success": False, "message": "SABR hors de ton pack."})
            else:
                SABR_TRAILING_ACTIVE = not SABR_TRAILING_ACTIVE
                await run_bg(save_settings)
                await send_alert(f"📈 SL Suiveur SABR : {'🟢 ACTIVÉ' if SABR_TRAILING_ACTIVE else '🔴 DÉSACTIVÉ'}"
                                 + (f" ({SABR_TRAILING_PIPS} pips)" if SABR_TRAILING_ACTIVE else ""))
                set_status("done", {"success": True})

        elif cmd_type == "set_allowed_channels":
            # (#7) Met à jour à distance le pack du client (canaux visibles).
            # Envoyé par la commande /channels du bot de licence via Firestore.
            global ALLOWED_CHANNELS, SERVER_PACK_CHANNELS
            new_list = payload.get("channels", cmd_data.get("channels", []))
            if isinstance(new_list, str):
                new_list = [c.strip() for c in new_list.split(",") if c.strip()]
            ALLOWED_CHANNELS = [c for c in (new_list or []) if c]
            # (#39) Met aussi à jour SERVER_PACK_CHANNELS -- sinon le prochain check_license_online()
            # (démarrage ou toutes les 4h) réécrase ce changement avec l'ancienne valeur du
            # document licence Firestore, qui n'était jamais mise à jour par ce chemin.
            SERVER_PACK_CHANNELS = list(ALLOWED_CHANNELS)
            recompute_pack_lock()  # (#25) reverrouille les canaux hors du nouveau pack
            await run_bg(save_settings)
            pack_txt = ", ".join(c.replace("CANAL ", "") for c in ALLOWED_CHANNELS) if ALLOWED_CHANNELS else "TOUS les canaux"
            await send_alert(f"📦 **Pack mis à jour**\n\nCanaux visibles : {pack_txt}")
            set_status("done", {"success": True, "allowed_channels": ALLOWED_CHANNELS})

        elif cmd_type == "update_setting":
            # Réutilise la logique existante via appel direct
            from pydantic import BaseModel as BM
            class _S(BM):
                key: str
                value: Any
            _res = await api_update_setting(_S(key=payload.get("key", ""), value=payload.get("value")))
            set_status("done", {"success": _res.get("status") == "success", **({"message": _res["message"]} if "message" in _res else {})})

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
            days = str(payload.get("days", "30"))
            # (#108) Le dashboard joint le fuseau du telephone : les periodes partent du minuit
            # du CLIENT, et non de celui de la machine du bot (un VPS en UTC, par exemple).
            tz = noter_fuseau_client(payload)
            if days == "custom":
                # (#107) Plage choisie au dashboard. Une seule cle pour toutes les plages, qui
                # recopie debut/fin : le dashboard verifie qu'il affiche bien la plage demandee,
                # et le document Firestore (limite a 1 Mo) ne grossit pas a chaque essai.
                bornes = bornes_plage(payload, tz)
                if bornes is None:
                    set_status("error", {"success": False, "message":
                                         "Plage invalide : la date de début doit précéder celle de fin."})
                    return
                info = {}
                stats = await get_historical_stats(bornes)
                chan_stats = await run_bg(get_channel_stats_period, bornes, info)
                curve = await get_equity_curve(bornes)
                if firebase_sync._firebase_initialized:
                    await firebase_sync.push_state(user_id, {"stats_plage": firebase_sync._safe({
                        "debut": bornes["jour_debut"], "fin": bornes["jour_fin"],
                        "global": stats or {}, "courbe": curve,
                        "canaux": {ch: v for ch, v in chan_stats.items() if channel_allowed(ch)},
                        "seed_exclu": [x for x in info.get("seed_exclu", [])
                                       if channel_allowed(x["canal"])],
                    })})
                set_status("done", {"success": True})
                return
            # (#95/#108) Une seule regle, celle de l'historique des trades (cf. bornes_periode) :
            #   "today" = aujourd'hui depuis 00h00, "week" = depuis lundi 00h00 ;
            #   "14", "30"... = N journees entieres, aujourd'hui compris. Avant, ces dernieres
            #   comptaient N x 24 h a l'heure pres : "1M" ne couvrait pas les memes trades que
            #   le "1M" de l'historique.
            bornes = bornes_periode(days, tz)
            if bornes is None:
                # Plus de repli silencieux sur 30 jours : il poussait d'autres chiffres que ceux
                # demandes, sous la cle de la periode demandee, sans que rien ne le signale.
                logger.warning(f"⚠️ request_stats : période inconnue {days!r}")
                set_status("error", {"success": False, "message": f"Période inconnue : {days}"})
                return
            stats = await get_historical_stats(bornes)
            chan_stats = await run_bg(get_channel_stats_period, bornes)
            curve = await get_equity_curve(bornes)
            if firebase_sync._firebase_initialized:
                payload_push = {}
                if stats:
                    payload_push[f"global_period_{days}"] = firebase_sync._safe(stats)
                # (#65) Courbe de balance de la MÊME période, pour le graphique sous les 4 cases.
                payload_push[f"equity_curve_{days}"] = firebase_sync._safe(curve)
                # (#stats) Stats par canal sur la même période que l'onglet Global. Poussé
                # même si vide : un dict vide est une réponse légitime (aucun trade sur la
                # période) et le dashboard doit pouvoir l'afficher plutôt que rester bloqué
                # sur la période précédente.
                payload_push[f"channels_period_{days}"] = firebase_sync._safe(
                    {ch: v for ch, v in chan_stats.items() if channel_allowed(ch)})
                await firebase_sync.push_state(user_id, payload_push)
            set_status("done", {"success": True})

        elif cmd_type == "request_history":
            # (#74) Historique des trades sur la periode demandee. Commande dediee et non
            # greffee sur request_stats : la section a son propre selecteur, ouvert/ferme
            # independamment de la fenetre de statistiques.
            period = str(payload.get("period", "1"))
            tz = noter_fuseau_client(payload)  # (#108)
            bornes = bornes_periode(period, tz)
            if bornes is None:
                logger.warning(f"⚠️ request_history : période inconnue {period!r}")
                set_status("error", {"success": False, "message": f"Période inconnue : {period}"})
                return
            canal = str(payload.get("canal") or "").strip()
            if canal:
                # (#109) Historique d'un seul canal. Une seule clé pour tous les filtres, qui
                # recopie période et canal : une clé par combinaison (6 périodes x 9 canaux,
                # 100 lignes chacune) approcherait la limite de 1 Mo du document Firestore.
                if canal not in STATS_CHANNELS:
                    set_status("error", {"success": False, "message": f"Canal inconnu : {canal}"})
                    return
                hist = await run_bg(get_trades_history, bornes, canal=canal)
                DERNIER_HISTORIQUE_FILTRE.update({"period": period, "canal": canal, "t": time.time()})
                if firebase_sync._firebase_initialized:
                    await firebase_sync.push_state(user_id, {"trades_history_filtre": firebase_sync._safe(
                        {"period": period, "canal": canal, "trades": hist})})
                set_status("done", {"success": True})
                return
            hist = await run_bg(get_trades_history, bornes)
            if firebase_sync._firebase_initialized:
                await firebase_sync.push_state(user_id, {
                    f"trades_history_{period}": firebase_sync._safe(hist)})
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
    # Publie d'abord les métadonnées (telegram_id + bot_token) nécessaires à l'auth du dashboard
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, firebase_sync.publish_client_metadata, user_id, BOT_TOKEN, USER_LICENSE_KEY)
    firebase_sync.start_command_listener(user_id, handle_firebase_command, asyncio.get_event_loop())
    asyncio.create_task(firebase_sync.firebase_push_loop(user_id, get_current_state_for_firebase, interval_seconds=4))
    asyncio.create_task(_firebase_push_stats_loop(user_id))
    logger.info(f"🔥 Firebase SaaS activé pour user {user_id}.")


async def _firebase_push_stats_loop(user_id: str) -> None:
    """Pousse les stats historiques (30j) vers Firestore toutes les 60 secondes."""
    while True:
        await asyncio.sleep(60)
        try:
            # (#108) Memes bornes que les demandes du dashboard (meme fonction, meme fuseau
            # client) : sinon ce rafraichissement ecraserait toutes les 60 s la reponse a une
            # demande par des chiffres calcules autrement.
            b30 = bornes_periode("30")
            stats_30d = await get_historical_stats(b30)
            if stats_30d and firebase_sync._firebase_initialized:
                # (#stats) channels_period_30 est poussé ici pour que l'onglet "Par Canal" ait
                # quelque chose à afficher dès l'ouverture du dashboard, sans attendre l'aller-
                # retour request_stats. Les autres périodes restent à la demande.
                chan_30 = await run_bg(get_channel_stats_period, b30)
                envoi = {
                    "global_period_30": firebase_sync._safe(stats_30d),
                    "channels_period_30": firebase_sync._safe(
                        {c: v for c, v in chan_30.items() if channel_allowed(c)}),
                    "equity_curve_30": firebase_sync._safe(await get_equity_curve(b30)),
                    "trades_history_1": firebase_sync._safe(
                        await run_bg(get_trades_history, bornes_periode("1"))),
                }
                # (#109) L'historique filtré qu'on regarde se met à jour comme « Jour ».
                f = dict(DERNIER_HISTORIQUE_FILTRE)
                if f and time.time() - f.get("t", 0) < 3600:
                    bf = bornes_periode(f["period"])
                    if bf is not None:
                        envoi["trades_history_filtre"] = firebase_sync._safe({
                            "period": f["period"], "canal": f["canal"],
                            "trades": await run_bg(get_trades_history, bf, canal=f["canal"])})
                await firebase_sync.push_state(user_id, envoi)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.warning(f"Erreur push stats historiques : {e}")


async def mark_bot_offline_and_exit():
    await firebase_sync.mark_bot_offline(str(TELEGRAM_DESTINATION_ID))
    # (#46) STOP_ME signale un arrêt VOLONTAIRE au watchdog externe, qui sinon relance le
    # worker automatiquement. `os.exit()` n'existe pas (AttributeError) : c'était la raison
    # pour laquelle un kill déclenché depuis le dashboard ne stoppait jamais réellement le bot.
    with open("STOP_ME", "w") as f: f.write("STOP")
    os._exit(0)


# (#103) Diagnostic de connexion MetaTrader.
#
# L'ancienne version tenait en trois lignes : un mt5.initialize() nu, un message unique
# ("MetaTrader 5 fermé ou inaccessible") et un `return` sec. Trois causes tres differentes
# aboutissaient donc au meme texte -- terminal ferme, terminal ouvert mais pas connecte a son
# compte, ou niveaux d'elevation differents entre Kairos (toujours admin, cf. uac_admin) et
# MetaTrader. Un client a passe un week-end a chercher, avec MT5 ouvert sous les yeux, alors
# que son compte demo avait expire ("authorization failed (Invalid account)" dans le journal
# MetaTrader). mt5.last_error() connaissait la reponse depuis le debut.
MT5_CAUSES = {
    -10005: "Le terminal ne répond pas (délai dépassé).",
    -10004: "Connexion au terminal refusée.",
    -10003: "Impossible d'initialiser le lien avec le terminal.",
    -8:     "Le trading algorithmique est désactivé dans MetaTrader.",
    -6:     "MetaTrader a refusé l'autorisation (compte non connecté ?).",
    -4:     "Terminal MetaTrader introuvable sur ce PC.",
}
# (#106) Delais entre deux essais : ils s'allongent, puis plafonnent a 5 min, indefiniment.
# La V15.14.0 abandonnait au bout de 10 essais et laissait le watchdog relancer TOUT le worker
# (connexion Telegram, verification de licence...) : un cycle complet toutes les ~6 min tant
# que MT5 restait ferme, soit ~80 redemarrages sur une nuit, pour rien.
MT5_DELAIS_S = (30, 60, 120, 300)
MT5_ALERTE_APRES_S = 600   # alerte Telegram au bout de 10 min d'echec


def _tenter_connexion_mt5():
    """Un essai de connexion. Renvoie (ok, code, libelle).

    MT5_PATH n'est utilise que s'il pointe sur un fichier existant : un chemin errone dans
    settings.json ferait echouer l'initialisation alors que l'auto-detection, elle, aurait
    pu aboutir. On essaie donc le chemin configure d'abord, l'auto-detection ensuite.
    """
    tentatives = []
    if MT5_PATH and os.path.exists(MT5_PATH):
        tentatives.append({"path": MT5_PATH})
    tentatives.append({})   # auto-detection (comportement historique)

    for kwargs in tentatives:
        try:
            if mt5.initialize(**kwargs):
                return True, 0, ""
        except Exception as e:
            logger.warning(f"⚠️ mt5.initialize({kwargs}) a levé : {e}")
    try:
        code, libelle = mt5.last_error()
    except Exception:
        code, libelle = 0, "inconnue"
    # (#106) On ne relance plus le processus entre deux essais : on remet donc a zero
    # explicitement le lien avec le terminal, comme le faisait de fait un redemarrage.
    try:
        mt5.shutdown()
    except Exception:
        pass
    return False, code, libelle


def _banniere_echec_mt5(code, libelle, essai, delai):
    """Message client : le code brut, la cause probable, et les gestes dans l'ordre."""
    lignes = [
        "", "█" * 68,
        " ⚠️  METATRADER 5 : CONNEXION IMPOSSIBLE ".center(67, "█"),
        "█" * 68, "",
        f" Code MetaTrader : {code}  ({libelle})",
    ]
    if code in MT5_CAUSES:
        lignes.append(f" Cause probable  : {MT5_CAUSES[code]}")
    if MT5_PATH and not os.path.exists(MT5_PATH):
        lignes += ["",
                   " ❗ Le chemin MT5_PATH de settings.json n'existe pas :",
                   f"    {MT5_PATH}",
                   "    Corrigez-le, ou videz-le pour laisser Kairos chercher tout seul."]
    lignes += [
        "",
        " À VÉRIFIER, DANS CET ORDRE :",
        "",
        " 1. MetaTrader 5 est-il bien OUVERT ?",
        "",
        " 2. Est-il CONNECTÉ à votre compte ?",
        "    Regardez le voyant tout en bas à DROITE de MetaTrader :",
        "      • vert, avec des kilo-octets qui montent = connecté",
        "      • rouge, ou « 0 / 0 Kb »                 = PAS connecté",
        "    Un terminal ouvert mais déconnecté ne suffit pas.",
        "    Reconnectez-vous : Fichier > Connexion au compte de trading.",
        "    (Un compte démo non utilisé finit par être supprimé par le serveur.)",
        "",
        " 3. Kairos tourne en administrateur : MetaTrader doit l'être AUSSI.",
        "    Clic droit sur MetaTrader 5 > Exécuter en tant qu'administrateur.",
        "",
        " 4. Plusieurs MetaTrader installés ? N'en laissez qu'UN seul ouvert,",
        "    ou indiquez le bon dans MT5_PATH (settings.json).",
        "",
        f" Nouvel essai dans {delai} s  (tentative {essai})",
        " Laissez cette fenêtre ouverte : la connexion se fera toute seule",
        " dès que MetaTrader sera prêt.",
        "█" * 68, "",
    ]
    return "\n".join(lignes)


def _alerte_directe(texte):
    """(#106) Message Telegram envoye par l'API HTTP du bot, sans passer par bot_user.

    Pendant la connexion a MT5, rien n'est encore pret pour send_alert() : bot_user n'est
    demarre qu'apres, et ADMIN_ID n'est charge que par load_settings(). Avancer ces deux
    etapes aurait permis a des commandes Telegram d'arriver avant le chargement des reglages.
    Le jeton du bot et le destinataire, eux, sont lus des l'import. Texte brut : pas de
    Markdown, dont la syntaxe differe entre Telethon et l'API HTTP. True si envoye.
    """
    if not (BOT_TOKEN and ALERTE_CHAT_ID):
        return False
    try:
        r = requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                          json={"chat_id": ALERTE_CHAT_ID, "text": texte}, timeout=10)
        return r.status_code == 200
    except Exception as e:
        logger.warning(f"⚠️ Alerte Telegram directe impossible : {e}")
        return False


async def _avec_pouls(coro):
    """(#106) Attend `coro` en entretenant le pouls toutes les 5 s.

    mt5.initialize() peut bloquer jusqu'a 60 s a lui seul, et un essai de connexion en fait
    jusqu'a deux (MT5_PATH puis auto-detection). Sans pouls pendant ce temps, le watchdog
    (seuil 60 s) pouvait tuer un worker qui attendait simplement le terminal.
    """
    tache = asyncio.ensure_future(coro)
    while not tache.done():
        update_pulse()
        await asyncio.wait({tache}, timeout=5)
    return tache.result()


async def connexion_mt5():
    """Connexion a MetaTrader, avec diagnostic et reprise. Ne rend la main qu'une fois connecte.

    Historique : la toute premiere version testait UNE fois puis faisait `return`, et le bot
    restait bloque a vie (#103). La V15.14.0 retentait 10 fois puis abandonnait, laissant le
    watchdog relancer tout le worker -- un redemarrage complet toutes les ~6 min tant que MT5
    restait ferme.

    (#106) On ne rend plus la main : les essais s'espacent (30 s, 1, 2 puis 5 min), le pouls
    est entretenu pour que le watchdog laisse faire, et la connexion se fait toujours dans les
    5 min qui suivent le retour de MT5. Surtout, le client est PREVENU : sur un VPS personne ne
    regarde la console, et la banniere de diagnostic s'affichait dans une fenetre que
    personne ne voyait. Une alerte part au bout de 10 min, une autre au retour.
    """
    debut = time.time()
    essai = 0
    dernier_code = None
    alerte_envoyee = False
    while True:
        essai += 1
        update_pulse()
        ok, code, libelle = await _avec_pouls(run_mt5(_tenter_connexion_mt5))
        if ok:
            if essai > 1:
                duree = int((time.time() - debut) // 60)
                client_status(f"✔️ MetaTrader 5 connecté (après {essai} tentatives, {duree} min).")
                if alerte_envoyee:
                    await run_bg(_alerte_directe,
                                 f"✅ MetaTrader 5 de nouveau joignable après {duree} min. "
                                 f"Le bot démarre normalement.")
            return True

        delai = MT5_DELAIS_S[min(essai - 1, len(MT5_DELAIS_S) - 1)]
        logger.critical(f"❌ MT5 injoignable [{code}] {libelle} (essai {essai}, prochain dans {delai} s)")
        # Banniere complete au premier echec ou quand la cause change ; sinon une ligne, pour
        # ne pas noyer la console sous des dizaines de bannieres identiques.
        if code != dernier_code:
            client_status(_banniere_echec_mt5(code, libelle, essai, delai))
            dernier_code = code
        else:
            client_status(f"⏳ MetaTrader 5 toujours injoignable [{code}] — essai {essai}, "
                          f"prochain dans {delai} s.")

        if not alerte_envoyee and time.time() - debut >= MT5_ALERTE_APRES_S:
            minutes = int((time.time() - debut) // 60)
            alerte_envoyee = await run_bg(
                _alerte_directe,
                f"⚠️ MetaTrader 5 injoignable depuis {minutes} min.\n\n"
                f"Code : {code} ({libelle})\n\n"
                f"Le bot ne peut pas trader. Vérifie que MetaTrader est ouvert ET connecté à "
                f"ton compte (voyant vert en bas à droite). Le bot reprendra tout seul.")

        for t in range(delai):
            await asyncio.sleep(1)
            if t % 5 == 0:
                update_pulse()

async def main():
    logger.info("\n" + "—"*50)
    logger.info("🔄 INITIALISATION DES COMPOSANTS...")

    if not await connexion_mt5():
        return
    acc_init = mt5.account_info()
    # (#103) account_info() peut etre None sur un terminal connecte sans compte : l'ancien
    # `mt5.account_info().login` levait alors un AttributeError remonte en "Crash Fatal Worker".
    if acc_init is None:
        logger.critical("❌ Terminal joignable mais aucun compte connecté.")
        client_status("❌ MetaTrader répond, mais aucun compte n'est connecté.\n"
                      "   Fichier > Connexion au compte de trading, puis relancez Kairos.")
        return
    logger.info(f"✔️ Terminal MT5 Connecté (Compte: {acc_init.login})")
    client_status(f"✔️ MetaTrader 5 connecté (compte {acc_init.login})")

    # (#69) Sans "Trading algorithmique" coché dans MT5, la connexion réussit, les signaux
    # arrivent, mais CHAQUE ordre est rejeté (retcode 10027). Le client voyait un bot qui
    # démarre normalement puis échoue en silence sur tous ses trades, sans indice sur la cause.
    try:
        term = mt5.terminal_info()
        if term is not None and not term.trade_allowed:
            logger.critical("❌ Trading algorithmique DÉSACTIVÉ dans MetaTrader 5.")
            client_status(
                "\n" + "█" * 65 + "\n" +
                " ⚠️  TRADING ALGORITHMIQUE DÉSACTIVÉ DANS METATRADER 5 ".center(64, "█") + "\n" +
                "█" * 65 + "\n\n" +
                " Le programme est bien connecté, mais MetaTrader refusera TOUS\n"
                " les ordres tant que cette option est désactivée.\n\n"
                " 👉 Dans MetaTrader 5, cliquez sur le bouton « Algo Trading »\n"
                "    de la barre d'outils : il doit devenir VERT.\n\n"
                " Si le bouton est absent : menu Outils > Options > Expert Advisors,\n"
                " puis cochez « Autoriser le trading algorithmique ».\n\n"
                " ⚠️ DÉCOCHEZ AUSSI, dans ce même onglet :\n"
                "    « Désactiver le trading algorithmique lors du changement\n"
                "      de compte » — sinon MetaTrader recoupe l'option tout seul\n"
                "    à chaque fois que vous changez de compte, et plus aucun\n"
                "    ordre ne passe.\n\n"
                " Aucun redémarrage nécessaire, l'activation est prise en compte\n"
                " immédiatement.\n" + "█" * 65 + "\n")
    except Exception as e:
        logger.warning(f"⚠️ Vérification du trading algorithmique impossible : {e}")

    # (#103) Mode de compte. Kairos suit ses positions PAR TICKET : ACTIVE_TRADE_SOURCES est
    # indexe par ticket, close_partial() envoie "position": pos.ticket, et une zone SABR /
    # INDODOG / FTS pose jusqu'a 3 ordres sur le MEME symbole. Sur un compte NETTING, MT5
    # fusionne tout ce qui touche un symbole en UNE position : deux des trois tickets
    # disparaissent, le bot croit ces positions fermees alors que le volume est au marche,
    # les partiels sont rejetes, et un signal vendeur d'un canal REDUIT la position acheteuse
    # d'un autre canal. Rien de tout cela ne remonte une erreur : on refuse de demarrer.
    try:
        acc_mode = mt5.account_info()
        if acc_mode is not None and acc_mode.margin_mode != mt5.ACCOUNT_MARGIN_MODE_RETAIL_HEDGING:
            logger.critical(f"❌ Compte NON hedging (margin_mode={acc_mode.margin_mode}). Arrêt.")
            client_status(
                "\n" + "█" * 68 + "\n" +
                " ⛔  COMPTE INCOMPATIBLE : MODE « NETTING » ".center(67, "█") + "\n" +
                "█" * 68 + "\n\n"
                " Votre compte MetaTrader est en mode NETTING. Kairos a besoin\n"
                " d'un compte en mode HEDGING pour fonctionner.\n\n"
                " Pourquoi : en netting, MetaTrader fusionne toutes les positions\n"
                " d'un même symbole en une seule. Les 3 ordres d'une zone SABR se\n"
                " confondraient, les prises partielles seraient refusées, et un\n"
                " signal vendeur pourrait fermer un achat d'un autre canal.\n\n"
                " 👉 Le mode est écrit dans le TITRE de la fenêtre MetaTrader :\n"
                "    « 12345678 - MonBroker - Netting » ou « ... - Hedge ».\n\n"
                " Il ne se change pas : il faut ouvrir un compte en Hedging chez\n"
                " votre courtier. Les comptes prop (FTMO & co) le sont toujours.\n\n"
                " Le bot ne démarrera pas tant que ce compte est utilisé.\n"
                + "█" * 68 + "\n")
            return
    except AttributeError:
        # Vieux paquet MetaTrader5 sans la constante : on n'invente pas un blocage.
        logger.warning("⚠️ margin_mode indisponible dans ce paquet MetaTrader5 — contrôle ignoré.")
    except Exception as e:
        logger.warning(f"⚠️ Vérification du mode de compte impossible : {e}")

    await bot_user.start(bot_token=BOT_TOKEN)
    logger.info("✔️ Interface interactive Telegram chargée.")
    client_status("✔️ Interface Telegram chargée.")

    # Bouton "Dashboard" permanent du bot -- posé à chaque démarrage (idempotent), donc les
    # clients déjà appairés l'obtiennent aussi, sans manipulation de leur part.
    await run_bg(setup_bot_menu_button)

    load_memory()
    load_settings()
    load_channel_data()
    load_ladder_stats()   # (#87/#90)
    load_stats_reset()    # (#91)
    charger_fuseau_client()  # (#108)

    if not await init_daily_stats(): 
        logger.error("❌ Erreur de chargement des statistiques.")
        return

    asyncio.create_task(risk_schedule_monitor())
    asyncio.create_task(monitor_positions())
    asyncio.create_task(license_watchdog())
    asyncio.create_task(safety_net_scanner())
    asyncio.create_task(heartbeat_monitor())  # (#35) oublié -- sans lui, alive.txt n'est jamais
    # réécrit et le watchdog ne peut jamais détecter un gel du worker.
    asyncio.create_task(horloge_serveur_monitor())  # (#108)

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

        pairing_banner = (
            "\n" + "█"*65 + "\n" +
            " 🔐 INITIALISATION : LIAISON TELEGRAM REQUISE ".center(64, "█") + "\n" +
            "█"*65 + "\n\n" +
            " ✅ Succès : Le programme est connecté à MetaTrader 5.\n" +
            " 👤 Il ne reste plus qu'à vous identifier comme le propriétaire.\n\n" +
            " 📱 ÉTAPE 1 : Ouvrez Telegram sur votre téléphone ou PC.\n" +
            f" 🔍 ÉTAPE 2 : Cherchez votre bot : {bot_username}\n" +
            " 💬 ÉTAPE 3 : Cliquez sur 'Démarrer' et envoyez cette commande :\n\n" +
            f"                         /start {SECURITY_PIN}\n\n" +
            " ⏳ Le système est en pause et attend votre message sur Telegram...\n" +
            "█"*65 + "\n"
        )
        logger.warning(pairing_banner)
        client_status(pairing_banner)  # (#30) essentiel -- toujours visible, meme en mode "client"
    else:
        logger.warning("")
        logger.info("═"*50)
        logger.info("✅ SYSTÈME OPÉRATIONNEL ET SÉCURISÉ")
        logger.info("📡 En attente des signaux de trading...")
        client_status("\n" + "═"*50 + "\n✅ SYSTÈME OPÉRATIONNEL ET SÉCURISÉ\n📡 En attente des signaux de trading...\n" + "═"*50)
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
    resize_console_window()
    # 🧹 Nettoie la console Windows au tout démarrage
    os.system('cls' if os.name == 'nt' else 'clear')
    
    logger.info("==================================================")
    logger.info("      🚀 DÉMARRAGE DU MOTEUR KAIROS 🚀          ")
    logger.info("==================================================")
    logger.info("🛡️ Système de surveillance (Watchdog) activé.")

    # Nettoyage du fichier de vie au démarrage
    if os.path.exists(WATCH_FILE):
        try: os.remove(WATCH_FILE)
        except: pass
    # (#46) Nettoyage d'un STOP_ME laissé par une session précédente, pour ne pas bloquer
    # ce nouveau démarrage volontaire du Chef.
    if os.path.exists("STOP_ME"):
        try: os.remove("STOP_ME")
        except: pass

    while True:
        # (#46) Un STOP_ME présent ici signifie que le Worker vient de s'arrêter
        # VOLONTAIREMENT (/kill, kill dashboard, licence révoquée) -- sans cette vérif, le
        # Chef le relançait automatiquement quelques secondes après, rendant ces arrêts
        # inopérants. On ne relance pas et on termine le Chef.
        if os.path.exists("STOP_ME"):
            logger.info("🛑 Arrêt volontaire détecté (STOP_ME) — le Chef ne relance pas le Worker.")
            try: os.remove("STOP_ME")
            except: pass
            break

        # On prépare la commande pour lancer l'Ouvrier (soi-même avec l'argument --worker)
        # (#nuitka) sys.frozen est une convention PyInstaller -- Nuitka ne le définit pas, il a
        # sa propre variable __compiled__. Mais sous Nuitka (mode standalone), sys.executable
        # pointe vers un "python.exe" FICTIF qui n'existe pas réellement sur le disque -- c'est
        # sys.argv[0] qui contient le vrai chemin de l'exe compilé (confirmé par test : sans ce
        # cas séparé, subprocess.Popen plantait avec FileNotFoundError [WinError 2]).
        if globals().get("__compiled__"):
            # Cas .exe (Nuitka)
            cmd = [sys.argv[0], "--worker"]
        elif getattr(sys, 'frozen', False):
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

            # A. Vérifier si l'ouvrier s'est arrêté (crash OU arrêt volontaire, cf. STOP_ME
            # vérifié au sommet de la boucle externe)
            if process.poll() is not None:
                logger.warning("⚠️ Le Worker s'est arrêté. Nouvelle itération du Chef...")
                break # On sort de la boucle pour relancer (ou s'arrêter si STOP_ME présent)

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
        resize_console_window()
        os.system('cls' if os.name == 'nt' else 'clear')
        print_kairos_logo()

        # 1. Synchronisation Heure
        sync_windows_time()

        # 2. Vérification Licence
        if not check_license_online():
            time.sleep(5)
            sys.exit()

        logger.info("🔵 Démarrage du Bot Kairos (Mode Worker)...")
        client_status("🔵 Démarrage du bot (Mode Worker)...")
        
        try:
            # (#30) Ce bloc n'a de sens QUE la toute première fois (avant que Telethon ait
            # sauvegardé la session sur disque) -- sinon il s'affichait à chaque relance,
            # même une fois déjà connecté, ce qui contredit "juste le logo" en mode client.
            if not os.path.exists("ftmo_bot_session.session"):
                print("\n" + "═"*60)
                print(" 📱 CONNEXION À VOTRE COMPTE TELEGRAM (TELETHON)")
                print("═"*60)
                print(" Le bot a besoin de lire les canaux VIP auxquels vous êtes abonné.")
                print(" La console va vous demander :")
                print(" 1. Votre numéro de téléphone (avec l'indicatif, ex: +33612345678)")
                print(" 2. Le code à 5 chiffres que Telegram va vous envoyer sur votre app.")
                print(" (Note : Quand vous tapez le code, il s'affiche, c'est normal).")
                print("═"*60 + "\n")

            # On démarre le client Telegram (ne redemande le numéro/code que la 1ère fois)
            client.start()
            
            # On crée l'Event Loop asynchrone
            loop = asyncio.get_event_loop()
            
            # 1. On lance le bot de trading dans l'Event Loop
            loop.create_task(run_bot_safe())
            
            # On maintient la boucle asyncio en vie via le client Telegram (Telethon)
            logger.info("🔄 Boucle principale active (Telegram + Firebase).")
            # (#80) run_until_disconnected() renvoie None quand le client est DÉJÀ déconnecté,
            # et run_until_complete(None) lève "An asyncio.Future, a coroutine or an awaitable
            # is required" -- remontée en "Crash Fatal Worker". C'est ce qui se passait à chaque
            # Ctrl+C : l'arrêt volontaire produisait un CRITICAL trompeur, et le watchdog
            # relançait le worker au lieu de le laisser s'arrêter (observé le 2026-07-29).
            _waiter = client.run_until_disconnected()
            if _waiter is not None:
                loop.run_until_complete(_waiter)
            else:
                logger.info("ℹ️ Client Telegram déjà déconnecté — arrêt propre.")
            
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