import type { FilterOptions, ForecastFilters } from '../api'

/** The PRD's forecast filters (FR-16), shared by the pipeline and snapshot views. */
export const FILTERS: { key: keyof ForecastFilters; label: string; options: (o: FilterOptions) => [string, string][] }[] = [
  { key: 'owner', label: 'Owner', options: (o) => o.owners.map((p) => [p.code, p.name]) },
  { key: 'practice', label: 'TECH', options: (o) => o.practices.map((v) => [v, v]) },
  { key: 'region', label: 'Region', options: (o) => o.regions.map((v) => [v, v]) },
  { key: 'industry', label: 'Industry', options: (o) => o.industries.map((v) => [v, v]) },
  { key: 'category', label: 'Category', options: (o) => o.categories.map((v) => [v, v]) },
  { key: 'customer', label: 'Customer', options: (o) => o.customers.map((v) => [v, v]) },
]
