"""
analysis/network_analysis.py
Analiza gubitaka u distributivnoj mreži.
Gubici > 10% ne mogu biti tehnički — sumnja na krađu.
"""

import sys
import os
from dataclasses import dataclass

import pandas as pd
import numpy as np

sys.path.append(os.path.join(os.path.dirname(__file__), ".."))
from get_data.db import q

# ─────────────────────────────────────────────
# KONSTANTE
# ─────────────────────────────────────────────

SCALE       = 100.0
COS_PHI     = 0.9
DT_INTERVAL = 0.5   # sati između mjerenja (30 min)

CID_V = {"a": 6, "b": 7, "c": 8}
CID_I = {"a": 9, "b": 10, "c": 11}

# Granice gubitaka u %
# Nova mreža (Nigerija) — niski tehnički gubici, visoki komercijalni rizik
PRAG_NORMALNO   = 5.0   # tehnički gubici u normi za novu infrastrukturu
PRAG_UPOZORENJE = 10.0  # iznad: sumnja na krađu (ne može biti tehničko)
PRAG_KRITICNO   = 20.0  # kritična krađa

# Pokrivenost DT-ova za ekstrapolaciju
COVERAGE_MIN  = 0.30   # ispod: NEPOZNATO (prenisko za procjenu)
COVERAGE_FULL = 0.80   # iznad: puni pragovi (visoka pouzdanost)

V_NORMALAN_MIN = 190.0
V_NORMALAN_MAX = 250.0
V_PREKID       = 10.0


# ─────────────────────────────────────────────
# REZULTAT
# ─────────────────────────────────────────────

@dataclass
class RezultatAnalize:
    dt:    pd.DataFrame
    f11:   pd.DataFrame
    f33:   pd.DataFrame
    alarmi: pd.DataFrame


# ─────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────

def chunk(lst, size=1000):
    for i in range(0, len(lst), size):
        yield lst[i:i + size]


_V_CIDS = set(CID_V.values())   # {6, 7, 8}
_I_CIDS = set(CID_I.values())   # {9, 10, 11}
_VI_CIDS = _V_CIDS | _I_CIDS


def _energy_all(df: pd.DataFrame) -> dict:
    """Vektorizovano: kWh i prosječni napon za sve Mid-ove odjednom."""
    if df.empty:
        return {}

    sub = df[df["Cid"].isin(_VI_CIDS)]
    if sub.empty:
        return {}

    pivot = sub.pivot_table(
        index=["Mid", "Ts"], columns="Cid", values="Val", aggfunc="mean"
    )

    kwh = pd.Series(0.0, index=pivot.index.get_level_values("Mid").unique())
    for ph in ("a", "b", "c"):
        cv, ci = CID_V[ph], CID_I[ph]
        if cv in pivot.columns and ci in pivot.columns:
            p = (pivot[cv] / SCALE) * (pivot[ci] / SCALE) * COS_PHI / 1000.0
            kwh = kwh.add(p.groupby(level="Mid").sum() * DT_INTERVAL, fill_value=0.0)
    kwh = kwh.clip(lower=0)

    v_df = df[df["Cid"].isin(_V_CIDS)].copy()
    v_df = v_df.assign(v=v_df["Val"] / SCALE)
    v_df = v_df[(v_df["v"] >= V_PREKID) & (v_df["v"] <= 350)]
    v_avg = v_df.groupby("Mid")["v"].mean()

    result: dict = {}
    for mid in kwh.index:
        result[int(mid)] = {
            "kwh": float(kwh[mid]),
            "v":   float(v_avg[mid]) if mid in v_avg.index else None,
        }
    for mid in v_avg.index:
        if int(mid) not in result:
            result[int(mid)] = {"kwh": 0.0, "v": float(v_avg[mid])}
    return result


def _loss(ulaz: float, izlaz: float):
    """Gubici u % = (ulaz - izlaz) / ulaz * 100."""
    if ulaz <= 0:
        return None
    return round((ulaz - izlaz) / ulaz * 100, 2)


# ─────────────────────────────────────────────
# KLASIFIKACIJA
# ─────────────────────────────────────────────

def classify(gubitak_pct, napon, kwh_ulaz, kwh_izlaz):
    """
    Klasifikuj stanje voda na osnovu gubitaka.
    Iznad 10% gubici ne mogu biti tehnički — sumnja na krađu.
    """
    if gubitak_pct is None:
        return dict(status="NEPOZNATO", tip="Nema podataka", boja="#888888")

    if napon is not None and napon < V_PREKID and kwh_izlaz < 0.001:
        return dict(status="ALARM", tip="Prekid napajanja", boja="#000000")

    if gubitak_pct < 0:
        return dict(status="UPOZORENJE", tip="Negativni gubici — greška mjerenja", boja="#888888")

    if gubitak_pct < PRAG_NORMALNO:
        return dict(status="NORMALNO", tip="Tehnički gubici u normi", boja="#1D9E75")

    if gubitak_pct < PRAG_UPOZORENJE:
        return dict(status="UPOZORENJE", tip="Povišeni gubici — provjeri", boja="#EF9F27")

    if gubitak_pct < PRAG_KRITICNO:
        if napon is not None and napon < V_NORMALAN_MIN:
            return dict(status="KRITIČNO", tip="Oštećenje voda (nizak napon)", boja="#D85A30")
        return dict(status="KRITIČNO", tip="Sumnja na krađu struje", boja="#D85A30")

    return dict(status="ALARM", tip="Kritična krađa struje", boja="#A32D2D")


# ─────────────────────────────────────────────
# MAIN CLASS
# ─────────────────────────────────────────────

class NetworkAnalysis:

    def __init__(self):
        self.top = self._load_topology()

    def _load_topology(self):
        return {
            "dt":  q("SELECT Id, Name, MeterId, Feeder11Id, Feeder33Id FROM DistributionSubstation WHERE MeterId IS NOT NULL"),
            "f11": q("SELECT Id, Name, MeterId, SsId, Feeder33Id FROM Feeders11 WHERE MeterId IS NOT NULL"),
            "f33": q("SELECT Id, Name, MeterId, TsId FROM Feeders33 WHERE MeterId IS NOT NULL"),
            "ss":  q("SELECT Id, Name FROM Substations"),
            "ts":  q("SELECT Id, Name FROM TransmissionStations"),
        }

    # ─────────────────────────────
    # UČITAJ MJERENJA
    # ─────────────────────────────

    def _load_measurements(self, meter_ids, hours):
        if not meter_ids:
            return pd.DataFrame()

        # Koristimo MAX(Ts) kao referentu tačku — podaci možda nisu "live"
        ref = q("SELECT MAX(Ts) as t FROM MeterReads").iloc[0]["t"]
        print(f"  Referentno vrijeme: {ref}")

        dfs = []
        for part in chunk(list(set(meter_ids)), 200):
            ids = ",".join(map(str, part))
            dfs.append(q(f"""
                SELECT Mid, Cid, Val, Ts
                FROM MeterReads
                WHERE Mid IN ({ids})
                  AND Ts > DATEADD(HOUR, -{hours}, '{ref}')
            """))

        return pd.concat(dfs, ignore_index=True) if dfs else pd.DataFrame()

    # ─────────────────────────────
    # DT — status napona
    # ─────────────────────────────

    def _dt(self, e: dict):
        dt = self.top["dt"]

        res = []
        for _, r in dt.iterrows():
            mid = int(r["MeterId"])
            x = e.get(mid, {"kwh": 0, "v": None})
            v = x["v"]

            if v is None:
                status, tip, boja = "NEPOZNATO", "Nema mjerenja", "#888888"
            elif v < V_PREKID:
                status, tip, boja = "ALARM", "Prekid napajanja", "#000000"
            elif v < V_NORMALAN_MIN:
                status, tip, boja = "UPOZORENJE", "Nizak napon", "#EF9F27"
            else:
                status, tip, boja = "NORMALNO", "OK", "#1D9E75"

            res.append({
                "dt_id":     r["Id"],
                "naziv":     r["Name"],
                "feeder11":  r["Feeder11Id"],
                "kwh":       x["kwh"],
                "v":         v,
                "status":    status,
                "tip":       tip,
                "boja":      boja,
            })

        return pd.DataFrame(res)

    # ─────────────────────────────
    # F11 — gubici po 11kV vodu
    # Ulaz:  F11 mjerač na izlazu iz SS
    # Izlaz: suma svih DT-ova na tom vodu
    # ─────────────────────────────

    def _f11(self, e: dict, dt):
        f11 = self.top["f11"]

        # Pokrivenost: koliko DT-ova ima mjerenja vs ukupno
        dt_top = self.top["dt"][["Id", "Feeder11Id"]].copy()
        dt_top = dt_top.merge(
            dt[["dt_id", "kwh"]].rename(columns={"dt_id": "Id"}),
            on="Id", how="left"
        )
        dt_top["izmjereno"] = (dt_top["kwh"].fillna(0) > 0).astype(int)

        dt_ukupno  = dt_top.groupby("Feeder11Id")["Id"].count()
        dt_izmjer  = dt_top.groupby("Feeder11Id")["izmjereno"].sum()
        dt_kwh_sum = dt_top.groupby("Feeder11Id")["kwh"].sum()

        res = []
        for _, r in f11.iterrows():
            f11_id = r["Id"]
            f11_mid = int(r["MeterId"])

            ulaz = e.get(f11_mid, {"kwh": 0, "v": None})
            kwh_u = ulaz["kwh"]

            kwh_i     = float(dt_kwh_sum.get(f11_id, 0.0))
            n_ukupno  = int(dt_ukupno.get(f11_id, 0))
            n_izmjer  = int(dt_izmjer.get(f11_id, 0))

            coverage = n_izmjer / n_ukupno if n_ukupno > 0 else 0.0

            if n_izmjer == 0 or coverage < COVERAGE_MIN:
                res.append({
                    "f11_id": f11_id, "naziv": r["Name"],
                    "kwh_u": round(kwh_u, 2), "kwh_i": 0.0,
                    "loss": None, "n_dt": n_ukupno, "n_dt_ok": n_izmjer,
                    "status": "NEPOZNATO",
                    "tip": f"Premalo podataka: {n_izmjer}/{n_ukupno} DT",
                    "boja": "#888888",
                })
                continue

            # Ekstrapolacija: skaliramo izmjereni izlaz na cijeli vod
            kwh_i_est = kwh_i / coverage
            gubitak = _loss(kwh_u, kwh_i_est)

            if coverage < COVERAGE_FULL:
                # Djelimična pokrivenost — procjena, pragovi uvećani 1.5×
                if gubitak is None:
                    c = dict(status="NEPOZNATO", tip="Nema ulaznih podataka", boja="#888888")
                elif gubitak < 0:
                    c = dict(status="UPOZORENJE", tip=f"Negativni gubici — greška mjerenja (procj. {n_izmjer}/{n_ukupno})", boja="#888888")
                elif gubitak < PRAG_NORMALNO:
                    c = dict(status="NORMALNO", tip=f"Tehnički gubici — procjena {n_izmjer}/{n_ukupno} DT", boja="#1D9E75")
                elif gubitak < PRAG_UPOZORENJE * 1.5:
                    c = dict(status="UPOZORENJE", tip=f"Povišeni gubici — procjena {n_izmjer}/{n_ukupno} DT", boja="#EF9F27")
                elif gubitak < PRAG_KRITICNO * 1.5:
                    c = dict(status="KRITIČNO", tip=f"Sumnja na krađu — procjena {n_izmjer}/{n_ukupno} DT", boja="#D85A30")
                else:
                    c = dict(status="ALARM", tip=f"Kritična krađa — procjena {n_izmjer}/{n_ukupno} DT", boja="#A32D2D")
            else:
                c = classify(gubitak, ulaz["v"], kwh_u, kwh_i_est)

            kwh_i = kwh_i_est  # dalje koristimo procijenjenu vrijednost

            res.append({
                "f11_id":  f11_id,
                "naziv":   r["Name"],
                "kwh_u":   round(kwh_u, 2),
                "kwh_i":   round(kwh_i, 2),
                "loss":    gubitak,
                "n_dt":    n_ukupno,
                "n_dt_ok": n_izmjer,
                **c,
            })

        return pd.DataFrame(res)

    # ─────────────────────────────
    # F33 — gubici po 33kV vodu
    # Ulaz:  F33 mjerač
    # Izlaz: suma F11 mjerača koji idu s tog F33
    # ─────────────────────────────

    def _f33(self, e: dict, f11):
        f33 = self.top["f33"]

        # f11_id → Feeder33Id, pa suma kwh_u po f33
        # Povežemo s topologijom da dobijemo Feeder33Id
        f11_top = self.top["f11"][["Id", "Feeder33Id"]].copy()
        f11_top.columns = ["f11_id", "feeder33_id"]
        f11_merged = f11.merge(f11_top, on="f11_id", how="left")
        f11_sum_by_f33 = f11_merged.groupby("feeder33_id")["kwh_u"].sum()

        res = []
        for _, r in f33.iterrows():
            f33_id = r["Id"]
            f33_mid = int(r["MeterId"])

            ulaz = e.get(f33_mid, {"kwh": 0, "v": None})
            kwh_u = ulaz["kwh"]

            # Suma F11 ulaza koji idu s ovog F33
            kwh_i = float(f11_sum_by_f33.get(f33_id, 0.0))

            gubitak = _loss(kwh_u, kwh_i)
            c = classify(gubitak, ulaz["v"], kwh_u, kwh_i)

            res.append({
                "f33_id": f33_id,
                "naziv":  r["Name"],
                "kwh_u":  round(kwh_u, 2),
                "kwh_i":  round(kwh_i, 2),
                "loss":   gubitak,
                **c,
            })

        return pd.DataFrame(res)

    # ─────────────────────────────
    # POKRETANJE ANALIZE
    # ─────────────────────────────

    def analiziraj(self, hours=24):
        mids = []
        for k in self.top:
            if "MeterId" in self.top[k].columns:
                mids += self.top[k]["MeterId"].dropna().astype(int).tolist()
        mids = list(set(mids))

        print(f"Učitavam mjerenja za {len(mids)} brojača ({hours}h)...")
        df = self._load_measurements(mids, hours)
        print(f"  → {len(df)} redova učitano.")

        print("Računam energiju po brojaču...")
        e = _energy_all(df)
        print(f"  → {len(e)} brojača s podacima.")

        print("Analiziram DT-ove...")
        dt = self._dt(e)

        print("Analiziram F11 vodove (11kV)...")
        f11 = self._f11(e, dt)

        print("Analiziram F33 vodove (33kV)...")
        f33 = self._f33(e, f11)

        alarmi = pd.concat([
            f11[f11["status"].isin(["KRITIČNO", "ALARM"])].assign(nivo="F11"),
            f33[f33["status"].isin(["KRITIČNO", "ALARM"])].assign(nivo="F33"),
        ], ignore_index=True)

        return RezultatAnalize(dt, f11, f33, alarmi)


# ─────────────────────────────
# CLI
# ─────────────────────────────

if __name__ == "__main__":
    na = NetworkAnalysis()

    # ── Dijagnostika: koje CID-ove imaju F11 i DT mjerači? ──
    ref = q("SELECT MAX(Ts) as t FROM MeterReads").iloc[0]["t"]

    def _diag(mids, label):
        print(f"\n=== DIJAGNOSTIKA {label} ===")
        ids = ",".join(map(str, mids[:50]))
        d = q(f"""
            SELECT Mid, Cid, COUNT(*) as n, AVG(CAST(Val AS FLOAT)) as avg_val
            FROM MeterReads
            WHERE Mid IN ({ids})
              AND Ts > DATEADD(HOUR, -24, '{ref}')
            GROUP BY Mid, Cid
            ORDER BY Mid, Cid
        """)
        if d.empty:
            print("  UPOZORENJE: Nema podataka!")
            return
        # Pivot: za svaki Mid prikaži koje CID-ove ima
        cid_by_mid = d.groupby("Mid")["Cid"].apply(set)
        missing_i = [mid for mid, cids in cid_by_mid.items() if not {9,10,11} & cids]
        has_both  = [mid for mid, cids in cid_by_mid.items() if {9,10,11} & cids and {6,7,8} & cids]
        print(f"  Ima V+I (može računati kWh): {len(has_both)} mjerača")
        print(f"  Nema struje (CID 9/10/11):   {len(missing_i)} mjerača — {missing_i}")
        print(d[["Mid","Cid","n","avg_val"]].to_string(index=False))

    f11_mids = na.top["f11"]["MeterId"].dropna().astype(int).tolist()
    dt_mids  = na.top["dt"]["MeterId"].dropna().astype(int).tolist()
    _diag(f11_mids, "F11 MJERAČA")
    _diag(dt_mids,  "DT MJERAČA")
    print()

    r = na.analiziraj(24)

    print("\n=== F11 GUBICI ===")
    print(r.f11[["naziv", "kwh_u", "kwh_i", "loss", "n_dt", "n_dt_ok", "status", "tip"]].to_string(index=False))

    print("\n=== F33 GUBICI ===")
    print(r.f33[["naziv", "kwh_u", "kwh_i", "loss", "status", "tip"]].to_string(index=False))

    print(f"\n=== ALARMI ({len(r.alarmi)}) ===")
    if r.alarmi.empty:
        print("Nema alarma.")
    else:
        print(r.alarmi[["nivo", "naziv", "loss", "status", "tip"]].to_string(index=False))
