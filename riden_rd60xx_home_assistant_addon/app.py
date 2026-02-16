import asyncio
import json
import logging
import os
import sys
import urllib.request
import urllib.error

from rd60xx_to_mqtt import RD60xxToMQTT

OPTIONS_PATH = "/data/options.json"
SERVICES_PATH = "/data/services.json"
SUPERVISOR_MQTT_URL = "http://supervisor/services/mqtt"


def load_options():
    try:
        with open(OPTIONS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except Exception as exc:
        logging.warning("Failed to read %s: %s", OPTIONS_PATH, exc)
        return {}


def load_services_file():
    try:
        with open(SERVICES_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            if not isinstance(data, dict):
                return None
            if isinstance(data.get("mqtt"), dict):
                return data.get("mqtt")
            services = data.get("services")
            if isinstance(services, dict) and isinstance(services.get("mqtt"), dict):
                return services.get("mqtt")
    except FileNotFoundError:
        return None
    except Exception as exc:
        logging.debug("Failed to read %s: %s", SERVICES_PATH, exc)
    return None


def fetch_mqtt_service():
    token = os.getenv("SUPERVISOR_TOKEN")
    if not token:
        return None
    req = urllib.request.Request(
        SUPERVISOR_MQTT_URL,
        headers={"Authorization": f"Bearer {token}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.URLError as exc:
        logging.debug("Supervisor services API not reachable: %s", exc)
        return None
    except Exception as exc:
        logging.debug("Failed to parse Supervisor services response: %s", exc)
        return None

    if isinstance(payload, dict) and isinstance(payload.get("data"), dict):
        return payload.get("data")
    return payload if isinstance(payload, dict) else None


def get_mqtt_service():
    service = load_services_file()
    if service:
        return service
    return fetch_mqtt_service()


def main():
    options = load_options()

    hostname = options.get("mqtt_host")
    port = int(options.get("mqtt_port", 1883) or 1883)
    client_id = options.get("mqtt_client_id")
    username = options.get("mqtt_username")
    password = options.get("mqtt_password")
    mqtt_base_topic = options.get("mqtt_prefix", "riden")

    ip_to_identity_cache_timeout_secs = float(options.get("ip_to_identity_cache_timeout_secs", 0) or 0)
    mqtt_reconnect_delay_secs = float(options.get("mqtt_reconnect_delay_secs", 5) or 5)
    set_clock_on_connection = bool(options.get("set_clock_on_connection", True))
    default_update_period = float(options.get("default_update_period", 0) or 0)
    mqtt_discovery_enabled = bool(options.get("mqtt_discovery_enabled", False))
    mqtt_discovery_prefix = options.get("mqtt_discovery_prefix", "homeassistant")
    psu_addresses_raw = options.get("psu_addresses")
    if psu_addresses_raw:
        psu_addresses = [int(a.strip()) for a in str(psu_addresses_raw).split(",") if a.strip()]
    else:
        psu_addresses = [int(options.get("psu_address", 1) or 1)]

    log_level = str(options.get("log_level", "info")).upper()
    log_format = "[%(asctime)s] [%(name)s] [%(levelname)s] %(message)s"
    logging.basicConfig(stream=sys.stdout, level=getattr(logging, log_level, logging.INFO), format=log_format)

    service = get_mqtt_service()
    if service:
        if not hostname or hostname == "core-mosquitto":
            hostname = service.get("host") or hostname
        if not options.get("mqtt_port"):
            try:
                port = int(service.get("port", port))
            except (TypeError, ValueError):
                pass
        if not username:
            username = service.get("username")
        if not password:
            password = service.get("password")
        if service.get("ssl"):
            logging.warning("MQTT service reports SSL enabled; this add-on does not configure TLS settings.")

    if not hostname:
        logging.error("Missing mqtt_host and no Supervisor MQTT service available")
        sys.exit(1)

    mqtt_bridge = RD60xxToMQTT(
        hostname,
        port,
        client_id=client_id,
        username=username,
        password=password,
        ca_cert=None,
        client_cert=None,
        client_key=None,
        insecure=False,
        mqtt_base_topic=mqtt_base_topic,
        psu_identity_to_name={},
        ip_to_identity_cache_timeout_secs=ip_to_identity_cache_timeout_secs,
        mqtt_reconnect_delay_secs=mqtt_reconnect_delay_secs,
        set_clock_on_connection=set_clock_on_connection,
        default_update_period=default_update_period,
        mqtt_discovery_enabled=mqtt_discovery_enabled,
        mqtt_discovery_prefix=mqtt_discovery_prefix,
        psu_addresses=psu_addresses,
    )

    asyncio.run(mqtt_bridge.run())


if __name__ == "__main__":
    main()
