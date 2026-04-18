"""
get_feeder_status.py
Single responsibility:
→ Return confidence and alarm status for a feeder by ID.
NO PNG, NO FILE OUTPUT, NO CLI LOGIC
"""

from theft_detection import (
    ANALYSIS_HOURS,
    FALLBACK_KVA_DT,
    FALLBACK_KVA_F11,
    FALLBACK_KVA_F33,
    MIN_COVERAGE_F11,
    MIN_COVERAGE_F33,
    MIN_KWH_FEEDER,
    LOAD_FACTOR_SUSPICIOUS,
    _tfes_delta,
    _classify,
    _nameplate_max_kwh,
)
import pandas as pd
from get_data.db import q


# ─────────────────────────────────────────────
# PUBLIC API
# ─────────────────────────────────────────────

def get_feeder_status(feeder_id: int,
                      level: str,
                      hours: int = ANALYSIS_HOURS) -> dict:
    """
    Query a single feeder and return its NTL status and confidence.

    Args:
        feeder_id: Feeders11.Id or Feeders33.Id
        level:     "f11" or "f33"
        hours:     analysis window in hours (default 168 = 7 days)

    Returns:
        dict with keys:
            id, level, feeder_name,
            status, confidence,
            loss_pct, loss_kwh,
            input_kwh, output_kwh_est,
            coverage,
            dt_profiles   (F11 only) — list of DT details
            f11_profiles  (F33 only) — list of F11 details
    """
    level = level.lower()

    if level == "f11":
        row = q("""
            SELECT Id, Name, MeterId, Feeder33Id,
                   ISNULL(NameplateRating, 0) AS NameplateRating
            FROM Feeders11 WHERE Id = :fid
        """, {"fid": feeder_id})

        if row.empty:
            raise ValueError(f"F11 feeder with id={feeder_id} does not exist")

        r       = row.iloc[0]
        f11_mid = int(r["MeterId"])
        f11_kva = float(r["NameplateRating"]) if r["NameplateRating"] > 0 else FALLBACK_KVA_F11

        dts = q("""
            SELECT Id, Name, MeterId,
                   ISNULL(NameplateRating, 0) AS NameplateRating,
                   Latitude, Longitude
            FROM DistributionSubstation
            WHERE Feeder11Id = :fid AND MeterId IS NOT NULL
        """, {"fid": feeder_id})

        all_mids = [f11_mid] + dts["MeterId"].dropna().astype(int).tolist()
        tfes     = _tfes_delta(all_mids, hours)

        def _kwh(mid, kva):
            if mid not in tfes:
                return 0.0
            kwh    = tfes[mid]["kwh"]
            max_kw = _nameplate_max_kwh(kva, hours)
            return round(kwh, 1) if kwh <= max_kw else 0.0

        input_kwh  = _kwh(f11_mid, f11_kva)
        output_sum = 0.0
        n_ok       = 0
        dt_profiles = []

        for _, dt in dts.iterrows():
            dt_mid = int(dt["MeterId"])
            dt_kva = float(dt["NameplateRating"]) if dt["NameplateRating"] > 0 else FALLBACK_KVA_DT
            dt_kwh = _kwh(dt_mid, dt_kva)
            max_dt = _nameplate_max_kwh(dt_kva, hours)

            lf         = round(dt_kwh / max_dt, 4) if max_dt > 0 and max_dt != float("inf") else 0.0
            suspicious = dt_kwh > 0 and dt_kwh < max_dt * LOAD_FACTOR_SUSPICIOUS

            if dt_kwh > 0:
                output_sum += dt_kwh
                n_ok += 1

            dt_profiles.append({
                "dt_id":         int(dt["Id"]),
                "name":          dt["Name"],
                "kwh_7d":        dt_kwh,
                "load_factor":   lf,
                "nameplate_kva": dt_kva,
                "latitude":      float(dt["Latitude"])  if pd.notna(dt["Latitude"])  else None,
                "longitude":     float(dt["Longitude"]) if pd.notna(dt["Longitude"]) else None,
                "suspicious":    suspicious,
                "has_data":      dt_kwh > 0,
            })

        n_total  = len(dts)
        coverage = n_ok / n_total if n_total > 0 else 0.0

        if input_kwh < MIN_KWH_FEEDER:
            status, confidence = "INVALID_INPUT_METER", "LOW"
            output_kwh_est, loss_kwh, loss_pct = 0.0, 0.0, None
        elif coverage < MIN_COVERAGE_F11:
            status, confidence = "INSUFFICIENT_DATA", "LOW"
            output_kwh_est, loss_kwh, loss_pct = 0.0, 0.0, None
        else:
            output_kwh_est = round(output_sum / coverage, 1)
            loss_kwh       = round(max(input_kwh - output_kwh_est, 0.0), 1)
            loss_pct       = round((input_kwh - output_kwh_est) / input_kwh * 100, 2)
            status, confidence = _classify(loss_pct, coverage)

        return {
            "id":                    feeder_id,
            "level":                 "F11",
            "feeder_name":           r["Name"],
            "f33_id":                int(r["Feeder33Id"]) if pd.notna(r.get("Feeder33Id")) else None,
            "status":                status,
            "confidence":            confidence,
            "loss_pct":              loss_pct,
            "loss_kwh":              loss_kwh,
            "input_kwh":             input_kwh,
            "output_kwh_est":        output_kwh_est,
            "coverage":              round(coverage, 3),
            "dt_count":              n_total,
            "dt_measured":           n_ok,
            "dt_suspicious":         sum(1 for d in dt_profiles if d["suspicious"]),
            "analysis_window_hours": hours,
            "dt_profiles":           dt_profiles,
        }

    elif level == "f33":
        row = q("""
            SELECT Id, Name, MeterId,
                   ISNULL(NameplateRating, 0) AS NameplateRating
            FROM Feeders33 WHERE Id = :fid
        """, {"fid": feeder_id})

        if row.empty:
            raise ValueError(f"F33 feeder with id={feeder_id} does not exist")

        r       = row.iloc[0]
        f33_mid = int(r["MeterId"])
        f33_kva = float(r["NameplateRating"]) if r["NameplateRating"] > 0 else FALLBACK_KVA_F33

        f11s = q("""
            SELECT Id, Name, MeterId,
                   ISNULL(NameplateRating, 0) AS NameplateRating
            FROM Feeders11
            WHERE Feeder33Id = :fid AND MeterId IS NOT NULL
        """, {"fid": feeder_id})

        all_mids = [f33_mid] + f11s["MeterId"].dropna().astype(int).tolist()
        tfes     = _tfes_delta(all_mids, hours)

        def _kwh(mid, kva):
            if mid not in tfes:
                return 0.0
            kwh    = tfes[mid]["kwh"]
            max_kw = _nameplate_max_kwh(kva, hours)
            return round(kwh, 1) if kwh <= max_kw else 0.0

        input_kwh    = _kwh(f33_mid, f33_kva)
        output_sum   = 0.0
        n_ok         = 0
        f11_profiles = []

        for _, f11 in f11s.iterrows():
            f11_mid_i = int(f11["MeterId"])
            f11_kva_i = float(f11["NameplateRating"]) if f11["NameplateRating"] > 0 else FALLBACK_KVA_F11
            f11_kwh   = _kwh(f11_mid_i, f11_kva_i)

            if f11_kwh >= MIN_KWH_FEEDER:
                output_sum += f11_kwh
                n_ok += 1

            f11_profiles.append({
                "f11_id":   int(f11["Id"]),
                "name":     f11["Name"],
                "kwh_7d":   f11_kwh,
                "has_data": f11_kwh > 0,
            })

        n_total  = len(f11s)
        coverage = n_ok / n_total if n_total > 0 else 0.0

        if input_kwh < MIN_KWH_FEEDER:
            status, confidence = "INVALID_INPUT_METER", "LOW"
            output_kwh_est, loss_kwh, loss_pct = 0.0, 0.0, None
        elif coverage < MIN_COVERAGE_F33:
            status, confidence = "INSUFFICIENT_DATA", "LOW"
            output_kwh_est, loss_kwh, loss_pct = 0.0, 0.0, None
        else:
            output_kwh_est = round(output_sum / coverage, 1)
            loss_kwh       = round(max(input_kwh - output_kwh_est, 0.0), 1)
            loss_pct       = round((input_kwh - output_kwh_est) / input_kwh * 100, 2)
            status, confidence = _classify(loss_pct, coverage)

        return {
            "id":                    feeder_id,
            "level":                 "F33",
            "feeder_name":           r["Name"],
            "status":                status,
            "confidence":            confidence,
            "loss_pct":              loss_pct,
            "loss_kwh":              loss_kwh,
            "input_kwh":             input_kwh,
            "output_kwh_est":        output_kwh_est,
            "coverage":              round(coverage, 3),
            "f11_count":             n_total,
            "f11_measured":          n_ok,
            "analysis_window_hours": hours,
            "f11_profiles":          f11_profiles,
        }

    else:
        raise ValueError(f"Invalid level '{level}'. Use 'f11' or 'f33'.")


# ─────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────

if __name__ == "__main__":
    import json, sys

    if len(sys.argv) < 3:
        print("Usage: python3 get_feeder_status.py <feeder_id> <f11|f33>")
        print("Example: python3 get_feeder_status.py 599 f11")
        sys.exit(1)

    feeder_id = int(sys.argv[1])
    level     = sys.argv[2]

    result = get_feeder_status(feeder_id, level)
    print(json.dumps(result, indent=2, default=str))