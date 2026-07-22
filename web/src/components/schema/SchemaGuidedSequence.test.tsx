import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'

import { SchemaGuidedSequence } from './SchemaGuidedSequence'
import type { SchemaDetails, SchemaStatus } from '../../types/schema'

function status(partial: Partial<SchemaStatus> = {}): SchemaStatus {
  return {
    target: 'imdb',
    exists: true,
    tables: 3,
    columns: 12,
    relationships: 2,
    terminology: 0,
    updated_at: null,
    ...partial,
  }
}

// A sparsely documented schema keeps the sequence expanded (below the
// well-documented collapse threshold).
const schema: SchemaDetails = {
  target: 'imdb',
  tables: [
    {
      name: 'users',
      description: null,
      business_context: null,
      row_estimate: null,
      columns: [],
      relationships: [],
    },
  ],
  terminology: [],
  extensions: [],
  custom_types: [],
  metrics: [],
}

function renderSequence(s: SchemaStatus) {
  return render(
    <SchemaGuidedSequence
      schema={schema}
      status={s}
      onRefresh={() => {}}
      onProfile={() => {}}
      onAnnotate={() => {}}
      refreshing={false}
      profiling={false}
      annotating={false}
    />
  )
}

describe('SchemaGuidedSequence profile step', () => {
  afterEach(() => {
    cleanup()
  })

  it('shows the default caption when nothing is profiled', () => {
    renderSequence(status())
    expect(
      screen.getByText("Lets Ask reason about what's in a column, not just its name.")
    ).toBeTruthy()
  })

  it('shows progress when partially profiled', () => {
    renderSequence(status({ profiled_tables: 1 }))
    expect(screen.getByText('Profiled · 1 of 3 tables')).toBeTruthy()
  })

  it('shows the done state when every table is profiled', () => {
    renderSequence(status({ profiled_tables: 3 }))
    expect(screen.getByText('Profiled · 3 of 3 tables')).toBeTruthy()
    // The step marker renders the done tick instead of the number 2.
    expect(screen.queryByText('2')).toBeNull()
  })
})
