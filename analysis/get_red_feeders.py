"""
get_ntl_feeders.py
Single responsibility:
→ Return JSON-serialisable dict of feeders that should be highlighted red.
NO PNG, NO FILE OUTPUT, NO CLI LOGIC
"""

import math
from theft_detection import TheftDetector, ANALYSIS_HOURS, NTL_STATUSES


def _clean(obj):
    """Replace NaN/inf with None for safe JSON serialisation."""
    if isinstance(obj, float):
        return None if (math.isnan(obj) or math.isinf(obj)) else obj
    if isinstance(obj, dict):  return {k: _clean(v) for k, v in obj.items()}
    if isinstance(obj, list):  return [_clean(i) for i in obj]
    return obj


def _is_red(row: dict) -> bool:
    """
    A feeder is red when losses are above physical maximum AND
    we have enough data to trust the result.

    HIGH confidence (≥80% DT measured): any loss > 15%
    MEDIUM confidence (50-80% measured): only if loss > 50%
      (signal strong enough to flag even with partial data)
    """
    loss = row.get("loss_pct") or 0
    conf = row.get("confidence")

    if conf == "HIGH"   and loss > 15: return True
    if conf == "MEDIUM" and loss > 50: return True
    return False


# ─────────────────────────────────────────────
# PUBLIC API
# ─────────────────────────────────────────────

def get_ntl_feeders(hours: int = ANALYSIS_HOURS) -> dict:
    """
    Run NTL analysis and return feeders that should be coloured red.

    A feeder is red when:
      - HIGH confidence  and  loss > 15%
      - MEDIUM confidence and  loss > 50%

    Args:
        hours: analysis window in hours (default 168 = 7 days)

    Returns:
        dict with keys:
            analysis_window_hours  - int
            summary                - F11/F33 KPIs
            red_feeders            - list of dicts, each with:
                id, level, feeder_name, status, confidence,
                loss_pct, loss_kwh
    """
    rep = TheftDetector().analyse(hours=hours)

    f11_records = rep.f11_feeders.to_dict(orient="records")
    f33_records = rep.f33_feeders.to_dict(orient="records")

    red = (
        [
            {
                "id":          r["f11_id"],
                "level":       "F11",
                "feeder_name": r["feeder_name"],
                "status":      r["status"],
                "confidence":  r["confidence"],
                "loss_pct":    r["loss_pct"],
                "loss_kwh":    r["loss_kwh"],
            }
            for r in f11_records if _is_red(r)
        ]
        + [
            {
                "id":          r["f33_id"],
                "level":       "F33",
                "feeder_name": r["feeder_name"],
                "status":      r["status"],
                "confidence":  r["confidence"],
                "loss_pct":    r["loss_pct"],
                "loss_kwh":    r["loss_kwh"],
            }
            for r in f33_records if _is_red(r)
        ]
    )

    return _clean({
        "analysis_window_hours": hours,
        "summary": {
            "f11_ntl_kwh":   round(rep.f11_total_ntl_kwh, 1),
            "f33_ntl_kwh":   round(rep.f33_total_ntl_kwh, 1),
            "f11_ntl_share": round(rep.f11_total_ntl_kwh / rep.f11_total_input_kwh * 100, 1)
                             if rep.f11_total_input_kwh else None,
            "f33_ntl_share": round(rep.f33_total_ntl_kwh / rep.f33_total_input_kwh * 100, 1)
                             if rep.f33_total_input_kwh else None,
        },
        "red_feeders": red,
    })


if __name__ == "__main__":
    import json
    print(json.dumps(get_ntl_feeders(), indent=2))