export type SaveOutcome = { ok: true; changed: boolean } | { ok: false; message: string }

/** Save one month of a project's forecast. The workspace implements it for grid and panel alike. */
export type SaveAmount = (
  projectId: number,
  month: string,
  raw: string,
  reason?: string,
) => Promise<SaveOutcome>
