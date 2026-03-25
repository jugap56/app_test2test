import pandas as pd
import numpy as np
from typing import Tuple

def generiere_haushaltslast(
    jahresverbrauch: float,
    csv_pfad: str = '2025_15min_Haushaltswerte.csv'
) -> pd.DataFrame:
    """
    Generiert ein vektorisiertes 15-Minuten-Lastprofil für den Haushalt für das Jahr 2025.
    Ersetzt die alte schleifenbasierte Logik durch reine Numpy/Pandas-Vektorisierung.

    Args:
        jahresverbrauch (float): Der gewünschte Jahresverbrauch in kWh (z.B. 4000.0).
        csv_pfad (str): Pfad zur CSV-Datei mit den typischen Tagesprofilen je Monat.

    Returns:
        pd.DataFrame: DataFrame mit DatetimeIndex (2025, 15T) und Spalte 'verbrauch_kwh'.
    """
    try:
        df_csv = pd.read_csv(csv_pfad, sep=';', decimal=',', index_col=0)
    except Exception as e:
        raise ValueError(f"Fehler beim Laden der Datei {csv_pfad}: {e}")

    if df_csv.isnull().values.any():
        raise ValueError(f"Die Datei {csv_pfad} enthält NaN-Werte. Bitte bereinigen.")

    # DatetimeIndex für 2025 erstellen (exakt 35.040 Werte)
    idx = pd.date_range(start="2025-01-01 00:00:00", end="2025-12-31 23:45:00", freq="15T")

    # Mapping für die Monatsnamen der CSV
    monate_mapping = {
        1: 'Januar', 2: 'Februar', 3: 'März', 4: 'April',
        5: 'Mai', 6: 'Juni', 7: 'Juli', 8: 'August',
        9: 'September', 10: 'Oktober', 11: 'November', 12: 'Dezember'
    }

    jahres_profil_werte = np.zeros(len(idx))
    monat_index = idx.month

    for monat_num, monat_name in monate_mapping.items():
        if monat_name not in df_csv.columns:
            raise ValueError(f"Erwartete Spalte '{monat_name}' fehlt in {csv_pfad}")

        tagesprofil = df_csv[monat_name].values
        if len(tagesprofil) != 96:
            raise ValueError(f"Das Tagesprofil für {monat_name} muss exakt 96 Werte enthalten (15-Min-Takt).")

        # Maske für alle 15-Min-Intervalle dieses Monats
        mask = (monat_index == monat_num)
        anzahl_tage = mask.sum() // 96
        
        # Vektorisiertes Kacheln des Tagesprofils über den gesamten Monat
        jahres_profil_werte[mask] = np.tile(tagesprofil, anzahl_tage)

    summe_profil = np.sum(jahres_profil_werte)
    if summe_profil == 0:
        raise ValueError("Summe des Basis-Profils ist 0. Skalierung unmöglich.")

    # Exakte Skalierung auf den eingegebenen Jahresverbrauch
    verbrauch_skaliert = jahresverbrauch * (jahres_profil_werte / summe_profil)

    # DataFrame erstellen
    df_haushalt = pd.DataFrame(
        data={'verbrauch_kwh': verbrauch_skaliert},
        index=idx
    )

    return df_haushalt


def berechne_haushaltsverbrauch(
    haushaltslast_df: pd.DataFrame,
    pv_ertrag_df: pd.DataFrame,
    spotpreise_df: pd.DataFrame,
) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """
    Berechnet den Nettoverbrauch des Haushalts nach PV-Verrechnung und die
    daraus resultierenden reinen Energiekosten für § 14a EnWG Module 1–3.

    Args:
        haushaltslast_df: DataFrame mit Index=DatetimeIndex (15T), Spalte="verbrauch_kwh" [kWh].
        pv_ertrag_df: DataFrame mit Index=DatetimeIndex (15T), Spalte="ertrag_kwh" [kWh].
        spotpreise_df: DataFrame mit Index=DatetimeIndex (15T), Spalte="preis_eur_per_kwh"[€/kWh].

    Returns:
        Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
            - nettoverbrauch_df: Spalte "net_consumption_kwh" [kWh].
            - pv_ueberschuss_df: Spalte "surplus_kwh" [kWh] (für SteuVB verfügbar).
            - kosten_df: Spalten "cost_mod1_eur", "cost_mod2_eur", "cost_mod3_eur" [€].
    """
    # 1. Validierung
    for df, col, name in zip([haushaltslast_df, pv_ertrag_df, spotpreise_df],["verbrauch_kwh", "ertrag_kwh", "preis_eur_per_kwh"],["Haushaltslast", "PV-Ertrag", "Spotpreise"]
    ):
        if col not in df.columns:
            raise ValueError(f"Spalte '{col}' fehlt im {name}-DataFrame.")
        if df[col].isnull().any():
            raise ValueError(f"{name}-DataFrame enthält NaN-Werte. Bitte bereinigen.")

    # 2. Alignment der Indizes sicherstellen (Vermeidet Mismatches)
    try:
        df_kombi = pd.concat([
            haushaltslast_df["verbrauch_kwh"],
            pv_ertrag_df["ertrag_kwh"],
            spotpreise_df["preis_eur_per_kwh"]
        ], axis=1, join="inner")
    except Exception as e:
        raise ValueError(f"Fehler beim Zusammenführen der DataFrames (Index-Mismatch?): {e}")

    if len(df_kombi) == 0:
        raise ValueError("Nach dem Zusammenführen der DataFrames sind keine Daten übrig. DatetimeIndizes prüfen!")

    verbrauch = df_kombi["verbrauch_kwh"]
    ertrag = df_kombi["ertrag_kwh"]
    preis = df_kombi["preis_eur_per_kwh"]

    # 3. PV-Verrechnung (Saldo aus Haushalt und PV-Anlage)
    # Vektorisierte max(0, x) Alternative mittels .clip(lower=0)
    netto_verbrauch = (verbrauch - ertrag).clip(lower=0.0)
    pv_ueberschuss = (ertrag - verbrauch).clip(lower=0.0)

    # 4. Kostenberechnung § 14a EnWG Module 1–3
    # Modul 1: Spotpreis + 0,5 ct/kWh (0.005 €). Keine negativen Preise -> clip(lower=0)
    preis_mod1 = (preis + 0.005).clip(lower=0.0)
    kosten_mod1 = netto_verbrauch * preis_mod1

    # Modul 2: Spotpreis + 1 ct/kWh (0.01 €). Negative Preise erlaubt.
    preis_mod2 = preis + 0.010
    kosten_mod2 = netto_verbrauch * preis_mod2

    # Modul 3: 90% des Spotpreises. Mindestens 0 ct/kWh -> clip(lower=0)
    preis_mod3 = (preis * 0.9).clip(lower=0.0)
    kosten_mod3 = netto_verbrauch * preis_mod3

    # 5. Output-Strukturierung
    nettoverbrauch_df = pd.DataFrame({"net_consumption_kwh": netto_verbrauch}, index=df_kombi.index)
    pv_ueberschuss_df = pd.DataFrame({"surplus_kwh": pv_ueberschuss}, index=df_kombi.index)
    
    kosten_df = pd.DataFrame({
        "cost_mod1_eur": kosten_mod1,
        "cost_mod2_eur": kosten_mod2,
        "cost_mod3_eur": kosten_mod3
    }, index=df_kombi.index)

    return nettoverbrauch_df, pv_ueberschuss_df, kosten_df


# =====================================================================
# TESTBEREICH
# =====================================================================
if __name__ == "__main__":
    print("Starte Modul-Test für 'haushalt.py' ...")
    
    # Mock-Daten Generierung für einen schnellen Check (ohne CSV)
    test_idx = pd.date_range(start="2025-01-01 00:00:00", end="2025-12-31 23:45:00", freq="15T")
    mock_haushalt = pd.DataFrame({"verbrauch_kwh": np.random.uniform(0.1, 1.0, len(test_idx))}, index=test_idx)
    mock_pv = pd.DataFrame({"ertrag_kwh": np.random.uniform(0.0, 2.0, len(test_idx))}, index=test_idx)
    mock_spot = pd.DataFrame({"preis_eur_per_kwh": np.random.uniform(-0.05, 0.30, len(test_idx))}, index=test_idx)

    netto_df, pv_df, kosten_df = berechne_haushaltsverbrauch(mock_haushalt, mock_pv, mock_spot)
    
    print(f"✅ Dimensionen erfolgreich: Netto={netto_df.shape}, PV_Überschuss={pv_df.shape}, Kosten={kosten_df.shape}")
