# Riden RD60xx MQTT Bridge in Docker

Run the bridge as a standalone Compose service alongside Home Assistant Container. The bridge accepts connections from the standard Riden WiFi module and exposes PSU status and controls through MQTT Discovery.

For the Supervisor-managed Home Assistant App, see the [`main` branch](https://github.com/DetFisch/Riden-RD60xx-MQTT-Bridge-Home-Assistant-Addon/tree/main).

## Install

You need Docker with the Compose plugin, an MQTT broker reachable from the Docker host and its containers, and Home Assistant connected to that broker via the MQTT integration. A broker on another Docker network needs a reachable published address. If the broker shares a Docker network with this service, use its service name there.

```sh
git clone --branch codex/docker https://github.com/DetFisch/Riden-RD60xx-MQTT-Bridge-Home-Assistant-Addon.git riden-docker
cd riden-docker
cp .env.example .env
cp options.example.json options.json
chmod 600 options.json
```

Set `mqtt_host` in `options.json` to your broker's reachable DNS name or IP. Supply its credentials if required. `localhost` resolves inside the Riden container, and `core-mosquitto` is normally specific to Supervisor installations. Use the address of the Docker host or broker that the container can actually reach.

In `.env`, you can set `RIDEN_BIND_ADDRESS` to the host's LAN IP, `RIDEN_PORT` to its published TCP port, and `TZ` to the time zone used when setting the PSU clock. Keep TCP 8080 reachable from the PSU on your LAN. The stock WiFi provisioning tool uses port 8080. Limit access to your trusted LAN, as the PSU TCP endpoint has no authentication.

```sh
docker compose config --quiet
docker compose up -d --build
docker compose logs -f riden
```

The Dockerfile builds the bridge from this checkout. Local settings and credentials stay in the two ignored files. The bridge keeps its connection state in memory, while the MQTT broker stores retained discovery data according to its configuration.

## Home Assistant and the PSU

Add the **MQTT** integration in Home Assistant and connect it to the same broker. With MQTT Discovery enabled, the PSU's device and entities appear after it connects. The bridge listens on TCP, so there is no web UI to add to the sidebar. You can use its entities on any Home Assistant dashboard.

If the WiFi module is not yet configured, run `python riden_wifi_provision.py` on a computer connected to the target 2.4 GHz WiFi network. Follow the prompts and enter the **Docker host's LAN IP** as the server. The PSU connects to that address on TCP 8080. With an already provisioned PSU, keep its existing host address and port or provision it again for the new host.

When moving from the App, stop the old instance before starting this service. Keep the same broker, `mqtt_prefix` and `mqtt_discovery_prefix` to preserve the topic layout. Copy only the needed options into `options.json` and replace the Supervisor MQTT address with one reachable by this container.

## Change settings

Edit `options.json`, then run `docker compose up -d --force-recreate riden` to reload the settings and refresh the file mount. The same command also applies edits to `.env`.

| Setting in `options.json` | Purpose |
| --- | --- |
| `mqtt_host`, `mqtt_port` | Broker address and port. Use the standard non-TLS MQTT listener on a trusted network. |
| `mqtt_username`, `mqtt_password`, `mqtt_client_id` | Broker credentials and optional explicit client ID. Use a different client ID for each instance. |
| `mqtt_prefix` | MQTT topic prefix for PSU status and controls. |
| `mqtt_discovery_enabled`, `mqtt_discovery_prefix` | Enable HA discovery and set its prefix. The default HA prefix is `homeassistant`. |
| `default_update_period` | Polling interval in seconds. |
| `psu_addresses` | Comma-separated Modbus addresses to try, for example `"1,2"`. |
| `set_clock_on_connection` | Set the PSU clock when the bridge connects. |
| `ip_to_identity_cache_timeout_secs`, `mqtt_reconnect_delay_secs` | Optional connection cache lifetime and reconnect delay. |
| `log_level` | Python logging level. |

Use JSON booleans (`true` and `false`) and numbers where shown in the example.

## Update or stop

```sh
git pull --ff-only
docker compose up -d --build
docker compose logs --tail=100 riden
```

To stop the service, run `docker compose down`. Back up `options.json` and `.env` if you want to reinstall without re-entering settings.

If the PSU does not appear, check the broker address and credentials, MQTT Discovery in Home Assistant, and the published TCP 8080 connection from the PSU to this host. Port 8080 expects Modbus RTU traffic from the WiFi module.

## Credits

- Based on the [Riden RD60xx MQTT bridge by pgreenland](https://github.com/pgreenland/RidenRD60xxMQTT).
- ESPTouch / SmartConfig provisioning inspired by the ESPTouch Android reference implementation.
- Home Assistant App build workflow contributed by [Andras Elso (elbandi)](https://github.com/elbandi).
- PSU disconnect and log-flooding report by [Dewieinns](https://github.com/Dewieinns).
