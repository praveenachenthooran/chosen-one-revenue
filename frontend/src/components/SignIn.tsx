import { Logo } from '../ui/Logo'

const MESSAGES: Record<string, string> = {
  not_provisioned:
    'That Google account has not been given access to Biggie Revenue. Ask an administrator to provision your access, then try again.',
  email_unverified: 'Google reports that the email address on that account is not verified.',
  wrong_domain: 'That Google account does not belong to the organization allowed to sign in.',
  not_configured:
    'Google sign-in has not been configured on this server yet. An administrator needs to set the Google credentials.',
  failed: 'Sign-in could not be completed. Please try again.',
  session_ended: 'Your session has ended. Please sign in again.',
}

function authErrorMessage(code: string): string {
  return MESSAGES[code] ?? MESSAGES.failed
}

/** The faceted artwork from the reference's sign-in panel. */
function Facets() {
  return (
    <svg className="facets" viewBox="0 0 600 800" preserveAspectRatio="xMidYMid slice" aria-hidden="true">
      <polygon points="320,0 600,0 600,260 430,180" fill="#fff" opacity=".07" />
      <polygon points="430,180 600,260 600,520 470,430" fill="#fff" opacity=".12" />
      <polygon points="320,0 430,180 260,240" fill="#fff" opacity=".04" />
      <polygon points="260,240 430,180 470,430 300,470" fill="#9FE3E6" opacity=".10" />
      <polygon points="470,430 600,520 600,800 420,800" fill="#fff" opacity=".06" />
      <polygon points="300,470 470,430 420,800 240,800" fill="#CFF3F4" opacity=".08" />
      <polygon points="0,560 300,470 240,800 0,800" fill="#fff" opacity=".035" />
      <polyline points="300,470 360,420 410,440 470,360 530,380 600,300" fill="none" stroke="#9FE3E6" strokeWidth="2" opacity=".55" />
      <circle cx="470" cy="360" r="5" fill="#9FE3E6" opacity=".9" />
    </svg>
  )
}

export default function SignIn({ error }: { error: string | null }) {
  return (
    <main className="login">
      <section className="login-art">
        <Facets />
        <div className="wordmark">
          <Logo />
          <span>Biggie Revenue</span>
        </div>
        <div>
          <h1>Know where every project’s revenue is heading.</h1>
          <p>
            Biggie Revenue brings the project forecast, the HubSpot pipeline and actual revenue into one place, and flags
            where the numbers don’t line up.
          </p>
          <div className="sources">
            <span>Excel forecast</span>
            <span>HubSpot pipeline</span>
            <span>Actual revenue</span>
            <span>Quality checks</span>
          </div>
        </div>
        <p className="login-foot">Bits In Glass internal tool</p>
      </section>
      <section className="login-form" aria-labelledby="signin-title">
        <div>
          <div>
            <h2 id="signin-title">Sign in</h2>
            <p className="login-lead">Use your Bits In Glass Google account.</p>
          </div>
          {error && (
            <div className="alert alert-danger py-2" role="alert">
              {authErrorMessage(error)}
            </div>
          )}
          {/* A full-page navigation: the API redirects to Google and back. */}
          <a className="btn btn-primary btn-lg" href="/api/auth/login">
            Sign in with Google
          </a>
          <p className="hint">Access is limited to people an administrator has provisioned.</p>
        </div>
      </section>
    </main>
  )
}
