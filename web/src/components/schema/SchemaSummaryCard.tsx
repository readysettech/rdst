import { Text } from '@rs/ui-new/text'
import { Icon } from '@rs/ui-new/icon'
import { HStack } from '@rs/ui-new/stack'
import { Card } from '@rs/ui-new/card'
import { Show } from '@rs/ui-new/show'
import type { SchemaStatus } from '../../types/schema'

interface SchemaSummaryCardProps {
  status: SchemaStatus
}

interface StatItemProps {
  label: string
  value: number
  icon: "layers" | "connect" | "folder-file"
}

function StatItem({ label, value, icon }: StatItemProps) {
  return (
    <HStack className="gap-3 items-center">
      <div className="w-10 h-10 rounded-xl bg-surface-layout-2 flex items-center justify-center">
        <Icon name={icon} label={label} className="w-5 h-5 text-content-layout-3" />
      </div>
      <div>
        <Text level="headline-4" className="text-content-layout-1 tabular-nums">
          {value}
        </Text>
        <Text level="caption" className="text-content-layout-3">
          {label}
        </Text>
      </div>
    </HStack>
  )
}

export function SchemaSummaryCard({ status }: SchemaSummaryCardProps) {
  const stats: StatItemProps[] = [
    { label: 'Tables', value: status.tables, icon: 'layers' },
    { label: 'Columns', value: status.columns, icon: 'layers' },
    { label: 'Relationships', value: status.relationships, icon: 'connect' },
    { label: 'Terms', value: status.terminology, icon: 'folder-file' },
  ]

  return (
    <Card className="w-full">
      <Card.Content>
        <HStack className="justify-between items-center flex-wrap gap-6">
          <HStack className="gap-8 items-center flex-wrap">
            {stats.map((stat, index) => (
              <div key={stat.label} className="flex items-center gap-6">
                {index > 0 && (
                  <div className="w-px h-10 bg-border-layout-1 hidden tablet:block" />
                )}
                <StatItem {...stat} />
              </div>
            ))}
          </HStack>
          <Show when={!!status.updated_at}>
            <HStack className="gap-2 items-center">
              <Icon name="observe" label="Updated" className="w-3.5 h-3.5 text-content-layout-3" />
              <Text level="caption" className="text-content-layout-3">
                Updated {new Date(status.updated_at!).toLocaleDateString()}
              </Text>
            </HStack>
          </Show>
        </HStack>
      </Card.Content>
    </Card>
  )
}
