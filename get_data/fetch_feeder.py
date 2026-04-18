"""
Generic feeder reading fetcher by meter_id.
Used by fetch_ts and fetch_ss to avoid duplicating SQL per feeder type.
"""

from .db import q
import pandas as pd


def get_feeder_readings_24h(meter_id: int) -> pd.DataFrame | None:
    """
    Fetch the last 24h of combined readings for any meter
    (works for F33, F11, DT - the table lookup is identical).

    Args:
        meter_id: Meters.Id (any meter, not tied to a station type)

    Returns:
        DataFrame with columns:
            Ts, V_a, V_b, V_c, I_a, I_b, I_c, kwh
        or None if no readings.
    """
    last = q("""
        SELECT MAX(last_ts) AS last_ts FROM (
            SELECT MAX(Ts) AS last_ts FROM MeterReads     WHERE Mid = :mid
            UNION ALL
            SELECT MAX(Ts)            FROM MeterReadTfes  WHERE Mid = :mid
        ) t
    """, {"mid": meter_id})
    last_ts = last.iloc[0]["last_ts"]
    if last_ts is None:
        return None

    df = q("""
        WITH pivoted AS (
            SELECT mr.Ts,
                MAX(CASE WHEN mr.Cid = 6  THEN mr.Val END) AS V_a,
                MAX(CASE WHEN mr.Cid = 7  THEN mr.Val END) AS V_b,
                MAX(CASE WHEN mr.Cid = 8  THEN mr.Val END) AS V_c,
                MAX(CASE WHEN mr.Cid = 9  THEN mr.Val END) AS I_a,
                MAX(CASE WHEN mr.Cid = 10 THEN mr.Val END) AS I_b,
                MAX(CASE WHEN mr.Cid = 11 THEN mr.Val END) AS I_c
            FROM MeterReads mr
            WHERE mr.Mid = :mid
              AND mr.Ts > DATEADD(HOUR, -24, :last_ts)
            GROUP BY mr.Ts
        ),
        tfes AS (
            SELECT Ts, Val AS kwh
            FROM MeterReadTfes
            WHERE Mid = :mid
              AND Ts > DATEADD(HOUR, -24, :last_ts)
        )
        SELECT
            COALESCE(p.Ts, t.Ts) AS Ts,
            p.V_a, p.V_b, p.V_c,
            p.I_a, p.I_b, p.I_c,
            t.kwh
        FROM pivoted p
        FULL OUTER JOIN tfes t ON p.Ts = t.Ts
        ORDER BY Ts
    """, {"mid": meter_id, "last_ts": last_ts})

    return df if not df.empty else None