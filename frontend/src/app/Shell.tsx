import { useCallback, useState } from 'react'
import { Outlet, useLocation } from 'react-router'
import { ShortcutsDialog } from './ShortcutsDialog'
import { useShortcuts } from './shortcuts'
import { TopBar } from './TopBar'

/** The frame around every screen: the top bar, the page, and app-wide keyboard shortcuts. */
export function Shell() {
  const [helpOpen, setHelpOpen] = useState(false)
  const openHelp = useCallback(() => setHelpOpen(true), [])
  const closeHelp = useCallback(() => setHelpOpen(false), [])
  useShortcuts(openHelp)
  const { pathname } = useLocation()

  return (
    <div className="flex min-h-dvh flex-col">
      {/* A button, not a link: with hash routing, an anchor to #main would change the route. */}
      <button
        type="button"
        onClick={() => document.getElementById('main')?.focus()}
        className="sr-only focus-visible:not-sr-only focus-visible:absolute focus-visible:top-2 focus-visible:left-2 focus-visible:z-50 focus-visible:rounded-sm focus-visible:bg-ink focus-visible:px-3 focus-visible:py-2 focus-visible:text-sm focus-visible:text-plate"
      >
        Skip to the page
      </button>
      <TopBar onHelp={openHelp} />
      <main id="main" tabIndex={-1} className="mx-auto w-full max-w-[1680px] flex-1 p-3 outline-none md:p-4">
        {/* Keyed by route so each screen plays its entrance when you switch to it. */}
        <div key={pathname} className="rise-in">
          <Outlet />
        </div>
      </main>
      <ShortcutsDialog open={helpOpen} onClose={closeHelp} />
    </div>
  )
}
