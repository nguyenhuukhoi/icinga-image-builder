from __future__ import annotations

import argparse
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

from icinga_image_builder import cli


VERSIONS = {
    "icinga2": "2.16.5",
    "icingadb": "1.5.1",
    "icingadb_redis": "7.4.11",
    "icingaweb2": "2.14.0",
    "mariadb": "12.3.3",
}


def arguments(config: Path, **overrides):
    values = {
        "config": str(config),
        "source_registry": None,
        "registry": None,
        "tag_suffix": None,
        "platform": None,
        "network": None,
        "retries": None,
        "image": None,
        "images": [],
        "pull": False,
        "no_cache": False,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


class PlanTests(unittest.TestCase):
    def write_config(self, directory: Path, extra=None) -> Path:
        data = {"versions": VERSIONS, "target_registry": "registry.example/icinga"}
        if extra:
            data.update(extra)
        path = directory / "config.yml"
        path.write_text(yaml.safe_dump(data), encoding="utf-8")
        return path

    def test_all_image_names_and_sources(self):
        with tempfile.TemporaryDirectory() as temp:
            path = self.write_config(Path(temp))
            plan, target, deploy = cli.create_plan(arguments(path))
        self.assertEqual([item["name"] for item in plan], list(cli.IMAGE_ORDER))
        self.assertEqual(plan[0]["source"], "icinga/icinga2:2.16.5")
        self.assertEqual(plan[2]["source"], "redis:7.4.11")
        self.assertEqual(plan[4]["target"], "registry.example/icinga/mariadb:12.3.3")
        self.assertEqual(target, "registry.example/icinga")
        self.assertEqual(deploy, target)

    def test_default_dockerfiles_are_external_to_installed_package(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            path = self.write_config(root)
            plan, _, _ = cli.create_plan(arguments(path))
        self.assertEqual(plan[0]["dockerfile"], root / "dockerfiles" / "icinga2" / "Dockerfile")
        self.assertEqual(plan[4]["context"], root / "dockerfiles" / "mariadb")

    def test_source_registry_and_selection(self):
        with tempfile.TemporaryDirectory() as temp:
            path = self.write_config(Path(temp), {"source_registry": "mirror.local/cache"})
            plan, _, _ = cli.create_plan(arguments(path, image=["icinga2,mariadb"]))
        self.assertEqual(len(plan), 2)
        self.assertEqual(plan[0]["source"], "mirror.local/cache/icinga/icinga2:2.16.5")

    def test_local_namespace_is_emitted_for_local_deploy(self):
        with tempfile.TemporaryDirectory() as temp:
            path = self.write_config(
                Path(temp), {"target_registry": "", "local_namespace": "my-local"}
            )
            plan, target, deploy = cli.create_plan(arguments(path, image=["icinga2"]))
        self.assertEqual(target, "")
        self.assertEqual(deploy, "my-local")
        self.assertEqual(plan[0]["target"], "my-local/icinga2:2.16.5")

    def test_positional_image_selection_like_kolla_build(self):
        with tempfile.TemporaryDirectory() as temp:
            path = self.write_config(Path(temp))
            plan, _, _ = cli.create_plan(arguments(path, images=["icinga2", "icingadb"]))
        self.assertEqual([item["name"] for item in plan], ["icinga2", "icingadb"])

    def test_per_image_custom_suffix(self):
        with tempfile.TemporaryDirectory() as temp:
            path = self.write_config(
                Path(temp), {"images": {"icinga2": {"tag_suffix": "-custom"}}}
            )
            plan, _, _ = cli.create_plan(arguments(path, images=["icinga2"]))
        self.assertEqual(plan[0]["target"], "registry.example/icinga/icinga2:2.16.5-custom")

    def test_parser_accepts_multiple_positional_images(self):
        parsed = cli.make_parser().parse_args(
            ["build", "icinga2", "icingadb", "--config", "config.yml"]
        )
        self.assertEqual(parsed.command, "build")
        self.assertEqual(parsed.images, ["icinga2", "icingadb"])

    def test_missing_version_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            bad_versions = dict(VERSIONS)
            del bad_versions["mariadb"]
            path = Path(temp) / "config.yml"
            path.write_text(yaml.safe_dump({"versions": bad_versions}), encoding="utf-8")
            with self.assertRaisesRegex(cli.ConfigError, "versions.mariadb"):
                cli.create_plan(arguments(path))

    def test_registry_scheme_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            path = self.write_config(Path(temp), {"target_registry": "https://registry.example/icinga"})
            with self.assertRaisesRegex(cli.ConfigError, "không thêm scheme"):
                cli.create_plan(arguments(path))

    def test_base_image_build_arg_cannot_override_pinned_version(self):
        with tempfile.TemporaryDirectory() as temp:
            path = self.write_config(
                Path(temp),
                {"images": {"icinga2": {"build_args": {"BASE_IMAGE": "icinga/icinga2:latest"}}}},
            )
            with self.assertRaisesRegex(cli.ConfigError, "versions.icinga2"):
                cli.create_plan(arguments(path, images=["icinga2"]))


class DockerTests(unittest.TestCase):
    def test_default_dockerfiles_require_base_image_from_config(self):
        for name in cli.IMAGE_ORDER:
            dockerfile = Path(__file__).resolve().parents[1] / "dockerfiles" / name / "Dockerfile"
            lines = dockerfile.read_text(encoding="utf-8").splitlines()
            self.assertEqual(
                lines[:4],
                ["# check=skip=InvalidDefaultArgInFrom", "", "ARG BASE_IMAGE", "FROM ${BASE_IMAGE}"],
                name,
            )

    def test_build_passes_base_proxy_and_custom_args(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            dockerfile = root / "Dockerfile"
            dockerfile.write_text("ARG BASE_IMAGE\nFROM ${BASE_IMAGE}\n", encoding="utf-8")
            item = {
                "name": "icinga2",
                "version": "2.16.5",
                "source": "icinga/icinga2:2.16.5",
                "target": "registry.example/icinga/icinga2:2.16.5",
                "dockerfile": dockerfile,
                "context": root,
                "platform": "linux/amd64",
                "network": "host",
                "build_args": {"EXTRA": "yes"},
            }
            args = argparse.Namespace(pull=True, no_cache=True)
            commands = []
            with patch.object(cli.shutil, "which", return_value="/usr/bin/docker"), patch.object(
                cli, "run_docker", side_effect=lambda command, retries=0: commands.append(command)
            ), patch.dict(cli.os.environ, {"http_proxy": "http://proxy:3128"}, clear=True):
                args.effective_retries = 1
                cli.build_images([item], args)
        command = commands[0]
        self.assertIn("--pull", command)
        self.assertIn("--no-cache", command)
        self.assertIn("--network", command)
        self.assertIn("host", command)
        self.assertIn("BASE_IMAGE=icinga/icinga2:2.16.5", command)
        self.assertIn("http_proxy=http://proxy:3128", command)
        self.assertIn("EXTRA=yes", command)

    def test_push_without_registry_is_rejected(self):
        with self.assertRaisesRegex(cli.ConfigError, "Push cần"):
            cli.push_images([], "")

    def test_sensitive_build_args_are_redacted_from_display(self):
        rendered = cli.display_command(
            ["build", "--build-arg", "https_proxy=http://user:pass@proxy", "--build-arg", "SAFE=yes"]
        )
        self.assertNotIn("pass", rendered)
        self.assertIn("https_proxy=***", rendered)
        self.assertIn("SAFE=yes", rendered)

    def test_docker_command_retries_once(self):
        failure = subprocess.CalledProcessError(1, ["docker", "push"])
        with patch.object(cli.subprocess, "run", side_effect=[failure, None]) as run:
            cli.run_docker(["push", "registry/image:tag"], retries=1)
        self.assertEqual(run.call_count, 2)


if __name__ == "__main__":
    unittest.main()
