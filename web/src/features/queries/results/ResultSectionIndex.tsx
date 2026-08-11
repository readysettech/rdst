import { iconTileStyles } from '@rs/ui-new/icon-tile'
import { Text } from '@rs/ui-new/text'

export function ResultSectionIndex({ value }: { value: 1 | 2 | 3 }) {
  const styles = iconTileStyles({
    size: 'base',
    accent: 'primary',
  })

  return (
    <div aria-hidden="true" className={styles.root()}>
      <Text
        as="span"
        level="label-extra-small"
        className="text-content-primary-soft"
      >
        {String(value).padStart(2, '0')}
      </Text>
    </div>
  )
}
