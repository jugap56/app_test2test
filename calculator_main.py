import pandas as pd
import numpy as np
import os
import PVAnlage as pv
import waermepumpe as wp
import eAuto as ea
import haushalt as ha

EINSPEISEVERGUETUNG_EUR = 0.0778  # 7,78 ct/kWh in Euro

def lade_strompreise_als_df(csv_dateiname: str) -> pd.DataFrame:
    """
    Lädt die Spotmarktpreise, erzwingt einen 2025-DatetimeIndex und 
    gibt die Preise in Euro/kWh zurück.
    """
    idx = pd.date_range("2025-01-01 00:00:00", "2025-12-31 23:45:00", freq="15min")

    if not os.path.exists(csv_dateiname):
        # Fallback auf Dummy-Preise (z.B. für Erstausführung ohne CSV)
        print("randomisierte Preise")
        dummy_preise = np.random.uniform(0.10, 0.40, len(idx))
        return pd.DataFrame({'preis_eur': dummy_preise}, index=idx)

    try:
        df = pd.read_csv(csv_dateiname, sep=';', decimal=',')
        preis_reihe = pd.to_numeric(df['Endkundenpreis_brutto (Cent/kWh)'], errors='coerce').fillna(32.4)

        # Umrechnung von Cent in Euro
        df_preise = pd.DataFrame({'preis_eur': preis_reihe.values / 100.0})

        # Länge anpassen, falls CSV abweicht
        if len(df_preise) > 35040:
            df_preise = df_preise.iloc[:35040]
        elif len(df_preise) < 35040:
            df_preise = df_preise.reindex(range(35040)).ffill().fillna(0.324)

        df_preise.index = idx
        print("Preise erfolgreich eingelesen")
        return df_preise
    except Exception as e:
        print(f"Fehler beim Laden der Preise: {e}")
        return pd.DataFrame({'preis_eur': 0.324}, index=idx)


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
    df_h = ha.generiere_haushaltslast(jahresverbrauch=ha_verbrauch)
    df_pv = pv.generiere_pv_ertrag(pv_kwp, pv_neigung, pv_ausrichtung)
    df_wp = wp.berechne_waermepumpe_verbrauch(temp_datei="2025_15min_temperaturverlauf.csv", jahresbedarf=wp_bedarf)
    df_ea = ea.generiere_lade_profil(ea_wochentag, ea_wochenende, ea_verbrauch, ea_leistung, ea_beginn)
    df_spot = lade_strompreise_als_df("2025_15min_spotmarktpreise_netto.csv")
    print(df_h)
    print(df_pv)
    print(df_wp)
    print(df_ea)
    print(df_sport)

    h_verbrauch = df_h['verbrauch_kwh']
    pv_ertrag = df_pv['ertrag_kwh']
    steuvb_verbrauch = df_wp['verbrauch_kwh'] + df_ea['verbrauch_kwh']
    spot = df_spot['preis_eur']

    # 2. Fachliche Verrechnung Schritt 1: Haushalt vs. PV
    netz_haushalt = (h_verbrauch - pv_ertrag).clip(lower=0.0)
    pv_ueberschuss = (pv_ertrag - h_verbrauch).clip(lower=0.0)

    # 8. duplizierte Index-Einträge entfernen -- Optimierungspotential, da redundant, wenn besser initialisiert wird
    steuvb_verbrauch = steuvb_verbrauch[~steuvb_verbrauch.index.duplicated(keep='first')]
    pv_ueberschuss = pv_ueberschuss[~pv_ueberschuss.index.duplicated(keep='first')]

    # 3. Fachliche Verrechnung Schritt 2: SteuVB nutzen den PV-Überschuss
    steuvb_aus_pv = steuvb_verbrauch.clip(upper=pv_ueberschuss)
    netz_steuvb = steuvb_verbrauch - steuvb_aus_pv
    pv_ins_netz = pv_ueberschuss - steuvb_aus_pv

    # 4. Batterie-Simulation (Vektorisiert via grouped cumsum)
    if speicher_max > 0 and speicher_leistung > 0:
        max_flow = speicher_leistung * 0.25  # Max Energie in 15 Min (kWh)

        charge_pot = pv_ins_netz.clip(upper=max_flow)
        discharge_pot = (netz_haushalt + netz_steuvb).clip(upper=max_flow)

        # Netto-Batteriestromfluss. GroupBy sorgt für täglichen Reset (näherungsweise realistisch für EFH)
        net_flow = charge_pot - discharge_pot
        soc = net_flow.groupby(net_flow.index.date).cumsum().clip(lower=0.0, upper=speicher_max)

        actual_flow = soc - soc.shift(1).fillna(0.0)

        batt_charge = actual_flow.clip(lower=0.0)
        batt_discharge = (-actual_flow).clip(lower=0.0)

        pv_ins_netz = pv_ins_netz - batt_charge

        # Entladung proportional auf Haushalt und SteuVB aufteilen
        summe_netz = (netz_haushalt + netz_steuvb).replace(0, 1) # Div by 0 verhindern
        ratio_h = netz_haushalt / summe_netz

        netz_haushalt = (netz_haushalt - (batt_discharge * ratio_h)).clip(lower=0.0)
        netz_steuvb = (netz_steuvb - (batt_discharge * (1.0 - ratio_h))).clip(lower=0.0)

    # 5. § 14a EnWG Modul-Preiskalkulation
    # Grundpreis Haushalt (immer Modul 1 als Basis)
    preis_h = (spot + 0.005).clip(lower=0.0)

    if enwg == 1:
        # Modul 1: Rabatt erfolgt gesamtjährig pauschal (wird in Streamlit-UI abgezogen)
        preis_steuvb = preis_h 
    elif enwg == 2:
        # Modul 2: 60% Netzentgelt-Rabatt. Spotpreis kann negativ wirken!
        preis_steuvb = spot + 0.010
    elif enwg == 3:
        # Modul 3: Zeitvariabel (Nachtstrom-Simulation). 90% Spot, mind. 0 ct.
        preis_steuvb = (spot * 0.9).clip(lower=0.0)
    else:
        preis_steuvb = preis_h

    # 6. Kosten ermitteln
    kosten_energie_eur = (netz_haushalt * preis_h) + (netz_steuvb * preis_steuvb)
    einspeise_ertrag_eur = pv_ins_netz * EINSPEISEVERGUETUNG_EUR

    summe_energie = kosten_energie_eur.sum() - einspeise_ertrag_eur.sum()

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

    #print(type(verbrauch))
    #print(type(pv_ertrag))
    # Beide Indizes auf tz-naive umstellen, da Zeitformat nicht übereinstimmt und somti subtraktion nicht möglich ist
    #verbrauch.index = verbrauch.index.tz_localize(None)
    #pv_ertrag.index = pv_ertrag.index.tz_localize(None)
    # Frequenz-Tag wiederherstellen, da dieser durch .index.tz_localize(None) verloren geht
    #verbrauch = verbrauch.asfreq('15min')
    #pv_ertrag = pv_ertrag.asfreq('15min')
    print(verbrauch.index)
    print(pv_ertrag.index)
    netzbezug = (verbrauch - pv_ertrag).clip(lower=0.0)
    pv_ins_netz = (pv_ertrag - verbrauch).clip(lower=0.0)

    if speicher_max > 0 and speicher_leistung > 0:
        max_flow = speicher_leistung * 0.25
        charge_pot = pv_ins_netz.clip(upper=max_flow)
        discharge_pot = netzbezug.clip(upper=max_flow)

        net_flow = charge_pot - discharge_pot
        soc = net_flow.groupby(net_flow.index.date).cumsum().clip(lower=0.0, upper=speicher_max)

        actual_flow = soc - soc.shift(1).fillna(0.0)

        batt_charge = actual_flow.clip(lower=0.0)
        batt_discharge = (-actual_flow).clip(lower=0.0)

        pv_ins_netz = pv_ins_netz - batt_charge
        netzbezug = netzbezug - batt_discharge

    # Statischer Preisansatz: 32.4 ct/kWh
    statischer_preis_eur = 0.324
    kosten_energie_eur = netzbezug * statischer_preis_eur
    einspeise_ertrag_eur = pv_ins_netz * EINSPEISEVERGUETUNG_EUR

    summe_energie = kosten_energie_eur.sum() - einspeise_ertrag_eur.sum()
    summe_energie += 123.09  # Statischer Grundpreis

    return round(summe_energie, 2)
