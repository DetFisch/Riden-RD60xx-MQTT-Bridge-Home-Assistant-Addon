# Add-on container releases

`.github/addons.json` lists the installable add-on directories and their image names.
The helper reads the release version and the complete architecture list from each
add-on configuration. The configured image, when present, must match this plan.
`build.yaml` / `build.json` architecture-specific base images are passed to Docker.

Every push to the default branch tests and builds the images, using native AMD64
and ARM64 runners and QEMU for the declared 32-bit architectures. Pull requests
build without publishing. Builds have separate caches per add-on and architecture.
After all builds pass, the workflow publishes and signs the versioned images and
`latest`. Existing version tags remain unchanged; bump `version` for each release.
Home Assistant pulls the tag matching `version`, not `latest`.

Verify new GHCR package visibility. Keep images from private repositories private.
Their users must configure a GHCR registry login in Home Assistant Supervisor;
access to the source repository does not also authenticate container pulls.
Public add-ons require the final image package to be public before enabling the
`image` setting. Intermediate architecture packages can remain private.

Offline validation:

```sh
python -m pip install PyYAML==6.0.3
python .github/scripts/test_addon_images.py
python .github/scripts/addon_images.py matrix
```
