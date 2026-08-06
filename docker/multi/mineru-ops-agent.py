#!/usr/bin/env python3
"""Restricted host-side control agent for the MinerU operations console."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import socketserver
import subprocess
from pathlib import Path
from typing import Any


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--socket", required=True, type=Path)
    parser.add_argument("--project-dir", required=True, type=Path)
    parser.add_argument("--env-file", required=True)
    parser.add_argument("--compose-file", action="append", required=True)
    return parser.parse_args()


def run_command(command: list[str], cwd: Path, timeout: int = 180) -> dict[str, Any]:
    try:
        completed = subprocess.run(
            command,
            cwd=cwd,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    output = "\n".join(part.strip() for part in (completed.stdout, completed.stderr) if part.strip())
    return {
        "ok": completed.returncode == 0,
        "exit_code": completed.returncode,
        "output": output,
        "error": None if completed.returncode == 0 else output or "command failed",
    }


def parse_compose_ps(output: str) -> dict[str, dict[str, Any]]:
    if not output.strip():
        return {}
    payloads: list[dict[str, Any]] = []
    try:
        decoded = json.loads(output)
        if isinstance(decoded, list):
            payloads = [item for item in decoded if isinstance(item, dict)]
        elif isinstance(decoded, dict):
            payloads = [decoded]
    except json.JSONDecodeError:
        for line in output.splitlines():
            try:
                decoded = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(decoded, dict):
                payloads.append(decoded)
    services: dict[str, dict[str, Any]] = {}
    for payload in payloads:
        service = payload.get("Service") or payload.get("service")
        if not service:
            continue
        services[str(service)] = {
            "container": payload.get("Name") or payload.get("name"),
            "state": payload.get("State") or payload.get("state"),
            "status": payload.get("Status") or payload.get("status"),
            "health": payload.get("Health") or payload.get("health"),
            "image": payload.get("Image") or payload.get("image"),
            "ports": payload.get("Publishers") or payload.get("publishers"),
        }
    return services


class Agent:
    def __init__(self, project_dir: Path, env_file: str, compose_files: list[str]) -> None:
        self.project_dir = project_dir.resolve()
        self.env_file = env_file
        self.compose_files = list(compose_files)
        if shutil.which("docker") and run_command(["docker", "compose", "version"], self.project_dir, timeout=15)["ok"]:
            self.compose_prefix = ["docker", "compose"]
        elif shutil.which("docker-compose"):
            self.compose_prefix = ["docker-compose"]
        else:
            raise SystemExit("Docker Compose is unavailable")

    def compose_command(self, *arguments: str) -> list[str]:
        command = list(self.compose_prefix)
        for compose_file in self.compose_files:
            command.extend(["-f", compose_file])
        command.extend(["--env-file", self.env_file])
        command.extend(arguments)
        return command

    def configured_services(self) -> set[str]:
        result = run_command(
            self.compose_command("config", "--services"),
            self.project_dir,
            timeout=30,
        )
        if not result["ok"]:
            return set()
        return {line.strip() for line in result["output"].splitlines() if line.strip()}

    def handle(self, request: dict[str, Any]) -> dict[str, Any]:
        action = request.get("action")
        if action == "services":
            return self.services()
        if action == "service_action":
            return self.service_action(
                str(request.get("service", "")),
                str(request.get("operation", "")),
            )
        if action == "stack_action":
            return self.stack_action(str(request.get("operation", "")))
        if action == "logs":
            return self.logs(
                str(request.get("service", "")),
                int(request.get("tail", 300)),
            )
        if action == "diagnostics":
            return self.diagnostics(int(request.get("tail", 800)))
        return {"ok": False, "error": "unsupported agent action"}

    def services(self) -> dict[str, Any]:
        configured = self.configured_services()
        result = run_command(
            self.compose_command("ps", "-a", "--format", "json"),
            self.project_dir,
            timeout=30,
        )
        runtime = parse_compose_ps(result.get("output", "")) if result["ok"] else {}
        for service in configured:
            runtime.setdefault(
                service,
                {
                    "container": None,
                    "state": "not-created",
                    "status": "Container has not been created",
                    "health": None,
                    "image": None,
                    "ports": None,
                },
            )
        return {
            "ok": result["ok"],
            "services": runtime,
            "error": result.get("error"),
        }

    def service_action(self, service: str, operation: str) -> dict[str, Any]:
        allowed_operations = {"start", "stop", "restart"}
        configured = self.configured_services()
        if service not in configured:
            return {"ok": False, "error": "service is not part of the configured Compose project"}
        if service == "mineru-ops":
            return {"ok": False, "error": "mineru-ops cannot control its own container"}
        if operation not in allowed_operations:
            return {"ok": False, "error": "unsupported service operation"}
        if operation == "start":
            command = self.compose_command("start", service)
        else:
            command = self.compose_command(operation, service)
        result = run_command(command, self.project_dir, timeout=180)
        result.update({"service": service, "operation": operation})
        return result

    def stack_action(self, operation: str) -> dict[str, Any]:
        if operation not in {"start", "stop", "restart"}:
            return {"ok": False, "error": "unsupported stack operation"}
        services = sorted(
            service for service in self.configured_services() if service not in {"mineru-ops", "mineru-code-sync"}
        )
        if not services:
            return {"ok": False, "error": "no MinerU business services were discovered"}
        command = self.compose_command(operation, *services)
        result = run_command(command, self.project_dir, timeout=240)
        result.update({"services": services, "operation": operation})
        return result

    def logs(self, service: str, tail: int) -> dict[str, Any]:
        if service not in self.configured_services():
            return {"ok": False, "error": "service is not part of the configured Compose project"}
        result = run_command(
            self.compose_command("logs", "--no-color", "--tail", str(max(20, min(tail, 2000))), service),
            self.project_dir,
            timeout=30,
        )
        return {
            "ok": result["ok"],
            "service": service,
            "logs": result.get("output", ""),
            "error": result.get("error"),
        }

    def diagnostics(self, tail: int) -> dict[str, Any]:
        services = sorted(
            service for service in self.configured_services() if service == "mineru-router" or service.startswith("mineru-api-")
        )
        logs = {service: self.logs(service, tail) for service in services}
        npu_smi = run_command(["npu-smi", "info"], self.project_dir, timeout=30)
        docker_info = run_command(
            ["docker", "version", "--format", "{{json .}}"],
            self.project_dir,
            timeout=30,
        )
        return {
            "ok": True,
            "logs": logs,
            "npu_smi": npu_smi,
            "docker_version": docker_info,
        }


class AgentRequestHandler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        raw = self.rfile.readline(1024 * 1024)
        try:
            payload = json.loads(raw.decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("request must be an object")
            response = self.server.agent.handle(payload)  # type: ignore[attr-defined]
        except Exception as exc:
            response = {"ok": False, "error": f"invalid request: {exc}"}
        self.wfile.write((json.dumps(response, ensure_ascii=False) + "\n").encode("utf-8"))


class ThreadingUnixServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True
    allow_reuse_address = True


def main() -> int:
    args = parse_args()
    socket_path = args.socket.resolve()
    socket_path.parent.mkdir(parents=True, exist_ok=True)
    socket_path.unlink(missing_ok=True)
    agent = Agent(args.project_dir, args.env_file, args.compose_file)
    server = ThreadingUnixServer(str(socket_path), AgentRequestHandler)
    server.agent = agent  # type: ignore[attr-defined]
    os.chmod(socket_path, 0o660)
    try:
        server.serve_forever(poll_interval=0.5)
    finally:
        server.server_close()
        socket_path.unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
