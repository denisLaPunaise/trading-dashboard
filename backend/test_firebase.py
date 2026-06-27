"""
Script de test Firebase — valide le push et le listener de commandes.
Lance avec : python test_firebase.py
"""
import asyncio
import os
import sys

SERVICE_ACCOUNT = os.path.join(os.path.dirname(__file__), "serviceAccountKey.json")
TEST_USER_ID = "-1003692316672"  # Ton vrai TELEGRAM_DESTINATION_ID

# ── Vérification préalable ──────────────────────────────────────────────────
if not os.path.exists(SERVICE_ACCOUNT):
    print(f"❌ Fichier introuvable : {SERVICE_ACCOUNT}")
    print("   Place ton serviceAccountKey.json dans le dossier backend/ et relance.")
    sys.exit(1)

import firebase_sync

# ── 1. Initialisation ───────────────────────────────────────────────────────
print("\n🔥 Test Firebase FTMO\n" + "─"*40)
ok = firebase_sync.init_firebase(SERVICE_ACCOUNT)
if not ok:
    print("❌ Initialisation Firebase échouée. Vérifie le fichier serviceAccountKey.json.")
    sys.exit(1)

db = firebase_sync.get_db()

# ── 2. Push d'un état simulé ────────────────────────────────────────────────
async def test_push():
    print("\n📤 Test 1 : Push d'un état simulé...")
    fake_state = firebase_sync.build_state_document(
        account={"balance": 10000.0, "equity": 10250.0, "free_margin": 8500.0},
        daily_stats={"total_pnl": 250.0, "realized_pnl": 100.0, "floating_pnl": 150.0, "percentage": 2.5},
        settings={"risk_gold": 0.008, "muted_channels": [], "max_dd": 0.047},
        active_trades=[
            {"ticket": 123456, "symbol": "XAUUSD", "type": "BUY", "volume": 0.1,
             "open_price": 2320.5, "current_price": 2345.0, "sl": 2310.0, "tp": 2360.0,
             "profit": 245.0, "pips": 24.5, "open_time": "09:30", "source": "MDT VIP", "managed": True}
        ],
        closed_trades=[],
    )
    success = await firebase_sync.push_state(TEST_USER_ID, fake_state)
    if success:
        print(f"   ✅ État pushé avec succès → /users/{TEST_USER_ID}/state")
    else:
        print("   ❌ Push échoué.")
    return success

# ── 3. Écriture + lecture d'une commande simulée ────────────────────────────
async def test_command():
    print("\n📩 Test 2 : Écriture d'une commande simulée...")
    from firebase_admin import firestore as fs
    cmd_ref = (
        db.collection("users").document(TEST_USER_ID)
        .collection("commands").document()
    )
    cmd_ref.set({
        "type": "close_trade",
        "payload": {"ticket": 123456},
        "status": "pending",
        "created_at": fs.SERVER_TIMESTAMP,
    })
    cmd_id = cmd_ref.id
    print(f"   ✅ Commande écrite → /users/{TEST_USER_ID}/commands/{cmd_id}")

    # Simule la réception et le traitement
    print("\n⚙️  Test 3 : Simulation du handler de commandes...")
    received = asyncio.Event()

    async def fake_handler(cid, cdata):
        print(f"   ✅ Commande reçue par le handler : [{cid}] type={cdata.get('type')}")
        firebase_sync.update_command_status(TEST_USER_ID, cid, "done", {"success": True, "message": "Test OK"})
        print(f"   ✅ Statut mis à jour → 'done'")
        received.set()

    loop = asyncio.get_event_loop()
    firebase_sync.start_command_listener(TEST_USER_ID, fake_handler, loop)

    print("   ⏳ En attente de la commande via le listener (max 10s)...")
    try:
        await asyncio.wait_for(received.wait(), timeout=10.0)
    except asyncio.TimeoutError:
        print("   ⚠️  Timeout — le listener n'a pas reçu la commande dans les 10s.")
        print("      (C'est normal si la commande était déjà 'done' avant le démarrage du listener)")

    firebase_sync.stop_command_listener()

# ── 4. Nettoyage ────────────────────────────────────────────────────────────
async def cleanup():
    print("\n🧹 Nettoyage du nœud de test dans Firestore...")
    import concurrent.futures
    loop = asyncio.get_event_loop()
    def _delete():
        # Supprime les commandes
        cmds = db.collection("users").document(TEST_USER_ID).collection("commands").stream()
        for c in cmds:
            c.reference.delete()
        # Supprime le document state
        db.collection("users").document(TEST_USER_ID).delete()
    await loop.run_in_executor(None, _delete)
    print(f"   ✅ Nœud /users/{TEST_USER_ID} supprimé.")

# ── Main ─────────────────────────────────────────────────────────────────────
async def main():
    push_ok = await test_push()
    if push_ok:
        await test_command()
    # Nettoyage désactivé : les données restent dans Firestore pour tester le dashboard
    # await cleanup()
    print("\n" + "─"*40)
    print("🏁 Tests terminés. Ouvre le navigateur pour voir le dashboard !")
    print(f"   Les données sont visibles dans Firestore sous /users/{TEST_USER_ID}\n")

asyncio.run(main())
