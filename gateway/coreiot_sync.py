"""
CoreIOT Gateway MQTT Synchronization, Simple RuleChain Logic & Telegram Alert (`T08`, `T12`, `T13`, `T15`).

Uses standard CoreIOT / ThingsBoard Gateway MQTT topics:
  - `v1/gateway/connect`    : Auto-registers `YoloUNO_Node1_Tx` & `YoloUNO_Node2_Rx`
  - `v1/gateway/telemetry`  : Publishes synchronized room temperature/humidity & AI fall detection JSON
  - `v1/gateway/attributes` : Syncs device attributes (`fall_threshold`, `ai_model`)
  - `v1/gateway/rpc`        : Receives remote alarm control (`setAlarm`) from CoreIOT Dashboard

Security Compliance:
  Reads tokens from environment variables (`COREIOT_GATEWAY_TOKEN`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`)
  or `gateway/config.yaml`. When placeholder tokens are present, runs in `Offline Mock Sync` mode
  and logs exact MQTT JSON packets to `gateway/runtime/coreiot_mqtt_journal.jsonl`.
"""

from __future__ import annotations

import json
import os
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Callable

import paho.mqtt.client as mqtt


def evaluate_coreiot_rulechain_logic(telemetry_values: dict) -> dict:
    """
    Simple RuleChain alert evaluation (`T12`) matching `coreiot/rulechain_fall.json`:
      1) If `sos_triggered == true` -> CRITICAL_SOS_EMERGENCY
      2) If `csi_state == "FALL"` and `ai_conf >= fall_threshold` -> CRITICAL_FALL_EMERGENCY
      3) Otherwise -> NORMAL
    """
    state = str(telemetry_values.get("csi_state", "EMPTY"))
    ai_conf = float(telemetry_values.get("ai_conf", 0.0))
    temp_c = float(telemetry_values.get("room_temp_c", 27.5))
    humi_rh = float(telemetry_values.get("room_humi_rh", 65.0))
    fall_th = float(telemetry_values.get("fall_threshold", 0.80))
    sos = bool(telemetry_values.get("sos_triggered", False))

    if sos:
        return {
            "triggered": True,
            "alarm_type": "CRITICAL_SOS_EMERGENCY",
            "ai_conf": round(ai_conf, 4),
            "telegram_text": (
                f"[SOS EMERGENCY] Manual SOS button was held on Node 2! "
                f"Room Temp: {temp_c:.1f}C, Humidity: {humi_rh:.1f}%. Please check immediately!"
            ),
        }

    if state == "FALL" and ai_conf >= fall_th:
        return {
            "triggered": True,
            "alarm_type": "CRITICAL_FALL_EMERGENCY",
            "ai_conf": round(ai_conf, 4),
            "telegram_text": (
                f"[FALL EMERGENCY] Human FALL detected in the monitored room! "
                f"AI Confidence: {round(ai_conf * 100)}%, "
                f"Room Temp: {temp_c:.1f}C, Humidity: {humi_rh:.1f}%. Please check immediately!"
            ),
        }

    return {
        "triggered": False,
        "alarm_type": "NORMAL",
        "ai_conf": round(ai_conf, 4),
        "telegram_text": "",
    }


class TelegramAlertClient:
    """Sends real Telegram Bot HTTP alerts (`T13`) or logs dry-run alerts when token is placeholder."""

    def __init__(
        self,
        bot_token: str = "",
        chat_id: str = "",
        cooldown_sec: float = 15.0,
        journal_path: Path | None = None,
    ) -> None:
        self.bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", bot_token).strip()
        self.chat_id = os.environ.get("TELEGRAM_CHAT_ID", chat_id).strip()
        self.cooldown_sec = float(cooldown_sec)
        self.journal_path = journal_path
        self._last_sent_ts: dict[str, float] = {}

    @property
    def is_live_configured(self) -> bool:
        return (
            bool(self.bot_token)
            and bool(self.chat_id)
            and not self.bot_token.startswith("REPLACE_")
            and not self.chat_id.startswith("REPLACE_")
        )

    def send_alert(self, alarm_type: str, message_text: str) -> dict:
        now = time.time()
        last = self._last_sent_ts.get(alarm_type, 0.0)
        if (now - last) < self.cooldown_sec:
            return {"sent": False, "reason": "cooldown", "mode": "live" if self.is_live_configured else "dry_run"}

        self._last_sent_ts[alarm_type] = now
        record = {
            "ts": round(now, 3),
            "alarm_type": alarm_type,
            "text": message_text,
            "mode": "live" if self.is_live_configured else "dry_run",
            "sent": False,
        }

        if self.is_live_configured:
            try:
                url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
                payload = urllib.parse.urlencode({"chat_id": self.chat_id, "text": message_text}).encode("utf-8")
                req = urllib.request.Request(url, data=payload, method="POST")
                with urllib.request.urlopen(req, timeout=5.0) as resp:
                    record["sent"] = resp.status == 200
            except Exception as exc:
                record["error"] = str(exc)
        else:
            record["sent"] = True  # Dry-run recorded locally

        if self.journal_path is not None:
            self.journal_path.parent.mkdir(parents=True, exist_ok=True)
            with self.journal_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        return record


class CoreIOTGatewaySync:
    """
    Synchronizes PC Local IoT Gateway with CoreIOT Cloud Gateway (`T08`, `T15`)
    and dispatches downlink RPC commands (`setAlarm`) back to Node 2.
    """

    def __init__(
        self,
        host: str = "app.coreiot.io",
        port: int = 1883,
        gateway_token: str = "",
        node1_name: str = "YoloUNO_Node1_Tx",
        node2_name: str = "YoloUNO_Node2_Rx",
        device_profile: str = "Smart_Room_Fall_Profile",
        runtime_dir: Path | str = "gateway/runtime",
        on_rpc_callback: Callable[[str, str, dict], dict] | None = None,
    ) -> None:
        self.host = os.environ.get("COREIOT_HOST", host)
        self.port = int(os.environ.get("COREIOT_PORT", port))
        self.gateway_token = os.environ.get("COREIOT_GATEWAY_TOKEN", gateway_token).strip()
        self.node1_name = node1_name
        self.node2_name = node2_name
        self.device_profile = device_profile
        self.runtime_dir = Path(runtime_dir)
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        self.journal_path = self.runtime_dir / "coreiot_mqtt_journal.jsonl"
        self.on_rpc_callback = on_rpc_callback

        self.is_mock_mode = (not self.gateway_token) or self.gateway_token.startswith("REPLACE_")
        self.connected = False
        self.published_count = 0
        self._lock = threading.Lock()
        self._client: mqtt.Client | None = None

    def start(self) -> None:
        if self.is_mock_mode:
            self.connected = True
            self._log_journal("v1/gateway/connect", {"device": self.node1_name, "type": self.device_profile})
            self._log_journal("v1/gateway/connect", {"device": self.node2_name, "type": self.device_profile})
            return

        if hasattr(mqtt, "CallbackAPIVersion"):
            self._client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="pc_local_fall_gateway")
        else:
            self._client = mqtt.Client(client_id="pc_local_fall_gateway")

        self._client.username_pw_set(self.gateway_token)
        self._client.on_connect = self._on_connect
        self._client.on_message = self._on_message
        self._client.on_disconnect = self._on_disconnect

        try:
            self._client.connect_async(self.host, self.port, keepalive=30)
            self._client.loop_start()
        except Exception as exc:
            print(f"[CoreIOT T15] Live connect warning ({exc}) -> keeping local journal active.")

    def stop(self) -> None:
        if self._client is not None:
            try:
                self._client.loop_stop()
                self._client.disconnect()
            except Exception:
                pass
        self.connected = False

    def _on_connect(self, client, userdata, flags, rc, *args) -> None:
        rc_val = int(rc) if isinstance(rc, (int, float)) else getattr(rc, "value", 0)
        if rc_val == 0:
            self.connected = True
            client.subscribe("v1/gateway/rpc", qos=1)
            client.subscribe("v1/gateway/attributes", qos=1)
            client.publish(
                "v1/gateway/connect",
                json.dumps({"device": self.node1_name, "type": self.device_profile}),
                qos=1,
            )
            client.publish(
                "v1/gateway/connect",
                json.dumps({"device": self.node2_name, "type": self.device_profile}),
                qos=1,
            )
            print(f"[CoreIOT T15] Connected to {self.host}:{self.port} & registered child nodes!")

    def _on_disconnect(self, client, userdata, *args) -> None:
        self.connected = False

    def _on_message(self, client, userdata, msg) -> None:
        try:
            payload = json.loads(msg.payload.decode("utf-8"))
        except Exception:
            return

        self._log_journal(f"DOWNLINK:{msg.topic}", payload)

        if msg.topic == "v1/gateway/rpc" and self.on_rpc_callback is not None:
            dev = str(payload.get("device", self.node2_name))
            data = payload.get("data", {})
            req_id = data.get("id", 0)
            method = str(data.get("method", ""))
            params = data.get("params", {})
            reply = self.on_rpc_callback(dev, method, params)
            resp_pkt = {"device": dev, "id": req_id, "data": reply}
            client.publish("v1/gateway/rpc", json.dumps(resp_pkt), qos=1)

        elif msg.topic == "v1/gateway/attributes" and self.on_rpc_callback is not None:
            dev = str(payload.get("device", self.node2_name))
            attrs = payload.get("data", {})
            self.on_rpc_callback(dev, "sharedAttributesUpdate", attrs)

    def publish_gateway_telemetry(self, node1_values: dict, node2_values: dict) -> dict:
        """Publish synchronized multi-node telemetry JSON to `v1/gateway/telemetry` (`T15`)."""
        ts_ms = int(time.time() * 1000)
        gateway_payload = {
            self.node1_name: [{"ts": ts_ms, "values": node1_values}],
            self.node2_name: [{"ts": ts_ms, "values": node2_values}],
        }
        body = json.dumps(gateway_payload, ensure_ascii=False)
        if self._client is not None and not self.is_mock_mode and self.connected:
            self._client.publish("v1/gateway/telemetry", body, qos=1)

        self._log_journal("v1/gateway/telemetry", gateway_payload)
        with self._lock:
            self.published_count += 1
        return gateway_payload

    def publish_gateway_attributes(self, node2_attrs: dict) -> None:
        payload = {self.node2_name: node2_attrs}
        if self._client is not None and not self.is_mock_mode and self.connected:
            self._client.publish("v1/gateway/attributes", json.dumps(payload), qos=1)
        self._log_journal("v1/gateway/attributes", payload)

    def inject_simulated_rpc(self, device: str, method: str, params: dict | bool | float) -> dict:
        """Allow Local UI / Tests to verify downlink RPC handling (`T08`)."""
        self._log_journal("DOWNLINK:v1/gateway/rpc", {"device": device, "data": {"method": method, "params": params}})
        if self.on_rpc_callback is not None:
            return self.on_rpc_callback(device, method, {"value": params} if not isinstance(params, dict) else params)
        return {"ok": False}

    def _log_journal(self, topic: str, payload: dict) -> None:
        entry = {
            "ts": round(time.time(), 3),
            "mode": "mock" if self.is_mock_mode else "live",
            "topic": topic,
            "payload": payload,
        }
        with self._lock:
            with self.journal_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
