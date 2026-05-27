"""
Dashboard de disponibilidad Business — EF Scraper
Lee datos de Google Sheets en tiempo real
"""

import streamlit as st
import pandas as pd
import numpy as np
import gspread
from google.oauth2.service_account import Credentials
from datetime import datetime

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
    "AV185": "07:55", "LA4907": "12:10", "AV249": "14:20",
    "AV161": "17:00", "LA4903": "21:20", "AV85":  "21:45", "AV199": "08:00",
    "AV248": "01:20", "AV160": "02:20", "AV86":  "07:35",
    "LA4904": "08:15", "AV184": "17:15", "LA4908": "23:45",
}

def key_class(vuelo, route):
    if "GRU" in route and vuelo.startswith("LA"):
        return "W"
    if vuelo.startswith("AV"):
        return "C"
    return "J"

DOW_ES = {"Mon":"Lunes","Tue":"Martes","Wed":"Miércoles",
           "Thu":"Jueves","Fri":"Viernes","Sat":"Sábado","Sun":"Domingo"}

# ── LOESS smoothing ────────────────────────────────────────────────────────────
def loess_smooth(xs, ys, bandwidth=0.4):
    xs, ys = np.array(xs), np.array(ys)
    n = len(xs)
    smoothed = np.zeros(n)
    k = max(3, int(np.ceil(bandwidth * n)))
    for i, x0 in enumerate(xs):
        dists = np.abs(xs - x0)
        idx = np.argsort(dists)[:k]
        max_d = dists[idx[-1]]
        if max_d == 0:
            smoothed[i] = ys[i]
            continue
        u = dists[idx] / max_d
        w = (1 - u**3)**3
        w = np.maximum(w, 0)
        X = xs[idx]
        Y = ys[idx]
        sw = w.sum()
        xbar = (w * X).sum() / sw
        ybar = (w * Y).sum() / sw
        Sxx = (w * (X - xbar)**2).sum()
        Sxy = (w * (X - xbar) * (Y - ybar)).sum()
        b1 = Sxy / Sxx if Sxx > 0 else 0
        b0 = ybar - b1 * xbar
        smoothed[i] = np.clip(b0 + b1 * x0, 0, 7)
    return smoothed

def compute_ci(xs, ys, smoothed, confidence=0.80):
    residuals = (np.array(ys) - smoothed)**2
    n = len(xs)
    window = max(3, int(0.2 * n))
    upper, lower = [], []
    z = 1.28  # 80% CI
    for i in range(n):
        lo, hi = max(0, i - window), min(n - 1, i + window)
        local_var = residuals[lo:hi+1].mean()
        se = z * np.sqrt(local_var)
        upper.append(float(np.clip(smoothed[i] + se, 0, 7)))
        lower.append(float(np.clip(smoothed[i] - se, 0, 7)))
    return upper, lower

# ── Google Sheets ──────────────────────────────────────────────────────────────
@st.cache_data(ttl=300)
def load_sheet(sheet_name):
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
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


# ── UI ─────────────────────────────────────────────────────────────────────────
st.set_page_config(page_title="EF Availability", page_icon="✈️", layout="wide")
st.title("✈️ Disponibilidad Business")
st.caption(f"Datos actualizados cada 5 min · Última carga: {datetime.now().strftime('%H:%M')}")

with st.sidebar:
    st.header("Filtros")
    route = st.selectbox("Ruta", ROUTES)
    st.divider()
    view = st.radio("Vista", ["📊 Heatmap por fecha", "📈 Curva vs tiempo", "📅 Evolución de una fecha"])

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
    if view != "📅 Evolución de una fecha":
        sel_days_es = st.multiselect(
            "Día de la semana del vuelo", days_opts, default=days_opts,
            help="Filtrá por día de semana del vuelo.")
        sel_days = [days_map[d] for d in sel_days_es]
    else:
        sel_days = list(days_map.values())
        sel_days_es = days_opts

df_f = df[df["Vuelo"].isin(sel_flights) & df["dow"].isin(sel_days)]

if df_f.empty:
    st.warning("Sin datos con los filtros seleccionados.")
    st.stop()

COLORS = [
    ("#185FA5", "rgba(24,95,165,0.15)"),
    ("#D85A30", "rgba(216,90,48,0.15)"),
    ("#0F6E56", "rgba(15,110,86,0.15)"),
    ("#993C1D", "rgba(153,60,29,0.15)"),
]

# ── Vista 1: Heatmap ───────────────────────────────────────────────────────────
if view == "📊 Heatmap por fecha":
    st.subheader(f"Disponibilidad por fecha — {route}")
    st.caption("Última lectura disponible por vuelo y fecha. Verde = amplio, rojo = cerrado.")

    latest = (df_f.sort_values("Timestamp consulta")
                  .groupby(["Fecha vuelo", "Vuelo"]).last().reset_index())

    for vuelo in sel_flights:
        sub = latest[latest["Vuelo"] == vuelo].sort_values("flight_date")
        if sub.empty:
            continue
        cls = key_class(vuelo, route)
        if cls not in sub.columns:
            cls = "J"
        dep = FLIGHT_TIMES.get(vuelo, "")
        st.markdown(f"**{vuelo}** {dep} — clase `{cls}`")
        dates = [d.split(" ")[0] if " " in d else d for d in sub["Fecha vuelo"].tolist()]
        vals  = sub[cls].tolist()
        row_df = pd.DataFrame([vals], columns=dates)

        def color_cell(val):
            if val >= 7:   return "background-color:#4CAF50;color:white;font-weight:bold"
            elif val >= 5: return "background-color:#C0DD97;color:#1a3a00;font-weight:bold"
            elif val >= 3: return "background-color:#FAC775;color:#4a2800;font-weight:bold"
            elif val >= 1: return "background-color:#F7C1C1;color:#5a0000;font-weight:bold"
            else:          return "background-color:#FCEBEB;color:#8a0000;font-weight:bold"

        st.dataframe(row_df.style.map(color_cell).format("{:.0f}"),
                     use_container_width=True, hide_index=True)
        st.divider()

# ── Vista 2: Curva LOESS ───────────────────────────────────────────────────────
elif view == "📈 Curva vs tiempo":
    st.subheader(f"Disponibilidad vs tiempo antes del vuelo — {route}")
    st.caption("Línea = tendencia suavizada (LOESS) · Banda = intervalo de confianza 80% · Puntos = observaciones reales")

    df_h = compute_hours_before(df_f, FLIGHT_TIMES)
    if df_h.empty:
        st.warning("Sin datos suficientes para la curva.")
        st.stop()

    traces = []
    for i, vuelo in enumerate(sel_flights):
        sub = df_h[df_h["Vuelo"] == vuelo].copy()
        if len(sub) < 5:
            continue
        cls = key_class(vuelo, route)
        if cls not in sub.columns:
            cls = "J"

        # Bucket a 6h y calcular por punto individual para LOESS
        sub = sub.sort_values("hours_before", ascending=False)
        xs_raw = sub["hours_before"].values
        ys_raw = sub[cls].values.astype(float)

        from numpy import interp

        # Bandwidth bajo: priorizar fidelidad a los datos sobre suavidad
        n_pts = len(xs_raw)
        bw = max(0.10, min(0.20, 8 / n_pts))

        smoothed_pts = loess_smooth(xs_raw, ys_raw, bandwidth=bw)
        upper_pts, lower_pts = compute_ci(xs_raw, ys_raw, smoothed_pts)

        # Grid para visualización
        x_grid = np.linspace(xs_raw.max(), xs_raw.min(), 80)
        sm_grid = interp(x_grid, xs_raw[::-1], smoothed_pts[::-1])
        up_grid = interp(x_grid, xs_raw[::-1], upper_pts[::-1])
        lo_grid = interp(x_grid, xs_raw[::-1], lower_pts[::-1])

        # Anclar extremos al valor real (primer y último punto observado)
        sm_grid = np.clip(sm_grid, 0, 7)
        sm_grid[0]  = float(ys_raw[0])   # anclaje inicio
        sm_grid[-1] = float(ys_raw[-1])  # anclaje cierre

        color_line, color_band = COLORS[i % len(COLORS)]
        dep = FLIGHT_TIMES.get(vuelo, "")
        label = f"{vuelo} {dep} ({cls})"

        # Scatter observaciones reales
        traces.append({
            "x": xs_raw.tolist(), "y": ys_raw.tolist(),
            "type": "scatter", "mode": "markers",
            "name": f"{vuelo} observaciones",
            "marker": {"color": color_line, "size": 5, "opacity": 0.3},
            "showlegend": False,
            "hovertemplate": "%{x:.0f}h antes · " + cls + "=%{y}<extra></extra>",
        })
        # Banda CI
        traces.append({
            "x": x_grid.tolist() + x_grid.tolist()[::-1],
            "y": up_grid.tolist() + lo_grid.tolist()[::-1],
            "fill": "toself", "fillcolor": color_band,
            "line": {"color": "rgba(0,0,0,0)"},
            "showlegend": False, "hoverinfo": "skip",
            "type": "scatter", "mode": "lines",
        })
        # Línea LOESS
        hover_txt = [
            f"<b>{x:.0f}h antes del vuelo</b><br>Tendencia: {s:.1f}<br>IC 80%: [{lo:.1f} – {up:.1f}]"
            for x, s, lo, up in zip(x_grid, sm_grid, lo_grid, up_grid)
        ]
        traces.append({
            "x": x_grid.tolist(), "y": sm_grid.tolist(),
            "type": "scatter", "mode": "lines",
            "name": label,
            "line": {"color": color_line, "width": 2.5},
            "text": hover_txt,
            "hovertemplate": "%{text}<extra>" + vuelo + "</extra>",
        })

    if traces:
        layout = {
            "height": 440,
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
        st.caption("← Más días antes del vuelo  |  Momento del vuelo →  · Banda angosta = patrón predecible · Banda ancha = alta varianza")

    # Tabla de cortes
    st.subheader("Disponibilidad promedio por corte de tiempo")
    CUTS = {
        "7d": (7*24-12, 7*24+12), "6d": (6*24-12, 6*24+12),
        "5d": (5*24-12, 5*24+12), "4d": (4*24-12, 4*24+12),
        "3d": (3*24-12, 3*24+12), "2d": (2*24-12, 2*24+12),
        "1d": (1*24-12, 1*24+12), "12h": (9, 15), "6h": (3, 9), "3h": (0, 5),
    }
    rows = []
    for vuelo in sel_flights:
        sub = df_h[df_h["Vuelo"] == vuelo].copy()
        if sub.empty: continue
        cls = key_class(vuelo, route)
        if cls not in sub.columns: cls = "J"
        dep = FLIGHT_TIMES.get(vuelo, "")
        row = {"Vuelo": f"{vuelo} {dep}", "Clase": cls}
        for cut_label, (lo, hi) in CUTS.items():
            window = sub[(sub["hours_before"] >= lo) & (sub["hours_before"] <= hi)]
            row[cut_label] = round(window[cls].mean(), 1) if len(window) > 0 else None
        rows.append(row)

    if rows:
        tbl = pd.DataFrame(rows).set_index("Vuelo")
        def color_cut(val):
            if pd.isna(val): return "color:#ccc"
            if val >= 7:     return "background-color:#4CAF50;color:white;font-weight:bold"
            elif val >= 5:   return "background-color:#C0DD97;color:#1a3a00;font-weight:bold"
            elif val >= 3:   return "background-color:#FAC775;color:#4a2800;font-weight:bold"
            elif val >= 1:   return "background-color:#F7C1C1;color:#5a0000;font-weight:bold"
            else:            return "background-color:#FCEBEB;color:#8a0000;font-weight:bold"
        num_cols = [c for c in tbl.columns if c != "Clase"]
        st.dataframe(tbl.style.map(color_cut, subset=num_cols).format("{:.1f}", subset=num_cols, na_rep="—"),
                     use_container_width=True)

    # ── Tabla detalle por vuelo y fecha ──────────────────────────────────────
    st.subheader("Detalle por vuelo y fecha")
    st.caption("Cada fila es un vuelo en una fecha específica. Mismo color que la tabla de promedios.")

    detail_rows = []
    for vuelo in sel_flights:
        sub_v = df_h[df_h["Vuelo"] == vuelo].copy()
        if sub_v.empty: continue
        cls = key_class(vuelo, route)
        if cls not in sub_v.columns: cls = "J"
        dep = FLIGHT_TIMES.get(vuelo, "")

        for fecha in sorted(sub_v["Fecha vuelo"].unique()):
            sub_f = sub_v[sub_v["Fecha vuelo"] == fecha]
            dow = sub_f["dow"].iloc[0] if not sub_f.empty else ""
            dow_es = DOW_ES.get(dow, dow)
            fecha_short = fecha.split(" ")[0] if " " in fecha else fecha
            row = {
                "Vuelo": f"{vuelo} {dep}",
                "Fecha": f"{fecha_short} ({dow_es})",
                "Clase": cls,
            }
            for cut_label, (lo, hi) in CUTS.items():
                window = sub_f[(sub_f["hours_before"] >= lo) & (sub_f["hours_before"] <= hi)]
                row[cut_label] = round(window[cls].mean(), 1) if len(window) > 0 else None
            detail_rows.append(row)

    if detail_rows:
        dtbl = pd.DataFrame(detail_rows).set_index(["Vuelo", "Fecha"])
        num_cols_d = [c for c in dtbl.columns if c != "Clase"]
        styled_dtbl = (dtbl.style
                          .map(color_cut, subset=num_cols_d)
                          .format("{:.1f}", subset=num_cols_d, na_rep="—"))
        st.dataframe(styled_dtbl, use_container_width=True)

    # Scatter
    st.divider()
    st.subheader("Dispersión de observaciones individuales")
    st.caption("Cada punto es una medición real.")
    sc_traces = []
    for i, vuelo in enumerate(sel_flights):
        sub = df_h[df_h["Vuelo"] == vuelo].copy()
        if sub.empty: continue
        cls = key_class(vuelo, route)
        if cls not in sub.columns: cls = "J"
        color_line, _ = COLORS[i % len(COLORS)]
        dep = FLIGHT_TIMES.get(vuelo, "")
        hover_sc = [
            f"<b>{vuelo} {dep}</b><br>{h:.0f}h antes<br>{cls} = {v:.0f}<br>{fv}"
            for h, v, fv in zip(sub["hours_before"], sub[cls], sub["Fecha vuelo"])
        ]
        sc_traces.append({
            "x": sub["hours_before"].tolist(), "y": sub[cls].tolist(),
            "type": "scatter", "mode": "markers",
            "name": f"{vuelo} ({cls})",
            "marker": {"color": color_line, "size": 7, "opacity": 0.6, "line": {"color": "white", "width": 0.5}},
            "text": hover_sc, "hovertemplate": "%{text}<extra></extra>",
        })
    if sc_traces:
        sc_layout = {
            "height": 380,
            "xaxis": {"autorange": "reversed", "title": "Horas antes del vuelo",
                      "tickvals": list(range(0, 200, 24)),
                      "ticktext": [f"{i}d" if i > 0 else "✈" for i in range(0, 9)],
                      "gridcolor": "rgba(128,128,128,0.1)"},
            "yaxis": {"title": "Disponibilidad", "range": [-0.3, 7.5],
                      "tickvals": list(range(0, 8)),
                      "ticktext": ["0","1","2","3","4","5","6","7+"],
                      "gridcolor": "rgba(128,128,128,0.1)"},
            "legend": {"orientation": "h", "yanchor": "bottom", "y": 1.02},
            "margin": {"l": 40, "r": 20, "t": 40, "b": 40},
            "plot_bgcolor": "rgba(0,0,0,0)", "paper_bgcolor": "rgba(0,0,0,0)",
        }
        st.plotly_chart({"data": sc_traces, "layout": sc_layout}, use_container_width=True)

# ── Vista 3: Evolución de una fecha específica ─────────────────────────────────
else:
    st.subheader(f"Evolución histórica de una fecha de vuelo — {route}")
    st.caption("Fecha pasada: línea real. Fecha futura: línea real hasta hoy + proyección ajustada al valor actual.")

    # Selector de vuelo y fecha
    col1, col2 = st.columns(2)
    with col1:
        vuelo_sel = st.selectbox("Vuelo", sel_flights if sel_flights else flights)
    with col2:
        fechas_disp = sorted(
            df[df["Vuelo"] == vuelo_sel]["Fecha vuelo"].dropna().unique()
        )
        if not fechas_disp:
            st.warning("Sin fechas disponibles para este vuelo.")
            st.stop()
        # Default: fecha más cercana a hoy+2 días
        target_date = (datetime.now() + pd.Timedelta(days=2)).strftime("%Y-%m-%d")
        default_idx = 0
        for i, f in enumerate(fechas_disp):
            f_date = f.split(" ")[0] if " " in f else f
            if f_date >= target_date:
                default_idx = i
                break
        fecha_sel = st.selectbox("Fecha del vuelo", fechas_disp, index=default_idx)

    sub = df[(df["Vuelo"] == vuelo_sel) & (df["Fecha vuelo"] == fecha_sel)].copy()
    sub = sub.sort_values("Timestamp consulta")

    cls = key_class(vuelo_sel, route)
    if cls not in sub.columns:
        cls = "J"

    if sub.empty:
        st.warning("Sin datos para esta combinación.")
        st.stop()

    dep = FLIGHT_TIMES.get(vuelo_sel, "")

    # Determinar si el vuelo ya ocurrió
    flight_date_parsed = pd.to_datetime(
        fecha_sel.split(" ")[0] if " " in fecha_sel else fecha_sel, errors="coerce")
    dep_h, dep_m = map(int, dep.split(":")) if dep else (0, 0)
    flight_dt = flight_date_parsed.replace(hour=dep_h, minute=dep_m) if pd.notna(flight_date_parsed) else None
    is_future = flight_dt is not None and flight_dt > datetime.now()

    st.markdown(f"**{vuelo_sel}** {dep} · **{fecha_sel}** · clase `{cls}` · {len(sub)} mediciones")

    # ── Slider de cupos mínimos (solo para vuelos futuros) ──
    if is_future:
        cupos_min = st.slider("Cupos mínimos al cierre para contar como éxito", 1, 4, 2)
    else:
        cupos_min = 2

    ev_traces = []

    # Línea real
    ev_traces.append({
        "x": sub["Timestamp consulta"].dt.strftime("%d/%m %H:%M").tolist(),
        "y": sub[cls].tolist(),
        "type": "scatter", "mode": "lines+markers",
        "name": "Observado",
        "line": {"color": "#185FA5", "width": 2.5},
        "marker": {"size": 7, "color": "#185FA5"},
        "hovertemplate": "%{x}<br>" + cls + " = %{y}<extra>Observado</extra>",
    })

    prob = None

    if is_future and flight_dt is not None:
        # Valor actual (última lectura)
        current_val = float(sub[cls].iloc[-1])
        current_ts  = sub["Timestamp consulta"].iloc[-1]
        hours_now   = (flight_dt - current_ts).total_seconds() / 3600

        # Histórico: mismo vuelo, mismos días de semana, OTRAS fechas
        hist = compute_hours_before(
            df[(df["Vuelo"] == vuelo_sel) & (df["Fecha vuelo"] != fecha_sel)].copy(),
            FLIGHT_TIMES
        )

        if len(hist) >= 5 and cls in hist.columns:
            hist = hist.sort_values("hours_before", ascending=False)

            # Proyección: forma normalizada de la curva histórica
            # Escala la forma histórica para que parta de current_val
            from numpy import interp

            flight_dow = flight_date_parsed.strftime("%a") if pd.notna(flight_date_parsed) else None
            hist_dow = hist[hist["dow"] == flight_dow].copy() if (
                flight_dow and "dow" in hist.columns and
                len(hist[hist["dow"] == flight_dow]) >= 3
            ) else hist.copy()

            hist_dow["bucket"] = (hist_dow["hours_before"] / 12).round() * 12
            buckets = hist_dow.groupby("bucket")[cls].agg(["mean","std","count"]).reset_index()
            buckets = buckets.sort_values("bucket", ascending=True)  # 0 a max
            buckets["std"] = buckets["std"].fillna(0)

            n_proj = 40
            proj_hours = np.linspace(hours_now, 0, n_proj)
            valid_proj = False
            n_hist_dates = len(hist_dow["Fecha vuelo"].unique())

            if len(buckets) >= 3:
                b_xs   = buckets["bucket"].values.astype(float)
                b_mean = buckets["mean"].values.astype(float)
                b_std  = buckets["std"].values.astype(float)

                # Valor histórico en hours_now y al cierre (h=0)
                hist_at_now   = float(np.clip(interp(hours_now, b_xs, b_mean), 0.001, 7))
                hist_at_close = float(np.clip(interp(0, b_xs, b_mean), 0, 7))

                # Forma histórica en los puntos proyectados
                hist_at_proj = interp(proj_hours, b_xs, b_mean)
                std_at_proj  = np.clip(interp(proj_hours, b_xs, b_std), 0, 3)

                # Normalizar: escalar la forma histórica para que en hours_now = current_val
                # y al cierre = current_val * (hist_close / hist_now)
                scale = current_val / hist_at_now if hist_at_now > 0 else 1.0
                proj_smooth = np.clip(hist_at_proj * scale, 0, 7)
                proj_upper  = np.clip((hist_at_proj + 1.28 * std_at_proj) * scale, 0, 7)
                proj_lower  = np.clip((hist_at_proj - 1.28 * std_at_proj) * scale, 0, 7)

                # Anclar primer punto exactamente al valor actual
                proj_smooth[0] = float(current_val)
                proj_upper[0]  = float(np.clip(current_val * (1 + 1.28 * float(interp(hours_now, b_xs, b_std)) / hist_at_now), 0, 7))
                proj_lower[0]  = float(np.clip(current_val * (1 - 1.28 * float(interp(hours_now, b_xs, b_std)) / hist_at_now), 0, 7))

                proj_ts = [
                    (flight_dt - pd.Timedelta(hours=float(h))).strftime("%d/%m %H:%M")
                    for h in proj_hours
                ]

                ev_traces.append({
                    "x": proj_ts + proj_ts[::-1],
                    "y": proj_upper.tolist() + proj_lower.tolist()[::-1],
                    "fill": "toself", "fillcolor": "rgba(24,95,165,0.10)",
                    "line": {"color": "rgba(0,0,0,0)"},
                    "showlegend": False, "hoverinfo": "skip",
                    "type": "scatter", "mode": "lines",
                })
                hover_proj = [
                    f"<b>Proyección</b><br>{t}<br>{cls} estimado: {v:.1f}<br>IC 80%: [{lo:.1f} – {up:.1f}]<br>Basado en {n_hist_dates} fecha(s) históricas"
                    for t, v, lo, up in zip(proj_ts, proj_smooth, proj_lower, proj_upper)
                ]
                ev_traces.append({
                    "x": proj_ts, "y": proj_smooth.tolist(),
                    "type": "scatter", "mode": "lines",
                    "name": "Proyección ajustada",
                    "line": {"color": "#185FA5", "width": 2, "dash": "dash"},
                    "text": hover_proj,
                    "hovertemplate": "%{text}<extra>Proyección</extra>",
                })
                valid_proj = True

                if n_hist_dates < 4:
                    st.warning(f"⚠️ Proyección basada en solo {n_hist_dates} fecha(s) histórica(s) del mismo día — alta incertidumbre.")
                # Probabilidad empírica ajustada
                # Para cada fecha histórica, tomar el valor en hours_now e interpolar al cierre
                fechas_hist = hist["Fecha vuelo"].unique()
                cierres = []
                for fh in fechas_hist:
                    sh = hist[hist["Fecha vuelo"] == fh].sort_values("hours_before")
                    if len(sh) < 2:
                        continue
                    hx = sh["hours_before"].values
                    hy = sh[cls].values.astype(float)
                    val_now_h = float(np.clip(interp(hours_now, hx[::-1], hy[::-1]), 0, 7)) if hours_now <= hx.max() else hy[0]
                    val_close = float(hy[hy == hy.min()].mean()) if len(hy) > 0 else 0
                    delta = val_close - val_now_h  # cuánto cayó ese vuelo
                    cierre_ajustado = current_val + delta
                    cierres.append(np.clip(cierre_ajustado, 0, 7))

                if cierres:
                    prob = sum(1 for c in cierres if c >= cupos_min) / len(cierres)
                    n_hist = len(cierres)

    ev_layout = {
        "height": 400,
        "xaxis": {
            "title": "Fecha y hora de consulta",
            "tickangle": -45,
            "gridcolor": "rgba(128,128,128,0.1)",
        },
        "yaxis": {
            "title": f"Disponibilidad ({cls})",
            "range": [-0.3, 7.5],
            "tickvals": list(range(0, 8)),
            "ticktext": ["0","1","2","3","4","5","6","7+"],
            "gridcolor": "rgba(128,128,128,0.1)",
        },
        "legend": {"orientation": "h", "yanchor": "bottom", "y": 1.02},
        "margin": {"l": 40, "r": 20, "t": 40, "b": 80},
        "plot_bgcolor": "rgba(0,0,0,0)",
        "paper_bgcolor": "rgba(0,0,0,0)",
    }
    st.plotly_chart({"data": ev_traces, "layout": ev_layout}, use_container_width=True)

    if is_future and prob is not None:
        pct = int(round(prob * 100))
        if pct >= 70:
            color = "🟢"
        elif pct >= 40:
            color = "🟡"
        else:
            color = "🔴"
        st.metric(
            label=f"{color} Probabilidad de {cupos_min}+ cupos {cls} al cierre",
            value=f"{pct}%",
            help=f"Basado en {n_hist} vuelos históricos similares. Proyección anclada al valor actual ({current_val:.0f} cupos) aplicando la forma histórica de caída."
        )
    elif not is_future:
        st.caption("Vuelo ya ocurrido — mostrando el historial real completo.")

    with st.expander("Ver todas las lecturas"):
        tbl2 = sub[["Timestamp consulta", cls, "Fecha vuelo"]].copy()
        tbl2["Timestamp consulta"] = tbl2["Timestamp consulta"].dt.strftime("%d/%m/%Y %H:%M")
        st.dataframe(tbl2.reset_index(drop=True), use_container_width=True)

# ── Footer ─────────────────────────────────────────────────────────────────────
st.divider()
col1, col2, col3 = st.columns(3)
with col1:
    latest_ts = df["Timestamp consulta"].max()
    st.metric("Última consulta", latest_ts.strftime("%d/%m %H:%M") if pd.notna(latest_ts) else "—")
with col2:
    st.metric("Total registros", f"{len(df):,}")
with col3:
    st.metric("Fechas únicas", df["flight_date"].nunique())
