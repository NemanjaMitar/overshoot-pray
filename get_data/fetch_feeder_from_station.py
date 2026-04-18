"""
Feeder picker for selected TS or SS.

Thin layer over fetch_ts and fetch_ss. The UI flow is:
  1. User picks a TS or SS
  2. Call list_feeders_of_ts(ts_id) or list_feeders_of_ss(ss_id)
     -> populates a feeder dropdown
  3. User picks a feeder (by id)
  4. Call get_feeder_from_ts(ts_id, feeder_id)
     or  get_feeder_from_ss(ss_id, feeder_id)
     -> returns that feeder's info + readings for plotting
"""

from .fetch_ts import get_ts_readings_24h
from .fetch_ss import get_ss_readings_24h
import pandas as pd


# ------------------------------------------------------------------
# LIST — for populating a dropdown after user selects a station
# ------------------------------------------------------------------

def list_feeders_of_ts(ts_id: int) -> pd.DataFrame | None:
    """
    List every feeder coming out of the given TS.

    Returns:
        DataFrame with columns:
            id              - feeder id
            name            - feeder name
            role            - "F33" | "F11_trade"
            meter_id        - meter attached (may be None)
            nameplate_kva   - rated capacity (may be None)
            has_readings    - bool, True if this feeder has 24h data
        or None if the TS doesn't exist.
    """
    bundle = get_ts_readings_24h(ts_id)
    if bundle is None:
        return None

    rows = []
    for role, feeders in (("F33", bundle["F33"]), ("F11_trade", bundle["F11_trade"])):
        for f in feeders:
            rows.append({
                "id": f["id"],
                "name": f["name"],
                "role": role,
                "meter_id": f["meter_id"],
                "nameplate_kva": f["nameplate_kva"],
                "has_readings": f["readings"] is not None,
            })
    return pd.DataFrame(rows)


def list_feeders_of_ss(ss_id: int) -> pd.DataFrame | None:
    """
    List every feeder connected to the given SS (incoming F33 + outgoing F11).

    Returns:
        DataFrame with columns:
            id              - feeder id
            name            - feeder name
            role            - "F33_incoming" | "F11_outgoing"
            meter_id        - meter attached (may be None)
            nameplate_kva   - rated capacity (may be None)
            has_readings    - bool
        or None if the SS doesn't exist.
    """
    bundle = get_ss_readings_24h(ss_id)
    if bundle is None:
        return None

    rows = []
    for role, feeders in (("F33_incoming", bundle["F33_incoming"]),
                          ("F11_outgoing", bundle["F11_outgoing"])):
        for f in feeders:
            rows.append({
                "id": f["id"],
                "name": f["name"],
                "role": role,
                "meter_id": f["meter_id"],
                "nameplate_kva": f["nameplate_kva"],
                "has_readings": f["readings"] is not None,
            })
    return pd.DataFrame(rows)


# ------------------------------------------------------------------
# GET — for plotting one specific feeder
# ------------------------------------------------------------------

def get_feeder_from_ts(ts_id: int, feeder_id: int) -> dict | None:
    """
    Fetch one feeder (by id) from the given TS, with its 24h readings.

    Returns:
        dict {
            "id", "name", "role" ("F33" | "F11_trade"),
            "meter_id", "nameplate_kva",
            "readings": DataFrame [Ts, V_a..V_c, I_a..I_c, kwh] | None,
            "station": {"kind": "TS", "id", "name", "latitude", "longitude"}
        }
        or None if TS or feeder not found.
    """
    bundle = get_ts_readings_24h(ts_id)
    if bundle is None:
        return None

    for role, feeders in (("F33", bundle["F33"]), ("F11_trade", bundle["F11_trade"])):
        for f in feeders:
            if f["id"] == feeder_id:
                return {
                    "id": f["id"],
                    "name": f["name"],
                    "role": role,
                    "meter_id": f["meter_id"],
                    "nameplate_kva": f["nameplate_kva"],
                    "readings": f["readings"],
                    "station": {"kind": "TS", **bundle["info"]},
                }
    return None


def get_feeder_from_ss(ss_id: int, feeder_id: int) -> dict | None:
    """
    Fetch one feeder (by id) attached to the given SS, with its 24h readings.

    Returns:
        dict {
            "id", "name", "role" ("F33_incoming" | "F11_outgoing"),
            "meter_id", "nameplate_kva",
            "readings": DataFrame | None,
            "station": {"kind": "SS", "id", "name", "latitude", "longitude"}
        }
        or None if SS or feeder not found.
    """
    bundle = get_ss_readings_24h(ss_id)
    if bundle is None:
        return None

    for role, feeders in (("F33_incoming", bundle["F33_incoming"]),
                          ("F11_outgoing", bundle["F11_outgoing"])):
        for f in feeders:
            if f["id"] == feeder_id:
                return {
                    "id": f["id"],
                    "name": f["name"],
                    "role": role,
                    "meter_id": f["meter_id"],
                    "nameplate_kva": f["nameplate_kva"],
                    "readings": f["readings"],
                    "station": {"kind": "SS", **bundle["info"]},
                }
    return None


# ===== CLI =====
if __name__ == "__main__":
    import sys

    if len(sys.argv) < 3:
        print("Usage:")
        print("  python3 fetch_feeder_from_station.py ts_list <ts_id>")
        print("  python3 fetch_feeder_from_station.py ss_list <ss_id>")
        print("  python3 fetch_feeder_from_station.py ts <ts_id> <feeder_id>")
        print("  python3 fetch_feeder_from_station.py ss <ss_id> <feeder_id>")
        sys.exit(0)

    cmd = sys.argv[1]

    if cmd == "ts_list":
        df = list_feeders_of_ts(int(sys.argv[2]))
        print(df.to_string(index=False) if df is not None else "No such TS")
        sys.exit(0)

    if cmd == "ss_list":
        df = list_feeders_of_ss(int(sys.argv[2]))
        print(df.to_string(index=False) if df is not None else "No such SS")
        sys.exit(0)

    if cmd == "ts":
        res = get_feeder_from_ts(int(sys.argv[2]), int(sys.argv[3]))
    elif cmd == "ss":
        res = get_feeder_from_ss(int(sys.argv[2]), int(sys.argv[3]))
    else:
        print(f"Unknown command: {cmd}")
        sys.exit(1)

    if res is None:
        print("Not found.")
        sys.exit(1)

    print(f"[{res['role']}] {res['name']}  (id={res['id']})")
    print(f"  station:       {res['station']['kind']} '{res['station']['name']}'")
    print(f"  meter_id:      {res['meter_id']}")
    print(f"  nameplate_kva: {res['nameplate_kva']}")
    if res["readings"] is None:
        print("  readings:      None")
    else:
        print(f"  readings:      {len(res['readings'])} rows")
        print(res["readings"].head(3).to_string(index=False))