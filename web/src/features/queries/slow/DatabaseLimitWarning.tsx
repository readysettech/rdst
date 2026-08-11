import { CopyButton } from '@rs/ui-new/copy-button'
import { InlineNotice } from '@rs/ui-new/error-state'
import { VStack } from '@rs/ui-new/stack'
import type { TopDbLimitWarningEventData } from '../../../types/top'

function formatKilobytes(bytes: number) {
  return `${Math.round(bytes / 1024)} KB`
}

export function DatabaseLimitWarning({
  warning,
}: {
  warning: TopDbLimitWarningEventData
}) {
  const isPostgres = warning.db_engine.includes('postgres')
  const sql = isPostgres
    ? `ALTER SYSTEM SET ${warning.setting_name} = ${warning.recommended_bytes};`
    : `SET GLOBAL ${warning.setting_name} = ${warning.recommended_bytes};`

  return (
    <div className="rounded-xl border border-border-warning-soft bg-surface-warning-soft/50 p-4">
      <VStack className="gap-3 items-stretch">
        <InlineNotice
          errorClass="database"
          accent="warning"
          icon="alert"
          title="Database query text may be truncated"
          message={`${warning.setting_name} is ${formatKilobytes(warning.db_limit_bytes)}. Increase it to at least ${formatKilobytes(warning.recommended_bytes)} to capture complete queries.`}
          className="border-0 bg-transparent p-0 shadow-none"
        />
        <div className="relative group ml-7">
          <pre className="overflow-x-auto rounded-lg bg-surface-layout-1 px-3 py-2.5 text-content-layout-1 text-mono-small">
            {sql}
            {isPostgres ? '\n-- Then restart PostgreSQL' : ''}
          </pre>
          <div className="absolute top-2 right-2 opacity-0 transition-opacity group-hover:opacity-100 group-focus-within:opacity-100">
            <CopyButton text={sql} />
          </div>
        </div>
      </VStack>
    </div>
  )
}
