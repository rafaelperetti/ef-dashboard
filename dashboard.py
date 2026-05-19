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
    # BOG-GRU
    "AV185": "07:55", "LA4907": "12:10", "AV249": "14:20",
    "AV161": "17:00", "LA4903": "21:20", "AV85":  "21:45", "AV199": "08:00",
    # GRU-BOG
    "AV248": "01:20", "AV160": "02:20", "AV86":  "07:35",
    "LA4904": "08:15", "AV184": "17:15", "LA4908": "23:45",
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
        "Día de la semana del vuelo",
        days_opts,
        default=days_opts,
        help="Filtrá por día de semana del vuelo. Ej: seleccioná solo Domingo para ver el patrón de vuelos que salen un domingo."
    )
    sel_days = [days_map[d] for d in sel_days_es]

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
    st.caption("Línea = promedio · Banda = rango entre percentil 25 y 75 de todas las observaciones.")

    df_h = compute_hours_before(df_f, FLIGHT_TIMES)

    if df_h.empty:
        st.warning("Sin datos suficientes para la curva.")
        st.stop()

    with st.sidebar:
        sel_days2 = sel_days  # mismo filtro para curva y tabla

    df_h = df_h[df_h["dow"].isin(sel_days2)] if sel_days2 else df_h

    COLORS = [
        ("#185FA5", "rgba(24,95,165,0.15)"),
        ("#D85A30", "rgba(216,90,48,0.15)"),
        ("#0F6E56", "rgba(15,110,86,0.15)"),
        ("#993C1D", "rgba(153,60,29,0.15)"),
    ]

    traces = []
    for i, vuelo in enumerate(sel_flights):
        sub = df_h[df_h["Vuelo"] == vuelo].copy()
        if sub.empty:
            continue
        cls = key_class(vuelo, route)
        if cls not in sub.columns:
            cls = "J"
        sub["bucket"] = (sub["hours_before"] / 6).round() * 6
        grouped = sub.groupby("bucket")[cls].agg(
            mean="mean", q25=lambda x: x.quantile(0.25), q75=lambda x: x.quantile(0.75)
        ).reset_index().sort_values("bucket", ascending=False)

        color_line, color_band = COLORS[i % len(COLORS)]
        dep   = FLIGHT_TIMES.get(vuelo, "")
        label = f"{vuelo} {dep} ({cls})"

        # Upper band
        traces.append({
            "x": grouped["bucket"].tolist() + grouped["bucket"].tolist()[::-1],
            "y": grouped["q75"].tolist() + grouped["q25"].tolist()[::-1],
            "fill": "toself",
            "fillcolor": color_band,
            "line": {"color": "rgba(0,0,0,0)"},
            "showlegend": False,
            "hoverinfo": "skip",
            "type": "scatter",
            "mode": "lines",
        })
        # Mean line
        traces.append({
            "x": grouped["bucket"].tolist(),
            "y": grouped["mean"].tolist(),
            "type": "scatter",
            "mode": "lines+markers",
            "name": label,
            "line": {"color": color_line, "width": 2.5},
            "marker": {"size": 5, "color": color_line},
            "hovertemplate": "%{x:.0f}h antes<br>Promedio = %{y:.1f}<extra>" + vuelo + "</extra>",
        })

    if traces:
        layout = {
            "height": 420,
            "xaxis": {
                "autorange": "reversed",
                "title": "Horas antes del vuelo",
                "tickvals": list(range(0, 200, 24)),
                "ticktext": [f"{i}d" if i > 0 else "✈" for i in range(0, 9)],
                "gridcolor": "rgba(128,128,128,0.1)",
            },
            "yaxis": {
                "title": "Disponibilidad",
                "range": [0, 7.5],
                "tickvals": list(range(0, 8)),
                "ticktext": ["0","1","2","3","4","5","6","7+"],
                "gridcolor": "rgba(128,128,128,0.1)",
            },
            "legend": {"orientation": "h", "yanchor": "bottom", "y": 1.02},
            "margin": {"l": 40, "r": 20, "t": 40, "b": 40},
            "plot_bgcolor": "rgba(0,0,0,0)",
            "paper_bgcolor": "rgba(0,0,0,0)",
        }
        st.plotly_chart({"data": traces, "layout": layout}, use_container_width=True)
        st.caption("← Más días antes del vuelo  |  Momento del vuelo →  · Banda = P25-P75")

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
