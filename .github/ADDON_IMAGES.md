# Add-on container releases

`.github/addons.json` lists the installable add-on directories and their image names.
The helper reads the release version and the complete architecture list from each
add-on configuration. The configured image, when present, must match this plan.
Base images and build defaults live in the Dockerfile; deprecated build files are rejected.

Every push to the default branch tests and builds the images, using native AMD64
and ARM64 runners. Only the currently supported Home Assistant architectures
`amd64` and `aarch64` are accepted. Pull requests
build without publishing. Builds have separate caches per add-on and architecture.
After all builds pass, the workflow publishes and signs the versioned images and
`latest`. Existing version tags remain unchanged; bump `version` for each release.
Home Assistant pulls the tag matching `version`, not `latest`.

Verify new GHCR package visibility. Keep images from private repositories private.
Their users must configure a GHCR registry login in Home Assistant Supervisor;
access to the source repository does not also authenticate container pulls.
Public add-ons require the final image package to be public before enabling the
`image` setting. Intermediate architecture packages can remain private.

A manual workflow dispatch can publish from a release branch before merging its
version/configuration change to the default branch, so images exist first.

Offline validation:

```sh
python -m pip install PyYAML==6.0.3
python .github/scripts/test_addon_images.py
python .github/scripts/addon_images.py matrix
```

Publication is gated by frenck/action-addon-linter v2.21, the actual Supervisor
2026.10.3 configuration/default-options validators (pinned image digest),
repository tests, successful builds on every declared architecture, and
inspection of io.hass.type=app, architecture and exact version on each
locally loaded image. A failed check prevents release publication.
Schema validation runs without networking, Docker socket, host devices
or persistent data, and does not start a Supervisor service.
A real HA installation and device/network integration remain separate tests.

Each built image also passes its configured runtime dependency/syntax check
without networking, host capabilities, persistent mounts or application startup.
