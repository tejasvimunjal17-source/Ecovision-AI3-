# Step 5G Runbook — SQLite → Supabase Migration

**Status: code-ready, not yet executed.** No file in this repo runs
any step below automatically — every command here must be run
explicitly by a human operator, in order, checking the STOP conditions
before moving to the next step. `USE_SUPABASE` stays `false` for the
entire sequence except the final, isolated staging test in 5G-7.

## Source vs. target

- **Source of truth, throughout:** SQLite (`database/db.py`, the file
  at `settings.DATABASE_PATH`). Never modified by anything in this
  runbook.
- **Target:** a Supabase Postgres project with migrations `001`–`010`
  (`database/migrations/*.sql`) already applied via the Supabase SQL
  Editor. **Must be empty or staging** — every step below stops if it
  finds unexpected existing data.

## Required configuration (set these — never paste secret values into a chat with Claude)

| Variable | Where | Required for |
|---|---|---|
| `SUPABASE_DB_URL` | `.env` or `.streamlit/secrets.toml` (never committed — both are gitignored) | Every step below |
| `psycopg` installed | `pip install -r requirements.txt` | Every step below |
| `SUPABASE_URL` / `SUPABASE_ANON_KEY` / `SUPABASE_SERVICE_ROLE_KEY` | same | Only if you also want the REST/Auth client path (`database/supabase_client.py`) exercised — not required for the migration tools themselves |

`USE_SUPABASE` must remain `false` in every environment variable /
secrets file until 5G-6 passes with an overall `PASS`.

---

## 5G-1 — Preflight

```python
from database.preflight_5g import run_preflight
import json
print(json.dumps(run_preflight(), indent=2, default=str))
```

**STOP if:** `sdk_installed` is `false`, `configured` is `false`,
`reachable` is `false`, `ready_for_dry_run` is `false`, or `warnings`
contains anything about existing data in a table that should be empty.

**PASS criteria:** `ready_for_dry_run: true` and `warnings: []`.

## 5G-2 — Users dry-run

```python
from database.migrate_users import migrate_users
plan = migrate_users(dry_run=True)
print([(p.sqlite_id, p.email, p.action, p.reason) for p in plan])
```

**STOP if:** any `action` is `"would_skip"` for a row you didn't
expect to conflict, or the tool raises at all.

**PASS criteria:** every row shows `"would_insert"` (a totally empty
target) — or, on a re-run, only rows already known to be migrated show
`"would_skip"`.

## 5G-3 — Users live migration

Only after 5G-2 is clean:

```python
from database.migrate_users import migrate_users
results = migrate_users(dry_run=False)
print([(r.sqlite_id, r.email, r.action, r.reason) for r in results])
```

**Immediately after, verify by hand:**
```python
from database.migrate_data import compare_counts, compare_id_sets
print(compare_counts("users"))
print(compare_id_sets("users"))
```
**PASS criteria:** `compare_counts("users")["match"] is True`,
`compare_id_sets("users")["only_in_sqlite"] == []`.

## 5G-4 — Remaining data dry-run

```python
from database.migrate_data import migrate_all
plan = migrate_all(dry_run=True)
for table, rows in plan.items():
    print(table, {r.action for r in rows}, len(rows))
```

Runs every table in the discovered `dependency_order()` automatically
— do not reorder or run tables individually unless diagnosing a
specific failure.

**STOP if:** any unexpected `"would_skip"`, or the call raises.

## 5G-5 — Remaining data live migration

Only after 5G-4 is clean:

```python
from database.migrate_data import migrate_all
results = migrate_all(dry_run=False)
for table, rows in results.items():
    errors = [r for r in rows if r.action == "error"]
    print(table, "inserted:", sum(1 for r in rows if r.action == "inserted"), "errors:", errors)
```

**STOP if:** any table reports an `"error"` row — diagnose that
specific row before continuing (do not re-run blindly; the tool is
idempotent, so re-running is safe, but understand the error first).

## 5G-6 — Verification

```python
from database.verify_migration import verify_all
result = verify_all(dry_run=False)
print(result["overall"])
for table, r in result["tables"].items():
    print(table, r.status, r.summarize() if hasattr(r, "summarize") else r.notes)
```

**STOP and diagnose if `overall` is not `"PASS"`.** Do not proceed to
5G-7 on a `FAIL`. This checks row counts, ID sets, FK integrity,
`complaints.status` validity, `chat_history.language` values, boolean/
timestamp type conversions, and `image_path` string equality — all
read-only, described in `database/README.md`'s "Step 5D-4" section.

## 5G-7 — Staging live-application test (`USE_SUPABASE=true`, staging only)

Only after 5G-6 shows `overall: PASS`. In a **staging deployment**,
not production:

```bash
export USE_SUPABASE=true
streamlit run app.py
```

Manually test: Email login, Google login (if configured), all 3
dashboards, complaint creation/update, notifications, Prakriti AI +
chat history, carbon calculator, recycling pages, rewards, analytics,
admin category management. Confirm via Supabase's own dashboard/logs
that these reads/writes are actually hitting Postgres, not a stray
SQLite fallback.

**STOP if** anything behaves differently than the SQLite-backed app,
or if any error surfaces that the local test suite didn't predict.

## 5G-8 — Production cutover

**Not part of this runbook's automated guidance — a deliberate,
separate decision** after 5G-7 has run cleanly in staging for a
reasonable soak period. Out of scope until you explicitly request it.

---

## Rollback

- **5G-1/5G-2/5G-4/5G-6:** nothing to roll back — read-only.
- **5G-3/5G-5 (live migration):** every row insert is wrapped in a
  `SAVEPOINT`; a failed row is rolled back to that savepoint
  individually and does not abort the rest of the table. If you need
  to undo an entire table's migration, delete those rows directly in
  Supabase (they're additive — SQLite is never touched, so there's
  nothing to "restore," only the Supabase side to clear) and re-run
  from 5G-4/5G-2 for that table.
- **5G-7 (staging flag flip):** revert by unsetting `USE_SUPABASE` (or
  setting it back to `false`) and restarting the app — SQLite was
  never modified, so this is an instant, complete rollback.
- **At every stage:** SQLite (`settings.DATABASE_PATH`) is the
  original source and is never written to by anything in this
  runbook.
