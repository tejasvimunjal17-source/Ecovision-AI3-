"""
backend/complaints.py
------------------------
All complaint lifecycle operations: creation, status transitions,
officer assignment, and reward-point accrual.
"""
import logging
from database.db_router import execute, fetch_one, fetch_all
from database.sql_dialect import current_dialect, now_expr
from config import settings

logger = logging.getLogger("ecovision.complaints")


def create_complaint(user_id, category, description, ai_description="", ai_predicted_category="",
                      ai_confidence=None, priority="Medium", image_path="", latitude=None,
                      longitude=None, ward="", address_text=""):
    complaint_id = execute(
        """INSERT INTO complaints
           (user_id, category, ai_predicted_category, ai_confidence, description, ai_description,
            priority, status, image_path, latitude, longitude, ward, address_text)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (user_id, category, ai_predicted_category, ai_confidence, description, ai_description,
         priority, "Submitted", image_path, latitude, longitude, ward, address_text),
    )
    _add_timeline(complaint_id, "Submitted", "Complaint submitted by citizen", user_id)
    award_points(user_id, settings.REWARD_POINTS["complaint_submitted"], "Complaint submitted")
    logger.info("Complaint #%s created by user %s", complaint_id, user_id)
    return complaint_id


def _add_timeline(complaint_id, status, note, changed_by):
    execute(
        "INSERT INTO complaint_timeline (complaint_id, status, note, changed_by) VALUES (?,?,?,?)",
        (complaint_id, status, note, changed_by),
    )


def get_complaint(complaint_id):
    return fetch_one("SELECT * FROM complaints WHERE id=?", (complaint_id,))


def get_user_complaints(user_id, limit=100):
    return fetch_all(
        "SELECT * FROM complaints WHERE user_id=? ORDER BY created_at DESC LIMIT ?",
        (user_id, limit),
    )


def get_all_complaints(status=None, ward=None, category=None, limit=500):
    query = "SELECT c.*, u.full_name as citizen_name FROM complaints c JOIN users u ON u.id=c.user_id WHERE 1=1"
    params = []
    if status and status != "All":
        query += " AND c.status=?"
        params.append(status)
    if ward and ward != "All":
        query += " AND c.ward=?"
        params.append(ward)
    if category and category != "All":
        query += " AND c.category=?"
        params.append(category)
    query += " ORDER BY c.created_at DESC LIMIT ?"
    params.append(limit)
    return fetch_all(query, tuple(params))


def get_timeline(complaint_id):
    return fetch_all(
        "SELECT * FROM complaint_timeline WHERE complaint_id=? ORDER BY created_at ASC",
        (complaint_id,),
    )


def update_status(complaint_id, new_status, changed_by, note=""):
    execute(f"UPDATE complaints SET status=?, updated_at={now_expr(current_dialect())} WHERE id=?",
            (new_status, complaint_id))
    if new_status == "Resolved":
        execute(f"UPDATE complaints SET resolved_at={now_expr(current_dialect())} WHERE id=?", (complaint_id,))
        complaint = get_complaint(complaint_id)
        if complaint:
            award_points(complaint["user_id"], settings.REWARD_POINTS["complaint_resolved_bonus"],
                         "Complaint resolved bonus")
    _add_timeline(complaint_id, new_status, note, changed_by)


def assign_officer(complaint_id, officer_id, worker_name="", changed_by=None):
    execute(
        f"UPDATE complaints SET assigned_officer_id=?, assigned_worker=?, status='Assigned', "
        f"updated_at={now_expr(current_dialect())} WHERE id=?",
        (officer_id, worker_name, complaint_id),
    )
    _add_timeline(complaint_id, "Assigned", f"Assigned to officer #{officer_id}"
                  + (f" / worker {worker_name}" if worker_name else ""), changed_by)


def award_points(user_id, points, reason):
    execute("INSERT INTO rewards (user_id, points, reason) VALUES (?,?,?)", (user_id, points, reason))
    execute("UPDATE users SET reward_points = reward_points + ? WHERE id=?", (points, user_id))


def get_user_rewards(user_id):
    return fetch_all("SELECT * FROM rewards WHERE user_id=? ORDER BY created_at DESC", (user_id,))


def get_leaderboard(limit=20):
    return fetch_all(
        "SELECT full_name, ward, reward_points FROM users WHERE role='citizen' "
        "ORDER BY reward_points DESC LIMIT ?",
        (limit,),
    )


def get_officers():
    return fetch_all("SELECT id, full_name, ward FROM users WHERE role='officer' AND is_active=1")


def get_wards():
    rows = fetch_all("SELECT DISTINCT ward FROM complaints WHERE ward IS NOT NULL AND ward != '' ")
    return sorted([r["ward"] for r in rows])
