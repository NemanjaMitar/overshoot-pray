from flask import Flask, render_template, jsonify, request
import os
import sys
import math
import json
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from plotly.utils import PlotlyJSONEncoder

from database.repositories import (
    DistributionSubstationRepository,
    SubstationRepository,
    TransmissionStationRepository,
    Feeder11Repository,
    Feeder33Repository,
    Feeder33SubstationRepository,
)

from get_data.plot_station import plot_station
from get_data.fetch_ts import get_ts_feeders
from get_data.fetch_ss import get_ss_feeders
from outages.get_system_outages import get_system_outages_optimized

app = Flask(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")


@app.route("/")
def index():
    return render_template("index.html")


def _valid_coord(lat, lon):
    return lat is not None and lon is not None


def _to_coord(row):
    if not _valid_coord(row.get("Latitude"), row.get("Longitude")):
        return None
    return [float(row["Longitude"]), float(row["Latitude"])]


def _euclidean_distance(a, b):
    return math.sqrt((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2)


def _order_by_nearest(start_coord, coords):
    if not start_coord or not coords:
        return []

    unique_coords = []
    seen = set()
    for c in coords:
        key = (round(c[0], 6), round(c[1], 6))
        if key not in seen:
            seen.add(key)
            unique_coords.append(c)

    ordered = []
    remaining = unique_coords[:]
    current = start_coord

    while remaining:
        nearest = min(remaining, key=lambda x: _euclidean_distance(current, x))
        ordered.append(nearest)
        remaining.remove(nearest)
        current = nearest

    return ordered


# ── Helpers za konverziju feedera u JSON opcije ───────────────────────────────
# get_ts_feeders() vraća {"F33": df, "F11_trade": df}
# get_ss_feeders() vraća {"F33_incoming": df, "F11_outgoing": df}
# Nijedan df nema kolonu "role" — dodajemo je ovdje.

def _ts_feeders_to_options(feeders_dict):
    """
    Konvertuje rezultat get_ts_feeders() u listu JSON-serializable dict-ova.
    Dodaje 'role' kolonu koja nedostaje u DataFrameu.
    """
    options = []

    for _, row in feeders_dict["F33"].iterrows():
        mid = row.get("meter_id")
        nkva = row.get("nameplate_kva")
        options.append({
            "id":            int(row["id"]),
            "name":          row["name"],
            "role":          "F33",
            "meter_id":      int(mid) if mid is not None and mid == mid else None,
            "nameplate_kva": int(nkva) if nkva is not None and nkva == nkva else None,
            "has_readings":  mid is not None and mid == mid,
        })

    for _, row in feeders_dict["F11_trade"].iterrows():
        mid = row.get("meter_id")
        nkva = row.get("nameplate_kva")
        options.append({
            "id":            int(row["id"]),
            "name":          row["name"],
            "role":          "F11_trade",
            "meter_id":      int(mid) if mid is not None and mid == mid else None,
            "nameplate_kva": int(nkva) if nkva is not None and nkva == nkva else None,
            "has_readings":  mid is not None and mid == mid,
        })

    return options


def _ss_feeders_to_options(feeders_dict):
    """
    Konvertuje rezultat get_ss_feeders() u listu JSON-serializable dict-ova.
    Dodaje 'role' kolonu koja nedostaje u DataFrameu.
    """
    options = []

    for _, row in feeders_dict["F33_incoming"].iterrows():
        mid = row.get("meter_id")
        nkva = row.get("nameplate_kva")
        options.append({
            "id":            int(row["id"]),
            "name":          row["name"],
            "role":          "F33_incoming",
            "meter_id":      int(mid) if mid is not None and mid == mid else None,
            "nameplate_kva": int(nkva) if nkva is not None and nkva == nkva else None,
            "has_readings":  mid is not None and mid == mid,
        })

    for _, row in feeders_dict["F11_outgoing"].iterrows():
        mid = row.get("meter_id")
        nkva = row.get("nameplate_kva")
        options.append({
            "id":            int(row["id"]),
            "name":          row["name"],
            "role":          "F11_outgoing",
            "meter_id":      int(mid) if mid is not None and mid == mid else None,
            "nameplate_kva": int(nkva) if nkva is not None and nkva == nkva else None,
            "has_readings":  mid is not None and mid == mid,
        })

    return options


@app.route("/data/trafostanice")
def data_trafostanice():
    trans_repo = TransmissionStationRepository()
    sub_repo   = SubstationRepository()
    dist_repo  = DistributionSubstationRepository()

    features = []

    for row in trans_repo.get_all(limit=None):
        if row["Latitude"] is None or row["Longitude"] is None:
            continue
        features.append({
            "type": "Feature",
            "geometry": {
                "type": "Point",
                "coordinates": [float(row["Longitude"]), float(row["Latitude"])],
            },
            "properties": {
                "id":    row.get("Id"),
                "naziv": row.get("Name") or "Nepoznato",
                "tip":   "transmission",
            },
        })

    for row in sub_repo.get_all(limit=None):
        if row["Latitude"] is None or row["Longitude"] is None:
            continue
        features.append({
            "type": "Feature",
            "geometry": {
                "type": "Point",
                "coordinates": [float(row["Longitude"]), float(row["Latitude"])],
            },
            "properties": {
                "id":    row.get("Id"),
                "naziv": row.get("Name") or "Nepoznato",
                "tip":   "substation",
            },
        })

    for row in dist_repo.get_all(limit=None):
        if row["Latitude"] is None or row["Longitude"] is None:
            continue
        features.append({
            "type": "Feature",
            "geometry": {
                "type": "Point",
                "coordinates": [float(row["Longitude"]), float(row["Latitude"])],
            },
            "properties": {
                "id":    row.get("Id"),
                "naziv": row.get("Name") or "Nepoznato",
                "tip":   "distribution",
            },
        })

    return jsonify({
        "type": "FeatureCollection",
        "features": features,
    })


@app.route("/data/vodovi")
def data_vodovi():
    trans_repo       = TransmissionStationRepository()
    sub_repo         = SubstationRepository()
    dist_repo        = DistributionSubstationRepository()
    feeder11_repo    = Feeder11Repository()
    feeder33_repo    = Feeder33Repository()
    feeder33_sub_repo = Feeder33SubstationRepository()

    transmission_rows = trans_repo.get_all(limit=None)
    substation_rows   = sub_repo.get_all(limit=None)
    distribution_rows = dist_repo.get_all(limit=None)
    feeder11_rows     = feeder11_repo.get_all(limit=None)
    feeder33_rows     = feeder33_repo.get_all(limit=None)
    feeder33_sub_rows = feeder33_sub_repo.get_all(limit=None)

    transmission_by_id = {row["Id"]: row for row in transmission_rows}
    substation_by_id   = {row["Id"]: row for row in substation_rows}
    distribution_by_id = {row["Id"]: row for row in distribution_rows}

    feeder33_to_substations = {}
    for row in feeder33_sub_rows:
        feeder_id = row.get("Feeders33Id")
        sub_id    = row.get("SubstationsId")
        if feeder_id is None or sub_id is None:
            continue
        feeder33_to_substations.setdefault(feeder_id, []).append(sub_id)

    feeder11_to_distributions = {}
    feeder33_to_distributions = {}

    for row in distribution_rows:
        dist_id    = row.get("Id")
        feeder11_id = row.get("Feeder11Id")
        feeder33_id = row.get("Feeder33Id")

        if feeder11_id is not None:
            feeder11_to_distributions.setdefault(feeder11_id, []).append(dist_id)
        if feeder33_id is not None:
            feeder33_to_distributions.setdefault(feeder33_id, []).append(dist_id)

    features = []

    # FEEDERS 33: TS -> SS -> eventualno direktni DT
    for feeder in feeder33_rows:
        feeder_id   = feeder.get("Id")
        feeder_name = feeder.get("Name") or f"Feeder33 #{feeder_id}"
        ts_id       = feeder.get("TsId")

        source_row = transmission_by_id.get(ts_id)
        if not source_row:
            continue

        source_coord = _to_coord(source_row)
        if not source_coord:
            continue

        substation_coords  = []
        distribution_coords = []
        station_keys       = []

        if ts_id is not None:
            station_keys.append(f"transmission:{ts_id}")

        for sub_id in feeder33_to_substations.get(feeder_id, []):
            sub_row = substation_by_id.get(sub_id)
            if not sub_row:
                continue
            coord = _to_coord(sub_row)
            if coord:
                substation_coords.append(coord)
                station_keys.append(f"substation:{sub_id}")

        for dist_id in feeder33_to_distributions.get(feeder_id, []):
            dist_row = distribution_by_id.get(dist_id)
            if not dist_row:
                continue
            coord = _to_coord(dist_row)
            if coord:
                distribution_coords.append(coord)
                station_keys.append(f"distribution:{dist_id}")

        ordered_substations  = _order_by_nearest(source_coord, substation_coords)
        last_coord           = ordered_substations[-1] if ordered_substations else source_coord
        ordered_distributions = _order_by_nearest(last_coord, distribution_coords)

        coords = [source_coord] + ordered_substations + ordered_distributions
        if len(coords) < 2:
            continue

        features.append({
            "type": "Feature",
            "geometry": {"type": "LineString", "coordinates": coords},
            "properties": {
                "id":               feeder_id,
                "naziv":            feeder_name,
                "tip":              "feeder33",
                "source_type":      "transmission",
                "source_id":        ts_id,
                "children_count":   len(substation_coords) + len(distribution_coords),
                "nameplate_rating": feeder.get("NameplateRating"),
                "meter_id":         feeder.get("MeterId"),
                "station_keys":     station_keys,
            },
        })

    # FEEDERS 11: SS -> DT ili TS -> DT (trade)
    for feeder in feeder11_rows:
        feeder_id   = feeder.get("Id")
        feeder_name = feeder.get("Name") or f"Feeder11 #{feeder_id}"
        ss_id       = feeder.get("SsId")
        ts_id       = feeder.get("TsId")

        source_row   = None
        source_coord = None
        source_type  = None
        source_id    = None
        station_keys = []

        if ss_id is not None and ss_id in substation_by_id:
            source_row   = substation_by_id[ss_id]
            source_coord = _to_coord(source_row)
            source_type  = "substation"
            source_id    = ss_id
            station_keys.append(f"substation:{ss_id}")
        elif ts_id is not None and ts_id in transmission_by_id:
            source_row   = transmission_by_id[ts_id]
            source_coord = _to_coord(source_row)
            source_type  = "transmission"
            source_id    = ts_id
            station_keys.append(f"transmission:{ts_id}")

        if not source_row or not source_coord:
            continue

        distribution_coords = []
        for dist_id in feeder11_to_distributions.get(feeder_id, []):
            dist_row = distribution_by_id.get(dist_id)
            if not dist_row:
                continue
            coord = _to_coord(dist_row)
            if coord:
                distribution_coords.append(coord)
                station_keys.append(f"distribution:{dist_id}")

        ordered_distributions = _order_by_nearest(source_coord, distribution_coords)
        coords = [source_coord] + ordered_distributions

        if len(coords) < 2:
            continue

        features.append({
            "type": "Feature",
            "geometry": {"type": "LineString", "coordinates": coords},
            "properties": {
                "id":                feeder_id,
                "naziv":             feeder_name,
                "tip":               "feeder11",
                "source_type":       source_type,
                "source_id":         source_id,
                "children_count":    len(distribution_coords),
                "nameplate_rating":  feeder.get("NameplateRating"),
                "meter_id":          feeder.get("MeterId"),
                "parent_feeder33_id": feeder.get("Feeder33Id"),
                "ts_id":             ts_id,
                "ss_id":             ss_id,
                "station_keys":      station_keys,
            },
        })

    return jsonify({
        "type": "FeatureCollection",
        "features": features,
    })


@app.route("/api/station-options/<station_type>/<int:station_id>")
def station_options(station_type, station_id):
    station_type = station_type.upper()

    try:
        if station_type == "DT":
            return jsonify({
                "station_type":    "DT",
                "station_id":      station_id,
                "requires_feeder": False,
                "feeders":         [],
            })

        if station_type == "TS":
            feeders = get_ts_feeders(station_id)
            return jsonify({
                "station_type":    "TS",
                "station_id":      station_id,
                "requires_feeder": True,
                "feeders":         _ts_feeders_to_options(feeders),
            })

        if station_type == "SS":
            feeders = get_ss_feeders(station_id)
            return jsonify({
                "station_type":    "SS",
                "station_id":      station_id,
                "requires_feeder": True,
                "feeders":         _ss_feeders_to_options(feeders),
            })

        return jsonify({"error": f"Invalid station type: {station_type}"}), 400

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/station-plot/<station_type>/<int:station_id>")
def station_plot(station_type, station_id):
    station_type = station_type.upper()
    feeder_id    = request.args.get("feeder_id", type=int)

    try:
        figure = plot_station(station_type, station_id, feeder_id=feeder_id)
        payload = {
            "station_type": station_type,
            "station_id":   station_id,
            "feeder_id":    feeder_id,
            "figure":       figure,
        }
        return app.response_class(
            response=json.dumps(payload, cls=PlotlyJSONEncoder),
            mimetype="application/json",
        )
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": f"Unexpected error: {str(e)}"}), 500
    
_outages_cache = {"data": None, "ts": 0}

@app.route("/data/outages")
def data_outages():
    global _outages_cache
    if time.time() - _outages_cache["ts"] > 86400:  # refresh svaki dan
        _outages_cache["data"] = get_system_outages_optimized(days=3)
        _outages_cache["ts"]   = time.time()
    return jsonify(_outages_cache["data"])

if __name__ == "__main__":
    app.run(debug=True)