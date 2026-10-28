"""PostgreSQL advisory-lock keys, so independent jobs agree on which one runs at a time."""

SYNC_LOCK_KEY = 7_301_001  # HubSpot sync (Milestone 3)
SNAPSHOT_LOCK_KEY = 7_301_002  # forecast snapshot (Milestone 4)
QA_LOCK_KEY = 7_301_003  # QA checks (Milestone 5)
