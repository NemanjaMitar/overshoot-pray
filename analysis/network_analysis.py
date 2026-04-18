"""
analysis/theft_detection.py

Detekcija nekomercijalnih (NTL) gubitaka u distributivnoj mreži.

Princip:
  - Tehnički (fizički) gubici: nastaju od otpora vodova (I²R), tipično 2–8%.
  - Netehničke gubitke (NTL, krađa): sve što je iznad praga za dati nivo napona.

Hijerarhija:
  TransmissionStation → Feeders33 → Feeders11 → DistributionSubstation (DT) → Korisnici

Pristup:
  1. Koristi TFES registar energije (pouzdaniji od V×I) s dužim prozorom (7 dana).
  2. Na F11 nivou: ulaz (F11 mjerač) − zbir DT mjerača = ukupni gubitak na feederu.
  3. Gubitak > PRAG_NTL → sumnja na krađu.
  4. Unutar svakog flagovanog F11 feeder: identifikuj DT-ove sa sumnjivo niskim
     load factorom (potrošnja / nameplate kapacitet) — mogući bypass mjerača.
  5. Rangiranje po apsolutnom kWh gubitku (ne samo %) — prioritizuje najveće štete.
"""

import sys
import os
from dataclasses import dataclass, field
from typing import Optional

import pandas as pd
import numpy as np

sys.path.append(os.path.join(os.path.dirname(__file__), ".."))
from get_data.db import q


# ─────────────────────────────────────────────
# KONSTANTE
# ─────────────────────────────────────────────

# Duži prozor = manje šuma od skaliranja, bolji coverage
ANALYSIS_HOURS = 168          # 7 dana

COS_PHI       = 0.9
TFES_WH_TO_KWH = 1000.0

# Pragovi gubitaka (%) po nivou napona
PRAG_TECH_F11   = 8.0         # Tehnički gubici na 11kV — do 8% je normalno
PRAG_NTL_F11    = 15.0        # Iznad 15% → NTL (krađa) na F11
PRAG_NTL_HIGH   = 30.0        # Iznad 30% → vjerovatna krađa
PRAG_NTL_ALARM  = 50.0        # Iznad 50% → alarm

# Minimalni podaci za validan zaključak
MIN_COVERAGE     = 0.30       # Min % DT-ova s mjerenjima za F11 analizu
MIN_KWH_FEEDER   = 10.0       # Min kWh ulaz za F11 da bi analiza imala smisla
MIN_TFES_HOURS   = 8.0        # Minimalnom vremenski prozor TFES mjerenja

# Load factor: ako je DT troši manje od X% svog kapaciteta → sumnjivo
LOAD_FACTOR_SUSPICIOUS = 0.02  # < 2% kapaciteta kroz 7 dana → moguć bypass

MAX_SCALE_FACTOR = 3.0        # Max 3× skaliranje (7 dana → dovoljno podataka)

# Fallback nameplate (kVA) ako nedostaje u bazi
FALLBACK_KVA_DT  = 500.0
FALLBACK_KVA_F11 = 5000.0
FALLBACK_KVA_F33 = 40000.0


# ─────────────────────────────────────────────
# TIPOVI
# ─────────────────────────────────────────────

@dataclass
class DtProfil:
    dt_id:       int
    naziv:       str
    feeder11_id: int
    kwh:         float          # Izmjerena potrošnja u prozoru
    load_factor: float          # kwh / (nameplate_kva * COS_PHI * hours)
    nameplate:   float          # kVA
    lat:         Optional[float]
    lon:         Optional[float]
    sumnjiv:     bool           # Neobično nizak load factor


@dataclass
class FeederRezultat:
    f11_id:      int
    naziv:       str
    kwh_ulaz:    float          # Energija na ulazu F11 feeder (mjerač)
    kwh_izlaz:   float          # Suma DT potrošnje (izmjerena)
    kwh_izlaz_est: float        # Procijenjena suma (korigovana za coverage)
    gubitak_kwh: float          # Procijenjeni apsolutni gubitak (kWh)
    gubitak_pct: Optional[float]
    coverage:    float          # Udio DT-ova s validnim mjerenjima
    n_dt:        int
    n_dt_ok:     int
    n_dt_sumnjiv: int           # DT-ovi sa sumnjivim load faktorom
    status:      str            # NORMALNO / UPOZORENJE / NTL / NTL_ALARM
    pouzdanost:  str            # VISOKA / SREDNJA / NISKA
    f33_id:      Optional[int]
    dt_profili:  list           # Lista DtProfil za ovaj feeder


@dataclass
class IzveštajKradje:
    feederi:     pd.DataFrame   # Rangirani po gubitak_kwh desc
    sumnjivi_dt: pd.DataFrame   # DT-ovi s niskim load factorom
    ukupno_kwh_ulaz:   float
    ukupno_kwh_gubitak: float
    ukupno_ntl_kwh:    float    # Procijenjeni NTL (>prag)


# ─────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────

def chunk(lst, size=500):
    for i in range(0, len(lst), size):
        yield lst[i:i + size]


def _tfes_delta(meter_ids: list, hours: int) -> dict:
    """
    Vraća {mid: kwh} koristeći MAX-MIN delta TFES registra.
    Prozor: poslednjih `hours` sati od MAX(Ts) svakog mjerača.
    Odbacuje kratke prozore i nerealno visoke vrijednosti.
    """
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
            mid = int(row["Mid"])
            delta_wh = float(row["delta_wh"])
            min_ts, max_ts = row["min_ts"], row["max_ts"]

            if pd.isna(min_ts) or pd.isna(max_ts):
                continue

            real_hours = max(
                (max_ts - min_ts).total_seconds() / 3600.0,
                0.5
            )

            # Odbaci prekratke prozore
            if real_hours < MIN_TFES_HOURS:
                continue

            # Skaliraj na traženi period, ali ne previše agresivno
            factor = min(hours / real_hours, MAX_SCALE_FACTOR)
            kwh = (delta_wh / TFES_WH_TO_KWH) * factor

            result[mid] = {
                "kwh":        kwh,
                "real_hours": real_hours,
                "n_readings": int(row["n_readings"]),
            }

    return result


def _klasifikuj(gubitak_pct: Optional[float], coverage: float) -> tuple[str, str]:
    """
    Vraća (status, pouzdanost).
    """
    if coverage >= 0.80:
        pouzd = "VISOKA"
    elif coverage >= 0.50:
        pouzd = "SREDNJA"
    else:
        pouzd = "NISKA"

    if gubitak_pct is None:
        return "NEPOZNATO", pouzd

    if gubitak_pct < 0:
        return "GREŠKA_MJERENJA", pouzd

    if gubitak_pct < PRAG_TECH_F11:
        return "NORMALNO", pouzd

    if gubitak_pct < PRAG_NTL_F11:
        return "UPOZORENJE", pouzd

    if gubitak_pct < PRAG_NTL_HIGH:
        return "NTL", pouzd          # Non-Technical Loss — sumnja na krađu

    if gubitak_pct < PRAG_NTL_ALARM:
        return "NTL_VISOKO", pouzd

    return "NTL_ALARM", pouzd


# ─────────────────────────────────────────────
# GLAVNI ANALIZATOR
# ─────────────────────────────────────────────

class TheftDetector:

    def __init__(self):
        print("Učitavam topologiju mreže...")
        self.top = self._load_topology()
        print(f"  F33: {len(self.top['f33'])} feedera")
        print(f"  F11: {len(self.top['f11'])} feedera")
        print(f"  DT:  {len(self.top['dt'])} podstanica")

    def _load_topology(self) -> dict:
        return {
            "f33": q("""
                SELECT Id, Name, MeterId, TsId,
                       ISNULL(NameplateRating, 0) AS NameplateRating
                FROM Feeders33
                WHERE MeterId IS NOT NULL
            """),
            "f11": q("""
                SELECT Id, Name, MeterId, Feeder33Id,
                       ISNULL(NameplateRating, 0) AS NameplateRating
                FROM Feeders11
                WHERE MeterId IS NOT NULL
            """),
            "dt": q("""
                SELECT Id, Name, MeterId, Feeder11Id,
                       ISNULL(NameplateRating, 0) AS NameplateRating,
                       Latitude, Longitude
                FROM DistributionSubstation
                WHERE MeterId IS NOT NULL
            """),
        }

    def _svi_mid(self) -> list:
        mids = []
        for tbl in self.top.values():
            if "MeterId" in tbl.columns:
                mids += tbl["MeterId"].dropna().astype(int).tolist()
        return list(set(mids))

    def _nameplate_kwh(self, kva: float, hours: float) -> float:
        """Maksimalni razumni kWh za dati kVA i period."""
        if kva <= 0:
            return float("inf")
        return kva * COS_PHI * hours

    def analiziraj(self, hours: int = ANALYSIS_HOURS) -> IzveštajKradje:
        mids = self._svi_mid()
        print(f"\nDohvatam TFES podatke za {len(mids)} mjerača ({hours}h prozor)...")
        tfes = _tfes_delta(mids, hours)
        print(f"  → {len(tfes)} mjerača s validnim TFES podacima.")

        # Nameplate cap mapa
        cap: dict[int, float] = {}
        for level, fallback_kva in [("dt", FALLBACK_KVA_DT), ("f11", FALLBACK_KVA_F11), ("f33", FALLBACK_KVA_F33)]:
            for _, row in self.top[level].iterrows():
                mid = int(row["MeterId"])
                kva = float(row["NameplateRating"]) if row["NameplateRating"] > 0 else fallback_kva
                max_kwh = self._nameplate_kwh(kva, hours)
                if mid not in cap or max_kwh < cap[mid]:
                    cap[mid] = max_kwh

        # Filtriraj nerealne vrijednosti
        energy: dict[int, float] = {}
        for mid, val in tfes.items():
            kwh = val["kwh"]
            if kwh <= cap.get(mid, float("inf")):
                energy[mid] = kwh

        print(f"  → {sum(1 for k in energy if energy[k] > 0)} mjerača s kWh > 0 (nakon cap filtera).")

        # ── Analiza po F11 feederu ──
        print("\nAnaliziram gubitke po F11 feederima...")

        dt_tbl = self.top["dt"]
        f11_tbl = self.top["f11"]

        rezultati: list[FeederRezultat] = []

        for _, f11_row in f11_tbl.iterrows():
            f11_id  = int(f11_row["Id"])
            f11_mid = int(f11_row["MeterId"])
            f11_kva = float(f11_row["NameplateRating"]) if f11_row["NameplateRating"] > 0 else FALLBACK_KVA_F11

            kwh_ulaz = energy.get(f11_mid, 0.0)

            # Svi DT-ovi na ovom F11 feederu
            dts = dt_tbl[dt_tbl["Feeder11Id"] == f11_id]

            dt_profili: list[DtProfil] = []
            kwh_izlaz_sum = 0.0
            n_ok = 0

            for _, dt_row in dts.iterrows():
                dt_mid = int(dt_row["MeterId"])
                dt_kva = float(dt_row["NameplateRating"]) if dt_row["NameplateRating"] > 0 else FALLBACK_KVA_DT
                dt_kwh = energy.get(dt_mid, 0.0)

                # Load factor: koliko % kapaciteta DT je iskorišćeno
                max_dt_kwh = self._nameplate_kwh(dt_kva, hours)
                load_factor = dt_kwh / max_dt_kwh if max_dt_kwh > 0 else 0.0

                sumnjiv = (
                    dt_mid in energy          # Mjerač radi
                    and dt_kwh < max_dt_kwh * LOAD_FACTOR_SUSPICIOUS
                    and dt_kwh > 0            # Nije offline — troši, ali premalo
                )

                has_data = dt_mid in energy and dt_kwh > 0

                if has_data:
                    kwh_izlaz_sum += dt_kwh
                    n_ok += 1

                dt_profili.append(DtProfil(
                    dt_id       = int(dt_row["Id"]),
                    naziv       = dt_row["Name"],
                    feeder11_id = f11_id,
                    kwh         = dt_kwh,
                    load_factor = round(load_factor, 4),
                    nameplate   = dt_kva,
                    lat         = dt_row.get("Latitude"),
                    lon         = dt_row.get("Longitude"),
                    sumnjiv     = sumnjiv,
                ))

            n_total = len(dts)
            coverage = n_ok / n_total if n_total > 0 else 0.0

            # Preskočiti feedere bez dovoljno podataka
            if kwh_ulaz < MIN_KWH_FEEDER:
                rezultati.append(FeederRezultat(
                    f11_id=f11_id, naziv=f11_row["Name"],
                    kwh_ulaz=kwh_ulaz, kwh_izlaz=kwh_izlaz_sum,
                    kwh_izlaz_est=0.0, gubitak_kwh=0.0, gubitak_pct=None,
                    coverage=coverage, n_dt=n_total, n_dt_ok=n_ok,
                    n_dt_sumnjiv=sum(1 for d in dt_profili if d.sumnjiv),
                    status="NEVALIDAN_ULAZ", pouzdanost="NISKA",
                    f33_id=int(f11_row["Feeder33Id"]) if pd.notna(f11_row.get("Feeder33Id")) else None,
                    dt_profili=dt_profili,
                ))
                continue

            if coverage < MIN_COVERAGE:
                rezultati.append(FeederRezultat(
                    f11_id=f11_id, naziv=f11_row["Name"],
                    kwh_ulaz=kwh_ulaz, kwh_izlaz=kwh_izlaz_sum,
                    kwh_izlaz_est=0.0, gubitak_kwh=0.0, gubitak_pct=None,
                    coverage=coverage, n_dt=n_total, n_dt_ok=n_ok,
                    n_dt_sumnjiv=sum(1 for d in dt_profili if d.sumnjiv),
                    status="NEDOVOLJNO_PODATAKA", pouzdanost="NISKA",
                    f33_id=int(f11_row["Feeder33Id"]) if pd.notna(f11_row.get("Feeder33Id")) else None,
                    dt_profili=dt_profili,
                ))
                continue

            # Procijeni ukupni izlaz korigovanjem za coverage
            # (pretpostavka: DT-ovi bez podataka troše slično kao oni s podacima)
            kwh_izlaz_est = kwh_izlaz_sum / coverage if coverage > 0 else 0.0

            gubitak_kwh = max(kwh_ulaz - kwh_izlaz_est, 0.0)
            gubitak_pct = round((kwh_ulaz - kwh_izlaz_est) / kwh_ulaz * 100, 2) if kwh_ulaz > 0 else None

            status, pouzdanost = _klasifikuj(gubitak_pct, coverage)

            rezultati.append(FeederRezultat(
                f11_id=f11_id, naziv=f11_row["Name"],
                kwh_ulaz=round(kwh_ulaz, 1),
                kwh_izlaz=round(kwh_izlaz_sum, 1),
                kwh_izlaz_est=round(kwh_izlaz_est, 1),
                gubitak_kwh=round(gubitak_kwh, 1),
                gubitak_pct=gubitak_pct,
                coverage=round(coverage, 3),
                n_dt=n_total, n_dt_ok=n_ok,
                n_dt_sumnjiv=sum(1 for d in dt_profili if d.sumnjiv),
                status=status, pouzdanost=pouzdanost,
                f33_id=int(f11_row["Feeder33Id"]) if pd.notna(f11_row.get("Feeder33Id")) else None,
                dt_profili=dt_profili,
            ))

        # ── Agregacija u DataFrame ──
        feederi_df = pd.DataFrame([{
            "f11_id":        r.f11_id,
            "naziv":         r.naziv,
            "f33_id":        r.f33_id,
            "kwh_ulaz":      r.kwh_ulaz,
            "kwh_izlaz_est": r.kwh_izlaz_est,
            "gubitak_kwh":   r.gubitak_kwh,
            "gubitak_pct":   r.gubitak_pct,
            "coverage":      r.coverage,
            "n_dt":          r.n_dt,
            "n_dt_ok":       r.n_dt_ok,
            "n_dt_sumnjiv":  r.n_dt_sumnjiv,
            "status":        r.status,
            "pouzdanost":    r.pouzdanost,
        } for r in rezultati])

        # Rangiraj po apsolutnom gubitku (najvažnija metrika za prioritizaciju)
        feederi_df = feederi_df.sort_values("gubitak_kwh", ascending=False).reset_index(drop=True)

        # ── Sumnjivi DT-ovi ──
        sumnjivi_dts = []
        for r in rezultati:
            if r.status in ("NTL", "NTL_VISOKO", "NTL_ALARM"):
                for dt in r.dt_profili:
                    if dt.sumnjiv:
                        sumnjivi_dts.append({
                            "feeder":      r.naziv,
                            "f11_id":      r.f11_id,
                            "dt_naziv":    dt.naziv,
                            "dt_id":       dt.dt_id,
                            "kwh_7d":      round(dt.kwh, 1),
                            "load_factor": dt.load_factor,
                            "nameplate_kva": dt.nameplate,
                            "lat":         dt.lat,
                            "lon":         dt.lon,
                        })

        sumnjivi_df = pd.DataFrame(sumnjivi_dts).sort_values("load_factor") if sumnjivi_dts else pd.DataFrame()

        # ── Sumarni KPI ──
        validni = feederi_df[feederi_df["gubitak_pct"].notna()]
        ukupno_ulaz    = float(validni["kwh_ulaz"].sum())
        ukupno_gubitak = float(validni["gubitak_kwh"].sum())

        ntl_mask = validni["status"].isin(["NTL", "NTL_VISOKO", "NTL_ALARM"])
        ntl_kwh  = float(validni.loc[ntl_mask, "gubitak_kwh"].sum())

        return IzveštajKradje(
            feederi=feederi_df,
            sumnjivi_dt=sumnjivi_df,
            ukupno_kwh_ulaz=ukupno_ulaz,
            ukupno_kwh_gubitak=ukupno_gubitak,
            ukupno_ntl_kwh=ntl_kwh,
        )


# ─────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────

if __name__ == "__main__":
    td = TheftDetector()
    izv = td.analiziraj(hours=ANALYSIS_HOURS)

    print("\n" + "═" * 80)
    print("  IZVJEŠTAJ O GUBICIMA I KRAĐI STRUJE")
    print("═" * 80)
    print(f"  Ukupno ulazna energija (validni feederi): {izv.ukupno_kwh_ulaz:,.0f} kWh")
    print(f"  Ukupno procijenjeni gubici:               {izv.ukupno_kwh_gubitak:,.0f} kWh")
    print(f"  Od toga NTL (sumnja na krađu):            {izv.ukupno_ntl_kwh:,.0f} kWh")
    if izv.ukupno_kwh_ulaz > 0:
        print(f"  NTL udio od ukupnog ulaza:                {izv.ukupno_ntl_kwh / izv.ukupno_kwh_ulaz * 100:.1f}%")
    print("═" * 80)

    # Top 20 feedera po gubitku
    print("\n=== TOP 20 FEEDERA PO PROCIJENJENOM GUBITKU ===")
    top20 = izv.feederi[izv.feederi["gubitak_pct"].notna()].head(20)
    print(top20[[
        "naziv", "kwh_ulaz", "kwh_izlaz_est", "gubitak_kwh",
        "gubitak_pct", "coverage", "n_dt", "n_dt_ok", "n_dt_sumnjiv",
        "status", "pouzdanost"
    ]].to_string(index=False))

    # NTL feederi
    ntl = izv.feederi[izv.feederi["status"].isin(["NTL", "NTL_VISOKO", "NTL_ALARM"])]
    print(f"\n=== NTL FEEDERI ({len(ntl)}) — SUMNJA NA KRAĐU ===")
    if ntl.empty:
        print("Nema detektovanih NTL feedera s dovoljno podataka.")
    else:
        print(ntl[[
            "naziv", "gubitak_kwh", "gubitak_pct",
            "coverage", "n_dt_sumnjiv", "status", "pouzdanost"
        ]].to_string(index=False))

    # Sumnjivi DT-ovi
    print(f"\n=== SUMNJIVE NISKONAPONSKE PODSTANICE ({len(izv.sumnjivi_dt)}) ===")
    print("  (Aktivne, ali troše < 2% kapaciteta — mogući bypass mjerača)")
    if izv.sumnjivi_dt.empty:
        print("  Nema.")
    else:
        print(izv.sumnjivi_dt[[
            "feeder", "dt_naziv", "kwh_7d", "load_factor", "nameplate_kva", "lat", "lon"
        ]].to_string(index=False))