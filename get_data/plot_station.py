"""
plot_station.py

Single responsibility:
→ Return Plotly figure JSON for any station (DT / TS / SS)

NO PNG, NO FILE OUTPUT, NO CLI LOGIC
"""

from .fetch_dt import get_dt_info
from .fetch_feeder_from_station import (
    get_feeder_from_ts,
    get_feeder_from_ss,
)
from .get_graph import get_graph_json


# ─────────────────────────────────────────────
# PUBLIC API
# ─────────────────────────────────────────────

def plot_station(station_type: str,
                 station_id: int,
                 feeder_id: int | None = None) -> dict:
    """
    Returns Plotly JSON figure for station.

    Args:
        station_type: "DT" | "TS" | "SS"
        station_id: station identifier
        feeder_id: required for TS/SS

    Returns:
        dict (Plotly figure JSON)
    """

    station_type = station_type.upper()

   # ─── DT ────────────────────────────────
    if station_type == "DT":
        info = get_dt_info(station_id)
        if info is None:
            raise ValueError(f"DT with id={station_id} does not exist")

        # Case 1: DT has its own meter
        if info["meter_id"] is not None:
            return get_graph_json(
                meter_id=info["meter_id"],
                name=info["name"],
                nameplate_kva=info.get("nameplate_kva"),
            )

        # Case 2: DT has no meter → walk up the chain: F11 → F33
        from .db import q

        # Try parent F11 with a meter
        parent_f11 = q("""
            SELECT TOP 1 f.Id, f.Name, f.MeterId, f.NameplateRating, f.Feeder33Id
            FROM DistributionSubstation dt
            JOIN Feeders11 f ON dt.Feeder11Id = f.Id
            WHERE dt.Id = :dt_id
              AND f.MeterId IS NOT NULL
        """, {"dt_id": station_id})

        if not parent_f11.empty:
            f = parent_f11.iloc[0]
            name = f"[via F11 feeder] {info['name']} ← {f['Name']}"
            return get_graph_json(
                meter_id=int(f["MeterId"]),
                name=name,
                nameplate_kva=(int(f["NameplateRating"])
                               if f["NameplateRating"] is not None else None),
            )

        # F11 has no meter → try F33 (parent of F11)
        parent_f33 = q("""
            SELECT TOP 1 f33.Id, f33.Name, f33.MeterId, f33.NameplateRating
            FROM DistributionSubstation dt
            JOIN Feeders11 f11 ON dt.Feeder11Id = f11.Id
            JOIN Feeders33 f33 ON f11.Feeder33Id = f33.Id
            WHERE dt.Id = :dt_id
              AND f33.MeterId IS NOT NULL
        """, {"dt_id": station_id})

        if not parent_f33.empty:
            f = parent_f33.iloc[0]
            name = f"[via F33 feeder] {info['name']} ← {f['Name']}"
            return get_graph_json(
                meter_id=int(f["MeterId"]),
                name=name,
                nameplate_kva=(int(f["NameplateRating"])
                               if f["NameplateRating"] is not None else None),
            )

        # DT may also be connected directly to F33 (not through F11)
        direct_f33 = q("""
            SELECT TOP 1 f33.Id, f33.Name, f33.MeterId, f33.NameplateRating
            FROM DistributionSubstation dt
            JOIN Feeders33 f33 ON dt.Feeder33Id = f33.Id
            WHERE dt.Id = :dt_id
              AND f33.MeterId IS NOT NULL
        """, {"dt_id": station_id})

        if not direct_f33.empty:
            f = direct_f33.iloc[0]
            name = f"[via F33 feeder] {info['name']} ← {f['Name']}"
            return get_graph_json(
                meter_id=int(f["MeterId"]),
                name=name,
                nameplate_kva=(int(f["NameplateRating"])
                               if f["NameplateRating"] is not None else None),
            )

        # Nothing found up the chain
        raise ValueError(
            f"DT '{info['name']}' has no meter, nor does any feeder above it"
        )

        # Case 2: DT has no meter → try parent Feeder11
        from .db import q
        parent = q("""
            SELECT TOP 1 f.Id, f.Name, f.MeterId, f.NameplateRating
            FROM DistributionSubstation dt
            JOIN Feeders11 f ON dt.Feeder11Id = f.Id
            WHERE dt.Id = :dt_id
              AND f.MeterId IS NOT NULL
        """, {"dt_id": station_id})

        if not parent.empty:
            f = parent.iloc[0]
            name = f"[via parent feeder] {info['name']} ← {f['Name']}"
            return get_graph_json(
                meter_id=int(f["MeterId"]),
                name=name,
                nameplate_kva=(int(f["NameplateRating"])
                               if f["NameplateRating"] is not None
                               else None),
            )

        # Case 3: No parent feeder meter either — give up
        raise ValueError(
            f"DT '{info['name']}' has no meter and parent feeder has no meter"
        )

    # ─── TS / SS ───────────────────────────
    if station_type in ("TS", "SS"):

        if feeder_id is None:
            raise ValueError(f"feeder_id is required for {station_type}")

        feeder = (
            get_feeder_from_ts(station_id, feeder_id)
            if station_type == "TS"
            else get_feeder_from_ss(station_id, feeder_id)
        )

        if feeder is None:
            raise ValueError(
                f"Feeder {feeder_id} not found on {station_type} {station_id}"
            )

        if feeder["meter_id"] is None:
            raise ValueError("Feeder has no meter attached")

        name = f"{feeder['station']['name']} → {feeder['name']}"

        return get_graph_json(
            meter_id=feeder["meter_id"],
            name=name,
            nameplate_kva=feeder.get("nameplate_kva"),
        )

    raise ValueError(f"Invalid station_type: {station_type}")