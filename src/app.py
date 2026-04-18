from flask import Flask, render_template, jsonify
import os
import math

from database.repositories import (
    DistributionSubstationRepository,
    SubstationRepository,
    TransmissionStationRepository,
    Feeder11Repository,
    Feeder33Repository,
    Feeder33SubstationRepository,
)

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
    """
    Vraća coords poređane nearest-neighbor heuristikom
    počevši od start_coord.
    """
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


@app.route("/data/trafostanice")
def data_trafostanice():
    trans_repo = TransmissionStationRepository()
    sub_repo = SubstationRepository()
    dist_repo = DistributionSubstationRepository()

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
                "id": row.get("Id"),
                "naziv": row.get("Name") or "Nepoznato",
                "tip": "transmission",
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
                "id": row.get("Id"),
                "naziv": row.get("Name") or "Nepoznato",
                "tip": "substation",
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
                "id": row.get("Id"),
                "naziv": row.get("Name") or "Nepoznato",
                "tip": "distribution",
            },
        })

    return jsonify({
        "type": "FeatureCollection",
        "features": features,
    })


@app.route("/data/vodovi")
def data_vodovi():
    """
    Hijerarhija po slici:

    F33:
      TS -> SS
      TS -> DT (direktno)  [dozvoljeno]

    F11:
      SS -> DT             [standard]
      TS -> DT             [trade F11]

    Geometrija je i dalje vizuelna aproksimacija jer nemamo stvarnu GIS trasu.
    Veze između čvorova su stvarne iz baze.
    """
    trans_repo = TransmissionStationRepository()
    sub_repo = SubstationRepository()
    dist_repo = DistributionSubstationRepository()
    feeder11_repo = Feeder11Repository()
    feeder33_repo = Feeder33Repository()
    feeder33_sub_repo = Feeder33SubstationRepository()

    transmission_rows = trans_repo.get_all(limit=None)
    substation_rows = sub_repo.get_all(limit=None)
    distribution_rows = dist_repo.get_all(limit=None)
    feeder11_rows = feeder11_repo.get_all(limit=None)
    feeder33_rows = feeder33_repo.get_all(limit=None)
    feeder33_sub_rows = feeder33_sub_repo.get_all(limit=None)

    transmission_by_id = {row["Id"]: row for row in transmission_rows}
    substation_by_id = {row["Id"]: row for row in substation_rows}
    distribution_by_id = {row["Id"]: row for row in distribution_rows}

    feeder33_to_substations = {}
    for row in feeder33_sub_rows:
        feeder_id = row.get("Feeders33Id")
        sub_id = row.get("SubstationsId")
        if feeder_id is None or sub_id is None:
            continue
        feeder33_to_substations.setdefault(feeder_id, []).append(sub_id)

    feeder11_to_distributions = {}
    feeder33_to_distributions = {}

    for row in distribution_rows:
        dist_id = row.get("Id")
        feeder11_id = row.get("Feeder11Id")
        feeder33_id = row.get("Feeder33Id")

        if feeder11_id is not None:
            feeder11_to_distributions.setdefault(feeder11_id, []).append(dist_id)

        if feeder33_id is not None:
            feeder33_to_distributions.setdefault(feeder33_id, []).append(dist_id)

    features = []

    # ---------------------------
    # FEEDERS 33
    # Hijerarhija:
    # TS -> SS -> (eventualno kasnije direktni DT)
    # ---------------------------
    for feeder in feeder33_rows:
        feeder_id = feeder.get("Id")
        feeder_name = feeder.get("Name") or f"Feeder33 #{feeder_id}"
        ts_id = feeder.get("TsId")

        source_row = transmission_by_id.get(ts_id)
        if not source_row:
            continue

        source_coord = _to_coord(source_row)
        if not source_coord:
            continue

        substation_coords = []
        distribution_coords = []
        station_keys = []

        if ts_id is not None:
            station_keys.append(f"transmission:{ts_id}")

        # 1) prvo SS
        for sub_id in feeder33_to_substations.get(feeder_id, []):
            sub_row = substation_by_id.get(sub_id)
            if not sub_row:
                continue
            coord = _to_coord(sub_row)
            if coord:
                substation_coords.append(coord)
                station_keys.append(f"substation:{sub_id}")

        # 2) onda eventualni direktni DT na F33
        for dist_id in feeder33_to_distributions.get(feeder_id, []):
            dist_row = distribution_by_id.get(dist_id)
            if not dist_row:
                continue
            coord = _to_coord(dist_row)
            if coord:
                distribution_coords.append(coord)
                station_keys.append(f"distribution:{dist_id}")

        ordered_substations = _order_by_nearest(source_coord, substation_coords)

        last_coord = source_coord
        if ordered_substations:
            last_coord = ordered_substations[-1]

        ordered_distributions = _order_by_nearest(last_coord, distribution_coords)

        coords = [source_coord] + ordered_substations + ordered_distributions

        if len(coords) < 2:
            continue

        features.append({
            "type": "Feature",
            "geometry": {
                "type": "LineString",
                "coordinates": coords,
            },
            "properties": {
                "id": feeder_id,
                "naziv": feeder_name,
                "tip": "feeder33",
                "source_type": "transmission",
                "source_id": ts_id,
                "children_count": len(substation_coords) + len(distribution_coords),
                "nameplate_rating": feeder.get("NameplateRating"),
                "meter_id": feeder.get("MeterId"),
                "station_keys": station_keys,
            },
        })

    # ---------------------------
    # FEEDERS 11
    # Hijerarhija:
    # standardno: SS -> DT
    # trade F11: TS -> DT
    # ---------------------------
    for feeder in feeder11_rows:
        feeder_id = feeder.get("Id")
        feeder_name = feeder.get("Name") or f"Feeder11 #{feeder_id}"

        ss_id = feeder.get("SsId")
        ts_id = feeder.get("TsId")

        source_row = None
        source_coord = None
        source_type = None
        source_id = None
        station_keys = []

        # PRIORITET:
        # ako ima SS -> standardni F11
        # ako nema SS, a ima TS -> trade F11
        if ss_id is not None and ss_id in substation_by_id:
            source_row = substation_by_id.get(ss_id)
            source_coord = _to_coord(source_row)
            source_type = "substation"
            source_id = ss_id
            station_keys.append(f"substation:{ss_id}")

        elif ts_id is not None and ts_id in transmission_by_id:
            source_row = transmission_by_id.get(ts_id)
            source_coord = _to_coord(source_row)
            source_type = "transmission"
            source_id = ts_id
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
            "geometry": {
                "type": "LineString",
                "coordinates": coords,
            },
            "properties": {
                "id": feeder_id,
                "naziv": feeder_name,
                "tip": "feeder11",
                "source_type": source_type,
                "source_id": source_id,
                "children_count": len(distribution_coords),
                "nameplate_rating": feeder.get("NameplateRating"),
                "meter_id": feeder.get("MeterId"),
                "parent_feeder33_id": feeder.get("Feeder33Id"),
                "ts_id": ts_id,
                "ss_id": ss_id,
                "station_keys": station_keys,
            },
        })

    return jsonify({
        "type": "FeatureCollection",
        "features": features,
    })


if __name__ == "__main__":
    app.run(debug=True)