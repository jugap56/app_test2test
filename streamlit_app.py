import streamlit as st
import pandas as pd
import numpy as np
import plotly.express as px
import plotly.graph_objects as go
import calculator_main as cm

# --- MAIN STREAMLIT APP ---
def main():
    st.set_page_config(page_title="Studi Energy Check - Tarif-Check", layout="wide")
    
    # Header Design
    st.markdown("<h1 style='color: #d50037;'>Der Stecker Checker</h1> <br> <h2>🔌 Deine Dose – Deine Regeln 🔌</h2>", unsafe_allow_html=True)
    st.title("Dynamik-Rechner für Einfamilienhäuser")

    with st.sidebar:
        st.header("Konfiguration")
        
        # --- 1. Hausstrom ---
        hausstrom_optionen = {
            "1 Person (1.500 kWh)": 1500,
            "2 Personen (2.500 kWh)": 2500,
            "3 Personen (3.500 kWh)": 3500,
            "4 Personen (4.500 kWh)": 4500,
            "Eigene Eingabe...": "custom"
        }

        auswahl_hausstrom = st.selectbox(
            "Welchen Energiebedarf hat Ihr Haushalt?", 
            options=list(hausstrom_optionen.keys()),
            index=2 
        )

        if hausstrom_optionen[auswahl_hausstrom] == "custom":
            h0 = st.number_input(
                "Eigener Energiebedarf (volle kWh)", 
                min_value=500, max_value=20000, value=3500, step=100
            )
        else:
            h0 = hausstrom_optionen[auswahl_hausstrom]
            
        st.divider()

        # --- 2. PV-Anlage ---
        ar_dict = {"Süden":0, "Süd-Westen":45, "Westen":90, "Nord-Westen":135, "Norden":180, "Nord-Osten":-135, "Osten":-90, "Süd-Osten":-45}
        pv, dn, ar, bat_capacity, bat_power, ar_deg = 0, 30, "Süden", 0, 0, 0
        
        hat_pv = st.radio("Haben Sie eine PV-Anlage im Einsatz?", ["Ja", "Nein"], index=1)
        if hat_pv == "Ja":
            pv = st.number_input("PV-Leistung [kWp]", 1, 25, 10)
            dn = st.number_input("Dachneigung [°]", 0, 60, 30)
            ar = st.selectbox("Ausrichtung",["Norden", "Nord-Osten", "Osten", "Süd-Osten", "Süden", "Süd-Westen", "Westen", "Nord-Westen"], index=4)
            ar_deg = ar_dict[ar]
            
            # --- 3. Speicher ---
            hat_speicher = st.radio("Haben Sie einen dazugehörigen Energiespeicher im Einsatz?", ["Ja", "Nein"], index=1)
            if hat_speicher == "Ja":
                bat_capacity = st.number_input("Speicherkapazität [kWh]", 1, 100, 10)
                bat_power = st.number_input("Abgabeleistung [kW]", 0, 15, 3) 
                
        st.divider()

        # --- 4. E-Auto ---
        ev_charge_hour = 0
        wallbox_power = 0
        km_woche, km_wochenende, verbrauch_100km = 0, 0, 0
        
        hat_ev = st.radio("Besitzen Sie ein E-Auto, welches Sie zuhause laden?",["Ja", "Nein"], index=1)
        if hat_ev == "Ja":
            km_woche = st.number_input("Fahrstrecke pro Wochentag [km]", min_value=0, value=40, step=5)
            km_wochenende = st.number_input("Fahrstrecke pro Tag am Wochenende [km]", min_value=0, value=20, step=5)
            verbrauch_100km = st.number_input("Verbrauch auf 100km [kWh]", min_value=0.0, value=15.0, step=0.1)
            
            uhrzeiten =[f"{i:02d}:00" for i in range(24)]
            auswahl_zeit = st.selectbox("Wann laden Sie normalerweise Ihr Auto?", options=uhrzeiten, index=18)
            ev_charge_hour = int(auswahl_zeit.split(":")[0])
            
            wallbox_power = st.selectbox("Welche Ausgangsleistung liefert Ihre Wallbox? [kW]", [11, 22])

        st.divider()

        # --- 5. Wärmepumpe ---
        hp = 0
        hat_wp = st.radio("Heizen Sie mit einer Wärmepumpe?", ["Ja", "Nein"], index=1)
        if hat_wp == "Ja":
            wp_bekannt = st.radio("Kennen Sie den jährlichen Energieverbrauch [kWh] Ihrer Wärmepumpe?",["Ja", "Nein (Rechnung mit Fixwert)"], index=1)
            if wp_bekannt == "Ja":
                hp = st.number_input("Wie viel Energie [kWh] benötigen Sie zum Heizen?", min_value=0, value=5000, step=100)
            else:
                hp = 5000 # Fixwert
        
        st.divider()
        
        calc_btn = st.button("🚀 Berechnung starten", type="primary", use_container_width=True)


    # --- HAUPTBEREICH (Ergebnisse) ---
    if calc_btn:
        with st.spinner("Analysiere Daten und berechne Tarife für 35.040 Intervalle..."):
            # 1. Alle Szenarien simultan berechnen (Dank Vektorisierung geht das in Sekundenbruchteilen!)
            # Statisch
            cost_static = cm.calculate_static(hp, dn, ar_deg, pv, km_woche, km_wochenende, verbrauch_100km, wallbox_power, ev_charge_hour, h0, bat_capacity, bat_power)
            
            # §14a EnWG Modul 1 (Pauschale Gutschrift von 168€ wird nachträglich abgezogen)
            cost_mod1 = cm.calculate_dynamic(hp, dn, ar_deg, pv, km_woche, km_wochenende, verbrauch_100km, wallbox_power, ev_charge_hour, h0, bat_capacity, bat_power, 1)
            cost_mod1 = round(cost_mod1 - 168.0, 2)
            
            # §14a EnWG Modul 2 (Prozentualer Rabatt auf Netzentgelte für SteuVB)
            cost_mod2 = cm.calculate_dynamic(hp, dn, ar_deg, pv, km_woche, km_wochenende, verbrauch_100km, wallbox_power, ev_charge_hour, h0, bat_capacity, bat_power, 2)
            
            # §14a EnWG Modul 3 (Zeitvariable Netzentgelte)
            cost_mod3 = cm.calculate_dynamic(hp, dn, ar_deg, pv, km_woche, km_wochenende, verbrauch_100km, wallbox_power, ev_charge_hour, h0, bat_capacity, bat_power, 3)

        # 2. Tooltip / Erklärung der Module
        st.markdown("""
        <style>
        .tooltip { position: relative; display: inline-block; cursor: pointer; color: #d50037; font-weight: bold; }
        .tooltip .tooltiptext { visibility: hidden; width: 400px; background-color: #f9f9f9; color: #000; text-align: left; border-radius: 8px; border: 1px solid #ccc; padding: 15px; position: absolute; z-index: 1; bottom: 125%; left: 0; box-shadow: 0px 4px 12px rgba(0,0,0,0.15); font-size: 14px; font-weight: normal; }
        .tooltip:hover .tooltiptext { visibility: visible; }
        </style>
        <h3>📊 Szenarien-Vergleich: Ihr optimaler Tarif <span class="tooltip">ⓘ Erklärung der Module<div class="tooltiptext">
          <b><ins>Modul 1 (Pauschale)</ins></b><br>Einmal jährlich Gutschrift über 168€, unabhängig von der Verbrauchszeit.<br><br>
          <b><ins>Modul 2 (Prozentual)</ins></b><br>60% Rabatt auf Netzentgelte der steuerbaren Verbraucher. Lohnt sich für Vielfahrer und große Wärmepumpen.<br><br>
          <b><ins>Modul 3 (Zeitvariabel)</ins></b><br>Nachts (23-05 Uhr) niedrigere Netzentgelte. Ideal für klassisches Nachtladen.
        </div></span></h3>
        """, unsafe_allow_html=True)

        # 3. 4-Spalten Ansicht & Gewinner-Ermittlung
        costs_dict = {
            "Fixer Tarif (Basis)": cost_static,
            "§14a Modul 1 (Pauschale)": cost_mod1,
            "§14a Modul 2 (Prozentual)": cost_mod2,
            "§14a Modul 3 (Zeitvariabel)": cost_mod3
        }
        
        best_module = min(costs_dict, key=costs_dict.get)
        cols = st.columns(4)

        for col, (name, cost) in zip(cols, costs_dict.items()):
            with col:
                if name == best_module:
                    # Highlight für den günstigsten Tarif
                    st.markdown(
                        f"""
                        <div style="background-color: #e6f4ea; border: 2px solid #28a745; border-radius: 10px; padding: 15px; text-align: center; height: 190px; display: flex; flex-direction: column; justify-content: space-between; align-items: center;">
                            <h4 style="color: #155724; margin: 0;">{name}</h4>
                            <h2 style="color: #28a745; margin: 0;">{cost:,.2f} €</h2>
                            <div><span style="background-color: #28a745; color: white; padding: 5px 10px; border-radius: 15px; font-size: 12px; font-weight: bold;">🏆 Günstigste Wahl</span></div>
                        </div>
                        """, unsafe_allow_html=True
                    )
                else:
                    # Standard-Design für die anderen
                    st.markdown(
                        f"""
                        <div style="background-color: #f8f9fa; border: 1px solid #dee2e6; border-radius: 10px; padding: 15px; text-align: center; height: 190px; display: flex; flex-direction: column; justify-content: space-between; align-items: center;">
                            <h4 style="color: #495057; margin: 0;">{name}</h4>
                            <h2 style="color: #343a40; margin: 0;">{cost:,.2f} €</h2>
                            <div style="height: 24px;"></div> </div>
                        """, unsafe_allow_html=True
                    )
        
        st.divider()

        # 4. Smart-Charging Graph (nur wenn E-Auto vorhanden)
        if hat_ev == "Ja":
            st.markdown("### 🚗 Smart-Charging: Der optimale Ladezeitpunkt")
            
            # Lade Spotmarktpreise und bilde den Tagesdurchschnitt
            df_spot = cm.lade_strompreise_als_df("2025_15min_spotmarktpreise_netto.csv")
            # Gruppiere nach Stunde und rechne in Cent/kWh um
            avg_hourly = df_spot.groupby(df_spot.index.hour)['Spotmarktpreis_netto (Cent/kWh)'].mean() * 100 
            
            best_hour = avg_hourly.idxmin()
            
            # Interaktiver Plotly-Graph
            fig = go.Figure()
            
            # Farben: Günstigste Stunde in Grün, Rest in Blau
            colors =['#28a745' if i == best_hour else '#1f77b4' for i in range(24)]
            
            fig.add_trace(go.Bar(
                x=[f"{i:02d}:00" for i in range(24)],
                y=avg_hourly.values,
                marker_color=colors,
                text=[f"{v:.1f} ct" for v in avg_hourly.values],
                textposition='auto'
            ))
            
            fig.update_layout(
                title="Durchschnittlicher Spotmarkt-Strompreis im Tagesverlauf",
                xaxis_title="Uhrzeit",
                yaxis_title="Strompreis (rein Energie) [ct/kWh]",
                template="plotly_white",
                showlegend=False,
                margin=dict(t=50, b=0, l=0, r=0)
            )
            
            st.plotly_chart(fig, use_container_width=True)
            
            st.success(f"💡 **Empfehlung:** Historisch gesehen ist Strom um **{best_hour:02d}:00 Uhr** am günstigsten. Wenn Sie den Ladebeginn Ihrer Wallbox auf diese Uhrzeit programmieren, maximieren Sie Ihren finanziellen Vorteil in dynamischen Tarifen.")

    else:
        # Startbildschirm (wenn noch nichts geklickt wurde)
        st.info("👈 Bitte konfigurieren Sie Ihren Haushalt auf der linken Seite und klicken Sie anschließend auf 'Berechnung starten'.")


if __name__ == "__main__":
    main()
