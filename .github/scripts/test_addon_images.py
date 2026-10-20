"""Offline checks for release tags, architecture mapping and publication errors."""

import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import addon_images as images


class ReleaseImagesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / ".github").mkdir()
        (self.root / ".github/addons.json").write_text(
            json.dumps([{"path": ".", "image": "ghcr.io/detfisch/example"}]))
        (self.root / "Dockerfile").write_text("FROM alpine\n")
        self.config = dict(name="Example", slug="example", description="Example app",
                           version="1.2.3", arch=["amd64", "aarch64"])
        self.write_config()

    def write_config(self):
        (self.root / "config.json").write_text(json.dumps(self.config))

    def test_all_declared_architectures_are_built(self):
        entries = images.matrix(images.load_addons(self.root))["include"]
        self.assertEqual([e["arch"] for e in entries], self.config["arch"])
        self.assertEqual(entries[1]["runner"], "ubuntu-24.04-arm")
        self.assertEqual(entries[1]["platform"], "linux/arm64")

    def test_deprecated_build_file_is_rejected(self):
        (self.root / "build.json").write_text("{}")
        with self.assertRaisesRegex(ValueError, "Deprecated"):
            images.load_addons(self.root)

    def test_config_image_must_match_published_image(self):
        self.config["image"] = "ghcr.io/detfisch/wrong"
        self.write_config()
        with self.assertRaisesRegex(ValueError, "differs"):
            images.load_addons(self.root)

    def test_invalid_architecture_is_rejected(self):
        self.config["arch"] = ["armv7"]
        self.write_config()
        with self.assertRaisesRegex(ValueError, "architectures"):
            images.load_addons(self.root)

    def test_existing_arch_placeholder_is_preserved(self):
        self.assertEqual(images.arch_image("ghcr.io/detfisch/app-{arch}", "aarch64"),
                         "ghcr.io/detfisch/app-aarch64")

    def test_private_repository_fork_cannot_publish_to_original_owner(self):
        with patch.dict(os.environ, {"GITHUB_REPOSITORY_OWNER": "different-owner"}):
            with self.assertRaisesRegex(ValueError, "namespace"):
                images.load_addons(self.root)

    def check_publish(self, result):
        addons = images.load_addons(self.root)
        with patch.dict(os.environ, {"GITHUB_SHA": "a" * 40, "SIGN_IMAGES": "false"}), \
             patch.object(images, "docker", return_value=json.dumps({"digest": "sha256:123"})) as docker, \
             patch.object(subprocess, "run", return_value=result):
            images.publish(addons)
        return docker.call_args_list

    def test_existing_release_tag_is_never_overwritten(self):
        calls = self.check_publish(subprocess.CompletedProcess([], 0, "found", ""))
        created = [c.args for c in calls if c.args[0] == "create"]
        self.assertEqual(len(created), 1)
        self.assertIn("ghcr.io/detfisch/example:latest", created[0])
        self.assertNotIn("ghcr.io/detfisch/example:1.2.3", created[0])
        self.assertEqual(len([v for v in created[0] if ":sha-" in v]), 2)

    def test_new_version_is_published(self):
        calls = self.check_publish(subprocess.CompletedProcess([], 1, "", "manifest unknown"))
        self.assertTrue(any("ghcr.io/detfisch/example:1.2.3" in c.args
                            for c in calls if c.args[0] == "create"))

    def test_same_digest_is_signed_once_for_both_release_tags(self):
        def run(args, **kwargs):
            return subprocess.CompletedProcess(args, 1 if args[0] == "docker" else 0,
                                               "", "manifest unknown")
        with patch.dict(os.environ, {"GITHUB_SHA": "a" * 40, "SIGN_IMAGES": "true"}), \
             patch.object(images, "docker", return_value=json.dumps({"digest": "sha256:123"})), \
             patch.object(subprocess, "run", side_effect=run) as commands:
            images.publish(images.load_addons(self.root))
        signatures = [call for call in commands.call_args_list if call.args[0][0] == "bash"]
        self.assertEqual(len(signatures), 1)

    def test_registry_permission_error_does_not_overwrite_a_release(self):
        with self.assertRaisesRegex(RuntimeError, "unauthorized"):
            self.check_publish(subprocess.CompletedProcess([], 1, "", "unauthorized"))


if __name__ == "__main__":
    unittest.main()
