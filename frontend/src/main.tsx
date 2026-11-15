import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import '@fontsource-variable/atkinson-hyperlegible-next/wght.css'
import '@fontsource-variable/atkinson-hyperlegible-mono/wght.css'
import './styles/index.css'
import { App } from './app/App'
import { startDerive } from './app/derive'
import { connect, startAimSync } from './app/runtime'
import { useSettings } from './state/settings'
import { launchSettings } from './state/launch'

// The data layer lives for as long as the page does, so it starts here and not in a component.
useSettings.getState().update(launchSettings(window.location.search))
startDerive()
startAimSync()
connect()

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
