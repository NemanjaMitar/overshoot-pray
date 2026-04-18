"""
DT (Distribution Transformer) readings fetcher.

Returns V/I per phase AND total kWh for a given DT over the last 24h.
Pulls from both MeterReads (voltage/current) and MeterReadTfes (energy).

Usage:
    from fetch_dt import get_dt_readings_24h, list_active_dts, get_dt_info

    dts = list_active_dts()                   # DataFrame of all DTs with data
    readings = get_dt_readings_24h(335)       # DataFrame or None
    info = get_dt_info(335)                   # dict or None
"""

from .db import q
import pandas as pd


def get_dt_readings_24h(dt_id: int) -> pd.DataFrame | None:
    """
    Fetch the last 24h of readings for a specific DT, combining
    phase measurements (from MeterReads) with total energy
    (from MeterReadTfes). Timestamps from both tables are merged;
    missing values appear as None/NaN.

    The 24h window is relative to the DT's own latest reading
    across both tables, so inactive DTs still return their most
    recent available data.

    Args:
        dt_id: DistributionSubstation.Id

    Returns:
        DataFrame with 8 columns:
            Ts    - timestamp (datetime)
            V_a   - Phase A voltage  (raw DB scale; divide by 100 for Volts)
            V_b   - Phase B voltage
            V_c   - Phase C voltage
            I_a   - Phase A current  (raw DB scale; divide by 100 for Amps)
            I_b   - Phase B current
            I_c   - Phase C current
            kwh   - total energy counter (raw DB value from MeterReadTfes)

        Returns None if the DT doesn't exist, has no meter,
        or has no readings in either table.
    """
    # 1. Find the meter attached to this DT
    meter = q(
        "SELECT MeterId FROM DistributionSubstation WHERE Id = :dt_id",
        {"dt_id": dt_id}
    )
    if meter.empty or pd.isna(meter.iloc[0]["MeterId"]):
        return None
    meter_id = int(meter.iloc[0]["MeterId"])

    # 2. Latest timestamp across BOTH source tables for this meter
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

    # 3. FULL OUTER JOIN: pivot V/I per timestamp, merge with kWh
    readings = q("""
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

    return readings if not readings.empty else None


def get_dt_info(dt_id: int) -> dict | None:
    """
    Fetch metadata about a DT (useful for map pins, labels, etc.).

    Args:
        dt_id: DistributionSubstation.Id

    Returns:
        dict with keys: id, name, meter_id, latitude, longitude, nameplate_kva
        or None if the DT doesn't exist.
    """
    df = q("""
        SELECT Id, Name, MeterId, NameplateRating, Latitude, Longitude
        FROM DistributionSubstation
        WHERE Id = :dt_id
    """, {"dt_id": dt_id})

    if df.empty:
        return None

    row = df.iloc[0]
    return {
        "id": int(row["Id"]),
        "name": row["Name"],
        "meter_id": int(row["MeterId"]) if pd.notna(row["MeterId"]) else None,
        "latitude": float(row["Latitude"]) if pd.notna(row["Latitude"]) else None,
        "longitude": float(row["Longitude"]) if pd.notna(row["Longitude"]) else None,
        "nameplate_kva": int(row["NameplateRating"]) if pd.notna(row["NameplateRating"]) else None,
    }


def list_active_dts() -> pd.DataFrame:
    """
    List all DTs that have at least one meter reading in either
    MeterReads or MeterReadTfes.
    Useful for populating a dropdown, filter list, or map layer.

    Returns:
        DataFrame with columns:
            id             - DistributionSubstation.Id
            name           - DT name
            meter_id       - MeterId attached to this DT
            last_reading   - timestamp of the most recent reading (datetime)
    """
    return q("""
        WITH any_reads AS (
            SELECT Mid, MAX(Ts) AS last_ts FROM MeterReads    GROUP BY Mid
            UNION ALL
            SELECT Mid, MAX(Ts)            FROM MeterReadTfes GROUP BY Mid
        ),
        per_meter AS (
            SELECT Mid, MAX(last_ts) AS last_reading
            FROM any_reads
            GROUP BY Mid
        )
        SELECT dt.Id AS id, dt.Name AS name, dt.MeterId AS meter_id,
               pm.last_reading
        FROM DistributionSubstation dt
        JOIN per_meter pm ON dt.MeterId = pm.Mid
        ORDER BY dt.Name
    """)


# ===== CLI =====
if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage:")
        print("  python3 fetch_dt.py <dt_id>           # readings for one DT")
        print("  python3 fetch_dt.py list              # list all active DTs")
        print("  python3 fetch_dt.py search <text>     # search by name")
        print("  python3 fetch_dt.py info <dt_id>      # metadata only\n")
        print("Examples:")
        print("  python3 fetch_dt.py 335")
        print("  python3 fetch_dt.py search Buchanan")
        sys.exit(0)

    arg = sys.argv[1]

    if arg == "list":
        lista = list_active_dts()
        print(f"Active DTs: {len(lista)}\n")
        print(lista.to_string(index=False))
        sys.exit(0)

    if arg == "search":
        if len(sys.argv) < 3:
            print("Provide search text: python3 fetch_dt.py search <text>")
            sys.exit(1)
        search = sys.argv[2].lower()
        lista = list_active_dts()
        filtered = lista[lista["name"].str.lower().str.contains(search)]
        if filtered.empty:
            print(f"No DTs with name containing '{search}'")
        else:
            print(f"Found {len(filtered)} DTs:\n")
            print(filtered.to_string(index=False))
        sys.exit(0)

    if arg == "info":
        if len(sys.argv) < 3:
            print("Provide dt_id: python3 fetch_dt.py info <dt_id>")
            sys.exit(1)
        try:
            dt_id = int(sys.argv[2])
        except ValueError:
            print(f"'{sys.argv[2]}' is not a valid dt_id")
            sys.exit(1)
        info = get_dt_info(dt_id)
        if info is None:
            print(f"No DT with id={dt_id}")
            sys.exit(1)
        for k, v in info.items():
            print(f"  {k}: {v}")
        sys.exit(0)

    # Otherwise treat as dt_id
    try:
        dt_id = int(arg)
    except ValueError:
        print(f"Error: '{arg}' is not a valid dt_id (must be integer)")
        sys.exit(1)

    readings = get_dt_readings_24h(dt_id)
    if readings is None:
        print(f"No data for DT id={dt_id}")
        print("Run 'python3 fetch_dt.py list' to see available DTs.")
        sys.exit(1)

    print(f"DT {dt_id} — {len(readings)} readings in last 24h\n")
    print(readings.to_string(index=False))