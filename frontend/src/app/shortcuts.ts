import { useEffect } from 'react'
import { useNavigate } from 'react-router'
import { useSession } from '../state/session'
import { finishTrial, markDeformation, markDrop, startTrial, toggleHaptics, toggleRecording, toggleSpeak } from './runtime'

/** Shown in the shortcuts dialog. The handler below is the source of what each key does. */
export const SHORTCUTS: { keys: string; does: string }[] = [
  { keys: 'T', does: 'Start a trial, or finish the one that is running' },
  { keys: 'D', does: 'Mark a drop during a trial' },
  { keys: 'F', does: 'Mark a deformation during a trial' },
  { keys: 'H', does: 'Turn haptics on or off (not during a trial)' },
  { keys: 'R', does: 'Start or stop recording' },
  { keys: 'S', does: 'Read captions aloud, on or off' },
  { keys: '1', does: 'Go to Console' },
  { keys: '2', does: 'Go to Trials' },
  { keys: '3', does: 'Go to Signals' },
  { keys: '?', does: 'Show this list' },
]

function isTyping(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false
  return target.isContentEditable || ['INPUT', 'TEXTAREA', 'SELECT'].includes(target.tagName)
}

/** Single-key shortcuts for the whole app. Ignored while typing or when a dialog is open. */
export function useShortcuts(openHelp: () => void): void {
  const navigate = useNavigate()

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent): void => {
      if (event.defaultPrevented || event.repeat) return
      if (event.ctrlKey || event.metaKey || event.altKey) return
      if (isTyping(event.target) || document.querySelector('dialog[open]')) return

      switch (event.key.toLowerCase()) {
        case 't':
          if (useSession.getState().trial) finishTrial('completed')
          else startTrial()
          break
        case 'd':
          markDrop()
          break
        case 'f':
          markDeformation()
          break
        case 'h':
          toggleHaptics()
          break
        case 'r':
          toggleRecording()
          break
        case 's':
          toggleSpeak()
          break
        case '1':
          void navigate('/')
          break
        case '2':
          void navigate('/trials')
          break
        case '3':
          void navigate('/signals')
          break
        case '?':
          openHelp()
          break
        default:
          return
      }
      event.preventDefault()
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [navigate, openHelp])
}
