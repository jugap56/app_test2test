import pandas as pd
import numpy as np

def generiere_lade_profil(
        fahrleistung_woche_tag_km: float,
        fahrleistung_wochenende_tag_km: float,
        verbrauch_pro_100km: float,
        wallbox_leistung_kw: float,
        ladebeginn_stunde: int
) -> pd.DataFrame:
    """
    Erstellt ein vektorisiertes 15-Minuten-Lastprofil für das E-Auto im Jahr 2025.
    Nutzt eine 2D-Matrix-Addition, um Ladevorgänge, die über Mitternacht hinausgehen 
    (Spillover), ohne performanceraubende Schleifen auf den nächsten Tag zu übertragen.

    Args:
        fahrleistung_woche_tag_km (float): Gefahrene Kilometer an Wochentagen.
        fahrleistung_wochenende_tag_km (float): Gefahrene Kilometer am Wochenende.
        verbrauch_pro_100km (float): Verbrauch in kWh pro 100 km.
        wallbox_leistung_kw (float): Maximale Ladeleistung in kW (z. B. 11.0).
        ladebeginn_stunde (int): Stunde des Ladebeginns (z. B. 18 für 18:00 Uhr).

    Returns:
        pd.DataFrame: DataFrame mit DatetimeIndex (2025, 15T) und Spalte 'verbrauch_kwh'.
    """
    # 1. Ziel-Index erstellen
    idx = pd.date_range("2025-01-01 00:00:00", "2025-12-31 23:45:00", freq="15T")

    if wallbox_leistung_kw <= 0:
        return pd.DataFrame({"verbrauch_kwh": 0.0}, index=idx)

    # 2. Energiebedarf pro Tag in kWh berechnen
    e_woche = (fahrleistung_woche_tag_km / 100.0) * verbrauch_pro_100km
    e_wochenende = (fahrleistung_wochenende_tag_km / 100.0) * verbrauch_pro_100km
    
    # Maximale Energie pro 15-Minuten-Slot
    max_e_per_15min = wallbox_leistung_kw * 0.25

    # 3. 48-Stunden-Basisprofile (192 Intervalle) erstellen, um Spillover abzubilden
    def erstelle_basis_profil(energie: float) -> np.ndarray:
        prof = np.zeros(192)
        if energie <= 0:
            return prof
        
        full_slots = int(energie // max_e_per_15min)
        rem = energie % max_e_per_15min
        start_idx = ladebeginn_stunde * 4
        
        end_idx = min(start_idx + full_slots, 192)
        prof[start_idx:end_idx] = max_e_per_15min
        
        if end_idx < 192 and rem > 0:
            prof[end_idx] = rem
            
        return prof

    prof_woche = erstelle_basis_profil(e_woche)
    prof_wochenende = erstelle_basis_profil(e_wochenende)

    # 4. Array der 365 Tage im Jahr 2025 ermitteln (Wochentag vs. Wochenende)
    tage_2025 = pd.date_range("2025-01-01", "2025-12-31", freq="D")
    ist_wochenende = tage_2025.dayofweek >= 5  # 5=Samstag, 6=Sonntag

    # 2D-Matrix (365 Tage x 192 Intervalle). np.where wählt Vektorisiert das richtige Profil.
    tages_matrix = np.where(ist_wochenende[:, None], prof_wochenende, prof_woche)

    # 5. Spillover auflösen (Verschiebung der Slots > 96 auf den Folgetag)
    # Wir erstellen eine Matrix mit 366 Tagen, um den Spillover vom 31.12. abzufangen
    ergebnis_matrix = np.zeros((366, 96))

    # Addiere den Anteil, der am selben Tag stattfindet (Intervalle 0 bis 95)
    ergebnis_matrix[:-1, :] += tages_matrix[:, 0:96]

    # Addiere den Spillover auf den Folgetag (Intervalle 96 bis 191 rutschen eine Zeile tiefer)
    ergebnis_matrix[1:, :] += tages_matrix[:, 96:192]

    # 6. Matrix auf 1D reduzieren und den Spillover ins Jahr 2026 abschneiden
    profil_1d = ergebnis_matrix[:-1, :].flatten()

    # 7. Output formatieren
    df_ea = pd.DataFrame({"verbrauch_kwh": profil_1d}, index=idx)

    return df_ea


# =====================================================================
# TESTBEREICH
# =====================================================================
if __name__ == "__main__":
    print("Starte Vektorisierungs-Test für 'eAuto.py' ...")
    
    df_profil = generiere_lade_profil(
        fahrleistung_woche_tag_km=40.0,
        fahrleistung_wochenende_tag_km=15.0,
        verbrauch_pro_100km=18.0,
        wallbox_leistung_kw=11.0,
        ladebeginn_stunde=22  # Spätes Laden erzwingt Spillover über Mitternacht!
    )

    print("-" * 50)
    print(f"Dimension des DataFrames: {df_profil.shape} (Erwartet: 35040, 1)")
    print(f"Gesamt geladene Energie im Jahr: {df_profil['verbrauch_kwh'].sum():.2f} kWh")
    print("-" * 50)
    
    # Kontrolle des Spillovers vom 01.01. (Mittwoch) auf den 02.01.
    print("Test: Mitternachts-Spillover vom 01.01. auf den 02.01.")
    print(df_profil.loc["2025-01-01 23:00":"2025-01-02 01:00"])
