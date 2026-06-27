"""
Module de synchronisation Firebase pour le bot FTMO.
- Push périodique de l'état vers Firestore (/users/{user_id}/state)
- Listener temps réel sur les commandes (/users/{user_id}/commands/)
- Résilience réseau : retry avec backoff exponentiel, buffer mémoire
"""

import asyncio
import logging
import time
import os
from datetime import datetime, timezone
from typing import Callable, Any

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────
# Initialisation Firebase Admin SDK
# ──────────────────────────────────────────────
_firebase_initialized = False
_db = None

def init_firebase(service_account_path: str) -> bool:
    global _firebase_initialized, _db
    if _firebase_initialized:
        return True
    try:
        import firebase_admin
        from firebase_admin import credentials, firestore
        if not firebase_admin._apps:
            cred = credentials.Certificate(service_account_path)
            firebase_admin.initialize_app(cred)
        _db = firestore.client()
        _firebase_initialized = True
        logger.info("✅ Firebase initialisé avec succès.")
        return True
    except Exception as e:
        logger.error(f"❌ Échec initialisation Firebase : {e}")
        return False


def get_db():
    return _db


# ──────────────────────────────────────────────
# Serialisation des données (Firestore n'accepte pas None ni certains types)
# ──────────────────────────────────────────────
def _safe(val: Any) -> Any:
    if val is None:
        return 0
    if isinstance(val, float) and (val != val):  # NaN
        return 0.0
    if isinstance(val, (list, tuple)):
        return [_safe(x) for x in val]
    if isinstance(val, dict):
        return {k: _safe(v) for k, v in val.items()}
    return val


def build_state_document(
    account: dict,
    daily_stats: dict,
    settings: dict,
    active_trades: list,
    closed_trades: list,
    extra: dict = None,
) -> dict:
    """Construit le document Firestore à partir des données brutes du bot."""
    from firebase_admin import firestore as fs
    doc = {
        "account": _safe(account),
        "daily_stats": _safe(daily_stats),
        "settings": _safe(settings),
        "active_trades": _safe(active_trades),
        "closed_trades": _safe(closed_trades),
        "last_push": fs.SERVER_TIMESTAMP,
        "bot_online": True,
    }
    if extra:
        doc.update(_safe(extra))
    return doc


# ──────────────────────────────────────────────
# Push de l'état avec retry + backoff
# ──────────────────────────────────────────────
_PUSH_RETRY_DELAYS = [1, 2, 4, 8, 16, 30]  # secondes


async def push_state(user_id: str, state_doc: dict, executor=None) -> bool:
    """
    Pousse l'état vers /users/{user_id}/state.
    Retry automatique avec backoff exponentiel si la connexion échoue.
    Retourne True si succès, False si tous les retries ont échoué.
    """
    if not _firebase_initialized or _db is None:
        logger.warning("Firebase non initialisé, push ignoré.")
        return False

    import concurrent.futures
    loop = asyncio.get_event_loop()

    def _do_push():
        ref = _db.collection("users").document(str(user_id))
        ref.set(state_doc, merge=True)

    for attempt, delay in enumerate(_PUSH_RETRY_DELAYS + [None]):
        try:
            if executor:
                await loop.run_in_executor(executor, _do_push)
            else:
                await loop.run_in_executor(None, _do_push)
            if attempt > 0:
                logger.info(f"✅ Push Firebase réussi après {attempt} retry(s).")
            return True
        except Exception as e:
            if delay is None:
                logger.error(f"❌ Push Firebase abandonné après tous les retries : {e}")
                return False
            logger.warning(f"⚠️ Push Firebase échoué (tentative {attempt + 1}) : {e}. Retry dans {delay}s.")
            await asyncio.sleep(delay)

    return False


async def mark_bot_offline(user_id: str, executor=None) -> None:
    """Marque le bot comme hors-ligne dans Firestore (appelé à l'arrêt propre)."""
    if not _firebase_initialized or _db is None:
        return
    from firebase_admin import firestore as fs
    import concurrent.futures
    loop = asyncio.get_event_loop()

    def _do_offline():
        ref = _db.collection("users").document(str(user_id))
        ref.set({"bot_online": False, "last_push": fs.SERVER_TIMESTAMP}, merge=True)

    try:
        if executor:
            await loop.run_in_executor(executor, _do_offline)
        else:
            await loop.run_in_executor(None, _do_offline)
        logger.info("🔴 Bot marqué hors-ligne dans Firebase.")
    except Exception as e:
        logger.warning(f"Impossible de marquer le bot hors-ligne : {e}")


# ──────────────────────────────────────────────
# Listener de commandes
# ──────────────────────────────────────────────
_command_listener_handle = None


def start_command_listener(user_id: str, command_handler: Callable, loop: asyncio.AbstractEventLoop) -> None:
    """
    Démarre un listener Firestore sur /users/{user_id}/commands/ où status == "pending".
    command_handler est une coroutine async def handler(cmd_id, cmd_data).
    """
    global _command_listener_handle
    if not _firebase_initialized or _db is None:
        logger.warning("Firebase non initialisé, listener non démarré.")
        return

    from google.cloud.firestore_v1 import Watch

    from google.cloud.firestore_v1.base_query import FieldFilter
    ref = (
        _db.collection("users")
        .document(str(user_id))
        .collection("commands")
        .where(filter=FieldFilter("status", "==", "pending"))
    )

    def on_snapshot(col_snapshot, changes, read_time):
        from google.cloud.firestore_v1.watch import ChangeType
        for change in changes:
            if change.type.name == "ADDED":
                cmd_id = change.document.id
                cmd_data = change.document.to_dict()
                logger.info(f"📩 Commande reçue [{cmd_id}] : {cmd_data.get('type')}")
                asyncio.run_coroutine_threadsafe(
                    command_handler(cmd_id, cmd_data), loop
                )

    _command_listener_handle = ref.on_snapshot(on_snapshot)
    logger.info(f"👂 Listener commandes démarré pour user {user_id}.")


def stop_command_listener() -> None:
    global _command_listener_handle
    if _command_listener_handle:
        _command_listener_handle.unsubscribe()
        _command_listener_handle = None
        logger.info("🔇 Listener commandes arrêté.")


# ──────────────────────────────────────────────
# Mise à jour du statut d'une commande
# ──────────────────────────────────────────────
def update_command_status(user_id: str, cmd_id: str, status: str, result: dict = None) -> None:
    """
    Appelé depuis le handler de commandes (dans un thread executor).
    status : "executing" | "done" | "error"
    """
    if not _firebase_initialized or _db is None:
        return
    from firebase_admin import firestore as fs
    try:
        ref = (
            _db.collection("users")
            .document(str(user_id))
            .collection("commands")
            .document(cmd_id)
        )
        data = {"status": status, "executed_at": fs.SERVER_TIMESTAMP}
        if result is not None:
            data["result"] = _safe(result)
        ref.update(data)
    except Exception as e:
        logger.error(f"Impossible de mettre à jour le statut de la commande {cmd_id} : {e}")


# ──────────────────────────────────────────────
# Boucle de push périodique
# ──────────────────────────────────────────────
async def firebase_push_loop(
    user_id: str,
    get_state_fn: Callable,
    interval_seconds: float = 4.0,
    executor=None,
) -> None:
    """
    Boucle asyncio qui appelle get_state_fn() toutes les interval_seconds secondes
    et pousse le résultat vers Firestore.
    get_state_fn doit être une coroutine async retournant un dict (build_state_document).
    """
    logger.info(f"🔄 Boucle de push Firebase démarrée (intervalle : {interval_seconds}s).")
    consecutive_failures = 0

    while True:
        try:
            state_doc = await get_state_fn()
            if state_doc:
                success = await push_state(user_id, state_doc, executor)
                if success:
                    consecutive_failures = 0
                else:
                    consecutive_failures += 1
                    logger.warning(f"⚠️ {consecutive_failures} push(es) consécutif(s) échoué(s).")
        except asyncio.CancelledError:
            logger.info("🛑 Boucle de push Firebase annulée.")
            break
        except Exception as e:
            logger.error(f"Erreur inattendue dans la boucle de push : {e}")
            consecutive_failures += 1

        await asyncio.sleep(interval_seconds)
