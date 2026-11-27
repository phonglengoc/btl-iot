"""
PC Local IoT Gateway Dashboard & Alert History Assistant (`T10`, `N01`).

Run with one command (works even without physical hardware connected):
    streamlit run gateway/streamlit_ui.py

Features:
  - `T10`: Real-time line charts of Moving-Average CSI disturbance (2s window),
           room temperature & humidity (DHT20), Random Forest probabilities (`T09`),
           and direct RPC buttons to turn the Buzzer ON/OFF (`RPC:ALARM:1` / `RPC:ALARM:0`).
  - `N01`: Alert History Query tab reading `gateway/runtime/event_log.jsonl` with quick
           preset questions and free-text Q&A (fall count, latest alert, room temperature stats).
  - `T15`: Live inspection of CoreIOT MQTT sync packets and Telegram alerts.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from main_gateway import LocalIoTGatewayService, load_gateway_config
from virtual_node_simulator import VirtualYoloUnoCluster

ROOT_DIR = Path(__file__).resolve().parents[1]
RUNTIME_DIR = ROOT_DIR / "gateway" / "runtime"
LIVE_STATE_FILE = RUNTIME_DIR / "live_state.json"
CONTROL_FILE = RUNTIME_DIR / "simulator_control.json"
UI_CMD_FILE = RUNTIME_DIR / "ui_commands.jsonl"
EVENTS_LOG_FILE = RUNTIME_DIR / "event_log.jsonl"
MQTT_JOURNAL_FILE = RUNTIME_DIR / "coreiot_mqtt_journal.jsonl"
TELEGRAM_LOG_FILE = RUNTIME_DIR / "telegram_alerts.jsonl"

PRESET_QUESTIONS = [
    "How many fall incidents have been detected?",
    "When did the most recent emergency alert occur?",
    "What is the current room temperature and humidity (and average during alerts)?",
    "Were there any manual SOS button alerts?",
    "Summarize the current room status and alert history.",
]


@st.cache_resource
def ensure_background_gateway_running() -> str:
    """Auto-start `LocalIoTGatewayService` in background if no recent `live_state.json` heartbeat exists."""
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    if LIVE_STATE_FILE.exists():
        try:
            st_data = json.loads(LIVE_STATE_FILE.read_text(encoding="utf-8"))
            if (time.time() - float(st_data.get("updated_ts", 0))) < 2.5:
                return "attached_external"
        except Exception:
            pass

    cfg = load_gateway_config()
    svc = LocalIoTGatewayService(cfg)
    th = threading.Thread(target=svc.run_forever, daemon=True)
    th.start()
    time.sleep(0.8)
    return "started_embedded"


def answer_event_history_query(question: str, live_state: dict, events: list[dict]) -> str:
    """
    Simple rule-based Alert History Assistant (`N01`):
    Reads `event_log.jsonl` records and answers statistical questions about falls, SOS, and room environment.
    """
    q = question.lower().strip()
    n1 = live_state.get("node1", {})
    n2 = live_state.get("node2", {})

    falls = [e for e in events if "FALL" in str(e.get("alarm_type", ""))]
    sos_events = [e for e in events if "SOS" in str(e.get("alarm_type", ""))]

    if any(k in q for k in ("latest", "recent", "last", "when")):
        if not events:
            return "✅ **No emergency incidents** have been recorded in `event_log.jsonl` yet."
        last = events[-1]
        return (
            f"🕒 **Most Recent Alert:** `{last['alarm_type']}` at **`{last['timestamp']}`**.\n"
            f"- AI State: **{last['state']}** (Confidence: `{last['ai_conf']*100:.1f}%`, Signal Std = `{last['signal_std']}`)\n"
            f"- Room Environment at Alert Time: Temp **`{last['temp_c']} °C`**, Humidity **`{last['humi_rh']} %RH`**."
        )

    if any(k in q for k in ("sos", "button")):
        if not sos_events:
            return "✅ No manual **SOS emergency button** presses have been recorded on Node 2."
        last_sos = sos_events[-1]
        return (
            f"🆘 Recorded **{len(sos_events)} manual SOS alert(s)**! "
            f"Most recent at `{last_sos['timestamp']}` (Room Temp: `{last_sos['temp_c']} °C`)."
        )

    if any(k in q for k in ("temp", "humid", "environment", "dht20")):
        cur_temp = float(n1.get("room_temp_c", 27.5))
        cur_humi = float(n1.get("room_humi_rh", 65.0))
        if events:
            avg_t = float(np.mean([float(e.get("temp_c", cur_temp)) for e in events]))
            avg_h = float(np.mean([float(e.get("humi_rh", cur_humi)) for e in events]))
            return (
                f"🌡️ **Room Environment (I2C DHT20 Sensor — `T01`):**\n"
                f"- Current: Temp **`{cur_temp:.1f} °C`** | Humidity **`{cur_humi:.1f} %RH`**\n"
                f"- Average across **{len(events)} recorded alert(s)**: **`{avg_t:.1f} °C`** / **`{avg_h:.1f} %RH`**."
            )
        return (
            f"🌡️ **Current Room Environment (DHT20 — `T01`):**\n"
            f"- Temperature: **`{cur_temp:.1f} °C`** | Humidity: **`{cur_humi:.1f} %RH`**."
        )

    if any(k in q for k in ("fall", "how many", "count")):
        if not falls:
            return "✅ **No fall incidents** have been detected during this monitoring session."
        last = falls[-1]
        avg_conf = float(np.mean([float(e.get("ai_conf", 0.0)) for e in falls])) * 100.0
        return (
            f"🚨 Total fall emergencies recorded: **{len(falls)} incident(s)**!\n"
            f"- Most recent: **`{last['timestamp']}`** (Confidence `{last['ai_conf']*100:.1f}%`, Temp `{last['temp_c']} °C`)\n"
            f"- Average AI confidence across fall alerts: **`{avg_conf:.1f}%`**."
        )

    return (
        f"📊 **Room Status & Alert History Summary (`N01`):**\n"
        f"- Current AI State: **{n2.get('csi_state', 'UNKNOWN')}** "
        f"(Random Forest Confidence: `{n2.get('ai_conf', 0)*100:.1f}%`, Signal Std: `{n2.get('signal_std', 0):.2f}`)\n"
        f"- Room Environment (DHT20 `T01`): **{n1.get('room_temp_c', 27.5):.1f} °C** | Humidity **{n1.get('room_humi_rh', 65.0):.1f} %RH**\n"
        f"- Total entries in `event_log.jsonl`: **{len(events)} alert(s)** "
        f"(**{len(falls)} fall(s)**, **{len(sos_events)} SOS alert(s)**)."
    )


def _write_sim_control(scenario: str, dist: float, nlos: bool, alarm_cmd: bool | None = None) -> None:
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    payload = {"scenario": scenario, "distance_m": dist, "nlos": nlos, "alarm_cmd": alarm_cmd}
    CONTROL_FILE.write_text(json.dumps(payload), encoding="utf-8")


def _send_gateway_rpc(method: str, params: dict) -> None:
    """Queue a downlink RPC command (`ui_commands.jsonl`) that works on BOTH physical Serial and Simulator!"""
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    with UI_CMD_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"method": method, "params": params}) + "\n")


st.set_page_config(
    page_title="HCMUT IoT HK261 - Smart Fall Detection & Room Monitoring",
    page_icon="🏠",
    layout="wide",
)

ensure_background_gateway_running()
gw_cfg_now = load_gateway_config().get("gateway", {})
is_serial_hw_mode = gw_cfg_now.get("source_mode", "simulator") == "serial"

# Sidebar: Hardware / Simulator Status & Live Buzzer RPC Controls (`T08`, `T10`)
with st.sidebar:
    st.header("🎛️ Gateway Control Panel")
    if is_serial_hw_mode:
        st.success(
            f"🔌 **Physical Board Mode (USB Serial)**\n\n"
            f"Port: `{gw_cfg_now.get('serial_port', 'COM4')}` @ `{gw_cfg_now.get('serial_baudrate', 115200)} bps`"
        )
    else:
        st.info("💻 **Simulator Mode** (Set `source_mode: serial` in `gateway/config.yaml` for physical YoloUNO boards)")
        selected_scenario = st.selectbox(
            "Simulation Scenario (Hardware-Free Mode)",
            options=VirtualYoloUnoCluster.SCENARIOS,
            index=1,
            help="Select an activity scenario to test the Random Forest classifier and alerting pipeline.",
        )
        sim_dist = st.slider("Tx–Rx Distance (m)", 1.0, 3.5, 2.0, 0.5)
        sim_nlos = st.checkbox("Through-Wall / Obstacle (NLOS)", value=False)

        if st.button("▶ Apply Scenario", type="primary", use_container_width=True):
            _write_sim_control(selected_scenario, sim_dist, sim_nlos, alarm_cmd=None)

    st.divider()
    st.subheader("🚨 Local Alert Control — LCD & Traffic Light (`T08`)")
    c_on, c_off = st.columns(2)
    if c_on.button("🚨 Alarm ON", use_container_width=True):
        _send_gateway_rpc("setAlarm", {"active": True})
    if c_off.button("🔕 Mute Alarm", use_container_width=True):
        _send_gateway_rpc("setAlarm", {"active": False})

    st.divider()
    auto_refresh = st.toggle("Auto Refresh (0.6s)", value=True)

st.title("🏠 Smart Indoor Fall Detection & Room Monitoring System")
st.caption(
    "Faculty of Computer Science & Engineering — HCMUT (HK261) | "
    "**DHT20 (`T01`)** + **ESP-NOW 20 Hz (`T06`)** + **Random Forest CPU (`T09`)** + "
    "**PC Gateway & Streamlit (`T08`, `T10`, `N01`)** + **CoreIOT & Telegram (`T11–T13`, `T15`)**"
)


def _read_live_state() -> dict:
    if LIVE_STATE_FILE.exists():
        try:
            return json.loads(LIVE_STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _read_events_list() -> list[dict]:
    events: list[dict] = []
    if EVENTS_LOG_FILE.exists():
        try:
            for ln in EVENTS_LOG_FILE.read_text(encoding="utf-8").splitlines()[-100:]:
                if ln.strip():
                    try:
                        events.append(json.loads(ln))
                    except json.JSONDecodeError:
                        pass
        except Exception:
            pass
    return events


_refresh_kpi = 0.5 if auto_refresh else None
_refresh_charts = 0.6 if auto_refresh else None
_refresh_cloud = 1.5 if auto_refresh else None


@st.fragment(run_every=_refresh_kpi)
def render_live_kpi_header() -> None:
    live_state = _read_live_state()
    if not live_state:
        st.info("Initializing 20 Hz CSI stream and loading scikit-learn Random Forest model...")
        return

    n1 = live_state.get("node1", {})
    n2 = live_state.get("node2", {})
    ai = live_state.get("ai", {})
    rule = live_state.get("rulechain", {})

    # Safety / Emergency Status Banner
    if rule.get("triggered"):
        st.error(f"🚨 **{rule.get('alarm_type')}** — {rule.get('telegram_text')}")
    elif live_state.get("alarm_active"):
        st.warning("🚨 **LOCAL HARDWARE ALARM ACTIVE** — Press **Mute Alarm** (left sidebar) or tap Node 2 button to silence.")
    elif n2.get("csi_state") == "WALKING":
        st.info("🚶 Normal walking movement detected inside the room.")
    else:
        st.success("✅ Room Status SAFE — No fall emergency or SOS alert detected.")

    # Top 4 KPI Cards
    k1, k2, k3, k4 = st.columns(4)
    k1.metric(
        "AI Activity State (`T09`)",
        n2.get("csi_state", "--"),
        f"Conf: {n2.get('ai_conf', 0)*100:.1f}% ({ai.get('infer_ms', 0):.1f} ms CPU)",
    )
    k2.metric(
        "CSI Wave Fluctuation (2s)",
        f"Std = {n2.get('signal_std', 0):.2f}",
        f"Max - Min = {n2.get('signal_range', 0):.2f}",
    )
    k3.metric(
        "DHT20 Environment (`T01`)",
        f"{n1.get('room_temp_c', 27.5):.1f} °C / {n1.get('room_humi_rh', 65.0):.1f}%",
        f"ESP-NOW Loss: {n1.get('pkt_loss_pct', 0):.2f}%",
    )
    k4.metric(
        "Alarm & CoreIOT Sync",
        "ACTIVE 🚨" if live_state.get("alarm_active") else "OFF (Safe)",
        f"MQTT Synced: {live_state.get('coreiot_published', 0)} msgs",
    )


render_live_kpi_header()

tab_live, tab_history, tab_cloud = st.tabs(
    [
        "📈 Real-Time Monitoring (`T10`)",
        "🔍 Alert History Assistant (`N01`)",
        "☁️ CoreIOT MQTT & Telegram (`T12`, `T13`, `T15`)",
    ]
)


@st.fragment(run_every=_refresh_charts)
def render_live_charts() -> None:
    live_state = _read_live_state()
    if not live_state:
        st.info("Waiting for first 2.0-second CSI window from Node 2...")
        return

    ai = live_state.get("ai", {})
    c_left, c_right = st.columns(2)

    # Line Chart 1: Moving-Average CSI disturbance over the 2.0-second window
    curve = ai.get("activity_curve", [0.0] * 40)
    t_axis = np.round(np.arange(len(curve)) / 20.0, 2)
    fig_wave = go.Figure()
    fig_wave.add_trace(
        go.Scatter(
            x=t_axis,
            y=curve,
            mode="lines+markers",
            name="CSI Disturbance (Moving Avg)",
            line=dict(color="#2563eb", width=2.5),
            marker=dict(size=4),
        )
    )
    fig_wave.update_layout(
        title="2-Second Wi-Fi CSI Disturbance Curve (20 Hz, Moving Average)",
        xaxis_title="Time in Window (seconds)",
        yaxis_title="Mean Disturbance Across 52 Subcarriers",
        height=320,
        margin=dict(l=20, r=20, t=42, b=20),
        uirevision="csi_wave_keep",
    )
    c_left.plotly_chart(fig_wave, width="stretch", key="chart_csi_wave")

    # Bar Chart: Random Forest 4-class probability distribution (`T09`)
    probs_vals = ai.get("probs", [0, 0, 0, 0])
    probs_chart = go.Figure(
        data=[
            go.Bar(
                x=["EMPTY", "STATIONARY", "WALKING", "FALL"],
                y=probs_vals,
                marker_color=["#64748b", "#10b981", "#f59e0b", "#ef4444"],
                text=[f"{p*100:.1f}%" for p in probs_vals],
                textposition="auto",
            )
        ]
    )
    probs_chart.update_layout(
        title="Activity Classification Probabilities — Random Forest CPU (`T09`)",
        yaxis=dict(range=[0, 1.08], title="Probability"),
        height=320,
        margin=dict(l=20, r=20, t=42, b=20),
        uirevision="rf_probs_keep",
    )
    c_right.plotly_chart(probs_chart, width="stretch", key="chart_rf_probs")

    # Line Chart 2: Rolling history of Signal Std/Range & Room Temp/Humidity
    hist = live_state.get("history", [])
    if hist:
        df_h = pd.DataFrame(hist)
        c_h1, c_h2 = st.columns(2)

        fig_std = go.Figure()
        fig_std.add_trace(go.Scatter(x=df_h["time"], y=df_h["signal_std"], mode="lines", name="CSI Signal Std"))
        fig_std.add_trace(go.Scatter(x=df_h["time"], y=df_h["signal_range"], mode="lines", name="Max - Min Range"))
        fig_std.update_layout(
            title="CSI Standard Deviation (Std) & Peak-to-Peak (Max - Min) Trend",
            height=265,
            margin=dict(l=20, r=20, t=40, b=20),
            uirevision="std_trend_keep",
        )
        c_h1.plotly_chart(fig_std, width="stretch", key="chart_std_trend")

        fig_env = go.Figure()
        fig_env.add_trace(go.Scatter(x=df_h["time"], y=df_h["temp_c"], mode="lines+markers", name="Temperature (°C)"))
        fig_env.add_trace(go.Scatter(x=df_h["time"], y=df_h["humi_rh"], mode="lines", name="Humidity (%RH)"))
        fig_env.update_layout(
            title="Room Temperature & Humidity Over Time (DHT20 — `T01`)",
            height=265,
            margin=dict(l=20, r=20, t=40, b=20),
            uirevision="env_trend_keep",
        )
        c_h2.plotly_chart(fig_env, width="stretch", key="chart_env_trend")


@st.fragment(run_every=_refresh_cloud)
def render_cloud_logs() -> None:
    live_state = _read_live_state()
    st.subheader(f"☁️ CoreIOT Gateway MQTT Sync Journal (`{live_state.get('coreiot_mode', 'SYNC')}`)")
    if MQTT_JOURNAL_FILE.exists():
        tail_lines = MQTT_JOURNAL_FILE.read_text(encoding="utf-8").splitlines()[-8:]
        for raw_ln in reversed(tail_lines):
            st.code(raw_ln, language="json")
    else:
        st.caption("No MQTT messages recorded yet.")

    st.divider()
    st.subheader("📨 Telegram Alert Log (`T13`)")
    if TELEGRAM_LOG_FILE.exists():
        tg_lines = TELEGRAM_LOG_FILE.read_text(encoding="utf-8").splitlines()[-6:]
        for raw_ln in reversed(tg_lines):
            st.code(raw_ln, language="json")
    else:
        st.caption("No Telegram alerts sent yet.")


with tab_live:
    render_live_charts()

with tab_history:
    st.subheader("🔍 Alert History Query Assistant (`N01`)")
    st.write("Reads directly from `gateway/runtime/event_log.jsonl` and answers statistics about recorded events.")
    if st.button("🔄 Refresh Alert Log", key="btn_refresh_log"):
        pass

    live_now = _read_live_state()
    events_list = _read_events_list()
    preset = st.selectbox("Select a quick preset question:", options=PRESET_QUESTIONS)
    custom_q = st.text_input("Or type your own question:", value=preset)
    if custom_q:
        st.info(answer_event_history_query(custom_q, live_now, events_list))

    st.divider()
    st.subheader("📋 Alert Event History Table (`event_log.jsonl`)")
    if not events_list:
        st.caption(
            "No alerts recorded yet. Trigger a fall or hold the Node 2 SOS button (>3s) to log an event!"
        )
    else:
        df_ev = pd.DataFrame(reversed(events_list))
        cols_show = ["timestamp", "alarm_type", "state", "ai_conf", "signal_std", "temp_c", "humi_rh", "telegram_text"]
        st.dataframe(df_ev[[c for c in cols_show if c in df_ev.columns]], width="stretch")

with tab_cloud:
    render_cloud_logs()
