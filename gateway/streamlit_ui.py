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
EVENTS_LOG_FILE = RUNTIME_DIR / "event_log.jsonl"
MQTT_JOURNAL_FILE = RUNTIME_DIR / "coreiot_mqtt_journal.jsonl"
TELEGRAM_LOG_FILE = RUNTIME_DIR / "telegram_alerts.jsonl"

PRESET_QUESTIONS = [
    "Thống kê tổng số lần phát hiện té ngã?",
    "Sự cố khẩn cấp gần nhất xảy ra lúc nào?",
    "Nhiệt độ và độ ẩm phòng hiện tại (và khi xảy ra sự cố) là bao nhiêu?",
    "Có lần nào người dùng nhấn nút SOS khẩn cấp không?",
    "Tóm tắt toàn bộ trạng thái phòng và lịch sử cảnh báo?",
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

    if any(k in q for k in ("gần nhất", "mới nhất", "lúc nào", "latest", "recent")):
        if not events:
            return "✅ Hiện tại **chưa có sự cố khẩn cấp nào** được ghi nhận trong file `event_log.jsonl`."
        last = events[-1]
        return (
            f"🕒 **Sự cố gần nhất:** `{last['alarm_type']}` vào lúc **`{last['timestamp']}`**.\n"
            f"- Trạng thái AI: **{last['state']}** (độ tin cậy `{last['ai_conf']*100:.1f}%`, Std = `{last['signal_std']}`)\n"
            f"- Môi trường lúc xảy ra sự cố: Nhiệt độ **`{last['temp_c']} °C`**, Độ ẩm **`{last['humi_rh']} %RH`**."
        )

    if any(k in q for k in ("sos", "nút nhấn", "nút bấm")):
        if not sos_events:
            return "✅ Chưa ghi nhận lần nhấn nút **SOS khẩn cấp** nào trên Node 2."
        last_sos = sos_events[-1]
        return (
            f"🆘 Đã ghi nhận **{len(sos_events)} lần nhấn nút SOS khẩn cấp**! "
            f"Lần gần nhất lúc `{last_sos['timestamp']}` (nhiệt độ phòng `{last_sos['temp_c']} °C`)."
        )

    if any(k in q for k in ("nhiệt độ", "độ ẩm", "môi trường", "dht20", "temp", "humi")):
        cur_temp = float(n1.get("room_temp_c", 27.5))
        cur_humi = float(n1.get("room_humi_rh", 65.0))
        if events:
            avg_t = float(np.mean([float(e.get("temp_c", cur_temp)) for e in events]))
            avg_h = float(np.mean([float(e.get("humi_rh", cur_humi)) for e in events]))
            return (
                f"🌡️ **Thông số môi trường phòng (cảm biến I2C DHT20 - `T01`):**\n"
                f"- Hiện tại: Nhiệt độ **`{cur_temp:.1f} °C`** | Độ ẩm **`{cur_humi:.1f} %RH`**\n"
                f"- Trung bình trong **{len(events)} sự cố** đã ghi nhận: **`{avg_t:.1f} °C`** / **`{avg_h:.1f} %RH`**."
            )
        return (
            f"🌡️ **Thông số môi trường phòng hiện tại (DHT20 - `T01`):**\n"
            f"- Nhiệt độ: **`{cur_temp:.1f} °C`** | Độ ẩm: **`{cur_humi:.1f} %RH`**."
        )

    if any(k in q for k in ("té ngã", "ngã", "fall", "bao nhiêu lần", "số lần")):
        if not falls:
            return "✅ Từ lúc bắt đầu phiên giám sát đến nay **chưa phát hiện ca té ngã nào**."
        last = falls[-1]
        avg_conf = float(np.mean([float(e.get("ai_conf", 0.0)) for e in falls])) * 100.0
        return (
            f"🚨 Hệ thống đã ghi nhận tổng cộng **{len(falls)} lần phát hiện té ngã**!\n"
            f"- Lần gần nhất: **`{last['timestamp']}`** (độ tin cậy `{last['ai_conf']*100:.1f}%`, nhiệt độ `{last['temp_c']} °C`)\n"
            f"- Độ tin cậy trung bình của các ca té ngã: **`{avg_conf:.1f}%`**."
        )

    return (
        f"📊 **Tóm tắt trạng thái phòng & lịch sử cảnh báo (`N01`):**\n"
        f"- Trạng thái hiện tại: **{n2.get('csi_state', 'UNKNOWN')}** "
        f"(Độ tin cậy Random Forest: `{n2.get('ai_conf', 0)*100:.1f}%`, Std sóng: `{n2.get('signal_std', 0):.2f}`)\n"
        f"- Môi trường phòng (DHT20 `T01`): **{n1.get('room_temp_c', 27.5):.1f} °C** | Độ ẩm **{n1.get('room_humi_rh', 65.0):.1f} %RH**\n"
        f"- Tổng số bản ghi trong `event_log.jsonl`: **{len(events)} sự kiện** "
        f"(**{len(falls)} ca té ngã**, **{len(sos_events)} tín hiệu SOS**)."
    )


def _write_sim_control(scenario: str, dist: float, nlos: bool, alarm_cmd: bool | None = None) -> None:
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    payload = {"scenario": scenario, "distance_m": dist, "nlos": nlos, "alarm_cmd": alarm_cmd}
    CONTROL_FILE.write_text(json.dumps(payload), encoding="utf-8")


st.set_page_config(
    page_title="HCMUT IoT HK261 - Phát hiện Té ngã & Giám sát Phòng ở",
    page_icon="🏠",
    layout="wide",
)

ensure_background_gateway_running()

# Sidebar: Điều khiển mô phỏng & Bật/Tắt còi RPC (`T08`, `T10`)
with st.sidebar:
    st.header("🎛️ Bảng Điều khiển Gateway")
    selected_scenario = st.selectbox(
        "Kịch bản Mô phỏng (khi chưa cắm mạch)",
        options=VirtualYoloUnoCluster.SCENARIOS,
        index=1,
        help="Chọn trạng thái hoạt động để kiểm thử mô hình Random Forest và luồng cảnh báo!",
    )
    sim_dist = st.slider("Khoảng cách Tx–Rx (m)", 1.0, 3.5, 2.0, 0.5)
    sim_nlos = st.checkbox("Có vật cản / vách ngăn (NLOS)", value=False)

    if st.button("▶ Áp dụng Kịch bản", type="primary", use_container_width=True):
        _write_sim_control(selected_scenario, sim_dist, sim_nlos, alarm_cmd=None)

    st.divider()
    st.subheader("🔊 Điều khiển Còi Báo động (`T08`)")
    c_on, c_off = st.columns(2)
    if c_on.button("🚨 Bật Còi", use_container_width=True):
        _write_sim_control(selected_scenario, sim_dist, sim_nlos, alarm_cmd=True)
    if c_off.button("🔕 Tắt Còi", use_container_width=True):
        _write_sim_control(selected_scenario, sim_dist, sim_nlos, alarm_cmd=False)

    st.divider()
    auto_refresh = st.toggle("Tự động cập nhật (1 giây)", value=True)

st.title("🏠 Hệ thống Phát hiện Té ngã & Giám sát Phòng ở Thông minh")
st.caption(
    "ĐH Bách Khoa TP.HCM (HCMUT - HK261) | "
    "**DHT20 (`T01`)** + **ESP-NOW 20 Hz (`T06`)** + **Random Forest CPU (`T09`)** + "
    "**PC Gateway & Streamlit (`T08`, `T10`, `N01`)** + **CoreIOT & Telegram (`T11–T13`, `T15`)**"
)

live_state: dict = {}
if LIVE_STATE_FILE.exists():
    try:
        live_state = json.loads(LIVE_STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass

if not live_state:
    st.info("Đang khởi tạo luồng dữ liệu 20 Hz và nạp mô hình Random Forest (scikit-learn)...")
    time.sleep(0.8)
    st.rerun()

n1 = live_state.get("node1", {})
n2 = live_state.get("node2", {})
ai = live_state.get("ai", {})
rule = live_state.get("rulechain", {})

# Banner trạng thái an toàn / cảnh báo
if rule.get("triggered"):
    st.error(f"🚨 **{rule.get('alarm_type')}** — {rule.get('telegram_text')}")
elif n2.get("csi_state") == "WALKING":
    st.info("🚶 Đang có người di chuyển bình thường trong phòng.")
else:
    st.success("✅ Trạng thái phòng AN TOÀN — Không phát hiện té ngã hay tín hiệu SOS.")

# 4 thẻ KPI chính
k1, k2, k3, k4 = st.columns(4)
k1.metric(
    "Trạng thái AI (`T09`)",
    n2.get("csi_state", "--"),
    f"Tin cậy: {n2.get('ai_conf', 0)*100:.1f}% ({ai.get('infer_ms', 0):.1f} ms CPU)",
)
k2.metric(
    "Dao động Sóng CSI (2s)",
    f"Std = {n2.get('signal_std', 0):.2f}",
    f"Max - Min = {n2.get('signal_range', 0):.2f}",
)
k3.metric(
    "Nhiệt độ / Độ ẩm (`T01`)",
    f"{n1.get('room_temp_c', 27.5):.1f} °C / {n1.get('room_humi_rh', 65.0):.1f}%",
    f"Mất gói ESP-NOW: {n1.get('pkt_loss_pct', 0):.2f}%",
)
k4.metric(
    "Còi Báo động & CoreIOT",
    "ĐANG BẬT 🚨" if live_state.get("alarm_active") else "TẮT (An toàn)",
    f"MQTT Sync: {live_state.get('coreiot_published', 0)} bản tin",
)

# Đọc danh sách sự kiện từ file `event_log.jsonl` cho Tab N01
events_list: list[dict] = []
if EVENTS_LOG_FILE.exists():
    for ln in EVENTS_LOG_FILE.read_text(encoding="utf-8").splitlines()[-100:]:
        if ln.strip():
            try:
                events_list.append(json.loads(ln))
            except json.JSONDecodeError:
                pass

tab_live, tab_history, tab_cloud = st.tabs(
    [
        "📈 Giám sát Thời gian thực (`T10`)",
        "🔍 Tra cứu Lịch sử Cảnh báo (`N01`)",
        "☁️ Đồng bộ CoreIOT & Telegram (`T12`, `T13`, `T15`)",
    ]
)

with tab_live:
    c_left, c_right = st.columns(2)

    # Biểu đồ đường 1: Mức xáo trộn sóng trong cửa sổ 2 giây (đã làm mượt Moving Average)
    curve = ai.get("activity_curve", [0.0] * 40)
    t_axis = np.round(np.arange(len(curve)) / 20.0, 2)
    fig_wave = go.Figure()
    fig_wave.add_trace(
        go.Scatter(
            x=t_axis,
            y=curve,
            mode="lines+markers",
            name="Độ dao động sóng (Moving Avg)",
            line=dict(color="#2563eb", width=2.5),
            marker=dict(size=4),
        )
    )
    fig_wave.update_layout(
        title="Biểu đồ Dao động Sóng CSI trong Cửa sổ 2 giây (20 Hz, Trung bình trượt)",
        xaxis_title="Thời gian trong cửa sổ (giây)",
        yaxis_title="Mức xáo trộn trung bình 52 sóng mang",
        height=330,
        margin=dict(l=20, r=20, t=42, b=20),
    )
    c_left.plotly_chart(fig_wave, use_container_width=True)

    # Biểu đồ cột: Xác suất phân loại 4 lớp của Random Forest (`T09`)
    probs_chart = go.Figure(
        data=[
            go.Bar(
                x=["EMPTY", "STATIONARY", "WALKING", "FALL"],
                y=ai.get("probs", [0, 0, 0, 0]),
                marker_color=["#64748b", "#10b981", "#f59e0b", "#ef4444"],
                text=[f"{p*100:.1f}%" for p in ai.get("probs", [0, 0, 0, 0])],
                textposition="auto",
            )
        ]
    )
    probs_chart.update_layout(
        title="Xác suất Phân loại Hành vi — Random Forest CPU (`T09`)",
        yaxis=dict(range=[0, 1.08], title="Xác suất"),
        height=330,
        margin=dict(l=20, r=20, t=42, b=20),
    )
    c_right.plotly_chart(probs_chart, use_container_width=True)

    # Biểu đồ đường 2: Lịch sử Độ lệch chuẩn sóng & Nhiệt độ phòng theo thời gian
    hist = live_state.get("history", [])
    if hist:
        df_h = pd.DataFrame(hist)
        c_h1, c_h2 = st.columns(2)

        fig_std = go.Figure()
        fig_std.add_trace(go.Scatter(x=df_h["time"], y=df_h["signal_std"], mode="lines", name="Std sóng CSI"))
        fig_std.add_trace(go.Scatter(x=df_h["time"], y=df_h["signal_range"], mode="lines", name="Max - Min"))
        fig_std.update_layout(
            title="Xu hướng Độ lệch chuẩn (Std) & Biên độ (Max - Min)",
            height=270,
            margin=dict(l=20, r=20, t=40, b=20),
        )
        c_h1.plotly_chart(fig_std, use_container_width=True)

        fig_env = go.Figure()
        fig_env.add_trace(go.Scatter(x=df_h["time"], y=df_h["temp_c"], mode="lines+markers", name="Nhiệt độ (°C)"))
        fig_env.add_trace(go.Scatter(x=df_h["time"], y=df_h["humi_rh"], mode="lines", name="Độ ẩm (%RH)"))
        fig_env.update_layout(
            title="Nhiệt độ & Độ ẩm phòng theo thời gian (DHT20 - `T01`)",
            height=270,
            margin=dict(l=20, r=20, t=40, b=20),
        )
        c_h2.plotly_chart(fig_env, use_container_width=True)

with tab_history:
    st.subheader("🔍 Trợ lý Tra cứu Lịch sử Cảnh báo (`N01`)")
    st.write("Đọc trực tiếp từ tệp nhật ký sự kiện `gateway/runtime/event_log.jsonl` và thống kê theo câu hỏi.")

    preset = st.selectbox("Chọn nhanh câu hỏi mẫu:", options=PRESET_QUESTIONS)
    custom_q = st.text_input("Hoặc nhập câu hỏi của bạn:", value=preset)
    if custom_q:
        st.info(answer_event_history_query(custom_q, live_state, events_list))

    st.divider()
    st.subheader("📋 Bảng Nhật ký Sự kiện Cảnh báo (`event_log.jsonl`)")
    if not events_list:
        st.caption("Chưa có sự kiện cảnh báo nào. Hãy chọn kịch bản `FALL_EMERGENCY` hoặc `SOS_BUTTON_HOLD` ở thanh bên trái để kiểm thử!")
    else:
        df_ev = pd.DataFrame(reversed(events_list))
        cols_show = ["timestamp", "alarm_type", "state", "ai_conf", "signal_std", "temp_c", "humi_rh", "telegram_text"]
        st.dataframe(df_ev[[c for c in cols_show if c in df_ev.columns]], use_container_width=True)

with tab_cloud:
    st.subheader(f"☁️ Nhật ký Bản tin MQTT Đồng bộ CoreIOT (`{live_state.get('coreiot_mode')}`)")
    if MQTT_JOURNAL_FILE.exists():
        tail_lines = MQTT_JOURNAL_FILE.read_text(encoding="utf-8").splitlines()[-8:]
        for raw_ln in reversed(tail_lines):
            st.code(raw_ln, language="json")
    else:
        st.caption("Chưa có bản tin MQTT nào.")

    st.divider()
    st.subheader("📨 Nhật ký Tin nhắn Cảnh báo Telegram (`T13`)")
    if TELEGRAM_LOG_FILE.exists():
        tg_lines = TELEGRAM_LOG_FILE.read_text(encoding="utf-8").splitlines()[-6:]
        for raw_ln in reversed(tg_lines):
            st.code(raw_ln, language="json")
    else:
        st.caption("Chưa có tin nhắn cảnh báo Telegram nào được gửi.")

if auto_refresh:
    time.sleep(1.0)
    st.rerun()
