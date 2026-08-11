import { createFileRoute, Navigate } from '@tanstack/react-router'
import { QueriesDesignLabPage } from '../features/queries/lab/QueriesDesignLabPage'
import {
  QUERY_LAB_VARIANTS,
  type QueryLabVariant,
} from '../features/queries/lab/queryLabModel'

const VARIANTS = new Set(Object.keys(QUERY_LAB_VARIANTS))

export const Route = createFileRoute('/lab/queries/$variant')({
  component: QueriesDesignLabRoute,
})

function QueriesDesignLabRoute() {
  const { variant } = Route.useParams()
  if (!VARIANTS.has(variant)) {
    return <Navigate to="/lab/queries/$variant" params={{ variant: '1' }} />
  }
  return <QueriesDesignLabPage variant={variant as QueryLabVariant} />
}
