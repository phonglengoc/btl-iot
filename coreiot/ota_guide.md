# CoreIOT Gateway, RuleChain, Telegram Alert & OTA Firmware Update Guide (`T11–T15`)

This guide walks through importing the pre-built configuration files in `coreiot/` onto **`https://app.coreiot.io`** (ThingsBoard-based IoT platform) and verifying Over-the-Air firmware updates (**OTA — `T14`**, 2.0 points).

---

## 1. Create Device Profile & Gateway Device on CoreIOT (`T11`, `T15`)

1. Sign in to `https://app.coreiot.io`.
2. Navigate to **Profiles -> Device profiles -> + Add device profile**:
   - Name: **`CSI_Radar_Profile`**.
   - In the **Rule chain** tab, assign `CSI_Fall_And_Intrusion_Smart_RuleChain` (after importing it in Step 2).
3. Navigate to **Entities -> Devices -> + Add new device**:
   - Create a device named **`PC_Local_CSI_Gateway`** and check **`Is gateway` (`true`)**.
   - Click **Copy access token** and paste it into `COREIOT_GATEWAY_TOKEN` inside `.env` or `gateway/config.yaml` (never commit real tokens to GitHub).
   - When `python gateway/main_gateway.py` starts, both child devices **`YoloUNO_Node1_Tx`** and **`YoloUNO_Node2_Rx`** are automatically provisioned and synchronized via `v1/gateway/connect` (`T15`).

---

## 2. Import RuleChain & Configure Telegram Bot (`T12`, `T13`)

1. Navigate to **Rule chains -> + Add rule chain -> Import rule chain** and upload `coreiot/rulechain_fall_sec.json`.
2. Open device `YoloUNO_Node2_Rx` -> **Attributes -> Server attributes**, and add two secret attributes (kept out of GitHub):
   - `telegramBotToken`: Bot token issued by `@BotFather` on Telegram.
   - `telegramChatId`: Target family/caregiver group chat ID for emergency alerts.
3. Click **Apply changes** (bottom-right checkmark icon in the RuleChain editor).

---

## 3. Import Remote Monitoring & Control Dashboard (`T11`)

1. Navigate to **Dashboards -> + Add dashboard -> Import dashboard** and select `coreiot/dashboard_template.json`.
2. Open the imported Dashboard -> click **Edit mode -> Entity aliases** (top-right filter icon):
   - Bind `alias_node1_tx` to device `YoloUNO_Node1_Tx`.
   - Bind `alias_node2_rx` to device `YoloUNO_Node2_Rx`.
   - Save the Dashboard to display real-time telemetry charts and RPC control switches.

---

## 4. Over-the-Air (OTA) Firmware Update Procedure for YoloUNO ESP32-S3 (`T14`)

The partition table `firmware/partitions_16mb_ota.csv` allocates two symmetric OTA slots (`ota_0`: `4 MB`, `ota_1`: `4 MB`) with **SHA-256** integrity verification and automatic **Rollback** if the new image fails to boot cleanly.

### Step 1: Build Upgraded Firmware (`v1.1.0`) and Compute SHA-256 Checksum
```powershell
# Update FIRMWARE_VERSION to "1.1.0" in firmware/include/config.h, then build:
pio run -d firmware/node2_rx_edge_ai -e yolo_uno

# Compute the SHA-256 checksum of firmware.bin:
Get-FileHash firmware\node2_rx_edge_ai\.pio\build\yolo_uno\firmware.bin -Algorithm SHA256
```

### Step 2: Upload Firmware Package to CoreIOT OTA Repository
1. Navigate to **OTA updates -> + Add package** on `app.coreiot.io`:
   - **Title**: `yolo_uno_node2_rx_edge_ai` (matches `FIRMWARE_TITLE_NODE2`).
   - **Version**: `1.1.0`.
   - **Package type**: `Firmware`.
   - Upload the compiled `firmware.bin` and select **`SHA-256`** as the checksum algorithm.
2. Open device `YoloUNO_Node2_Rx` -> assign firmware package `1.1.0` or trigger via RPC from the Dashboard:
   - Method: `triggerOtaUpdate`
   - Params: `{"version": "1.1.0", "sha256": "<64-char-hex-sha256>"}`
3. Observe Serial Monitor output: the board streams `4096-byte` chunks, prints `[OTA-T14] SHA-256 verified OK`, reboots into `ota_1`, and reports `[OTA-T14] Boot partition verified OK -> Rollback cancelled`.
