import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App.jsx'
import './index.css'
import WebApp from '@twa-dev/sdk' // 👈 AJOUT 1 : On importe Telegram

// 👈 AJOUT 2 : On dit à Telegram que l'app est prête et on la force en plein écran
WebApp.ready();
WebApp.expand(); 

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
)