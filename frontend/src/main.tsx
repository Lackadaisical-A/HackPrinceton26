import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import '@fontsource-variable/atkinson-hyperlegible-next/wght.css'
import '@fontsource-variable/atkinson-hyperlegible-mono/wght.css'
import './styles/index.css'
import { App } from './app/App'
import { startDerive } from './app/derive'
import { connect } from './app/runtime'

// The data layer lives for as long as the page does, so it starts here and not in a component.
startDerive()
connect()

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
