import pandas as pd
import requests
import os
from typing import Optional

def generiere_pv_ertrag(
    pv_kwp: float,
    pv_neigung: float,
    pv_ausrichtung: float,
    lat: float = 51.0504,
    lon: float = 13.7373,
    cache_dir: str = "."
) -> pd.DataFrame:
    """
    Ruft stündliche PV-Daten von der EU PVGIS API ab, interpoliert diese auf
    15-Minuten-Intervalle, berechnet den Ertrag in kWh und mappt die Zeitreihe
    physikalisch korrekt auf das Zieljahr 2025.

    Args:
        pv_kwp (float): Installierte PV-Leistung in kWp.
        pv_neigung (float): Dachneigung in Grad (0 = flach, 90 = vertikal).
        pv_ausrichtung (float): Ausrichtung in Grad (0 = Süd, -90 = Ost, 90 = West).
        lat (float): Breitengrad (Standard: Dresden).
        lon (float): Längengrad (Standard: Dresden).
        cache_dir (str): Verzeichnis zum Speichern/Laden gecachter CSV-Dateien.

    Returns:
        pd.DataFrame: DataFrame mit DatetimeIndex (2025, 15T, Europe/Berlin) 
                      und Spalte 'ertrag_kwh'.
    """
    if pv_kwp <= 0:
        # Falls keine PV-Anlage konfiguriert ist, direkt ein Null-Array zurückgeben
        ziel_index = pd.date_range(
            start='2025-01-01 00:00:00', periods=35040, freq='15T', tz='Europe/Berlin'
        )
        return pd.DataFrame({'ertrag_kwh': 0.0}, index=ziel_index)

    # 1. Lokalen Cache prüfen (um wiederholte, sehr langsame API-Calls bei UI-Reloads zu vermeiden)
    cache_file = os.path.join(cache_dir, f"pv_ertrag_2025_{pv_kwp}kWp_{pv_neigung}deg_{pv_ausrichtung}deg.csv")
    
    if os.path.exists(cache_file):
        try:
            df_cache = pd.read_csv(cache_file, sep=';', decimal=',', index_col=0, parse_dates=True)
            print(df_cache)
            # Zeitzone sicherstellen, falls durch CSV-Export verloren gegangen
            #if df_cache.index.tz is None:
            #    df_cache.index = df_cache.index.tz_localize('Europe/Berlin', ambiguous='infer', nonexistent='shift_forward')
            if pv_ertrag.index.tz is not None:
                pv_ertrag.index = pv_ertrag.index.tz_localize(None)
            return df_cache[['ertrag_kwh']]
        except Exception as e:
            print(f"Cache konnte nicht gelesen werden, lade Daten neu: {e}")

    # 2. API Call (Jahr 2019 als Basis für typisches Wetter, da PVGIS nicht in die Zukunft reicht)
    print("Lade stündliche Daten von der EU-Datenbank herunter...")
    url = "https://re.jrc.ec.europa.eu/api/v5_2/seriescalc"
    params = {
        'lat': lat,
        'lon': lon,
        'pvcalculation': 1,
        'peakpower': pv_kwp,
        'loss': 14,
        'angle': pv_neigung,
        'aspect': pv_ausrichtung,
        'startyear': 2019,
        'endyear': 2019,
        'outputformat': 'json'
    }

    response = requests.get(url, params=params)
    if response.status_code != 200:
        raise ValueError(f"Fehler bei PVGIS API-Anfrage: {response.status_code} - {response.text}")

    data = response.json()
    df = pd.DataFrame(data['outputs']['hourly'])

    # 3. Zeit parsen und als Index setzen (Wir bleiben zunächst strikt in UTC!)
    df['time'] = pd.to_datetime(df['time'], format='%Y%m%d:%H%M', utc=True)
    df = df.set_index('time')

    # 4. Leistung in kW umrechnen (API liefert P in Watt)
    df['P_kW'] = df['P'] / 1000.0

    # Zeiten auf volle Stunden runden, um sauberes Resampling zu ermöglichen
    df.index = df.index.round('H')

    # 5. Resampling auf 15-Minuten-Raster und Interpolation (in UTC)
    df_15min = df[['P_kW']].resample('15T').interpolate(method='time')
    df_15min['P_kW'] = df_15min['P_kW'].clip(lower=0.0)

    # 6. Umrechnung von Leistung (kW) in Energie (kWh)
    # In 15 Minuten (0.25h) ist die eingespeiste Energiemenge = Leistung * 0.25
    df_15min['ertrag_kwh'] = df_15min['P_kW'] * 0.25

    # 7. Mapping auf das Zieljahr 2025 (Physikalisch korrekte Verschiebung der UTC-Zeit)
    # 2019 bis 2025 sind exakt 2192 Tage (inkl. 2 Schaltjahre 2020 und 2024).
    # Durch diese Verschiebung bleibt der Sonnenhöchststand auf die Minute genau erhalten.
    df_15min.index = df_15min.index + pd.Timedelta(days=2192)

    # 8. Konvertierung in die deutsche Zeitzone (berücksichtigt automatisch Sommerzeit für 2025)
    df_2025_berlin = df_15min.tz_convert('Europe/Berlin')

    # 9. Zwingen in das exakte 2025-Raster (Genau 35.040 Zeilen)
    # Eventuelle Lücken an den Rändern (z.B. Neujahr 00:00 Uhr, weil der erste Wert UTC verschoben wurde)
    # werden mit 0.0 gefüllt, da nachts ohnehin kein PV-Ertrag anfällt.
    ziel_index = pd.date_range(
        start='2025-01-01 00:00:00', 
        periods=35040, 
        freq='15T', 
        tz='Europe/Berlin'
    )
    df_export = df_2025_berlin[['ertrag_kwh']].reindex(ziel_index).fillna(0.0)

    # 10. In Cache speichern
    df_export.to_csv(cache_file, sep=';', decimal=',')

    return df_export


# =====================================================================
# TESTBEREICH
# =====================================================================
if __name__ == "__main__":
    print("Starte API-Test und Vektorisierungsprüfung für 'PVAnlage.py' ...")
    
    # Beispielaufruf
    df_pv = generiere_pv_ertrag(pv_kwp=10.0, pv_neigung=30, pv_ausrichtung=0)
    
    print("-" * 50)
    print(f"Dimension des DataFrames: {df_pv.shape} (Erwartet: 35040, 1)")
    print(f"Index vom Typ: {type(df_pv.index)}")
    print(f"Zeitzone des Index: {df_pv.index.tz}")
    print(f"Jahresertrag: {df_pv['ertrag_kwh'].sum():.2f} kWh")
    print("-" * 50)
    print("Beispieldaten (Mittags an einem Sommertag):")
    print(df_pv.loc['2025-06-21 12:00':'2025-06-21 13:00'])
