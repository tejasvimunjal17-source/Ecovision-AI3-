"""
backend/notifications.py
---------------------------
Service layer for the notification system (Step 4). Talks to the
`notifications` / `notification_recipients` tables added to
database/schema.sql — the same design as
database/migrations/009_notifications.sql from Step 1, just executed
against SQLite (today's live database) instead of Postgres.

Authorization note: every function here takes an explicit user_id
(always st.session_state["user"]["id"] from the caller — see the
require_login()-gated pages that call these functions) and every read
query filters by that user_id. No query here ever trusts a
client-supplied user id for *whose* notifications to return — the same
pattern already used by backend/complaints.py's get_user_complaints().
This is the same access boundary the Postgres RLS policies in
migration 010 encode declaratively for a future direct-to-Supabase
client; here it's enforced in this one, narrow, reused module instead
of scattered across pages.
"""
from database.db_router import execute, fetch_all, fetch_one
from database.sql_dialect import current_dialect, now_expr, insert_ignore_sql

VALID_AUDIENCES = ("all", "citizens", "officers", "selected_users")
VALID_TYPES = ("general", "complaint_update", "announcement", "reward", "account")
VALID_PRIORITIES = ("low", "normal", "high")


def _resolve_recipient_ids(audience: str, selected_user_ids=None) -> list[int]:
    if audience == "all":
        rows = fetch_all("SELECT id FROM users WHERE is_active=1")
    elif audience == "citizens":
        rows = fetch_all("SELECT id FROM users WHERE role='citizen' AND is_active=1")
    elif audience == "officers":
        rows = fetch_all("SELECT id FROM users WHERE role='officer' AND is_active=1")
    elif audience == "selected_users":
        rows = [{"id": uid} for uid in (selected_user_ids or [])]
    else:
        raise ValueError(f"Unknown audience: {audience}")
    return [r["id"] for r in rows]


def send_notification(title: str, message: str, created_by: int, audience: str = "all",
                       type_: str = "general", priority: str = "normal", selected_user_ids=None):
    """
    Creates one `notifications` row, resolves the chosen audience into
    concrete recipient ids, and fans out one `notification_recipients`
    row per recipient (unread by default — read_at stays NULL until
    that user opens/marks it). Returns (True, recipient_count) or
    (False, error_message).
    """
    title = (title or "").strip()
    message = (message or "").strip()
    if not title or not message:
        return False, "Title and message are both required."
    if audience not in VALID_AUDIENCES:
        return False, f"Invalid audience: {audience}"
    if type_ not in VALID_TYPES:
        return False, f"Invalid type: {type_}"
    if priority not in VALID_PRIORITIES:
        return False, f"Invalid priority: {priority}"
    if audience == "selected_users" and not selected_user_ids:
        return False, "Select at least one user for a targeted notification."

    recipient_ids = _resolve_recipient_ids(audience, selected_user_ids)
    if not recipient_ids:
        return False, "No matching recipients found for this audience."

    notification_id = execute(
        """INSERT INTO notifications (title, message, type, audience, priority, created_by)
           VALUES (?,?,?,?,?,?)""",
        (title, message, type_, audience, priority, created_by),
    )
    for uid in recipient_ids:
        execute(
            insert_ignore_sql("notification_recipients", ["notification_id", "user_id"],
                               ["notification_id", "user_id"], current_dialect()),
            (notification_id, uid),
        )
    return True, len(recipient_ids)


def get_user_notifications(user_id: int, limit: int = 20):
    """Most recent notifications for this user, newest first, with each
    recipient row's own read/unread state."""
    return fetch_all(
        """SELECT n.id, n.title, n.message, n.type, n.priority, n.created_at,
                  nr.read_at
           FROM notification_recipients nr
           JOIN notifications n ON n.id = nr.notification_id
           WHERE nr.user_id = ?
           ORDER BY n.created_at DESC
           LIMIT ?""",
        (user_id, limit),
    )


def get_unread_count(user_id: int) -> int:
    row = fetch_one(
        "SELECT COUNT(*) as c FROM notification_recipients WHERE user_id=? AND read_at IS NULL",
        (user_id,),
    )
    return row["c"] if row else 0


def mark_read(notification_id: int, user_id: int):
    """Scoped to (notification_id, user_id) — a user can only ever mark
    their own recipient row, never someone else's, even if a stray
    notification_id from another user's list were passed in."""
    execute(
        f"UPDATE notification_recipients SET read_at={now_expr(current_dialect())} "
        "WHERE notification_id=? AND user_id=? AND read_at IS NULL",
        (notification_id, user_id),
    )


def mark_all_read(user_id: int):
    execute(
        f"UPDATE notification_recipients SET read_at={now_expr(current_dialect())} "
        "WHERE user_id=? AND read_at IS NULL",
        (user_id,),
    )


def list_sent_notifications(limit: int = 50):
    """For the Admin Panel's notification log — includes a per-notification
    recipient/read tally, not any individual recipient's identity."""
    return fetch_all(
        """SELECT n.id, n.title, n.type, n.audience, n.priority, n.created_at,
                  u.full_name as sent_by,
                  COUNT(nr.id) as recipients,
                  SUM(CASE WHEN nr.read_at IS NOT NULL THEN 1 ELSE 0 END) as read_count
           FROM notifications n
           LEFT JOIN users u ON u.id = n.created_by
           LEFT JOIN notification_recipients nr ON nr.notification_id = n.id
           GROUP BY n.id
           ORDER BY n.created_at DESC
           LIMIT ?""",
        (limit,),
    )
