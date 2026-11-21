# CoreIOT Gateway, Dashboard, RuleChain & Telegram Setup Guide (`T11`, `T12`, `T13`, `T15`)

This guide walks through configuring **`https://app.coreiot.io`** (ThingsBoard-based IoT platform) for the **Smart Indoor Fall Detection & Room Monitoring System**.

---

## 1. Create Device Profile & Gateway Device on CoreIOT (`T11`, `T15`)

1. Sign in at `https://app.coreiot.io`.
2. Navigate to **Profiles -> Device profiles -> + Add device profile**:
   - Name: **`Smart_Room_Fall_Profile`**.
   - In the **Rule chain** tab, assign `Smart_Room_Fall_Alert_RuleChain` (after importing it in Step 2).
3. Navigate to **Entities -> Devices -> + Add new device**:
   - Create a device named **`PC_Local_Fall_Gateway`** and check **`Is gateway` (`true`)**.
   - Click **Copy access token** and paste it into `COREIOT_GATEWAY_TOKEN` inside `.env` or `gateway/config.yaml` (never commit real tokens to GitHub).
   - When you run `python gateway/main_gateway.py`, both child devices (**`YoloUNO_Node1_Tx`** and **`YoloUNO_Node2_Rx`**) are automatically registered and synchronized over MQTT (`v1/gateway/connect`, `v1/gateway/telemetry`).

---

## 2. Import RuleChain & Configure Telegram Bot (`T12`, `T13`)

1. Navigate to **Rule chains -> + Add rule chain -> Import rule chain** and upload `coreiot/rulechain_fall.json`.
2. Alert pipeline overview:
   - Persist incoming time-series telemetry -> Check condition `(msg.csi_state === 'FALL' && msg.ai_conf >= 0.80) || msg.sos_triggered === true` -> Create CoreIOT Alarm + Send Telegram Bot message + Send downlink RPC to turn ON the buzzer.
3. Open device `YoloUNO_Node2_Rx` -> **Attributes -> Server attributes**, and add two private attributes:
   - `telegramBotToken`: Bot token issued by `@BotFather` on Telegram.
   - `telegramChatId`: Target caregiver/family chat ID.
4. Click **Apply changes** to activate the RuleChain.

---

## 3. Import Remote Monitoring & RPC Control Dashboard (`T11`)

1. Navigate to **Dashboards -> + Add dashboard -> Import dashboard** and upload `coreiot/dashboard_template.json`.
2. Open the imported dashboard -> click **Edit mode -> Entity aliases**:
   - Bind `alias_node1_tx` to `YoloUNO_Node1_Tx`.
   - Bind `alias_node2_rx` to `YoloUNO_Node2_Rx`.
3. Save the dashboard to monitor live DHT20 temperature/humidity, AI fall classification, CSI wave trends, alarm logs, and remote buzzer RPC switches.
