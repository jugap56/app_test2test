import pandas as pd
import numpy as np
import os

def berechne_waermepumpe_verbrauch(
    temp_datei: str, 
    t_base: float = 15.0, 
    jahresbedarf: float = 4000.0, 
    verbose: bool = False
) -> pd.DataFrame:
    """
    Liest Temperaturdaten ein, interpoliert Lücken und berechnet den 
    Energieverbrauch der Wärmepumpe für jedes 15-Minuten-Intervall.
    Nutzt zur Leistungsberechnung vektorisierte Pandas-Methoden.

    Args:
        temp_datei (str): Pfad zur CSV-Datei mit den Temperaturdaten.
        t_base (float): Heizgrenztemperatur in °C (Standard: 15.0).
        jahresbedarf (float): Jährlicher Heizenergiebedarf in kWh (Standard: 4000.0).
        verbose (bool): Wenn True, werden Zwischenschritte in der Konsole ausgegeben.

    Returns:
        pd.DataFrame: DataFrame mit exakt 35.040 Zeilen.
                      Index: DatetimeIndex (2025, 15T).
                      Spalten: "temperatur_c" [°C], "verbrauch_kwh" [kWh].
    """
    if verbose:
        print(f"Lade und strukturiere Temperaturdaten aus '{temp_datei}'...")

    if not os.path.exists(temp_datei):
        raise FileNotFoundError(f"Die Temperatur-Datei '{temp_datei}' wurde nicht gefunden.")

    # Einlesen der CSV
    try:
        df_temp = pd.read_csv(temp_datei, sep=";", decimal=",")
    except Exception as e:
        raise ValueError(f"Fehler beim Einlesen der Datei {temp_datei}: {e}")

    # 1. Daten in ein sauberes Zeitreihenformat (Long-Format) umwandeln
    if 'Datum' in df_temp.columns:
        # Falls die Tabelle breit ist (Tage als Zeilen, Uhrzeiten als Spalten)
        df_temp = df_temp.melt(id_vars=['Datum'], var_name='Uhrzeit', value_name='Temperatur')
        df_temp['Uhrzeit'] = df_temp['Uhrzeit'].astype(str).str[:5]
        
        # Echtes Datetime-Parsing für korrekte chronologische Sortierung!
        df_temp['timestamp'] = pd.to_datetime(
            df_temp['Datum'] + ' ' + df_temp['Uhrzeit'], 
            format='%d.%m.%Y %H:%M', 
            dayfirst=True, 
            errors='coerce'
        )
        df_temp = df_temp.dropna(subset=['timestamp'])
        df_temp = df_temp.sort_values('timestamp').set_index('timestamp')
    elif 'Temperatur' in df_temp.columns and len(df_temp) == 35040:
        # Falls die Tabelle bereits eine 1D-Zeitreihe ist
        pass
    else:
        raise ValueError("Unbekanntes CSV-Format. Erwarte Spalte 'Datum' oder exakt 35.040 'Temperatur'-Zeilen.")

    # 2. DataFrame auf das exakte 2025-Raster zwingen (35.040 Zeilen)
    ziel_index = pd.date_range(start="2025-01-01 00:00:00", end="2025-12-31 23:45:00", freq="15T")
    
    # Reindex & Füllen von eventuellen Lücken (Vorwärts- und dann Rückwärtsfüllen für die Ränder)
    df_temp = df_temp[~df_temp.index.duplicated(keep='first')]
    df_temp = df_temp.reindex(ziel_index)
    df_temp['Temperatur'] = df_temp['Temperatur'].ffill().bfill()

    temperatur = df_temp['Temperatur'].astype(float)

    if verbose:
        print("Berechne dynamischen Energieverbrauch (vektorisiert)...")

    # 3. Vektorisierte Berechnung des Wärmepumpen-Verbrauchs
    # Temperaturdifferenz zur Heizgrenze (alles über t_base wird zu 0.0 -> Heizung aus)
    delta_t = (t_base - temperatur).clip(lower=0.0)
    
    # COP (Leistungszahl) dynamisch nach Außentemperatur berechnen und begrenzen
    cop = (2.5 + 0.1 * temperatur).clip(lower=1.0, upper=5.5)
    
    # Theoretischer Bedarf (unskaliert)
    bedarf_roh = delta_t / cop
    summe_bedarf = bedarf_roh.sum()

    # 4. Skalierung auf den exakten Jahresbedarf
    if summe_bedarf > 0:
        verbrauch_kwh = (bedarf_roh / summe_bedarf) * jahresbedarf
    else:
        verbrauch_kwh = pd.Series(0.0, index=ziel_index)

    # 5. Output formatieren
    df_out = pd.DataFrame({
        "temperatur_c": temperatur,
        "verbrauch_kwh": verbrauch_kwh
    }, index=ziel_index)

    if df_out.isnull().values.any():
        raise ValueError("Berechnungsfehler: Der resultierende Wärmepumpen-DataFrame enthält NaN-Werte.")

    return df_out


# =====================================================================
# TEST- / EINGABEBEREICH
# =====================================================================
if __name__ == "__main__":
    datei_temperatur = "temperatur_verlauf_2025_15min.csv"

    # Erzeuge dynamisch Dummy-Daten, falls die CSV zum Testen nicht existiert
    if not os.path.exists(datei_temperatur):
        print(f"Erstelle temporäre Dummy-Datei '{datei_temperatur}' für den Test...")
        dummy_idx = pd.date_range(start="2025-01-01", periods=365, freq="D")
        dummy_data =[]
        for dt in dummy_idx:
            # Erzeuge ein sinus-artiges Temperaturprofil über das Jahr (Winter kalt, Sommer warm)
            tages_temp = 10 + 15 * np.sin((dt.dayofyear - 100) * 2 * np.pi / 365)
            row = {'Datum': dt.strftime('%d.%m.%Y')}
            for hour in range(24):
                for minute in [0, 15, 30, 45]:
                    time_str = f"{hour:02d}:{minute:02d}"
                    row[time_str] = tages_temp + np.random.uniform(-2, 2)
            dummy_data.append(row)
        pd.DataFrame(dummy_data).to_csv(datei_temperatur, sep=";", decimal=",", index=False)

    print("Starte Vektorisierungs-Test für 'waermepumpe.py' ...")
    
    try:
        bedarf_input = 4000.0
        
        df_wp = berechne_waermepumpe_verbrauch(
            temp_datei=datei_temperatur,
            t_base=15.0,
            jahresbedarf=bedarf_input,
            verbose=True
        )

        print("-" * 50)
        print(f"Dimension des DataFrames: {df_wp.shape} (Erwartet: 35040, 2)")
        print(f"Erster Wert (01.01. 00:00): {df_wp.iloc[0]['verbrauch_kwh']:.4f} kWh")
        print(f"Letzter Wert (31.12. 23:45): {df_wp.iloc[-1]['verbrauch_kwh']:.4f} kWh")
        print(f"Summe Verbrauch (Kontrolle): {df_wp['verbrauch_kwh'].sum():.1f} kWh")
        print("-" * 50)
        
    except Exception as e:
        print(f"Es gab einen unerwarteten Fehler: {e}")
