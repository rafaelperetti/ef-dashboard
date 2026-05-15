"""
Dashboard de disponibilidad Business — EF Scraper
Lee datos de Google Sheets en tiempo real
"""

import streamlit as st
import pandas as pd
import gspread
from google.oauth2.service_account import Credentials
from datetime import datetime

# ── Config ────────────────────────────────────────────────────────────────────
GSHEET_ID = "1gHQNU564qiWArnZYLjXfJm7wbi49JRtpuQgKEwTml-A"

ROUTES = ["BOG-SCL", "SCL-BOG", "BOG-MAD", "MAD-BOG", "BOG-GRU", "GRU-BOG"]

FLIGHT_TIMES = {
    "LA575": "06:35", "LA711": "23:10",
    "LA710": "16:30", "LA572": "23:05",
    "IB152": "17:30", "IB156": "12:35", "IB154": "21:40",
    "IB151": "12:10", "IB153": "16:25", "IB155": "00:10",
    "UX194": "20:15", "UX193": "15:15",
    "AV182": "16:25", "AV46":  "07:45", "AV26": "13:35", "AV10": "21:35",
    "AV183": "11:05", "AV47":  "02:20", "AV27": "08:10", "AV11": "17:20",
}

# Clase relevante por aerolínea
def key_class(vuelo, route):
    if "GRU" in route and vuelo.startswith("LA"):
        return "W"
    if vuelo.startswith("AV"):
        return "C"
    return "J"

DOW_ES = {"Mon":"Lunes","Tue":"Martes","Wed":"Miércoles",
           "Thu":"Jueves","Fri":"Viernes","Sat":"Sábado","Sun":"Domingo"}

# ── Google Sheets ─────────────────────────────────────────────────────────────
@st.cache_data(ttl=300)  # refresca cada 5 min
def load_sheet(sheet_name):
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    # Try Streamlit Secrets first (cloud), fallback to local credentials.json
    try:
        creds = Credentials.from_service_account_info(
            st.secrets["gcp_service_account"], scopes=scopes)
    except Exception:
        creds = Credentials.from_service_account_file("credentials.json", scopes=scopes)

    client = gspread.authorize(creds)
    gsheet = client.open_by_key(GSHEET_ID)
    try:
        ws   = gsheet.worksheet(sheet_name)
        data = ws.get_all_values()
        if len(data) < 2:
            return pd.DataFrame()
        df = pd.DataFrame(data[1:], columns=data[0])
        for col in ["J","C","D","I","W","P"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0).astype(int)
        df["Timestamp consulta"] = pd.to_datetime(df["Timestamp consulta"], errors="coerce")
        df["flight_date"] = pd.to_datetime(
            df["Fecha vuelo"].str.extract(r"(\d{4}-\d{2}-\d{2})")[0], errors="coerce")
        df["dow"] = df["Fecha vuelo"].str.extract(r"\((\w+)\)")[0]
        df = df[df["Vuelo"].notna() & (df["Vuelo"] != "Sin datos")]
        return df
    except Exception as e:
        st.error(f"Error cargando '{sheet_name}': {e}")
        return pd.DataFrame()


def compute_hours_before(df, flight_times):
    df = df.copy()
    df["flight_dt"] = pd.NaT
    for vuelo, dep in flight_times.items():
        h, m = map(int, dep.split(":"))
        mask = df["Vuelo"] == vuelo
        df.loc[mask, "flight_dt"] = df.loc[mask, "flight_date"].apply(
            lambda d: d.replace(hour=h, minute=m) if pd.notna(d) else pd.NaT)
    df["hours_before"] = (df["flight_dt"] - df["Timestamp consulta"]).dt.total_seconds() / 3600
    return df[(df["hours_before"] >= 0) & (df["hours_before"] <= 220)]


# ── UI ────────────────────────────────────────────────────────────────────────
st.set_page_config(page_title="EF Availability", page_icon="✈️", layout="wide")

st.title("✈️ Disponibilidad Business")
st.caption(f"Datos actualizados cada 5 min · Última carga: {datetime.now().strftime('%H:%M')}")

# Sidebar filters
with st.sidebar:
    st.header("Filtros")
    route = st.selectbox("Ruta", ROUTES)
    st.divider()
    view = st.radio("Vista", ["📊 Heatmap por fecha", "📈 Curva vs tiempo"])

df = load_sheet(route)

if df.empty:
    st.warning("Sin datos para esta ruta todavía.")
    st.stop()

flights   = sorted(df["Vuelo"].unique())
all_days  = sorted(df["dow"].dropna().unique())
days_opts = [DOW_ES.get(d, d) for d in all_days]
days_map  = {DOW_ES.get(d, d): d for d in all_days}

with st.sidebar:
    sel_flights = st.multiselect("Vuelos", flights, default=flights[:2] if len(flights) >= 2 else flights)
    sel_days_es = st.multiselect(
        "Día del vuelo",
        days_opts,
        default=days_opts,
        help="Filtra las fechas de vuelo por día de semana. Afecta el heatmap."
    )
    sel_days    = [days_map[d] for d in sel_days_es]

df_f = df[df["Vuelo"].isin(sel_flights) & df["dow"].isin(sel_days)]

if df_f.empty:
    st.warning("Sin datos con los filtros seleccionados.")
    st.stop()

# ── Vista 1: Heatmap ──────────────────────────────────────────────────────────
if view == "📊 Heatmap por fecha":
    st.subheader(f"Disponibilidad por fecha — {route}")
    st.caption("Última lectura disponible por vuelo y fecha. Verde = amplio, rojo = cerrado.")

    latest = (df_f.sort_values("Timestamp consulta")
                  .groupby(["Fecha vuelo", "Vuelo"])
                  .last()
                  .reset_index())

    for vuelo in sel_flights:
        sub = latest[latest["Vuelo"] == vuelo].sort_values("flight_date")
        if sub.empty:
            continue
        cls = key_class(vuelo, route)
        if cls not in sub.columns:
            cls = "J"

        dep = FLIGHT_TIMES.get(vuelo, "")
        st.markdown(f"**{vuelo}** {dep} — clase `{cls}`")

        # Build a single-row dataframe for display
        dates = [d.split(" ")[0] if " " in d else d for d in sub["Fecha vuelo"].tolist()]
        vals  = sub[cls].tolist()
        row_df = pd.DataFrame([vals], columns=dates)

        def color_cell(val):
            if val >= 7:   return "background-color:#4CAF50;color:white;font-weight:bold"
            elif val >= 5: return "background-color:#C0DD97;color:#1a3a00;font-weight:bold"
            elif val >= 3: return "background-color:#FAC775;color:#4a2800;font-weight:bold"
            elif val >= 1: return "background-color:#F7C1C1;color:#5a0000;font-weight:bold"
            else:          return "background-color:#FCEBEB;color:#8a0000;font-weight:bold"

        styled = row_df.style.map(color_cell).format("{:.0f}")
        st.dataframe(styled, use_container_width=True, hide_index=True)
        st.divider()

# ── Vista 2: Curva vs tiempo ──────────────────────────────────────────────────
else:
    st.subheader(f"Disponibilidad vs tiempo antes del vuelo — {route}")
    st.caption("Promedio de disponibilidad según horas restantes. Eje invertido: derecha = momento del vuelo.")

    df_h = compute_hours_before(df_f, FLIGHT_TIMES)

    if df_h.empty:
        st.warning("Sin datos suficientes para la curva.")
        st.stop()

    with st.sidebar:
        sel_days2_es = st.multiselect(
            "Día del vuelo para curva y tabla",
            days_opts,
            default=sel_days_es,
            key="curve_days",
            help="Seleccioná uno o más días para ver cómo evoluciona la disponibilidad promedio antes de vuelos en esos días. Ej: solo 'Domingo' muestra el patrón típico de los vuelos del domingo."
        )
        sel_days2 = [days_map[d] for d in sel_days2_es]

    df_h = df_h[df_h["dow"].isin(sel_days2)] if sel_days2 else df_h

    fig_data = {}
    for i, vuelo in enumerate(sel_flights):
        sub = df_h[df_h["Vuelo"] == vuelo].copy()
        if sub.empty:
            continue
        cls = key_class(vuelo, route)
        if cls not in sub.columns:
            cls = "J"
        sub["bucket"] = (sub["hours_before"] / 6).round() * 6
        grouped = (sub.groupby("bucket")[cls]
                      .mean()
                      .reset_index()
                      .sort_values("bucket", ascending=True))
        dep = FLIGHT_TIMES.get(vuelo, "")
        label = f"{vuelo} {dep} ({cls})"
        fig_data[label] = grouped.set_index("bucket")[cls]

    if fig_data:
        chart_df = pd.DataFrame(fig_data)
        chart_df.index.name = "Horas antes del vuelo"
        # Sort descending = más horas a la izquierda, vuelo a la derecha
        chart_df = chart_df.sort_index(ascending=False)
        st.line_chart(chart_df, use_container_width=True, height=400)
        st.caption("← Más días antes del vuelo  |  Momento del vuelo →")

    st.caption("⚠️ Con pocos días de datos las curvas son indicativas. Se van a afinar con más semanas de scraping.")

    # ── Tabla de cortes temporales ────────────────────────────────────────────
    st.subheader("Disponibilidad promedio por corte de tiempo")
    st.caption("Promedio de la clase menos restrictiva según cuántas horas faltaban para el vuelo.")

    CUTS = {
        "7d": (7*24-12, 7*24+12),
        "6d": (6*24-12, 6*24+12),
        "5d": (5*24-12, 5*24+12),
        "4d": (4*24-12, 4*24+12),
        "3d": (3*24-12, 3*24+12),
        "2d": (2*24-12, 2*24+12),
        "1d": (1*24-12, 1*24+12),
        "12h": (9, 15),
        "6h":  (3, 9),
        "3h":  (0, 5),
    }

    rows = []
    for vuelo in sel_flights:
        sub = df_h[df_h["Vuelo"] == vuelo].copy()
        if sub.empty:
            continue
        cls = key_class(vuelo, route)
        if cls not in sub.columns:
            cls = "J"
        dep = FLIGHT_TIMES.get(vuelo, "")
        row = {"Vuelo": f"{vuelo} {dep}", "Clase": cls}
        for cut_label, (lo, hi) in CUTS.items():
            window = sub[(sub["hours_before"] >= lo) & (sub["hours_before"] <= hi)]
            if len(window) > 0:
                row[cut_label] = round(window[cls].mean(), 1)
            else:
                row[cut_label] = None
        rows.append(row)

    if rows:
        tbl = pd.DataFrame(rows).set_index("Vuelo")

        def color_cut(val):
            if pd.isna(val):   return "color:#ccc"
            if val >= 7:       return "background-color:#4CAF50;color:white;font-weight:bold"
            elif val >= 5:     return "background-color:#C0DD97;color:#1a3a00;font-weight:bold"
            elif val >= 3:     return "background-color:#FAC775;color:#4a2800;font-weight:bold"
            elif val >= 1:     return "background-color:#F7C1C1;color:#5a0000;font-weight:bold"
            else:              return "background-color:#FCEBEB;color:#8a0000;font-weight:bold"

        num_cols = [c for c in tbl.columns if c != "Clase"]
        styled_tbl = (tbl.style
                        .map(color_cut, subset=num_cols)
                        .format("{:.1f}", subset=num_cols, na_rep="—"))
        st.dataframe(styled_tbl, use_container_width=True)

# ── Footer ────────────────────────────────────────────────────────────────────
st.divider()
col1, col2, col3 = st.columns(3)
with col1:
    latest_ts = df["Timestamp consulta"].max()
    st.metric("Última consulta", latest_ts.strftime("%d/%m %H:%M") if pd.notna(latest_ts) else "—")
with col2:
    st.metric("Total registros", f"{len(df):,}")
with col3:
    st.metric("Fechas únicas", df["flight_date"].nunique())
