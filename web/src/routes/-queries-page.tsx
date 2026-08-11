/**
 * Route-local compatibility export.
 *
 * The implementation lives in the Queries feature. Keeping this route-ignored
 * adapter preserves existing test imports and the TanStack code-splitting
 * boundary while routes stay limited to routing concerns.
 */
export { QueriesWorkspace as QueriesPage } from '../features/queries/workspace/QueriesWorkspace'
