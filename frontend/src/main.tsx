import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter, HashRouter } from 'react-router-dom'
import App from './App'
import { STATIC_DEMO } from './store/workstation'
import './index.css'

const container = document.getElementById('root')
if (!container) throw new Error('Missing #root container')

// The deployed workstation is served by FastAPI, which falls through to
// index.html for unknown paths, so it uses real URLs. The static preview is
// published on a GitHub Pages subpath with no such fallback, so it routes on the
// hash instead and a refresh still lands on the right screen.
const Router = STATIC_DEMO ? HashRouter : BrowserRouter

createRoot(container).render(
  <StrictMode>
    <Router>
      <App />
    </Router>
  </StrictMode>,
)
