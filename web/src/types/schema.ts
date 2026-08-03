/**
 * Types for semantic layer management
 */

export interface SchemaStatus {
  target: string
  exists: boolean
  tables: number
  columns: number
  relationships: number
  terminology: number
  updated_at: string | null
  profiled_tables?: number
  profiled_at?: string | null
}

export interface SchemaTableColumn {
  name: string
  data_type: string | null
  description: string | null
  unit: string | null
  is_pii: boolean
  null_fraction?: number | null
  distinct_count?: number | null
  enum_values: Record<string, string> | null
}

export interface SchemaTableRelationship {
  target_table: string
  relationship_type: string
  join_pattern: string
}

export interface SchemaTable {
  name: string
  description: string | null
  business_context: string | null
  row_estimate: string | null
  columns: SchemaTableColumn[]
  relationships: SchemaTableRelationship[]
}

export interface SchemaTerminology {
  term: string
  definition: string
  sql_pattern: string
  synonyms: string[]
}

export interface SchemaMetric {
  name: string
  definition: string
  sql: string
}

export interface SchemaExtension {
  name: string
  version: string
  description: string | null
  types_provided: string[]
}

export interface SchemaCustomType {
  name: string
  type_category: string
  base_type: string | null
  enum_values: string[] | null
  description: string | null
}

export interface SchemaDetails {
  target: string
  tables: SchemaTable[]
  terminology: SchemaTerminology[]
  extensions: SchemaExtension[]
  custom_types: SchemaCustomType[]
  metrics: SchemaMetric[]
}

export interface SchemaTargetSummary {
  name: string
  tables: number
  terminology: number
  updated_at: string | null
}

export interface SchemaTargetList {
  targets: SchemaTargetSummary[]
}

export interface SchemaInitResult {
  success: boolean
  target: string
  tables: number
  columns: number
  relationships: number
  enum_columns: string[]
  path: string | null
  error: string | null
  code?: string | null
  category?: string | null
}

export interface SchemaExportResult {
  success: boolean
  format: string
  content: string
  error: string | null
}

export interface SchemaDeleteResult {
  success: boolean
  target: string
  error: string | null
}

export interface SchemaUpdateResult {
  success: boolean
  message: string
  error: string | null
}

// Request types for mutations

export interface AddTableData {
  table_name: string
  description: string
  business_context?: string
  row_estimate?: string
}

export interface AddColumnData {
  table_name: string
  column_name: string
  description: string
  data_type?: string
  unit?: string
  is_pii?: boolean
}

export interface AddEnumData {
  table_name: string
  column_name: string
  enum_values: Record<string, string>
}

export interface AddTerminologyData {
  term: string
  definition: string
  sql_pattern: string
  synonyms?: string[]
}

export interface AddRelationshipData {
  source_table: string
  target_table: string
  join_pattern: string
  relationship_type?: string
}

export interface AddMetricData {
  name: string
  definition: string
  sql: string
  unit?: string
}
