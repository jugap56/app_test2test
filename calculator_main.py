import pandas as pd
import numpy as np
import os
import PVAnlage as pv
import waermepumpe as wp
import eAuto as ea
import haushalt as ha

EINSPEISEVERGUETUNG_EUR = 0.0778  # 7,78 ct/kWh in Euro
EINSPEISEVERGUETUNG_CT = 7.78  # 7,78 ct/kWh in Euro


def lade_strompreise_als_df(csv_dateiname: str) -> pd.DataFrame:
    """
    Lädt die Spotmarktpreise, erzwingt einen 2025-DatetimeIndex und 
    gibt die Preise in Euro/kWh zurück.
    """
    idx = pd.date_range("2025-01-01 00:00:00", "2025-12-31 23:45:00", freq="15min")

    #if not os.path.exists(csv_dateiname):
    #    # Fallback auf Dummy-Preise (z.B. für Erstausführung ohne CSV)
    #    print("randomisierte Preise")
    #    dummy_preise = np.random.uniform(0.10, 0.40, len(idx))
    #    return pd.DataFrame({'preis_eur': dummy_preise}, index=idx)

    try:
        df_preise = pd.read_csv(csv_dateiname, sep=';', decimal=',')
        #preis_reihe = pd.to_numeric(df['Endkundenpreis_brutto (Cent/kWh)'], errors='coerce').fillna(32.4)
        #preis_reihe = pd.to_numeric(df['Endkundenpreis_brutto (Cent/kWh)'], errors='coerce').fillna(0.)
        if df_preise.isnull().values.any():
            print(df_preise)
            raise ValueError("Berechnungsfehler: Der resultierende Wärmepumpen-DataFrame enthält NaN-Werte.")

        # Umrechnung von Cent in Euro
        #df_preise = pd.DataFrame({'preis_eur': preis_reihe.values / 100.0})

        # Länge anpassen, falls CSV abweicht
        #if len(df_preise) > 35040:
        #    df_preise = df_preise.iloc[:35040]
        #elif len(df_preise) < 35040:
        #    df_preise = df_preise.reindex(range(35040)).ffill().fillna(0.324)

        df_preise.index = idx
        print("Preise erfolgreich eingelesen")
        return df_preise
    except Exception as e:
        print(f"Fehler beim Laden der Preise: {e}")
        return pd.DataFrame({'preis_eur': 0.324}, index=idx)

def calculate_battery_pandas(df: pd.DataFrame, speicher_max: float, speicher_leistung: float, eff_in=0.9, eff_out=1.0) -> pd.DataFrame:
    """
    Erwartet einen DataFrame mit den Spalten 'pv_ueberschuss' und 'last_bedarf'.
    Gibt den DataFrame zurück, angereichert mit 'soc', 'batt_charge' und 'batt_discharge'.
    """
    # 1. Rohe NumPy-Arrays aus Pandas extrahieren (für maximale Geschwindigkeit)
    pv_val = df['pv_ueberschuss'].values
    load_val = df['last_bedarf'].values
    
    # 2. Pre-Allocation der Ergebnis-Arrays
    n = len(df)
    soc = np.zeros(n, dtype=np.float32)
    charge = np.zeros(n, dtype=np.float32)
    discharge = np.zeros(n, dtype=np.float32)
    
    max_flow = speicher_leistung * 0.25  # Max kWh pro 15 Min
    current_soc = 0.0
    
    # 3. Die schnelle C-ähnliche Schleife über die NumPy-Arrays
    for i in range(n):
        actual_in = 0
        actual_out = 0
        if pv_val[i] > 0:
            # Wie viel kann der Wechselrichter / die Batterie maximal aufnehmen?
            space_left = (speicher_max - current_soc) / eff_in
            actual_in = min(pv_val[i], max_flow, space_left)
            
            charge[i] = actual_in
            current_soc += actual_in * eff_in
            
        elif load_val[i] > 0:
            # Wie viel kann die Batterie abgeben?
            energy_avail = current_soc * eff_out
            actual_out = min(load_val[i], max_flow, energy_avail)
            
            discharge[i] = actual_out
            current_soc -= actual_out / eff_out
            
        soc[i] = current_soc
        print("SOC: ", soc[i])
        print("in: ", actual_in)
        print("load: ", actual_out)

    raise ValueError
    # 4. Ergebnisse als neue Pandas-Spalten in den DataFrame einfügen
    df_result = df.copy()
    df_result['soc'] = soc
    df_result['batt_charge'] = charge
    df_result['batt_discharge'] = discharge
    
    return df_result


def calculate_dynamic(
    wp_bedarf: float, pv_neigung: float, pv_ausrichtung: float, pv_kwp: float, 
    ea_wochentag: float, ea_wochenende: float, ea_verbrauch: float, ea_leistung: float, 
    ea_beginn: int, ha_verbrauch: float, speicher_max: float, speicher_leistung: float, enwg: int
) -> float:
    """
    Vektorisierte Hauptkalkulation für dynamische Tarife inkl. § 14a EnWG Modul 1-3.
    Reihenfolge: Haushalt decken -> PV-Überschuss für SteuVB nutzen -> Batterie laden/entladen.
    """
    # 1. Profile generieren (alle geben DataFrames mit 2025-DatetimeIndex zurück)
    # aufruf der Funktionen mit benötigten Parametern aus Modulen und speichern in DataFrames
    df_h = ha.generiere_haushaltslast(jahresverbrauch=ha_verbrauch)
    df_pv = pv.generiere_pv_ertrag(pv_kwp, pv_neigung, pv_ausrichtung)
    df_wp = wp.berechne_waermepumpe_verbrauch(temp_datei="2025_15min_temperaturverlauf.csv", jahresbedarf=wp_bedarf)
    df_ea = ea.generiere_lade_profil(ea_wochentag, ea_wochenende, ea_verbrauch, ea_leistung, ea_beginn)
    df_spot = lade_strompreise_als_df("2025_15min_spotmarktpreise_netto.csv")
    #print("Haushalt:",df_h)
    #print("PV:",df_pv)
    #print("WP:",df_wp)
    #print("ea:",df_ea)
    #print("spot: ",df_spot)

    h_verbrauch = df_h['verbrauch_kwh']
    pv_ertrag = df_pv['ertrag_kwh']
    steuvb_verbrauch = df_wp['verbrauch_kwh'] + df_ea['verbrauch_kwh']
    spot = df_spot['Spotmarktpreis_netto (Cent/kWh)']

    # 2. Fachliche Verrechnung Schritt 1: Haushalt vs. PV
    netz_haushalt = (h_verbrauch - pv_ertrag).clip(lower=0.0)        # wenn pv haus deckt, dann wert = 0. Sonst fehlender Betrag (abdeckung -> netz/speicher).
    pv_ueberschuss = (pv_ertrag - h_verbrauch).clip(lower=0.0)        # 

    # 8. duplizierte Index-Einträge entfernen -- Optimierungspotential, da redundant, wenn besser initialisiert wird
    steuvb_verbrauch = steuvb_verbrauch[~steuvb_verbrauch.index.duplicated(keep='first')]
    pv_ueberschuss = pv_ueberschuss[~pv_ueberschuss.index.duplicated(keep='first')]

    # 3. Fachliche Verrechnung Schritt 2: SteuVB nutzen den PV-Überschuss
      # steuvb_verbrauch wird auf pv_ueberschuss begrenzt
    steuvb_aus_pv = steuvb_verbrauch.clip(upper=pv_ueberschuss) 
    netz_steuvb = steuvb_verbrauch - steuvb_aus_pv
    pv_ins_netz_vor_batt = pv_ueberschuss - steuvb_aus_pv

# 4. Batterie-Simulation im Pandas-Style
    if speicher_max > 0 and speicher_leistung > 0:
        
        # Temporären DataFrame für die Speicher-Inputs bauen
        df_batt_input = pd.DataFrame({
            'pv_ueberschuss': pv_ins_netz_vor_batt,
            'last_bedarf': netz_haushalt + netz_steuvb
        })
        
        # Aufruf der optimierten Pandas/NumPy-Funktion
        df_batt = calculate_battery_pandas(df_batt_input, speicher_max, speicher_leistung)
        
        # Jetzt arbeiten wir ganz normal mit Pandas-Series weiter!
        pv_ins_netz = pv_ins_netz_vor_batt - df_batt['batt_charge']
        batt_discharge = df_batt['batt_discharge']
        
        # Verteilung des entladenen Stroms auf Haushalt und SteuVB
        batt_to_haushalt = netz_haushalt.clip(upper=batt_discharge)
        remaining_batt_discharge = batt_discharge - batt_to_haushalt

        netz_haushalt = netz_haushalt - batt_to_haushalt
        netz_steuvb = (netz_steuvb - remaining_batt_discharge).clip(lower=0.0)
    else:
        pv_ins_netz = pv_ins_netz_vor_batt

    # 5. § 14a EnWG Modul-Preiskalkulation
    # Fix-Kosten für Spotpreise
    basisverbrauch = 2.5
    arbeitspreis = 9.29
    konzession = 2.39
    kwkumlage = 0.446
    netznutzung = 1.559
    offshore = 0.941
    stromsteuer = 2.05
    summe_dyn_fix = basisverbrauch + arbeitspreis + konzession + kwkumlage + netznutzung + offshore + stromsteuer
    summe_enwg_2 = basisverbrauch + arbeitspreis*0.4 + konzession + kwkumlage + netznutzung + offshore + stromsteuer

    # Grundpreis Haushalt (immer Modul 1 als Basis)
    preis_h = (spot + summe_dyn_fix).clip(lower=0.0)

    if enwg == 1:
        # Modul 1: Rabatt erfolgt gesamtjährig pauschal (wird in Streamlit-UI abgezogen)
        preis_steuvb = preis_h 
    elif enwg == 2:
        # Modul 2: 60% Netzentgelt-Rabatt. Spotpreis kann negativ wirken!
        preis_steuvb = spot + summe_enwg_2
    elif enwg == 3:
        # Modul 3: Zeitvariabel (Nachtstrom-Simulation). 90% Spot, mind. 0 ct.
        preis_steuvb = (spot * 0.9).clip(lower=0.0)
    #else:
    #    preis_steuvb = preis_h

    # 6. Kosten ermitteln
    kosten_energie_ct = (netz_haushalt * preis_h) + (netz_steuvb * preis_steuvb)
    einspeise_ertrag_ct = pv_ins_netz * EINSPEISEVERGUETUNG_CT

    summe_energie = kosten_energie_ct.sum()*1.19 - einspeise_ertrag_ct.sum()

    #Umrechnung in Euro und MwSt.
    summe_energie = round((summe_energie / 100),2)
    
    # 7. Fixkosten & Zähler-Infrastruktur berechnen
    gesamt_verbrauch = netz_haushalt.sum() + netz_steuvb.sum()

    # Basisgrundpreis für Haushalt
    if gesamt_verbrauch > 10000:
        summe_energie += 133.82
    elif gesamt_verbrauch > 6000:
        summe_energie += 123.82
    else:
        summe_energie += 113.82

    # Zusätzlicher Zähler für Modul 2/3 (SteuVB separat erfasst)
    if enwg in [2, 3]:
        v_steuvb = netz_steuvb.sum()
        if v_steuvb > 10000:
            summe_energie += 50.0
        elif v_steuvb > 6000:
            summe_energie += 40.0
        else:
            summe_energie += 30.0
            
    return round(summe_energie, 2)


def calculate_static(
    wp_bedarf: float, pv_neigung: float, pv_ausrichtung: float, pv_kwp: float, 
    ea_wochentag: float, ea_wochenende: float, ea_verbrauch: float, ea_leistung: float, 
    ea_beginn: int, ha_verbrauch: float, speicher_max: float, speicher_leistung: float
) -> float:
    """
    Vektorisierte Kalkulation für den statischen, klassischen Fixtarif.
    """
    df_h = ha.generiere_haushaltslast(jahresverbrauch=ha_verbrauch)
    df_pv = pv.generiere_pv_ertrag(pv_kwp, pv_neigung, pv_ausrichtung)
    df_wp = wp.berechne_waermepumpe_verbrauch(temp_datei="2025_15min_temperaturverlauf.csv", jahresbedarf=wp_bedarf)
    df_ea = ea.generiere_lade_profil(ea_wochentag, ea_wochenende, ea_verbrauch, ea_leistung, ea_beginn)

    verbrauch = df_h['verbrauch_kwh'] + df_wp['verbrauch_kwh'] + df_ea['verbrauch_kwh']
    pv_ertrag = df_pv['ertrag_kwh']

    netzbezug = (verbrauch - pv_ertrag).clip(lower=0.0)
    pv_ins_netz_vor_batt = (pv_ertrag - verbrauch).clip(lower=0.0)

    # --- NEUE BATTERIELOGIK HIER EINFÜGEN ---
    if speicher_max > 0 and speicher_leistung > 0:
        df_batt_input = pd.DataFrame({
            'pv_ueberschuss': pv_ins_netz_vor_batt,
            'last_bedarf': netzbezug
        })
        
        df_batt = calculate_battery_pandas(df_batt_input, speicher_max, speicher_leistung)
        
        pv_ins_netz = pv_ins_netz_vor_batt - df_batt['batt_charge']
        netzbezug = netzbezug - df_batt['batt_discharge']
    else:
        pv_ins_netz = pv_ins_netz_vor_batt

    # Statischer Preisansatz: 32.4 ct/kWh
    statischer_preis_eur = 0.324
    kosten_energie_eur = netzbezug * statischer_preis_eur
    einspeise_ertrag_eur = pv_ins_netz * EINSPEISEVERGUETUNG_EUR

    summe_energie = kosten_energie_eur.sum() - einspeise_ertrag_eur.sum()
    summe_energie += 123.09  # Statischer Grundpreis

    return round(summe_energie, 2)
