import streamlit as st
import pandas as pd
import requests

# Dieser Dekorator sagt Streamlit: "Merk dir das Ergebnis dieser Funktion!"
# Wenn die gleichen Parameter nochmal kommen, lade es direkt aus dem RAM.
@st.cache_data 
def generiere_pv_ertrag(
        pv_kwp: float,
        pv_neigung: float,
        pv_ausrichtung: float,
        lat: float = 51.0504,
        lon: float = 13.7373
) -> pd.DataFrame:
    
    if pv_kwp <= 0:
        ziel_index = pd.date_range(start='2025-01-01 00:00:00', periods=35040, freq='15min', tz=None)
        return pd.DataFrame({'ertrag_kwh': 0.0}, index=ziel_index)

    # KEIN CSV-CACHE MEHR NÖTIG! Wir gehen direkt zur API.
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
        raise ValueError(f"Fehler bei PVGIS API-Anfrage: {response.status_code}")

    data = response.json()
    df = pd.DataFrame(data['outputs']['hourly'])
    
    # ... (Dein restlicher Code für die Datenverarbeitung bleibt exakt gleich) ...
    df['time'] = pd.to_datetime(df['time'], format='%Y%m%d:%H%M', utc=True)
    df = df.set_index('time')
    df['P_kW'] = df['P'] / 1000.0
    df.index = df.index.round('h')
    
    df_15min = df[['P_kW']].resample('15min').interpolate(method='time')
    df_15min['P_kW'] = df_15min['P_kW'].clip(lower=0.0)
    df_15min['ertrag_kwh'] = df_15min['P_kW'] * 0.25
    df_15min.index = df_15min.index + pd.Timedelta(days=2192)
    
    df_2025_berlin = df_15min.copy()
    df_2025_berlin.index = df_2025_berlin.index.tz_localize(None)
    
    ziel_index = pd.date_range(start='2025-01-01 00:00:00', periods=35040, freq='15min', tz=None)
    df_export = df_2025_berlin[['ertrag_kwh']].reindex(ziel_index).fillna(0.0)
    df_export.index = df_export.index.tz_localize(None)

    # Einfach den DataFrame zurückgeben. Streamlit kümmert sich um den Rest!
    return df_export
