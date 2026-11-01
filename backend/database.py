"""
RASEED Database Layer - SQLite implementation.
Handles tables: receipts, duplicate_results, audit_actions.
"""

import sqlite3
import os
from datetime import datetime
from typing import List, Dict, Any, Optional

DB_FILE = os.path.join(os.path.dirname(__file__), "raseed.db")


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    conn = get_connection()
    cursor = conn.cursor()

    # Table 1: receipts
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS receipts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        receipt_id TEXT,
        vendor TEXT NOT NULL,
        date TEXT NOT NULL,
        amount REAL NOT NULL,
        description TEXT,
        category TEXT DEFAULT 'General Expense',
        status TEXT DEFAULT 'Normal',
        duplicate_score REAL DEFAULT 0.0,
        submitter TEXT DEFAULT 'submitter',
        image_url TEXT DEFAULT '',
        raw_text TEXT DEFAULT '',
        created_at TEXT NOT NULL
    )
    """)

    # Check and add missing columns if upgrading existing table
    cursor.execute("PRAGMA table_info(receipts)")
    existing_cols = {col["name"] for col in cursor.fetchall()}
    if "submitter" not in existing_cols:
        cursor.execute("ALTER TABLE receipts ADD COLUMN submitter TEXT DEFAULT 'submitter'")
    if "image_url" not in existing_cols:
        cursor.execute("ALTER TABLE receipts ADD COLUMN image_url TEXT DEFAULT ''")
    if "raw_text" not in existing_cols:
        cursor.execute("ALTER TABLE receipts ADD COLUMN raw_text TEXT DEFAULT ''")

    # Table 2: duplicate_results
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS duplicate_results (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        receipt_a_id INTEGER NOT NULL,
        receipt_b_id INTEGER NOT NULL,
        receipt_a_code TEXT,
        receipt_b_code TEXT,
        text_similarity REAL DEFAULT 0.0,
        amount_match REAL DEFAULT 0.0,
        date_match REAL DEFAULT 0.0,
        receipt_id_match REAL DEFAULT 0.0,
        final_score REAL NOT NULL,
        status TEXT DEFAULT 'Pending Review',
        explanation TEXT,
        matching_evidence TEXT,
        notes TEXT DEFAULT '',
        created_at TEXT NOT NULL,
        FOREIGN KEY (receipt_a_id) REFERENCES receipts(id) ON DELETE CASCADE,
        FOREIGN KEY (receipt_b_id) REFERENCES receipts(id) ON DELETE CASCADE
    )
    """)

    # Table 3: audit_actions
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS audit_actions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        duplicate_result_id INTEGER,
        receipt_a_code TEXT,
        receipt_b_code TEXT,
        score REAL,
        action TEXT NOT NULL,
        note TEXT DEFAULT '',
        reviewer TEXT DEFAULT 'Lead Auditor',
        timestamp TEXT NOT NULL,
        FOREIGN KEY (duplicate_result_id) REFERENCES duplicate_results(id) ON DELETE SET NULL
    )
    """)

    # Create indexes for fast querying
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_receipts_receipt_id ON receipts(receipt_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_receipts_vendor ON receipts(vendor)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_receipts_status ON receipts(status)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_dup_status ON duplicate_results(status)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_dup_final_score ON duplicate_results(final_score)")

    conn.commit()
    conn.close()


def row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
    return {k: row[k] for k in row.keys()}


# ------------------ Receipt Helpers ------------------

def create_receipt(receipt: Dict[str, Any]) -> int:
    conn = get_connection()
    cursor = conn.cursor()
    now = datetime.now().isoformat()
    cursor.execute("""
        INSERT INTO receipts (
            receipt_id, vendor, date, amount, description, category, 
            status, duplicate_score, submitter, image_url, raw_text, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        receipt.get("receipt_id", "").strip(),
        receipt.get("vendor", "").strip(),
        receipt.get("date", "").strip(),
        float(receipt.get("amount", 0.0)),
        receipt.get("description", "").strip(),
        receipt.get("category", "General Expense").strip(),
        receipt.get("status", "Normal"),
        float(receipt.get("duplicate_score", 0.0)),
        receipt.get("submitter", "submitter").strip(),
        receipt.get("image_url", "").strip(),
        receipt.get("raw_text", "").strip(),
        receipt.get("created_at", now)
    ))
    new_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return new_id


def get_all_receipts(
    search: str = "",
    category: str = "",
    status: str = "",
    submitter: str = "",
    sort_by: str = "id",
    order: str = "DESC",
    limit: int = 1000,
    offset: int = 0
) -> Dict[str, Any]:
    conn = get_connection()
    cursor = conn.cursor()

    query = "SELECT * FROM receipts WHERE 1=1"
    params = []

    if search:
        query += " AND (receipt_id LIKE ? OR vendor LIKE ? OR description LIKE ?)"
        term = f"%{search}%"
        params.extend([term, term, term])

    if category and category != "All":
        query += " AND category = ?"
        params.append(category)

    if status and status != "All":
        query += " AND status = ?"
        params.append(status)

    if submitter and submitter != "All":
        query += " AND submitter = ?"
        params.append(submitter)

    # Count total matching
    count_query = f"SELECT COUNT(*) as total FROM ({query})"
    cursor.execute(count_query, params)
    total = cursor.fetchone()["total"]

    # Order & Pagination
    valid_sort_cols = {"id", "receipt_id", "vendor", "date", "amount", "duplicate_score", "status", "created_at"}
    if sort_by not in valid_sort_cols:
        sort_by = "id"
    sort_direction = "ASC" if order.upper() == "ASC" else "DESC"
    query += f" ORDER BY {sort_by} {sort_direction} LIMIT ? OFFSET ?"
    params.extend([limit, offset])

    cursor.execute(query, params)
    rows = [row_to_dict(r) for r in cursor.fetchall()]
    conn.close()

    return {"total": total, "receipts": rows}


def get_database_records(limit: int = 100) -> List[Dict[str, Any]]:
    """
    Returns real SQLite records formatted for the visible System Data / Database Records UI.
    Columns: Receipt ID | Vendor | Date | Amount | Status | Saved At
    """
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM receipts ORDER BY id DESC LIMIT ?", (limit,))
    rows = cursor.fetchall()
    conn.close()

    formatted = []
    for r in rows:
        d = row_to_dict(r)
        # Format created_at to clean time string e.g. 10:42 AM
        created_str = d.get("created_at", "")
        saved_at = created_str
        try:
            dt = datetime.fromisoformat(created_str)
            saved_at = dt.strftime("%I:%M %p, %d/%m/%Y")
        except Exception:
            pass
        d["saved_at"] = saved_at
        formatted.append(d)
    return formatted


def get_submitter_stats(submitter: str = "submitter") -> Dict[str, Any]:
    """Returns metrics for Submitter Dashboard."""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT COUNT(*) as cnt FROM receipts WHERE submitter = ?", (submitter,))
    my_receipts = cursor.fetchone()["cnt"]

    cursor.execute("SELECT COUNT(*) as cnt FROM receipts WHERE submitter = ? AND status = 'Flagged Duplicate'", (submitter,))
    pending_review = cursor.fetchone()["cnt"]

    cursor.execute("SELECT COUNT(*) as cnt FROM receipts WHERE submitter = ? AND duplicate_score >= 60", (submitter,))
    possible_duplicates = cursor.fetchone()["cnt"]

    cursor.execute("SELECT COUNT(*) as cnt FROM receipts WHERE submitter = ? AND status IN ('Normal', 'Confirmed Duplicate', 'Dismissed')", (submitter,))
    processed = cursor.fetchone()["cnt"]

    conn.close()
    return {
        "my_receipts": my_receipts,
        "pending_review": pending_review,
        "possible_duplicates": possible_duplicates,
        "processed": processed
    }


def get_manager_analytics() -> Dict[str, Any]:
    """Returns high-level oversight analytics for Manager Dashboard & Analytics view."""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT COUNT(*) as cnt FROM receipts")
    total_receipts = cursor.fetchone()["cnt"]

    cursor.execute("SELECT COUNT(*) as cnt FROM duplicate_results")
    total_alerts = cursor.fetchone()["cnt"]

    cursor.execute("SELECT COUNT(*) as cnt FROM duplicate_results WHERE status = 'Confirmed Duplicate'")
    confirmed_dups = cursor.fetchone()["cnt"]

    cursor.execute("SELECT COUNT(*) as cnt FROM duplicate_results WHERE status = 'Dismissed'")
    dismissed_cases = cursor.fetchone()["cnt"]

    cursor.execute("SELECT COUNT(*) as cnt FROM duplicate_results WHERE status = 'Pending Review'")
    pending_reviews = cursor.fetchone()["cnt"]

    # Category expense distribution
    cursor.execute("SELECT category, SUM(amount) as total_amount, COUNT(*) as count FROM receipts GROUP BY category")
    category_summary = [row_to_dict(r) for r in cursor.fetchall()]

    conn.close()
    return {
        "total_receipts": total_receipts,
        "total_alerts": total_alerts,
        "confirmed_duplicates": confirmed_dups,
        "dismissed_cases": dismissed_cases,
        "pending_reviews": pending_reviews,
        "category_summary": category_summary
    }

    # Count total matching
    count_query = f"SELECT COUNT(*) as total FROM ({query})"
    cursor.execute(count_query, params)
    total = cursor.fetchone()["total"]

    # Order & Pagination
    valid_sort_cols = {"id", "receipt_id", "vendor", "date", "amount", "duplicate_score", "status"}
    if sort_by not in valid_sort_cols:
        sort_by = "id"
    sort_direction = "ASC" if order.upper() == "ASC" else "DESC"
    query += f" ORDER BY {sort_by} {sort_direction} LIMIT ? OFFSET ?"
    params.extend([limit, offset])

    cursor.execute(query, params)
    rows = [row_to_dict(r) for r in cursor.fetchall()]
    conn.close()

    return {"total": total, "receipts": rows}


def get_receipt_by_id(pk: int) -> Optional[Dict[str, Any]]:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM receipts WHERE id = ?", (pk,))
    row = cursor.fetchone()
    conn.close()
    return row_to_dict(row) if row else None


def update_receipt_status(pk: int, status: str, duplicate_score: Optional[float] = None):
    conn = get_connection()
    cursor = conn.cursor()
    if duplicate_score is not None:
        cursor.execute("UPDATE receipts SET status = ?, duplicate_score = ? WHERE id = ?", (status, duplicate_score, pk))
    else:
        cursor.execute("UPDATE receipts SET status = ? WHERE id = ?", (status, pk))
    conn.commit()
    conn.close()


# ------------------ Duplicate Results Helpers ------------------

def save_duplicate_result(dup_data: Dict[str, Any]) -> int:
    conn = get_connection()
    cursor = conn.cursor()
    now = datetime.now().isoformat()
    cursor.execute("""
        INSERT INTO duplicate_results (
            receipt_a_id, receipt_b_id, receipt_a_code, receipt_b_code,
            text_similarity, amount_match, date_match, receipt_id_match,
            final_score, status, explanation, matching_evidence, notes, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        dup_data["receipt_a_id"],
        dup_data["receipt_b_id"],
        dup_data.get("receipt_a_code", ""),
        dup_data.get("receipt_b_code", ""),
        dup_data.get("text_similarity", 0.0),
        dup_data.get("amount_match", 0.0),
        dup_data.get("date_match", 0.0),
        dup_data.get("receipt_id_match", 0.0),
        dup_data["final_score"],
        dup_data.get("status", "Pending Review"),
        dup_data.get("explanation", ""),
        dup_data.get("matching_evidence", ""),
        dup_data.get("notes", ""),
        now
    ))
    new_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return new_id


def get_duplicate_results(status: str = "All", limit: int = 100) -> List[Dict[str, Any]]:
    conn = get_connection()
    cursor = conn.cursor()

    query = """
        SELECT d.*, 
               ra.vendor as vendor_a, ra.date as date_a, ra.amount as amount_a, ra.description as desc_a, ra.category as cat_a,
               rb.vendor as vendor_b, rb.date as date_b, rb.amount as amount_b, rb.description as desc_b, rb.category as cat_b
        FROM duplicate_results d
        JOIN receipts ra ON d.receipt_a_id = ra.id
        JOIN receipts rb ON d.receipt_b_id = rb.id
        WHERE 1=1
    """
    params = []
    if status != "All":
        query += " AND d.status = ?"
        params.append(status)

    query += " ORDER BY d.final_score DESC LIMIT ?"
    params.append(limit)

    cursor.execute(query, params)
    results = [row_to_dict(r) for r in cursor.fetchall()]
    conn.close()
    return results


def get_duplicate_by_id(dup_id: int) -> Optional[Dict[str, Any]]:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT d.*, 
               ra.vendor as vendor_a, ra.date as date_a, ra.amount as amount_a, ra.description as desc_a, ra.category as cat_a,
               rb.vendor as vendor_b, rb.date as date_b, rb.amount as amount_b, rb.description as desc_b, rb.category as cat_b
        FROM duplicate_results d
        JOIN receipts ra ON d.receipt_a_id = ra.id
        JOIN receipts rb ON d.receipt_b_id = rb.id
        WHERE d.id = ?
    """, (dup_id,))
    row = cursor.fetchone()
    conn.close()
    return row_to_dict(row) if row else None


def update_duplicate_status(dup_id: int, status: str, note: str = ""):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE duplicate_results 
        SET status = ?, notes = ? 
        WHERE id = ?
    """, (status, note, dup_id))
    conn.commit()
    conn.close()


# ------------------ Audit History Helpers ------------------

def log_audit_action(dup_id: Optional[int], receipt_a_code: str, receipt_b_code: str, score: float, action: str, note: str, reviewer: str = "Auditor"):
    conn = get_connection()
    cursor = conn.cursor()
    now = datetime.now().strftime("%I:%M %p, %b %d, %Y")
    cursor.execute("""
        INSERT INTO audit_actions (duplicate_result_id, receipt_a_code, receipt_b_code, score, action, note, reviewer, timestamp)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (dup_id, receipt_a_code, receipt_b_code, score, action, note, reviewer, now))
    conn.commit()
    conn.close()


def get_audit_history(limit: int = 100) -> List[Dict[str, Any]]:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM audit_actions ORDER BY id DESC LIMIT ?", (limit,))
    rows = [row_to_dict(r) for r in cursor.fetchall()]
    conn.close()
    return rows


# ------------------ Stats Helpers ------------------

def get_stats_overview() -> Dict[str, Any]:
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT COUNT(*) as cnt FROM receipts")
    total_receipts = cursor.fetchone()["cnt"]

    cursor.execute("SELECT COUNT(*) as cnt FROM duplicate_results WHERE status = 'Pending Review'")
    possible_duplicates = cursor.fetchone()["cnt"]

    cursor.execute("SELECT COUNT(*) as cnt FROM duplicate_results WHERE final_score >= 85 AND status = 'Pending Review'")
    high_risk_matches = cursor.fetchone()["cnt"]

    cursor.execute("SELECT COUNT(*) as cnt FROM duplicate_results WHERE status IN ('Confirmed Duplicate', 'Dismissed')")
    reviewed_count = cursor.fetchone()["cnt"]

    # Status distribution for Recharts
    cursor.execute("SELECT status, COUNT(*) as count FROM receipts GROUP BY status")
    receipt_status_counts = {r["status"]: r["count"] for r in cursor.fetchall()}

    # Duplicate review status
    cursor.execute("SELECT status, COUNT(*) as count FROM duplicate_results GROUP BY status")
    dup_status_counts = {r["status"]: r["count"] for r in cursor.fetchall()}

    conn.close()

    return {
        "total_receipts": total_receipts,
        "possible_duplicates": possible_duplicates,
        "high_risk_matches": high_risk_matches,
        "reviewed": reviewed_count,
        "receipt_status_counts": receipt_status_counts,
        "dup_status_counts": dup_status_counts,
    }


def reset_database():
    """Wipes all rows and recreates tables."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("DROP TABLE IF EXISTS audit_actions")
    cursor.execute("DROP TABLE IF EXISTS duplicate_results")
    cursor.execute("DROP TABLE IF EXISTS receipts")
    conn.commit()
    conn.close()
    init_db()
