#!/usr/bin/env python3
"""Restricted host-side control agent for the MinerU operations console."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import socketserver
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


CONFIG_SCHEMA: list[dict[str, Any]] = [
    {
        "key": "MINERU_MODEL_SOURCE",
        "category": "模型与设备",
        "description": "模型来源。离线现场通常使用 local。",
        "type": "enum",
        "choices": ["local", "modelscope", "huggingface"],
        "editable": True,
    },
    {
        "key": "MINERU_DEVICE_MODE",
        "category": "模型与设备",
        "description": "运行设备模式，例如 cpu、npu 或 cuda。",
        "type": "enum",
        "choices": ["cpu", "npu", "cuda"],
        "editable": True,
    },
    {
        "key": "MINERU_VLM_MODEL",
        "category": "模型与设备",
        "description": "VLM 模型名称或本地模型目录。",
        "type": "string",
        "editable": True,
    },
    {
        "key": "MINERU_PROCESSING_WINDOW_SIZE",
        "category": "API 并发",
        "description": "API 处理窗口大小，影响同时保留的任务数。",
        "type": "integer",
        "minimum": 1,
        "maximum": 128,
        "editable": True,
    },
    {
        "key": "MINERU_API_MAX_CONCURRENT_REQUESTS",
        "category": "API 并发",
        "description": "API 同时处理的请求数量。",
        "type": "integer",
        "minimum": 1,
        "maximum": 64,
        "editable": True,
    },
    {
        "key": "MINERU_VLM_FAILURE_POLICY",
        "category": "VLM 超时与重试",
        "description": "VLM 失败时的处理策略。",
        "type": "enum",
        "choices": ["skip", "fail", "retry"],
        "editable": True,
    },
    {
        "key": "MINERU_VLM_GLOBAL_PAGE_CONCURRENCY",
        "category": "VLM 超时与重试",
        "description": "VLM 页面级并发数；测试耗时建议先使用 1。",
        "type": "integer",
        "minimum": 1,
        "maximum": 64,
        "editable": True,
    },
    {
        "key": "MINERU_VLM_PAGE_TIMEOUT_SECONDS",
        "category": "VLM 超时与重试",
        "description": "单页 VLM 请求的软超时时间（秒）。",
        "type": "integer",
        "minimum": 1,
        "maximum": 7200,
        "editable": True,
    },
    {
        "key": "MINERU_VLM_CONNECT_MAX_RETRIES",
        "category": "VLM 超时与重试",
        "description": "连接失败时自动重试次数；测量单页真实耗时建议为 0。",
        "type": "integer",
        "minimum": 0,
        "maximum": 3,
        "editable": True,
    },
    {
        "key": "MINERU_VLM_CLIENT_MAX_CONCURRENCY",
        "category": "VLM 超时与重试",
        "description": "VLM 客户端请求并发上限。",
        "type": "integer",
        "minimum": 1,
        "maximum": 64,
        "editable": True,
    },
    {
        "key": "MINERU_VLM_CLIENT_MAX_RETRIES",
        "category": "VLM 超时与重试",
        "description": "VLM 客户端内部重试次数。",
        "type": "integer",
        "minimum": 0,
        "maximum": 5,
        "editable": True,
    },
    {
        "key": "MINERU_VLM_CLIENT_HTTP_TIMEOUT",
        "category": "VLM 超时与重试",
        "description": "VLM 客户端 HTTP 超时时间（秒）。",
        "type": "integer",
        "minimum": 1,
        "maximum": 14400,
        "editable": True,
    },
    {
        "key": "MINERU_OPS_PORT",
        "category": "运维控制台",
        "description": "运维控制台监听端口。修改后需要重启服务。",
        "type": "integer",
        "minimum": 1,
        "maximum": 65535,
        "editable": True,
    },
    {
        "key": "MINERU_OPS_AUTH_TOKEN",
        "category": "运维控制台",
        "description": "运维控制台管理令牌；页面只显示脱敏值。",
        "type": "secret",
        "editable": True,
    },
    {
        "key": "MINERU_OPS_TEST_HOST_PATH",
        "category": "运维控制台",
        "description": "宿主机批量测试目录。",
        "type": "string",
        "editable": True,
    },
    {
        "key": "MINERU_OPS_DATA_VOLUME",
        "category": "运维控制台",
        "description": "运维数据持久化目录或 volume 名称。",
        "type": "string",
        "editable": False,
    },
]
CONFIG_SCHEMA_BY_KEY = {item["key"]: item for item in CONFIG_SCHEMA}


def _is_sensitive_config_key(key: str) -> bool:
    upper = key.upper()
    return any(marker in upper for marker in ("TOKEN", "PASSWORD", "SECRET"))


def _parse_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        values[key] = value
    return values


def _masked_value(key: str, value: str) -> str:
    return "••••••••" if _is_sensitive_config_key(key) and value else value


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--socket", required=True, type=Path)
    parser.add_argument("--project-dir", required=True, type=Path)
    parser.add_argument("--env-file", required=True)
    parser.add_argument("--compose-file", action="append", required=True)
    return parser.parse_args()


def unlink_if_exists(path: Path) -> None:
    """Remove a runtime socket without requiring Python 3.8's missing_ok flag."""
    try:
        path.unlink()
    except FileNotFoundError:
        pass


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
            "exit_code": payload.get("ExitCode") if "ExitCode" in payload else payload.get("exit_code"),
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

    def config_path(self) -> Path:
        candidate = Path(self.env_file)
        if not candidate.is_absolute():
            candidate = self.project_dir / candidate
        resolved = candidate.resolve()
        try:
            resolved.relative_to(self.project_dir)
        except ValueError as exc:
            raise ValueError("env file must stay inside the configured project directory") from exc
        return resolved

    def config_schema(self) -> dict[str, Any]:
        return {
            "ok": True,
            "items": CONFIG_SCHEMA,
            "categories": list(dict.fromkeys(item["category"] for item in CONFIG_SCHEMA)),
            "mode": "read_validate_only",
        }

    def config_read(self) -> dict[str, Any]:
        try:
            path = self.config_path()
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        if not path.is_file():
            return {"ok": False, "error": f"configured env file does not exist: {path.name}"}
        try:
            values = _parse_env_file(path)
            modified_at = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat()
        except (OSError, UnicodeError) as exc:
            return {"ok": False, "error": f"failed to read env file: {exc}"}
        items: list[dict[str, Any]] = []
        unknown_keys: list[str] = []
        for key, value in values.items():
            schema = CONFIG_SCHEMA_BY_KEY.get(key)
            sensitive = _is_sensitive_config_key(key)
            if schema is None:
                unknown_keys.append(key)
                items.append(
                    {
                        "key": key,
                        "value": None if sensitive else value,
                        "display_value": _masked_value(key, value),
                        "category": "其他配置",
                        "description": "该变量不在控制台白名单中，只读显示。",
                        "type": "secret" if sensitive else "string",
                        "editable": False,
                        "sensitive": sensitive,
                        "source": "file",
                    }
                )
                continue
            item = dict(schema)
            item.update(
                {
                    "value": None if sensitive else value,
                    "display_value": _masked_value(key, value),
                    "sensitive": sensitive,
                    "source": "file",
                }
            )
            items.append(item)
        existing_keys = set(values)
        for schema in CONFIG_SCHEMA:
            if schema["key"] in existing_keys:
                continue
            item = dict(schema)
            item.update(
                {
                    "value": "",
                    "display_value": "",
                    "sensitive": _is_sensitive_config_key(schema["key"]),
                    "source": "missing",
                }
            )
            items.append(item)
        return {
            "ok": True,
            "env_file": str(path.relative_to(self.project_dir)),
            "modified_at": modified_at,
            "items": items,
            "unknown_keys": sorted(unknown_keys),
            "mode": "read_validate_only",
        }

    def config_validate(self, values: Any) -> dict[str, Any]:
        if not isinstance(values, dict):
            return {"ok": False, "error": "values must be an object of string values"}
        errors: dict[str, str] = {}
        normalized: dict[str, str] = {}
        for raw_key, raw_value in values.items():
            if not isinstance(raw_key, str) or not isinstance(raw_value, str):
                errors[str(raw_key)] = "配置名和候选值必须是字符串"
                continue
            schema = CONFIG_SCHEMA_BY_KEY.get(raw_key)
            if schema is None or not schema.get("editable"):
                errors[raw_key] = "该配置不在可编辑白名单中"
                continue
            value = raw_value.strip()
            if schema["type"] == "integer":
                try:
                    number = int(value)
                except ValueError:
                    errors[raw_key] = "必须填写整数"
                    continue
                minimum = schema.get("minimum")
                maximum = schema.get("maximum")
                if minimum is not None and number < minimum:
                    errors[raw_key] = f"不能小于 {minimum}"
                    continue
                if maximum is not None and number > maximum:
                    errors[raw_key] = f"不能大于 {maximum}"
                    continue
                value = str(number)
            choices = schema.get("choices")
            if choices and value not in choices:
                errors[raw_key] = f"可选值：{', '.join(choices)}"
                continue
            if "\n" in value or "\r" in value:
                errors[raw_key] = "不能包含换行符"
                continue
            normalized[raw_key] = value
        return {
            "ok": not errors,
            "valid": not errors,
            "errors": errors,
            "values": normalized,
            "message": "候选配置校验通过；本阶段不会写入 env 文件。" if not errors else "候选配置存在错误。",
        }

    def config_history(self) -> dict[str, Any]:
        try:
            path = self.config_path()
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        history: list[dict[str, Any]] = []
        for candidate in sorted(path.parent.glob(f"{path.name}.bak-*"), reverse=True)[:20]:
            try:
                stat = candidate.stat()
            except OSError:
                continue
            history.append(
                {
                    "name": candidate.name,
                    "created_at": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
                    "size_bytes": stat.st_size,
                    "restorable": False,
                }
            )
        return {"ok": True, "items": history, "mode": "read_only"}

    def handle(self, request: dict[str, Any]) -> dict[str, Any]:
        action = request.get("action")
        if action == "config_schema":
            return self.config_schema()
        if action == "config_read":
            return self.config_read()
        if action == "config_validate":
            return self.config_validate(request.get("values"))
        if action == "config_history":
            return self.config_history()
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
            self.compose_command(
                "logs",
                "--no-color",
                "--timestamps",
                "--tail",
                str(max(20, min(tail, 2000))),
                service,
            ),
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
    unlink_if_exists(socket_path)
    agent = Agent(args.project_dir, args.env_file, args.compose_file)
    server = ThreadingUnixServer(str(socket_path), AgentRequestHandler)
    server.agent = agent  # type: ignore[attr-defined]
    os.chmod(socket_path, 0o660)
    try:
        server.serve_forever(poll_interval=0.5)
    finally:
        server.server_close()
        unlink_if_exists(socket_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
