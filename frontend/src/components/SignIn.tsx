const MESSAGES: Record<string, string> = {
  not_provisioned:
    'That Google account has not been given access to The Chosen One. Ask an administrator to provision your access, then try again.',
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

export default function SignIn({ error }: { error: string | null }) {
  return (
    <main className="signin-page">
      <section className="signin-card" aria-labelledby="signin-title">
        <p className="eyebrow mb-2">BIG Revenue Forecasting</p>
        <h1 id="signin-title" className="h2 fw-semibold mb-2">
          The Chosen One
        </h1>
        <p className="text-secondary mb-4">Sign in with your BIG Google Workspace account to continue.</p>
        {error && (
          <div className="alert alert-danger py-2 small" role="alert">
            {authErrorMessage(error)}
          </div>
        )}
        {/* A full-page navigation: the API redirects to Google and back. */}
        <a className="btn btn-primary btn-lg w-100" href="/api/auth/login">
          Sign in with Google
        </a>
        <p className="text-secondary small mt-3 mb-0">
          Access is limited to people an administrator has provisioned.
        </p>
      </section>
    </main>
  )
}
