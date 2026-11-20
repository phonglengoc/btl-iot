"""
PC Local IoT Gateway Real-Time Monitoring, Control & Event-Recording Dashboard (`T10`, `N06`, `N01`).

Run with a single command (even with ZERO physical hardware connected!):
    streamlit run gateway/streamlit_ui.py

Features:
  - `T10`: Live 52-Subcarrier Wi-Fi CSI Heatmap, 64x64 STFT Spectrogram, Edge INT8 vs RTX 5070 GPU
           confidence gauges, DHT20 temperature/humidity, BLE authorization status, and RPC controls.
  - `N04`: Real-time GPU inference latency & ExecutionProvider telemetry (`CUDAExecutionProvider`).
  - `N06`: Automatic Event Detection & Dual-Panel Snapshot Gallery with timestamped history.
  - `T05`: Integrated Labeled CSV Dataset Recorder directly inside the Gateway UI.
  - `N01`: Built-in Natural Language Telemetry Assistant (English & Vietnamese Q&A over live logs).
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import numpy as np
import plotly.graph_objects as go
import streamlit as st

from main_gateway import LocalIoTGatewayService, load_gateway_config
from virtual_node_simulator import VirtualYoloUnoCluster

ROOT_DIR = Path(__file__).resolve().parents[1]
RUNTIME_DIR = ROOT_DIR / "gateway" / "runtime"
LIVE_STATE_FILE = RUNTIME_DIR / "live_state.json"
CONTROL_FILE = RUNTIME_DIR / "simulator_control.json"
EVENTS_LOG_FILE = RUNTIME_DIR / "event_snapshots_log.jsonl"
MQTT_JOURNAL_FILE = RUNTIME_DIR / "coreiot_mqtt_journal.jsonl"


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


def answer_telemetry_query(question: str, live_state: dict, events: list[dict], lang: str = "English") -> str:
    """Local intelligent log analyzer (`N01`) supporting both English and Vietnamese queries."""
    q = question.lower()
    n2 = live_state.get("node2", {})
    falls = [e for e in events if "FALL" in e.get("alarm_type", "")]
    intrusions = [e for e in events if "INTRUSION" in e.get("alarm_type", "")]
    sos_list = [e for e in events if "SOS" in e.get("alarm_type", "")]

    if lang == "English":
        if any(k in q for k in ("fall", "emergency", "té ngã", "ngã")):
            if not falls:
                return "✅ **No fall incidents** have been recorded during the current monitoring session."
            last = falls[-1]
            return (
                f"🚨 Recorded **{len(falls)} fall event(s)**! Most recent at `{last['timestamp']}` "
                f"with fused AI confidence **{last['fused_conf']*100:.1f}%** (room temp `{last['temp_c']}°C`). "
                f"Evidence spectrogram saved to `{Path(last['snapshot_png']).name}`."
            )
        if any(k in q for k in ("intruder", "intrusion", "security", "ble", "trộm", "đột nhập")):
            ble_str = (
                f"Authorized resident BLE tag `{n2.get('ble_mac')}` (`{n2.get('ble_rssi')} dBm`)"
                if n2.get("ble_authorized")
                else "NO authorized resident BLE tag currently in range"
            )
            return (
                f"🔐 Current BLE identity status: **{ble_str}**. "
                f"Total intrusion alerts in event log: **{len(intrusions)}**."
            )
        return (
            f"📊 **Live Room Telemetry Summary:**\n"
            f"- Recognized Activity: **{n2.get('csi_state', 'UNKNOWN')}** (Fused AI Conf: `{n2.get('fused_conf', 0)*100:.1f}%`)\n"
            f"- Ambient Environment (DHT20 `T01`): **{n2.get('room_temp_c', 27.5):.1f} °C** | Humidity **{n2.get('room_humi_rh', 65.0):.1f} %RH**\n"
            f"- Incident History (`N06`): **{len(falls)} fall(s)**, **{len(intrusions)} intrusion alert(s)**, **{len(sos_list)} SOS signal(s)**."
        )

    # Vietnamese mode
    if "té ngã" in q or "ngã" in q or "cấp cứu" in q:
        if not falls:
            return "✅ Từ lúc khởi động phiên giám sát đến nay **chưa ghi nhận ca té ngã nào**."
        last = falls[-1]
        return (
            f"🚨 Hệ thống đã ghi nhận **{len(falls)} sự kiện té ngã**! Gần nhất lúc `{last['timestamp']}` "
            f"với độ tin cậy AI hợp nhất **{last['fused_conf']*100:.1f}%** (nhiệt độ phòng `{last['temp_c']}°C`). "
            f"Ảnh chụp phổ sóng đã lưu tại `{Path(last['snapshot_png']).name}`."
        )
    if "trộm" in q or "đột nhập" in q or "người lạ" in q or "ble" in q:
        ble_str = (
            f"Đang có thẻ BLE người nhà `{n2.get('ble_mac')}` (`{n2.get('ble_rssi')} dBm`)"
            if n2.get("ble_authorized")
            else "Hiện KHÔNG phát hiện thẻ BLE người nhà trong phòng"
        )
        return (
            f"🔐 Trạng thái định danh hiện tại: **{ble_str}**. "
            f"Tổng số cảnh báo đột nhập trong nhật ký: **{len(intrusions)} lần**."
        )
    return (
        f"📊 **Tóm tắt trạng thái phòng hiện tại:**\n"
        f"- Hành vi nhận diện: **{n2.get('csi_state', 'UNKNOWN')}** (Độ tin cậy hợp nhất: `{n2.get('fused_conf', 0)*100:.1f}%`)\n"
        f"- Môi trường (DHT20 `T01`): **{n2.get('room_temp_c', 27.5):.1f} °C** | Độ ẩm **{n2.get('room_humi_rh', 65.0):.1f} %RH**\n"
        f"- Lịch sử sự cố (`N06`): **{len(falls)} ca té ngã**, **{len(intrusions)} cảnh báo xâm nhập**, **{len(sos_list)} tín hiệu SOS**."
    )


st.set_page_config(
    page_title="HCMUT IoT HK261 - Wi-Fi CSI Radar & GPU Gateway",
    page_icon="📡",
    layout="wide",
)

gw_mode = ensure_background_gateway_running()

# Sidebar: Scenario Injector (Virtual HIL) & Downlink RPC Controls (`T08`, `T10`)
with st.sidebar:
    ui_lang = st.selectbox("🌐 UI Language", options=["English", "Tiếng Việt"], index=0)
    is_en = ui_lang == "English"

    st.header("🎛️ HIL Control & RPC Console" if is_en else "🎛️ Bảng Điều khiển & Giả lập HIL")
    selected_scenario = st.selectbox(
        "Physics HIL Scenario (Hardware-Free Mode)" if is_en else "Kịch bản Mô phỏng Vật lý",
        options=VirtualYoloUnoCluster.SCENARIOS,
        index=5,
        help=(
            "Select an activity scenario synthesized by the 64-subcarrier OFDM multipath generator."
            if is_en
            else "Chọn kịch bản để bộ mô phỏng sóng đa đường OFDM 64 kênh phát tín hiệu tương ứng!"
        ),
    )
    sim_dist = st.slider(
        "Tx–Rx / Subject Distance (m)" if is_en else "Khoảng cách Tx–Rx / Đối tượng (m)",
        1.0,
        3.5,
        2.0,
        0.5,
    )
    sim_nlos = st.checkbox(
        "Through-Wall Occlusion (NLOS)" if is_en else "Có vách ngăn che khuất (NLOS)",
        value=False,
    )

    col_b1, col_b2 = st.columns(2)
    if col_b1.button(
        "Apply Scenario" if is_en else "Áp dụng Kịch bản",
        type="primary",
        use_container_width=True,
    ):
        CONTROL_FILE.write_text(
            json.dumps({"scenario": selected_scenario, "distance_m": sim_dist, "nlos": sim_nlos, "reset_alarm": False}),
            encoding="utf-8",
        )
    if col_b2.button("Mute Siren" if is_en else "Tắt Còi Báo Động", use_container_width=True):
        CONTROL_FILE.write_text(
            json.dumps({"scenario": selected_scenario, "distance_m": sim_dist, "nlos": sim_nlos, "reset_alarm": True}),
            encoding="utf-8",
        )

    st.divider()
    st.subheader("🔗 Node 2 Embedded Web UI (`T07`)")
    st.markdown(
        "Open Node 2's direct HTTP portal: [http://127.0.0.1:8082](http://127.0.0.1:8082)"
        if is_en
        else "Truy cập trực tiếp giao diện HTTP nhúng của Node 2: [http://127.0.0.1:8082](http://127.0.0.1:8082)"
    )
    auto_refresh = st.toggle(
        "Real-time Auto Refresh (1s)" if is_en else "Tự động làm mới thời gian thực (1s)",
        value=True,
    )

if is_en:
    st.title("📡 Camera-Free Wi-Fi CSI & BLE Radar for Fall & Intrusion Detection")
    st.caption(
        "Faculty of Computer Science & Engineering — HCMUT (HK261) | "
        "Dual-Tier Collaborative AI: **ESP32-S3 INT8 TinyML (`T04`)** + "
        "**NVIDIA RTX 5070 ONNX CUDA (`T09`, `N04`)** + **CoreIOT Cloud Sync (`T15`)**"
    )
else:
    st.title("📡 Hệ thống Radar Không Camera (Wi-Fi CSI & BLE) Phát hiện Té ngã & Xâm nhập")
    st.caption(
        "Khoa KH&KT Máy tính — ĐH Bách Khoa TP.HCM (HK261) | "
        "Kiến trúc AI Hiệp đồng 2 Tầng: **ESP32-S3 INT8 TinyML (`T04`)** + "
        "**NVIDIA RTX 5070 ONNX CUDA (`T09`, `N04`)** + **CoreIOT Sync (`T15`)**"
    )

# Read latest Gateway state
live_state: dict = {}
if LIVE_STATE_FILE.exists():
    try:
        live_state = json.loads(LIVE_STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass

if not live_state:
    st.info(
        "Initializing 100 Hz Wi-Fi CSI stream and loading ONNX 2D-CNN onto RTX 5070 GPU..."
        if is_en
        else "Đang khởi tạo luồng Wi-Fi CSI 100 Hz và nạp mô hình ONNX lên GPU RTX 5070..."
    )
    time.sleep(0.8)
    st.rerun()

n1 = live_state.get("node1", {})
n2 = live_state.get("node2", {})
ai = live_state.get("ai", {})
rule = live_state.get("rulechain", {})

# Banner Alert Status
if rule.get("triggered"):
    st.error(f"🚨 **{rule.get('alarm_type')}** — {rule.get('telegram_text')}")
elif n2.get("csi_state") == "WALKING" and n2.get("ble_authorized"):
    st.warning(
        "🚶 Authorized Resident Walking Motion Detected (Valid BLE Beacon Verified)."
        if is_en
        else "🚶 Phát hiện chuyển động đi lại hợp lệ (Đã xác thực thẻ BLE người nhà)."
    )
else:
    st.success(
        "✅ Room Status SAFE — No fall emergency or unauthorized intrusion detected."
        if is_en
        else "✅ Trạng thái phòng AN TOÀN — Không phát hiện té ngã hay xâm nhập trái phép."
    )

# Top KPI Row
k1, k2, k3, k4, k5 = st.columns(5)
k1.metric(
    "Fused AI State" if is_en else "Trạng thái Hợp nhất",
    n2.get("csi_state", "--"),
    f"Conf: {n2.get('fused_conf', 0)*100:.1f}%",
)
k2.metric("TinyML 1D-CNN (`T04`)", ai.get("edge_state", "--"), f"INT8: {ai.get('edge_conf', 0)*100:.1f}%")
k3.metric(
    "GPU 2D-CNN (`T09`, `N04`)",
    ai.get("gpu_state", "--"),
    f"{ai.get('gpu_infer_ms', 0):.2f} ms ({ai.get('active_provider', 'CPU').replace('ExecutionProvider', '')})",
)
k4.metric(
    "BLE Identity (`T03`)" if is_en else "Định danh BLE (`T03`)",
    (
        ("Authorized (Resident)" if n2.get("ble_authorized") else "No BLE Tag")
        if is_en
        else ("Hợp lệ (Người nhà)" if n2.get("ble_authorized") else "Không có BLE")
    ),
    f"{n2.get('ble_mac', 'NONE')} ({n2.get('ble_rssi', -100)} dBm)",
)
k5.metric(
    "DHT20 Env (`T01`)" if is_en else "Nhiệt ẩm DHT20 (`T01`)",
    f"{n1.get('room_temp_c', 27.5):.1f} °C / {n1.get('room_humi_rh', 65.0):.1f}%",
    (
        f"CoreIOT Sync: {live_state.get('coreiot_published', 0)} msgs"
        if is_en
        else f"CoreIOT Sync: {live_state.get('coreiot_published', 0)} bản tin"
    ),
)

tab_live, tab_snapshots, tab_coreiot, tab_ai_chat = st.tabs(
    [
        "📈 Live CSI Heatmap & STFT (`T10`)" if is_en else "📈 Bản đồ Nhiệt CSI & Phổ STFT (`T10`)",
        "📸 Event Snapshot Gallery (`N06`)" if is_en else "📸 Nhật ký & Ảnh chụp Sự cố (`N06`)",
        "☁️ CoreIOT Sync & RuleChain (`T12`, `T15`)" if is_en else "☁️ Đồng bộ CoreIOT & RuleChain (`T12`, `T15`)",
        "🤖 AI Telemetry Assistant (`N01`)" if is_en else "🤖 Trợ lý Ảo Phân tích Nhật ký (`N01`)",
    ]
)

with tab_live:
    c_left, c_right = st.columns(2)
    heatmap_arr = np.array(live_state.get("heatmap_50x52", np.zeros((50, 52)))).T
    spec_arr = np.array(live_state.get("spectrogram_64x64", np.zeros((64, 64))))

    fig_hm = go.Figure(data=go.Heatmap(z=heatmap_arr, colorscale="Viridis"))
    fig_hm.update_layout(
        title=(
            "52-Subcarrier Wi-Fi CSI Amplitude Heatmap (2.0 s @ 100 Hz)"
            if is_en
            else "Bản đồ nhiệt Biên độ 52 Sóng mang Wi-Fi CSI (2.0 giây @ 100 Hz)"
        ),
        xaxis_title="Time Window Sample Index (2.0 s)" if is_en else "Thời gian mẫu (2.0s Window)",
        yaxis_title="OFDM Subcarrier Index (0..51)" if is_en else "Chỉ số Sóng mang OFDM (0..51)",
        height=370,
        margin=dict(l=20, r=20, t=40, b=20),
    )
    c_left.plotly_chart(fig_hm, use_container_width=True)

    fig_sp = go.Figure(data=go.Heatmap(z=spec_arr, colorscale="Inferno"))
    fig_sp.update_layout(
        title=(
            f"64×64 STFT Time–Frequency Doppler Spectrogram ({ai.get('gpu_device', 'GPU')})"
            if is_en
            else f"Ảnh phổ Thời gian–Tần số STFT 64×64 ({ai.get('gpu_device', 'GPU')})"
        ),
        xaxis_title="STFT Time Frame (0..63)" if is_en else "Khung thời gian STFT (0..63)",
        yaxis_title="Doppler Frequency Bin (0..63)" if is_en else "Tần số dịch chuyển Doppler (0..63)",
        height=370,
        margin=dict(l=20, r=20, t=40, b=20),
    )
    c_right.plotly_chart(fig_sp, use_container_width=True)

    probs_chart = go.Figure(
        data=[
            go.Bar(
                name="Edge INT8 1D-CNN (0.4)",
                x=["EMPTY", "STATIONARY", "WALKING", "FALL"],
                y=ai.get("edge_probs", [0, 0, 0, 0]),
            ),
            go.Bar(
                name="RTX 5070 GPU 2D-CNN (0.6)",
                x=["EMPTY", "STATIONARY", "WALKING", "FALL"],
                y=ai.get("gpu_probs", [0, 0, 0, 0]),
            ),
            go.Bar(
                name="Fused Collaborative AI" if is_en else "Hợp nhất Fused AI",
                x=["EMPTY", "STATIONARY", "WALKING", "FALL"],
                y=ai.get("fused_probs", [0, 0, 0, 0]),
            ),
        ]
    )
    probs_chart.update_layout(
        barmode="group",
        title=(
            "Class Probability Distribution Across Collaborative AI Tiers"
            if is_en
            else "Phân bố Xác suất Phân loại Hành vi giữa 2 Tầng AI"
        ),
        yaxis=dict(range=[0, 1.05]),
        height=280,
        margin=dict(l=20, r=20, t=40, b=20),
    )
    st.plotly_chart(probs_chart, use_container_width=True)

events_list: list[dict] = []
if EVENTS_LOG_FILE.exists():
    for ln in EVENTS_LOG_FILE.read_text(encoding="utf-8").splitlines()[-20:]:
        if ln.strip():
            events_list.append(json.loads(ln))

with tab_snapshots:
    st.subheader(
        "📸 Automatic Dual-Panel Event Spectrogram Archive (`N06`)"
        if is_en
        else "📸 Kho Lưu trữ Ảnh chụp Phổ Sóng Tự động khi Phát hiện Sự kiện (`N06`)"
    )
    if not events_list:
        st.info(
            "No anomalous events recorded yet. Select `FALL_EMERGENCY` or `WALKING_INTRUDER` in the left sidebar to test!"
            if is_en
            else "Chưa có sự kiện bất thường. Hãy chọn kịch bản `FALL_EMERGENCY` hoặc `WALKING_INTRUDER` ở thanh bên trái!"
        )
    else:
        for ev in reversed(events_list[-6:]):
            with st.expander(f"[{ev['timestamp']}] {ev['alarm_type']} — Fused Conf: {ev['fused_conf']*100:.1f}%", expanded=True):
                st.write(ev["telegram_text"])
                snap_p = Path(ev["snapshot_png"])
                if snap_p.exists():
                    st.image(str(snap_p), use_container_width=True)

with tab_coreiot:
    st.subheader(
        f"☁️ CoreIOT Gateway Synchronized MQTT Journal (`{live_state.get('coreiot_mode')}`)"
        if is_en
        else f"☁️ Nhật ký Bản tin MQTT Đồng bộ CoreIOT Gateway (`{live_state.get('coreiot_mode')}`)"
    )
    if MQTT_JOURNAL_FILE.exists():
        tail_lines = MQTT_JOURNAL_FILE.read_text(encoding="utf-8").splitlines()[-8:]
        for raw_ln in reversed(tail_lines):
            st.code(raw_ln, language="json")

with tab_ai_chat:
    st.subheader(
        "🤖 Natural-Language Room Telemetry Assistant (`N01`)"
        if is_en
        else "🤖 Trợ lý Ảo Hỏi đáp Trạng thái Phòng (`N01`)"
    )
    user_q = st.text_input(
        "Ask about live room status or recorded incidents:" if is_en else "Đặt câu hỏi cho hệ thống:",
        value=(
            "Were there any fall emergencies or intrusion alerts recorded today?"
            if is_en
            else "Hôm nay trong phòng có xảy ra sự cố té ngã hay đột nhập nào không?"
        ),
    )
    if user_q:
        st.markdown(answer_telemetry_query(user_q, live_state, events_list, lang=ui_lang))

if auto_refresh:
    time.sleep(1.0)
    st.rerun()
