import { Chip } from '../../ui/Chip'

/**
 * Whether a message format is agreed. A proposed format has a dashed outline as well as the
 * word, the way a drawing marks something not yet built.
 */
export function StandingChip({ standing }: { standing: 'contract' | 'proposed' }) {
  return (
    <Chip className={standing === 'proposed' ? 'border-dashed' : undefined}>
      {standing === 'contract' ? 'Contract' : 'Proposed'}
    </Chip>
  )
}
