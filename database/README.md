# EcoVision AI — Database

Today the app runs on SQLite (`database/schema.sql`, `database/db.py`). This
folder adds a parallel, production-ready **Supabase/PostgreSQL** schema as a
set of ordered migrations, mirroring every table currently in
`schema.sql`, plus the new notifications feature.

Porting `database/db.py` itself from `sqlite3` to the Supabase client is a
separate, later piece of work — running these migrations does not change
what the running app uses today.

## Migration order

| File | Purpose |
|---|---|
| `001_init_schema.sql` | Extensions + `users` (ports the SQLite `users` table; adds nullable Google-OAuth-readiness columns) |
| `002_categories.sql` | `categories` table + the 9 default waste categories |
| `003_complaints.sql` | `complaints` + `complaint_timeline` |
| `004_rewards.sql` | `rewards` (Green Rewards ledger) |
| `005_prakriti_ai_chat.sql` | `chat_history` — Prakriti AI Connect, including the existing `language` column used for translation |
| `006_recycling_centres.sql` | `recycling_centres` table + the 4 default centres |
| `007_carbon_calculations.sql` | `carbon_records` (Carbon Calculator) |
| `008_security_and_audit.sql` | `login_attempts` + `audit_log` |
| `009_notifications.sql` | **New:** `notifications` + `notification_recipients` — backs the admin "Create Notification" screen and the user dashboard's 🔔 bell |
| `010_indexes_and_rls.sql` | Row Level Security policies for every table above |

Run them in numeric order in the Supabase SQL Editor (**Project → SQL
Editor → New query**), one file at a time. Every file is idempotent
(`if not exists` / `on conflict do nothing`) so re-running a file is safe.

## What was deliberately *not* changed

- Table and column names match the existing SQLite schema wherever
  possible (e.g. `chat_history`, `complaint_timeline`, `audit_log`) so the
  eventual `db.py` port is a smaller diff.
- The `language` column on `chat_history` — the existing Prakriti AI
  translation mechanism — is carried over unmodified.
- No admin credentials are seeded by these migrations. The current
  SQLite build seeds a default admin in Python
  (`database/db.py::_seed_admin`) using PBKDF2-HMAC-SHA256, which isn't
  something plain SQL can reproduce compatibly. Admin bootstrapping for
  Supabase will ship with the authentication piece instead of being
  hardcoded into a committed migration file.

## Row Level Security

`010_indexes_and_rls.sql` enables RLS on every table and adds policies
keyed off a new `users.auth_user_id` column that links an EcoVision user
to a Supabase Auth user. That link is populated once the authentication
piece wires up Google/Supabase Auth — until then, the app talks to
Postgres with the Supabase **service role** key (which bypasses RLS), so
enabling these policies now has no effect on the running app.

## Environment variables

Once you create a Supabase project, add these (Streamlit secrets or
`.env` — never commit real values):

```
USE_SUPABASE=false        # see "Step 5A" section below — leave as false for now
SUPABASE_URL=https://<your-project-ref>.supabase.co
SUPABASE_ANON_KEY=...
SUPABASE_SERVICE_ROLE_KEY=...
```

## Storage

Complaint/waste images and generated reports should go into Supabase
Storage buckets, with only the object path stored in `complaints.image_path`
— not implemented yet (see "Not yet done" below).

## Step 5A — Supabase client (preparation layer, added but not active)

`database/supabase_client.py` provides `get_client()` / `get_admin_client()`
/ `health_check()`, built on the official `supabase-py` package
(`requirements.txt`). **Nothing in the running application calls this
module yet** — `database/db.py`'s SQLite implementation is still the
only active backend, for every table, on every page.

A `USE_SUPABASE` flag exists (`config/settings.py`, defaults to
`False`) as the intended future cutover switch. Setting Supabase
credentials in `.env` / secrets today does **not** change app behavior
by itself — it only makes `database/supabase_client.py`'s functions
*able* to connect, for testing in isolation, ahead of the actual
cutover.

### Not yet done (tracked for later steps)
- No live data has been migrated from SQLite to Supabase.
- RLS policies (`010_indexes_and_rls.sql`) are still unactivated — see the Step 5 audit's RLS/Auth section for why (the `auth_user_id` bridge to this app's own authentication doesn't exist yet).
- No Supabase Storage bucket exists; complaint images/videos still save to local disk (`assets/uploads/`).
- `backend/*.py` and `chatbot/prakriti.py` are all unmodified — still 100% SQLite.

## Step 5C — Supabase database adapter (added but not adopted)

Two new files, neither imported by the running application yet:

- **`database/db_supabase.py`** — implements `execute()` / `fetch_one()`
  / `fetch_all()` / `get_connection()` / `init_db()` with the exact same
  signatures as `database/db.py`, backed by a **direct Postgres
  connection** (via `psycopg`, using the new `SUPABASE_DB_URL` setting)
  rather than `supabase-py`. Reason: `supabase-py` wraps Supabase's REST
  API (PostgREST), which can't execute arbitrary raw parameterized SQL —
  and every call site in this codebase (`backend/*.py`,
  `chatbot/prakriti.py`, 4 page files) does exactly that. A direct
  Postgres connection lets the adapter honor that same contract with
  minimal call-site changes later. See the module's own docstring for
  full detail, including what it does and does not translate
  automatically (placeholders: yes; SQLite-specific functions like
  `datetime('now')`/`julianday()`/`strftime()`: no — those need
  per-call-site fixes in a later step, not silent string-substitution).

- **`database/db_router.py`** — the dispatcher: `USE_SUPABASE=false`
  (default) resolves to `database/db.py`; `USE_SUPABASE=true` resolves
  to `database/db_supabase.py`. **No file in `backend/`, `chatbot/`,
  `pages/`, or `app.py` imports from this router yet** — they all still
  import `database.db` directly, so this step changes zero runtime
  behavior. Adopting the router (switching those imports) is Step 5D's
  job.

**No silent fallback, by design and by test:** with `USE_SUPABASE=true`
and no Supabase credentials configured, calling `execute()` through the
router raises `SupabaseAdapterError` — it does not quietly return
SQLite results. This was explicitly tested, not just asserted.

### SQLite-specific SQL still to handle before Step 5D

Confirmed by inspecting every `execute()`/`fetch_one()`/`fetch_all()`
call site in the codebase:

| Pattern | Where | Postgres equivalent needed |
|---|---|---|
| `datetime('now')` | `backend/auth.py` (×2), `backend/complaints.py` (×3), `backend/notifications.py` (×2) | `now()` |
| `julianday(x) - julianday(y)` | `backend/analytics.py::kpi_summary()` | `EXTRACT(EPOCH FROM (x - y)) / 3600` |
| `strftime('%Y-%m', created_at)` | `backend/analytics.py::complaints_monthly_trend()` | `to_char(created_at, 'YYYY-MM')` |
| `date(created_at)` + `datetime('now', '-N days')` | `backend/analytics.py::complaints_daily_trend()` | `created_at::date` + `now() - interval 'N days'` |
| `INSERT OR IGNORE` | `database/db.py::_seed_categories()`, `backend/notifications.py::send_notification()`, `pages/8_🛠️_Admin_Dashboard.py` (add-category form) | `INSERT ... ON CONFLICT DO NOTHING` |

None of these were modified in Step 5C — they still run correctly against SQLite exactly as before. They will raise a clear Postgres error (undefined function/syntax) if ever run unmodified through `db_supabase.py`, rather than silently misbehaving.

## Step 5D-1 — PostgreSQL compatibility preparation (added but not adopted)

Two more additions, again neither imported by the running application:

- **`database/sql_dialect.py`** — pure functions (`now_expr()`,
  `age_hours_expr()`, `month_trunc_expr()`, `date_expr()`,
  `now_minus_days_expr()`, `insert_ignore_sql()`,
  `failed_attempts_since_param()`) that return the correct SQL fragment
  or parameter value for either `"sqlite"` or `"postgres"`. This is the
  "clean abstraction" for the 5 SQLite-specific constructs found in
  Step 5C, plus one more found while re-auditing every call site in
  this step (see below). **Not wired into any `backend/*.py` call site
  yet** — that's a later, feature-specific step, done one call site at
  a time.
- **`complaints.status` CHECK constraint** — implemented and tested
  (deferred in Step 5C as optional). `database/schema.sql` now includes
  the constraint directly for fresh databases; `database/db.py::
  _ensure_complaints_status_check()` safely migrates an existing
  database via SQLite's documented table-rebuild procedure, with a
  data-safety guard that skips (never crashes or coerces data) if any
  row already has an out-of-range status value.
- **`tests/test_db_compatibility.py`** — a real test suite (26 tests,
  `python3 -m unittest tests.test_db_compatibility -v`): SQLite
  regression coverage for every feature (auth, complaints, rewards,
  chat history, notifications, the direct-query pages), the status
  migration (fresh DB / pre-existing DB with FK-referencing child rows
  / idempotency / bad-data skip), the dialect helpers (SQLite branches
  executed for real; Postgres branches text-verified only), and the
  router's no-silent-fallback guarantee (via isolated subprocesses, so
  `USE_SUPABASE` is never touched for the real environment).

### One more incompatibility found in this step

`backend/auth.py::_recent_failed_attempts()` formats a Python
`datetime` as a fixed string and compares it against
`login_attempts.created_at` as a `?` parameter — works today because
SQLite stores that column as `TEXT`, but Postgres's `timestamptz`
column would need a real `datetime` object (or an explicit cast), not
a formatted string, for this to compare correctly. Added to
`sql_dialect.py` as `failed_attempts_since_param()`; not yet applied to
`backend/auth.py`.

## Step 5D-2 — Users/authentication data migration (tool built, not run)

**`database/migrate_users.py`** — a standalone migration utility, not
imported by `app.py`, `database/db.py`, `database/db_router.py`,
`backend/auth.py`, or any page. `USE_SUPABASE` stays `false`
regardless of whether this tool exists or has been run.

- Copies rows from SQLite's `users` table into Supabase's `users`
  table (schema per `database/migrations/001_init_schema.sql`),
  preserving `id` exactly (via Postgres's `OVERRIDING SYSTEM VALUE` on
  the identity column — required, since 9 other tables have foreign
  keys to `users.id`), and copying `password_hash`/`salt` byte-for-byte
  (no rehashing).
- `migrate_users(dry_run=True)` (the default) only plans and reports —
  writes nothing. `dry_run=False` actually migrates, one row per
  `SAVEPOINT` so a single row's failure never aborts the batch.
- Never overwrites an existing Supabase row — any id/email/google_id
  conflict is skipped and reported with a reason (see the module's own
  docstring for the full conflict matrix).
- Does **not** create Supabase Auth users — `auth_user_id` is left
  `NULL` for every migrated row, per Step 5B's deferred-bridge decision.
- Migrates only the `users` table — complaints, rewards, chat history,
  notifications, carbon records, recycling centres, and files are all
  explicitly out of scope for this step.

**Test suite:** `tests/test_users_migration.py` (21 tests) — pure-logic
tests for the conflict matrix and ID/role/hash preservation, plus
stub-connection tests (a recorded fake, not a live database) verifying
the exact generated SQL, parameter values, and `SAVEPOINT`/`ROLLBACK TO
SAVEPOINT` transaction control. Nothing here required or used a live
Postgres/Supabase connection — that remains explicitly untested until
one is available.

## Step 5D-3 — Remaining-tables data migration (tool built, not run)

**`database/migrate_data.py`** — migrates every table except `users`
(handled separately by `migrate_users.py`, Step 5D-2 — run that first).
Not imported by the running application; `USE_SUPABASE` stays `false`.

- **Dependency order is discovered, not assumed**:
  `discover_dependency_graph()` parses the actual `REFERENCES` clauses
  out of `database/schema.sql` at call time and topologically sorts
  them. Verified against the real shipped schema (not a guess):
  `audit_log`, `carbon_records`, `categories`, `chat_history`,
  `complaints`, `login_attempts`, `notifications`,
  `recycling_centres`, `rewards` → then `complaint_timeline` (needs
  `complaints`) → then `notification_recipients` (needs
  `notifications`).
- Same conflict policy, ID-preservation (`OVERRIDING SYSTEM VALUE` +
  sequence repair), and per-row `SAVEPOINT` transaction safety as
  `migrate_users.py`. `notification_recipients` additionally checks its
  `UNIQUE(notification_id, user_id)` natural key, not just `id`, since
  a colliding pair under a different id would violate that constraint.
- **Type conversions documented and applied**: `categories.is_active`,
  `recycling_centres.is_active`, `login_attempts.success` (SQLite 0/1 →
  Postgres boolean); every `*_at`/`last_login`/`read_at`/`expires_at`
  column (SQLite naive-UTC text → a real timezone-aware UTC
  `datetime`, via `_normalize_timestamp()` — done explicitly rather
  than relying on an implicit cast, since SQLite's stored text carries
  no timezone marker).
- `audit_source_data()` — read-only SQLite-side audit: row counts,
  FK-orphan detection, invalid `complaints.status` values, and (for
  visibility only, no CHECK constraint exists on it)
  `chat_history.language` values in use.
- `audit_complaint_image_paths()` — reports how many
  `complaints.image_path` values point at files that actually exist
  under `assets/uploads/` vs. are missing. **Migrates zero files** —
  Supabase Storage migration is explicitly a separate, later step.
- Everything else (`password_hash`, `salt`, `email`, `role`, reward
  points, complaint descriptions/status, chat messages/language,
  notification content) is copied through completely unchanged.

**Test suite:** `tests/test_data_migration.py` (27 tests) — dependency
graph discovery (against both a fixture schema and the real shipped
`database/schema.sql`), boolean/timestamp conversion, conflict
handling (including the natural-key case), and stub-connection tests
for the write path (ID preservation, rollback-on-error,
dry-run-makes-zero-writes, sequence-repair SQL, idempotent re-run).
Audit functions are tested against a real temporary SQLite database
(not stubbed, since they need no Supabase connection). No live
Postgres/Supabase connection was available or used.

## Step 5D-4 — Migration verification / readiness (read-only, tool built, not run live)

**`database/verify_migration.py`** — the last piece before any real
cutover decision: confirms whether a migration actually landed
correctly, without migrating or modifying anything itself. Not
imported by the running application; `USE_SUPABASE` stays `false`.

**Reuses, rather than duplicates**, Step 5D-3's already-tested
`database.migrate_data.compare_counts()` / `compare_id_sets()` (both
work for any table name, including `users` — no new code needed for
that) and its `TABLE_SPECS` metadata for foreign-key columns. This
module adds only what didn't already exist: post-migration FK
integrity checking on the Supabase side, `complaints.status` /
`chat_history.language` / `complaints.image_path` validation, a
type-conversion sanity check (confirms Supabase actually stores a real
`bool`/`datetime`, not a leftover `0/1` or string), and a single
PASS/FAIL report format.

### What it checks
- Row counts and ID sets (missing / unexpected / matched) for `users`
  plus all 11 Step 5D-3 tables.
- Foreign-key integrity — for every `REFERENCES` column
  `TABLE_SPECS` knows about (covers all 9 references to `users.id`,
  plus `complaint_timeline → complaints` and
  `notification_recipients → notifications`), confirms every non-null
  FK value in Supabase actually resolves to a real row.
- `complaints.status` values are within the allowed set.
- `chat_history.language` distinct values (reporting only — no CHECK
  constraint restricts this column).
- `complaints.image_path` — the **string** migrated unchanged
  (row-by-row comparison by id). This does **not** check whether the
  underlying file exists anywhere — no file is read, copied, or
  touched by this module.
- Type conversions actually stuck: every declared boolean/timestamp
  column comes back from Supabase as a real Python `bool`/`datetime`,
  not a raw `0/1` or a string.

### How to run it
```python
from database.verify_migration import verify_all

# Quick readiness check -- confirms Supabase is configured and
# reachable, but does NOT run the full per-table comparison:
verify_all(dry_run=True)

# Full verification -- opens one real connection, checks every table:
result = verify_all(dry_run=False)
print(result["overall"])   # "PASS" or "FAIL"
for table, report in result["tables"].items():
    print(table, report.status, report.notes)
```
Both calls require `SUPABASE_DB_URL` to be configured (same variable
`database/db_supabase.py` uses — see the Step 5C section above) — this
tool does not introduce a second way to connect to Postgres.

### When Supabase isn't configured
Every function raises `database.db_supabase.SupabaseAdapterError`
immediately — `dry_run=True` included. There is no code path that
returns a fake "PASS" or silently skips the check because credentials
are missing.

### PASS/FAIL interpretation
A table's `TableVerificationReport.status` is `"FAIL"` if **any** of:
row-count mismatch, any missing/unexpected id, any FK violation, any
invalid status value, or any type-conversion issue. Otherwise `"PASS"`.
`verify_all()`'s overall status is `"PASS"` only if every checked table
passed.

### Guarantees
- **Read-only on both databases** — verified by a dedicated test that
  every SQL statement issued during a full run is a `SELECT`.
- **Does not modify data** — no `INSERT`/`UPDATE`/`DELETE` appears
  anywhere in this module.
- **SQLite remains the live backend** throughout and after this step —
  this module never touches `database/db.py`, `database/db_router.py`,
  or any application code path.

**Test suite:** `tests/test_migration_verification.py` (20 tests) —
matching/mismatched counts, missing/unexpected IDs, FK violations
(including the `notification_recipients` relationship), status
validation, boolean/timestamp type-conversion checks, chat-language
reporting, image-path comparison, dry-run behavior, the no-write
guarantee (asserted by inspecting every recorded SQL statement), and
explicit confirmation that no live Supabase connection was configured
or used anywhere in this suite. All via stub connections (recorded
fakes, not a live database) plus real temporary SQLite databases for
the SQLite-side reads — the same pattern used throughout Steps
5D-2/5D-3.

## Step 5E — Dispatcher wiring (implemented, `USE_SUPABASE` still `false`)

The 11 files that previously imported `database.db` directly now
import `database.db_router` instead: `app.py`, `utils/helpers.py`,
`backend/auth.py`, `backend/complaints.py`, `backend/analytics.py`,
`backend/notifications.py`, `chatbot/prakriti.py`, and the 4 pages
with direct queries (`8` Admin Dashboard, `10` Recycling Guide, `11`
Carbon Calculator, `13` Recycling Centres). Only the import line
changed in each file — zero business logic touched.

`database/db_router.py` itself (Step 5C) is unchanged: `USE_SUPABASE
=false` (the default) resolves every one of those files' `execute()`/
`fetch_one()`/`fetch_all()`/`get_connection()`/`init_db()` calls to the
real `database.db` (SQLite) implementation — verified by a subprocess
test that inspects `execute.__module__` and confirms it's literally
`"database.db"`, not just that it *behaves* the same. `USE_SUPABASE=
true` resolves to `database.db_supabase` instead and raises
`SupabaseAdapterError` — never silently falls back — when Supabase
isn't configured, confirmed the same way through `backend.auth` (a
real application module) rather than only through the router directly.

The migration/verification tools (`migrate_users.py`, `migrate_data.py`,
`verify_migration.py`) deliberately still import `database.db`
directly — they always read the SQLite *source of truth* for a
migration, regardless of what the live app is currently pointed at.

**Test suite:** `tests/test_dispatcher_wiring.py` (8 tests) — confirms
by reading file text (not import side effects) that every intended
file was switched and no unintended file still imports `database.db`
directly (a repo-wide scan, not just the fixed file list); confirms via
fresh subprocesses that an actual application module resolves to the
correct backend under both flag states and fails loudly, never
silently, when `USE_SUPABASE=true` has no credentials; and runs a full
register→login flow end-to-end through the new import path.

## Step 5F — Dialect-aware SQL at the real call sites (implemented)

The 6 SQLite-specific SQL constructs identified since Step 5D-1 are now
fixed **at their exact call sites**, using `database/sql_dialect.py`'s
existing pure functions plus one new convenience,
`current_dialect()` (returns `"sqlite"` or `"postgres"` based on
`database.db_router.active_backend()` — the only new code in
`sql_dialect.py`; every existing pure function is untouched).

| Call site | Before | After |
|---|---|---|
| `backend/auth.py::_recent_failed_attempts()` | Python-formatted string compared to `created_at` | `failed_attempts_since_param(minutes, current_dialect())` — a real `datetime` under Postgres |
| `backend/auth.py` — 2× `last_login` updates | `datetime('now')` | `{now_expr(current_dialect())}` |
| `backend/complaints.py` — `update_status()` ×2, `assign_officer()` | `datetime('now')` | `{now_expr(current_dialect())}` |
| `backend/analytics.py::kpi_summary()` | `julianday(resolved_at) - julianday(created_at)` | `{age_hours_expr('created_at','resolved_at', current_dialect())}` |
| `backend/analytics.py::complaints_daily_trend()` | `date(created_at)`, `datetime('now','-N days')` | `{date_expr(...)}`, `{now_minus_days_expr(...)}` |
| `backend/analytics.py::complaints_monthly_trend()` | `strftime('%Y-%m', created_at)` | `{month_trunc_expr('created_at', current_dialect())}` |
| `backend/notifications.py::send_notification()` fan-out | `INSERT OR IGNORE` | `insert_ignore_sql("notification_recipients", [...], [...], current_dialect())` |
| `backend/notifications.py` — `mark_read()`, `mark_all_read()` | `datetime('now')` | `{now_expr(current_dialect())}` |
| `pages/8` Admin Dashboard — add-category form | `INSERT OR IGNORE` | `insert_ignore_sql("categories", [...], [...], current_dialect())` |

**Deliberately left untouched:** `database/db.py`'s own `datetime('now')`
defaults and `INSERT OR IGNORE` seeding calls, and `database/schema.sql`'s
column `DEFAULT (datetime('now'))` clauses — these are SQLite's own
backend implementation and schema, never executed against Postgres
(Supabase's schema is provisioned via `database/migrations/*.sql`
separately, not by running SQLite's `schema.sql`/`db.py` against it).

**Test suite:** `tests/test_dialect_call_sites.py` (26 tests) — every
fixed call site re-executed against a real temporary SQLite database
(unchanged behavior confirmed, not assumed); Postgres-dialect SQL text
verified for every site (explicitly **not** run against a live
connection — none is available); and a repository-wide scan confirming
no SQLite-specific SQL *string* remains outside the three files it's
supposed to stay in (`database/db.py`, `database/sql_dialect.py`,
`database/db_supabase.py`'s docstring), while confirming the
legitimate pure-Python `.strftime()` calls elsewhere were correctly
left alone.

## Step 5G — Real migration (execution guide, not yet run)

**`database/preflight_5g.py`** — one command
(`from database.preflight_5g import run_preflight`) combining
config/SDK/reachability checks with a side-by-side SQLite-vs-Supabase
row-count and table-existence report for every migrated table. Never
writes; never prints a secret value (tested explicitly). Returns
cleanly with `ready_for_dry_run: false` and a clear reason when nothing
is configured — confirmed offline, since no real Supabase project
exists in this environment.

**For the full command sequence, STOP conditions, PASS/FAIL criteria,
and rollback instructions, see [`database/RUNBOOK_5G.md`](./RUNBOOK_5G.md).**
Nothing in this codebase executes any of those steps automatically —
every one is a command a human operator runs explicitly, in order.
