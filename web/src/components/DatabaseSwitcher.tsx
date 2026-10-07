import { useTarget } from '../hooks/useTarget'
import { TargetDropdown } from './TargetDropdown'

/**
 * The global database switcher as a compact header control: the selected database's
 * name, and a menu of every database with its monitoring state. Where a pick lands
 * follows switchDestination.
 */
export function DatabaseSwitcher() {
  const { target, setTarget } = useTarget()
  return (
    <TargetDropdown
      variant="compact"
      selectedTarget={target}
      onSelectTarget={setTarget}
    />
  )
}
