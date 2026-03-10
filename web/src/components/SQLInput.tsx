import { useQuery } from "@tanstack/react-query";
import { Spinner } from "@rs/ui-new/spinner";
import { Icon } from "@rs/ui-new/icon";
import { Text } from "@rs/ui-new/text";
import { HStack } from "@rs/ui-new/stack";
import { m, AnimatePresence } from "@rs/ui-new/motion";
import { SQLEditor, type SchemaInfo, formatQuery } from "./SQLEditor";
import { fetchSchema } from "../lib/api";

interface SQLInputProps {
  value: string;
  onChange: (v: string) => void;
  onSubmit?: () => void;
  placeholder?: string;
  disabled?: boolean;
  minHeight?: string;
  showPrettify?: boolean;
  target?: string | null;
}

function SchemaStatus({
  isLoading,
  isConnected,
  tableCount,
  dialect,
}: {
  isLoading: boolean;
  isConnected: boolean;
  tableCount: number;
  dialect?: string;
}) {
  if (isLoading) {
    return (
      <m.div
        initial={{ opacity: 0, y: 5 }}
        animate={{ opacity: 1, y: 0 }}
        exit={{ opacity: 0, y: -5 }}
        className="flex items-center gap-2"
      >
        <Spinner size="base" color="primary-soft" />
        <Text level="caption" className="text-content-layout-3">
          Connecting to database...
        </Text>
      </m.div>
    );
  }

  if (isConnected) {
    return (
      <m.div
        initial={{ opacity: 0, y: 5 }}
        animate={{ opacity: 1, y: 0 }}
        exit={{ opacity: 0, y: -5 }}
        className="flex items-center gap-3"
      >
        <HStack className="gap-1.5 items-center">
          <Icon
            name="database"
            label="Tables"
            className="w-3 h-3 text-content-layout-3"
          />
          <Text level="caption" className="text-content-layout-3">
            {tableCount} table{tableCount !== 1 ? "s" : ""}
          </Text>
        </HStack>
        {dialect && (
          <>
            <div className="w-px h-3 bg-border-layout-1" />
            <Text
              level="caption"
              className="text-content-layout-3 uppercase tracking-wider"
            >
              {dialect}
            </Text>
          </>
        )}
      </m.div>
    );
  }

  return (
    <m.div
      initial={{ opacity: 0, y: 5 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, y: -5 }}
      className="flex items-center gap-1.5"
    >
      <div className="w-2 h-2 rounded-full bg-content-layout-3" />
      <Text level="caption" className="text-content-layout-3">
        No target selected
      </Text>
    </m.div>
  );
}

export function SQLInput({
  value,
  onChange,
  onSubmit,
  placeholder,
  disabled,
  minHeight = "12rem",
  showPrettify = true,
  target,
}: SQLInputProps) {
  const {
    data: schemaData,
    isLoading: isLoadingSchema,
    isFetching: isFetchingSchema,
  } = useQuery({
    queryKey: ["schema", target],
    queryFn: () => fetchSchema(target || undefined),
    staleTime: 5 * 60 * 1000,
    enabled: !!target,
  });

  const schema: SchemaInfo | undefined =
    schemaData?.tables && Object.keys(schemaData.tables).length > 0
      ? { tables: schemaData.tables, dialect: schemaData.dialect }
      : undefined;

  const isSchemaLoading = target && (isLoadingSchema || isFetchingSchema);
  const tableCount = schema?.tables ? Object.keys(schema.tables).length : 0;

  const handleFormat = showPrettify
    ? () => {
        const formatted = formatQuery(value, schema?.dialect);
        if (formatted !== value) {
          onChange(formatted);
        }
      }
    : undefined;

  return (
    <div className="space-y-3 w-full">
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

      {/* Schema status bar */}
      <div className="px-1">
        <AnimatePresence mode="wait">
          <SchemaStatus
            key={
              isSchemaLoading
                ? "loading"
                : schema
                  ? "connected"
                  : "disconnected"
            }
            isLoading={!!isSchemaLoading}
            isConnected={!!schema}
            tableCount={tableCount}
            dialect={schema?.dialect}
          />
        </AnimatePresence>
      </div>
    </div>
  );
}
