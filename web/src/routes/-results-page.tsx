// Route-ignored page module (TanStack skips `-`-prefixed files). Keeping this
// re-export preserves route-level code splitting while the feature owns its
// controller, state views, and presentation.
export { ResultsPage } from '../features/queries/results/ResultsPage'
