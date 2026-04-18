"""
analysis/theft_detection.py

Detection of Non-Technical Losses (NTL) in a distribution network.
Silent library — no print statements outside __main__.

Network hierarchy:
  TransmissionStation → Feeders33 → Feeders11 → DistributionSubstation (DT) → Customers
"""

import sys
import os
from dataclasses import dataclass
from typing import Optional

import pandas as pd

sys.path.append(os.path.join(os.path.dirname(__file__), ".."))
from get_data.db import q


# ─────────────────────────────────────────────
# CONSTANTS
# ─────────────────────────────────────────────

ANALYSIS_HOURS = 168

COS_PHI        = 0.9
TFES_WH_TO_KWH = 1000.0

THRESHOLD_TECHNICAL = 8.0
THRESHOLD_NTL       = 15.0
THRESHOLD_NTL_HIGH  = 30.0
THRESHOLD_NTL_ALARM = 50.0

MIN_COVERAGE_F11 = 0.30
MIN_COVERAGE_F33 = 0.30
MIN_KWH_FEEDER   = 10.0
MIN_TFES_HOURS   = 8.0

LOAD_FACTOR_SUSPICIOUS = 0.02
MAX_SCALE_FACTOR       = 3.0

FALLBACK_KVA_DT  =   500.0
FALLBACK_KVA_F11 =  5000.0
FALLBACK_KVA_F33 = 40000.0

NTL_STATUSES = {"NTL", "NTL_HIGH", "NTL_ALARM"}


# ─────────────────────────────────────────────
# DATA TYPES
# ─────────────────────────────────────────────

@dataclass
class DtProfile:
    dt_id:       int
    name:        str
    feeder11_id: int
    kwh:         float
    load_factor: float
    nameplate:   float
    lat:         Optional[float]
    lon:         Optional[float]
    suspicious:  bool


@dataclass
class F11Result:
    f11_id:         int
    name:           str
    f33_id:         Optional[int]
    input_kwh:      float
    output_kwh:     float
    output_kwh_est: float
    loss_kwh:       float
    loss_pct:       Optional[float]
    coverage:       float
    dt_count:       int
    dt_measured:    int
    dt_suspicious:  int
    status:         str
    confidence:     str
    dt_profiles:    list


@dataclass
class F33Result:
    f33_id:         int
    name:           str
    input_kwh:      float
    output_kwh:     float
    output_kwh_est: float
    loss_kwh:       float
    loss_pct:       Optional[float]
    coverage:       float
    f11_count:      int
    f11_measured:   int
    status:         str
    confidence:     str


@dataclass
class TheftReport:
    f11_feeders:         pd.DataFrame
    f33_feeders:         pd.DataFrame
    suspicious_dts:      pd.DataFrame
    f11_total_input_kwh: float
    f11_total_loss_kwh:  float
    f11_total_ntl_kwh:   float
    f33_total_input_kwh: float
    f33_total_loss_kwh:  float
    f33_total_ntl_kwh:   float


# ─────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────

def chunk(lst, size=500):
    for i in range(0, len(lst), size):
        yield lst[i:i + size]


def _nameplate_max_kwh(kva: float, hours: float) -> float:
    if kva <= 0:
        return float("inf")
    return kva * COS_PHI * hours


def _tfes_delta(meter_ids: list, hours: int) -> dict:
    if not meter_ids:
        return {}
    result = {}

    for part in chunk(meter_ids):
        ids = ",".join(map(str, part))
        df = q(f"""
            WITH last AS (
                SELECT Mid, MAX(Ts) AS last_ts
                FROM MeterReadTfes
                WHERE Mid IN ({ids})
                GROUP BY Mid
            ),
            win AS (
                SELECT t.Mid,
                       MIN(t.Ts)  AS min_ts,
                       MAX(t.Ts)  AS max_ts,
                       COUNT(*)   AS n_readings,
                       MAX(CAST(t.Val AS FLOAT)) - MIN(CAST(t.Val AS FLOAT)) AS delta_wh
                FROM MeterReadTfes t
                JOIN last l ON t.Mid = l.Mid
                WHERE t.Ts > DATEADD(HOUR, -{hours}, l.last_ts)
                GROUP BY t.Mid
            )
            SELECT Mid, delta_wh, min_ts, max_ts, n_readings
            FROM win
            WHERE delta_wh IS NOT NULL AND delta_wh > 0
        """)

        for _, row in df.iterrows():
            mid      = int(row["Mid"])
            delta_wh = float(row["delta_wh"])
            min_ts, max_ts = row["min_ts"], row["max_ts"]

            if pd.isna(min_ts) or pd.isna(max_ts):
                continue

            real_hours = max((max_ts - min_ts).total_seconds() / 3600.0, 0.5)
            if real_hours < MIN_TFES_HOURS:
                continue

            factor = min(hours / real_hours, MAX_SCALE_FACTOR)
            result[mid] = {
                "kwh":        (delta_wh / TFES_WH_TO_KWH) * factor,
                "real_hours": real_hours,
                "n_readings": int(row["n_readings"]),
            }

    return result


def _classify(loss_pct: Optional[float], coverage: float) -> tuple[str, str]:
    confidence = "HIGH" if coverage >= 0.80 else "MEDIUM" if coverage >= 0.50 else "LOW"
    if loss_pct is None:               return "UNKNOWN",           confidence
    if loss_pct < 0:                   return "MEASUREMENT_ERROR", confidence
    if loss_pct < THRESHOLD_TECHNICAL: return "NORMAL",            confidence
    if loss_pct < THRESHOLD_NTL:       return "WARNING",           confidence
    if loss_pct < THRESHOLD_NTL_HIGH:  return "NTL",               confidence
    if loss_pct < THRESHOLD_NTL_ALARM: return "NTL_HIGH",          confidence
    return "NTL_ALARM", confidence


# ─────────────────────────────────────────────
# MAIN CLASS
# ─────────────────────────────────────────────

class TheftDetector:

    def __init__(self):
        self.top = self._load_topology()

    def _load_topology(self) -> dict:
        return {
            "f33": q("""
                SELECT Id, Name, MeterId, TsId,
                       ISNULL(NameplateRating, 0) AS NameplateRating
                FROM Feeders33 WHERE MeterId IS NOT NULL
            """),
            "f11": q("""
                SELECT Id, Name, MeterId, Feeder33Id,
                       ISNULL(NameplateRating, 0) AS NameplateRating
                FROM Feeders11 WHERE MeterId IS NOT NULL
            """),
            "dt": q("""
                SELECT Id, Name, MeterId, Feeder11Id,
                       ISNULL(NameplateRating, 0) AS NameplateRating,
                       Latitude, Longitude
                FROM DistributionSubstation WHERE MeterId IS NOT NULL
            """),
        }

    def _all_meter_ids(self) -> list:
        mids = []
        for tbl in self.top.values():
            if "MeterId" in tbl.columns:
                mids += tbl["MeterId"].dropna().astype(int).tolist()
        return list(set(mids))

    def _build_energy(self, tfes: dict, hours: float) -> dict[int, float]:
        cap: dict[int, float] = {}
        for level, fallback_kva in [
            ("dt",  FALLBACK_KVA_DT),
            ("f11", FALLBACK_KVA_F11),
            ("f33", FALLBACK_KVA_F33),
        ]:
            for _, row in self.top[level].iterrows():
                mid = int(row["MeterId"])
                kva = float(row["NameplateRating"]) if row["NameplateRating"] > 0 else fallback_kva
                max_kwh = _nameplate_max_kwh(kva, hours)
                if mid not in cap or max_kwh < cap[mid]:
                    cap[mid] = max_kwh

        return {
            mid: val["kwh"]
            for mid, val in tfes.items()
            if val["kwh"] <= cap.get(mid, float("inf"))
        }

    def _analyse_f11(self, energy: dict) -> list[F11Result]:
        dt_tbl  = self.top["dt"]
        f11_tbl = self.top["f11"]
        results = []

        for _, f11_row in f11_tbl.iterrows():
            f11_id    = int(f11_row["Id"])
            f11_mid   = int(f11_row["MeterId"])
            input_kwh = energy.get(f11_mid, 0.0)
            f33_id    = int(f11_row["Feeder33Id"]) if pd.notna(f11_row.get("Feeder33Id")) else None

            dts = dt_tbl[dt_tbl["Feeder11Id"] == f11_id]
            dt_profiles, output_sum, n_ok = [], 0.0, 0

            for _, dt_row in dts.iterrows():
                dt_mid = int(dt_row["MeterId"])
                dt_kva = float(dt_row["NameplateRating"]) if dt_row["NameplateRating"] > 0 else FALLBACK_KVA_DT
                dt_kwh = energy.get(dt_mid, 0.0)

                max_dt_kwh  = _nameplate_max_kwh(dt_kva, ANALYSIS_HOURS)
                load_factor = dt_kwh / max_dt_kwh if max_dt_kwh > 0 else 0.0
                suspicious  = (dt_mid in energy and dt_kwh > 0
                               and dt_kwh < max_dt_kwh * LOAD_FACTOR_SUSPICIOUS)

                if dt_mid in energy and dt_kwh > 0:
                    output_sum += dt_kwh
                    n_ok += 1

                dt_profiles.append(DtProfile(
                    dt_id=int(dt_row["Id"]), name=dt_row["Name"],
                    feeder11_id=f11_id, kwh=dt_kwh,
                    load_factor=round(load_factor, 4), nameplate=dt_kva,
                    lat=dt_row.get("Latitude"), lon=dt_row.get("Longitude"),
                    suspicious=suspicious,
                ))

            n_total  = len(dts)
            coverage = n_ok / n_total if n_total > 0 else 0.0
            n_susp   = sum(1 for d in dt_profiles if d.suspicious)

            if input_kwh < MIN_KWH_FEEDER:
                results.append(F11Result(
                    f11_id=f11_id, name=f11_row["Name"], f33_id=f33_id,
                    input_kwh=input_kwh, output_kwh=output_sum,
                    output_kwh_est=0.0, loss_kwh=0.0, loss_pct=None,
                    coverage=coverage, dt_count=n_total, dt_measured=n_ok,
                    dt_suspicious=n_susp, status="INVALID_INPUT_METER",
                    confidence="LOW", dt_profiles=dt_profiles,
                ))
                continue

            if coverage < MIN_COVERAGE_F11:
                results.append(F11Result(
                    f11_id=f11_id, name=f11_row["Name"], f33_id=f33_id,
                    input_kwh=input_kwh, output_kwh=output_sum,
                    output_kwh_est=0.0, loss_kwh=0.0, loss_pct=None,
                    coverage=coverage, dt_count=n_total, dt_measured=n_ok,
                    dt_suspicious=n_susp, status="INSUFFICIENT_DATA",
                    confidence="LOW", dt_profiles=dt_profiles,
                ))
                continue

            output_kwh_est = output_sum / coverage
            loss_kwh = max(input_kwh - output_kwh_est, 0.0)
            loss_pct = round((input_kwh - output_kwh_est) / input_kwh * 100, 2)
            status, confidence = _classify(loss_pct, coverage)

            results.append(F11Result(
                f11_id=f11_id, name=f11_row["Name"], f33_id=f33_id,
                input_kwh=round(input_kwh, 1), output_kwh=round(output_sum, 1),
                output_kwh_est=round(output_kwh_est, 1),
                loss_kwh=round(loss_kwh, 1), loss_pct=loss_pct,
                coverage=round(coverage, 3), dt_count=n_total, dt_measured=n_ok,
                dt_suspicious=n_susp, status=status, confidence=confidence,
                dt_profiles=dt_profiles,
            ))

        return results

    def _analyse_f33(self, energy: dict, f11_results: list[F11Result]) -> list[F33Result]:
        f33_tbl = self.top["f33"]

        f11_by_f33: dict[int, list[F11Result]] = {}
        for r in f11_results:
            if r.f33_id is not None:
                f11_by_f33.setdefault(r.f33_id, []).append(r)

        results = []

        for _, f33_row in f33_tbl.iterrows():
            f33_id    = int(f33_row["Id"])
            f33_mid   = int(f33_row["MeterId"])
            input_kwh = energy.get(f33_mid, 0.0)

            downstream = f11_by_f33.get(f33_id, [])
            n_total    = len(downstream)
            measured   = [r for r in downstream if r.input_kwh >= MIN_KWH_FEEDER]
            n_ok       = len(measured)
            coverage   = n_ok / n_total if n_total > 0 else 0.0
            output_sum = sum(r.input_kwh for r in measured)

            if input_kwh < MIN_KWH_FEEDER:
                results.append(F33Result(
                    f33_id=f33_id, name=f33_row["Name"],
                    input_kwh=input_kwh, output_kwh=output_sum,
                    output_kwh_est=0.0, loss_kwh=0.0, loss_pct=None,
                    coverage=coverage, f11_count=n_total, f11_measured=n_ok,
                    status="INVALID_INPUT_METER", confidence="LOW",
                ))
                continue

            if coverage < MIN_COVERAGE_F33:
                results.append(F33Result(
                    f33_id=f33_id, name=f33_row["Name"],
                    input_kwh=input_kwh, output_kwh=output_sum,
                    output_kwh_est=0.0, loss_kwh=0.0, loss_pct=None,
                    coverage=coverage, f11_count=n_total, f11_measured=n_ok,
                    status="INSUFFICIENT_DATA", confidence="LOW",
                ))
                continue

            output_kwh_est = output_sum / coverage
            loss_kwh = max(input_kwh - output_kwh_est, 0.0)
            loss_pct = round((input_kwh - output_kwh_est) / input_kwh * 100, 2)
            status, confidence = _classify(loss_pct, coverage)

            results.append(F33Result(
                f33_id=f33_id, name=f33_row["Name"],
                input_kwh=round(input_kwh, 1), output_kwh=round(output_sum, 1),
                output_kwh_est=round(output_kwh_est, 1),
                loss_kwh=round(loss_kwh, 1), loss_pct=loss_pct,
                coverage=round(coverage, 3), f11_count=n_total, f11_measured=n_ok,
                status=status, confidence=confidence,
            ))

        return results

    def analyse(self, hours: int = ANALYSIS_HOURS) -> TheftReport:
        mids   = self._all_meter_ids()
        tfes   = _tfes_delta(mids, hours)
        energy = self._build_energy(tfes, float(hours))

        f11_results = self._analyse_f11(energy)
        f33_results = self._analyse_f33(energy, f11_results)

        f11_df = pd.DataFrame([{
            "f11_id":         r.f11_id,
            "feeder_name":    r.name,
            "f33_id":         r.f33_id,
            "input_kwh":      r.input_kwh,
            "output_kwh_est": r.output_kwh_est,
            "loss_kwh":       r.loss_kwh,
            "loss_pct":       r.loss_pct,
            "coverage":       r.coverage,
            "dt_count":       r.dt_count,
            "dt_measured":    r.dt_measured,
            "dt_suspicious":  r.dt_suspicious,
            "status":         r.status,
            "confidence":     r.confidence,
        } for r in f11_results]).sort_values("loss_kwh", ascending=False).reset_index(drop=True)

        f33_df = pd.DataFrame([{
            "f33_id":         r.f33_id,
            "feeder_name":    r.name,
            "input_kwh":      r.input_kwh,
            "output_kwh_est": r.output_kwh_est,
            "loss_kwh":       r.loss_kwh,
            "loss_pct":       r.loss_pct,
            "coverage":       r.coverage,
            "f11_count":      r.f11_count,
            "f11_measured":   r.f11_measured,
            "status":         r.status,
            "confidence":     r.confidence,
        } for r in f33_results]).sort_values("loss_kwh", ascending=False).reset_index(drop=True)

        suspicious_rows = [
            {
                "feeder_name":   r.name,
                "f11_id":        r.f11_id,
                "dt_name":       dt.name,
                "dt_id":         dt.dt_id,
                "kwh_7d":        round(dt.kwh, 1),
                "load_factor":   dt.load_factor,
                "nameplate_kva": dt.nameplate,
                "latitude":      dt.lat,
                "longitude":     dt.lon,
            }
            for r in f11_results if r.status in NTL_STATUSES
            for dt in r.dt_profiles if dt.suspicious
        ]
        suspicious_df = (
            pd.DataFrame(suspicious_rows).sort_values("load_factor")
            if suspicious_rows else pd.DataFrame()
        )

        def _kpis(df):
            valid = df[df["loss_pct"].notna()]
            return (
                float(valid["input_kwh"].sum()),
                float(valid["loss_kwh"].sum()),
                float(valid.loc[valid["status"].isin(NTL_STATUSES), "loss_kwh"].sum()),
            )

        f11_in, f11_loss, f11_ntl = _kpis(f11_df)
        f33_in, f33_loss, f33_ntl = _kpis(f33_df)

        return TheftReport(
            f11_feeders=f11_df, f33_feeders=f33_df, suspicious_dts=suspicious_df,
            f11_total_input_kwh=f11_in, f11_total_loss_kwh=f11_loss, f11_total_ntl_kwh=f11_ntl,
            f33_total_input_kwh=f33_in, f33_total_loss_kwh=f33_loss, f33_total_ntl_kwh=f33_ntl,
        )


# ─────────────────────────────────────────────
# CLI  (prints allowed only here)
# ─────────────────────────────────────────────

if __name__ == "__main__":
    td  = TheftDetector()
    rep = td.analyse(hours=ANALYSIS_HOURS)

    def _pct(part, total):
        return f"{part / total * 100:.1f}%" if total > 0 else "n/a"

    print("\n" + "═" * 80)
    print("  NTL DETECTION REPORT")
    print("═" * 80)
    print(f"  {'':30s} {'F11 (11kV)':>20}   {'F33 (33kV)':>20}")
    print(f"  {'Total input energy':30s} {rep.f11_total_input_kwh:>20,.0f}   {rep.f33_total_input_kwh:>20,.0f}  kWh")
    print(f"  {'Total estimated loss':30s} {rep.f11_total_loss_kwh:>20,.0f}   {rep.f33_total_loss_kwh:>20,.0f}  kWh")
    print(f"  {'NTL (suspected theft)':30s} {rep.f11_total_ntl_kwh:>20,.0f}   {rep.f33_total_ntl_kwh:>20,.0f}  kWh")
    print(f"  {'NTL share':30s} {_pct(rep.f11_total_ntl_kwh, rep.f11_total_input_kwh):>20}   {_pct(rep.f33_total_ntl_kwh, rep.f33_total_input_kwh):>20}")
    print("═" * 80)

    f33_valid = rep.f33_feeders[rep.f33_feeders["loss_pct"].notna()]
    print("\n=== F33 FEEDERS (33kV) ===")
    if f33_valid.empty:
        print("No F33 feeders with sufficient data.")
    else:
        print(f33_valid[[
            "feeder_name", "input_kwh", "output_kwh_est", "loss_kwh",
            "loss_pct", "coverage", "f11_count", "f11_measured",
            "status", "confidence"
        ]].to_string(index=False))

    f11_ntl = rep.f11_feeders[rep.f11_feeders["status"].isin(NTL_STATUSES)]
    print(f"\n=== F11 NTL FEEDERS ({len(f11_ntl)}) — SUSPECTED THEFT ===")
    if f11_ntl.empty:
        print("No NTL feeders detected with sufficient data.")
    else:
        print(f11_ntl[[
            "feeder_name", "loss_kwh", "loss_pct",
            "coverage", "dt_suspicious", "status", "confidence"
        ]].to_string(index=False))

    print(f"\n=== SUSPICIOUS DISTRIBUTION SUBSTATIONS ({len(rep.suspicious_dts)}) ===")
    print("  (Active but consuming < 2% of rated capacity — possible meter bypass)")
    if rep.suspicious_dts.empty:
        print("  None.")
    else:
        print(rep.suspicious_dts[[
            "feeder_name", "dt_name", "kwh_7d",
            "load_factor", "nameplate_kva", "latitude", "longitude"
        ]].to_string(index=False))