"""CLI for building and pushing version-pinned Icinga images."""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml


IMAGE_ORDER = ("icinga2", "icingadb", "icingadb_redis", "icingaweb2", "mariadb")
VERSION_RE = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z.-]+)?$")
PACKAGE_ROOT = Path(__file__).resolve().parent
DEFAULTS = {
    "icinga2": ("icinga/icinga2", "icinga2"),
    "icingadb": ("icinga/icingadb", "icingadb"),
    "icingadb_redis": ("redis", "icingadb-redis"),
    "icingaweb2": ("icinga/icingaweb2", "icingaweb2"),
    "mariadb": ("mariadb", "mariadb"),
}


class ConfigError(RuntimeError):
    pass


def load_config(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise ConfigError(f"Không tìm thấy file cấu hình: {path}")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigError(f"Không đọc được YAML {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError("Cấu hình gốc phải là YAML mapping.")
    return data


def registry(value: Any, field: str) -> str:
    result = str(value or "").strip().rstrip("/")
    if "://" in result:
        raise ConfigError(f"{field} chỉ nhận host[:port][/project], không thêm scheme.")
    if any(char.isspace() for char in result):
        raise ConfigError(f"{field} không được chứa khoảng trắng.")
    if "@" in result:
        raise ConfigError(f"{field} không được chứa credential; hãy dùng docker login.")
    return result


def select_images(values: list[str] | None) -> list[str]:
    if not values:
        return list(IMAGE_ORDER)
    selected: list[str] = []
    for value in values:
        for name in value.split(","):
            name = name.strip()
            if not name:
                continue
            if name not in IMAGE_ORDER:
                raise ConfigError(f"Image '{name}' không hợp lệ. Chọn: {', '.join(IMAGE_ORDER)}")
            if name not in selected:
                selected.append(name)
    if not selected:
        raise ConfigError("Danh sách --image đang rỗng.")
    return selected


def resolve_custom_path(value: str, config_path: Path) -> Path:
    path = Path(value).expanduser()
    return path if path.is_absolute() else config_path.parent / path


def create_plan(args: argparse.Namespace) -> tuple[list[dict[str, Any]], str, str]:
    config_path = Path(args.config).expanduser().resolve()
    config = load_config(config_path)
    versions = config.get("versions")
    images = config.get("images", {}) or {}
    if not isinstance(versions, dict):
        raise ConfigError("Thiếu mapping versions trong config.yml.")
    if not isinstance(images, dict):
        raise ConfigError("images phải là YAML mapping.")

    source_registry = registry(
        args.source_registry if args.source_registry is not None else config.get("source_registry", ""),
        "source_registry",
    )
    target_registry = registry(
        args.registry if args.registry is not None else config.get("target_registry", ""),
        "target_registry",
    )
    local_namespace = registry(config.get("local_namespace", "icinga-local"), "local_namespace")
    target_prefix = target_registry or local_namespace
    if not target_prefix:
        raise ConfigError("Cần target_registry hoặc local_namespace.")
    suffix = args.tag_suffix if args.tag_suffix is not None else str(config.get("tag_suffix", "-custom"))
    platform = args.platform if args.platform is not None else str(config.get("platform", "")).strip()
    build_network = (
        args.network if args.network is not None else str(config.get("build_network", "")).strip()
    )
    raw_retries = args.retries if args.retries is not None else config.get("retries", 1)
    try:
        retries = int(raw_retries)
    except (TypeError, ValueError) as exc:
        raise ConfigError("retries phải là số nguyên từ 0 đến 10.") from exc
    if retries < 0 or retries > 10:
        raise ConfigError("retries phải là số nguyên từ 0 đến 10.")
    args.effective_retries = retries

    result: list[dict[str, Any]] = []
    for name in select_images(args.image):
        version = str(versions.get(name, "")).strip()
        if not VERSION_RE.fullmatch(version):
            raise ConfigError(f"versions.{name}='{version}' không hợp lệ; cần version dạng x.y.z.")
        image_config = images.get(name, {}) or {}
        if not isinstance(image_config, dict):
            raise ConfigError(f"images.{name} phải là YAML mapping.")
        if image_config.get("enabled", True) is False:
            continue
        default_source, default_target = DEFAULTS[name]
        source_repo = str(image_config.get("source_repository", default_source)).strip().strip("/")
        target_name = str(image_config.get("target_name", default_target)).strip().strip("/")
        if not source_repo or not target_name or any(c.isspace() for c in source_repo + target_name):
            raise ConfigError(f"Repository/name của {name} không hợp lệ.")
        custom_dockerfile = image_config.get("dockerfile")
        dockerfile = (
            resolve_custom_path(str(custom_dockerfile), config_path)
            if custom_dockerfile
            else PACKAGE_ROOT / "dockerfiles" / name / "Dockerfile"
        )
        custom_context = image_config.get("context")
        context = (
            resolve_custom_path(str(custom_context), config_path)
            if custom_context
            else dockerfile.parent
        )
        build_args = image_config.get("build_args", {}) or {}
        if not isinstance(build_args, dict):
            raise ConfigError(f"images.{name}.build_args phải là YAML mapping.")
        source = f"{source_registry + '/' if source_registry else ''}{source_repo}:{version}"
        short_target = f"{target_name}:{version}{suffix}"
        result.append(
            {
                "name": name,
                "version": version,
                "source": source,
                "target": f"{target_prefix}/{short_target}",
                "short_target": short_target,
                "dockerfile": dockerfile,
                "context": context,
                "build_args": {str(k): str(v) for k, v in build_args.items()},
                "platform": platform,
                "network": build_network,
            }
        )
    if not result:
        raise ConfigError("Không có image nào được bật để xử lý.")
    return result, target_registry, target_prefix


def show_plan(plan: list[dict[str, Any]], deploy_registry: str) -> None:
    print("IMAGE              SOURCE -> TARGET")
    for item in plan:
        print(f"{item['name']:<18} {item['source']} -> {item['target']}")
    print("\nYAML dùng cho icinga-ansible:")
    output = {
        "custom_image_registry": deploy_registry,
        "custom_image_overrides": {item["name"]: item["short_target"] for item in plan},
    }
    print(yaml.safe_dump(output, sort_keys=False, allow_unicode=True).rstrip())


def save_overrides(path: Path, plan: list[dict[str, Any]], deploy_registry: str) -> None:
    output = {
        "custom_image_registry": deploy_registry,
        "custom_image_overrides": {item["name"]: item["short_target"] for item in plan},
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(output, sort_keys=False, allow_unicode=True), encoding="utf-8")
    print(f"Đã ghi: {path}")


def display_command(arguments: list[str]) -> str:
    safe = list(arguments)
    for index, value in enumerate(safe):
        if index and safe[index - 1] == "--build-arg" and "=" in value:
            key = value.split("=", 1)[0]
            if any(word in key.lower() for word in ("proxy", "password", "secret", "token")):
                safe[index] = f"{key}=***"
    return "+ docker " + " ".join(safe)


def run_docker(arguments: list[str], retries: int = 0) -> None:
    print(display_command(arguments))
    for attempt in range(retries + 1):
        try:
            subprocess.run(["docker", *arguments], check=True)
            return
        except FileNotFoundError as exc:
            raise ConfigError("Không tìm thấy lệnh docker trên build node.") from exc
        except subprocess.CalledProcessError as exc:
            if attempt == retries:
                raise ConfigError(f"Docker thất bại với mã {exc.returncode}.") from exc
            print(f"Docker lỗi; retry {attempt + 1}/{retries}...", file=sys.stderr)


def build_images(plan: list[dict[str, Any]], args: argparse.Namespace) -> None:
    if shutil.which("docker") is None:
        raise ConfigError("Không tìm thấy lệnh docker trên build node.")
    proxy_args = {
        key: os.environ[key]
        for key in ("http_proxy", "https_proxy", "no_proxy", "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY")
        if os.environ.get(key)
    }
    for item in plan:
        if not item["dockerfile"].is_file():
            raise ConfigError(f"Không tìm thấy Dockerfile: {item['dockerfile']}")
        if not item["context"].is_dir():
            raise ConfigError(f"Không tìm thấy build context: {item['context']}")
        command = ["build"]
        if args.pull:
            command.append("--pull")
        if args.no_cache:
            command.append("--no-cache")
        if item["platform"]:
            command.extend(["--platform", item["platform"]])
        if item["network"]:
            command.extend(["--network", item["network"]])
        command.extend(["--file", str(item["dockerfile"]), "--tag", item["target"]])
        all_build_args = {"BASE_IMAGE": item["source"], **proxy_args, **item["build_args"]}
        for key, value in all_build_args.items():
            command.extend(["--build-arg", f"{key}={value}"])
        command.extend(
            [
                "--label", f"org.opencontainers.image.base.name={item['source']}",
                "--label", f"org.opencontainers.image.version={item['version']}",
                str(item["context"]),
            ]
        )
        run_docker(command, args.effective_retries)


def push_images(plan: list[dict[str, Any]], target_registry: str, retries: int = 0) -> None:
    if not target_registry:
        raise ConfigError("Push cần --registry hoặc target_registry trong config.yml.")
    for item in plan:
        run_docker(["push", item["target"]], retries)


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="icinga-image-build",
        description="Tool độc lập build/push image Icinga theo version chỉ định.",
    )
    parser.add_argument("command", choices=("plan", "build", "push"))
    parser.add_argument("--config", default="config.yml", help="file cấu hình (mặc định ./config.yml)")
    parser.add_argument("--source-registry", help="ghi đè registry nguồn để pull base image")
    parser.add_argument("--registry", help="ghi đè registry/project đích")
    parser.add_argument("--image", action="append", help="lọc image; lặp lại hoặc ngăn bằng dấu phẩy")
    parser.add_argument("--tag-suffix", help="ghi đè suffix tag custom")
    parser.add_argument("--platform", help="ghi đè platform, ví dụ linux/amd64")
    parser.add_argument("--network", help="network dùng cho RUN khi build, ví dụ host")
    parser.add_argument("--retries", type=int, help="số lần retry thêm khi Docker lỗi (0-10)")
    parser.add_argument("--pull", action="store_true", help="pull lại base image khi build")
    parser.add_argument("--no-cache", action="store_true", help="build không dùng cache")
    parser.add_argument("--push", action="store_true", help="push ngay sau build")
    parser.add_argument("--output-overrides", type=Path, help="ghi YAML cho icinga-ansible")
    return parser


def main() -> int:
    args = make_parser().parse_args()
    try:
        plan, target_registry, deploy_registry = create_plan(args)
        if args.push and args.command != "build":
            raise ConfigError("--push chỉ đi cùng command build; dùng command push để push riêng.")
        show_plan(plan, deploy_registry)
        if args.output_overrides:
            save_overrides(args.output_overrides, plan, deploy_registry)
        if args.command == "build":
            build_images(plan, args)
            if args.push:
                push_images(plan, target_registry, args.effective_retries)
        elif args.command == "push":
            push_images(plan, target_registry, args.effective_retries)
        return 0
    except ConfigError as exc:
        sys.stderr.write(f"ERROR: {exc}\n")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
