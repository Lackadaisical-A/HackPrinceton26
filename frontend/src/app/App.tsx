import { createHashRouter, RouterProvider } from 'react-router'
import ConsolePage from '../routes/console/ConsolePage'
import { NotFound, RouteError } from './RouteError'
import { Shell } from './Shell'

/**
 * Hash routing, so the built app works from any static host or a file share with no server
 * rewrite rules. The console loads with the app; the other screens load when first opened.
 */
const router = createHashRouter([
  {
    path: '/',
    Component: Shell,
    ErrorBoundary: RouteError,
    HydrateFallback: () => null,
    children: [
      { index: true, Component: ConsolePage },
      {
        path: 'trials',
        lazy: async () => ({ Component: (await import('../routes/trials/TrialsPage')).default }),
      },
      {
        path: 'signals',
        lazy: async () => ({ Component: (await import('../routes/signals/SignalsPage')).default }),
      },
      {
        path: 'setup',
        lazy: async () => ({ Component: (await import('../routes/setup/SetupPage')).default }),
      },
      { path: '*', Component: NotFound },
    ],
  },
])

export function App() {
  return <RouterProvider router={router} />
}
