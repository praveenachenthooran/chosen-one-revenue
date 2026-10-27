import '@testing-library/jest-dom/vitest'
import { configure } from '@testing-library/react'

// AG Grid's first render in jsdom can take over a second on a cold start.
configure({ asyncUtilTimeout: 4000 })
