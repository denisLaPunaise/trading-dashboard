import { useState, useEffect, useRef, useCallback } from 'react'
import { Activity, Wallet, TrendingUp, TrendingDown, Settings, XCircle, Shield, ChevronDown, ChevronUp, Briefcase, AlertTriangle, Lock, Unlock, Power, BellOff, Target, Award, Crosshair, Calendar, MessageCircle } from 'lucide-react'
import './App.css'
import { db, auth } from './firebase'
import { doc, collection, onSnapshot, addDoc, writeBatch, serverTimestamp, Timestamp, query, where, orderBy } from 'firebase/firestore'
import { signInAnonymously, signInWithCustomToken } from 'firebase/auth'

// (#12) TTL Firestore sur users/{uid}/commands : la politique pointe sur ce champ pour purger après 3 jours
const commandExpiry = () => Timestamp.fromMillis(Date.now() + 3 * 24 * 60 * 60 * 1000)

// (#16) day_factor_0..6 = Lundi..Dimanche
const FULL_DAY_NAMES = ['Lundi', 'Mardi', 'Mercredi', 'Jeudi', 'Vendredi', 'Samedi', 'Dimanche']

// (#31) Statuts mailbox : 'lu' (/vu) et 'corrige' (/corrige) sont mis à jour indépendamment
// de la réponse (/rep) côté bot de licence -- cf. admin_license_bot_V3.py.
const FEEDBACK_STATUS_BADGE = {
  corrige: { label: 'Corrigé ✓', color: '#00e676', bg: 'rgba(0,230,118,0.1)' },
  lu:      { label: 'Vu ✓',      color: '#64b5f6', bg: 'rgba(100,181,246,0.1)' },
  default: { label: 'Envoyé',    color: '#ffb74d', bg: 'rgba(255,183,77,0.1)' },
}

// Extrait l'ID Telegram de l'utilisateur depuis initData (signé par Telegram)
const getTelegramUserId = () => {
  try {
    const initData = window?.Telegram?.WebApp?.initData || '';
    if (initData) {
      const params = new URLSearchParams(initData);
      const user = JSON.parse(params.get('user') || '{}');
      if (user.id) return String(user.id);
    }
    // Fallback dev : utilise le vrai TELEGRAM_DESTINATION_ID du bot pour tester en navigateur
    if (import.meta.env.DEV) return import.meta.env.VITE_DEV_USER_ID || null;
    return null;
  } catch {
    return null;
  }
};

// --- COMPOSANT ODOMÈTRE (ÉTAPE 5) ---
const AnimatedNumber = ({ value, duration = 800, prefix = '' }) => {
  const [displayValue, setDisplayValue] = useState(value);
  const displayValueRef = useRef(value);

  useEffect(() => {
    displayValueRef.current = displayValue;
  }, [displayValue]);

  useEffect(() => {
    let startTimestamp = null;
    const startValue = displayValueRef.current;
    if (startValue === value) return;

    const step = (timestamp) => {
      if (!startTimestamp) startTimestamp = timestamp;
      const progress = Math.min((timestamp - startTimestamp) / duration, 1);
      const easeProgress = 1 - Math.pow(1 - progress, 4); // Effet de freinage en douceur
      setDisplayValue(startValue + (value - startValue) * easeProgress);

      if (progress < 1) window.requestAnimationFrame(step);
      else setDisplayValue(value);
    };
    window.requestAnimationFrame(step);
  }, [value, duration]);

  return <>{prefix}{displayValue.toLocaleString('fr-FR', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</>;
};

// (#65) Courbe d'évolution du portefeuille, sous les 4 cases de l'onglet Global.
// SVG écrit à la main plutôt qu'une librairie de graphiques : le bundle dépasse déjà 870 Ko et
// une seule courbe ne justifie pas une dépendance de plus. `points` vient du bot :
// [{d: "2026-08-13", v: 10236.69}, ...], balance réalisée reconstruite depuis l'historique MT5.
const EquityChart = ({ points, loading }) => {
  const [hover, setHover] = useState(null);

  if (loading) return null;
  if (!Array.isArray(points) || points.length < 2) {
    return (
      <div style={{ background: '#1e1e1e', borderRadius: '12px', border: '1px solid #333',
                    padding: '30px', textAlign: 'center', color: '#666', fontSize: '0.85rem' }}>
        Pas encore assez d'historique pour tracer une courbe sur cette période.
      </div>
    );
  }

  const values = points.map((p) => p.v);
  const min = Math.min(...values);
  const max = Math.max(...values);
  // Marge de 8 % en haut et en bas, et garde-fou sur une courbe parfaitement plate (max === min
  // donnerait une division par zéro, donc des coordonnées NaN et un tracé invisible).
  const span = (max - min) || Math.max(Math.abs(max) * 0.01, 1);
  const pad = span * 0.08;
  const lo = min - pad;
  const hi = max + pad;

  const W = 1000;
  const H = 260;
  const x = (i) => (i / (points.length - 1)) * W;
  const y = (v) => H - ((v - lo) / (hi - lo)) * H;

  const line = points.map((p, i) => `${i === 0 ? 'M' : 'L'}${x(i).toFixed(1)},${y(p.v).toFixed(1)}`).join(' ');
  const area = `${line} L${W},${H} L0,${H} Z`;

  const first = values[0];
  const last = values[values.length - 1];
  const delta = last - first;
  const pct = first !== 0 ? (delta / Math.abs(first)) * 100 : 0;
  const up = delta >= 0;
  const color = up ? '#00e676' : '#ff4444';
  const gradId = up ? 'eqUp' : 'eqDown';

  const eur = (v) => v.toLocaleString('fr-FR', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  const shortDate = (d) => {
    const [, m, day] = String(d).split('-');
    return day && m ? `${day}/${m}` : d;
  };

  // Le survol travaille en pourcentage de largeur : le SVG est étiré en CSS, donc les
  // coordonnées de l'événement ne correspondent pas au repère interne du viewBox.
  const onMove = (e) => {
    const r = e.currentTarget.getBoundingClientRect();
    const clientX = e.touches ? e.touches[0].clientX : e.clientX;
    const ratio = Math.min(Math.max((clientX - r.left) / r.width, 0), 1);
    setHover(Math.round(ratio * (points.length - 1)));
  };

  const hp = hover !== null ? points[hover] : null;

  return (
    <div style={{ background: '#1e1e1e', borderRadius: '12px', border: '1px solid #333', padding: '18px 16px 12px' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: '14px' }}>
        <div>
          <div style={{ color: '#888', fontSize: '0.75rem', textTransform: 'uppercase', letterSpacing: '1px' }}>
            Évolution du portefeuille
          </div>
          <div style={{ fontSize: '1.5rem', fontWeight: 'bold', color: '#fff', marginTop: '6px' }}>
            {eur(hp ? hp.v : last)} €
          </div>
        </div>
        <div style={{ textAlign: 'right' }}>
          <div style={{ color, fontWeight: 'bold', fontSize: '1rem' }}>
            {up ? '+' : ''}{eur(delta)} €
          </div>
          <div style={{ color, fontSize: '0.8rem' }}>
            {up ? '+' : ''}{pct.toFixed(2)} %
          </div>
        </div>
      </div>

      <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none"
           style={{ width: '100%', height: '150px', display: 'block', touchAction: 'pan-y' }}
           onMouseMove={onMove} onMouseLeave={() => setHover(null)}
           onTouchStart={onMove} onTouchMove={onMove} onTouchEnd={() => setHover(null)}>
        <defs>
          <linearGradient id={gradId} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={color} stopOpacity="0.35" />
            <stop offset="100%" stopColor={color} stopOpacity="0" />
          </linearGradient>
        </defs>
        <path d={area} fill={`url(#${gradId})`} />
        {/* vectorEffect : sans lui, preserveAspectRatio="none" étire aussi l'épaisseur du trait */}
        <path d={line} fill="none" stroke={color} strokeWidth="2"
              vectorEffect="non-scaling-stroke" strokeLinejoin="round" strokeLinecap="round" />
        {hp && (
          <>
            <line x1={x(hover)} y1="0" x2={x(hover)} y2={H} stroke="#666" strokeWidth="1"
                  strokeDasharray="4 4" vectorEffect="non-scaling-stroke" />
            <circle cx={x(hover)} cy={y(hp.v)} r="4" fill={color} stroke="#1e1e1e" strokeWidth="2"
                    vectorEffect="non-scaling-stroke" />
          </>
        )}
      </svg>

      <div style={{ display: 'flex', justifyContent: 'space-between', color: '#666', fontSize: '0.7rem', marginTop: '8px' }}>
        <span>{shortDate(points[0].d)}</span>
        {hp && <span style={{ color: '#aaa' }}>{shortDate(hp.d)}</span>}
        <span>{shortDate(points[points.length - 1].d)}</span>
      </div>
    </div>
  );
};

function App() {
  const [dashboardData, setDashboardData] = useState(null)
  const [error, setError] = useState(null)
  const isUpdating = useRef(false)
  const updateTimeout = useRef(null)
  const settingDebounce = useRef({}) // (#11) timers de debounce par clé de réglage
  
  const [isTradesOpen, setIsTradesOpen] = useState(false) // (#9) fermé par défaut
  const [isSettingsOpen, setIsSettingsOpen] = useState(false) // (#9) fermé par défaut

  // (#29) Mailbox retour client (bugs/suggestions)
  const [isFeedbackOpen, setIsFeedbackOpen] = useState(false) // (#9) fermé par défaut
  const [feedbackType, setFeedbackType] = useState('bug')
  const [feedbackMessage, setFeedbackMessage] = useState('')
  const [feedbackSending, setFeedbackSending] = useState(false)
  const [myFeedback, setMyFeedback] = useState([])
  const [isMyFeedbackOpen, setIsMyFeedbackOpen] = useState(false) // (#29) historique replié par défaut, peut devenir long
  const [isDangerOpen, setIsDangerOpen] = useState(false)

  const [isClosedTradesOpen, setIsClosedTradesOpen] = useState(false) // Fermé par défaut pour ne pas polluer l'écran

  // (#15) Verrou des sliders de risque (canaux) : évite les modifs accidentelles en scrollant sur mobile
  const [risksLocked, setRisksLocked] = useState(true)
  // (#16) Liste des 7 jours repliée par défaut (juste le jour actuel affiché)
  const [daysExpanded, setDaysExpanded] = useState(false)

  // --- ÉTAT DE LA FENÊTRE DE CONFIRMATION ---
  const [confirmModal, setConfirmModal] = useState({ isOpen: false, message: '', onConfirm: null, type: 'warning' })
  const cancelButtonRef = useRef(null);

  // (#61) La modale de confirmation n'avait ni rôle dialog/aria-modal, ni fermeture Échap, ni
  // focus initial -- inaccessible au clavier/lecteur d'écran. Focus trap complet (cycle Tab)
  // volontairement omis : 2 boutons seulement, coût/bénéfice disproportionné pour ce lot.
  useEffect(() => {
    if (!confirmModal.isOpen) return;
    cancelButtonRef.current?.focus();
    const onKey = (e) => {
      if (e.key === 'Escape') setConfirmModal((m) => ({ ...m, isOpen: false }));
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [confirmModal.isOpen]);

  // --- ÉTATS POUR LES ANIMATIONS (ÉTAPE 1 & 2) ---
  const [displayedTrades, setDisplayedTrades] = useState([]);
  const [pnlPulse, setPnlPulse] = useState(null); // 'gain' ou 'loss'
  const prevPnl = useRef(0);
  const initialLoad = useRef(true);
  // (#41) Permet à sendCommand() de couper ses listeners onSnapshot immédiatement si le
  // composant démonte pendant une commande en vol, au lieu d'attendre jusqu'à 15s chacun.
  const activeCommandUnsubs = useRef(new Set());
  useEffect(() => () => {
    activeCommandUnsubs.current.forEach((unsub) => unsub());
    activeCommandUnsubs.current.clear();
  }, []);

  // --- FIREBASE SAAS ---
  const [userId, setUserId] = useState(null);
  const [loadingAction, setLoadingAction] = useState(null); // ex: 'close_123456', 'panic'
  const [botOnline, setBotOnline] = useState(false);

// Fonction ultra-sécurisée pour les retours haptiques (Vibrations)
  const triggerHaptic = (type = 'light') => {
    try {
      // 1. On vérifie que TOUT l'arbre Telegram existe avant de l'appeler
      if (window?.Telegram?.WebApp?.HapticFeedback) {
        if (type === 'light' || type === 'medium' || type === 'heavy') {
          window.Telegram.WebApp.HapticFeedback.impactOccurred(type);
        } else if (type === 'success' || type === 'error' || type === 'warning') {
          window.Telegram.WebApp.HapticFeedback.notificationOccurred(type);
        }
      }
    } catch {
      // Si ça échoue (ex: navigateur PC), on ignore silencieusement pour ne pas crasher l'app
      console.log("Haptic feedback non disponible.");
    }
  };

  // (#61) Les sections pliables (Trades Actifs, Panneau de Contrôle, etc.) étaient des
  // <div onClick> sans role/tabIndex/onKeyDown -- inaccessibles au clavier. Helper commun
  // pour ne pas dupliquer ces 3 attributs sur chaque section.
  const a11yToggleProps = (onClick, expanded) => ({
    role: 'button',
    tabIndex: 0,
    'aria-expanded': expanded,
    onClick,
    onKeyDown: (e) => {
      if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onClick(e); }
    },
  });

  // Moteur d'animation React pour intercepter les fermetures
  useEffect(() => {
    if (!dashboardData) return;
    const incoming = dashboardData.active_trades || [];

    // 1. Gestion de l'animation des trades
    setDisplayedTrades(prev => {
      const closed = prev.filter(p => !p.isClosing && !incoming.find(i => i.ticket === p.ticket));
      let next = incoming.map(t => ({ ...t, isClosing: false }));
      
      if (closed.length > 0) {
        closed.forEach(ct => next.push({ ...ct, isClosing: true }));
        // Nettoyage après l'animation (500ms)
        setTimeout(() => setDisplayedTrades(current => current.filter(t => !t.isClosing)), 500);
      }
      return next.sort((a, b) => b.ticket - a.ticket);
    });

    // 2. Gestion du Pulse P&L
    const currentPnl = dashboardData.daily_stats.realized_pnl;
    if (initialLoad.current) {
      initialLoad.current = false;
      prevPnl.current = currentPnl;
    } else if (currentPnl !== prevPnl.current) {
      setPnlPulse(currentPnl > prevPnl.current ? 'gain' : 'loss');
      setTimeout(() => setPnlPulse(null), 500);
      prevPnl.current = currentPnl;
    }
  }, [dashboardData]);
  
  // États pour les Stats
  const [isStatsOpen, setIsStatsOpen] = useState(false);
  const [statsTab, setStatsTab] = useState('jour'); // 'jour', 'canal', 'global'
  const [historicalStats, setHistoricalStats] = useState(null);
  const [globalPeriod, setGlobalPeriod] = useState('30'); // 👈 NOUVEAU : 30 jours par défaut
  // (#stats) Période propre à l'onglet "Par Canal", indépendante de celle de l'onglet Global.
  // "Max" par défaut : c'est la vue cumulée depuis toujours (elle inclut l'historique repris de
  // stats_seed.json), donc celle qui a du sens à l'ouverture. Un 1M par défaut affichait
  // "Aucun trade sur cette période" tant qu'aucun trade récent n'était clôturé.
  const [channelPeriod, setChannelPeriod] = useState('all');
  const [dayWeekToggle, setDayWeekToggle] = useState('day'); // 'day' ou 'week'
  const [isLoadingStats, setIsLoadingStats] = useState(false);
  const requestedPeriods = useRef(new Set());

  // Stats dérivées du state Firestore — chaque période dans sa propre clé
  useEffect(() => {
    if (!dashboardData) return;
    const periodKey = `global_period_${globalPeriod}`;
    // Fallback sur global_period (poussé par l'exe) si la clé spécifique n'existe pas encore
    const period = dashboardData[periodKey] || (globalPeriod === '30' ? dashboardData.global_period : null);
    // (#60) "Cette Semaine" (onglet Jour & Semaine) a besoin d'un vrai agrégat 7 jours
    // INDÉPENDANT de la période choisie dans l'onglet Global -- avant, les deux partageaient
    // le même champ (global_period), donc "Cette Semaine" affichait en fait la période
    // sélectionnée côté Global (30 jours par défaut), pas un vrai 7 jours.
    const weekPeriod = dashboardData.global_period_7 || null;
    // (#stats) Source unique des stats par canal : le CSV détaillé du bot, réagrégé sur la
    // période choisie. Il n'y a plus de cumul "lifetime" séparé -- "Max" joue ce rôle.
    const channelsPeriod = dashboardData[`channels_period_${channelPeriod}`] || null;
    // (#65) Courbe de balance, poussée par le bot avec les stats de la même période.
    const equityCurve = dashboardData[`equity_curve_${globalPeriod}`] || null;
    setHistoricalStats({ global_period: period || null, week_period: weekPeriod,
                         channels_period: channelsPeriod, equity_curve: equityCurve });
    if (period) setIsLoadingStats(false);
  }, [dashboardData, globalPeriod, channelPeriod]);

  // Déclenche une requête au bot quand la période (Global) ou la vue Semaine change (sans alert, sans doublon)
  useEffect(() => {
    if (!isStatsOpen || !userId) return;
    // (#60) Deux sources de requête indépendantes : la période choisie dans l'onglet Global,
    // et un 7 jours fixe pour "Cette Semaine" (onglet Jour & Semaine) -- qui ne doit JAMAIS
    // suivre le sélecteur de l'onglet Global.
    const wantWeek = statsTab === 'jour' && dayWeekToggle === 'week';
    const wantGlobal = statsTab === 'global';
    // (#stats) L'onglet Par Canal a sa propre période -- même commande request_stats, qui
    // pousse désormais aussi channels_period_<days> en plus de global_period_<days>.
    const wantChannel = statsTab === 'canal';
    if (!wantWeek && !wantGlobal && !wantChannel) return;
    const period = wantWeek ? '7' : (wantChannel ? channelPeriod : globalPeriod);
    const periodKey = wantChannel ? `channels_period_${period}` : `global_period_${period}`;
    // (#stats) On ne se fie PAS à la présence de la clé dans Firestore pour éviter la requête :
    // ces champs sont persistants et peuvent dater d'une version antérieure du bot. C'était la
    // cause d'un affichage figé où "14j" montrait plus de trades que "1M", et où 3M et 6M
    // étaient identiques -- deux vieux instantanés que rien ne rafraîchissait jamais.
    // requestedPeriods suffit : une requête par période et par chargement de page.
    const reqKey = wantChannel ? `chan_${period}` : period;
    if (requestedPeriods.current.has(reqKey)) return; // requête déjà envoyée
    requestedPeriods.current.add(reqKey);
    setIsLoadingStats(true);
    // (#41) cancelled/unsub/timer permettent au cleanup du useEffect de couper le listener et
    // le setTimeout de secours immédiatement au démontage, au lieu de les laisser vivre jusqu'à
    // 20s après (avant, rien ne les annulait avant leur propre résolution interne).
    let cancelled = false;
    let unsub = null;
    let timer = null;
    (async () => {
      try {
        const cmdRef = await addDoc(collection(db, 'users', userId, 'commands'), {
          type: 'request_stats', payload: { days: period }, status: 'pending', created_at: serverTimestamp(), expire_at: commandExpiry(),
        });
        if (cancelled) return;
        await new Promise((resolve) => {
          unsub = onSnapshot(doc(db, 'users', userId, 'commands', cmdRef.id), (snap) => {
            const d = snap.data();
            if (d?.status === 'done' || d?.status === 'error') { unsub(); resolve(); }
          });
          timer = setTimeout(() => { unsub(); resolve(); }, 20000);
        });
      } catch { /* silencieux */ }
      if (!cancelled) setIsLoadingStats(false);
    })();
    return () => {
      cancelled = true;
      if (unsub) unsub();
      if (timer) clearTimeout(timer);
    };
  // eslint-disable-next-line react-hooks/exhaustive-deps -- dashboardData lu volontairement sans redéclencher la requête (cf. commentaire ci-dessus)
  }, [isStatsOpen, statsTab, dayWeekToggle, globalPeriod, channelPeriod, userId]);

  // Authentification Firebase + abonnement temps réel Firestore
  useEffect(() => {
    let unsubscribeState = null;

    const setup = async () => {
      try {
        const tgId = getTelegramUserId();
        if (!tgId) {
          setError("Impossible d'identifier l'utilisateur Telegram. Ouvre ce dashboard depuis ton bot Telegram.");
          return;
        }

        // Réutilisation de session : si Firebase a déjà une session valide pour ce telegram_id,
        // on saute l'appel à la fonction Vercel (ouverture quasi instantanée, pas de cold start).
        await auth.authStateReady();
        if (auth.currentUser?.uid !== tgId) {
          const initData = window?.Telegram?.WebApp?.initData || '';
          if (initData) {
            // On échange l'initData signé par Telegram contre un token Firebase lié au telegram_id
            const resp = await fetch('/api/dashboard-auth', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ initData }),
            });
            if (!resp.ok) {
              setError("Authentification refusée. Ouvre ce dashboard depuis ton bot Telegram.");
              return;
            }
            const { token } = await resp.json();
            await signInWithCustomToken(auth, token);
          } else if (import.meta.env.DEV) {
            // Dev local uniquement (hors Telegram) — accès limité par les règles strictes
            await signInAnonymously(auth);
          } else {
            setError("Impossible d'identifier l'utilisateur Telegram. Ouvre ce dashboard depuis ton bot Telegram.");
            return;
          }
        }

        const uid = tgId;
        setUserId(uid);
        const userRef = doc(db, 'users', uid);
        unsubscribeState = onSnapshot(userRef, (snap) => {
          if (!snap.exists()) { return; }
          const data = snap.data();
          setBotOnline(data.bot_online === true);
          if (!isUpdating.current) setDashboardData(data);
          setError(null);
        }, (err) => {
          console.error('Firestore error:', err);
          setError('Connexion Firebase perdue. Vérifie ta connexion internet.');
          setBotOnline(false);
        });
      } catch (err) {
        console.error('Firebase setup error:', err);
        setError('Erreur de connexion au cloud Firebase.');
      }
    };

    setup();
    return () => { if (unsubscribeState) unsubscribeState(); };
  }, []);

  // (#29) Historique "Mes signalements" -- state séparé de dashboardData pour ne pas
  // interférer avec le moteur d'animation (useEffect ci-dessus qui réagit à dashboardData).
  useEffect(() => {
    if (!userId) return;
    const q = query(collection(db, 'feedback'), where('user_uid', '==', userId), orderBy('created_at', 'desc'));
    const unsub = onSnapshot(q, (snap) => {
      setMyFeedback(snap.docs.map((d) => ({ id: d.id, ...d.data() })));
    }, (err) => console.error('Feedback snapshot error:', err));
    return () => unsub();
  }, [userId]);

  // --- FONCTION POUR OUVRIR LA MODAL ---
  const requestConfirm = (message, action, type = 'warning') => {
    setConfirmModal({
      isOpen: true,
      message,
      onConfirm: async () => {
        setConfirmModal({ ...confirmModal, isOpen: false }) // Ferme la modal
        await action() // Exécute l'action
      },
      type
    })
  }

  // Envoie une commande à Firestore et attend confirmation du bot (spinner intégré)
  const sendCommand = useCallback(async (type, payload = {}, actionKey = type) => {
    if (!userId) return;
    setLoadingAction(actionKey);
    try {
      const cmdRef = await addDoc(collection(db, 'users', userId, 'commands'), {
        type, payload, status: 'pending', created_at: serverTimestamp(), expire_at: commandExpiry(),
      });
      await new Promise((resolve, reject) => {
        const unsub = onSnapshot(doc(db, 'users', userId, 'commands', cmdRef.id), (snap) => {
          const d = snap.data();
          if (!d) return;
          if (d.status === 'done')  { unsub(); activeCommandUnsubs.current.delete(unsub); resolve(d.result); }
          if (d.status === 'error') { unsub(); activeCommandUnsubs.current.delete(unsub); reject(new Error(d.result?.message || 'Erreur bot')); }
        });
        activeCommandUnsubs.current.add(unsub);
        setTimeout(() => {
          unsub(); activeCommandUnsubs.current.delete(unsub);
          reject(new Error('Timeout : le bot ne répond pas (15s).'));
        }, 15000);
      });
      triggerHaptic('success');
    } catch (err) {
      console.error('Commande échouée :', err);
      triggerHaptic('error');
      alert(`❌ ${err.message}`);
    } finally {
      setLoadingAction(null);
    }
  }, [userId]);

  // --- ACTIONS ---
  const handleCloseTrade = (ticketId) => {
    requestConfirm(`Es-tu sûr de vouloir FERMER le trade #${ticketId} ?`, async () => {
      await sendCommand('close_trade', { ticket: ticketId }, `close_${ticketId}`);
    }, 'warning');
  };

  const handleBeTrade = (ticketId) => {
    requestConfirm(`Es-tu sûr de vouloir sécuriser (BE) le trade #${ticketId} ?`, async () => {
      await sendCommand('be_trade', { ticket: ticketId }, `be_${ticketId}`);
    }, 'warning');
  };

  const handleSuivre = (ticketId) => {
    requestConfirm(`Veux-tu que le bot prenne le contrôle de ce trade #${ticketId} ?`, async () => {
      await sendCommand('suivre', { ticket: ticketId }, `suivre_${ticketId}`);
    }, 'warning');
  };

  const handlePanic = () => {
    requestConfirm("🚨 ALERTE ROUGE : Veux-tu VRAIMENT fermer TOUTES les positions en cours ?", async () => {
      await sendCommand('panic', {}, 'panic');
    }, 'danger');
  };

  const handleKill = () => {
    requestConfirm("💀 ATTENTION : Le bot va s'éteindre. Tu devras te reconnecter au VPS pour le rallumer. Confirmer ?", async () => {
      await sendCommand('kill', {}, 'kill');
    }, 'danger');
  };

  const handleUnlock = () => {
    requestConfirm("⚠️ Es-tu sûr de vouloir forcer la reprise du trading après un Drawdown ?", async () => {
      await sendCommand('unlock', {}, 'unlock');
    }, 'warning');
  };

  // Curseurs & réglages : mise à jour optimiste immédiate + envoi DÉBOUNCÉ (#11).
  // Un glissement de curseur déclenche des dizaines de onChange : sans debounce, ça
  // créait une commande Firestore par cran (flood -> cascade de pop-ups « Timeout »).
  // On n'envoie donc qu'UNE commande par clé, ~500ms après le dernier changement,
  // en fire-and-forget (pas d'alert bloquant : l'état réel revient via le snapshot du bot).
  // (#15) Le debounce ne concerne QUE les vraies sliders (drag continu, { debounce: true } par défaut) :
  // les champs numériques / clics ponctuels passent { debounce: false } pour un envoi immédiat.
  const updateSetting = (key, value, { debounce = true } = {}) => {
    isUpdating.current = true;
    if (updateTimeout.current) clearTimeout(updateTimeout.current);
    // 1. Affichage optimiste immédiat (curseur fluide)
    setDashboardData(prev => prev ? { ...prev, settings: { ...prev.settings, [key]: value } } : prev);

    // 2. Envoi (débouncé pour les sliders, immédiat sinon)
    if (settingDebounce.current[key]) clearTimeout(settingDebounce.current[key]);
    const sendUpdate = () => {
      if (!userId) return;
      addDoc(collection(db, 'users', userId, 'commands'), {
        type: 'update_setting', payload: { key, value }, status: 'pending', created_at: serverTimestamp(), expire_at: commandExpiry(),
      }).catch((err) => console.error('update_setting échoué', err));
    };
    if (debounce) {
      settingDebounce.current[key] = setTimeout(sendUpdate, 500);
    } else {
      sendUpdate();
    }

    // 3. On rouvre l'écoute des snapshots après stabilisation (bot a le temps de renvoyer l'état)
    updateTimeout.current = setTimeout(() => { isUpdating.current = false; }, 2500);
  };

  // (#17) Toggles instantanés (drawdown, SL suiveur, soft SL, mute) : optimiste + fire-and-forget,
  // comme les réglages (updateSetting). sendCommand attend jusqu'à 15s la confirmation du bot
  // (poll 3s) avant de changer visuellement -- correct pour les actions à risque (fermer un trade,
  // panic, kill) mais ressenti comme un gros lag sur de simples interrupteurs. On flip donc l'état
  // local tout de suite ; l'état réel revient de toute façon via le snapshot du bot.
  const sendInstantToggle = (type, payload, settingKey, settingValue) => {
    triggerHaptic('medium');
    setDashboardData(prev => prev ? { ...prev, settings: { ...prev.settings, [settingKey]: settingValue } } : prev);
    if (!userId) return;
    addDoc(collection(db, 'users', userId, 'commands'), {
      type, payload, status: 'pending', created_at: serverTimestamp(), expire_at: commandExpiry(),
    }).catch((err) => console.error(`${type} échoué`, err));
  };

  // (#29) Anti-spam 1 message / 15 min / client -- basé sur le dernier signalement connu.
  const FEEDBACK_COOLDOWN_MS = 15 * 60 * 1000;
  const lastFeedbackAt = myFeedback[0]?.created_at?.toMillis ? myFeedback[0].created_at.toMillis() : 0;
  const feedbackRemainingMs = lastFeedbackAt ? FEEDBACK_COOLDOWN_MS - (Date.now() - lastFeedbackAt) : 0;
  const feedbackOnCooldown = feedbackRemainingMs > 0;

  const handleSendFeedback = async () => {
    if (!userId || !feedbackMessage.trim() || feedbackOnCooldown || feedbackSending) return;
    setFeedbackSending(true);
    try {
      // (#38) Écriture groupée atomique : les règles Firestore exigent que la création du
      // ticket avance AUSSI feedback_cooldown/{uid}.last_at dans le même batch -- ce document
      // est lui-même limité à 1 écriture/15min par sa propre règle, ce qui rend le cooldown
      // infranchissable côté serveur (avant, seul le bouton désactivé l'appliquait).
      const batch = writeBatch(db);
      batch.set(doc(collection(db, 'feedback')), {
        user_uid: userId, type: feedbackType, message: feedbackMessage.trim(),
        status: 'nouveau', ticket: null, reply: null, relayed: false, created_at: serverTimestamp(),
      });
      batch.set(doc(db, 'feedback_cooldown', userId), { last_at: serverTimestamp() });
      await batch.commit();
      setFeedbackMessage('');
      triggerHaptic('success');
    } catch (err) {
      console.error('Envoi feedback échoué :', err);
      triggerHaptic('error');
      alert("❌ Échec de l'envoi, réessaie plus tard.");
    } finally {
      setFeedbackSending(false);
    }
  };

  const toggleMute = (channelName) => {
    const current = dashboardData?.settings?.muted_channels || [];
    const next = current.includes(channelName) ? current.filter(c => c !== channelName) : [...current, channelName];
    sendInstantToggle('toggle_mute', { channel: channelName }, 'muted_channels', next);
  };

  const toggleDrawdown = () => {
    sendInstantToggle('toggle_drawdown', {}, 'drawdown_active', !(dashboardData?.settings?.drawdown_active !== false));
  };

  const toggleSlSuiveur = () => {
    sendInstantToggle('toggle_sl_suiveur', {}, 'sl_suiveur_active', !(dashboardData?.settings?.sl_suiveur_active !== false));
  };

  const toggleSoftSl = () => {
    sendInstantToggle('toggle_soft_sl', {}, 'soft_sl_active', !(dashboardData?.settings?.soft_sl_active !== false));
  };

  const toggleSabrDoublon = () => {
    sendInstantToggle('toggle_sabr_doublon', {}, 'sabr_anti_doublon', !(dashboardData?.settings?.sabr_anti_doublon !== false));
  };

  // --- RENDU ---
  if (error) return <div style={{ color: '#ff4444', textAlign: 'center', padding: '50px', background: '#121212', minHeight: '100vh', boxSizing: 'border-box', overflowX: 'hidden' }}>{error}</div>

  // --- ÉCRAN DE DÉMARRAGE (SPLASH SCREEN) ---
  if (!dashboardData) {
    return (
      <div style={{ width: '800px', maxWidth: '100%', boxSizing: 'border-box', overflowX: 'hidden', margin: '0 auto', padding: '20px', backgroundColor: '#121212', minHeight: '100vh', display: 'flex', flexDirection: 'column' }}>

        {/* CONTENU CENTRAL DU SPLASH SCREEN */}
        <div style={{ flex: 1, display: 'flex', flexDirection: 'column', justifyContent: 'center', alignItems: 'center' }}>
          <div style={{ animation: 'logoPulse 2s infinite ease-in-out', marginBottom: '30px' }}>
            {/* L'icône fait office de Logo */}
            <Activity size={100} color="#00e676" strokeWidth={1.5} />
          </div>
          <h1 style={{ color: '#fff', fontSize: '2rem', letterSpacing: '8px', margin: '0 0 10px 0', fontWeight: '300', textTransform: 'uppercase' }}>
            Système <strong style={{ fontWeight: '900' }}>Actif</strong>
          </h1>
          <p style={{ color: '#00e676', animation: 'textBlink 1.5s infinite ease-in-out', fontSize: '1rem', letterSpacing: '2px', fontWeight: 'bold', margin: 0 }}>
            CONNEXION AU CLOUD...
          </p>
        </div>
      </div>
    );
  }

const { account, daily_stats, settings, active_trades, closed_trades = [] } = dashboardData

  // (#7) Pack du client : on n'affiche que les canaux de son pack (réglages, mute, stats).
  // Liste vide/absente = tout afficher (rétro-compatible). Filtre purement cosmétique.
  const allowedChannels = settings.allowed_channels || [];
  // (#ui) Les prix débordaient sur la colonne du gain : un toFixed(4) sur une paire JPY
  // (195.1234) ou un indice fait 7 chiffres ou plus. On plafonne à 6 chiffres au total, point
  // exclu, en rognant uniquement sur les décimales -- la partie entière n'est jamais tronquée,
  // sinon le prix devient faux et non plus seulement imprécis.
  const MAX_PRICE_DIGITS = 6;
  const fmtPrice = (value) => {
    const n = Number(value);
    if (!Number.isFinite(n)) return '—';
    const intDigits = Math.trunc(Math.abs(n)).toString().length;
    return n.toFixed(Math.max(0, MAX_PRICE_DIGITS - intDigits));
  };

  const channelBaseToken = (name) => {
    if (!name) return '';
    let t = String(name).toUpperCase().replace('CANAL', '').trim().replace(/\s+/g, '_').replace(/_+/g, '_');
    for (const suf of ['_SNIPER', '_ZONE', '_BLIND']) {
      if (t.endsWith(suf)) t = t.slice(0, -suf.length);
    }
    return t.replace(/^_+|_+$/g, '');
  };
  const isChannelVisible = (name) => {
    // (#39) sentinel "aucun canal" (pack suspendu) -- distinct de la liste vide (pas de pack
    // configuré -> tout visible), cf. channel_allowed()/recompute_pack_lock() côté backend.
    if (allowedChannels.length === 1 && allowedChannels[0] === 'NONE') return false;
    if (!allowedChannels.length) return true;        // pack vide = tout visible
    const base = channelBaseToken(name);
    if (!base) return true;
    return allowedChannels.some((c) => channelBaseToken(c) === base);
  };
  // (#pack) Pack 100% SABR -> les % globaux (or/indices/devises) et le planning heure/jour
  // n'ont aucun effet sur les trades (lots fixes / risk_override dédié / multiplicateur
  // appliqué uniquement à la branche "lot auto"). Le backend tranche via
  // pack_uses_risk_engine() ; on retombe sur un calcul local si le champ est absent
  // (ancien exe client pas encore mis à jour).
  const usesRiskEngine = settings.uses_risk_engine !== undefined
    ? settings.uses_risk_engine
    : (!allowedChannels.length
        || (allowedChannels.length === 1 && allowedChannels[0] === 'NONE')
        || allowedChannels.some((c) => !channelBaseToken(c).startsWith('SABR')));

  // Curseurs de risque % réellement affichables pour ce pack. Sur un pack 100% SABR la
  // liste est VIDE (les 3 globaux sont riskEngineOnly, les autres sont filtrés par canal) :
  // on masque alors tout l'en-tête "Gestion du Risque (%)" + son verrou, et la section
  // Lots SABR perd son trait de séparation, sinon il restait un bloc titre vide.
  const riskSliders = [
    { label: 'Or (Global)', key: 'risk_gold', color: '#ffd700', riskEngineOnly: true },
    { label: 'Indices (Global)', key: 'risk_indices', color: '#4da8da', riskEngineOnly: true },
    { label: 'Devises (Global)', key: 'risk_other', color: '#ff8a65', riskEngineOnly: true },
    { label: 'MDT VIP', key: 'risk_mdt_vip', color: '#a5d6a7', channel: 'CANAL MDT_VIP' },
    { label: 'MDT Free', key: 'risk_mdt_free', color: '#81c784', channel: 'CANAL MDT_FREE' },
    { label: 'Space', key: 'risk_space', color: '#ce93d8', channel: 'CANAL SPACE' },
    { label: 'Ligue', key: 'risk_ligue', color: '#ffb74d', channel: 'CANAL LIGUE' },
    { label: 'Perso', key: 'risk_perso', color: '#90caf9', channel: 'CANAL PERSO' },
  ].filter((item) => (!item.channel || isChannelVisible(item.channel))
                     && (!item.riskEngineOnly || usesRiskEngine));
  const hasRiskSliders = riskSliders.length > 0;

  // 1. On déclare d'abord la balance
  // (#61) Valeur DÉRIVÉE (pas un champ direct) -- ?? seul au point d'affichage ne protège pas
  // contre un NaN si account.balance était transitoirement absent (?? ne rattrape que
  // null/undefined, pas NaN). On garde les deux opérandes ici, à la source.
  const balanceMinuit = (account.balance ?? 0) - (daily_stats.realized_pnl ?? 0);
  // Verrou Drawdown : persistant côté backend une fois déclenché (#DD), ne se lève plus
  // tout seul en relevant juste le Max DD -- il faut ensuite un déverrouillage manuel.
  const isLocked = !!settings.security_triggered;
  // 2. ENSUITE on calcule le risque en utilisant la balance
  const ddLimit = balanceMinuit * settings.max_dd;
  const currentLoss = daily_stats.total_pnl < 0 ? Math.abs(daily_stats.total_pnl) : 0;
  const riskPercent = (currentLoss / ddLimit) * 100;
  const isNearDD = riskPercent >= 80;
  // (#DD) Jaune : verrouillé mais plus au-dessus du seuil critique (Max DD relevé) --
  // il ne manque plus que le déverrouillage manuel pour repasser à la normale.
  const isDDPendingUnlock = isLocked && !isNearDD;

  const currentHour = new Date().getHours();
  const pyDay = (new Date().getDay() + 6) % 7; 
  
  const isPrimeNow = (settings.prime_hours || []).includes(currentHour);
  const timeFactor = isPrimeNow ? 1.0 : (settings.time_off_peak ?? 0.5);
  const dayFactor = settings[`day_factor_${pyDay}`] ?? 1.0;
  const currentMultiplier = timeFactor * dayFactor;

  return (
    <div style={{ width: '800px', maxWidth: '100%', boxSizing: 'border-box', margin: '0 auto', fontFamily: 'system-ui, sans-serif', padding: '20px', backgroundColor: '#121212', color: '#ffffff', minHeight: '100vh', position: 'relative', overflowX: 'hidden' }}>
      
      {/* VIGNETTE D'ALERTE DANGER (ÉTAPE 3) */}
      {isNearDD && <div className="vignette-alert" />}
      {/* (#DD) Jaune : verrou DD actif mais Max DD déjà relevé -- il manque juste le déverrouillage manuel */}
      {isDDPendingUnlock && <div className="vignette-alert" style={{ boxShadow: 'inset 0 0 35px rgba(255, 193, 7, 0.9)' }} />}
      
      {/* INJECTION DES ANIMATIONS CSS POUR LA MODAL */}
      <style>{`
        /* Modale */
        @keyframes fadeIn { from { opacity: 0; backdrop-filter: blur(0px); } to { opacity: 1; backdrop-filter: blur(4px); } }
        @keyframes popIn { from { transform: scale(0.9); opacity: 0; } to { transform: scale(1); opacity: 1; } }
        
        /* Trades & P&L */
        @keyframes pulseGain { 0% { transform: scale(1); box-shadow: 0 0 0px rgba(0, 230, 118, 0); } 50% { transform: scale(1.02); box-shadow: 0 0 25px rgba(0, 230, 118, 0.8); } 100% { transform: scale(1); box-shadow: 0 0 0px rgba(0, 230, 118, 0); } }
        @keyframes pulseLoss { 0% { transform: scale(1); box-shadow: 0 0 0px rgba(255, 68, 68, 0); } 50% { transform: scale(1.02); box-shadow: 0 0 25px rgba(255, 68, 68, 0.8); } 100% { transform: scale(1); box-shadow: 0 0 0px rgba(255, 68, 68, 0); } }
        @keyframes slideOutGain { 0% { max-height: 150px; opacity: 1; background: rgba(0, 230, 118, 0.6); transform: scale(1); } 100% { max-height: 0px; opacity: 0; padding: 0; margin: 0; transform: scale(0.9); background: rgba(0, 230, 118, 0); border-color: transparent; } }
        @keyframes slideOutLoss { 0% { max-height: 150px; opacity: 1; background: rgba(255, 68, 68, 0.6); transform: scale(1); } 100% { max-height: 0px; opacity: 0; padding: 0; margin: 0; transform: scale(0.9); background: rgba(255, 68, 68, 0); border-color: transparent; } }
        
        /* Zone de Danger (Étape 3) */
        @keyframes siren { 0% { border-color: #ff4444; box-shadow: 0 0 5px #ff4444; } 50% { border-color: #ffc107; box-shadow: 0 0 20px #ff4444; } 100% { border-color: #ff4444; box-shadow: 0 0 5px #ff4444; } }
        @keyframes radioactive { 0% { box-shadow: 0 0 5px #ff4444; background: rgba(255, 68, 68, 0.1); } 50% { box-shadow: 0 0 20px #ff4444; background: rgba(255, 68, 68, 0.3); } 100% { box-shadow: 0 0 5px #ff4444; background: rgba(255, 68, 68, 0.1); } }
        @keyframes alertFade { from { opacity: 0; } to { opacity: 1; } }
        
        .vignette-alert { 
          position: fixed; top: 0; left: 0; right: 0; bottom: 0; pointer-events: none; z-index: 999; 
          box-shadow: inset 0 0 35px rgba(255, 0, 0, 0.9); /* Moins étalé (60px), rouge plus fort (0.9) */
          animation: alertFade 0.5s ease-out forwards; /* Apparition rapide, nette, et reste affichée */
        }

        /* Lockdown et Cascade (Étape 4) */
        /* Le fond rouge apparaît, devient gris flou, puis disparaît totalement */
        @keyframes lockdownFlash { 
          0% { background-color: rgba(255, 0, 0, 0.8); backdrop-filter: blur(0px); opacity: 1; z-index: 1000; } 
          20% { background-color: rgba(18, 18, 18, 0.85); backdrop-filter: blur(8px); opacity: 1; z-index: 1000; } 
          80% { background-color: rgba(18, 18, 18, 0.85); backdrop-filter: blur(8px); opacity: 1; z-index: 1000; } 
          100% { background-color: rgba(18, 18, 18, 0); backdrop-filter: blur(0px); opacity: 0; z-index: -1; } 
        }
        
        /* Le tampon rebondit, reste visible, puis s'évanouit en montant */
        @keyframes stampBounce { 
          0% { transform: scale(3) rotate(-10deg); opacity: 0; } 
          15% { transform: scale(0.9) rotate(-10deg); opacity: 1; } 
          25% { transform: scale(1.1) rotate(-10deg); opacity: 1; } 
          80% { transform: scale(1) rotate(-10deg); opacity: 1; } 
          100% { transform: scale(1.2) translateY(-50px) rotate(-10deg); opacity: 0; } 
        }
        
        /* Cascade fluide : Lève (anticipation), glisse, puis rétrécit */
        @keyframes cascadeClose { 
          0% { opacity: 1; transform: translate(0, 0) scale(1); max-height: 150px; } 
          20% { opacity: 1; transform: translate(0, -15px) scale(1.02); max-height: 150px; } /* Prend son élan vers le haut */
          40% { opacity: 0; transform: translateX(100px); max-height: 150px; padding: 15px; margin-bottom: 15px; }
          60% { opacity: 0; transform: translate(150px, -15px) scale(0.9); max-height: 150px; padding: 15px; margin-bottom: 15px; } /* Fuit sur le côté */
          100% { opacity: 0; transform: translate(150px, -15px) scale(0.9); max-height: 0px; padding: 0px; margin-bottom: 0px; border-color: transparent; } /* Se referme doucement */
        }
        
        .lockdown-overlay {
          position: fixed; top: 0; left: 0; right: 0; bottom: 0; 
          display: flex; flex-direction: column; justify-content: center; align-items: center;
          pointer-events: none; /* Pour pouvoir cliquer au travers même quand c'est affiché */
          animation: lockdownFlash 2s ease-out forwards; /* Dure 2 secondes puis reste "effacé" */
        }
        .lockdown-stamp {
          color: #ff4444; border: 5px solid #ff4444; border-radius: 15px; padding: 20px 40px;
          font-size: 3rem; font-weight: 900; text-transform: uppercase; letter-spacing: 5px;
          text-shadow: 0 0 20px rgba(255, 68, 68, 0.5); box-shadow: 0 0 30px rgba(255, 68, 68, 0.2), inset 0 0 20px rgba(255, 68, 68, 0.2);
          animation: stampBounce 2s ease-out forwards; /* Synchronisé avec le fond */
        }

        /* Splash Screen & Hovers */
        @keyframes logoPulse { 
          0% { transform: scale(1); filter: drop-shadow(0 0 10px rgba(0, 230, 118, 0.3)); } 
          50% { transform: scale(1.15); filter: drop-shadow(0 0 40px rgba(0, 230, 118, 0.8)); } 
          100% { transform: scale(1); filter: drop-shadow(0 0 10px rgba(0, 230, 118, 0.3)); } 
        }
        @keyframes textBlink { 0%, 100% { opacity: 0.4; } 50% { opacity: 1; } }
        
        .btn-interactive { transition: all 0.2s ease; }
        .btn-interactive:hover, .btn-interactive.force-hover { transform: translateY(-2px) scale(1.05); filter: brightness(1.2); box-shadow: 0 4px 12px rgba(255,255,255,0.1); }

        /* Animation du Point Actif (Point 3B) */
        @keyframes fetchPulse { 0% { transform: scale(1); opacity: 1; box-shadow: 0 0 8px #00e676; } 50% { transform: scale(1.5); opacity: 0.5; box-shadow: 0 0 20px #00e676; } 100% { transform: scale(1); opacity: 1; box-shadow: 0 0 8px #00e676; } }
        .skeleton-box {
          background: #1e1e1e;
          background-image: linear-gradient(to right, #1e1e1e 0%, #2a2a2a 20%, #1e1e1e 40%, #1e1e1e 100%);
          background-repeat: no-repeat; background-size: 800px 100%;
          animation: shimmer 1.5s infinite linear forwards;
          border-radius: 12px; border: 1px solid #333;
        }
      `}</style>

      {/* --- MODAL DE CONFIRMATION --- */}
      {confirmModal.isOpen && (
        <div style={{ position: 'fixed', top: 0, left: 0, right: 0, bottom: 0, backgroundColor: 'rgba(0,0,0,0.7)', zIndex: 1000, display: 'flex', justifyContent: 'center', alignItems: 'center', animation: 'fadeIn 0.2s ease-out forwards' }}>
          <div role="dialog" aria-modal="true" aria-labelledby="confirm-modal-title" style={{ background: '#1e1e1e', border: `1px solid ${confirmModal.type === 'danger' ? '#ff4444' : '#ffc107'}`, borderRadius: '15px', padding: '25px', maxWidth: '400px', width: '90%', textAlign: 'center', boxShadow: '0 10px 40px rgba(0,0,0,0.8)', animation: 'popIn 0.2s ease-out' }}>
            <div style={{ color: confirmModal.type === 'danger' ? '#ff4444' : '#ffc107', marginBottom: '15px', display: 'flex', justifyContent: 'center' }}>
              <AlertTriangle size={48} />
            </div>
            <h3 id="confirm-modal-title" style={{ margin: '0 0 15px 0', fontSize: '1.2rem' }}>
              {confirmModal.type === 'danger' ? 'Contrôle Système' : 'Confirmation Requise'}
            </h3>
            <p style={{ color: '#ccc', fontSize: '1rem', lineHeight: '1.5', marginBottom: '25px' }}>
              {confirmModal.message}
            </p>
            <div style={{ display: 'flex', gap: '15px' }}>
              <button ref={cancelButtonRef} onClick={() => setConfirmModal({ ...confirmModal, isOpen: false })} style={{ flex: 1, padding: '12px', background: 'transparent', border: '1px solid #555', color: '#fff', borderRadius: '8px', cursor: 'pointer', fontWeight: 'bold', transition: '0.2s' }}>
                Annuler
              </button>
              <button onClick={confirmModal.onConfirm} style={{ flex: 1, padding: '12px', background: confirmModal.type === 'danger' ? '#ff4444' : '#ffc107', border: 'none', color: '#000', borderRadius: '8px', cursor: 'pointer', fontWeight: 'bold', boxShadow: `0 4px 12px ${confirmModal.type === 'danger' ? 'rgba(255, 68, 68, 0.4)' : 'rgba(255, 193, 7, 0.4)'}` }}>
                Confirmer
              </button>
            </div>
          </div>
        </div>
      )}

      {/* OVERLAY LOCKDOWN PANIC (ÉTAPE 4) */}
      {isLocked && (
        <div className="lockdown-overlay">
          <div className="lockdown-stamp">
            <AlertTriangle size={60} style={{ display: 'block', margin: '0 auto 10px' }} />
            SÉCURITÉ ACTIVÉE
          </div>
          <p style={{ marginTop: '20px', fontSize: '1.2rem', color: '#aaa', fontWeight: 'bold' }}>Marché verrouillé. Positions clôturées.</p>
        </div>
      )}

      {/* HEADER AVEC STATUT "ACTIF" */}
      <header style={{ borderBottom: '1px solid #333', paddingBottom: '15px', marginBottom: '20px', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <h1 style={{ fontSize: '1.5rem', margin: 0, display: 'flex', alignItems: 'center', gap: '10px' }}>
          <Activity size={24} color="#00e676" /> Dashboard {isLocked && (
            isDDPendingUnlock
              ? <span style={{color: '#ffc107', fontSize:'0.9rem'}}>(DD -- DÉVERROUILLAGE REQUIS)</span>
              : <span style={{color: '#ff4444', fontSize:'0.9rem'}}>(SÉCURITÉ DD)</span>
          )}
        </h1>
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px', color: '#aaa', fontSize: '0.9rem' }}>
          <span style={{
            height: '10px', width: '10px',
            backgroundColor: botOnline ? '#00e676' : '#ff4444',
            borderRadius: '50%', display: 'inline-block',
            boxShadow: `0 0 8px ${botOnline ? '#00e676' : '#ff4444'}`,
            animation: loadingAction ? 'fetchPulse 0.5s ease-out infinite' : 'none'
          }}></span>
          {botOnline ? 'Actif' : 'Hors ligne'}
        </div>
      </header>

      {/* SECTION 1 : STATS GLOBALES */}
      <div style={{ display: 'flex', gap: '10px', marginBottom: '15px', width: '100%' }}>
        
        {/* CASE BALANCE */}
        <div style={{ flex: 1, minWidth: 0, padding: '12px 8px', background: '#1e1e1e', border: '1px solid #333', borderRadius: '12px', display: 'flex', flexDirection: 'column', minHeight: '135px' }}>
          <h3 style={{ margin: 0, color: '#aaa', fontSize: '0.7rem', display: 'flex', alignItems: 'center', gap: '5px', whiteSpace: 'nowrap' }}>
            <Wallet size={12} color="#aaa"/> Balance
          </h3>
          
          <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
            <h2 style={{ fontSize: '1.2rem', fontWeight: 'bold', margin: 0, whiteSpace: 'nowrap', letterSpacing: '-0.5px' }}>
              <AnimatedNumber value={account.balance} />&nbsp;€
            </h2>
          </div>

          {/* Ligne d'informations secondaires (Centrées avec espacement) */}
          <div style={{ display: 'flex', width: '100%', fontSize: '0.65rem', color: '#888', borderTop: '1px solid rgba(255,255,255,0.05)', paddingTop: '8px', marginTop: '5px' }}>
            <div style={{ flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center', borderRight: '1px solid rgba(255,255,255,0.05)' }}>
              <span style={{ whiteSpace: 'nowrap' }}>Equity :</span>
              <strong style={{ color: '#666' }}>{Math.round(account.equity ?? 0).toLocaleString('fr-FR')} €</strong>
            </div>
            <div style={{ flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center' }}>
              <span style={{ whiteSpace: 'nowrap' }}>Réf 00h :</span>
              <strong style={{ color: '#666' }}>{Math.round(balanceMinuit).toLocaleString('fr-FR')} €</strong>
            </div>
          </div>
        </div>

        {/* CASE P&L DU JOUR (ENCAISSÉ) */}
        <div style={{ 
          flex: 1, minWidth: 0, padding: '12px 8px', 
          background: daily_stats.realized_pnl >= 0 ? 'rgba(0, 230, 118, 0.05)' : 'rgba(255, 68, 68, 0.05)', 
          border: `1px solid ${daily_stats.realized_pnl >= 0 ? '#00e676' : '#ff4444'}`, 
          borderRadius: '12px', display: 'flex', flexDirection: 'column', minHeight: '135px',
          animation: pnlPulse === 'gain' ? 'pulseGain 0.5s ease-out' : pnlPulse === 'loss' ? 'pulseLoss 0.5s ease-out' : 'none'
        }}>
          <h3 style={{ margin: 0, color: daily_stats.realized_pnl >= 0 ? '#00e676' : '#ff4444', fontSize: '0.7rem', display: 'flex', alignItems: 'center', gap: '5px', whiteSpace: 'nowrap' }}>
            {daily_stats.realized_pnl >= 0 ? <TrendingUp size={12}/> : <TrendingDown size={12}/>} Profit
          </h3>
          
          <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
            <div style={{ display: 'flex', alignItems: 'baseline', gap: '2px', whiteSpace: 'nowrap' }}>
              <h2 style={{ fontSize: '1.2rem', fontWeight: 'bold', margin: 0, color: daily_stats.realized_pnl >= 0 ? '#00e676' : '#ff4444', letterSpacing: '-0.5px' }}>
                <AnimatedNumber value={daily_stats.realized_pnl} prefix={daily_stats.realized_pnl > 0 ? '+' : ''} />&nbsp;€
              </h2>
              <span style={{ fontSize: '0.65rem', fontWeight: 'bold', color: daily_stats.realized_pnl >= 0 ? '#00e676' : '#ff4444', opacity: 0.8 }}>
                ({daily_stats.realized_pnl > 0 ? '+' : ''}{(balanceMinuit > 0 ? (daily_stats.realized_pnl / balanceMinuit) * 100 : 0).toFixed(1)}%)
              </span>
            </div>
          </div>

          {/* Ligne d'informations secondaires (Centrées avec espacement) */}
          <div style={{ display: 'flex', width: '100%', fontSize: '0.65rem', color: '#888', borderTop: '1px solid rgba(255,255,255,0.05)', paddingTop: '8px', marginTop: '5px' }}>
            <div style={{ flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center', borderRight: '1px solid rgba(255,255,255,0.05)' }}>
              <span style={{ whiteSpace: 'nowrap' }}>Flottant :</span>
              <strong style={{ color: (daily_stats.floating_pnl ?? 0) >= 0 ? '#00e676' : '#ff4444' }}>
                {daily_stats.floating_pnl > 0 ? '+' : ''}{Math.round(daily_stats.floating_pnl ?? 0).toLocaleString('fr-FR')} €
              </strong>
            </div>
            <div style={{ flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center' }}>
              <span style={{ whiteSpace: 'nowrap' }}>Total :</span>
              <strong style={{ color: (daily_stats.total_pnl ?? 0) >= 0 ? '#00e676' : '#ff4444' }}>
                {daily_stats.total_pnl > 0 ? '+' : ''}{Math.round(daily_stats.total_pnl ?? 0).toLocaleString('fr-FR')} €
              </strong>
            </div>
          </div>
        </div>

      </div>

      {/* BOUTON PANIC CENTRÉ SOUS LES STATS */}
      <div style={{ textAlign: 'center', marginBottom: '30px' }}>
        <button
          onClick={handlePanic}
          disabled={!!loadingAction}
          style={{
            background: 'rgba(255, 68, 68, 0.1)',
            border: '1px solid #ff4444',
            color: '#ff4444',
            padding: '8px 20px',
            borderRadius: '20px',
            cursor: 'pointer',
            fontWeight: 'bold',
            display: 'inline-flex',
            alignItems: 'center',
            gap: '8px',
            transition: '0.2s',
            opacity: loadingAction === 'panic' ? 0.6 : 1,
            animation: isNearDD ? 'radioactive 3s infinite alternate' : 'none'
          }}
        >
          <AlertTriangle size={16} /> {loadingAction === 'panic' ? 'EN COURS...' : 'TOUT COUPER'}
        </button>
      </div>

      {/* SECTION 2 : TRADES ACTIFS */}
      <div {...a11yToggleProps(() => { triggerHaptic('light'); setIsTradesOpen(!isTradesOpen); }, isTradesOpen)} style={{ display: 'flex', justifyContent: 'flex-start', alignItems: 'center', cursor: 'pointer', borderBottom: '1px solid #333', paddingBottom: '25px', marginBottom: '0px', marginTop: '30px', userSelect: 'none' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px', color: '#ddd' }}>
          <Briefcase size={20} color="#2196f3" /> {/* 👈 Icône Bleue */}
          <h2 style={{ fontSize: '1.2rem', margin: 0 }}>Trades Actifs ({active_trades.length})</h2>
          {isTradesOpen ? <ChevronUp size={20} color="#2196f3" /> : <ChevronDown size={20} color="#2196f3" />} {/* 👈 Flèches Bleues */}
        </div>
      </div>
      
      <div style={{ display: 'grid', gridTemplateRows: isTradesOpen ? '1fr' : '0fr', transition: 'grid-template-rows 0.4s ease-out' }}>
        <div style={{ overflow: 'hidden' }}>
          <div style={{ display: 'flex', flexDirection: 'column', gap: '12px', marginBottom: '30px', paddingTop: '5px' }}>
          {active_trades.length === 0 && displayedTrades.length === 0 ? (
            <div style={{ textAlign: 'center', padding: '30px', color: '#666', background: '#1e1e1e', borderRadius: '10px' }}>Aucune position.</div>
          ) : (
            displayedTrades.map((trade, index) => {
              return (
              <div key={trade.ticket} style={{ 
                background: trade.managed ? '#1e1e1e' : 'rgba(33, 150, 243, 0.1)', 
                border: `1px solid ${trade.managed ? '#333' : '#2196f3'}`, 
                padding: '12px 10px', 
                borderRadius: '10px', 
                
                /* STRUCTURE EN GRILLE : On élargit un peu la colonne de droite (75px) pour les gros boutons */
                display: 'grid', 
                gridTemplateColumns: '1fr 1fr 75px', 
                gap: '8px',
                alignItems: 'center',

                overflow: 'hidden',
                animation: isLocked ? `cascadeClose 1.2s ease-in-out forwards ${2 + (index * 0.2)}s` : trade.isClosing ? (trade.profit >= 0 ? 'slideOutGain 0.5s forwards' : 'slideOutLoss 0.5s forwards') : 'fadeIn 0.3s ease-out'
              }}>
                
                {/* --- COLONNE 1 : SYMBOLE ET PRIX --- */}
                <div style={{ display: 'flex', flexDirection: 'column', minWidth: 0 }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '6px', marginBottom: '6px' }}>
                    <span style={{ padding: '2px 6px', borderRadius: '4px', fontSize: '0.65rem', fontWeight: 'bold', background: trade.type === 'BUY' ? 'rgba(0, 230, 118, 0.2)' : 'rgba(255, 68, 68, 0.2)', color: trade.type === 'BUY' ? '#00e676' : '#ff4444' }}>
                      {trade.type}
                    </span>
                    <strong style={{ fontSize: '0.95rem', whiteSpace: 'nowrap', textOverflow: 'ellipsis', overflow: 'hidden' }}>
                      {trade.symbol}
                    </strong>
                  </div>
                  
                  {/* Prix formatés dynamiquement */}
                  <div style={{ fontSize: '0.65rem', color: '#888', lineHeight: '1.4', whiteSpace: 'nowrap' }}>
                    Ouv: {fmtPrice(trade.open_price)} | Act: <span style={{ color: '#fff' }}>{fmtPrice(trade.current_price)}</span><br />
                    SL: {trade.sl > 0 ? fmtPrice(trade.sl) : "Aucun"} | TP: {trade.tp > 0 ? fmtPrice(trade.tp) : "Aucun"}
                  </div>
                </div>

                {/* --- COLONNE 2 : LOTS, HEURE ET PROFIT --- */}
                <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', padding: '0 5px' }}>
                  
                  {/* Lots et Heures (Gris et petits) */}
                  <div style={{ display: 'flex', alignItems: 'center', gap: '4px', marginBottom: '6px' }}>
                     <span style={{ color: '#888', fontSize: '0.7rem' }}>{Number(trade.volume).toFixed(2)} lots</span>
                     <span style={{ color: '#666', fontSize: '0.65rem' }}>({trade.open_time})</span>
                  </div>
                  
                  {/* Profit agrandi (1.25rem) */}
                  <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center' }}>
                    <div style={{ fontWeight: 'bold', fontSize: '1.25rem', color: trade.profit >= 0 ? '#00e676' : '#ff4444', whiteSpace: 'nowrap' }}>
                      {trade.profit > 0 ? '+' : ''}{(trade.profit ?? 0).toFixed(2)} €
                    </div>
                    <div style={{ fontSize: '0.75rem', color: '#aaa' }}>
                      {trade.pips > 0 ? '+' : ''}{trade.pips} p
                    </div>
                  </div>
                </div>

                {/* --- COLONNE 3 : BOUTONS (Agrandis) --- */}
                <div style={{ display: 'flex', flexDirection: 'column', gap: '8px', justifyContent: 'center' }}>
                  {!trade.managed ? (
                    <button onClick={() => handleSuivre(trade.ticket)} disabled={!!loadingAction} className="btn-interactive" style={{ background: '#2196f3', border: 'none', color: 'white', padding: '12px 0', borderRadius: '6px', cursor: 'pointer', fontWeight: 'bold', fontSize: '0.8rem', width: '100%', opacity: loadingAction === `suivre_${trade.ticket}` ? 0.6 : 1 }}>
                      {loadingAction === `suivre_${trade.ticket}` ? '...' : 'Suivre'}
                    </button>
                  ) : (
                    <>
                      <button onClick={() => handleBeTrade(trade.ticket)} disabled={!!loadingAction} className="btn-interactive" style={{ background: 'rgba(255, 193, 7, 0.1)', border: '1px solid #ffc107', color: '#ffc107', padding: '8px 0', borderRadius: '6px', cursor: 'pointer', display: 'flex', alignItems: 'center', justifyContent: 'center', gap: '4px', fontSize: '0.75rem', fontWeight: 'bold', width: '100%', opacity: loadingAction === `be_${trade.ticket}` ? 0.6 : 1 }}>
                        {loadingAction === `be_${trade.ticket}` ? '...' : <><Shield size={14} /> BE</>}
                      </button>
                      <button onClick={() => handleCloseTrade(trade.ticket)} disabled={!!loadingAction} className="btn-interactive" style={{ background: 'rgba(255, 68, 68, 0.1)', border: '1px solid #ff4444', color: '#ff4444', padding: '8px 0', borderRadius: '6px', cursor: 'pointer', display: 'flex', alignItems: 'center', justifyContent: 'center', gap: '4px', fontSize: '0.75rem', fontWeight: 'bold', width: '100%', opacity: loadingAction === `close_${trade.ticket}` ? 0.6 : 1 }}>
                        {loadingAction === `close_${trade.ticket}` ? '...' : <><XCircle size={14} /> Close</>}
                      </button>
                    </>
                  )}
                </div>

              </div>
            )})
          )}
        </div>
      </div>
      </div>

      {/* SECTION 2 BIS : TRADES FERMÉS */}
      <div {...a11yToggleProps(() => { triggerHaptic('light'); setIsClosedTradesOpen(!isClosedTradesOpen); }, isClosedTradesOpen)} style={{ display: 'flex', justifyContent: 'flex-start', alignItems: 'center', cursor: 'pointer', borderBottom: '1px solid #333', paddingBottom: '25px', marginBottom: '0px', marginTop: '30px', userSelect: 'none' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px', color: '#888' }}>
          <XCircle size={20} color="#888" />
          <h2 style={{ fontSize: '1.2rem', margin: 0 }}>Trades Fermés ({closed_trades.length})</h2>
          {isClosedTradesOpen ? <ChevronUp size={20} color="#888" /> : <ChevronDown size={20} color="#888" />}
        </div>
      </div>
      
      <div style={{ display: 'grid', gridTemplateRows: isClosedTradesOpen ? '1fr' : '0fr', transition: 'grid-template-rows 0.4s ease-out' }}>
        <div style={{ overflow: 'hidden' }}>
          <div style={{ display: 'flex', flexDirection: 'column', gap: '10px', marginBottom: '30px', paddingTop: '5px' }}>
          {closed_trades.length === 0 ? (
            <div style={{ textAlign: 'center', padding: '20px', color: '#555', background: '#1a1a1a', borderRadius: '10px', fontSize: '0.9rem' }}>Aucun trade clôturé aujourd'hui.</div>
          ) : (
            closed_trades.map((trade, index) => (
              // (#61) ?? au lieu de || -- un ticket à 0 (peu probable en pratique pour MT5,
              // mais possible sur des lignes de stats sans vrai ticket) tombait à tort sur
              // l'index, moins stable en cas de réordonnancement du tableau.
              <div key={trade.ticket ?? index} style={{
                background: '#1a1a1a', 
                borderLeft: `4px solid ${trade.profit >= 0 ? '#00e676' : '#ff4444'}`, 
                padding: '12px 15px', 
                borderRadius: '8px', display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '15px'
              }}>
                <div style={{ flex: '1 1 200px' }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '10px', marginBottom: '5px' }}>
                    <span style={{ padding: '2px 6px', borderRadius: '4px', fontSize: '0.7rem', fontWeight: 'bold', background: trade.type === 'BUY' ? 'rgba(0, 230, 118, 0.1)' : 'rgba(255, 68, 68, 0.1)', color: trade.type === 'BUY' ? '#00e676' : '#ff4444' }}>{trade.type}</span>
                    <strong style={{ fontSize: '1rem', color: '#ccc' }}>{trade.symbol}</strong>
                    <div style={{ flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center' }}>
                      <span style={{ color: '#888', fontSize: '0.75rem', fontWeight: 'bold', whiteSpace: 'nowrap' }}>{trade.volume} lots</span>
                      <span style={{ color: '#555', fontSize: '0.65rem' }}>{trade.open_time}</span>
                    </div>
                  </div>
                  <div style={{ fontSize: '0.75rem', color: '#777' }}>
                    Ouv: {Number(trade.open_price).toFixed(5)} | Ferm: <span style={{color: '#aaa'}}>{Number(trade.close_price).toFixed(5)}</span>
                  </div>
                </div>

                <div style={{ textAlign: 'right' }}>
                  <div style={{ fontWeight: 'bold', fontSize: '1.1rem', color: trade.profit >= 0 ? '#00e676' : '#ff4444' }}>
                    {trade.profit > 0 ? '+' : ''}{(trade.profit ?? 0).toFixed(2)} €
                  </div>
                  <div style={{ fontSize: '0.8rem', color: '#666' }}>{trade.pips > 0 ? '+' : ''}{trade.pips} pips</div>
                </div>
              </div>
            ))
          )}
          </div>
        </div>
      </div>

      {/* SECTION 3 : PANNEAU DE CONTRÔLE */}
      <div {...a11yToggleProps(() => { triggerHaptic('light'); setIsSettingsOpen(!isSettingsOpen); }, isSettingsOpen)} style={{ display: 'flex', justifyContent: 'flex-start', alignItems: 'center', cursor: 'pointer', borderBottom: '1px solid #333', paddingBottom: '25px', marginBottom: '0px', marginTop: '25px', userSelect: 'none' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px', color: '#ddd' }}>
          <Settings size={20} color="#ffb74d" /> {/* 👈 Icône Orange */}
          <h2 style={{ fontSize: '1.2rem', margin: 0 }}>Panneau de Contrôle</h2>
          {isSettingsOpen ? <ChevronUp size={20} color="#ffb74d" /> : <ChevronDown size={20} color="#ffb74d" />} {/* 👈 Flèches Oranges */}
        </div>
      </div>
      
      <div style={{ display: 'grid', gridTemplateRows: isSettingsOpen ? '1fr' : '0fr', transition: 'grid-template-rows 0.4s ease-out' }}>
        <div style={{ overflow: 'hidden' }}>
          <div style={{ display: 'flex', flexDirection: 'column', gap: '20px', marginBottom: '30px', paddingTop: '5px' }}>
          
          {/* BLOC A : RISQUES & SABR */}
          <div style={{ background: '#1e1e1e', border: '1px solid #333', borderRadius: '12px', padding: '20px' }}>
            {hasRiskSliders && (
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', margin: '0 0 15px 0' }}>
              <h3 style={{ margin: 0, fontSize: '1rem', color: '#aaa' }}>Gestion du Risque (%)</h3>
              {/* (#15) Verrou anti-modif accidentelle des sliders en scrollant sur mobile */}
              <button
                onClick={() => { triggerHaptic('light'); setRisksLocked(!risksLocked); }}
                style={{
                  display: 'flex', alignItems: 'center', gap: '5px', padding: '5px 12px', borderRadius: '20px',
                  border: `1px solid ${risksLocked ? '#555' : '#00e676'}`,
                  background: risksLocked ? '#2a2a2a' : 'rgba(0, 230, 118, 0.1)',
                  color: risksLocked ? '#888' : '#00e676', cursor: 'pointer', fontSize: '0.7rem', fontWeight: 'bold'
                }}
              >
                {risksLocked ? <Lock size={13} /> : <Unlock size={13} />}
                {risksLocked ? 'Verrouillé' : 'Déverrouillé'}
              </button>
            </div>
            )}

            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))', gap: '20px' }}>
              {riskSliders.map((item) => (
                <div key={item.key}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '0.9rem', marginBottom: '5px' }}>
                    <span>{item.label}</span>
                    <strong style={{ color: item.color }}>{(settings[item.key] * 100).toFixed(2)} %</strong>
                  </div>
                  <input
                    type="range" min="0" max="2.0" step="0.01" value={settings[item.key] * 100} disabled={risksLocked}
                    onChange={(e) => updateSetting(item.key, parseFloat(e.target.value) / 100)}
                    style={{ width: '100%', accentColor: item.color, opacity: risksLocked ? 0.4 : 1, cursor: risksLocked ? 'not-allowed' : 'pointer' }}
                  />
                </div>
              ))}
            </div>

            {/* LOTS & RISQUE SABR (#15 : réaligné en grille, mini-descriptions retirées)
                Le trait de séparation + l'espace au-dessus n'ont de sens que s'il y a
                réellement des curseurs de risque avant (pack non-SABR). */}
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))', gap: '15px',
                          marginTop: hasRiskSliders ? '20px' : 0,
                          borderTop: hasRiskSliders ? '1px solid #333' : 'none',
                          paddingTop: hasRiskSliders ? '15px' : 0 }}>
              {[
                { label: 'Lots SABR VIP (lot)', key: 'sabr_lot_vip', channel: 'CANAL SABR_VIP', step: '0.01' },
                { label: 'Lots SABR Free (lot)', key: 'sabr_lot_free', channel: 'CANAL SABR_FREE', step: '0.01' },
                { label: 'Risque SABR autres (%)', key: 'risk_sabr_other', step: '0.1', mult: 100, fallback: 0.006 }
              ].filter((item) => !item.channel || isChannelVisible(item.channel)).map((item) => {
                const raw = settings[item.key] ?? item.fallback ?? 0;
                const displayVal = item.mult ? (raw * 100).toFixed(1) : raw;
                return (
                  <div key={item.key} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', background: '#2a2a2a', padding: '10px', borderRadius: '8px' }}>
                    <span style={{ fontSize: '0.85rem' }}>{item.label}</span>
                    <input
                      type="number" step={item.step} value={displayVal}
                      onChange={(e) => updateSetting(item.key, item.mult ? parseFloat(e.target.value) / item.mult : parseFloat(e.target.value), { debounce: false })}
                      style={{ background: '#121212', border: '1px solid #444', color: '#fff', padding: '5px', width: '70px', borderRadius: '4px', textAlign: 'center' }}
                    />
                  </div>
                );
              })}

              {/* (#pips) Répartition des lots sur la zone SABR : même présentation que les cases
                  ci-dessus (label + contrôle à droite), mais le contrôle est un toggle Manuel/Égale */}
              {(isChannelVisible('CANAL SABR_VIP') || isChannelVisible('CANAL SABR_FREE')) && (
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', background: '#2a2a2a', padding: '10px', borderRadius: '8px' }}>
                  <span style={{ fontSize: '0.85rem' }}>Répartition Zone SABR</span>
                  <div style={{ display: 'flex', background: '#121212', padding: '3px', borderRadius: '7px', border: '1px solid #444' }}>
                    <button
                      onClick={() => updateSetting('sabr_zone_equal_split', false, { debounce: false })}
                      style={{
                        padding: '5px 10px', borderRadius: '5px', border: 'none', cursor: 'pointer',
                        fontWeight: 'bold', fontSize: '0.7rem', transition: '0.2s',
                        background: !settings.sabr_zone_equal_split ? '#333' : 'transparent',
                        color: !settings.sabr_zone_equal_split ? '#00e676' : '#666',
                      }}
                    >
                      Manuel
                    </button>
                    <button
                      onClick={() => updateSetting('sabr_zone_equal_split', true, { debounce: false })}
                      style={{
                        padding: '5px 10px', borderRadius: '5px', border: 'none', cursor: 'pointer',
                        fontWeight: 'bold', fontSize: '0.7rem', transition: '0.2s',
                        background: settings.sabr_zone_equal_split ? '#333' : 'transparent',
                        color: settings.sabr_zone_equal_split ? '#00e676' : '#666',
                      }}
                    >
                      Égale
                    </button>
                  </div>
                </div>
              )}
            </div>

            {/* Mode Manuel : les 3 ordres tiennent sur la même ligne (cellules flex:1, pas de
                wrap) -- Ordre 3 reprend le calcul auto du reliquat (1 - Ordre 1 - Ordre 2) */}
            {(isChannelVisible('CANAL SABR_VIP') || isChannelVisible('CANAL SABR_FREE')) && !settings.sabr_zone_equal_split && (() => {
              const pct1 = settings.sabr_zone_pct1 ?? 0.45;
              const pct2 = settings.sabr_zone_pct2 ?? 0.35;
              const pct3 = Math.max(0, 1 - pct1 - pct2);
              return (
                <div style={{ display: 'flex', gap: '8px', marginTop: '15px' }}>
                  <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', gap: '4px', background: '#2a2a2a', padding: '8px 4px', borderRadius: '8px' }}>
                    <span style={{ fontSize: '0.7rem', color: '#888' }}>O1</span>
                    <input
                      type="number" min="1" max="98" step="1"
                      value={Math.round(pct1 * 100)}
                      onChange={(e) => updateSetting('sabr_zone_pct1', parseFloat(e.target.value) / 100, { debounce: false })}
                      style={{ background: '#121212', border: '1px solid #444', color: '#fff', padding: '4px', width: '40px', borderRadius: '4px', textAlign: 'center' }}
                    />
                    <span style={{ fontSize: '0.7rem', color: '#666' }}>%</span>
                  </div>
                  <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', gap: '4px', background: '#2a2a2a', padding: '8px 4px', borderRadius: '8px' }}>
                    <span style={{ fontSize: '0.7rem', color: '#888' }}>O2</span>
                    <input
                      type="number" min="1" max="98" step="1"
                      value={Math.round(pct2 * 100)}
                      onChange={(e) => updateSetting('sabr_zone_pct2', parseFloat(e.target.value) / 100, { debounce: false })}
                      style={{ background: '#121212', border: '1px solid #444', color: '#fff', padding: '4px', width: '40px', borderRadius: '4px', textAlign: 'center' }}
                    />
                    <span style={{ fontSize: '0.7rem', color: '#666' }}>%</span>
                  </div>
                  <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', gap: '4px', background: '#2a2a2a', padding: '8px 4px', borderRadius: '8px' }}>
                    <span style={{ fontSize: '0.7rem', color: '#888' }}>O3</span>
                    <span style={{ fontSize: '0.8rem', fontWeight: 'bold', color: '#00e676' }}>{Math.round(pct3 * 100)}%</span>
                  </div>
                </div>
              );
            })()}
          </div>

          {/* BLOC B : GESTION DU RISQUE (H) & JOURS
              (#pack) Masqué pour un pack 100% SABR : le multiplicateur heure/jour n'est
              appliqué que dans la branche "lot auto" du backend, jamais aux trades SABR
              (volume toujours forcé) -- l'afficher laissait croire qu'il agissait. */}
          {usesRiskEngine && (
          <div style={{ background: '#1e1e1e', border: '1px solid #333', borderRadius: '12px', padding: '20px' }}>
            <h3 style={{ margin: '0 0 20px 0', fontSize: '1rem', color: '#aaa', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                <Activity size={16}/> Gestion du Risque (h)
              </div>
              <div style={{ background: currentMultiplier >= 1 ? 'rgba(0, 230, 118, 0.1)' : 'rgba(255, 193, 7, 0.1)', padding: '4px 12px', borderRadius: '20px', fontSize: '0.8rem', fontWeight: 'bold', color: currentMultiplier >= 1 ? '#00e676' : '#ffc107', border: `1px solid ${currentMultiplier >= 1 ? '#00e676' : '#ffc107'}` }}>
                Multiplicateur Actuel : x{currentMultiplier.toFixed(2)}
              </div>
            </h3>

            {/* 1. RISQUE JOURNALIER (#16) : jour actuel en compact (jamais de débordement mobile),
                clic -> liste verticale des 7 jours (1 par ligne, plus de grille 7 colonnes qui débordait à 100%) */}
            {!daysExpanded ? (
              <div
                {...a11yToggleProps(() => { triggerHaptic('light'); setDaysExpanded(true); }, false)}
                style={{ background: '#2a2a2a', border: '1px solid #333', borderRadius: '8px', padding: '12px 15px', marginBottom: '25px', cursor: 'pointer', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}
              >
                <span style={{ fontSize: '0.85rem', color: '#888' }}>Aujourd'hui : <strong style={{ color: '#eee' }}>{FULL_DAY_NAMES[pyDay]}</strong></span>
                <strong style={{ color: '#00e676', fontSize: '1rem' }}>{((settings[`day_factor_${pyDay}`] ?? 1) * 100).toFixed(0)}%</strong>
              </div>
            ) : (
              <div style={{ background: '#121212', border: '1px solid #333', borderRadius: '8px', padding: '15px', marginBottom: '25px' }}>
                <div
                  {...a11yToggleProps(() => { triggerHaptic('light'); setDaysExpanded(false); }, true)}
                  style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', cursor: 'pointer', marginBottom: '15px' }}
                >
                  <span style={{ fontSize: '0.8rem', color: '#888' }}>Réglage des 7 jours</span>
                  <ChevronUp size={16} color="#888" />
                </div>
                {FULL_DAY_NAMES.map((dayName, index) => (
                  <div key={dayName} style={{ marginBottom: index < 6 ? '15px' : 0 }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '0.85rem', marginBottom: '5px' }}>
                      <span style={{ color: index === pyDay ? '#00e676' : '#ccc' }}>{dayName}{index === pyDay ? ' (auj.)' : ''}</span>
                      <strong style={{ color: '#00e676' }}>{((settings[`day_factor_${index}`] ?? 1) * 100).toFixed(0)}%</strong>
                    </div>
                    <input
                      type="range" min="0" max="1" step="0.1"
                      value={settings[`day_factor_${index}`] ?? 1}
                      onChange={(e) => updateSetting(`day_factor_${index}`, parseFloat(e.target.value))}
                      style={{ width: '100%', accentColor: '#00e676' }}
                    />
                  </div>
                ))}
              </div>
            )}

            {/* 2. L'HORLOGE INTERACTIVE */}
            <div style={{ background: '#121212', padding: '15px', borderRadius: '10px', marginBottom: '20px', border: '1px solid #222' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '12px' }}>
                <span style={{ fontSize: '0.85rem', color: '#eee' }}>Heures Pleines (100%)</span>
                <span style={{ fontSize: '0.7rem', color: '#666' }}>Clique pour surligner</span>
              </div>
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(12, 1fr)', gap: '4px' }}>
                {Array.from({ length: 24 }).map((_, hour) => {
                  // On lit directement depuis le serveur !
                  const isPrime = (settings.prime_hours || []).includes(hour);
                  return (
                    <div 
                      key={hour}
                      onClick={() => {
                        const currentHours = settings.prime_hours || [];
                        const newHours = isPrime 
                          ? currentHours.filter(h => h !== hour)
                          : [...currentHours, hour];
                        updateSetting('prime_hours', newHours, { debounce: false }); // On envoie le tableau au serveur
                      }}
                      style={{
                        height: '32px', display: 'flex', alignItems: 'center', justifyContent: 'center',
                        fontSize: '0.65rem', fontWeight: 'bold', cursor: 'pointer', borderRadius: '4px',
                        background: isPrime ? '#00e676' : '#2a2a2a', color: isPrime ? '#000' : '#555',
                        transition: 'all 0.1s ease'
                      }}
                    >
                      {hour}h
                    </div>
                  );
                })}
              </div>
            </div>

            {/* 3. INTERRUPTEUR ET CURSEUR HORS-SESSION */}
            <div style={{ background: 'rgba(255, 255, 255, 0.03)', padding: '15px', borderRadius: '10px', border: '1px solid #333' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: settings.time_off_peak > 0 ? '15px' : '0' }}>
                <span style={{ fontSize: '0.9rem' }}>Risque hors session</span>
                
                {/* INTERRUPTEUR (TOGGLE) */}
                <button 
                  onClick={() => updateSetting('time_off_peak', settings.time_off_peak > 0 ? 0 : 0.5, { debounce: false })}
                  style={{
                    background: settings.time_off_peak > 0 ? 'rgba(0, 230, 118, 0.2)' : 'rgba(255, 68, 68, 0.2)',
                    border: `1px solid ${settings.time_off_peak > 0 ? '#00e676' : '#ff4444'}`,
                    color: settings.time_off_peak > 0 ? '#00e676' : '#ff4444',
                    padding: '5px 15px', borderRadius: '20px', cursor: 'pointer', fontWeight: 'bold', fontSize: '0.8rem',
                    transition: '0.2s'
                  }}
                >
                  {settings.time_off_peak > 0 ? 'ON' : 'OFF'}
                </button>
              </div>

              {/* CURSEUR (Visible seulement si ON) */}
              {settings.time_off_peak > 0 && (
                <div style={{ animation: 'fadeIn 0.3s ease-in-out' }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '8px' }}>
                    <span style={{ fontSize: '0.75rem', color: '#888' }}>Niveau de risque autorisé :</span>
                    <strong style={{ color: '#ffc107' }}>{(settings.time_off_peak * 100).toFixed(0)}%</strong>
                  </div>
                  <input 
                    type="range" min="0.1" max="1" step="0.1" 
                    value={settings.time_off_peak} 
                    onChange={(e) => updateSetting('time_off_peak', parseFloat(e.target.value))}
                    style={{ width: '100%', accentColor: '#ffc107' }} 
                  />
                </div>
              )}
            </div>
          </div>
          )}

          {/* BLOC C & D : SECURITE ET MUTE */}
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(300px, 1fr))', gap: '20px' }}>
            
            {/* Securité */}
            <div style={{ background: '#1e1e1e', border: '1px solid #333', borderRadius: '12px', padding: '20px' }}>
              <h3 style={{ margin: '0 0 15px 0', fontSize: '1rem', color: '#aaa' }}>Sécurité & MM</h3>
              {/* (#14) Chaque ligne : label | champ valeur | toggle ON/OFF */}
              {[
                // Max Drawdown reste toujours visible : il agit au niveau du COMPTE, donc
                // aussi sur les trades SABR. (#pack) SL Suiveur / Soft SL en revanche ne
                // touchent jamais SABR (positions déviées avant le bloc trailing) -> masqués
                // pour un pack 100% SABR, comme dans /info.
                { label: 'Max Drawdown (%)', key: 'max_dd', val: settings.max_dd * 100, step: '1', mult: 100,
                  toggleKey: 'drawdown_active', toggleFn: toggleDrawdown, warn: true },
                { label: 'SL Suiveur (pips)', key: 'sl_suiveur', val: settings.sl_suiveur, step: '1', mult: 1,
                  toggleKey: 'sl_suiveur_active', toggleFn: toggleSlSuiveur, riskEngineOnly: true },
                { label: 'Soft SL Vente (%)', key: 'sl_sell', val: settings.sl_sell * 100, step: '1', mult: 100,
                  toggleKey: 'soft_sl_active', toggleFn: toggleSoftSl, riskEngineOnly: true },
                // (#pack) Anti-Doublon SABR : son état était déjà affiché dans /info et poussé
                // au dashboard, mais sans aucun moyen de le changer ailleurs qu'en tapant
                // /doublon_sabr. Pas de valeur numérique -> ligne en ON/OFF seul (noValue).
                { label: 'Anti-Doublon SABR', key: 'sabr_anti_doublon', noValue: true,
                  toggleKey: 'sabr_anti_doublon', toggleFn: toggleSabrDoublon, sabrOnly: true }
              ].filter((item) => (!item.riskEngineOnly || usesRiskEngine)
                                 && (!item.sabrOnly || isChannelVisible('CANAL SABR_VIP')
                                     || isChannelVisible('CANAL SABR_FREE'))).map((item) => {
                const active = settings[item.toggleKey] !== false;
                return (
                  <div key={item.key} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '10px', background: item.warn && !active ? '#3a1f1f' : '#2a2a2a', padding: '10px', borderRadius: '8px', border: item.warn && !active ? '1px solid #ff4444' : '1px solid transparent' }}>
                    <span style={{ fontSize: '0.9rem', flex: 1 }}>
                      {item.label}
                      {item.warn && !active && <span style={{ color: '#ff6b6b', fontSize: '0.7rem', display: 'block' }}>⚠️ Protection désactivée</span>}
                    </span>
                    {!item.noValue && (
                      <input type="number" step={item.step} value={item.val} onChange={(e) => updateSetting(item.key, parseFloat(e.target.value) / item.mult, { debounce: false })} style={{ background: '#121212', border: '1px solid #444', color: '#fff', padding: '5px', width: '60px', borderRadius: '4px', textAlign: 'center', marginRight: '8px' }} />
                    )}
                    <button onClick={item.toggleFn} style={{ padding: '6px 14px', borderRadius: '20px', border: 'none', cursor: 'pointer', fontSize: '0.75rem', fontWeight: 'bold', background: active ? '#00c853' : '#ff4444', color: '#fff', minWidth: '70px' }}>
                      {active ? 'ON 🟢' : 'OFF 🔴'}
                    </button>
                  </div>
                );
              })}
            </div>

            {/* Mute Canaux */}
            <div style={{ background: '#1e1e1e', border: '1px solid #333', borderRadius: '12px', padding: '20px' }}>
              <h3 style={{ margin: '0 0 15px 0', fontSize: '1rem', color: '#aaa', display: 'flex', alignItems: 'center', gap: '5px' }}><BellOff size={16}/> Mute Canaux</h3>
             <div style={{ display: 'flex', flexWrap: 'wrap', gap: '10px' }}>
                {["CANAL MDT_VIP", "CANAL MDT_FREE", "CANAL SABR_VIP", "CANAL SABR_FREE", "CANAL SABR_COPIE", "CANAL SPACE", "CANAL LIGUE", "CANAL PERSO"].filter(isChannelVisible).map(chan => {
                  const isMuted = settings.muted_channels?.includes(chan);
                  return (
                    <button 
                      key={chan} 
                      onClick={() => toggleMute(chan)}
                      style={{ padding: '6px 10px', borderRadius: '20px', border: 'none', cursor: 'pointer', fontSize: '0.7rem', fontWeight: 'bold', background: isMuted ? '#ff4444' : '#2a2a2a', color: isMuted ? '#fff' : '#aaa' }}
                    >
                      {chan.replace('CANAL ', '')} {isMuted ? '🔇' : '🔊'}
                    </button>
                  )
                })}
              </div>
            </div>

          </div>
        </div>
      </div>
      </div>

      {/* SECTION 4 : ANALYSES & STATISTIQUES */}
      <div {...a11yToggleProps(() => { triggerHaptic('light'); setIsStatsOpen(!isStatsOpen); }, isStatsOpen)} style={{ display: 'flex', justifyContent: 'flex-start', alignItems: 'center', cursor: 'pointer', borderBottom: '1px solid #333', paddingBottom: '25px', marginBottom: '0px', marginTop: '25px', userSelect: 'none' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px', color: '#ddd' }}>
          <TrendingUp size={20} color="#00e676" />
          <h2 style={{ fontSize: '1.2rem', margin: 0 }}>Analyses & Statistiques</h2>
          {isStatsOpen ? <ChevronUp size={20} color="#00e676" /> : <ChevronDown size={20} color="#00e676" />}
        </div>
      </div>

      <div style={{ display: 'grid', gridTemplateRows: isStatsOpen ? '1fr' : '0fr', transition: 'grid-template-rows 0.4s ease-out' }}>
        <div style={{ overflow: 'hidden' }}>
          <div style={{ display: 'flex', flexDirection: 'column', gap: '20px', marginBottom: '30px', paddingTop: '5px' }}>

          {/* NAVIGATION TABS */}
          <div style={{ display: 'flex', gap: '10px', background: '#1e1e1e', padding: '6px', borderRadius: '12px', border: '1px solid #333' }}>
            {[
              { id: 'jour', label: "Jour & Semaine" },
              { id: 'canal', label: 'Par Canal' },
              { id: 'global', label: 'Global' }
            ].map((tab) => (
              <button 
                key={tab.id}
                onClick={() => setStatsTab(tab.id)}
                style={{ 
                  flex: 1, padding: '10px', borderRadius: '8px', border: 'none', cursor: 'pointer', 
                  fontWeight: 'bold', fontSize: '0.85rem',
                  background: statsTab === tab.id ? '#333' : 'transparent', 
                  color: statsTab === tab.id ? '#00e676' : '#888', 
                  transition: '0.3s' 
                }}
              >
                {tab.label}
              </button>
            ))}
          </div>

          {/* 1. VUE PERFORMANCE (JOUR VS SEMAINE) */}
          {statsTab === 'jour' && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: '20px' }}>
              
              {/* SÉLECTEUR INTERNE JOUR/SEMAINE */}
              <div style={{ display: 'flex', background: '#121212', padding: '4px', borderRadius: '10px', border: '1px solid #333', width: 'fit-content', margin: '0 auto' }}>
                <button onClick={() => setDayWeekToggle('day')} style={{ padding: '6px 20px', borderRadius: '8px', border: 'none', cursor: 'pointer', fontWeight: 'bold', fontSize: '0.8rem', background: dayWeekToggle === 'day' ? '#333' : 'transparent', color: dayWeekToggle === 'day' ? '#00e676' : '#666', transition: '0.2s' }}>Aujourd'hui</button>
                <button onClick={() => setDayWeekToggle('week')} style={{ padding: '6px 20px', borderRadius: '8px', border: 'none', cursor: 'pointer', fontWeight: 'bold', fontSize: '0.8rem', background: dayWeekToggle === 'week' ? '#333' : 'transparent', color: dayWeekToggle === 'week' ? '#00e676' : '#666', transition: '0.2s' }}>Cette Semaine</button>
              </div>

              {/* On définit quelle source de données utiliser (jour ou semaine) */}
              {(() => {
                // (#60) week_period est un vrai agrégat 7 jours, dédié, indépendant de l'onglet Global
                const data = dayWeekToggle === 'day' ? daily_stats : historicalStats?.week_period;
                if (!data) return <div style={{textAlign:'center', color:'#666'}}>Chargement des données...</div>;

                return (
                  <>
                    <div style={{ display: 'flex', gap: '15px' }}>
                      <div style={{ flex: 1, background: '#1e1e1e', borderRadius: '12px', padding: '20px', border: '1px solid #333', textAlign: 'center' }}>
                        <div style={{ color: '#aaa', fontSize: '0.75rem', textTransform: 'uppercase', marginBottom: '5px' }}>Profit Encaissé</div>
                        <div style={{ fontSize: '1.4rem', fontWeight: 'bold', color: (data.realized_pnl ?? data.profit ?? 0) >= 0 ? '#00e676' : '#ff4444' }}>
                          {(data.realized_pnl ?? data.profit ?? 0).toLocaleString('fr-FR')} €
                        </div>
                      </div>
                      <div style={{ flex: 1, background: '#1e1e1e', borderRadius: '12px', padding: '20px', border: '1px solid #333', textAlign: 'center' }}>
                        <div style={{ color: '#aaa', fontSize: '0.75rem', textTransform: 'uppercase', marginBottom: '5px' }}>Volume Pips</div>
                        {/* (#61) ?? au lieu de || -- un daily_pips légitimement à 0 était traité comme
                            falsy et basculait sur data.pips (souvent absent), affichant une couleur
                            rouge à tort pour une valeur neutre. */}
                        <div style={{ fontSize: '1.4rem', fontWeight: 'bold', color: (data.daily_pips ?? data.pips ?? 0) >= 0 ? '#00e676' : '#ff4444' }}>
                          {(data.daily_pips ?? data.pips ?? 0).toFixed(1)} <span style={{fontSize:'1rem'}}>p</span>
                        </div>
                      </div>
                    </div>

                    {/* Objectifs TP */}
                    <div style={{ background: '#1e1e1e', borderRadius: '12px', padding: '20px', border: '1px solid #333' }}>
                      <h4 style={{ margin: '0 0 15px 0', fontSize: '0.85rem', color: '#00e676', textTransform: 'uppercase', letterSpacing: '1px' }}>Objectifs Validés</h4>
                      <div style={{ display: 'flex', justifyContent: 'space-between', textAlign: 'center' }}>
                        {['TP1', 'TP2', 'TP3', 'TP4'].map((tp, idx) => (
                          <div key={tp}>
                            <div style={{ color: '#666', fontSize: '0.7rem', marginBottom: '5px' }}>{tp}</div>
                            <div style={{ fontSize: '1.1rem', fontWeight: 'bold' }}>{data[`tp${idx+1}_reached`] || 0}</div>
                          </div>
                        ))}
                        <div>
                          <div style={{ color: '#00e676', fontSize: '0.7rem', marginBottom: '5px' }}>FINAL</div>
                          <div style={{ fontSize: '1.1rem', fontWeight: 'bold', color: '#00e676' }}>{data.tp_final || 0}</div>
                        </div>
                      </div>
                    </div>

                    {/* Sorties SL */}
                    <div style={{ background: '#1e1e1e', borderRadius: '12px', padding: '20px', border: '1px solid #333' }}>
                      <h4 style={{ margin: '0 0 15px 0', fontSize: '0.85rem', color: '#ff4444', textTransform: 'uppercase', letterSpacing: '1px' }}>Sécurités & Stop Loss</h4>
                      <div style={{ display: 'flex', justifyContent: 'space-between', textAlign: 'center' }}>
                        <div><div style={{ color: '#666', fontSize: '0.7rem', marginBottom: '5px' }}>FULL</div><div style={{ fontSize: '1.1rem', fontWeight: 'bold', color: '#ff4444' }}>{data.sl_full || 0}</div></div>
                        <div><div style={{ color: '#666', fontSize: '0.7rem', marginBottom: '5px' }}>BE</div><div style={{ fontSize: '1.1rem', fontWeight: 'bold', color: '#ffc107' }}>{data.sl_be || 0}</div></div>
                        <div><div style={{ color: '#666', fontSize: '0.7rem', marginBottom: '5px' }}>+20p</div><div style={{ fontSize: '1.1rem', fontWeight: 'bold' }}>{data.sl_30 || 0}</div></div>
                        <div><div style={{ color: '#666', fontSize: '0.7rem', marginBottom: '5px' }}>+80p</div><div style={{ fontSize: '1.1rem', fontWeight: 'bold' }}>{data.sl_80 || 0}</div></div>
                        <div><div style={{ color: '#666', fontSize: '0.7rem', marginBottom: '5px' }}>+140p</div><div style={{ fontSize: '1.1rem', fontWeight: 'bold' }}>{data.sl_130 || 0}</div></div>
                      </div>
                    </div>

                    {/* Ligne 4 : Les Moyennes d'Exécution */}
                    <div style={{ display: 'flex', justifyContent: 'space-between', background: 'rgba(255,255,255,0.03)', padding: '10px 15px', borderRadius: '8px', fontSize: '0.8rem', color: '#aaa', border: '1px solid #333', marginTop: '5px' }}>
                      <span>📈 Max: {data.pips_max_history?.length ? (data.pips_max_history.reduce((a,b)=>a+b,0)/data.pips_max_history.length).toFixed(1) : 0} pips</span>
                      <span>🏁 Close: {data.pips_close_history?.length ? (data.pips_close_history.reduce((a,b)=>a+b,0)/data.pips_close_history.length).toFixed(1) : 0} pips</span>
                      <span>📉 Min: {data.pips_min_history?.length ? (data.pips_min_history.reduce((a,b)=>a+b,0)/data.pips_min_history.length).toFixed(1) : 0} pips</span>
                    </div>
                  </>
                );
              })()}
            </div>
          )}

          {/* 2. VUE PAR CANAL (Les vraies données) */}
          {statsTab === 'canal' && (() => {
            // (#stats) Source unique : le CSV détaillé du bot, réagrégé sur la période choisie
            // (tous comptes MT5 confondus). "Max" tient lieu de cumul depuis toujours, donc le
            // sélecteur est toujours affiché -- il n'y a plus de mode dégradé sans période.
            const channelData = historicalStats?.channels_period || {};
            return (
            <div style={{ display: 'flex', flexDirection: 'column', gap: '15px' }}>

              <div style={{ display: 'flex', gap: '5px', background: '#1e1e1e', padding: '5px', borderRadius: '8px', border: '1px solid #333' }}>
                {[
                  { id: '14', label: '14j' }, { id: '30', label: '1M' }, { id: '90', label: '3M' },
                  { id: '180', label: '6M' }, { id: '365', label: '1 An' }, { id: 'all', label: 'Max' }
                ].map(p => (
                  <button
                    key={p.id}
                    onClick={() => setChannelPeriod(p.id)}
                    style={{
                      flex: 1, padding: '8px 5px', borderRadius: '6px', border: 'none', cursor: 'pointer',
                      fontSize: '0.75rem', fontWeight: 'bold',
                      background: channelPeriod === p.id ? '#00e676' : 'transparent',
                      color: channelPeriod === p.id ? '#000' : '#888',
                      transition: '0.2s'
                    }}
                  >
                    {p.label}
                  </button>
                ))}
              </div>

              {isLoadingStats && (
                <div style={{ textAlign: 'center', padding: '12px', color: '#00e676', fontSize: '0.82rem', background: 'rgba(0,230,118,0.05)', borderRadius: '8px', border: '1px solid rgba(0,230,118,0.2)' }}>
                  ⏳ Calcul en cours...
                </div>
              )}

              {/* (#stats) Pendant un recalcul on n'affiche AUCUNE carte : la valeur encore
                  presente dans Firestore peut dater d'une version anterieure du bot, et la
                  laisser visible quelques secondes revient a afficher des chiffres faux. */}
              {isLoadingStats ? null : Object.keys(channelData).length > 0 ? (
                Object.entries(channelData)
                .filter(([name]) => isChannelVisible(name)) // (#7) pack du client
                .sort((a, b) => (b[1].profit ?? 0) - (a[1].profit ?? 0)) // Tri par profit décroissant
                .map(([name, data]) => {
                  // (#stats) Moyenne simple pips/trade, calculée côté bot. Fallback sur le même
                  // calcul local pour un exe client pas encore à jour (champ absent).
                  const avgPips = (data.avg_pips !== undefined && data.avg_pips !== null)
                    ? Number(data.avg_pips).toFixed(1)
                    : (data.count > 0 ? (data.pips / data.count).toFixed(1) : "0.0");
                  // Une zone SABR = 1 signal mais 3 positions MT5 : on affiche le nombre de
                  // signaux, et le détail des positions seulement quand les deux diffèrent.
                  const nbPositions = data.positions ?? data.count;
                  const isPositive = (data.profit ?? 0) >= 0;
                  
                  return (
                  <div key={name} style={{ background: '#1e1e1e', borderRadius: '12px', padding: '15px', border: '1px solid #333' }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '12px' }}>
                      <span style={{ fontWeight: 'bold', fontSize: '1rem', color: '#fff' }}>{name.replace('CANAL ', '').replace('_', ' ')}</span>
                      <div style={{ textAlign: 'right' }}>
                        <div style={{ color: isPositive ? '#00e676' : '#ff4444', fontWeight: 'bold', fontSize: '1.2rem' }}>
                          {isPositive ? '+' : ''}{(data.profit ?? 0).toFixed(2)} €
                        </div>
                        <div style={{ fontSize: '0.8rem', color: '#888' }}>{data.pips > 0 ? '+' : ''}{(data.pips ?? 0).toFixed(1)} pips</div>
                      </div>
                    </div>
                    
                    {/* Barre de ratio visuelle */}
                    <div style={{ height: '6px', background: '#121212', borderRadius: '10px', overflow: 'hidden', marginBottom: '10px' }}>
                      <div style={{ 
                        height: '100%', 
                        width: `${Math.min(Math.max((Math.abs(data.profit) / (account.balance * 0.05)) * 100, 5), 100)}%`, 
                        background: isPositive ? 'linear-gradient(90deg, #00c853, #b2ff59)' : 'linear-gradient(90deg, #d50000, #ff5252)',
                        borderRadius: '10px'
                      }}></div>
                    </div>

                    <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '0.75rem', color: '#888' }}>
                      <span>📦 {data.count} Trades{nbPositions !== data.count ? ` (${nbPositions} ordres)` : ''}</span>
                      <span>⚖️ Moy: {avgPips} pips/trade</span>
                    </div>
                  </div>
                )})
              ) : (
                <div style={{ textAlign: 'center', color: '#666', padding: '40px', background: '#1e1e1e', borderRadius: '12px' }}>
                  Aucun trade sur cette période.
                </div>
              )}
            </div>
            );
          })()}

          {/* 3. VUE GLOBALE (Période Dynamique) */}
          {statsTab === 'global' && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: '15px' }}>
              
              {/* SÉLECTEUR DE PÉRIODE */}
              <div style={{ display: 'flex', gap: '5px', background: '#1e1e1e', padding: '5px', borderRadius: '8px', border: '1px solid #333' }}>
                {[
                  { id: '14', label: '14j' },
                  { id: '30', label: '1M' },
                  { id: '90', label: '3M' },
                  { id: '180', label: '6M' },
                  { id: '365', label: '1 An' },
                  { id: 'all', label: 'Max' }
                ].map(p => (
                  <button
                    key={p.id}
                    onClick={() => setGlobalPeriod(p.id)}
                    style={{
                      flex: 1, padding: '8px 5px', borderRadius: '6px', border: 'none', cursor: 'pointer',
                      fontSize: '0.75rem', fontWeight: 'bold',
                      background: globalPeriod === p.id ? '#00e676' : 'transparent',
                      color: globalPeriod === p.id ? '#000' : '#888',
                      transition: '0.2s'
                    }}
                  >
                    {p.label}
                  </button>
                ))}
              </div>

              {/* SPINNER CHARGEMENT */}
              {isLoadingStats && (
                <div style={{ textAlign: 'center', padding: '12px', color: '#00e676', fontSize: '0.82rem', background: 'rgba(0,230,118,0.05)', borderRadius: '8px', border: '1px solid rgba(0,230,118,0.2)', animation: 'textBlink 1s infinite' }}>
                  ⏳ Calcul en cours...
                </div>
              )}

              {/* CARTES DE DONNÉES */}
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))', gap: '15px' }}>
                <div style={{ background: '#1e1e1e', padding: '25px 20px', borderRadius: '12px', textAlign: 'center', border: '1px solid #333' }}>
                  <Calendar size={24} color="#aaa" style={{marginBottom: '10px'}}/>
                  <div style={{ color: '#888', fontSize: '0.75rem', textTransform: 'uppercase', letterSpacing: '1px', marginBottom: '10px' }}>Gain Période</div>
                  <div style={{ fontSize: '1.6rem', fontWeight: 'bold', color: historicalStats?.global_period?.profit >= 0 ? '#00e676' : '#ff4444' }}>
                    {historicalStats?.global_period?.profit?.toLocaleString('fr-FR') || 0} €
                  </div>
                </div>
                
                <div style={{ background: '#1e1e1e', padding: '25px 20px', borderRadius: '12px', textAlign: 'center', border: '1px solid #333' }}>
                  <Award size={24} color="#aaa" style={{marginBottom: '10px'}}/>
                  <div style={{ color: '#888', fontSize: '0.75rem', textTransform: 'uppercase', letterSpacing: '1px', marginBottom: '10px' }}>Taux Réussite</div>
                  <div style={{ fontSize: '1.6rem', fontWeight: 'bold', color: '#00e676' }}>
                    {historicalStats?.global_period?.winrate || 0}%
                  </div>
                </div>
                
                <div style={{ background: '#1e1e1e', padding: '25px 20px', borderRadius: '12px', textAlign: 'center', border: '1px solid #333' }}>
                  <Crosshair size={24} color="#aaa" style={{marginBottom: '10px'}}/>
                  <div style={{ color: '#888', fontSize: '0.75rem', textTransform: 'uppercase', letterSpacing: '1px', marginBottom: '10px' }}>Volume Période</div>
                  <div style={{ fontSize: '1.6rem', fontWeight: 'bold' }}>
                    {historicalStats?.global_period?.total_trades || 0} <span style={{fontSize:'0.9rem', color:'#666'}}>trades</span>
                  </div>
                </div>

                <div style={{ background: '#1e1e1e', padding: '25px 20px', borderRadius: '12px', textAlign: 'center', border: '1px solid #333' }}>
                  <Target size={24} color="#aaa" style={{marginBottom: '10px'}}/>
                  <div style={{ color: '#888', fontSize: '0.75rem', textTransform: 'uppercase', letterSpacing: '1px', marginBottom: '10px' }}>Pips Cumulés</div>
                  <div style={{ fontSize: '1.6rem', fontWeight: 'bold', color: (historicalStats?.global_period?.pips || 0) >= 0 ? '#00e676' : '#ff4444' }}>
                    {(historicalStats?.global_period?.pips || 0).toFixed(1)} <span style={{fontSize:'0.9rem'}}>p</span>
                  </div>
                </div>
              </div>

              {/* (#65) ÉVOLUTION DU PORTEFEUILLE sur la période sélectionnée */}
              <EquityChart points={historicalStats?.equity_curve} loading={isLoadingStats} />
            </div>
          )}

        </div>
      </div>
      </div>

      {/* SECTION 5 : MAILBOX (#29) -- signaler un bug/suggestion, historique + réponses admin.
          Placée ici (sous Analyses & Stats, au-dessus de la ligne rouge) plutôt qu'après
          Contrôle Système, sur demande utilisateur -- seul "Contrôle Système" reste sous la ligne rouge. */}
      <div {...a11yToggleProps(() => { triggerHaptic('light'); setIsFeedbackOpen(!isFeedbackOpen); }, isFeedbackOpen)} style={{ display: 'flex', justifyContent: 'flex-start', alignItems: 'center', cursor: 'pointer', marginTop: '25px', userSelect: 'none' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px', color: '#ddd' }}>
          <MessageCircle size={20} color="#64b5f6" />
          <h2 style={{ fontSize: '1.2rem', margin: 0 }}>Signaler un bug / suggestion</h2>
          {isFeedbackOpen ? <ChevronUp size={20} color="#64b5f6" /> : <ChevronDown size={20} color="#64b5f6" />}
        </div>
      </div>

      <div style={{ display: 'grid', gridTemplateRows: isFeedbackOpen ? '1fr' : '0fr', transition: 'grid-template-rows 0.4s ease-out' }}>
        <div style={{ overflow: 'hidden' }}>
          <div style={{ background: '#1e1e1e', border: '1px solid #333', borderRadius: '12px', padding: '10px', marginTop: '15px' }}>

            <div style={{ display: 'flex', gap: '10px', marginBottom: '12px' }}>
              {[
                { key: 'bug', label: '🐛 Bug' },
                { key: 'suggestion', label: '💡 Suggestion' },
                { key: 'autre', label: '💬 Autre' },
              ].map(({ key, label }) => (
                <button key={key} onClick={() => { triggerHaptic('light'); setFeedbackType(key); }}
                  style={{
                    flex: 1, padding: '10px', borderRadius: '8px', cursor: 'pointer', fontSize: '0.85rem', fontWeight: 'bold',
                    border: `1px solid ${feedbackType === key ? '#64b5f6' : '#444'}`,
                    background: feedbackType === key ? 'rgba(100, 181, 246, 0.15)' : '#2a2a2a',
                    color: feedbackType === key ? '#64b5f6' : '#aaa',
                  }}>
                  {label}
                </button>
              ))}
            </div>

            <textarea
              value={feedbackMessage}
              onChange={(e) => setFeedbackMessage(e.target.value)}
              placeholder="Décris le bug ou ta suggestion..."
              maxLength={1000}
              rows={3}
              style={{ width: '100%', background: '#000', color: '#eee', border: '1px solid #444', borderRadius: '8px', padding: '10px', fontSize: '0.9rem', resize: 'vertical', boxSizing: 'border-box' }}
            />

            <button
              onClick={handleSendFeedback}
              disabled={!feedbackMessage.trim() || feedbackOnCooldown || feedbackSending}
              style={{
                marginTop: '10px', width: '100%', padding: '12px', borderRadius: '8px', border: 'none', cursor: 'pointer', fontWeight: 'bold',
                background: (!feedbackMessage.trim() || feedbackOnCooldown || feedbackSending) ? '#333' : '#64b5f6',
                color: (!feedbackMessage.trim() || feedbackOnCooldown || feedbackSending) ? '#777' : '#000',
              }}>
              {feedbackSending ? 'Envoi...' : feedbackOnCooldown
                ? `Patiente encore ${Math.ceil(feedbackRemainingMs / 60000)} min`
                : 'Envoyer'}
            </button>

            {myFeedback.length > 0 && (
              <div style={{ marginTop: isMyFeedbackOpen ? '20px' : '8px', borderTop: isMyFeedbackOpen ? '1px solid #333' : 'none', paddingTop: isMyFeedbackOpen ? '15px' : '0px' }}>
                <div
                  {...a11yToggleProps(() => { triggerHaptic('light'); setIsMyFeedbackOpen(!isMyFeedbackOpen); }, isMyFeedbackOpen)}
                  style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', cursor: 'pointer', userSelect: 'none' }}
                >
                  <h3 style={{ margin: 0, fontSize: '0.9rem', color: '#aaa' }}>Mes signalements ({myFeedback.length})</h3>
                  {isMyFeedbackOpen ? <ChevronUp size={16} color="#aaa" /> : <ChevronDown size={16} color="#aaa" />}
                </div>
                <div style={{ display: 'grid', gridTemplateRows: isMyFeedbackOpen ? '1fr' : '0fr', transition: 'grid-template-rows 0.4s ease-out' }}>
                <div style={{ overflow: 'hidden' }}>
                <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
                  {myFeedback.map((fb) => (
                    <div key={fb.id} style={{ background: '#2a2a2a', border: '1px solid #383838', borderRadius: '8px', padding: '12px' }}>
                      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '6px' }}>
                        <span style={{ fontSize: '0.8rem', color: '#888' }}>
                          {{ bug: '🐛 Bug', suggestion: '💡 Suggestion', autre: '💬 Autre' }[fb.type] || fb.type}
                          {fb.ticket ? ` — #${fb.ticket}` : ''}
                        </span>
                        <span style={{
                          fontSize: '0.75rem', fontWeight: 'bold', padding: '2px 8px', borderRadius: '10px',
                          color: FEEDBACK_STATUS_BADGE[fb.status]?.color || FEEDBACK_STATUS_BADGE.default.color,
                          background: FEEDBACK_STATUS_BADGE[fb.status]?.bg || FEEDBACK_STATUS_BADGE.default.bg,
                        }}>
                          {FEEDBACK_STATUS_BADGE[fb.status]?.label || FEEDBACK_STATUS_BADGE.default.label}
                        </span>
                      </div>
                      <div style={{ fontSize: '0.85rem', color: '#ddd', whiteSpace: 'pre-wrap' }}>{fb.message}</div>
                      {fb.reply && (
                        <div style={{ marginTop: '8px', paddingTop: '8px', borderTop: '1px dashed #444', fontSize: '0.85rem', color: '#64b5f6' }}>
                          ↳ {fb.reply}
                        </div>
                      )}
                    </div>
                  ))}
                </div>
                </div>
                </div>
              </div>
            )}

          </div>
        </div>
      </div>

      {/* SECTION 6 : ACTIONS CRITIQUES (reste la dernière section, sous la ligne rouge) */}
      <div {...a11yToggleProps(() => { triggerHaptic('heavy'); setIsDangerOpen(!isDangerOpen); }, isDangerOpen)} style={{ display: 'flex', justifyContent: 'flex-start', alignItems: 'center', cursor: 'pointer', borderTop: '2px dashed #ff4444', paddingTop: '25px', marginTop: '25px', userSelect: 'none' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px', color: '#ff4444' }}>
          <AlertTriangle size={20} />
          <h2 style={{ fontSize: '1.2rem', margin: 0 }}>Contrôle Système</h2>
          {isDangerOpen ? <ChevronUp size={20} /> : <ChevronDown size={20} />}
        </div>
      </div>

      <div style={{ display: 'grid', gridTemplateRows: isDangerOpen ? '1fr' : '0fr', transition: 'grid-template-rows 0.4s ease-out' }}>
        <div style={{ overflow: 'hidden' }}>
          <div style={{ background: 'rgba(255, 68, 68, 0.05)', border: '1px solid #ff4444', borderRadius: '12px', padding: '20px', display: 'flex', gap: '15px', flexWrap: 'wrap', marginTop: '15px', marginBottom: '10px' }}>
          <button onClick={handleUnlock} disabled={!!loadingAction} style={{ flex: '1 1 200px', background: '#ffc107', border: 'none', color: '#000', padding: '15px', borderRadius: '8px', cursor: 'pointer', fontWeight: 'bold', display: 'flex', justifyContent: 'center', alignItems: 'center', gap: '8px', opacity: loadingAction === 'unlock' ? 0.6 : 1 }}>
            <Unlock size={18} /> {loadingAction === 'unlock' ? 'EN COURS...' : 'Forcer Déverrouillage DD'}
          </button>
          <button onClick={handleKill} disabled={!!loadingAction} style={{ flex: '1 1 200px', background: '#000', border: '1px solid #333', color: '#ff4444', padding: '15px', borderRadius: '8px', cursor: 'pointer', fontWeight: 'bold', display: 'flex', justifyContent: 'center', alignItems: 'center', gap: '8px', opacity: loadingAction === 'kill' ? 0.6 : 1 }}>
            <Power size={18} /> {loadingAction === 'kill' ? 'EXTINCTION...' : 'ARRÊT DU SERVEUR'}
          </button>
        </div>
      </div>
      </div>
    </div>
  )
}

export default App