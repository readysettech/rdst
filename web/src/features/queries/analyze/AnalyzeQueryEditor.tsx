import { Button } from '@rs/ui-new/button'
import { Card } from '@rs/ui-new/card-2'
import { HStack } from '@rs/ui-new/stack'
import { SQLInput } from '../../../components/SQLInput'
import { SQLSchemaStatus } from '../../../components/sql-input/SQLSchemaStatus'
import { useSQLSchema } from '../../../components/sql-input/useSQLSchema'
import { AnalysisModePicker } from './query-editor/AnalysisModePicker'

interface AnalyzeQueryEditorProps {
  value: string
  onChange: (v: string) => void
  onAnalyze: () => void
  disabled?: boolean
  target?: string | null
  fast?: boolean
  onFastChange?: (fast: boolean) => void
}

export function AnalyzeQueryEditor({
  value,
  onChange,
  onAnalyze,
  disabled,
  target,
  fast = false,
  onFastChange,
}: AnalyzeQueryEditorProps) {
  const schemaState = useSQLSchema(target)

  return (
    <Card className="w-full">
      <Card.Header className="items-stretch">
        <HStack className="w-full items-center justify-between gap-4">
          <Card.Title>SQL query</Card.Title>
          <div className="shrink-0">
            <SQLSchemaStatus {...schemaState} />
          </div>
        </HStack>
      </Card.Header>

      <Card.Content className="p-4 overflow-hidden">
        <SQLInput
          value={value}
          onChange={onChange}
          onSubmit={disabled ? undefined : onAnalyze}
          target={target}
          disabled={disabled}
          showPrettify={!disabled}
          showSchemaStatus={false}
          schemaState={schemaState}
          minHeight="10rem"
        />
      </Card.Content>

      {/* Footer actions are a separate card-2 band, matching QueryCard. */}
      <Card.Content className="px-5 py-4">
        <HStack className="justify-between items-center">
          <AnalysisModePicker
            fast={fast}
            onFastChange={onFastChange}
            disabled={disabled}
          />

          <Button
            onClick={onAnalyze}
            disabled={disabled || !value.trim()}
            variant="rising"
            modifier="solid"
            label="Analyze query"
            icon="sparkles"
            iconPosition="left"
          />
        </HStack>
      </Card.Content>
    </Card>
  )
}
