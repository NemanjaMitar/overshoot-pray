"""
TS (Transmission Station) fetcher.

For a given TS, returns its feeders (F33 + "Trade" F11) and their
24h readings (voltage, current, kWh) via the generic
fetch_feeder_readings function.

Usage:
    from fetch_ts import get_ts_info, get_ts_feeders, get_ts_readings_24h

    info     = get_ts_info(1)
    feeders  = get_ts_feeders(1)
    bundle   = get_ts_readings_24h(1)
"""

from .db import q
from .fetch_feeder import get_feeder_readings_24h
import pandas as pd


def get_ts_info(ts_id: int) -> dict | None:
    """
    Metadata for a TS.

    Returns:
        dict: id, name, latitude, longitude  (or None if TS doesn't exist)
    """
    df = q("""
        SELECT Id, Name, Latitude, Longitude
        FROM TransmissionStations WHERE Id = :ts_id
    """, {"ts_id": ts_id})
    if df.empty:
        return None
    row = df.iloc[0]
    return {
        "id": int(row["Id"]),
        "name": row["Name"],
        "latitude": float(row["Latitude"]) if pd.notna(row["Latitude"]) else None,
        "longitude": float(row["Longitude"]) if pd.notna(row["Longitude"]) else None,
    }


def get_ts_feeders(ts_id: int) -> dict:
    """
    All feeders coming OUT of this TS.

    Returns:
        dict with two DataFrames:
          "F33":        F33 feeders starting from this TS
                        [id, name, meter_id, nameplate_kva]
          "F11_trade":  Trade F11 feeders going directly from TS to DT
                        (no intermediate F33/SS)
                        [id, name, meter_id, nameplate_kva, ts_id]
    """
    f33 = q("""
        SELECT Id AS id, Name AS name, MeterId AS meter_id,
               NameplateRating AS nameplate_kva
        FROM Feeders33
        WHERE TsId = :ts_id
          AND (IsDeleted = 0 OR IsDeleted IS NULL)
        ORDER BY Name
    """, {"ts_id": ts_id})

    f11_trade = q("""
        SELECT Id AS id, Name AS name, MeterId AS meter_id,
               NameplateRating AS nameplate_kva, TsId AS ts_id
        FROM Feeders11
        WHERE TsId = :ts_id AND Feeder33Id IS NULL
        ORDER BY Name
    """, {"ts_id": ts_id})

    return {"F33": f33, "F11_trade": f11_trade}


def get_ts_readings_24h(ts_id: int) -> dict | None:
    """
    Fetch the last 24h of readings for ALL feeders going out of this TS.

    Returns:
        dict:
        {
            "info": {...},          # TS metadata
            "F33": [                # list of F33 feeders out of this TS
                {
                    "id": int, "name": str, "meter_id": int,
                    "nameplate_kva": int,
                    "readings": DataFrame  # Ts, V_a..V_c, I_a..I_c, kwh
                }, ...
            ],
            "F11_trade": [...]      # same shape as F33
        }

        Returns None if the TS doesn't exist.
    """
    info = get_ts_info(ts_id)
    if info is None:
        return None

    feeders = get_ts_feeders(ts_id)

    def _hydrate(df: pd.DataFrame) -> list:
        out = []
        for _, r in df.iterrows():
            readings = (
                get_feeder_readings_24h(int(r["meter_id"]))
                if pd.notna(r["meter_id"]) else None
            )
            out.append({
                "id": int(r["id"]),
                "name": r["name"],
                "meter_id": int(r["meter_id"]) if pd.notna(r["meter_id"]) else None,
                "nameplate_kva": int(r["nameplate_kva"]) if pd.notna(r["nameplate_kva"]) else None,
                "readings": readings,
            })
        return out

    return {
        "info": info,
        "F33": _hydrate(feeders["F33"]),
        "F11_trade": _hydrate(feeders["F11_trade"]),
    }


def list_transmission_stations() -> pd.DataFrame:
    """Lists all TSs with their coordinates.
    Columns: id, name, latitude, longitude
    """
    return q("""
        SELECT Id AS id, Name AS name, Latitude AS latitude, Longitude AS longitude
        FROM TransmissionStations
        ORDER BY Name
    """)


# ===== CLI =====
if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage:")
        print("  python3 fetch_ts.py list                # list all TSs")
        print("  python3 fetch_ts.py <ts_id>             # feeders + readings")
        print("  python3 fetch_ts.py feeders <ts_id>     # feeders only")
        sys.exit(0)

    arg = sys.argv[1]

    if arg == "list":
        print(list_transmission_stations().to_string(index=False))
        sys.exit(0)

    if arg == "feeders":
        ts_id = int(sys.argv[2])
        feeders = get_ts_feeders(ts_id)
        print(f"F33 feeders from TS {ts_id}:")
        print(feeders["F33"].to_string(index=False))
        print(f"\nTrade F11 feeders from TS {ts_id}:")
        print(feeders["F11_trade"].to_string(index=False))
        sys.exit(0)

    ts_id = int(arg)
    bundle = get_ts_readings_24h(ts_id)
    if bundle is None:
        print(f"No TS with id={ts_id}")
        sys.exit(1)

    print(f"TS: {bundle['info']['name']} @ "
          f"({bundle['info']['latitude']}, {bundle['info']['longitude']})\n")

    print(f"=== {len(bundle['F33'])} F33 feeders ===")
    for f in bundle["F33"]:
        n = len(f["readings"]) if f["readings"] is not None else 0
        print(f"  [{f['id']}] {f['name']}  ({n} readings)")

    print(f"\n=== {len(bundle['F11_trade'])} Trade F11 feeders ===")
    for f in bundle["F11_trade"]:
        n = len(f["readings"]) if f["readings"] is not None else 0
        print(f"  [{f['id']}] {f['name']}  ({n} readings)")