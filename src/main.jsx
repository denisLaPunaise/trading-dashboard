import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App.jsx'
import './index.css'
import WebApp from '@twa-dev/sdk'

// On enveloppe l'initialisation dans un try/catch par sécurité
try {
  WebApp.ready();
  WebApp.expand();
} catch (e) {
  console.log("Exécution hors Telegram détectée.");
}

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
)