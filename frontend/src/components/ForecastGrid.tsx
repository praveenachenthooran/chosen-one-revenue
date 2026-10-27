import { useMemo } from 'react'
import {
  AllCommunityModule,
  ModuleRegistry,
  themeQuartz,
  type ColDef,
  type ColGroupDef,
  type ICellRendererParams,
  type ValueGetterParams,
} from 'ag-grid-community'
import { AgGridReact } from 'ag-grid-react'
import type { MonthColumn, Project, TotalsResponse } from '../api'
import { formatMoney } from '../format'

ModuleRegistry.registerModules([AllCommunityModule])

const theme = themeQuartz.withParams({
  accentColor: '#315be8',
  fontFamily: 'Inter, ui-sans-serif, system-ui, sans-serif',
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
  renderAll = false,
}: Props) {
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
        width: 200,
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
  }, [months, onOpenProject])

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
        suppressColumnVirtualisation={renderAll}
        suppressRowVirtualisation={renderAll}
        domLayout={renderAll ? 'autoHeight' : 'normal'}
        overlayNoRowsTemplate="No projects match the current filters."
      />
    </div>
  )
}
