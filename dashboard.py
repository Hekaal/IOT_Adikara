import time
from datetime import datetime, timedelta, timezone

import requests
import pandas as pd
import streamlit as st
import paho.mqtt.client as mqtt
import plotly.graph_objects as go
from streamlit_autorefresh import st_autorefresh

# =========================================================
# CONFIG (Streamlit Secrets ONLY)
# =========================================================
SUPABASE_URL = st.secrets.get("SUPABASE_URL", "").strip()
SUPABASE_KEY = st.secrets.get("SUPABASE_KEY", "").strip()

MQTT_BROKER = st.secrets.get("MQTT_BROKER", "").strip()
MQTT_PORT   = int(st.secrets.get("MQTT_PORT", 8883))
MQTT_USER   = st.secrets.get("MQTT_USER", "").strip()
MQTT_PASS   = st.secrets.get("MQTT_PASS", "").strip()

TOPIC_PUMP_CMD = st.secrets.get("TOPIC_PUMP_CMD", "adikara-iot/actuator/pump_cmd").strip()

JAKARTA_TZ = timezone(timedelta(hours=7))

# =========================================================
# PAGE
# =========================================================
st.set_page_config(page_title="Adikara IoT Dashboard", layout="wide")
st.title("Adikara IoT – Realtime Sensor Dashboard")

# =========================================================
# GUARD
# =========================================================
if not SUPABASE_URL or not SUPABASE_URL.startswith("http"):
    st.error("SUPABASE_URL tidak valid. Isi di Streamlit Secrets.")
    st.stop()

if not SUPABASE_KEY:
    st.error("SUPABASE_KEY kosong. Isi ANON key di Streamlit Secrets.")
    st.stop()

MQTT_OK = all([MQTT_BROKER, MQTT_USER, MQTT_PASS])

# =========================================================
# SUPABASE REST
# =========================================================
def sb_headers():
    return {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
    }

def supabase_select(table: str, select="*", params=None):
    url = f"{SUPABASE_URL}/rest/v1/{table}"
    q = {"select": select}
    if params:
        q.update(params)

    r = requests.get(url, headers=sb_headers(), params=q, timeout=20)
    if not r.ok:
        raise RuntimeError(f"Supabase HTTP {r.status_code}: {r.text}")
    return r.json()

@st.cache_data(ttl=3)
def get_latest_sensor():
    rows = supabase_select(
        "sensor_log",
        select="id,ts,temperature,humidity,soil,pump_status",
        params={"order": "ts.desc", "limit": "1"},
    )
    return rows[0] if rows else None

@st.cache_data(ttl=6)
def get_sensor_history(hours: int):
    since = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()

    rows = supabase_select(
        "sensor_log",
        select="id,ts,temperature,humidity,soil,pump_status",
        params={
            "ts": f"gte.{since}",
            "order": "ts.asc",
            "limit": "5000",
        },
    )

    # fallback kalau kosong
    if not rows:
        rows = supabase_select(
            "sensor_log",
            select="id,ts,temperature,humidity,soil,pump_status",
            params={"order": "ts.desc", "limit": "800"},
        )
        rows = list(reversed(rows))

    df = pd.DataFrame(rows)
    if df.empty:
        return df

    df["ts"] = pd.to_datetime(df["ts"], utc=True).dt.tz_convert(JAKARTA_TZ)
    for c in ["temperature", "humidity", "soil"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")

    return df.sort_values("ts")

# =========================================================
# MQTT
# =========================================================
def mqtt_publish_pump(cmd: str):
    if not MQTT_OK:
        raise RuntimeError("MQTT belum lengkap di Secrets.")

    client = mqtt.Client()
    client.username_pw_set(MQTT_USER, MQTT_PASS)
    client.tls_set()
    client.connect(MQTT_BROKER, MQTT_PORT, keepalive=30)
    client.loop_start()
    time.sleep(0.2)
    info = client.publish(TOPIC_PUMP_CMD, cmd, qos=0, retain=False)
    info.wait_for_publish()
    client.loop_stop()
    client.disconnect()

# =========================================================
# SIDEBAR
# =========================================================
with st.sidebar:
    st.header("Realtime & Rentang Data")
    realtime = st.toggle("Realtime ON", value=True)
    refresh_sec = st.slider("Update tiap (detik)", 2, 30, 5)

    st.caption("Quick range (lebih enak daripada slider jam):")
    quick = st.radio(
        "Range",
        options=["1 jam", "6 jam", "24 jam", "7 hari"],
        index=2,
        horizontal=True,
    )
    if quick == "1 jam":
        hist_hours = 1
    elif quick == "6 jam":
        hist_hours = 6
    elif quick == "24 jam":
        hist_hours = 24
    else:
        hist_hours = 168

    st.divider()
    if st.button("🔄 Refresh Sekarang", use_container_width=True):
        st.cache_data.clear()
        st.rerun()

    st.divider()
    st.subheader("Kontrol Pompa (MQTT)")
    if not MQTT_OK:
        st.warning("MQTT belum dikonfigurasi.")
    else:
        col1, col2, col3 = st.columns(3)
        if col1.button("ON", use_container_width=True):
            mqtt_publish_pump("ON")
            st.success("Terkirim: ON")
        if col2.button("OFF", use_container_width=True):
            mqtt_publish_pump("OFF")
            st.success("Terkirim: OFF")
        if col3.button("AUTO", use_container_width=True):
            mqtt_publish_pump("AUTO")
            st.success("Terkirim: AUTO")

# realtime smooth
if realtime:
    st_autorefresh(interval=refresh_sec * 1000, key="realtime")

# =========================================================
# KPI
# =========================================================
latest = get_latest_sensor()
k1, k2, k3, k4, k5 = st.columns([1, 1, 1, 1, 2])

if latest:
    ts_last = pd.to_datetime(latest["ts"], utc=True).tz_convert(JAKARTA_TZ)
    k1.metric("Suhu (°C)", latest.get("temperature", "--"))
    k2.metric("Humidity (%)", latest.get("humidity", "--"))
    k3.metric("Soil (%)", latest.get("soil", "--"))
    k4.metric("Pompa", latest.get("pump_status", "--"))
    k5.metric("Update Terakhir", ts_last.strftime("%Y-%m-%d %H:%M:%S"))
else:
    k5.info("Belum ada data sensor.")

st.divider()

# =========================================================
# LOAD DF
# =========================================================
df_s = get_sensor_history(hist_hours)

if df_s.empty:
    st.info("Data histori sensor kosong.")
    st.stop()

# =========================================================
# 1) OVERVIEW: Dual Axis (1 chart)
# =========================================================
st.subheader("Overview (1 Grafik) – Dual Axis")
fig = go.Figure()

fig.add_trace(go.Scatter(
    x=df_s["ts"], y=df_s["temperature"],
    name="Temperature (°C)",
    yaxis="y1", mode="lines", connectgaps=True,
))

fig.add_trace(go.Scatter(
    x=df_s["ts"], y=df_s["humidity"],
    name="Humidity (%)",
    yaxis="y2", mode="lines", connectgaps=True,
))

fig.add_trace(go.Scatter(
    x=df_s["ts"], y=df_s["soil"],
    name="Soil (%)",
    yaxis="y2", mode="lines", connectgaps=True,
))

fig.update_layout(
    height=460,
    hovermode="x unified",
    legend=dict(orientation="h", y=1.02),
    xaxis=dict(title="Waktu (WIB)", rangeslider=dict(visible=True)),  # zoom
    yaxis=dict(title="Temperature (°C)"),
    yaxis2=dict(
        title="Humidity & Soil (%)",
        overlaying="y",
        side="right",
        rangemode="tozero",
    ),
    margin=dict(l=40, r=40, t=40, b=40),
)

st.plotly_chart(fig, use_container_width=True)

st.divider()

# =========================================================
# 2) PER SENSOR: Chart terpisah (lebih jelas)
# =========================================================
st.subheader("Detail per Sensor (Grafik Terpisah)")

cA, cB, cC = st.columns(3)

with cA:
    fig_t = go.Figure()
    fig_t.add_trace(go.Scatter(
        x=df_s["ts"], y=df_s["temperature"],
        mode="lines", name="Temperature (°C)", connectgaps=True
    ))
    fig_t.update_layout(
        height=320,
        margin=dict(l=30, r=20, t=30, b=30),
        hovermode="x unified",
        xaxis=dict(title="WIB"),
        yaxis=dict(title="°C"),
        title="Temperature"
    )
    st.plotly_chart(fig_t, use_container_width=True)

with cB:
    fig_h = go.Figure()
    fig_h.add_trace(go.Scatter(
        x=df_s["ts"], y=df_s["humidity"],
        mode="lines", name="Humidity (%)", connectgaps=True
    ))
    fig_h.update_layout(
        height=320,
        margin=dict(l=30, r=20, t=30, b=30),
        hovermode="x unified",
        xaxis=dict(title="WIB"),
        yaxis=dict(title="%"),
        title="Humidity"
    )
    st.plotly_chart(fig_h, use_container_width=True)

with cC:
    fig_s = go.Figure()
    fig_s.add_trace(go.Scatter(
        x=df_s["ts"], y=df_s["soil"],
        mode="lines", name="Soil (%)", connectgaps=True
    ))
    fig_s.update_layout(
        height=320,
        margin=dict(l=30, r=20, t=30, b=30),
        hovermode="x unified",
        xaxis=dict(title="WIB"),
        yaxis=dict(title="%"),
        title="Soil Moisture"
    )
    st.plotly_chart(fig_s, use_container_width=True)

st.divider()

# =========================================================
# TABLE
# =========================================================
with st.expander("Tabel sensor_log (200 data terakhir)"):
    st.dataframe(df_s.tail(200), use_container_width=True)

st.caption("Adikara IoT Dashboard – Overview + Detail per Sensor (Supabase + MQTT)")
