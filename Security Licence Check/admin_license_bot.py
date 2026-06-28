import asyncio
import re
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from telethon import TelegramClient, events

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

# Configuration des colonnes (A=1, B=2, C=3, etc.)
# Vérifie dans ton Sheet où se trouvent ces infos
COL_CLE_LICENCE = 2  # Colonne B
COL_HWID = 5         # Colonne E (Si c'est là que tu veux stocker l'ID)

# ==========================================
# 🔌 CONNEXION GOOGLE SHEET
# ==========================================
scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
creds = ServiceAccountCredentials.from_json_keyfile_name("service_account.json", scope)
client_google = gspread.authorize(creds)

def update_sheet(license_key, pc_id):
    try:
        # Reconnexion au sheet à chaque fois pour éviter les timeouts
        sheet = client_google.open(SHEET_NAME).sheet1
        
        # 1. On cherche la ligne qui contient la clé de licence
        # find retourne une cellule (ex: Row 10, Col 1)
        cell = sheet.find(license_key)
        
        if cell:
            print(f"✅ Clé trouvée ligne {cell.row}")
            
            # 2. On récupère la valeur actuelle de la case HWID
            current_hwid = sheet.cell(cell.row, COL_HWID).value
            
            # 3. Logique de remplissage
            if not current_hwid or current_hwid == "":
                # Si vide, on écrit le nouveau HWID (Première connexion !)
                sheet.update_cell(cell.row, COL_HWID, pc_id)
                print(f"💾 SUCCÈS : HWID {pc_id} enregistré pour {license_key}")
                return "NEW_REGISTER"
            
            elif current_hwid == pc_id:
                print("ℹ️ Déjà enregistré (Même PC).")
                return "SAME"
            
            else:
                print(f"⚠️ ATTENTION : Tentative avec un autre ID ! (Attendu: {current_hwid}, Reçu: {pc_id})")
                return "CONFLICT"
        else:
            print(f"❌ Clé {license_key} introuvable dans le Sheet.")
            return "NOT_FOUND"

    except Exception as e:
        print(f"❌ Erreur Google Sheet : {e}")
        return "ERROR"

# ==========================================
# 👂 ÉCOUTE TELEGRAM
# ==========================================
client_tg = TelegramClient('admin_session', API_ID, API_HASH)

@client_tg.on(events.NewMessage(chats=SECURITY_CHANNEL_ID))
async def handler(event):
    msg = event.raw_text
    
    # Debug : On affiche ce que le bot voit vraiment pour comprendre
    # print(f"DEBUG REÇU:\n{msg}\n---") 

    # On filtre uniquement les messages de succès
    if "CONNEXION RÉUSSIE" in msg:
        print("\n📨 Nouveau login détecté...")
        
        # --- CORRECTION REGEX ---
        # On cherche : Le Label + Espaces + (Optionnel `) + LE TEXTE + (Optionnel `)
        # ([^`\n]+) signifie : "Prend tout ce qui n'est PAS un backtick ou un retour à la ligne"
        
        key_match = re.search(r"🔑 Clé:\s*[`]?([^`\n]+)[`]?", msg)
        hwid_match = re.search(r"💻 PC ID:\s*[`]?([^`\n]+)[`]?", msg)
        
        if key_match and hwid_match:
            licence = key_match.group(1).strip()
            hwid = hwid_match.group(1).strip()
            
            print(f"🔍 Traitement : {licence} | ID: {hwid}")
            
            # Mise à jour du Sheet (Fonction bloquante, on l'exécute)
            result = update_sheet(licence, hwid)
            
            # Optionnel : Le bot admin peut répondre sur Telegram pour confirmer
            if result == "NEW_REGISTER":
                await event.reply(f"🔒 **LICENCE VERROUILLÉE**\nAppareil lié automatiquement au Google Sheet.")
            elif result == "CONFLICT":
                await event.reply(f"🚨 **ALERTE VOL**\nCet appareil ne correspond pas à celui enregistré dans le Sheet !")
            elif result == "NOT_FOUND":
                print("❌ Clé introuvable dans le fichier Excel.")
        
        else:
            print("⚠️ Impossible de lire la clé ou l'ID (Format Regex non reconnu).")
            # Si ça échoue encore, décommente la ligne ci-dessous pour voir le souci :
            # print(f"Contenu brut reçu : {repr(msg)}")
            
async def main():
    print("🤖 ADMIN BOT : En écoute des nouvelles connexions...")
    print("📝 Prêt à écrire dans le Google Sheet.")
    await client_tg.run_until_disconnected()

if __name__ == '__main__':
    client_tg.start()
    client_tg.loop.run_until_complete(main())