# Riden RD60xx MQTT Bridge Home Assistant Add-on

Bridge for Riden RD60xx power supplies that exposes status and controls over MQTT.
It works with the **standard Riden WiFi module** out of the box. **No flashing, no hardware mods, no firmware changes required**.

---

## What it does

- Accepts TCP connections from the Riden PSU (default port 8080)
- Publishes status to MQTT
- Provides control via MQTT (output, voltage, current, OVP/OCP, etc.)
- Automatically adds Home Assistant MQTT Discovery entities

---

## Prerequisites

- Home Assistant with the **MQTT integration** enabled
- **Mosquitto broker add-on** installed and running

---

## Installation

### 1) Add this repository to Home Assistant

[![Open your Home Assistant instance and show the add add-on repository dialog with a specific repository URL pre-filled.](https://my.home-assistant.io/badges/supervisor_add_addon_repository.svg)](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2FDetFisch%2FRiden-RD60xx-MQTT-Bridge-Home-Assistant-Addon)

### 2) Install the add-on

- Find **Riden RD60xx MQTT Bridge Home Assistant Add-on** in the add-on store
- Install

### 3) Configure and start

- Open the add-on settings
- Configure MQTT (defaults work with Mosquitto)
- **Start the add-on**

If MQTT Discovery is enabled, entities will appear automatically in Home Assistant.

---

## Configuration (optional)

Minimal (Mosquitto defaults):

```yaml
mqtt_host: core-mosquitto
mqtt_port: 1883
mqtt_username: ""
mqtt_password: ""
mqtt_prefix: riden
mqtt_discovery_enabled: true
```

---

## WiFi Provisioning Tool (optional)

If your PSU WiFi module is not yet configured to connect to your Home Asisstant IP, use the provisioning tool:

### Tool location

```text
riden_wifi_provision.py
```

### Requirements

- Python 3 on **Windows / Linux / macOS**
- The machine running the tool must be connected to the **same 2.4 GHz WiFi** you want the PSU to join

### Usage

```bash
python riden_wifi_provision.py
```

You will be asked for:

- **Server IP**: The IP the PSU should connect to after setup (use your Home Assistant IP)
- **SSID** and **Password** of your 2.4 GHz WiFi
- Optional **BSSID** (AP MAC address)

After provisioning, the PSU connects to your WiFi Router and then to the **server IP** you entered (Home Assistant).

### Example with CLI commands (alternative)

```bash
python riden_wifi_provision.py --server-ip 192.168.1.50 --ssid "MyWiFi" --password "secret" --bssid "b8:c1:ac:a6:35:93"
```

---

## Troubleshooting

- Make sure MQTT is reachable and credentials are correct
- Ensure the PSU WiFi module is in the correct setup mode before provisioning

If the PSU connection is lost, its Connected sensor turns off and its controls
become unavailable until the PSU reconnects. Temporary Modbus errors on a live
connection are retried after at least five seconds. Commands that fail are not
replayed after reconnecting; check the device's reported state before retrying.

## Container images and tests

GitHub Actions tests the bridge with Python 3.12 and builds native amd64 and
aarch64 images. Successful builds on `main` publish signed versioned images and
`latest` to `ghcr.io/detfisch/riden_rd60xx_home_assistant_addon`.
Home Assistant installations continue to build locally from the add-on's
Dockerfile, including the other architectures listed in `config.yaml`.

Run the regression tests without connecting to a PSU, MQTT broker, or Home Assistant:

```bash
python -m pip install -r riden_rd60xx_home_assistant_addon/requirements.txt
python -B -m unittest discover -s tests -v
```

---

## Credits

- Based on the open-source Riden RD60xx MQTT bridge by pgreenland: https://github.com/pgreenland/RidenRD60xxMQTT
- ESPTouch / SmartConfig provisioning inspired by the ESPTouch Android reference implementation
- MQTT Discovery entities included for Home Assistant
- Container build workflow contributed by [Andras Elso (elbandi)](https://github.com/elbandi)
- PSU disconnect and log-flooding report by [Dewieinns](https://github.com/Dewieinns)

