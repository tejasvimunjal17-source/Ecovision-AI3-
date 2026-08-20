"""
database/db.py
----------------
Thin SQLite access layer. Every query goes through get_connection()
so we get consistent foreign-key enforcement, row-dict access, and a
single place to add logging or swap databases later.

SQL Injection protection: every query in this codebase uses
parameterized placeholders ("?") — never f-string / .format() query
building with untrusted input.
"""
import sqlite3
import logging
from pathlib import Path
from contextlib import contextmanager

from config import settings

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ecovision.db")


def _row_factory(cursor, row):
    return {col[0]: row[idx] for idx, col in enumerate(cursor.description)}


@contextmanager
def get_connection():
    Path(settings.DATABASE_PATH).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(settings.DATABASE_PATH, timeout=10, check_same_thread=False)
    conn.row_factory = _row_factory
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        logger.exception("Database error — transaction rolled back")
        raise
    finally:
        conn.close()


def init_db():
    """Create all tables if they don't exist yet, and seed defaults."""
    schema_path = Path(__file__).parent / "schema.sql"
    with get_connection() as conn:
        conn.executescript(schema_path.read_text())
    _ensure_google_auth_columns()
    _ensure_complaints_status_check()
    _seed_categories()
    _seed_recycling_centres()
    _seed_admin()


def _ensure_google_auth_columns():
    """
    Additive migration for databases created before Google OAuth support
    was added (schema.sql's CREATE TABLE IF NOT EXISTS only applies to a
    brand-new users table, so an already-existing ecovision.db needs these
    two nullable columns added explicitly). Safe/idempotent: SQLite has no
    'ADD COLUMN IF NOT EXISTS', so a duplicate-column error is simply
    ignored.
    """
    with get_connection() as conn:
        for stmt in (
            "ALTER TABLE users ADD COLUMN auth_provider TEXT NOT NULL DEFAULT 'email'",
            "ALTER TABLE users ADD COLUMN google_id TEXT UNIQUE",
        ):
            try:
                conn.execute(stmt)
            except sqlite3.OperationalError as e:
                if "duplicate column" not in str(e).lower():
                    raise


COMPLAINT_STATUSES = ("Submitted", "Under Review", "Assigned", "In Progress", "Resolved", "Rejected")


def _ensure_complaints_status_check():
    """
    Idempotent, additive migration (Step 5D-1 / Step 5B follow-up): adds
    CHECK(status IN (...)) to complaints.status for databases created
    before this constraint existed in schema.sql (this fixes the SQLite
    vs. Postgres schema drift flagged in the Step 5 audit -- the
    Postgres migration already had this constraint). SQLite can't ALTER
    a column's constraints directly, so this uses SQLite's own
    documented procedure for such changes: disable FK enforcement,
    rebuild the table with the constraint, copy every row across
    unchanged, drop the old table, rename the new one, restore FK
    enforcement, then verify referential integrity. Uses a dedicated
    connection in autocommit mode (required -- PRAGMA foreign_keys is a
    no-op inside a pending transaction) with an explicit BEGIN/COMMIT
    around the rebuild itself.

    Safety guards -- this function is a no-op (does nothing, changes
    nothing) unless ALL of the following hold:
    - The `complaints` table already exists (skip on a brand-new DB --
      schema.sql already has the constraint for those).
    - The constraint isn't already present (idempotent).
    - Every existing row's status is already one of COMPLAINT_STATUSES --
      if any row has an unexpected value, the migration is skipped
      entirely (logged as a warning) rather than risk failing partway
      through or silently coercing/dropping data.
    - The rebuild is wrapped in BEGIN/COMMIT with a foreign_key_check
      before committing; any failure raises (which also triggers the
      surrounding ROLLBACK), leaving the original table completely
      untouched.
    """
    conn = sqlite3.connect(settings.DATABASE_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='complaints'"
        ).fetchone()
        if row is None:
            return  # no complaints table yet -- nothing to migrate

        existing_sql = row["sql"] or ""
        if "CHECK (status IN" in existing_sql or "CHECK(status IN" in existing_sql:
            return  # already migrated

        placeholders = ",".join("?" for _ in COMPLAINT_STATUSES)
        bad_count = conn.execute(
            f"SELECT COUNT(*) AS c FROM complaints WHERE status NOT IN ({placeholders})",
            COMPLAINT_STATUSES,
        ).fetchone()["c"]
        if bad_count:
            logger.warning(
                "Skipping complaints.status CHECK-constraint migration: %d existing row(s) have "
                "a status value outside %s. Fix that data, then restart to retry.",
                bad_count, COMPLAINT_STATUSES,
            )
            return

        conn.isolation_level = None  # autocommit -- required for PRAGMA foreign_keys to take effect
        conn.execute("PRAGMA foreign_keys = OFF")
        conn.execute("BEGIN")
        try:
            # NOTE: SQLite prohibits bound parameters inside a CHECK
            # constraint's definition (DDL), so the status list must be
            # inlined as literals here -- safe because COMPLAINT_STATUSES
            # is a fixed, hardcoded, internal tuple, never user input.
            status_literals = ",".join(f"'{s}'" for s in COMPLAINT_STATUSES)
            conn.execute(f"""
                CREATE TABLE complaints_new (
                    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id             INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    category            TEXT NOT NULL,
                    ai_predicted_category TEXT,
                    ai_confidence       REAL,
                    description         TEXT,
                    ai_description      TEXT,
                    priority            TEXT DEFAULT 'Medium' CHECK (priority IN ('Low','Medium','High')),
                    status              TEXT DEFAULT 'Submitted' CHECK (status IN ({status_literals})),
                    image_path          TEXT,
                    latitude            REAL,
                    longitude           REAL,
                    ward                TEXT,
                    address_text        TEXT,
                    assigned_officer_id INTEGER REFERENCES users(id),
                    assigned_worker     TEXT,
                    officer_notes       TEXT,
                    created_at          TEXT DEFAULT (datetime('now')),
                    updated_at          TEXT DEFAULT (datetime('now')),
                    resolved_at         TEXT
                )
            """)
            conn.execute("""
                INSERT INTO complaints_new (id, user_id, category, ai_predicted_category, ai_confidence,
                    description, ai_description, priority, status, image_path, latitude, longitude,
                    ward, address_text, assigned_officer_id, assigned_worker, officer_notes,
                    created_at, updated_at, resolved_at)
                SELECT id, user_id, category, ai_predicted_category, ai_confidence,
                    description, ai_description, priority, status, image_path, latitude, longitude,
                    ward, address_text, assigned_officer_id, assigned_worker, officer_notes,
                    created_at, updated_at, resolved_at
                FROM complaints
            """)
            conn.execute("DROP TABLE complaints")
            conn.execute("ALTER TABLE complaints_new RENAME TO complaints")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_complaints_user ON complaints(user_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_complaints_status ON complaints(status)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_complaints_ward ON complaints(ward)")

            fk_violations = conn.execute("PRAGMA foreign_key_check(complaints)").fetchall()
            if fk_violations:
                raise RuntimeError(f"Foreign key check failed after complaints rebuild: {fk_violations}")

            conn.execute("COMMIT")
            row_count = conn.execute("SELECT COUNT(*) c FROM complaints").fetchone()["c"]
            logger.info(
                "Migrated complaints.status to add a CHECK constraint (%d row(s) preserved).", row_count
            )
        except Exception:
            conn.execute("ROLLBACK")
            raise
        finally:
            conn.execute("PRAGMA foreign_keys = ON")
    finally:
        conn.close()


def execute(query: str, params: tuple = ()):
    with get_connection() as conn:
        cur = conn.execute(query, params)
        return cur.lastrowid


def fetch_one(query: str, params: tuple = ()):
    with get_connection() as conn:
        cur = conn.execute(query, params)
        return cur.fetchone()


def fetch_all(query: str, params: tuple = ()):
    with get_connection() as conn:
        cur = conn.execute(query, params)
        return cur.fetchall()


def _seed_categories():
    defaults = [
        ("Plastic", "Plastic bottles, bags, wrappers, containers", "🧴",
         "Rinse and place in the dry-waste bin; drop bulk plastic at an authorized recycler."),
        ("Organic", "Food scraps, garden waste, biodegradable matter", "🍂",
         "Compost at home or place in the wet-waste (green) bin for municipal composting."),
        ("Paper", "Newspaper, cardboard, cartons, office paper", "📄",
         "Flatten and keep dry; place in the dry-waste bin or sell to a kabadiwala."),
        ("Glass", "Bottles, jars, broken glassware", "🍾",
         "Wrap broken pieces safely, place in dry-waste bin marked 'glass'."),
        ("Metal", "Cans, foil, scrap metal, utensils", "🔩",
         "Place in dry-waste bin; scrap metal can be sold to authorized scrap dealers."),
        ("Mixed", "Non-segregated general waste", "🗑️",
         "Please segregate at source; mixed waste delays processing and recycling."),
        ("E-Waste", "Batteries, electronics, wires, appliances", "🔋",
         "Never mix with household waste — drop at an authorized MCG e-waste collection centre."),
        ("Biomedical", "Medical/clinical waste, sharps, PPE", "🩺",
         "Requires special handling — contact MCG health department or an authorized biomedical waste handler."),
        ("Construction", "Debris, rubble, bricks, concrete", "🧱",
         "Book a municipal C&D waste pickup; do not dump on roads or drains."),
    ]
    with get_connection() as conn:
        for name, desc, icon, guide in defaults:
            conn.execute(
                "INSERT OR IGNORE INTO categories (name, description, icon, disposal_guide) VALUES (?,?,?,?)",
                (name, desc, icon, guide),
            )


def _seed_recycling_centres():
    centres = [
        ("MCG Material Recovery Facility - Sector 39", "Dry Waste MRF", "Sector 39, Gurugram",
         "Sector 39", 28.4501, 77.0424, "+91-124-2222222", "Plastic,Paper,Metal,Glass"),
        ("MCG E-Waste Collection Centre - Sector 14", "E-Waste", "Sector 14, Gurugram",
         "Sector 14", 28.4699, 77.0266, "+91-124-2333333", "E-Waste,Batteries"),
        ("Composting Unit - Sector 52", "Organic/Composting", "Sector 52, Gurugram",
         "Sector 52", 28.4177, 77.0729, "+91-124-2444444", "Organic"),
        ("Scrap & Metal Recyclers - Udyog Vihar", "Scrap/Metal", "Udyog Vihar Phase 3, Gurugram",
         "Udyog Vihar", 28.5017, 77.0881, "+91-124-2555555", "Metal,Glass"),
    ]
    with get_connection() as conn:
        existing = conn.execute("SELECT COUNT(*) as c FROM recycling_centres").fetchone()["c"]
        if existing == 0:
            for row in centres:
                conn.execute(
                    """INSERT INTO recycling_centres
                       (name, type, address, ward, latitude, longitude, contact, materials_accepted)
                       VALUES (?,?,?,?,?,?,?,?)""",
                    row,
                )


def _seed_admin():
    """Create one default admin account on first run (dev convenience)."""
    from backend.auth import hash_password
    with get_connection() as conn:
        existing = conn.execute("SELECT id FROM users WHERE role='admin' LIMIT 1").fetchone()
        if not existing:
            pw_hash, salt = hash_password("Admin@12345")
            conn.execute(
                """INSERT INTO users (full_name, email, phone, password_hash, salt, role, ward)
                   VALUES (?,?,?,?,?,?,?)""",
                ("System Administrator", "admin@ecovision.local", "9999999999",
                 pw_hash, salt, "admin", "HQ"),
            )
            logger.info("Seeded default admin: admin@ecovision.local / Admin@12345 (change immediately)")
