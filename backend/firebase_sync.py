"""
Module de synchronisation Firebase — V2 (Auth email/password, pas de serviceAccountKey.json)
- Authentification via Firebase Auth REST API (email + password)
- Push Firestore via REST API avec updateMask (merge safe)
- Listener commandes via polling toutes les 3s
- Refresh automatique du token toutes les 55 minutes
"""

import asyncio
import hashlib
import json
import logging
import math
import time
import threading
import requests
from datetime import datetime, timezone
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

# ── Config Firebase (publique — déjà dans le frontend) ───────────────────────
FIREBASE_API_KEY    = "AIzaSyDqyuWknI1fm-dV8bY_o6azfamm3XN3PKw"
FIREBASE_PROJECT_ID = "ftmo-trading-bot-59dae"
_FS_BASE  = f"https://firestore.googleapis.com/v1/projects/{FIREBASE_PROJECT_ID}/databases/(default)/documents"
_AUTH_URL = f"https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword?key={FIREBASE_API_KEY}"
_REFR_URL = f"https://securetoken.googleapis.com/v1/token?key={FIREBASE_API_KEY}"

# ── État interne ─────────────────────────────────────────────────────────────
_firebase_initialized: bool    = False
_id_token:             Optional[str] = None
_refresh_token_val:    Optional[str] = None
_token_expiry:         float   = 0.0
_email:                Optional[str] = None
_password:             Optional[str] = None
_listener_running:     bool    = False
_PUSH_RETRY_DELAYS = [1, 2, 4, 8, 16, 30]


# ════════════════════════════════════════════════════════════════════════════
# INITIALISATION
# ════════════════════════════════════════════════════════════════════════════

def init_firebase(email: str, password: str) -> bool:
    global _firebase_initialized, _email, _password
    _email    = email
    _password = password
    if _signin():
        _firebase_initialized = True
        logger.info("✅ Firebase Auth initialisé (email/password).")
        return True
    logger.error("❌ Échec authentification Firebase.")
    return False


def get_db():
    """Compatibilité avec l'ancien code — non utilisé en V2."""
    return None


def publish_client_metadata(user_id: str, bot_token: str, license_key: str) -> None:
    """Publie les métadonnées nécessaires à l'authentification du dashboard (Phase 1).

    - telegram_id dans /licenses/{key}        → permet aux règles Firestore de lier la licence au user
    - bot_token   dans /users/{id}/private/config → permet au serveur Vercel de valider la signature Telegram

    L'ordre importe : on écrit telegram_id AVANT bot_token, car la règle du doc privé
    vérifie que license.telegram_id == user_id.
    """
    if not _firebase_initialized:
        return
    ok = True
    try:
        token = _get_token()
        if not token:
            return
        headers = {"Authorization": f"Bearer {token}"}

        # 1. telegram_id dans la licence (le bot ne peut écrire QUE ce champ sur SA licence)
        if license_key:
            url1 = f"{_FS_BASE}/licenses/{license_key}?updateMask.fieldPaths=telegram_id"
            r1 = requests.patch(url1, headers=headers, json=_doc({"telegram_id": user_id}), timeout=10)
            if r1.status_code not in (200, 201):
                ok = False
                logger.warning(f"⚠️ publish telegram_id: {r1.status_code} {r1.text[:120]}")

        # 2. bot_token dans le doc privé (lisible par l'Admin SDK serveur uniquement)
        if bot_token:
            url2 = f"{_FS_BASE}/users/{user_id}/private/config?updateMask.fieldPaths=bot_token"
            r2 = requests.patch(url2, headers=headers, json=_doc({"bot_token": bot_token}), timeout=10)
            if r2.status_code not in (200, 201):
                ok = False
                logger.warning(f"⚠️ publish bot_token: {r2.status_code} {r2.text[:120]}")

        if ok:
            logger.info("📤 Métadonnées client publiées (telegram_id + bot_token).")
        else:
            logger.warning("⚠️ Publication métadonnées INCOMPLÈTE — déploie les règles Firestore strictes (Phase 1).")
    except Exception as e:
        logger.error(f"publish_client_metadata error: {e}")


# ════════════════════════════════════════════════════════════════════════════
# GESTION DU TOKEN
# ════════════════════════════════════════════════════════════════════════════

def _signin() -> bool:
    global _id_token, _refresh_token_val, _token_expiry
    try:
        r = requests.post(_AUTH_URL, json={
            "email": _email, "password": _password, "returnSecureToken": True
        }, timeout=10)
        if r.status_code == 200:
            d = r.json()
            _id_token          = d["idToken"]
            _refresh_token_val = d["refreshToken"]
            _token_expiry      = time.time() + int(d["expiresIn"]) - 60
            return True
        logger.error(f"❌ Firebase signin {r.status_code}: {r.json().get('error', {}).get('message', r.text)}")
        return False
    except Exception as e:
        logger.error(f"❌ Firebase signin exception: {e}")
        return False


def _refresh() -> bool:
    global _id_token, _refresh_token_val, _token_expiry
    if not _refresh_token_val:
        return _signin()
    try:
        r = requests.post(_REFR_URL, json={
            "grant_type": "refresh_token", "refresh_token": _refresh_token_val
        }, timeout=10)
        if r.status_code == 200:
            d = r.json()
            _id_token          = d["id_token"]
            _refresh_token_val = d["refresh_token"]
            _token_expiry      = time.time() + int(d["expires_in"]) - 60
            return True
        return _signin()
    except:
        return _signin()


def _get_token() -> Optional[str]:
    if time.time() > _token_expiry:
        _refresh()
    return _id_token


# ════════════════════════════════════════════════════════════════════════════
# SÉRIALISATION Python ↔ Firestore REST
# ════════════════════════════════════════════════════════════════════════════

def _safe(val: Any) -> Any:
    if val is None:
        return 0
    if isinstance(val, float) and (val != val):
        return 0.0
    if isinstance(val, (list, tuple)):
        return [_safe(x) for x in val]
    if isinstance(val, dict):
        return {k: _safe(v) for k, v in val.items()}
    return val


def _to_fs(val: Any) -> dict:
    if val is None:
        return {"nullValue": None}
    if isinstance(val, bool):
        return {"booleanValue": val}
    if isinstance(val, int):
        return {"integerValue": str(val)}
    if isinstance(val, float):
        return {"doubleValue": 0.0 if (math.isnan(val) or math.isinf(val)) else val}
    if isinstance(val, str):
        return {"stringValue": val}
    if isinstance(val, dict):
        return {"mapValue": {"fields": {k: _to_fs(v) for k, v in val.items()}}}
    if isinstance(val, (list, tuple)):
        return {"arrayValue": {"values": [_to_fs(x) for x in val]}}
    return {"stringValue": str(val)}


def _doc(data: dict) -> dict:
    return {"fields": {k: _to_fs(v) for k, v in data.items()}}


def _from_fs(v: dict) -> Any:
    if "stringValue"  in v: return v["stringValue"]
    if "integerValue" in v: return int(v["integerValue"])
    if "doubleValue"  in v: return float(v["doubleValue"])
    if "booleanValue" in v: return v["booleanValue"]
    if "nullValue"    in v: return None
    if "mapValue"     in v: return {k: _from_fs(fv) for k, fv in v["mapValue"].get("fields", {}).items()}
    if "arrayValue"   in v: return [_from_fs(av) for av in v["arrayValue"].get("values", [])]
    return None


def _from_doc(fields: dict) -> dict:
    return {k: _from_fs(v) for k, v in fields.items()}


# ════════════════════════════════════════════════════════════════════════════
# CONSTRUCTION DU DOCUMENT D'ÉTAT
# ════════════════════════════════════════════════════════════════════════════

def build_state_document(account, daily_stats, settings, active_trades, closed_trades, extra=None) -> dict:
    doc = {
        "account":       _safe(account),
        "daily_stats":   _safe(daily_stats),
        "settings":      _safe(settings),
        "active_trades": _safe(active_trades),
        "closed_trades": _safe(closed_trades),
        "bot_online":    True,
        "last_push":     datetime.now(timezone.utc).isoformat(),
    }
    if extra:
        doc.update(_safe(extra))
    return doc


# ════════════════════════════════════════════════════════════════════════════
# PUSH D'ÉTAT
# ════════════════════════════════════════════════════════════════════════════

async def push_state(user_id: str, state_doc: dict, executor=None) -> bool:
    if not _firebase_initialized:
        return False

    loop = asyncio.get_event_loop()

    def _do():
        token = _get_token()
        if not token:
            raise Exception("Token indisponible")
        mask = "&".join(f"updateMask.fieldPaths={k}" for k in state_doc.keys())
        url  = f"{_FS_BASE}/users/{user_id}?{mask}"
        r = requests.patch(url, headers={"Authorization": f"Bearer {token}"},
                           json=_doc(state_doc), timeout=10)
        if r.status_code not in (200, 201):
            raise Exception(f"PATCH {r.status_code}: {r.text[:200]}")

    for attempt, delay in enumerate(_PUSH_RETRY_DELAYS + [None]):
        try:
            await loop.run_in_executor(executor, _do)
            return True
        except Exception as e:
            if delay is None:
                logger.error(f"❌ Push Firebase abandonné: {e}")
                return False
            logger.warning(f"⚠️ Push Firebase ({attempt+1}): {e}. Retry dans {delay}s.")
            await asyncio.sleep(delay)
    return False


# ════════════════════════════════════════════════════════════════════════════
# STATUT COMMANDE
# ════════════════════════════════════════════════════════════════════════════

def update_command_status(user_id: str, cmd_id: str, status: str, result: dict = None) -> None:
    if not _firebase_initialized:
        return
    try:
        token  = _get_token()
        fields = {"status": status}
        if result:
            fields["result"] = _safe(result)
        mask = "&".join(f"updateMask.fieldPaths={k}" for k in fields.keys())
        url  = f"{_FS_BASE}/users/{user_id}/commands/{cmd_id}?{mask}"
        requests.patch(url, headers={"Authorization": f"Bearer {token}"},
                       json=_doc(fields), timeout=5)
    except Exception as e:
        logger.error(f"update_command_status error: {e}")


# ════════════════════════════════════════════════════════════════════════════
# LISTENER COMMANDES (polling 3s)
# ════════════════════════════════════════════════════════════════════════════

def start_command_listener(user_id: str, command_handler: Callable, loop: asyncio.AbstractEventLoop) -> None:
    global _listener_running
    if not _firebase_initialized:
        logger.warning("Firebase non initialisé, listener non démarré.")
        return

    _listener_running = True

    def _poll():
        seen_ids: set = set()
        while _listener_running:
            try:
                token = _get_token()
                url   = f"{_FS_BASE}/users/{user_id}:runQuery"
                body  = {"structuredQuery": {
                    "from":  [{"collectionId": "commands"}],
                    "where": {"fieldFilter": {
                        "field": {"fieldPath": "status"},
                        "op":    "EQUAL",
                        "value": {"stringValue": "pending"}
                    }}
                }}
                r = requests.post(url, headers={"Authorization": f"Bearer {token}"},
                                  json=body, timeout=5)
                if r.status_code == 200:
                    for item in r.json():
                        doc = item.get("document")
                        if not doc:
                            continue
                        doc_id = doc["name"].split("/")[-1]
                        if doc_id in seen_ids:
                            continue
                        fields = doc.get("fields", {})
                        if fields.get("status", {}).get("stringValue") == "pending":
                            seen_ids.add(doc_id)
                            cmd_data = _from_doc(fields)
                            logger.info(f"📩 Commande reçue [{doc_id}] : {cmd_data.get('type')}")
                            asyncio.run_coroutine_threadsafe(
                                command_handler(doc_id, cmd_data), loop
                            )
            except Exception as e:
                logger.debug(f"Listener poll error: {e}")
            time.sleep(3)

    t = threading.Thread(target=_poll, daemon=True)
    t.start()
    logger.info(f"👂 Listener commandes démarré (polling 3s) pour user {user_id}.")


def stop_command_listener() -> None:
    global _listener_running
    _listener_running = False
    logger.info("🔇 Listener commandes arrêté.")


# ════════════════════════════════════════════════════════════════════════════
# BOT OFFLINE / PUSH LOOP
# ════════════════════════════════════════════════════════════════════════════

async def mark_bot_offline(user_id: str, executor=None) -> None:
    if not _firebase_initialized:
        return
    loop = asyncio.get_event_loop()
    def _do():
        token = _get_token()
        url   = f"{_FS_BASE}/users/{user_id}?updateMask.fieldPaths=bot_online&updateMask.fieldPaths=last_push"
        requests.patch(url, headers={"Authorization": f"Bearer {token}"},
                       json=_doc({"bot_online": False,
                                  "last_push":  datetime.now(timezone.utc).isoformat()}),
                       timeout=5)
    try:
        await loop.run_in_executor(executor, _do)
        logger.info("🔴 Bot marqué hors-ligne dans Firebase.")
    except Exception as e:
        logger.warning(f"mark_bot_offline error: {e}")


async def firebase_push_loop(user_id: str, get_state_fn: Callable,
                              interval_seconds: float = 4.0, executor=None,
                              heartbeat_seconds: float = 30.0) -> None:
    """Pousse l'état dans Firestore.

    Optimisation coût/quota (Phase 2) : on vérifie l'état toutes les `interval_seconds`
    (gratuit, local) mais on n'ÉCRIT dans Firestore que si le contenu a réellement changé,
    avec un battement de cœur forcé au moins toutes les `heartbeat_seconds` (garde
    bot_online / last_push à jour). Réduit fortement les écritures quand le bot est au repos.
    """
    logger.info(f"🔄 Boucle push Firebase démarrée (check {interval_seconds}s, heartbeat {heartbeat_seconds}s).")
    consecutive_failures = 0
    last_sig = None
    last_push_ts = 0.0
    while True:
        try:
            state_doc = await get_state_fn()
            if state_doc:
                # Signature du contenu (on ignore last_push qui change à chaque cycle)
                sig_src = {k: v for k, v in state_doc.items() if k != "last_push"}
                sig = hashlib.md5(
                    json.dumps(sig_src, sort_keys=True, default=str).encode("utf-8")
                ).hexdigest()
                now = time.time()
                if sig != last_sig or (now - last_push_ts) >= heartbeat_seconds:
                    ok = await push_state(user_id, state_doc, executor)
                    if ok:
                        last_sig = sig
                        last_push_ts = now
                    consecutive_failures = 0 if ok else consecutive_failures + 1
                    if consecutive_failures > 3:
                        logger.warning(f"⚠️ {consecutive_failures} push(es) échoué(s) consécutifs.")
        except asyncio.CancelledError:
            logger.info("🛑 Boucle push Firebase annulée.")
            break
        except Exception as e:
            logger.error(f"Erreur boucle push: {e}")
            consecutive_failures += 1
        await asyncio.sleep(interval_seconds)
