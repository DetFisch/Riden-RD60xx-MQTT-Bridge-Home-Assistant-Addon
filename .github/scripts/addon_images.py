"""Build metadata and immutable release manifests for Home Assistant add-ons.

Run from the repository root. Requires PyYAML and, for publish, Docker Buildx.
Versions and architectures always come from the installable add-on config.
"""

import argparse
import json
import os
from pathlib import Path
import re
import subprocess

import yaml


PLATFORMS = {
    "amd64": ("linux/amd64", "ubuntu-24.04"),
    "aarch64": ("linux/arm64", "ubuntu-24.04-arm"),
}


def load_addons(root=Path(".")):
    plan = json.loads((root / ".github/addons.json").read_text(encoding="utf-8"))
    addons = []
    for entry in plan:
        folder = root / entry["path"]
        if not folder.resolve().is_relative_to(root.resolve()):
            raise ValueError("Add-on path must be inside the repository")
        configs = [folder / name for name in ("config.yaml", "config.yml", "config.json")
                   if (folder / name).is_file()]
        if len(configs) != 1 or not (folder / "Dockerfile").is_file():
            raise ValueError(f"Expected one config and a Dockerfile in {folder}")
        config = yaml.safe_load(configs[0].read_text(encoding="utf-8"))
        image = entry["image"]
        if not re.fullmatch(r"ghcr\.io/[a-z0-9_.-]+/[a-z0-9_./{}-]+", image):
            raise ValueError(f"Invalid image: {image}")
        if "{" in image.replace("{arch}", "") or "}" in image.replace("{arch}", ""):
            raise ValueError(f"Unsupported image placeholder: {image}")
        owner = os.environ.get("GITHUB_REPOSITORY_OWNER")
        if owner and image.split("/")[1] != owner.lower():
            raise ValueError("Image namespace must match the repository owner")
        if config.get("image", image) != image:
            raise ValueError(f"Image in {configs[0]} differs from .github/addons.json")
        version = str(config["version"])
        if not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,100}", version):
            raise ValueError(f"Invalid release tag: {version}")
        arches = config["arch"]
        if not arches or len(arches) != len(set(arches)) or set(arches) - PLATFORMS.keys():
            raise ValueError(f"Unsupported or duplicate architectures: {arches}")
        build = {}
        for name in ("build.yaml", "build.yml", "build.json"):
            if (folder / name).is_file():
                build = yaml.safe_load((folder / name).read_text(encoding="utf-8"))
                break
        addons.append(dict(entry, version=version, arch=arches, build=build,
                           name=config["name"], description=config["description"]))
    if not addons or len({a["image"] for a in addons}) != len(addons):
        raise ValueError("Expected at least one add-on and unique image names")
    return addons


def arch_image(image, arch):
    if "{arch}" in image:
        return image.replace("{arch}", arch)
    prefix, name = image.rsplit("/", 1)
    return f"{prefix}/{arch}-{name}"


def matrix(addons):
    result = []
    for addon in addons:
        for arch in addon["arch"]:
            platform, runner = PLATFORMS[arch]
            args = {
                "BUILD_ARCH": arch, "BUILD_VERSION": addon["version"],
                "BUILD_NAME": addon["name"], "BUILD_DESCRIPTION": addon["description"],
                "BUILD_REPOSITORY": os.environ.get("GITHUB_REPOSITORY", ""),
                "BUILD_REF": os.environ.get("GITHUB_SHA", ""),
            }
            build_from = addon["build"].get("build_from", {}).get(arch)
            if build_from:
                args["BUILD_FROM"] = build_from
            result.append(dict(path=addon["path"], arch=arch, platform=platform,
                               runner=runner, image=arch_image(addon["image"], arch),
                               version=addon["version"],
                               scope=f"{Path(addon['path']).name}-{arch}",
                               build_args="\n".join(f"{k}={v}" for k, v in args.items())))
    return {"include": result}


def docker(*args):
    return subprocess.run(["docker", "buildx", "imagetools", *args],
                          check=True, capture_output=True, text=True).stdout


def publish(addons):
    sha = os.environ["GITHUB_SHA"]
    if not re.fullmatch(r"[a-f0-9]{40}", sha):
        raise ValueError("Expected a full commit SHA")
    sign = os.environ.get("SIGN_IMAGES") == "true"
    signed = set()
    for addon in addons:
        sources = [f"{arch_image(addon['image'], arch)}:sha-{sha}" for arch in addon["arch"]]
        targets = ([arch_image(addon["image"], arch) for arch in addon["arch"]]
                   if "{arch}" in addon["image"] else [addon["image"]])
        for index, image in enumerate(targets):
            refs = [sources[index]] if "{arch}" in addon["image"] else sources
            version_ref = f"{image}:{addon['version']}"
            docker("create", "--tag", f"{image}:latest", *refs)
            existing = subprocess.run(
                ["docker", "buildx", "imagetools", "inspect", version_ref],
                capture_output=True, text=True,
            )
            tags = [f"{image}:latest"]
            if existing.returncode:
                if not any(message in existing.stderr.lower() for message in
                           ("not found", "manifest unknown", "name unknown")):
                    raise RuntimeError(existing.stderr)
                docker("create", "--tag", version_ref, *refs)
                tags.append(version_ref)
            else:
                print(f"Keep existing immutable release {version_ref}", flush=True)
            for ref in tags:
                descriptor = json.loads(docker("inspect", ref, "--format", "{{json .Manifest}}"))
                digest = descriptor["digest"]
                print(f"Published {ref}@{digest}", flush=True)
                if sign and (image, digest) not in signed:
                    subprocess.run(["bash", ".github/scripts/sign_image.sh", f"{image}@{digest}"], check=True)
                    signed.add((image, digest))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("matrix", "publish"))
    args = parser.parse_args()
    addons = load_addons()
    if args.command == "matrix":
        print(json.dumps(matrix(addons), separators=(",", ":")))
    else:
        publish(addons)


if __name__ == "__main__":
    main()
