"""
SS (Substation / Injection Substation) fetcher.

SS has no direct meter. Readings for an SS mean readings of the feeders
flowing through it:
  - F33 incoming (from TS into SS, via Feeder33Substation)
  - F11 outgoing (from SS down to DTs, via F33 that reach this SS)

Usage:
    from fetch_ss import get_ss_info, get_ss_feeders, get_ss_readings_24h
"""

from .db import q
from .fetch_feeder import get_feeder_readings_24h
import pandas as pd


def get_ss_info(ss_id: int) -> dict | None:
    df = q("""
        SELECT Id, Name, Latitude, Longitude
        FROM Substations WHERE Id = :ss_id
    """, {"ss_id": ss_id})
    if df.empty:
        return None
    row = df.iloc[0]
    return {
        "id": int(row["Id"]),
        "name": row["Name"],
        "latitude": float(row["Latitude"]) if pd.notna(row["Latitude"]) else None,
        "longitude": float(row["Longitude"]) if pd.notna(row["Longitude"]) else None,
    }


def get_ss_feeders(ss_id: int) -> dict:
    """
    Feeders attached to this SS.

    Returns:
        dict with two DataFrames:
          "F33_incoming": F33 feeders connected to this SS (via Feeder33Substation)
                          [id, name, meter_id, nameplate_kva]
          "F11_outgoing": F11 feeders whose parent F33 connects to this SS
                          [id, name, meter_id, nameplate_kva, feeder33_id]
    """
    f33_in = q("""
        SELECT f33.Id AS id, f33.Name AS name,
               f33.MeterId AS meter_id, f33.NameplateRating AS nameplate_kva
        FROM Feeder33Substation fs
        JOIN Feeders33 f33 ON fs.Feeders33Id = f33.Id
        WHERE fs.SubstationsId = :ss_id
          AND (f33.IsDeleted = 0 OR f33.IsDeleted IS NULL)
        ORDER BY f33.Name
    """, {"ss_id": ss_id})

    f11_out = q("""
        SELECT f11.Id AS id, f11.Name AS name,
               f11.MeterId AS meter_id, f11.NameplateRating AS nameplate_kva,
               f11.Feeder33Id AS feeder33_id
        FROM Feeders11 f11
        WHERE f11.Feeder33Id IN (
            SELECT fs.Feeders33Id
            FROM Feeder33Substation fs
            WHERE fs.SubstationsId = :ss_id
        )
        ORDER BY f11.Name
    """, {"ss_id": ss_id})

    return {"F33_incoming": f33_in, "F11_outgoing": f11_out}


def get_ss_readings_24h(ss_id: int) -> dict | None:
    """
    Fetch the last 24h of readings for F33 incoming and F11 outgoing feeders.

    Returns:
        dict:
        {
            "info": {...},
            "F33_incoming": [ { id, name, meter_id, nameplate_kva, readings }, ... ],
            "F11_outgoing": [ ... ]
        }
        Returns None if SS doesn't exist.
    """
    info = get_ss_info(ss_id)
    if info is None:
        return None

    feeders = get_ss_feeders(ss_id)

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
        "F33_incoming": _hydrate(feeders["F33_incoming"]),
        "F11_outgoing": _hydrate(feeders["F11_outgoing"]),
    }


def list_substations() -> pd.DataFrame:
    """Columns: id, name, latitude, longitude"""
    return q("""
        SELECT Id AS id, Name AS name, Latitude AS latitude, Longitude AS longitude
        FROM Substations
        ORDER BY Name
    """)


# ===== CLI =====
if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage:")
        print("  python3 fetch_ss.py list")
        print("  python3 fetch_ss.py <ss_id>")
        print("  python3 fetch_ss.py feeders <ss_id>")
        sys.exit(0)

    arg = sys.argv[1]

    if arg == "list":
        print(list_substations().to_string(index=False))
        sys.exit(0)

    if arg == "feeders":
        ss_id = int(sys.argv[2])
        f = get_ss_feeders(ss_id)
        print(f"F33 incoming to SS {ss_id}:")
        print(f["F33_incoming"].to_string(index=False))
        print(f"\nF11 outgoing from SS {ss_id}:")
        print(f["F11_outgoing"].to_string(index=False))
        sys.exit(0)

    ss_id = int(arg)
    bundle = get_ss_readings_24h(ss_id)
    if bundle is None:
        print(f"No SS with id={ss_id}")
        sys.exit(1)

    print(f"SS: {bundle['info']['name']} @ "
          f"({bundle['info']['latitude']}, {bundle['info']['longitude']})\n")

    print(f"=== {len(bundle['F33_incoming'])} F33 incoming ===")
    for f in bundle["F33_incoming"]:
        n = len(f["readings"]) if f["readings"] is not None else 0
        print(f"  [{f['id']}] {f['name']}  ({n} readings)")

    print(f"\n=== {len(bundle['F11_outgoing'])} F11 outgoing ===")
    for f in bundle["F11_outgoing"]:
        n = len(f["readings"]) if f["readings"] is not None else 0
        print(f"  [{f['id']}] {f['name']}  ({n} readings)")