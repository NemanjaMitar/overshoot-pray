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

        if info["meter_id"] is None:
            raise ValueError(f"DT '{info['name']}' has no meter attached")

        return get_graph_json(
            meter_id=info["meter_id"],
            name=info["name"],
            nameplate_kva=info.get("nameplate_kva"),
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