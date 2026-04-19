import os
import sys
import json
import pandas as pd

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from get_data.db import q

GAP_THRESHOLD_MIN       = 90
OUTAGE_CONFIRMATION_PCT = 50.0
END_OF_DATA_BUFFER_H    = 3


def _merge_overlapping(outages: list) -> list:
    """
    Merge overlapping outage intervals per feeder.
    Multiple DT meters on the same feeder often detect the same outage
    with slightly different start/end times — this collapses them into one.
    """
    if not outages:
        return outages

    df = pd.DataFrame(outages)
    df["start"] = pd.to_datetime(df["start"])
    df["end"]   = pd.to_datetime(df["end"])
    df = df.sort_values(["f_id", "start"])

    merged = []
    for f_id, group in df.groupby("f_id"):
        current = None
        for _, row in group.iterrows():
            if current is None:
                current = row.to_dict()
            elif row["start"] <= pd.Timestamp(current["end"]):
                # Overlapping — extend window, keep highest confidence
                current["end"]        = max(current["end"], row["end"])
                current["duration"]   = int(
                    (current["end"] - current["start"]).total_seconds() / 60
                )
                current["confidence"] = max(current["confidence"], row["confidence"])
            else:
                merged.append(current)
                current = row.to_dict()
        if current:
            merged.append(current)

    return merged


def get_system_outages_optimized(days: int = 3):
    # 1. Topology mapping
    topology = q("""
        SELECT Feeder11Id as f_id, MeterId as m_id, 'DT' as type
        FROM DistributionSubstation WHERE MeterId IS NOT NULL
        UNION
        SELECT Id as f_id, MeterId as m_id, 'F11' as type
        FROM Feeders11 WHERE MeterId IS NOT NULL
    """)

    feeder_map = topology.groupby('f_id')['m_id'].apply(list).to_dict()

    all_m_ids = topology['m_id'].unique().tolist()
    ids_str   = ",".join(map(str, all_m_ids))

    # 2. Fetch all readings in one query
    raw_data = q(f"""
        SELECT Mid, Ts
        FROM MeterReads
        WHERE Mid IN ({ids_str})
          AND Ts > DATEADD(DAY, -{days}, GETDATE())
    """)

    if raw_data.empty:
        return {"status": "empty", "outages": []}

    raw_data["Ts"] = pd.to_datetime(raw_data["Ts"])

    # Global last timestamp — used to discard end-of-dataset false alarms
    global_last_ts = raw_data["Ts"].max()

    meter_groups = {
        mid: group.sort_values("Ts")
        for mid, group in raw_data.groupby("Mid")
    }

    all_found_outages = []

    # 3. Analyse gaps per feeder
    for f_id, sibling_ids in feeder_map.items():
        feeder_outages_detected = []

        for m_id in sibling_ids:
            if m_id not in meter_groups:
                continue

            group         = meter_groups[m_id].copy()
            group["prev"] = group["Ts"].shift(1)
            group["gap"]  = (group["Ts"] - group["prev"]).dt.total_seconds() / 60

            gaps = group[group["gap"] > GAP_THRESHOLD_MIN]

            for _, gap in gaps.iterrows():
                start, end = gap["prev"], gap["Ts"]

                # Discard end-of-dataset artifacts
                hours_before_end = (global_last_ts - end).total_seconds() / 3600
                if hours_before_end < END_OF_DATA_BUFFER_H:
                    continue

                total_siblings = len(sibling_ids)
                if total_siblings <= 1:
                    continue

                others_silent = 0
                for s_id in sibling_ids:
                    if s_id == m_id:
                        continue
                    if s_id not in meter_groups:
                        others_silent += 1
                        continue

                    s_data       = meter_groups[s_id]
                    has_readings = s_data[
                        (s_data["Ts"] > start) & (s_data["Ts"] < end)
                    ]
                    if has_readings.empty:
                        others_silent += 1

                hit_pct = (others_silent / (total_siblings - 1)) * 100

                if hit_pct >= OUTAGE_CONFIRMATION_PCT:
                    feeder_outages_detected.append({
                        "f_id":       int(f_id),
                        "start":      start,
                        "end":        end,
                        "duration":   int(gap["gap"]),
                        "confidence": round(hit_pct, 1)
                    })

        if feeder_outages_detected:
            f_df = (pd.DataFrame(feeder_outages_detected)
                    .drop_duplicates(subset=['f_id', 'start', 'end']))
            all_found_outages.extend(f_df.to_dict('records'))

    # 4. Merge overlapping intervals per feeder
    all_found_outages = _merge_overlapping(all_found_outages)

    # 5. Attach feeder names
    f_names  = q("SELECT Id, Name FROM Feeders11")
    name_map = f_names.set_index('Id')['Name'].to_dict()

    final_list = [
        {
            "feeder_name":      name_map.get(o['f_id'], f"Feeder {o['f_id']}"),
            "start":            str(o['start']),
            "end":              str(o['end']),
            "duration_minutes": o['duration'],
            "confidence_pct":   o['confidence']
        }
        for o in all_found_outages
    ]

    return {
        "analysis_days":  days,
        "total_events":   len(final_list),
        "outages":        sorted(final_list, key=lambda x: x['start'], reverse=True)
    }


if __name__ == "__main__":
    print(json.dumps(get_system_outages_optimized(3), indent=2))