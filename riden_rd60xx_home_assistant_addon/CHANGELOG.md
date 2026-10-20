# Changelog

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
