import time
from datetime import datetime, timedelta, timezone

import requests
import pandas as pd
import streamlit as st
import paho.mqtt.client as mqtt

# =========================================================
# CONFIG (Streamlit Secrets ONLY)
# =========================================================
SUPABASE_URL = st.secrets.get("SUPABASE_URL", "").strip()
SUPABASE_KEY = st.secrets.get("SUPABASE_KEY", "").strip()  # disarankan ANON key

MQTT_BROKER = st.secrets.get("MQTT_BROKER", "").strip()
MQTT_PORT   = int(st.secrets.get("MQTT_PORT", 8883))
MQTT_USER   = st.secrets.get("MQTT_USER", "").strip()
MQTT_PASS   = st.secrets.get("MQTT_PASS", "").strip()

TOPIC_PUMP_CMD = st.secrets.get("TOPIC_PUMP_CMD", "adikara-iot/actuator/pump_cmd").strip()

JAKARTA_TZ = timezone(timedelta(hours=7))

# =========================================================
# GUARD
# =========================================================
st.set_page_config(page_title="Adikara IoT Dashboard", layout="wide")
st.title("Adikara IoT - Dashboard Sensor")

if not SUPABASE_URL or not SUPABASE_URL.startswith("http"):
    st.error("SUPABASE_URL kosong/tidak valid. Isi di Streamlit Cloud → Settings → Secrets.")
    st.stop()

if not SUPABASE_KEY:
    st.error("SUPABASE_KEY kosong. Isi di Secrets. Disarankan pakai ANON key (bukan sb_secret/service role).")
    st.stop()

MQTT_OK = all([MQTT_BROKER, MQTT_USER, MQTT_PASS])

# =========================================================
# SUPABASE REST HELPERS (lebih informatif kalau error)
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

    try:
        r = requests.get(url, headers=sb_headers(), params=q, timeout=20)
    except Exception as e:
        raise RuntimeError(f"Request error ke Supabase: {e}")

    if not r.ok:
        # Biar kamu langsung tau: 401/403 (RLS), 404 (table), dll.
        raise RuntimeError(f"Supabase HTTP {r.status_code}: {r.text}")

    return r.json()

@st.cache_data(ttl=5)
def get_latest_sensor():
    rows = supabase_select(
        "sensor_log",
        select="id,ts,temperature,humidity,soil,pump_status",
        params={"order": "ts.desc", "limit": "1"},
    )
    return rows[0] if rows else None

@st.cache_data(ttl=10)
def get_sensor_history(hours: int = 24):
    since = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()

    rows = supabase_select(
        "sensor_log",
        df["ts"] = pd.to_datetime(df["ts"], utc=True).dt.tz_convert(JAKARTA_TZ),
        params={
            "ts": f"gte.{since}",
            "order": "ts.asc",
            "limit": "5000",
        },
    )

    df = pd.DataFrame(rows)
    if df.empty:
        return df

    df["ts"] = pd.to_datetime(df["ts"], utc=True).dt.tz_convert(JAKARTA_TZ)

    for c in ["temperature", "humidity", "soil"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")

    return df


# =========================================================
# MQTT PUBLISH HELPER
# =========================================================
def mqtt_publish_pump(cmd: str):
    if not MQTT_OK:
        raise RuntimeError("MQTT belum lengkap di Secrets (MQTT_BROKER/MQTT_USER/MQTT_PASS).")

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

# =========================================================
# SIDEBAR
# =========================================================
with st.sidebar:
    st.header("Pengaturan")
    auto_refresh = st.toggle("Auto refresh", value=True)
    refresh_sec = st.slider("Interval refresh (detik)", 2, 30, 5)
    hist_hours = st.slider("Rentang histori sensor (jam)", 1, 168, 24)

    st.divider()
    st.subheader("Kontrol Pompa (MQTT)")

    if not MQTT_OK:
        st.warning("MQTT belum dikonfigurasi. Kontrol pompa dimatikan (dashboard tetap jalan).")
    else:
        col_a, col_b, col_c = st.columns(3)
        if col_a.button("ON", use_container_width=True):
            try:
                mqtt_publish_pump("ON")
                st.success("Terkirim: ON")
            except Exception as e:
                st.error(f"Gagal publish ON: {e}")

        if col_b.button("OFF", use_container_width=True):
            try:
                mqtt_publish_pump("OFF")
                st.success("Terkirim: OFF")
            except Exception as e:
                st.error(f"Gagal publish OFF: {e}")

        if col_c.button("AUTO", use_container_width=True):
            try:
                mqtt_publish_pump("AUTO")
                st.success("Terkirim: AUTO")
            except Exception as e:
                st.error(f"Gagal publish AUTO: {e}")

# Auto refresh
if auto_refresh:
    st.markdown(f"<meta http-equiv='refresh' content='{refresh_sec}'>", unsafe_allow_html=True)

# =========================================================
# MAIN: KPI
# =========================================================
latest = None
try:
    latest = get_latest_sensor()
except Exception as e:
    st.error(f"Gagal ambil latest sensor_log: {e}")

k1, k2, k3, k4, k5 = st.columns([1, 1, 1, 1, 2])

if latest:
    ts = pd.to_datetime(latest["ts"], utc=True).tz_convert(JAKARTA_TZ)
    k1.metric("Suhu (C)", latest.get("temperature", "--"))
    k2.metric("Kelembaban (%)", latest.get("humidity", "--"))
    k3.metric("Kelembaban Tanah (%)", latest.get("soil", "--"))
    k4.metric("Pompa", latest.get("pump_status", "--"))
    k5.metric("Update Terakhir (WIB)", ts.strftime("%Y-%m-%d %H:%M:%S"))
else:
    k5.info("Belum ada data di sensor_log atau akses Supabase masih bermasalah.")

st.divider()

# =========================================================
# HISTORI + RINGKASAN
# =========================================================
left, right = st.columns([2, 1])

with left:
    st.subheader("Histori Sensor")
    try:
        df_s = get_sensor_history(hist_hours)
        if df_s.empty:
            st.info("Data histori sensor kosong (atau query tidak dapat akses).")
        else:
            st.line_chart(df_s.set_index("created_at")[["temperature"]])
            st.line_chart(df_s.set_index("created_at")[["humidity"]])
            st.line_chart(df_s.set_index("created_at")[["soil"]])

            with st.expander("Tabel sensor_log (200 data terakhir)"):
                st.dataframe(df_s.tail(200), use_container_width=True)
    except Exception as e:
        st.error(f"Gagal load histori sensor_log: {e}")

with right:
    st.subheader("Ringkasan")
    try:
        df_s = get_sensor_history(hist_hours)
        if not df_s.empty:
            st.write("Statistik:")
            st.dataframe(df_s[["temperature", "humidity", "soil"]].describe().T, use_container_width=True)

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

st.caption("Adikara IoT Dashboard - Sensor (Supabase) + Kontrol Pompa (MQTT)")
