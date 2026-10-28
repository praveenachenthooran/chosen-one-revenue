import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react'
import { fetchAuth, signOut, type AuthUser } from './api'
import SignIn from './components/SignIn'
import ForecastWorkspace from './ForecastWorkspace'
import RevenueOverview from './leadership/RevenueOverview'
import PipelineView from './pipeline/PipelineView'
import PortfolioView from './portfolio/PortfolioView'
import MyProjects from './projects/MyProjects'
import ProjectPage from './projects/ProjectPage'
import QualityView from './quality/QualityView'
import AccuracyView from './reporting/AccuracyView'
import ActualsView from './reporting/ActualsView'
import SnapshotsView from './reporting/SnapshotsView'
import { ACTUALS_LOADERS, ACTUALS_VIEWERS, EXPERIENCE_LABEL, experiencesFor, hasRole, type Experience } from './roles'
import { ErrorState, Loading, PageHeader } from './ui/Panel'
import { icons } from './ui/icons'
import { Logo } from './ui/Logo'
import { WorkspaceProvider } from './workspace'

type SecondaryView = 'checks' | 'pipeline' | 'snapshots' | 'actuals' | 'accuracy' | 'grid'
type View = Experience | SecondaryView

type AuthState =
  | { kind: 'loading' }
  | { kind: 'unreachable' }
  | { kind: 'signedOut'; error: string | null }
  | { kind: 'signedIn'; user: AuthUser }

const isAbort = (error: unknown) => error instanceof DOMException && error.name === 'AbortError'

/** The sign-in error Google redirected back with (?auth_error=...), removed from the URL. */
function takeAuthError(): string | null {
  const url = new URL(window.location.href)
  const error = url.searchParams.get('auth_error')
  if (error) {
    url.searchParams.delete('auth_error')
    window.history.replaceState(null, '', url.pathname + url.search + url.hash)
  }
  return error
}

const initials = (name: string) =>
  name
    .split(/\s+/)
    .map((part) => part[0])
    .join('')
    .slice(0, 2)
    .toUpperCase()

const BACK_LABEL: Record<View, string> = {
  partner: 'Back to my projects',
  portfolio: 'Back to the portfolio',
  leadership: 'Back to the revenue overview',
  checks: 'Back to checks',
  pipeline: 'Back to the pipeline',
  snapshots: 'Back to snapshots',
  actuals: 'Back to actuals',
  accuracy: 'Back to accuracy',
  grid: 'Back to the monthly grid',
}

const EXPERIENCE_ICON = { leadership: icons.bars, portfolio: icons.chart, partner: icons.list } as const

function Signed({ user, onSignOut, onSessionEnded }: { user: AuthUser; onSignOut: () => void; onSessionEnded: () => void }) {
  const experiences = useMemo(() => experiencesFor(user), [user])
  const [view, setView] = useState<View>(experiences[0])
  const [projectId, setProjectId] = useState<number | null>(null)
  const [openDeal, setOpenDeal] = useState<{ id: number; title: string } | null>(null)

  const go = useCallback((next: View) => {
    setProjectId(null)
    setOpenDeal(null)
    setView(next)
    window.scrollTo(0, 0)
  }, [])
  const openProject = useCallback((id: number) => {
    setProjectId(id)
    window.scrollTo(0, 0)
  }, [])
  const openDealView = useCallback((deal: { id: number; title: string }) => {
    setProjectId(null)
    setOpenDeal(deal)
    setView('pipeline')
    window.scrollTo(0, 0)
  }, [])

  const canSeeActuals = hasRole(user, ...ACTUALS_VIEWERS)
  const secondary: [SecondaryView, string, ReactNode][] = [
    ['pipeline', 'Pipeline', icons.funnel],
    ['checks', 'Checks', icons.shield],
    ['snapshots', 'Snapshots', icons.clock],
    ...(canSeeActuals ? ([['actuals', 'Actuals', icons.upload], ['accuracy', 'Accuracy', icons.target]] as [SecondaryView, string, ReactNode][]) : []),
    ['grid', 'Monthly grid', icons.list],
  ]

  const nav = (id: View, label: string, icon: ReactNode) => (
    <button key={id} type="button" className={`navi${view === id ? ' on' : ''}`} aria-current={view === id ? 'page' : undefined} onClick={() => go(id)}>
      {icon}
      <span>{label}</span>
    </button>
  )

  let body: ReactNode
  if (projectId !== null) {
    body = (
      <ProjectPage
        key={projectId}
        projectId={projectId}
        backLabel={BACK_LABEL[view]}
        onBack={() => setProjectId(null)}
        chartOpen={view !== 'partner'}
        onSessionEnded={onSessionEnded}
        onOpenDeal={openDealView}
      />
    )
  } else if (view === 'partner') body = <MyProjects user={user} onOpen={openProject} />
  else if (view === 'portfolio') body = <PortfolioView user={user} title={EXPERIENCE_LABEL.portfolio(user)} onOpen={openProject} />
  else if (view === 'leadership')
    body = <RevenueOverview onSessionEnded={onSessionEnded} onOpenProject={openProject} onOpenDeal={openDealView} onGo={go} />
  else if (view === 'checks')
    body = (
      <>
        <PageHeader title="Checks" sub="Findings from the deterministic checks, with the AI Revenue Detective to explain them." />
        <QualityView onSessionEnded={onSessionEnded} onOpenProject={openProject} onOpenDeal={openDealView} />
      </>
    )
  else if (view === 'pipeline')
    body = (
      <>
        <PageHeader title="Pipeline" sub="HubSpot deals, weighted by stage probability, and the forecast they add." />
        <PipelineView key={openDeal?.id ?? 'pipeline'} initialDeal={openDeal} user={user} onSessionEnded={onSessionEnded} onOpenProject={openProject} />
      </>
    )
  else if (view === 'snapshots')
    body = (
      <>
        <PageHeader title="Snapshots" sub="Saved copies of the forecast that never change, for comparison and export." />
        <SnapshotsView onSessionEnded={onSessionEnded} />
      </>
    )
  else if (view === 'actuals')
    body = (
      <>
        <PageHeader title="Actuals" sub="Actual revenue by month, as uploaded by Finance." />
        <ActualsView canUpload={hasRole(user, ...ACTUALS_LOADERS)} onSessionEnded={onSessionEnded} />
      </>
    )
  else if (view === 'accuracy')
    body = (
      <>
        <PageHeader title="Accuracy" sub="How closely earlier forecasts matched actual revenue." />
        <AccuracyView onSessionEnded={onSessionEnded} />
      </>
    )
  else
    body = (
      <>
        <PageHeader title="Monthly grid" sub="Every project by month. Double-click a month to edit it where you are an owner." />
        <ForecastWorkspace onSessionEnded={onSessionEnded} />
      </>
    )

  return (
    <div className="shell">
      <aside className="side">
        <div className="wordmark">
          <Logo />
          <span>Biggie Revenue</span>
        </div>
        <nav className="nav" aria-label="Main">
          {experiences.map((e) => nav(e, EXPERIENCE_LABEL[e](user), EXPERIENCE_ICON[e]))}
          <div className="nav-group">Reports and checks</div>
          {secondary.map(([id, label, icon]) => nav(id, label, icon))}
        </nav>
        <div className="side-foot">
          <div className="who">
            <div className="av" aria-hidden="true">
              {initials(user.name)}
            </div>
            <div>
              <div className="who-n">{user.name}</div>
              <div className="who-r">{user.roles.join(', ') || 'No role assigned'}</div>
            </div>
          </div>
          <button type="button" className="linkbtn" onClick={onSignOut}>
            Sign out
          </button>
        </div>
      </aside>
      <main className="main" id="main">
        {body}
      </main>
    </div>
  )
}

export default function App() {
  const [auth, setAuth] = useState<AuthState>({ kind: 'loading' })
  const [attempt, setAttempt] = useState(0)

  useEffect(() => {
    const controller = new AbortController()
    const authError = takeAuthError()
    fetchAuth(controller.signal)
      .then((status) =>
        setAuth(status.authenticated && status.user ? { kind: 'signedIn', user: status.user } : { kind: 'signedOut', error: authError }),
      )
      .catch((error: unknown) => {
        if (!isAbort(error)) setAuth({ kind: 'unreachable' })
      })
    return () => controller.abort()
  }, [attempt])

  const onSessionEnded = useCallback(() => setAuth({ kind: 'signedOut', error: 'session_ended' }), [])

  async function handleSignOut() {
    try {
      await signOut()
    } finally {
      setAuth({ kind: 'signedOut', error: null })
    }
  }

  if (auth.kind === 'signedOut') return <SignIn error={auth.error} />
  if (auth.kind === 'loading')
    return (
      <div className="main">
        <Loading text="Checking your session…" />
      </div>
    )
  if (auth.kind === 'unreachable')
    return (
      <div className="main">
        <ErrorState
          text="We could not check whether you are signed in. The service may be down. Try again in a moment."
          onRetry={() => {
            setAuth({ kind: 'loading' })
            setAttempt((n) => n + 1)
          }}
        />
      </div>
    )

  return (
    <WorkspaceProvider onSessionEnded={onSessionEnded}>
      <Signed user={auth.user} onSignOut={() => void handleSignOut()} onSessionEnded={onSessionEnded} />
    </WorkspaceProvider>
  )
}
