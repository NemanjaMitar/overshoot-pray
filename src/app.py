from flask import Flask, render_template, jsonify
import os

from database.repositories import (
    DistributionSubstationRepository,
    SubstationRepository,
    TransmissionStationRepository,
)

app = Flask(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/data/trafostanice")
def data_trafostanice():
    """
    Vraća sve stanice (transmission, substations, distribution) kao jedan GeoJSON,
    u formatu koji je koleginica koristila: FeatureCollection sa properties.tip.
    """
    trans_repo = TransmissionStationRepository()
    sub_repo = SubstationRepository()
    dist_repo = DistributionSubstationRepository()

    features = []

    # Transmission (tip = 'transmission')
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
                "naziv": row["Name"] or "Nepoznato",
                "tip": "transmission",
            },
        })

    # Substations (tip = 'substation')
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
                "naziv": row["Name"] or "Nepoznato",
                "tip": "substation",
            },
        })

    # Distribution (tip = 'distribution')
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
                "naziv": row["Name"] or "Nepoznato",
                "tip": "distribution",
            },
        })

    return jsonify({
        "type": "FeatureCollection",
        "features": features,
    })


if __name__ == "__main__":
    app.run(debug=True)