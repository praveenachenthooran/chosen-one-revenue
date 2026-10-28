import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  AllCommunityModule,
  ModuleRegistry,
  themeQuartz,
  type ColDef,
  type ColGroupDef,
  type GridApi,
  type ICellRendererParams,
  type ValueGetterParams,
} from 'ag-grid-community'
import { AgGridReact } from 'ag-grid-react'
import type { MonthColumn, Project, TotalsResponse } from '../api'
import { formatMoney, parseAmountInput } from '../format'

ModuleRegistry.registerModules([AllCommunityModule])

const theme = themeQuartz.withParams({
  accentColor: 'var(--brand)',
  backgroundColor: 'var(--surface)',
  foregroundColor: 'var(--ink)',
  textColor: 'var(--ink)',
  headerBackgroundColor: 'var(--surface-2)',
  headerTextColor: 'var(--muted)',
  borderColor: 'var(--line)',
  chromeBackgroundColor: 'var(--surface-2)',
  oddRowBackgroundColor: 'var(--surface)',
  rowHoverColor: 'var(--brand-soft)',
  selectedRowBackgroundColor: 'var(--brand-soft)',
  inputBackgroundColor: 'var(--surface)',
  inputTextColor: 'var(--ink)',
  fontFamily: 'var(--font)',
  headerFontWeight: 600,
  rowHeight: 40,
  headerHeight: 40,
  spacing: 6,
})

export type GridRow = Project & { isTotal?: boolean }

type Props = {
  projects: Project[]
  months: MonthColumn[]
  totals: TotalsResponse | null
  quickFilter: string
  onOpenProject: (project: Project) => void
  /** Called when someone finishes editing a month; the workspace validates and saves it. */
  onEditAmount: (projectId: number, month: string, raw: string) => void
  /** `projectId:month` keys currently being saved. */
  pending: ReadonlySet<string>
  /** Turn off row/column virtualisation; only for environments without layout (jsdom tests). */
  renderAll?: boolean
}

function StatusCell({ data }: ICellRendererParams<GridRow>) {
  if (!data || data.isTotal) return null
  return (
    <span className="d-inline-flex align-items-center gap-2">
      <span className={`status-chip status-${data.status.toLowerCase()}`}>{data.status}</span>
      {data.attention_count > 0 && <span className="attention-flag">Needs attention</span>}
    </span>
  )
}

export default function ForecastGrid({
  projects,
  months,
  totals,
  quickFilter,
  onOpenProject,
  onEditAmount,
  pending,
  renderAll = false,
}: Props) {
  const gridApi = useRef<GridApi<GridRow> | null>(null)
  // A mutable copy of the pending keys that cell class rules can read at any time.
  const [pendingStore] = useState(() => new Set<string>())
  const isPending = useCallback(
    (id: number | undefined, month: string) => pendingStore.has(`${id}:${month}`),
    [pendingStore],
  )

  useEffect(() => {
    pendingStore.clear()
    pending.forEach((key) => pendingStore.add(key))
    gridApi.current?.refreshCells({ force: true, suppressFlash: true })
  }, [pending, pendingStore])

  const columnDefs = useMemo<(ColDef<GridRow> | ColGroupDef<GridRow>)[]>(() => {
    const amount = (month: string) => (params: ValueGetterParams<GridRow>) => {
      const value = params.data?.monthly?.[month]
      return value === undefined ? null : Number(value)
    }
    const money: ColDef<GridRow> = {
      type: 'rightAligned',
      filter: 'agNumberColumnFilter',
      valueFormatter: (p) => formatMoney(p.value as number | null),
      width: 112,
    }

    const byYear = new Map<string, ColDef<GridRow>[]>()
    for (const m of months) {
      const column: ColDef<GridRow> = {
        ...money,
        colId: m.month,
        headerName: m.label,
        headerTooltip: `${m.fiscal_year} · period ${m.fiscal_period}${m.is_closed ? ' · closed month' : ''}`,
        headerClass: m.is_closed ? 'month-closed' : undefined,
        cellClass: m.is_closed ? 'cell-closed' : undefined,
        valueGetter: amount(m.month),
        // Only rows the signed-in user may edit are editable; the server enforces this too.
        editable: (p) => !!p.data && !p.data.isTotal && p.data.can_edit,
        valueSetter: (p) => {
          const plain = parseAmountInput(String(p.newValue ?? ''))
          // Skip no-op edits; the workspace refreshes the row from the server after saving.
          if (plain !== null && p.oldValue !== null && Number(plain) === p.oldValue) return false
          if (p.data) onEditAmount(p.data.id, m.month, String(p.newValue ?? ''))
          return false
        },
        cellClassRules: {
          'cell-editable': (p) => !!p.data && !p.data.isTotal && p.data.can_edit,
          'cell-saving': (p) => isPending(p.data?.id, m.month),
        },
      }
      byYear.set(m.fiscal_year, [...(byYear.get(m.fiscal_year) ?? []), column])
    }

    return [
      {
        headerName: 'Customer',
        field: 'customer',
        pinned: 'left',
        width: 200,
        filter: 'agTextColumnFilter',
        cellClass: (p) => (p.data?.isTotal ? 'total-label' : ''),
      },
      {
        headerName: 'Project',
        field: 'name',
        pinned: 'left',
        width: 230,
        filter: 'agTextColumnFilter',
        cellRenderer: (p: ICellRendererParams<GridRow>) =>
          p.data && !p.data.isTotal ? (
            <button type="button" className="project-link" onClick={() => onOpenProject(p.data!)}>
              {p.value as string}
            </button>
          ) : null,
      },
      {
        headerName: 'Status',
        field: 'status',
        pinned: 'left',
        width: 250,
        filter: 'agTextColumnFilter',
        cellRenderer: StatusCell,
      },
      {
        headerName: 'Client Partner',
        valueGetter: (p) => p.data?.client_partner?.name ?? '',
        width: 160,
        filter: 'agTextColumnFilter',
      },
      { headerName: 'Practice', field: 'practice', width: 120, hide: true },
      { headerName: 'Region', field: 'region', width: 100, hide: true },
      { headerName: 'Industry', field: 'industry', width: 150, hide: true },
      { headerName: 'Category', field: 'category', width: 110, hide: true },
      {
        ...money,
        headerName: 'Total',
        colId: 'total',
        valueGetter: (p) => (p.data ? Number(p.data.total_revenue) : null),
        width: 130,
        cellClass: 'cell-total',
      },
      ...[...byYear.entries()].map(
        ([year, children]): ColGroupDef<GridRow> => ({ headerName: year, children }),
      ),
    ]
  }, [months, onOpenProject, onEditAmount, isPending])

  // Totals are calculated by the API (never summed in the browser).
  const totalRow = useMemo<GridRow[]>(() => {
    if (!totals) return []
    return [
      {
        id: -1,
        isTotal: true,
        customer: `Total (${totals.project_count} projects)`,
        name: '',
        status: '',
        total_revenue: totals.grand_total,
        attention_count: 0,
        monthly: Object.fromEntries(totals.months.map((m) => [m.month, m.total])),
      } as GridRow,
    ]
  }, [totals])

  return (
    <div className={`grid-wrap${renderAll ? ' grid-wrap-auto' : ''}`}>
      <AgGridReact<GridRow>
        theme={theme}
        rowData={projects}
        columnDefs={columnDefs}
        pinnedBottomRowData={totalRow}
        quickFilterText={quickFilter}
        defaultColDef={{ sortable: true, resizable: true, suppressMovable: true }}
        getRowId={(p) => String(p.data.id)}
        tooltipShowDelay={400}
        onGridReady={(event) => {
          gridApi.current = event.api
        }}
        stopEditingWhenCellsLoseFocus
        suppressColumnVirtualisation={renderAll}
        suppressRowVirtualisation={renderAll}
        domLayout={renderAll ? 'autoHeight' : 'normal'}
        overlayNoRowsTemplate="No projects match the current filters."
      />
    </div>
  )
}
