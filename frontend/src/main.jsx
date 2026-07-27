import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App.jsx'
import './index.css'
import WebApp from '@twa-dev/sdk'

// On enveloppe l'initialisation dans un try/catch par sécurité
try {
  WebApp.ready();
  WebApp.expand();
  WebApp.setHeaderColor('#0a0a0a');
} catch {
  console.log("Exécution hors Telegram détectée.");
}

// (#60) Sans ErrorBoundary, une exception de rendu (ex: un champ Firestore transitoirement
// absent) faisait planter TOUT le dashboard en écran blanc, sans message ni recours autre
// que deviner qu'il fallait recharger.
class ErrorBoundary extends React.Component {
  constructor(props) {
    super(props);
    this.state = { hasError: false };
  }
  static getDerivedStateFromError() {
    return { hasError: true };
  }
  componentDidCatch(error, info) {
    console.error('Dashboard crash:', error, info);
  }
  render() {
    if (this.state.hasError) {
      return (
        <div style={{ color: '#ff4444', textAlign: 'center', padding: '50px 20px', background: '#121212', minHeight: '100vh', boxSizing: 'border-box' }}>
          <p style={{ fontSize: '1.1rem', marginBottom: '20px' }}>⚠️ Une erreur inattendue est survenue.</p>
          <button
            onClick={() => window.location.reload()}
            style={{ background: '#ff4444', border: 'none', color: '#000', padding: '12px 24px', borderRadius: '8px', cursor: 'pointer', fontWeight: 'bold' }}
          >
            Recharger
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <ErrorBoundary>
      <App />
    </ErrorBoundary>
  </React.StrictMode>,
)