"""
RASEED - Receipt Analysis System for Exact & Duplicate Detection.
FastAPI Backend Application.
"""

import os
from typing import Optional, List, Dict, Any
from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from fastapi.staticfiles import StaticFiles
import time
import shutil

from .database import (
    init_db,
    get_connection,
    create_receipt,
    get_all_receipts,
    get_receipt_by_id,
    update_receipt_status,
    save_duplicate_result,
    get_duplicate_results,
    get_duplicate_by_id,
    update_duplicate_status,
    log_audit_action,
    get_audit_history,
    get_stats_overview,
    get_database_records,
    get_submitter_stats,
    get_manager_analytics,
    reset_database
)
from .duplicate_engine import evaluate_duplicate_pair, generate_grounded_explanation
from .ocr_engine import perform_ocr_on_bytes, extract_fields_from_text, SAMPLE_RECEIPTS
from .seed_data import generate_seed_data

app = FastAPI(
    title="RASEED API",
    description="Receipt Analysis System for Exact & Duplicate Detection",
    version="1.0.0"
)

# Enable CORS for local development
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Static files for stored uploaded images
UPLOADS_DIR = os.path.join(os.path.dirname(__file__), "uploads")
os.makedirs(UPLOADS_DIR, exist_ok=True)
app.mount("/uploads", StaticFiles(directory=UPLOADS_DIR), name="uploads")


# Initialize DB on startup
@app.on_event("startup")
def on_startup():
    init_db()
    # Check if database has data; if empty, seed automatically for immediate demo readiness
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) as cnt FROM receipts")
    count = cursor.fetchone()["cnt"]
    conn.close()
    if count == 0:
        print("[Startup] Empty database detected. Seeding 1,000 synthetic demo records...")
        generate_seed_data()


# ------------------ Pydantic Models ------------------

class LoginRequest(BaseModel):
    username: str
    password: str
    role: Optional[str] = None


class ReceiptCreateRequest(BaseModel):
    receipt_id: Optional[str] = Field("", description="Unique receipt identifier or invoice number")
    vendor: str = Field(..., description="Store or company name")
    date: str = Field(..., description="Receipt transaction date (DD/MM/YYYY or YYYY-MM-DD)")
    amount: float = Field(..., description="Monetary total amount")
    description: Optional[str] = Field("", description="Purchased items or purpose")
    category: Optional[str] = Field("General Expense", description="Expense category")
    submitter: Optional[str] = Field("submitter", description="Username who submitted this receipt")
    image_url: Optional[str] = Field("", description="Preview URL or path of stored receipt image")
    raw_text: Optional[str] = Field("", description="OCR extracted text")


class AnalyzeTextRequest(BaseModel):
    raw_text: str = Field(..., description="Raw receipt text to extract fields from")


class ReviewActionRequest(BaseModel):
    note: Optional[str] = Field("", description="Auditor review note")
    reviewer: Optional[str] = Field("Auditor", description="Name/Role of reviewer")


class ExplainMatchRequest(BaseModel):
    receipt_a: Dict[str, Any]
    receipt_b: Dict[str, Any]
    final_score: float


# ------------------ Demo Authentication ------------------

DEMO_USERS = {
    "submitter": {
        "password": "submit123",
        "role": "Submitter",
        "name": "Amruta (Submitter)"
    },
    "auditor": {
        "password": "auditor123",
        "role": "Lead Auditor",
        "name": "Lead Auditor"
    },
    "manager": {
        "password": "manager123",
        "role": "Manager",
        "name": "Manager"
    }
}


@app.post("/api/login")
@app.post("/login")
def login(payload: LoginRequest):
    """Simple demo login supporting the 3 required roles."""
    username = (payload.username or "").strip().lower()
    user = DEMO_USERS.get(username)
    if not user or user["password"] != payload.password.strip():
        raise HTTPException(
            status_code=401,
            detail="Invalid username or password. Please use demo credentials."
        )
    return {
        "status": "success",
        "username": username,
        "name": user["name"],
        "role": user["role"]
    }


# ------------------ Health & Meta Endpoints ------------------

@app.get("/")
def root():
    return {
        "system": "RASEED",
        "description": "Receipt Analysis System for Exact & Duplicate Detection",
        "version": "1.0.0",
        "status": "Operational",
        "disclaimer": "Possible Duplicate alerts require human auditor review. This system does not make automated fraud decisions."
    }


@app.get("/api/stats")
@app.get("/stats")
def get_stats():
    """Returns dashboard overview statistics and Recharts chart datasets."""
    stats = get_stats_overview()

    # Format chart data for Recharts
    status_counts = stats["receipt_status_counts"]
    status_chart = [
        {"name": "Normal Receipts", "value": status_counts.get("Normal", 0), "color": "#3B82F6"},
        {"name": "Possible Duplicates", "value": status_counts.get("Flagged Duplicate", 0), "color": "#F59E0B"},
        {"name": "Confirmed Duplicates", "value": status_counts.get("Confirmed Duplicate", 0), "color": "#EF4444"},
        {"name": "Dismissed", "value": status_counts.get("Dismissed", 0), "color": "#10B981"},
    ]

    # Score breakdown for duplicate results
    score_breakdown = [
        {"tier": "High Match (≥85%)", "count": stats["high_risk_matches"], "color": "#EF4444"},
        {"tier": "Possible Match (60–84%)", "count": max(0, stats["possible_duplicates"] - stats["high_risk_matches"]), "color": "#F59E0B"},
        {"tier": "Reviewed", "count": stats["reviewed"], "color": "#10B981"}
    ]

    return {
        "total_receipts": stats["total_receipts"],
        "possible_duplicates": stats["possible_duplicates"],
        "high_risk_matches": stats["high_risk_matches"],
        "reviewed": stats["reviewed"],
        "status_chart": status_chart,
        "score_breakdown": score_breakdown
    }


@app.get("/api/submitter/stats")
@app.get("/submitter/stats")
def get_submitter_dashboard_stats(submitter: str = Query("submitter")):
    """Returns specific metrics for Submitter role."""
    return get_submitter_stats(submitter=submitter)


@app.get("/api/manager/analytics")
@app.get("/manager/analytics")
def get_manager_analytics_endpoint():
    """Returns executive analytics metrics for Manager role."""
    return get_manager_analytics()


@app.get("/api/database-records")
@app.get("/database-records")
@app.get("/api/system-records")
def list_database_records(limit: int = Query(50, ge=1, le=200)):
    """Returns real SQLite database records showing persistent backend storage."""
    return {"records": get_database_records(limit=limit)}


# ------------------ Sample Receipts for Demo ------------------

@app.get("/api/sample-receipts")
@app.get("/sample-receipts")
def get_sample_receipts():
    """Returns pre-loaded sample receipts for 1-click test runs."""
    return {"samples": SAMPLE_RECEIPTS}


# ------------------ OCR & Analysis Endpoints ------------------

@app.post("/api/analyze")
@app.post("/analyze")
async def analyze_receipt_upload(
    file: Optional[UploadFile] = File(None),
    preset_id: Optional[str] = Form(None),
    raw_text: Optional[str] = Form(None)
):
    """
    Reads receipt via OCR or preset template and extracts fields.
    Gracefully handles edge cases with friendly messages.
    """
    extracted_text = ""
    parsed_fields = {}

    # Option 1: Preset selected
    image_url = ""
    if preset_id:
        preset = next((s for s in SAMPLE_RECEIPTS if s["id"] == preset_id), None)
        if preset:
            extracted_text = preset["raw_text"]
            image_url = f"/uploads/{preset['id']}.svg"
            parsed_fields = {
                "receipt_id": preset["receipt_id"],
                "vendor": preset["vendor"],
                "date": preset["date"],
                "amount": preset["amount"],
                "description": preset["description"],
                "category": preset["category"]
            }
        else:
            raise HTTPException(status_code=400, detail="Requested sample receipt not found.")

    # Option 2: Uploaded file
    elif file is not None:
        try:
            content = await file.read()
            if len(content) == 0:
                raise HTTPException(status_code=400, detail="No receipt uploaded")

            # Save uploaded image to uploads directory for persistent storage & UI display
            import re
            safe_name = f"{int(time.time())}_{re.sub(r'[^a-zA-Z0-9._-]', '_', file.filename or 'receipt.png')}"
            save_path = os.path.join(UPLOADS_DIR, safe_name)
            with open(save_path, "wb") as f_out:
                f_out.write(content)
            image_url = f"/uploads/{safe_name}"

            extracted_text, parsed_fields = perform_ocr_on_bytes(content, filename=file.filename or "")
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=422, detail="OCR could not read the receipt. Please edit the detected fields manually.")

    # Option 3: Raw text directly provided
    elif raw_text:
        extracted_text = raw_text.strip()
        parsed_fields = extract_fields_from_text(extracted_text)

    else:
        raise HTTPException(status_code=400, detail="No receipt uploaded")

    # Friendly warning check
    warnings = []
    if not parsed_fields.get("receipt_id"):
        warnings.append("Receipt ID is missing")
    if not parsed_fields.get("amount") or parsed_fields.get("amount") <= 0:
        warnings.append("Amount could not be detected")

    # Run instant duplicate comparison against existing receipts in database
    best_match = None
    all_receipts_resp = get_all_receipts(limit=2000)
    highest_score = 0.0

    target_obj = {
        "receipt_id": parsed_fields.get("receipt_id", ""),
        "vendor": parsed_fields.get("vendor", ""),
        "date": parsed_fields.get("date", ""),
        "amount": parsed_fields.get("amount", 0.0),
        "description": parsed_fields.get("description", ""),
        "category": parsed_fields.get("category", "")
    }

    for rec in all_receipts_resp["receipts"]:
        # Do not compare record against itself if identical ID and amount
        if rec.get("receipt_id") and rec.get("receipt_id") == target_obj.get("receipt_id") and rec.get("amount") == target_obj.get("amount") and rec.get("vendor") == target_obj.get("vendor"):
            continue
        eval_result = evaluate_duplicate_pair(target_obj, rec)
        if eval_result["final_score"] > highest_score:
            highest_score = eval_result["final_score"]
            best_match = {
                "matched_receipt": rec,
                "evaluation": eval_result
            }

    return {
        "status": "success",
        "raw_text": extracted_text,
        "image_url": image_url,
        "fields": parsed_fields,
        "warnings": warnings,
        "top_duplicate_match": best_match if highest_score >= 50.0 else None,
        "message": "OCR Processing Complete"
    }


# ------------------ Receipts Endpoints ------------------

@app.get("/api/receipts")
@app.get("/receipts")
def list_receipts(
    search: str = Query("", description="Search term for vendor, ID, or description"),
    category: str = Query("All", description="Filter by category"),
    status: str = Query("All", description="Filter by status"),
    submitter: str = Query("All", description="Filter by submitter role or username"),
    sort_by: str = Query("id", description="Field to sort by"),
    order: str = Query("DESC", description="ASC or DESC"),
    limit: int = Query(50, ge=1, le=1000),
    offset: int = Query(0, ge=0)
):
    """Searchable, filterable, and paginated receipts list."""
    return get_all_receipts(
        search=search,
        category=category,
        status=status,
        submitter=submitter,
        sort_by=sort_by,
        order=order,
        limit=limit,
        offset=offset
    )


@app.post("/api/receipts")
@app.post("/receipts")
def add_receipt(payload: ReceiptCreateRequest):
    """
    Saves a newly scanned receipt, evaluates it against the database for duplicates,
    and updates statuses accordingly.
    """
    receipt_data = payload.dict()
    receipt_data["status"] = "Normal"
    receipt_data["duplicate_score"] = 0.0

    # Insert into database
    new_pk = create_receipt(receipt_data)
    receipt_data["id"] = new_pk

    # Scan against existing receipts
    all_receipts_resp = get_all_receipts(limit=2000)
    top_matches = []

    for existing in all_receipts_resp["receipts"]:
        if existing["id"] == new_pk:
            continue
        eval_res = evaluate_duplicate_pair(receipt_data, existing)
        if eval_res["final_score"] >= 60.0:
            top_matches.append((existing, eval_res))

    # Sort descending by score
    top_matches.sort(key=lambda x: x[1]["final_score"], reverse=True)

    if top_matches:
        best_existing, best_eval = top_matches[0]
        # Update current receipt status
        update_receipt_status(new_pk, "Flagged Duplicate", best_eval["final_score"])

        # Save duplicate pair
        save_duplicate_result({
            "receipt_a_id": best_existing["id"],
            "receipt_b_id": new_pk,
            "receipt_a_code": best_existing.get("receipt_id", "N/A"),
            "receipt_b_code": receipt_data.get("receipt_id", "N/A"),
            "text_similarity": best_eval["text_similarity"],
            "amount_match": best_eval["amount_match"],
            "date_match": best_eval["date_match"],
            "receipt_id_match": best_eval["receipt_id_match"],
            "final_score": best_eval["final_score"],
            "status": "Pending Review",
            "explanation": best_eval["explanation"],
            "matching_evidence": ", ".join(best_eval["matching_evidence"]),
            "notes": "Flagged during live receipt submission"
        })

    return {
        "status": "success",
        "id": new_pk,
        "message": "Receipt saved successfully",
        "flagged_as_duplicate": len(top_matches) > 0,
        "top_match_score": top_matches[0][1]["final_score"] if top_matches else 0.0
    }


# ------------------ Duplicate Review Endpoints ------------------

@app.get("/api/duplicates")
@app.get("/duplicates")
def list_duplicates(
    status: str = Query("All", description="Status: 'All', 'Pending Review', 'Confirmed Duplicate', 'Dismissed'"),
    limit: int = Query(50, ge=1, le=200)
):
    """Returns duplicate pairs flagged for auditor review."""
    return {"duplicates": get_duplicate_results(status=status, limit=limit)}


@app.get("/api/duplicates/{dup_id}")
@app.get("/duplicates/{dup_id}")
def get_duplicate_detail(dup_id: int):
    """Returns full side-by-side details for a specific duplicate pair."""
    dup = get_duplicate_by_id(dup_id)
    if not dup:
        raise HTTPException(status_code=404, detail="Duplicate pair record not found")
    return dup


@app.post("/api/duplicates/{dup_id}/confirm")
@app.post("/duplicates/{dup_id}/confirm")
def confirm_duplicate(dup_id: int, payload: ReviewActionRequest):
    """Auditor confirms duplicate. Updates database and records audit trail."""
    dup = get_duplicate_by_id(dup_id)
    if not dup:
        raise HTTPException(status_code=404, detail="Duplicate pair record not found")

    note_text = payload.note or "Confirmed by auditor as duplicate submission."
    update_duplicate_status(dup_id, "Confirmed Duplicate", note=note_text)

    # Update individual receipts
    update_receipt_status(dup["receipt_a_id"], "Confirmed Duplicate")
    update_receipt_status(dup["receipt_b_id"], "Confirmed Duplicate")

    # Record in audit history
    log_audit_action(
        dup_id=dup_id,
        receipt_a_code=dup.get("receipt_a_code") or f"ID-{dup['receipt_a_id']}",
        receipt_b_code=dup.get("receipt_b_code") or f"ID-{dup['receipt_b_id']}",
        score=dup["final_score"],
        action="Confirmed Duplicate",
        note=note_text,
        reviewer=payload.reviewer or "Auditor"
    )

    return {
        "status": "success",
        "action": "Confirmed Duplicate",
        "message": f"Duplicate pair #{dup_id} marked as Confirmed Duplicate"
    }


@app.post("/api/duplicates/{dup_id}/dismiss")
@app.post("/duplicates/{dup_id}/dismiss")
def dismiss_duplicate(dup_id: int, payload: ReviewActionRequest):
    """Auditor dismisses duplicate. Updates database and records audit trail."""
    dup = get_duplicate_by_id(dup_id)
    if not dup:
        raise HTTPException(status_code=404, detail="Duplicate pair record not found")

    note_text = payload.note or "Dismissed by auditor as legitimate separate transactions."
    update_duplicate_status(dup_id, "Dismissed", note=note_text)

    # Revert receipts to Normal or Dismissed
    update_receipt_status(dup["receipt_a_id"], "Dismissed")
    update_receipt_status(dup["receipt_b_id"], "Dismissed")

    # Record in audit history
    log_audit_action(
        dup_id=dup_id,
        receipt_a_code=dup.get("receipt_a_code") or f"ID-{dup['receipt_a_id']}",
        receipt_b_code=dup.get("receipt_b_code") or f"ID-{dup['receipt_b_id']}",
        score=dup["final_score"],
        action="Dismissed",
        note=note_text,
        reviewer=payload.reviewer or "Auditor"
    )

    return {
        "status": "success",
        "action": "Dismissed",
        "message": f"Duplicate pair #{dup_id} marked as Dismissed"
    }


# ------------------ Audit History Endpoints ------------------

@app.get("/api/audit-history")
@app.get("/audit-history")
def audit_history(limit: int = Query(100, ge=1, le=500)):
    """Returns chronological audit actions logged by human reviewers."""
    return {"audit_actions": get_audit_history(limit=limit)}


# ------------------ GenAI Explanation Endpoint ------------------

@app.post("/api/explain-match")
@app.post("/explain-match")
def explain_match(payload: ExplainMatchRequest):
    """
    Returns a strictly grounded GenAI explanation based on factual similarity evidence.
    No financial advice, no fraud assertions.
    """
    eval_res = evaluate_duplicate_pair(payload.receipt_a, payload.receipt_b)
    return {
        "explanation": eval_res["explanation"],
        "matching_evidence": eval_res["matching_evidence"],
        "final_score": eval_res["final_score"],
        "match_tier": eval_res["match_tier"]
    }


# ------------------ Demo Seeding Endpoint ------------------

@app.post("/api/seed-demo")
@app.post("/seed-demo")
def seed_demo_endpoint():
    """Generates ~1,000 realistic synthetic receipts and resets demo state."""
    res = generate_seed_data()
    return res


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("backend.main:app", host="127.0.0.1", port=8000, reload=True)
