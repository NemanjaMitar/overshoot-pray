"""
fetch_outages.py

Detects power outages from meter reading data.

HOW IT WORKS:
1. Find gaps in the meter's readings (more than 90 minutes without data)
   within the last 7 days from the meter's latest reading.
2. For each gap, check sibling meters on the same feeder.
   - If most siblings also had gaps at the same time → real outage.
   - If only this meter had a gap → probably just meter fault.
"""

import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from get_data.db import q
import pandas as pd


# How long (in minutes) a data gap must be before we even consider it
GAP_THRESHOLD_MIN = 90

# If this % of sibling meters also had gaps at the same time,
# we classify it as a real power outage
OUTAGE_CONFIRMATION_PCT = 50.0

# How many days back from the meter's latest reading to analyze
DEFAULT_DAYS_BACK = 7


def find_outages(meter_id: int, days_back: int = DEFAULT_DAYS_BACK) -> pd.DataFrame:
    """
    Find power outages for a single meter over the last `days_back` days
    (measured from this meter's own latest reading).
    
    Returns DataFrame with columns:
        start            - when power went out
        end              - when power came back
        minutes          - outage duration
        siblings_hit_pct - % of other meters on same feeder that were also affected
        is_real_outage   - True = real outage, False = probably a local meter issue
    """
    # Step 1: Find all gaps in this meter's readings within the window
    gaps = _find_gaps_in_readings(meter_id, days_back)
    if gaps.empty:
        return pd.DataFrame()
    
    # Step 2: Find the feeder this meter belongs to, and its sibling meters
    sibling_ids = _find_sibling_meters(meter_id)
    
    # Step 3: For each gap, check how many siblings were also silent
    results = []
    for _, gap in gaps.iterrows():
        siblings_hit_pct = _siblings_also_affected_pct(
            sibling_ids, gap["start"], gap["end"]
        )
        results.append({
            "start": gap["start"],
            "end": gap["end"],
            "minutes": gap["minutes"],
            "siblings_hit_pct": siblings_hit_pct,
            "is_real_outage": siblings_hit_pct >= OUTAGE_CONFIRMATION_PCT,
        })
    
    return pd.DataFrame(results)


# ───────────────────────────────────────────
# HELPERS (internal)
# ───────────────────────────────────────────

def _find_gaps_in_readings(meter_id: int, days_back: int) -> pd.DataFrame:
    """
    Find every gap longer than GAP_THRESHOLD_MIN within the last
    `days_back` days from this meter's last reading.
    """
    df = q("""
        WITH meter_last AS (
            SELECT MAX(Ts) AS mts FROM MeterReads WHERE Mid = :mid
        )
        SELECT DISTINCT Ts FROM MeterReads
        WHERE Mid = :mid
          AND Ts > DATEADD(DAY, -:days, (SELECT mts FROM meter_last))
        ORDER BY Ts
    """, {"mid": meter_id, "days": days_back})
    
    if len(df) < 2:
        return pd.DataFrame(columns=["start", "end", "minutes"])
    
    df["Ts"] = pd.to_datetime(df["Ts"])
    df["prev"] = df["Ts"].shift(1)
    df["gap_min"] = (df["Ts"] - df["prev"]).dt.total_seconds() / 60
    
    gaps = df[df["gap_min"] > GAP_THRESHOLD_MIN].copy()
    return pd.DataFrame({
        "start": gaps["prev"],
        "end": gaps["Ts"],
        "minutes": gaps["gap_min"].astype(int),
    })


def _find_sibling_meters(meter_id: int) -> list[int]:
    """
    Returns IDs of other meters on the same feeder as this meter.
    Looks up via DT → Feeder11 → all other meters on that F11.
    Returns empty list if no feeder found.
    """
    # First, find which F11 feeder the meter belongs to
    f11_lookup = q("""
        -- Meter might be attached to a DT
        SELECT TOP 1 dt.Feeder11Id AS f11_id
        FROM DistributionSubstation dt
        WHERE dt.MeterId = :mid AND dt.Feeder11Id IS NOT NULL
        UNION
        -- Or directly attached to an F11
        SELECT TOP 1 f.Id AS f11_id
        FROM Feeders11 f
        WHERE f.MeterId = :mid
    """, {"mid": meter_id})
    
    if f11_lookup.empty:
        return []
    
    f11_id = int(f11_lookup.iloc[0]["f11_id"])
    
    # Now find all other meters on that F11
    siblings = q("""
        SELECT MeterId FROM DistributionSubstation
        WHERE Feeder11Id = :fid AND MeterId IS NOT NULL AND MeterId <> :mid
        UNION
        SELECT MeterId FROM Feeders11
        WHERE Id = :fid AND MeterId IS NOT NULL AND MeterId <> :mid
    """, {"fid": f11_id, "mid": meter_id})
    
    return [int(m) for m in siblings["MeterId"].tolist()]


def _siblings_also_affected_pct(sibling_ids: list[int],
                                 start: pd.Timestamp,
                                 end: pd.Timestamp) -> float:
    """
    For a given outage window (start → end), compute the % of sibling
    meters that ALSO had no readings during that window.
    Returns 0.0 if there are no siblings (we can't tell).
    """
    if not sibling_ids:
        return 0.0
    
    placeholders = ",".join(str(s) for s in sibling_ids)
    during = q(f"""
        SELECT DISTINCT Mid
        FROM MeterReads
        WHERE Mid IN ({placeholders})
          AND Ts > :start AND Ts < :end
    """, {"start": start, "end": end})
    
    had_readings = set(int(m) for m in during["Mid"].tolist())
    affected = len(sibling_ids) - len(had_readings)
    return round(100 * affected / len(sibling_ids), 1)


# ───────────────────────────────────────────
# CLI TEST
# ───────────────────────────────────────────

if __name__ == "__main__":
    meter_id = int(sys.argv[1]) if len(sys.argv) > 1 else 34508
    days = int(sys.argv[2]) if len(sys.argv) > 2 else DEFAULT_DAYS_BACK
    
    print(f"Analyzing outages for meter {meter_id} — last {days} days\n")
    
    outages = find_outages(meter_id, days_back=days)
    
    if outages.empty:
        print("No gaps found in this time window.")
        sys.exit(0)
    
    real = outages[outages["is_real_outage"]]
    fake = outages[~outages["is_real_outage"]]
    
    print("═══════════════════════════════════════════")
    print(f"Window analyzed:            last {days} days")
    print(f"Total data gaps:            {len(outages)}")
    print(f"Likely real outages:        {len(real)}")
    print(f"Likely meter-only issues:   {len(fake)}")
    if not real.empty:
        print(f"Total downtime (real):      {int(real['minutes'].sum() / 60)}h")
        print(f"Longest outage:             {int(real['minutes'].max() / 60)}h")
    print("═══════════════════════════════════════════\n")
    
    if not real.empty:
        print("Real outages (siblings also affected):\n")
        print(real[["start", "end", "minutes", "siblings_hit_pct"]]
              .to_string(index=False))
    
    if not fake.empty:
        print("\nLikely meter-specific issues (siblings were fine):\n")
        print(fake[["start", "end", "minutes", "siblings_hit_pct"]]
              .head(10).to_string(index=False))