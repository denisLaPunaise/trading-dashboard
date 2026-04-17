import { useState, useEffect, useRef } from 'react'
import axios from 'axios'
import { Activity, Wallet, TrendingUp, TrendingDown, Settings, XCircle, Shield, ChevronDown, ChevronUp, Briefcase, AlertTriangle, Unlock, Power, BellOff, Target, Award, Crosshair, Calendar } from 'lucide-react'
import './App.css'

// 🚨 REMET TON URL CLOUDFLARE ICI
const API_URL = "https://icing-jolly-drapery.ngrok-free.dev"

axios.defaults.headers.common['ngrok-skip-browser-warning'] = 'true';

// 🛡️ LE VIGILE REACT : On accroche le badge VIP juste avant chaque envoi
axios.interceptors.request.use((config) => {
  const tgInitData = window?.Telegram?.WebApp?.initData || "";
  config.headers['x-telegram-init-data'] = tgInitData;
  config.headers['ngrok-skip-browser-warning'] = 'true';
  return config;
});

// --- COMPOSANT ODOMÈTRE (ÉTAPE 5) ---
const AnimatedNumber = ({ value, duration = 800, prefix = '' }) => {
  const [displayValue, setDisplayValue] = useState(value);

  useEffect(() => {
    let startTimestamp = null;
    const startValue = displayValue;
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

function App() {
  const [dashboardData, setDashboardData] = useState(null)
  const [error, setError] = useState(null)
  const isUpdating = useRef(false)
  const updateTimeout = useRef(null)
  
  const [isTradesOpen, setIsTradesOpen] = useState(true)
  const [isSettingsOpen, setIsSettingsOpen] = useState(true)
  const [isDangerOpen, setIsDangerOpen] = useState(false)

  const [isClosedTradesOpen, setIsClosedTradesOpen] = useState(false) // Fermé par défaut pour ne pas polluer l'écran

  // --- ÉTAT DE LA FENÊTRE DE CONFIRMATION ---
  const [confirmModal, setConfirmModal] = useState({ isOpen: false, message: '', onConfirm: null, type: 'warning' })

  // --- ÉTATS POUR LES ANIMATIONS (ÉTAPE 1 & 2) ---
  const [displayedTrades, setDisplayedTrades] = useState([]);
  const [pnlPulse, setPnlPulse] = useState(null); // 'gain' ou 'loss'
  const prevPnl = useRef(0);
  const initialLoad = useRef(true);

  // --- OPTIMISATIONS TELEGRAM (POINTS 2B & 3B) ---
  const [isFetching, setIsFetching] = useState(false);

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
    } catch (e) {
      // Si ça échoue (ex: navigateur PC), on ignore silencieusement pour ne pas crasher l'app
      console.log("Haptic feedback non disponible.");
    }
  };

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
  const [dayWeekToggle, setDayWeekToggle] = useState('day'); // 'day' ou 'week'

  // Fonction pour charger les stats depuis l'API
  const fetchStats = async () => {
    try {
      // 👈 NOUVEAU : On envoie ?days=30, ?days=all, etc.
      const res = await axios.get(`${API_URL}/api/stats?days=${globalPeriod}`);
      setHistoricalStats(res.data);
    } catch (err) { console.error("Erreur stats", err); }
  };

  // 👈 NOUVEAU : React recharge les stats si on change de période
  useEffect(() => {
    if (isStatsOpen) {
      const period = statsTab === 'global' ? globalPeriod : (dayWeekToggle === 'week' ? 'week' : '1');
      axios.get(`${API_URL}/api/stats?days=${period}`)
           .then(res => setHistoricalStats(res.data))
           .catch(err => console.error(err));
    }
  }, [isStatsOpen, globalPeriod, statsTab, dayWeekToggle]);

const fetchData = async () => {
    // Si la page est cachée (arrière-plan du téléphone), on ne lance pas la requête pour économiser la batterie
    if (document.hidden) return; 

    try {
      setIsFetching(true); // Déclenche l'animation du point vert
      const response = await axios.get(`${API_URL}/api/dashboard`)
      if (!isUpdating.current) setDashboardData(response.data)
      setError(null)
    } catch (err) {
      setError("Impossible de joindre le VPS. Le tunnel est allumé ?")
    } finally {
      setTimeout(() => setIsFetching(false), 300); // Arrête l'animation après un bref délai
    }
  }

  useEffect(() => {
    fetchData()
    // Optimisation : On utilise setInterval, mais fetchData vérifiera 'document.hidden'
    const interval = setInterval(fetchData, 3000)
    
    // Écouteur pour forcer un rafraîchissement immédiat quand on revient sur l'application
    const handleVisibilityChange = () => { if (!document.hidden) fetchData(); };
    document.addEventListener("visibilitychange", handleVisibilityChange);

    return () => {
      clearInterval(interval);
      document.removeEventListener("visibilitychange", handleVisibilityChange);
    };
  }, [])

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

  // --- ACTIONS (Désormais branchées sur la Modal) ---
  const handleCloseTrade = (ticketId) => {
    requestConfirm(`Es-tu sûr de vouloir FERMER le trade #${ticketId} ?`, async () => {
      try { await axios.post(`${API_URL}/api/close_trade`, { ticket: ticketId }); fetchData() } catch (err) {}
    }, 'warning')
  }

  const handleBeTrade = (ticketId) => {
    requestConfirm(`Es-tu sûr de vouloir sécuriser (BE) le trade #${ticketId} ?`, async () => {
      try { await axios.post(`${API_URL}/api/be_trade`, { ticket: ticketId }); fetchData() } catch (err) {}
    }, 'warning')
  }

  const handleSuivre = (ticketId) => {
    requestConfirm(`Veux-tu que le bot prenne le contrôle de ce trade #${ticketId} ?`, async () => {
      try { await axios.post(`${API_URL}/api/suivre`, { ticket: ticketId }); fetchData() } catch (err) {}
    }, 'warning')
  }

  const handlePanic = () => {
    requestConfirm("🚨 ALERTE ROUGE : Veux-tu VRAIMENT fermer TOUTES les positions en cours ?", async () => {
      try { await axios.post(`${API_URL}/api/panic`); fetchData() } catch (err) {}
    }, 'danger')
  }

  const handleKill = () => {
    requestConfirm("💀 ATTENTION : Le bot va s'éteindre. Tu devras te reconnecter au VPS pour le rallumer. Confirmer ?", async () => {
      try { await axios.post(`${API_URL}/api/kill`); alert("Serveur éteint avec succès."); } catch (err) {}
    }, 'danger')
  }

  const handleUnlock = () => {
    requestConfirm("⚠️ Es-tu sûr de vouloir forcer la reprise du trading après un Drawdown ?", async () => {
      try { await axios.post(`${API_URL}/api/unlock`); fetchData() } catch (err) {}
    }, 'warning')
  }

// 1. Fonction générale pour les curseurs (Jours, Risques, etc.) avec Rollback
  const updateSetting = async (key, value) => {
    // 1. On lève le bouclier pour bloquer le rafraîchissement auto
    isUpdating.current = true;
    if (updateTimeout.current) clearTimeout(updateTimeout.current);

    // 📸 NOUVEAU : On sauvegarde l'état actuel en cas de crash
    const previousSettings = { ...dashboardData.settings };

    // 2. Mise à jour visuelle immédiate (Anti-clignotement)
    setDashboardData(prev => {
      if (!prev) return prev;
      return { 
        ...prev, 
        settings: { 
          ...prev.settings, 
          [key]: value 
        } 
      };
    });

    // 3. Envoi au VPS
    try { 
      await axios.post(`${API_URL}/api/update_setting`, { key, value }); 
    } catch (err) { 
      console.error("Erreur de sauvegarde", err); 
      triggerHaptic('error'); // Petite vibration d'erreur si disponible
      
      // 🚨 NOUVEAU : ROLLBACK ! Si échec, on remet l'ancienne valeur
      setDashboardData(prev => {
        if (!prev) return prev;
        return { 
          ...prev, 
          settings: previousSettings // On restaure la photo de sauvegarde
        };
      });
      // Optionnel : un petit message pour prévenir l'utilisateur
      // alert("Connexion perdue. Le réglage n'a pas été sauvegardé.");
    }

    // 4. On relance le chronomètre : le bouclier se baisse 1.5s APRÈS ton dernier clic
    updateTimeout.current = setTimeout(() => { 
      isUpdating.current = false; 
    }, 1500);
  }

  const toggleMute = async (channelName) => {
    try { await axios.post(`${API_URL}/api/toggle_mute`, { value: channelName }); fetchData() } catch (err) {}
  }

  // --- RENDU ---
  if (error) return <div style={{ color: '#ff4444', textAlign: 'center', padding: '50px', background: '#121212', height: '100vh' }}>{error}</div>

  // --- ÉCRAN DE DÉMARRAGE (SPLASH SCREEN) ---
  if (!dashboardData) {
    return (
      <div style={{ width: '800px', maxWidth: '100%', margin: '0 auto', padding: '20px', backgroundColor: '#121212', minHeight: '100vh', display: 'flex', flexDirection: 'column' }}>
        
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
            CONNEXION AU VPS...
          </p>
        </div>
      </div>
    );
  }

const { account, daily_stats, settings, active_trades, closed_trades = [] } = dashboardData

  // 1. On déclare d'abord la balance
  const balanceMinuit = account.balance - daily_stats.realized_pnl;
  // Détection du mode Panic (réel du backend ou via le bouton de test)
  const isPanicMode = settings.security_triggered;
  // 2. ENSUITE on calcule le risque en utilisant la balance
  const ddLimit = balanceMinuit * settings.max_dd;
  const currentLoss = daily_stats.total_pnl < 0 ? Math.abs(daily_stats.total_pnl) : 0;
  const riskPercent = (currentLoss / ddLimit) * 100;
  const isNearDD = riskPercent >= 80;

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
        .btn-interactive { transition: all 0.2s ease; }
        .btn-interactive:hover, .btn-interactive.force-hover { transform: translateY(-2px) scale(1.05); filter: brightness(1.2); box-shadow: 0 4px 12px rgba(255,255,255,0.1); }
      `}</style>

      {/* --- MODAL DE CONFIRMATION --- */}
      {confirmModal.isOpen && (
        <div style={{ position: 'fixed', top: 0, left: 0, right: 0, bottom: 0, backgroundColor: 'rgba(0,0,0,0.7)', zIndex: 1000, display: 'flex', justifyContent: 'center', alignItems: 'center', animation: 'fadeIn 0.2s ease-out forwards' }}>
          <div style={{ background: '#1e1e1e', border: `1px solid ${confirmModal.type === 'danger' ? '#ff4444' : '#ffc107'}`, borderRadius: '15px', padding: '25px', maxWidth: '400px', width: '90%', textAlign: 'center', boxShadow: '0 10px 40px rgba(0,0,0,0.8)', animation: 'popIn 0.2s ease-out' }}>
            <div style={{ color: confirmModal.type === 'danger' ? '#ff4444' : '#ffc107', marginBottom: '15px', display: 'flex', justifyContent: 'center' }}>
              <AlertTriangle size={48} />
            </div>
            <h3 style={{ margin: '0 0 15px 0', fontSize: '1.2rem' }}>
              {confirmModal.type === 'danger' ? 'Contrôle Système' : 'Confirmation Requise'}
            </h3>
            <p style={{ color: '#ccc', fontSize: '1rem', lineHeight: '1.5', marginBottom: '25px' }}>
              {confirmModal.message}
            </p>
            <div style={{ display: 'flex', gap: '15px' }}>
              <button onClick={() => setConfirmModal({ ...confirmModal, isOpen: false })} style={{ flex: 1, padding: '12px', background: 'transparent', border: '1px solid #555', color: '#fff', borderRadius: '8px', cursor: 'pointer', fontWeight: 'bold', transition: '0.2s' }}>
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
      {isPanicMode && (
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
          <Activity size={24} color="#00e676" /> Dashboard {settings.security_triggered && <span style={{color: '#ff4444', fontSize:'0.9rem'}}>(SÉCURITÉ DD)</span>}
        </h1>
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px', color: '#aaa', fontSize: '0.9rem' }}>
          <span style={{ 
            height: '10px', width: '10px', backgroundColor: '#00e676', borderRadius: '50%', display: 'inline-block', 
            boxShadow: '0 0 8px #00e676',
            animation: isFetching ? 'fetchPulse 0.5s ease-out' : 'none' /* Animation activée lors du fetch */
          }}></span>
          Actif
        </div>
      </header>

      {/* SECTION 1 : STATS GLOBALES */}
      <div style={{ display: 'flex', gap: '10px', marginBottom: '15px', width: '100%' }}>
        
        {/* CASE BALANCE */}
        <div style={{ flex: 1, minWidth: 0, padding: '15px', background: '#1e1e1e', border: '1px solid #333', borderRadius: '12px', display: 'flex', flexDirection: 'column', minHeight: '130px' }}>
          <h3 style={{ margin: 0, color: '#aaa', fontSize: '0.75rem', display: 'flex', alignItems: 'center', gap: '5px', whiteSpace: 'nowrap' }}>
            <Wallet size={14} color="#aaa"/> Balance
          </h3>
          <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
            <h2 style={{ fontSize: '1.4rem', margin: 0, whiteSpace: 'nowrap' }}>
              <AnimatedNumber value={account.balance} /> €
            </h2>
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', fontSize: '0.7rem', color: '#666', borderTop: '1px solid rgba(255,255,255,0.05)', paddingTop: '8px', marginTop: '5px' }}>
            <span>Eq : <strong>{account.equity.toLocaleString('fr-FR')} €</strong></span>
            <span>Réf : <strong>{balanceMinuit.toLocaleString('fr-FR')} €</strong></span>
          </div>
        </div>

        {/* CASE P&L DU JOUR (ENCAISSÉ) */}
        <div style={{ 
          flex: 1, minWidth: 0, padding: '15px', 
          background: daily_stats.realized_pnl >= 0 ? 'rgba(0, 230, 118, 0.05)' : 'rgba(255, 68, 68, 0.05)', 
          border: `1px solid ${daily_stats.realized_pnl >= 0 ? '#00e676' : '#ff4444'}`, 
          borderRadius: '12px', display: 'flex', flexDirection: 'column', minHeight: '130px',
          animation: pnlPulse === 'gain' ? 'pulseGain 0.5s ease-out' : pnlPulse === 'loss' ? 'pulseLoss 0.5s ease-out' : 'none'
        }}>
          <h3 style={{ margin: 0, color: daily_stats.realized_pnl >= 0 ? '#00e676' : '#ff4444', fontSize: '0.75rem', display: 'flex', alignItems: 'center', gap: '5px', whiteSpace: 'nowrap' }}>
            {daily_stats.realized_pnl >= 0 ? <TrendingUp size={14}/> : <TrendingDown size={14}/>} Profit
          </h3>
          <div style={{ flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center' }}>
            <h2 style={{ fontSize: '1.4rem', margin: 0, color: daily_stats.realized_pnl >= 0 ? '#00e676' : '#ff4444', whiteSpace: 'nowrap' }}>
              <AnimatedNumber value={daily_stats.realized_pnl} prefix={daily_stats.realized_pnl > 0 ? '+' : ''} /> €
            </h2>
            <span style={{ fontSize: '0.8rem', fontWeight: 'bold', color: daily_stats.realized_pnl >= 0 ? '#00e676' : '#ff4444', opacity: 0.8 }}>
              ({daily_stats.realized_pnl > 0 ? '+' : ''}{(balanceMinuit > 0 ? (daily_stats.realized_pnl / balanceMinuit) * 100 : 0).toFixed(2)}%)
            </span>
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', fontSize: '0.7rem', color: '#666', borderTop: '1px solid rgba(255,255,255,0.05)', paddingTop: '8px', marginTop: '5px' }}>
            <span>Flot : <strong style={{ color: daily_stats.floating_pnl >= 0 ? '#00e676' : '#ff4444' }}>{daily_stats.floating_pnl.toLocaleString('fr-FR')} €</strong></span>
            <span>Total : <strong style={{ color: daily_stats.total_pnl >= 0 ? '#00e676' : '#ff4444' }}>{daily_stats.total_pnl.toLocaleString('fr-FR')} €</strong></span>
          </div>
        </div>
      </div>

      {/* BOUTON PANIC CENTRÉ SOUS LES STATS */}
      <div style={{ textAlign: 'center', marginBottom: '30px' }}>
        <button 
          onClick={handlePanic}
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
            animation: isNearDD ? 'radioactive 3s infinite alternate' : 'none'
          }}
        >
          <AlertTriangle size={16} /> TOUT COUPER
        </button>
      </div>

      {/* SECTION 2 : TRADES ACTIFS */}
      <div onClick={() => { triggerHaptic('light'); setIsTradesOpen(!isTradesOpen); }} style={{ display: 'flex', justifyContent: 'flex-start', alignItems: 'center', cursor: 'pointer', borderBottom: '1px solid #333', paddingBottom: '25px', marginBottom: '0px', marginTop: '30px', userSelect: 'none' }}>
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
            displayedTrades.map((trade, index) => (
              <div key={trade.ticket} style={{ 
                background: trade.managed ? '#1e1e1e' : 'rgba(33, 150, 243, 0.1)', 
                border: `1px solid ${trade.managed ? '#333' : '#2196f3'}`, 
                padding: (trade.isClosing || isPanicMode) ? '0px 15px' : '15px', 
                borderRadius: '10px', display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '15px',
                overflow: 'hidden',
                animation: isPanicMode ? `cascadeClose 1.2s ease-in-out forwards ${2 + (index * 0.2)}s` : trade.isClosing ? (trade.profit >= 0 ? 'slideOutGain 0.5s forwards' : 'slideOutLoss 0.5s forwards') : 'fadeIn 0.3s ease-out'              }}>
                <div style={{ flex: '1 1 250px' }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '10px', marginBottom: '8px' }}>
                    <span style={{ padding: '3px 8px', borderRadius: '4px', fontSize: '0.8rem', fontWeight: 'bold', background: trade.type === 'BUY' ? 'rgba(0, 230, 118, 0.2)' : 'rgba(255, 68, 68, 0.2)', color: trade.type === 'BUY' ? '#00e676' : '#ff4444' }}>{trade.type}</span>
                    <strong style={{ fontSize: '1.1rem' }}>{trade.symbol}</strong>
                    <span style={{ color: '#888', fontSize: '0.9rem' }}>{Number(trade.volume).toFixed(2)} lots ({trade.open_time})</span>
                    {!trade.managed && <span style={{ fontSize: '0.7rem', background: '#2196f3', padding: '2px 6px', borderRadius: '4px', color: 'white', fontWeight: 'bold' }}>NON SUIVI</span>}
                  </div>
                  <div style={{ fontSize: '0.85rem', color: '#aaa', textAlign: 'left', lineHeight: '1.6' }}>
                    Ouv: {Number(trade.open_price).toFixed(5)} | Actuel: <span style={{ color: '#fff' }}>{Number(trade.current_price).toFixed(5)}</span><br />
                    SL: {trade.sl > 0 ? Number(trade.sl).toFixed(5) : "Aucun"} | TP: {trade.tp > 0 ? Number(trade.tp).toFixed(5) : "Aucun"}
                  </div>
                </div>

                <div style={{ textAlign: 'right', display: 'flex', alignItems: 'center', gap: '20px', flex: '1 1 auto', justifyContent: 'flex-end' }}>
                  <div style={{ textAlign: 'right' }}>
                    <div style={{ fontWeight: 'bold', fontSize: '1.2rem', color: trade.profit >= 0 ? '#00e676' : '#ff4444' }}>{trade.profit > 0 ? '+' : ''}{trade.profit.toFixed(2)} €</div>
                    <div style={{ fontSize: '0.85rem', color: '#888' }}>{trade.pips > 0 ? '+' : ''}{trade.pips} pips</div>
                  </div>
                  
                  {/* BOUTONS ACTIONS */}
                  <div style={{ display: 'flex', gap: '8px' }}>
                    {!trade.managed ? (
                      <button onClick={() => handleSuivre(trade.ticket)} className="btn-interactive" style={{ background: '#2196f3', border: 'none', color: 'white', padding: '8px 15px', borderRadius: '8px', cursor: 'pointer', fontWeight: 'bold' }}>Suivre</button>
                    ) : (
                      <>
                        <button onClick={() => handleBeTrade(trade.ticket)} className="btn-interactive" style={{ background: 'rgba(255, 193, 7, 0.1)', border: '1px solid #ffc107', color: '#ffc107', padding: '8px', borderRadius: '8px', cursor: 'pointer', display: 'flex', alignItems: 'center', gap: '5px' }}><Shield size={18} /> BE</button>
                        <button onClick={() => handleCloseTrade(trade.ticket)} className="btn-interactive" style={{ background: 'rgba(255, 68, 68, 0.1)', border: '1px solid #ff4444', color: '#ff4444', padding: '8px', borderRadius: '8px', cursor: 'pointer', display: 'flex', alignItems: 'center', gap: '5px' }}><XCircle size={18} /> Close</button>
                      </>
                    )}
                  </div>
                </div>
              </div>
            ))
          )}
        </div>
      </div>
      </div>

      {/* SECTION 2 BIS : TRADES FERMÉS */}
      <div onClick={() => { triggerHaptic('light'); setIsClosedTradesOpen(!isClosedTradesOpen); }} style={{ display: 'flex', justifyContent: 'flex-start', alignItems: 'center', cursor: 'pointer', borderBottom: '1px solid #333', paddingBottom: '25px', marginBottom: '0px', marginTop: '30px', userSelect: 'none' }}>
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
              <div key={trade.ticket || index} style={{ 
                background: '#1a1a1a', 
                borderLeft: `4px solid ${trade.profit >= 0 ? '#00e676' : '#ff4444'}`, 
                padding: '12px 15px', 
                borderRadius: '8px', display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '15px'
              }}>
                <div style={{ flex: '1 1 200px' }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '10px', marginBottom: '5px' }}>
                    <span style={{ padding: '2px 6px', borderRadius: '4px', fontSize: '0.7rem', fontWeight: 'bold', background: trade.type === 'BUY' ? 'rgba(0, 230, 118, 0.1)' : 'rgba(255, 68, 68, 0.1)', color: trade.type === 'BUY' ? '#00e676' : '#ff4444' }}>{trade.type}</span>
                    <strong style={{ fontSize: '1rem', color: '#ccc' }}>{trade.symbol}</strong>
                    <span style={{ color: '#666', fontSize: '0.8rem' }}>{trade.volume} lots ({trade.open_time})</span>
                  </div>
                  <div style={{ fontSize: '0.75rem', color: '#777' }}>
                    Ouv: {Number(trade.open_price).toFixed(5)} | Ferm: <span style={{color: '#aaa'}}>{Number(trade.close_price).toFixed(5)}</span>
                  </div>
                </div>

                <div style={{ textAlign: 'right' }}>
                  <div style={{ fontWeight: 'bold', fontSize: '1.1rem', color: trade.profit >= 0 ? '#00e676' : '#ff4444' }}>
                    {trade.profit > 0 ? '+' : ''}{trade.profit.toFixed(2)} €
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
      <div onClick={() => { triggerHaptic('light'); setIsSettingsOpen(!isSettingsOpen); }} style={{ display: 'flex', justifyContent: 'flex-start', alignItems: 'center', cursor: 'pointer', borderBottom: '1px solid #333', paddingBottom: '25px', marginBottom: '0px', marginTop: '25px', userSelect: 'none' }}>
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
            <h3 style={{ margin: '0 0 15px 0', fontSize: '1rem', color: '#aaa' }}>Gestion du Risque (%)</h3>
            
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))', gap: '20px' }}>
              {[
                { label: 'Or (Global)', key: 'risk_gold', color: '#ffd700' },
                { label: 'Indices (Global)', key: 'risk_indices', color: '#4da8da' },
                { label: 'Devises (Global)', key: 'risk_other', color: '#ff8a65' },
                { label: 'MDT VIP', key: 'risk_mdt_vip', color: '#a5d6a7' },
                { label: 'MDT Free', key: 'risk_mdt_free', color: '#81c784' },
                { label: 'Space', key: 'risk_space', color: '#ce93d8' },
                { label: 'Ligue', key: 'risk_ligue', color: '#ffb74d' },
                { label: 'Perso', key: 'risk_perso', color: '#90caf9' }
              ].map((item) => (
                <div key={item.key}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '0.9rem', marginBottom: '5px' }}>
                    <span>{item.label}</span>
                    <strong style={{ color: item.color }}>{(settings[item.key] * 100).toFixed(2)} %</strong>
                  </div>
                  <input type="range" min="0" max="2.0" step="0.01" value={settings[item.key] * 100} onChange={(e) => updateSetting(item.key, parseFloat(e.target.value) / 100)} style={{ width: '100%', accentColor: item.color }} />
                </div>
              ))}
            </div>
            
            {/* LOTS SABR */}
            <div style={{ display: 'flex', gap: '20px', marginTop: '20px', borderTop: '1px solid #333', paddingTop: '15px' }}>
               {[
                { label: 'Lots SABR VIP', key: 'sabr_lot_vip' },
                { label: 'Lots SABR Free', key: 'sabr_lot_free' }
               ].map((item) => (
                 <div key={item.key} style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
                    <span style={{ fontSize: '0.9rem' }}>{item.label}</span>
                    <input type="number" step="0.01" value={settings[item.key]} onChange={(e) => updateSetting(item.key, parseFloat(e.target.value))} style={{ background: '#2a2a2a', border: '1px solid #444', color: '#fff', padding: '5px', width: '70px', borderRadius: '4px', textAlign: 'center' }} />
                 </div>
               ))}
            </div>
          </div>

          {/* BLOC B : GESTION DU RISQUE (H) & JOURS */}
          <div style={{ background: '#1e1e1e', border: '1px solid #333', borderRadius: '12px', padding: '20px' }}>
            <h3 style={{ margin: '0 0 20px 0', fontSize: '1rem', color: '#aaa', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                <Activity size={16}/> Gestion du Risque (h)
              </div>
              <div style={{ background: currentMultiplier >= 1 ? 'rgba(0, 230, 118, 0.1)' : 'rgba(255, 193, 7, 0.1)', padding: '4px 12px', borderRadius: '20px', fontSize: '0.8rem', fontWeight: 'bold', color: currentMultiplier >= 1 ? '#00e676' : '#ffc107', border: `1px solid ${currentMultiplier >= 1 ? '#00e676' : '#ffc107'}` }}>
                Multiplicateur Actuel : x{currentMultiplier.toFixed(2)}
              </div>
            </h3>

            {/* 1. LES BULLES JOURNALIÈRES */}
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(7, 1fr)', gap: '8px', marginBottom: '25px' }}>
              {['Lun', 'Mar', 'Mer', 'Jeu', 'Ven', 'Sam', 'Dim'].map((day, index) => (
                <div key={day} style={{ background: '#2a2a2a', padding: '10px 5px', borderRadius: '8px', textAlign: 'center', border: '1px solid #333' }}>
                  <div style={{ fontSize: '0.7rem', fontWeight: 'bold', marginBottom: '5px', color: '#888' }}>{day}</div>
                  <div style={{ color: '#00e676', fontSize: '0.85rem', fontWeight: 'bold', marginBottom: '5px' }}>
                    {((settings[`day_factor_${index}`] ?? 1) * 100).toFixed(0)}%
                  </div>
                  <input 
                    type="range" min="0" max="1" step="0.1" 
                    value={settings[`day_factor_${index}`] ?? 1} 
                    onChange={(e) => updateSetting(`day_factor_${index}`, parseFloat(e.target.value))}
                    style={{ width: '100%', accentColor: '#00e676', height: '4px' }} 
                  />
                </div>
              ))}
            </div>

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
                        updateSetting('prime_hours', newHours); // On envoie le tableau au serveur
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
                  onClick={() => updateSetting('time_off_peak', settings.time_off_peak > 0 ? 0 : 0.5)}
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

          {/* BLOC C & D : SECURITE ET MUTE */}
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(300px, 1fr))', gap: '20px' }}>
            
            {/* Securité */}
            <div style={{ background: '#1e1e1e', border: '1px solid #333', borderRadius: '12px', padding: '20px' }}>
              <h3 style={{ margin: '0 0 15px 0', fontSize: '1rem', color: '#aaa' }}>Sécurité & MM</h3>
              {[
                { label: 'Max Drawdown (%)', key: 'max_dd', val: settings.max_dd * 100, step: '1', mult: 100 },
                { label: 'SL Suiveur (pips)', key: 'sl_suiveur', val: settings.sl_suiveur, step: '1', mult: 1 },
                { label: 'Soft SL Vente (%)', key: 'sl_sell', val: settings.sl_sell * 100, step: '1', mult: 100 }
              ].map((item) => (
                 <div key={item.key} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '10px', background: '#2a2a2a', padding: '10px', borderRadius: '8px' }}>
                    <span style={{ fontSize: '0.9rem' }}>{item.label}</span>
                    <input type="number" step={item.step} value={item.val} onChange={(e) => updateSetting(item.key, parseFloat(e.target.value) / item.mult)} style={{ background: '#121212', border: '1px solid #444', color: '#fff', padding: '5px', width: '60px', borderRadius: '4px', textAlign: 'center' }} />
                 </div>
              ))}
            </div>

            {/* Mute Canaux */}
            <div style={{ background: '#1e1e1e', border: '1px solid #333', borderRadius: '12px', padding: '20px' }}>
              <h3 style={{ margin: '0 0 15px 0', fontSize: '1rem', color: '#aaa', display: 'flex', alignItems: 'center', gap: '5px' }}><BellOff size={16}/> Mute Canaux</h3>
             <div style={{ display: 'flex', flexWrap: 'wrap', gap: '10px' }}>
                {["CANAL MDT_VIP", "CANAL MDT_FREE", "CANAL SABR_VIP", "CANAL SABR_FREE", "CANAL SABR_COPIE", "CANAL SPACE", "CANAL LIGUE", "CANAL PERSO"].map(chan => {
                  const isMuted = settings.muted_channels?.includes(chan);
                  return (
                    <button 
                      key={chan} 
                      onClick={() => toggleMute(chan)}
                      style={{ padding: '8px 12px', borderRadius: '20px', border: 'none', cursor: 'pointer', fontSize: '0.8rem', fontWeight: 'bold', background: isMuted ? '#ff4444' : '#2a2a2a', color: isMuted ? '#fff' : '#aaa' }}
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
      <div onClick={() => { triggerHaptic('light'); setIsStatsOpen(!isStatsOpen); }} style={{ display: 'flex', justifyContent: 'flex-start', alignItems: 'center', cursor: 'pointer', borderBottom: '1px solid #333', paddingBottom: '25px', marginBottom: '0px', marginTop: '25px', userSelect: 'none' }}>
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
                const data = dayWeekToggle === 'day' ? daily_stats : historicalStats?.global_period;
                if (!data) return <div style={{textAlign:'center', color:'#666'}}>Chargement des données...</div>;

                return (
                  <>
                    <div style={{ display: 'flex', gap: '15px' }}>
                      <div style={{ flex: 1, background: '#1e1e1e', borderRadius: '12px', padding: '20px', border: '1px solid #333', textAlign: 'center' }}>
                        <div style={{ color: '#aaa', fontSize: '0.75rem', textTransform: 'uppercase', marginBottom: '5px' }}>Profit Encaissé</div>
                        <div style={{ fontSize: '1.8rem', fontWeight: 'bold', color: (data.realized_pnl ?? data.profit) >= 0 ? '#00e676' : '#ff4444' }}>
                          {(data.realized_pnl ?? data.profit ?? 0).toLocaleString('fr-FR')} €
                        </div>
                      </div>
                      <div style={{ flex: 1, background: '#1e1e1e', borderRadius: '12px', padding: '20px', border: '1px solid #333', textAlign: 'center' }}>
                        <div style={{ color: '#aaa', fontSize: '0.75rem', textTransform: 'uppercase', marginBottom: '5px' }}>Volume Pips</div>
                        <div style={{ fontSize: '1.8rem', fontWeight: 'bold', color: (data.daily_pips || data.pips) >= 0 ? '#00e676' : '#ff4444' }}>
                          {(data.daily_pips || data.pips || 0).toFixed(1)} <span style={{fontSize:'1rem'}}>p</span>
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
          {statsTab === 'canal' && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: '15px' }}>
              {historicalStats?.channels_lifetime && Object.keys(historicalStats.channels_lifetime).length > 0 ? (
                Object.entries(historicalStats.channels_lifetime)
                .sort((a, b) => b[1].profit - a[1].profit) // Tri par profit décroissant
                .map(([name, data]) => {
                  const avgPips = data.count > 0 ? (data.pips / data.count).toFixed(1) : "0.0";
                  const isPositive = data.profit >= 0;
                  
                  return (
                  <div key={name} style={{ background: '#1e1e1e', borderRadius: '12px', padding: '15px', border: '1px solid #333' }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '12px' }}>
                      <span style={{ fontWeight: 'bold', fontSize: '1rem', color: '#fff' }}>{name.replace('CANAL ', '').replace('_', ' ')}</span>
                      <div style={{ textAlign: 'right' }}>
                        <div style={{ color: isPositive ? '#00e676' : '#ff4444', fontWeight: 'bold', fontSize: '1.2rem' }}>
                          {isPositive ? '+' : ''}{data.profit.toFixed(2)} €
                        </div>
                        <div style={{ fontSize: '0.8rem', color: '#888' }}>{data.pips > 0 ? '+' : ''}{data.pips.toFixed(1)} pips</div>
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
                      <span>📦 {data.count} Trades</span>
                      <span>⚖️ Moy: {avgPips} pips/trade</span>
                    </div>
                  </div>
                )})
              ) : (
                <div style={{ textAlign: 'center', color: '#666', padding: '40px', background: '#1e1e1e', borderRadius: '12px' }}>Aucun trade enregistré par canal.</div>
              )}
            </div>
          )}

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
              </div>
            </div>
          )}

        </div>
      </div>
      </div>

      {/* SECTION 5 : ACTIONS CRITIQUES */}
      <div onClick={() => { triggerHaptic('heavy'); setIsDangerOpen(!isDangerOpen); }} style={{ display: 'flex', justifyContent: 'flex-start', alignItems: 'center', cursor: 'pointer', borderTop: '2px dashed #ff4444', paddingTop: '25px', marginTop: '0px', userSelect: 'none' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px', color: '#ff4444' }}>
          <AlertTriangle size={20} />
          <h2 style={{ fontSize: '1.2rem', margin: 0 }}>Contrôle Système</h2>
          {isDangerOpen ? <ChevronUp size={20} /> : <ChevronDown size={20} />}
        </div>
      </div>

      <div style={{ display: 'grid', gridTemplateRows: isDangerOpen ? '1fr' : '0fr', transition: 'grid-template-rows 0.4s ease-out' }}>
        <div style={{ overflow: 'hidden' }}>
          <div style={{ background: 'rgba(255, 68, 68, 0.05)', border: '1px solid #ff4444', borderRadius: '12px', padding: '20px', display: 'flex', gap: '15px', flexWrap: 'wrap', marginTop: '15px', marginBottom: '10px' }}>
          <button onClick={handleUnlock} style={{ flex: '1 1 200px', background: '#ffc107', border: 'none', color: '#000', padding: '15px', borderRadius: '8px', cursor: 'pointer', fontWeight: 'bold', display: 'flex', justifyContent: 'center', alignItems: 'center', gap: '8px' }}>
            <Unlock size={18} /> Forcer Déverrouillage DD
          </button>
          <button onClick={handleKill} style={{ flex: '1 1 200px', background: '#000', border: '1px solid #333', color: '#ff4444', padding: '15px', borderRadius: '8px', cursor: 'pointer', fontWeight: 'bold', display: 'flex', justifyContent: 'center', alignItems: 'center', gap: '8px' }}>
            <Power size={18} /> ARRÊT DU SERVEUR
          </button>
        </div>
      </div>
      </div>
    </div>
  )
}

export default App