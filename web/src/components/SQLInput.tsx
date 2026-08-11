import { formatQuery, SQLEditor } from './SQLEditor'
import { SQLSchemaStatus } from './sql-input/SQLSchemaStatus'
import { type SQLSchemaState, useSQLSchema } from './sql-input/useSQLSchema'

interface SQLInputProps {
  value: string
  onChange: (v: string) => void
  onSubmit?: () => void
  placeholder?: string
  disabled?: boolean
  minHeight?: string
  showPrettify?: boolean
  showSchemaStatus?: boolean
  schemaState?: SQLSchemaState
  target?: string | null
}

export function SQLInput({
  value,
  onChange,
  onSubmit,
  placeholder,
  disabled,
  minHeight = '12rem',
  showPrettify = true,
  showSchemaStatus = true,
  schemaState,
  target,
}: SQLInputProps) {
  const internalSchemaState = useSQLSchema(target, !schemaState)
  const resolvedSchemaState = schemaState ?? internalSchemaState
  const { schema } = resolvedSchemaState

  const handleFormat = showPrettify
    ? () => {
        const formatted = formatQuery(value, schema?.dialect)
        if (formatted !== value) {
          onChange(formatted)
        }
      }
    : undefined

  return (
    <div className={showSchemaStatus ? 'space-y-3 w-full' : 'w-full'}>
      <SQLEditor
        value={value}
        onChange={onChange}
        onSubmit={onSubmit}
        onFormat={handleFormat}
        schema={schema}
        placeholder={placeholder}
        disabled={disabled}
        minHeight={minHeight}
      />

      {showSchemaStatus && (
        <div className="px-1">
          <SQLSchemaStatus {...resolvedSchemaState} />
        </div>
      )}
    </div>
  )
}
