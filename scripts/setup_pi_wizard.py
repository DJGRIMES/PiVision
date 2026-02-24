#!/usr/bin/env python3
"""Interactive Pi setup helper for env + deployment + systemd."""

from __future__ import annotations

import argparse
import datetime
import os
import shutil
import subprocess
from pathlib import Path
from typing import Dict, Iterable, Sequence, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
ENV_EXAMPLE = REPO_ROOT / "env.deploy.example"
ENV_FILE = REPO_ROOT / ".env.deploy"
DEPLOY_SCRIPT = REPO_ROOT / "scripts" / "deploy_pi.sh"
CHECK_SCRIPT = REPO_ROOT / "scripts" / "check_backend.sh"
SYSTEMD_TEMPLATE_DIR = REPO_ROOT / "deployment" / "systemd"
LEGACY_SERVICES = [
    "aifoodstand-camera.service",
    "aifoodstand-server.service",
    "aifoodstand-uploader.service",
    "foodstand-backend.service",
]
ENV_ITEMS: Sequence[Tuple[str, str]] = [
    ("PIVISION_DATA_DIR", "Location to store data and staging/event files"),
    ("PIVISION_DASHBOARD_DIR", "Location for dashboard sources served via http.server"),
    ("PIVISION_BACKEND_IMAGE", "Ingress/backend image reference"),
    ("PIVISION_WORKER_IMAGE", "Worker image reference"),
    ("PIVISION_RETENTION_IMAGE", "Retention job image reference"),
    ("PIVISION_CAMERA_IMAGE", "Camera/ESP simulation image reference"),
    ("PIVISION_DASHBOARD_IMAGE", "Static dashboard server image reference"),
    ("PIVISION_DEVICE_KEY", "Device auth key shared between camera clients and ingest"),
    ("CAMERA_DEVICE_ID", "Device id used by the onboard camera script"),
]


def load_key_values(path: Path) -> Dict[str, str]:
    if not path.exists():
        return {}
    result: Dict[str, str] = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        result[key.strip()] = value.strip()
    return result


def prompt_value(prompt: str, default: str) -> str:
    if default:
        display = f" [{default}]"
    else:
        display = ""
    while True:
        response = input(f"{prompt}{display}: ").strip()
        if response:
            return response
        if default:
            return default
        print("Value required.")


def confirm_action(prompt: str, default: bool = False) -> bool:
    default_hint = "Y/n" if default else "y/N"
    resp = input(f"{prompt} ({default_hint}): ").strip().lower()
    if not resp:
        return default
    return resp in {"y", "yes"}


def ensure_directories(paths: Iterable[str]) -> None:
    for raw in paths:
        path = Path(raw).expanduser()
        try:
            path.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            print(f"Failed to create {path}: {exc}")
            raise
        print(f"Ensured {path} exists")


def backup_existing_env(path: Path) -> None:
    if not path.exists():
        return
    stamp = datetime.datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    backup = path.with_name(f"{path.stem}.bak-{stamp}")
    path.replace(backup)
    print(f"Backed up existing {path.name} to {backup.name}")


def write_env(path: Path, values: Dict[str, str], keys: Sequence[str]) -> None:
    lines = [f"{key}={values[key]}" for key in keys]
    path.write_text("\n".join(lines) + "\n")
    print(f"Wrote {path}")


def run_subprocess(cmd: Sequence[str], **kwargs) -> None:
    print(f"Running: {' '.join(cmd)}")
    subprocess.run(cmd, check=True, **kwargs)


def try_docker_compose() -> bool:
    try:
        subprocess.run([
            "docker",
            "compose",
            "version",
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False


def run_deploy(skip: bool) -> None:
    if skip:
        print("Skipping deployment phase per request.")
        return
    if not DEPLOY_SCRIPT.exists():
        raise SystemExit("deploy_pi.sh missing; cannot run deploy step")
    if not try_docker_compose():
        print("Warning: 'docker compose' command not available; install Docker Compose before continuing.")
    env = os.environ.copy()
    env["ENV_FILE"] = str(ENV_FILE)
    run_subprocess([str(DEPLOY_SCRIPT)], env=env)


def run_health_check(skip: bool) -> None:
    if skip:
        print("Skipping health check per request.")
        return
    if not CHECK_SCRIPT.exists():
        raise SystemExit("check_backend.sh missing; cannot run health check")
    run_subprocess([str(CHECK_SCRIPT)])


def systemctl_active(service: str) -> bool:
    result = subprocess.run(["systemctl", "is-active", service], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return result.returncode == 0


def _is_root() -> bool:
    if hasattr(os, "geteuid"):
        return os.geteuid() == 0
    return False


def install_services(interactive: bool) -> None:
    if not SYSTEMD_TEMPLATE_DIR.exists():
        raise SystemExit("Systemd templates missing; cannot install services")

    if not _is_root():
        print("\nService installation requires root privileges. Either re-run the wizard with sudo or execute the following commands:")
        for template in SYSTEMD_TEMPLATE_DIR.glob("*.service"):
            print(f"  install -m 644 {template} /etc/systemd/system/{template.name}")
        print("  systemctl daemon-reload")
        print("  systemctl enable --now pivision-server pivision-worker")
        print("  systemctl status pivision-server pivision-worker")
        return

    legacy = [svc for svc in LEGACY_SERVICES if systemctl_active(svc)]
    if legacy:
        print("Legacy units claiming resources:")
        for svc in legacy:
            print(f"  - {svc}")
        if interactive and confirm_action("Stop and disable these legacy units?", default=True):
            for svc in legacy:
                run_subprocess(["systemctl", "stop", svc])
                run_subprocess(["systemctl", "disable", svc])
        else:
            print("Skipping legacy unit cleanup; ensure they are disabled before starting PiVision services.")

    for template in SYSTEMD_TEMPLATE_DIR.glob("*.service"):
        dest = Path("/etc/systemd/system") / template.name
        shutil.copy(template, dest)
        print(f"Installed {dest}")

    run_subprocess(["systemctl", "daemon-reload"])
    services = ["pivision-server", "pivision-worker"]
    run_subprocess(["systemctl", "enable", "--now", *services])
    run_subprocess(["systemctl", "status", *services])
    run_subprocess(["systemctl", "is-active", *services])
    try:
        run_subprocess(["ss", "-lntup"])
    except (subprocess.CalledProcessError, FileNotFoundError):
        print("Unable to run 'ss -lntup'; please verify port 8080 binding manually.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Guided PiVision deployment helper")
    parser.add_argument("--skip-deploy", action="store_true", help="Do not invoke scripts/deploy_pi.sh")
    parser.add_argument("--skip-health", action="store_true", help="Skip running the health check script")
    parser.add_argument("--install-services", action="store_true", help="Install systemd services after deploy")
    parser.add_argument("--yes", action="store_true", help="Skip confirmations")
    args = parser.parse_args()

    if not (REPO_ROOT / "backend").exists():
        raise SystemExit("Please run the wizard from the PiVision repo root")

    example_values = load_key_values(ENV_EXAMPLE)
    current_values = load_key_values(ENV_FILE)
    merged = {key: current_values.get(key, example_values.get(key, fallback)) for key, fallback in _fallbacks().items()}

    print("Provide the values used by docker-compose. Press Enter to keep the default/current value.")
    for key, desc in ENV_ITEMS:
        prompt = f"{key} – {desc}"
        merged[key] = prompt_value(prompt, merged[key])

    print("\nSummary of values to write:")
    for key, _desc in ENV_ITEMS:
        print(f"  {key}={merged[key]}")

    if not args.yes and not confirm_action("Write these values to .env.deploy and continue?", default=True):
        raise SystemExit("Setup aborted by user")

    backup_existing_env(ENV_FILE)
    write_env(ENV_FILE, merged, [key for key, _ in ENV_ITEMS])
    ensure_directories([merged["PIVISION_DATA_DIR"], merged["PIVISION_DASHBOARD_DIR"]])

    run_deploy(args.skip_deploy)
    run_health_check(args.skip_health)

    if args.install_services:
        install_services(interactive=not args.yes)

    print("Setup wizard completed.")


def _fallbacks() -> Dict[str, str]:
    return {
        "PIVISION_DATA_DIR": "/mnt/pivision/data",
        "PIVISION_DASHBOARD_DIR": "/mnt/pivision/dashboard",
        "PIVISION_BACKEND_IMAGE": "docker.io/pivision/backend:latest",
        "PIVISION_WORKER_IMAGE": "docker.io/pivision/backend:latest",
        "PIVISION_RETENTION_IMAGE": "docker.io/pivision/backend:latest",
        "PIVISION_CAMERA_IMAGE": "docker.io/pivision/backend:latest",
        "PIVISION_DASHBOARD_IMAGE": "python:3.12-slim",
        "PIVISION_DEVICE_KEY": "pi-device-key",
        "CAMERA_DEVICE_ID": "pi-camera",
    }


if __name__ == "__main__":
    main()
