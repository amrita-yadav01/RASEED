"""
RASEED Duplicate Detection Engine.
Implements the 4-component weighted scoring system:
- Receipt ID Match: 30%
- Amount Match: 30%
- Date Match: 20%
- Vendor / Text Similarity: 20%

Thresholds:
- >= 85%: High Match (HIGH CONFIDENCE DUPLICATE)
- 60% - 84%: Possible Match (Possible Duplicate — Human Review Required)
- < 60%: Low Match

Features:
- RapidFuzz & TF-IDF Cosine Similarity for vendor/description text comparison
- Rule-based deterministic exact matching
- Grounded explanation generator (Strictly factual, no fraud accusations or financial advice)
"""

import re
from typing import Dict, Any, Tuple, List, Optional
from datetime import datetime
from rapidfuzz import fuzz
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity


def normalize_text(text: Optional[str]) -> str:
    if not text:
        return ""
    # Lowercase, replace multiple spaces, remove special punct
    cleaned = re.sub(r"[^\w\s]", " ", text.lower())
    return re.sub(r"\s+", " ", cleaned).strip()


def normalize_receipt_id(rid: Optional[str]) -> str:
    if not rid:
        return ""
    # Strip spaces and standard separators to compare base code
    return re.sub(r"[\s\-_#]", "", rid.upper())


def parse_flexible_date(date_str: Optional[str]) -> Optional[datetime]:
    if not date_str:
        return None
    cleaned = str(date_str).strip()
    # Common formats
    formats = [
        "%Y-%m-%d",
        "%d/%m/%Y",
        "%d-%m-%Y",
        "%m/%d/%Y",
        "%d %b %Y",
        "%d %B %Y",
        "%Y/%m/%d",
        "%d.%m.%Y"
    ]
    for fmt in formats:
        try:
            return datetime.strptime(cleaned, fmt)
        except ValueError:
            continue
    return None


def calculate_receipt_id_match(id_a: Optional[str], id_b: Optional[str]) -> Tuple[float, str]:
    """Score 0.0 to 1.0 (weighted at 30%)."""
    norm_a = normalize_receipt_id(id_a)
    norm_b = normalize_receipt_id(id_b)

    if not norm_a or not norm_b:
        return 0.0, "Missing or unreadable Receipt ID"

    if norm_a == norm_b:
        return 1.0, f"Identical Receipt ID ({id_a.strip()})"

    # Check for prefix / series match (e.g. FIN-407 vs FIN-882 share FIN prefix)
    prefix_a = re.match(r"^([A-Z]+)", norm_a)
    prefix_b = re.match(r"^([A-Z]+)", norm_b)
    if prefix_a and prefix_b and prefix_a.group(1) == prefix_b.group(1):
        return 0.75, f"Matching series prefix '{prefix_a.group(1)}' ({id_a} vs {id_b})"

    # Check for 1-character typo or common variance
    ratio = fuzz.ratio(norm_a, norm_b)
    if ratio >= 70:
        return 0.7, f"Highly similar Receipt ID ({id_a} vs {id_b})"

    return 0.0, f"Different Receipt IDs ({id_a} vs {id_b})"


def calculate_amount_match(amt_a: float, amt_b: float) -> Tuple[float, str]:
    """Score 0.0 to 1.0 (weighted at 30%)."""
    try:
        val_a = float(amt_a)
        val_b = float(amt_b)
    except (ValueError, TypeError):
        return 0.0, "Invalid amount values"

    if val_a <= 0 or val_b <= 0:
        return 0.0, "Missing or zero amount"

    # Exact equality (or within 0.01 for floating currency rounding)
    diff = abs(val_a - val_b)
    if diff <= 0.01:
        return 1.0, f"Same amount (₹{val_a:,.2f})"

    # Minor difference (e.g. within 1% or tax discrepancy)
    if diff / max(val_a, val_b) < 0.02:
        return 0.6, f"Very close amount (₹{val_a:,.2f} vs ₹{val_b:,.2f})"

    return 0.0, f"Different amounts (₹{val_a:,.2f} vs ₹{val_b:,.2f})"


def calculate_date_match(date_a: Optional[str], date_b: Optional[str]) -> Tuple[float, str]:
    """Score 0.0 to 1.0 (weighted at 20%)."""
    dt_a = parse_flexible_date(date_a)
    dt_b = parse_flexible_date(date_b)

    if not dt_a or not dt_b:
        # Fallback to string comparison
        if date_a and date_b and date_a.strip().lower() == date_b.strip().lower():
            return 1.0, f"Same date string ({date_a.strip()})"
        return 0.0, "Missing or unparseable date"

    if dt_a.date() == dt_b.date():
        return 1.0, f"Same date ({dt_a.strftime('%d/%m/%Y')})"

    # Check if within 1-2 days (e.g. overnight transaction or timezone posting lag)
    day_diff = abs((dt_a - dt_b).days)
    if day_diff <= 1:
        return 0.7, f"Adjacent date (within 1 day: {dt_a.strftime('%d/%m/%Y')} vs {dt_b.strftime('%d/%m/%Y')})"
    if day_diff <= 3:
        return 0.4, f"Close dates (within {day_diff} days)"

    return 0.0, f"Different dates ({dt_a.strftime('%d/%m/%Y')} vs {dt_b.strftime('%d/%m/%Y')})"


def calculate_text_similarity(vendor_a: str, vendor_b: str, desc_a: str, desc_b: str) -> Tuple[float, str]:
    """
    Score 0.0 to 1.0 (weighted at 20%).
    Combines RapidFuzz token matching with TF-IDF cosine similarity.
    """
    v_a = normalize_text(vendor_a)
    v_b = normalize_text(vendor_b)
    d_a = normalize_text(desc_a)
    d_b = normalize_text(desc_b)

    # 1. Vendor similarity (token sort ratio handles word order like 'ABC Mart' vs 'Mart ABC')
    vendor_ratio = fuzz.token_sort_ratio(v_a, v_b) / 100.0 if (v_a and v_b) else 0.0

    # 2. Description similarity using TF-IDF cosine if words exist, or rapidfuzz
    desc_sim = 0.0
    if d_a and d_b:
        if d_a == d_b:
            desc_sim = 1.0
        else:
            try:
                tfidf = TfidfVectorizer().fit_transform([d_a, d_b])
                cos_sim = cosine_similarity(tfidf[0:1], tfidf[1:2])[0][0]
                fuzzy_desc = fuzz.token_set_ratio(d_a, d_b) / 100.0
                desc_sim = max(float(cos_sim), float(fuzzy_desc))
            except Exception:
                desc_sim = fuzz.token_set_ratio(d_a, d_b) / 100.0
    elif not d_a and not d_b:
        desc_sim = 0.5  # Neutral if neither has description

    # Weighted: Vendor 60%, Description 40% of this component
    combined_score = (vendor_ratio * 0.65) + (desc_sim * 0.35)

    evidence_parts = []
    if vendor_ratio >= 0.9:
        evidence_parts.append(f"Matching vendor ({vendor_a})")
    elif vendor_ratio >= 0.7:
        evidence_parts.append(f"Similar vendor ({vendor_a} ~ {vendor_b})")
    else:
        evidence_parts.append(f"Distinct vendors ({vendor_a} vs {vendor_b})")

    if desc_sim >= 0.85:
        evidence_parts.append("Highly similar description")
    elif desc_sim >= 0.6:
        evidence_parts.append("Related description")

    return round(combined_score, 3), "; ".join(evidence_parts)


def evaluate_duplicate_pair(receipt_a: Dict[str, Any], receipt_b: Dict[str, Any]) -> Dict[str, Any]:
    """
    Evaluates receipt_a against receipt_b using:
    - Receipt ID (30%)
    - Amount (30%)
    - Date (20%)
    - Vendor / Text (20%)
    """
    id_score, id_reason = calculate_receipt_id_match(receipt_a.get("receipt_id"), receipt_b.get("receipt_id"))
    amt_score, amt_reason = calculate_amount_match(receipt_a.get("amount", 0), receipt_b.get("amount", 0))
    date_score, date_reason = calculate_date_match(receipt_a.get("date"), receipt_b.get("date"))
    text_score, text_reason = calculate_text_similarity(
        receipt_a.get("vendor", ""),
        receipt_b.get("vendor", ""),
        receipt_a.get("description", ""),
        receipt_b.get("description", "")
    )

    # Weighted calculation
    weighted_score = (id_score * 0.30) + (amt_score * 0.30) + (date_score * 0.20) + (text_score * 0.20)
    final_percentage = round(weighted_score * 100.0, 1)

    # Determine risk category
    if final_percentage >= 85.0:
        match_tier = "High Match"
        status_label = "HIGH CONFIDENCE DUPLICATE"
    elif final_percentage >= 60.0:
        match_tier = "Possible Match"
        status_label = "POSSIBLE DUPLICATE"
    else:
        match_tier = "Low Match"
        status_label = "LOW MATCH"

    # Evidence checklist
    evidence_checklist = []
    if amt_score >= 0.95:
        evidence_checklist.append("Same amount")
    elif amt_score >= 0.5:
        evidence_checklist.append("Very close amount")

    if date_score >= 0.95:
        evidence_checklist.append("Same date")
    elif date_score >= 0.5:
        evidence_checklist.append("Adjacent transaction date")

    if text_score >= 0.7:
        evidence_checklist.append("Similar vendor name")
    if text_score >= 0.5:
        evidence_checklist.append("Similar description")

    if id_score >= 0.95:
        evidence_checklist.append("Identical Receipt ID")
    elif id_score >= 0.6:
        evidence_checklist.append("Similar Receipt ID")

    # Generate Grounded GenAI Explanation
    explanation = generate_grounded_explanation(
        match_tier=match_tier,
        final_score=final_percentage,
        receipt_a=receipt_a,
        receipt_b=receipt_b,
        id_score=id_score,
        amt_score=amt_score,
        date_score=date_score,
        text_score=text_score,
        evidence_checklist=evidence_checklist
    )

    return {
        "final_score": final_percentage,
        "match_tier": match_tier,
        "status_label": status_label,
        "receipt_id_match": round(id_score * 100.0, 1),
        "amount_match": round(amt_score * 100.0, 1),
        "date_match": round(date_score * 100.0, 1),
        "text_similarity": round(text_score * 100.0, 1),
        "matching_evidence": evidence_checklist,
        "explanation": explanation,
        "breakdown_reasons": {
            "receipt_id": id_reason,
            "amount": amt_reason,
            "date": date_reason,
            "text": text_reason
        }
    }


def generate_grounded_explanation(
    match_tier: str,
    final_score: float,
    receipt_a: Dict[str, Any],
    receipt_b: Dict[str, Any],
    id_score: float,
    amt_score: float,
    date_score: float,
    text_score: float,
    evidence_checklist: List[str]
) -> str:
    """
    Generates a grounded, factual explanation strictly based on verified system evidence.
    GUARANTEE: Does NOT decide fraud, does NOT give financial advice, does NOT invent rules.
    """
    signals = []
    if amt_score >= 0.95:
        signals.append(f"identical monetary amount of ₹{float(receipt_a.get('amount', 0)):,.2f}")
    elif amt_score >= 0.5:
        signals.append("nearly identical amounts")

    if date_score >= 0.95:
        signals.append(f"exact matching date ({receipt_a.get('date')})")
    elif date_score >= 0.5:
        signals.append("dates within close proximity")

    if id_score >= 0.95:
        signals.append(f"matching receipt identifier '{receipt_a.get('receipt_id')}'")
    elif id_score >= 0.6:
        signals.append(f"similar identifiers ('{receipt_a.get('receipt_id')}' vs '{receipt_b.get('receipt_id')}')")

    if text_score >= 0.8:
        signals.append(f"strongly matching vendor names ('{receipt_a.get('vendor')}' and '{receipt_b.get('vendor')}') and descriptions")
    elif text_score >= 0.5:
        signals.append("comparable vendor terminology")

    if signals:
        joined_signals = ", ".join(signals)
        explanation = (
            f"These receipts received a duplicate match score of {final_score}% based on: {joined_signals}. "
            f"Because the evidence indicates significant overlap across key financial fields, "
            f"the system flags this pair as: Possible Duplicate — Human Review Required."
        )
    else:
        explanation = (
            f"These receipts scored {final_score}% similarity. The detected financial fields show minimal "
            f"direct overlap. The system recommends standard record processing unless flagged by an auditor."
        )

    return explanation
