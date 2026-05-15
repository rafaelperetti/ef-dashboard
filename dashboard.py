"""
Dashboard de disponibilidad Business — EF Scraper
Lee datos de Google Sheets en tiempo real
"""

import streamlit as st
import pandas as pd
import gspread
from google.oauth2.service_account import Credentials
import plotly.graph_objects as go
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
    creds  = Credentials.from_service_account_file("credentials.json", scopes=scopes)
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
    sel_days_es = st.multiselect("Días de semana", days_opts, default=days_opts)
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

        col1, col2 = st.columns([1, 4])
        with col1:
            dep = FLIGHT_TIMES.get(vuelo, "")
            st.metric(vuelo, dep, f"clase {cls}")

        with col2:
            vals  = sub[cls].tolist()
            dates = sub["Fecha vuelo"].tolist()

            fig = go.Figure(go.Heatmap(
                z=[vals],
                x=dates,
                y=[vuelo],
                colorscale=[
                    [0,   "#FCEBEB"],
                    [0.15,"#F7C1C1"],
                    [0.45,"#FAC775"],
                    [0.85,"#C0DD97"],
                    [1,   "#4CAF50"],
                ],
                zmin=0, zmax=7,
                text=[[str(v) for v in vals]],
                texttemplate="%{text}",
                showscale=False,
                hovertemplate="%{x}<br>" + cls + " = %{z}<extra></extra>",
            ))
            fig.update_layout(
                height=90, margin=dict(l=0,r=0,t=0,b=0),
                xaxis=dict(showgrid=False, tickfont=dict(size=10)),
                yaxis=dict(showticklabels=False),
            )
            st.plotly_chart(fig, use_container_width=True, key=f"hm_{vuelo}")

# ── Vista 2: Curva vs tiempo ──────────────────────────────────────────────────
else:
    st.subheader(f"Disponibilidad vs tiempo antes del vuelo — {route}")
    st.caption("Promedio de disponibilidad según horas restantes. Eje invertido: derecha = momento del vuelo.")

    df_h = compute_hours_before(df_f, FLIGHT_TIMES)

    if df_h.empty:
        st.warning("Sin datos suficientes para la curva.")
        st.stop()

    with st.sidebar:
        sel_days2_es = st.multiselect("Filtrar días para curva", days_opts,
                                       default=sel_days_es, key="curve_days")
        sel_days2 = [days_map[d] for d in sel_days2_es]

    df_h = df_h[df_h["dow"].isin(sel_days2)] if sel_days2 else df_h

    colors = ["#185FA5","#D85A30","#0F6E56","#993C1D","#6B3FA0","#B5860D"]

    fig = go.Figure()
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
                      .sort_values("bucket", ascending=False))

        dep = FLIGHT_TIMES.get(vuelo, "")
        color = colors[i % len(colors)]
        fig.add_trace(go.Scatter(
            x=grouped["bucket"],
            y=grouped[cls],
            mode="lines+markers",
            name=f"{vuelo} {dep} ({cls})",
            line=dict(color=color, width=2.5,
                      dash="dash" if i % 2 == 1 else "solid"),
            marker=dict(size=6, color=color),
            hovertemplate="%{x:.0f}h antes<br>" + cls + " = %{y:.1f}<extra>" + vuelo + "</extra>",
        ))

    fig.update_layout(
        height=400,
        xaxis=dict(
            autorange="reversed",
            title="Horas antes del vuelo",
            tickvals=list(range(0, 220, 24)),
            ticktext=[f"{i}d" if i > 0 else "✈" for i in range(0, 10)],
            gridcolor="rgba(128,128,128,0.1)",
        ),
        yaxis=dict(
            title="Disponibilidad",
            range=[0, 7.2],
            tickvals=list(range(0, 8)),
            ticktext=["0","1","2","3","4","5","6","7+"],
            gridcolor="rgba(128,128,128,0.1)",
        ),
        legend=dict(orientation="h", yanchor="bottom", y=1.02),
        margin=dict(l=40, r=20, t=40, b=40),
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
    )
    st.plotly_chart(fig, use_container_width=True)

    st.caption("⚠️ Con pocos días de datos las curvas son indicativas. Se van a afinar con más semanas de scraping.")

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
