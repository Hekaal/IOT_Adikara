import json
import time
from datetime import datetime, timedelta, timezone

import requests
import pandas as pd
import streamlit as st

import paho.mqtt.client as mqtt

# =========================
# CONFIG (from secrets)
# =========================
SUPABASE_URL = st.secrets.get("SUPABASE_URL", "https://yhxbhhnumryhvmbqfydo.supabase.co")
SUPABASE_KEY = st.secrets.get("SUPABASE_KEY", "sb_secret_ax95UAB1rgUpsaam3v-Q7w_RMsRCE7N")

MQTT_BROKER = st.secrets.get("MQTT_BROKER", "9c50aa4767ef40c0bb562a0e1edf1547.s1.eu.hivemq.cloud")
MQTT_PORT   = int(st.secrets.get("MQTT_PORT", 8883))
MQTT_USER   = st.secrets.get("MQTT_USER", "patani")
MQTT_PASS   = st.secrets.get("MQTT_PASS", "Patani11")

TOPIC_PUMP_CMD = st.secrets.get("TOPIC_PUMP_CMD", "adikara-iot/actuator/pump_cmd")

JAKARTA_TZ = timezone(timedelta(hours=7))

# =========================
# Helpers: Supabase REST
# =========================
def sb_headers():
    return {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
    }

def supabase_select(table: str, select="*", params=None):
    url = f"{SUPABASE_URL}/rest/v1/{table}"
    r = requests.get(url, headers=sb_headers(), params={"select": select, **(params or {})}, timeout=20)
    r.raise_for_status()
    return r.json()

@st.cache_data(ttl=5)
def get_latest_sensor():
    rows = supabase_select(
        "sensor_log",
        select="id,created_at,temperature,humidity,soil,pump_status",
        params={"order": "created_at.desc", "limit": "1"},
    )
    return rows[0] if rows else None

@st.cache_data(ttl=10)
def get_sensor_history(hours: int = 24):
    since = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    rows = supabase_select(
        "sensor_log",
        select="id,created_at,temperature,humidity,soil,pump_status",
        params={
            "created_at": f"gte.{since}",
            "order": "created_at.asc",
            "limit": "5000",
        },
    )
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df["created_at"] = pd.to_datetime(df["created_at"], utc=True).dt.tz_convert(JAKARTA_TZ)
    for c in ["temperature", "humidity", "soil"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df

@st.cache_data(ttl=10)
def get_vision_history(limit: int = 200):
    rows = supabase_select(
        "vision_log",
        select="id,created_at,label,confidence,chat_id,raw_json",
        params={"order": "created_at.desc", "limit": str(limit)},
    )
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df["created_at"] = pd.to_datetime(df["created_at"], utc=True).dt.tz_convert(JAKARTA_TZ)
    df["confidence"] = pd.to_numeric(df["confidence"], errors="coerce")
    return df

# =========================
# Helpers: MQTT publish
# =========================
def mqtt_publish_pump(cmd: str):
    if not (MQTT_BROKER and MQTT_USER and MQTT_PASS):
        raise RuntimeError("MQTT config belum lengkap di secrets.")

    client = mqtt.Client()
    client.username_pw_set(MQTT_USER, MQTT_PASS)
    client.tls_set()  # HiveMQ Cloud TLS

    client.connect(MQTT_BROKER, MQTT_PORT, keepalive=30)
    client.loop_start()
    time.sleep(0.2)
    info = client.publish(TOPIC_PUMP_CMD, cmd, qos=0, retain=False)
    info.wait_for_publish()
    client.loop_stop()
    client.disconnect()

# =========================
# UI
# =========================
st.set_page_config(page_title="Adikara IoT Dashboard", layout="wide")

st.title("Adikara IoT - Smart Plant Doctor Dashboard")

with st.sidebar:
    st.header("Pengaturan")
    auto_refresh = st.toggle("Auto refresh", value=True)
    refresh_sec = st.slider("Interval refresh (detik)", 2, 30, 5)
    hist_hours = st.slider("Rentang histori sensor (jam)", 1, 168, 24)
    vision_limit = st.slider("Jumlah log vision", 50, 1000, 200)

    st.divider()
    st.subheader("Kontrol Pompa (MQTT)")
    col_a, col_b, col_c = st.columns(3)
    if col_a.button("PUMP ON", use_container_width=True):
        try:
            mqtt_publish_pump("ON")
            st.success("Terkirim: ON")
        except Exception as e:
            st.error(f"Gagal publish ON: {e}")

    if col_b.button("PUMP OFF", use_container_width=True):
        try:
            mqtt_publish_pump("OFF")
            st.success("Terkirim: OFF")
        except Exception as e:
            st.error(f"Gagal publish OFF: {e}")

    if col_c.button("PUMP AUTO", use_container_width=True):
        try:
            mqtt_publish_pump("AUTO")
            st.success("Terkirim: AUTO")
        except Exception as e:
            st.error(f"Gagal publish AUTO: {e}")

# Auto refresh
if auto_refresh:
    st.markdown(
        f"<meta http-equiv='refresh' content='{refresh_sec}'>",
        unsafe_allow_html=True
    )

# =========================
# Section: KPI realtime
# =========================
latest = None
try:
    latest = get_latest_sensor()
except Exception as e:
    st.error(f"Gagal ambil latest sensor dari Supabase: {e}")

k1, k2, k3, k4, k5 = st.columns([1,1,1,1,2])

if latest:
    ts = pd.to_datetime(latest["created_at"], utc=True).tz_convert(JAKARTA_TZ)
    k1.metric("Suhu (C)", latest.get("temperature", "--"))
    k2.metric("Kelembaban (%)", latest.get("humidity", "--"))
    k3.metric("Kelembaban Tanah (%)", latest.get("soil", "--"))
    k4.metric("Pompa", latest.get("pump_status", "--"))
    k5.metric("Update Terakhir (WIB)", ts.strftime("%Y-%m-%d %H:%M:%S"))
else:
    k5.info("Belum ada data sensor_log atau gagal konek.")

st.divider()

# =========================
# Section: Grafik sensor
# =========================
left, right = st.columns([2, 1])

with left:
    st.subheader("Histori Sensor")
    try:
        df_s = get_sensor_history(hist_hours)
        if df_s.empty:
            st.info("Data histori sensor kosong.")
        else:
            st.line_chart(df_s.set_index("created_at")[["temperature"]])
            st.line_chart(df_s.set_index("created_at")[["humidity"]])
            st.line_chart(df_s.set_index("created_at")[["soil"]])

            with st.expander("Tabel sensor_log (ringkas)"):
                st.dataframe(df_s.tail(200), use_container_width=True)
    except Exception as e:
        st.error(f"Gagal load histori sensor: {e}")

with right:
    st.subheader("Ringkasan 24 Jam")
    try:
        df_s = get_sensor_history(hist_hours)
        if not df_s.empty:
            st.write("Statistik:")
            st.dataframe(df_s[["temperature","humidity","soil"]].describe().T, use_container_width=True)

            # Simple alert rules (boleh kamu ubah)
            st.write("Alert cepat:")
            soil_last = pd.to_numeric(latest.get("soil", None), errors="coerce") if latest else None
            if soil_last is not None and not pd.isna(soil_last):
                if soil_last < 30:
                    st.warning("Tanah cenderung kering (soil < 30%).")
                elif soil_last > 80:
                    st.info("Tanah sangat lembab (soil > 80%).")
                else:
                    st.success("Kelembaban tanah normal.")
    except Exception as e:
        st.error(f"Gagal hitung ringkasan: {e}")

st.divider()

# =========================
# Section: Vision log
# =========================
st.subheader("Log Deteksi Daun (vision_log)")
try:
    df_v = get_vision_history(vision_limit)
    if df_v.empty:
        st.info("Belum ada data vision_log.")
    else:
        c1, c2 = st.columns([1,1])
        with c1:
            st.write("Top label:")
            st.dataframe(df_v["label"].value_counts().head(10), use_container_width=True)
        with c2:
            st.write("Distribusi confidence:")
            st.bar_chart(df_v["confidence"].dropna())

        st.write("Data terbaru:")
        view = df_v[["created_at","label","confidence","chat_id"]].copy()
        view["confidence_%"] = (view["confidence"] * 100).round(1)
        st.dataframe(view.drop(columns=["confidence"]).head(200), use_container_width=True)

        with st.expander("Lihat raw_json (10 data terbaru)"):
            for _, row in df_v.head(10).iterrows():
                st.write(f"{row['created_at']} | {row['label']} | {float(row['confidence'])*100:.1f}%")
                st.code(json.dumps(row.get("raw_json", {}), indent=2, ensure_ascii=False), language="json")
except Exception as e:
    st.error(f"Gagal load vision_log: {e}")

st.caption("Adikara IoT Dashboard - Supabase + MQTT")
