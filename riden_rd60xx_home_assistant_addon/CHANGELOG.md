# Changelog

## 1.0.8 - 2026-11-05

- Bound RTU receive work and memory before invoking pymodbus, preventing CPU
  spikes from malformed clients on port 8080. Close invalid streams with one
  short warning per connection.
- Assemble fragmented TCP replies, handle coalesced duplicates, and validate
  responses against the pending request, including write acknowledgements.
- Wake pending requests on disconnect, skip disconnected queued clients, and
  avoid stale bridges after a disconnect during discovery.
- Preserve the detected Modbus address across cached reconnects.
- Fix Windows asyncio socket keepalive setup.
- Round voltage, current, OVP and OCP to register precision instead of truncating
  floating-point artifacts (for example, 4.10 V becoming 4.09 V).
- Test real RTU/TCP traffic with a simulated PSU, malformed streams and a
  separate-process CPU regression check.

## 1.0.7 - 2026-10-20

- Align image metadata and configuration with current Home Assistant app requirements.
- Validate the Supervisor schema, default options and built image labels before publication.
- Keep versioned pre-built amd64/aarch64 images and immutable release tags.


## 1.0.6 - 2026-10-20

- Publish only supported 64-bit Home Assistant images (amd64 and aarch64).
- Remove obsolete 32-bit architecture declarations and build workarounds.

## 1.0.5 - 2026-10-20

- Build and publish versioned container images in GitHub Actions for faster Home Assistant installations and updates.
- Preserve release tags and build every architecture declared by the add-on.

## 1.0.4

- Stop polling dead PSU connections and publish disconnected status, including
  connections closed internally by pymodbus. A fresh inbound PSU connection can
  restore communication.
- Wait at least five seconds before retrying Modbus errors on a live connection,
  preventing a busy loop and log flooding, including during clock setup.
- Close the actual PSU client after unexpected worker errors.
- Retire old workers on reconnect and preserve cached model/serial metadata
  when multiple PSUs reconnect.
- Add regression tests and CI image builds for amd64 and aarch64, based on
  elbandi's contribution in PR #5. Read image versions from the add-on config and
  use a lowercase GHCR namespace. Pull requests build without publishing images.
