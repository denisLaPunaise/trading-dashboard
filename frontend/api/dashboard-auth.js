/* global process */
// Fonction serverless Vercel — génère un token Firebase pour le dashboard.
//
// Flux :
//   1. Le frontend envoie l'initData signé par Telegram
//   2. On lit le telegram_id (non vérifié) pour localiser le bot_token du client
//   3. On valide la signature HMAC de l'initData avec ce bot_token
//   4. Si valide → on génère un custom token Firebase avec uid = telegram_id
//
// La clé service account vit en variable d'environnement Vercel (jamais chez le client).

import { initializeApp, getApps, cert } from 'firebase-admin/app';
import { getFirestore } from 'firebase-admin/firestore';
import { getAuth } from 'firebase-admin/auth';
import crypto from 'crypto';

// --- Initialisation unique de l'Admin SDK (réutilisée entre invocations) ---
function ensureAdmin() {
  if (getApps().length) return true;
  const raw = process.env.FIREBASE_SERVICE_ACCOUNT;
  if (!raw) {
    console.error('FIREBASE_SERVICE_ACCOUNT manquant dans les variables Vercel.');
    return false;
  }
  initializeApp({ credential: cert(JSON.parse(raw)) });
  return true;
}

// --- Validation de la signature Telegram WebApp ---
// Retourne le telegram_id (string) si la signature est valide, sinon null.
function validateInitData(initData, botToken) {
  const params = new URLSearchParams(initData);
  const hash = params.get('hash');
  if (!hash) return null;
  params.delete('hash');

  // Anti-replay : initData de plus de 24h refusé
  const authDate = parseInt(params.get('auth_date') || '0', 10);
  if (!authDate || Date.now() / 1000 - authDate > 86400) return null;

  // data_check_string = clés triées, "k=v" jointes par \n
  const dataCheckString = [...params.entries()]
    .sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0))
    .map(([k, v]) => `${k}=${v}`)
    .join('\n');

  const secretKey = crypto.createHmac('sha256', 'WebAppData').update(botToken).digest();
  const computed = crypto.createHmac('sha256', secretKey).update(dataCheckString).digest('hex');
  if (computed !== hash) return null;

  try {
    const user = JSON.parse(params.get('user') || '{}');
    return user.id ? String(user.id) : null;
  } catch {
    return null;
  }
}

export default async function handler(req, res) {
  if (req.method !== 'POST') {
    return res.status(405).json({ error: 'Méthode non autorisée' });
  }
  if (!ensureAdmin()) {
    return res.status(500).json({ error: 'Serveur mal configuré (clé manquante).' });
  }

  try {
    const initData = req.body?.initData;
    if (!initData) return res.status(400).json({ error: 'initData manquant' });

    // 1. telegram_id non vérifié, juste pour localiser le bon bot_token
    let telegramId = null;
    try {
      const probe = new URLSearchParams(initData);
      telegramId = String(JSON.parse(probe.get('user') || '{}').id || '') || null;
    } catch {
      telegramId = null;
    }
    if (!telegramId) return res.status(400).json({ error: 'Utilisateur introuvable' });

    // 2. Récupère le bot_token du client (doc privé, lisible Admin SDK uniquement)
    const snap = await getFirestore().doc(`users/${telegramId}/private/config`).get();
    if (!snap.exists) return res.status(403).json({ error: 'Bot non enregistré' });
    const botToken = snap.data().bot_token;
    if (!botToken) return res.status(403).json({ error: 'bot_token absent' });

    // 3. Valide la signature avec ce bot_token
    const verifiedId = validateInitData(initData, botToken);
    if (!verifiedId || verifiedId !== telegramId) {
      return res.status(401).json({ error: 'Signature invalide' });
    }

    // 4. Génère le custom token Firebase (uid = telegram_id)
    const token = await getAuth().createCustomToken(telegramId);
    return res.status(200).json({ token });
  } catch (e) {
    console.error('dashboard-auth error:', e);
    return res.status(500).json({ error: 'Erreur serveur' });
  }
}
