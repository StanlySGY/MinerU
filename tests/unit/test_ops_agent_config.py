from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


AGENT_PATH = Path(__file__).resolve().parents[2] / "docker" / "multi" / "mineru-ops-agent.py"
SPEC = importlib.util.spec_from_file_location("mineru_ops_agent", AGENT_PATH)
assert SPEC is not None and SPEC.loader is not None
ops_agent = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ops_agent)


def make_agent(project_dir: Path, env_file: str = "env.multi"):
    agent = ops_agent.Agent.__new__(ops_agent.Agent)
    agent.project_dir = project_dir.resolve()
    agent.env_file = env_file
    agent.compose_files = ["compose-multi.yaml"]
    agent.compose_prefix = ["docker", "compose"]
    return agent


def test_parse_env_file_supports_comments_export_and_quotes(tmp_path: Path) -> None:
    env_file = tmp_path / "env.multi"
    env_file.write_text(
        """# comment
export MINERU_MODEL_SOURCE=local
MINERU_DEVICE_MODE='npu'
MINERU_VLM_MODEL=\"/models/vlm=latest\"
INVALID KEY=ignored
NO_EQUALS
""",
        encoding="utf-8",
    )

    assert ops_agent._parse_env_file(env_file) == {
        "MINERU_MODEL_SOURCE": "local",
        "MINERU_DEVICE_MODE": "npu",
        "MINERU_VLM_MODEL": "/models/vlm=latest",
    }


def test_config_read_masks_secrets_and_marks_unknown_values_read_only(tmp_path: Path) -> None:
    (tmp_path / "env.multi").write_text(
        """MINERU_MODEL_SOURCE=local
MINERU_OPS_AUTH_TOKEN=top-secret
CUSTOM_PASSWORD=hidden-value
CUSTOM_LABEL=site-a
""",
        encoding="utf-8",
    )
    agent = make_agent(tmp_path)

    result = agent.config_read()

    assert result["ok"] is True
    assert result["env_file"] == "env.multi"
    items = {item["key"]: item for item in result["items"]}
    assert items["MINERU_MODEL_SOURCE"]["value"] == "local"
    assert items["MINERU_OPS_AUTH_TOKEN"]["value"] is None
    assert items["MINERU_OPS_AUTH_TOKEN"]["display_value"] == "••••••••"
    assert items["CUSTOM_PASSWORD"]["value"] is None
    assert items["CUSTOM_PASSWORD"]["editable"] is False
    assert items["CUSTOM_LABEL"]["value"] == "site-a"
    assert items["MINERU_DEVICE_MODE"]["source"] == "missing"
    assert result["unknown_keys"] == ["CUSTOM_LABEL", "CUSTOM_PASSWORD"]


def test_config_validate_normalizes_valid_values_and_rejects_invalid_candidates(tmp_path: Path) -> None:
    agent = make_agent(tmp_path)

    valid = agent.config_validate(
        {
            "MINERU_MODEL_SOURCE": " local ",
            "MINERU_VLM_PAGE_TIMEOUT_SECONDS": "1200",
            "MINERU_VLM_CONNECT_MAX_RETRIES": "0",
        }
    )
    invalid = agent.config_validate(
        {
            "MINERU_MODEL_SOURCE": "internet",
            "MINERU_VLM_PAGE_TIMEOUT_SECONDS": "7201",
            "MINERU_OPS_DATA_VOLUME": "not-editable",
            "UNKNOWN_SETTING": "1",
        }
    )

    assert valid["ok"] is True
    assert valid["valid"] is True
    assert valid["values"] == {
        "MINERU_MODEL_SOURCE": "local",
        "MINERU_VLM_PAGE_TIMEOUT_SECONDS": "1200",
        "MINERU_VLM_CONNECT_MAX_RETRIES": "0",
    }
    assert invalid["ok"] is False
    assert invalid["valid"] is False
    assert set(invalid["errors"]) == {
        "MINERU_MODEL_SOURCE",
        "MINERU_VLM_PAGE_TIMEOUT_SECONDS",
        "MINERU_OPS_DATA_VOLUME",
        "UNKNOWN_SETTING",
    }


def test_config_path_must_remain_inside_project_directory(tmp_path: Path) -> None:
    project_dir = tmp_path / "multi"
    project_dir.mkdir()
    agent = make_agent(project_dir, "../env.multi")

    with pytest.raises(ValueError, match="must stay inside"):
        agent.config_path()


def test_config_history_is_restorable_and_limited_to_latest_twenty(tmp_path: Path) -> None:
    env_file = tmp_path / "env.multi"
    env_file.write_text("MINERU_MODEL_SOURCE=local\n", encoding="utf-8")
    for index in range(25):
        backup = tmp_path / f"env.multi.bak-{index:02d}"
        backup.write_text(str(index), encoding="utf-8")
    agent = make_agent(tmp_path)

    result = agent.config_history()

    assert result["ok"] is True
    assert result["mode"] == "safe_apply"
    assert len(result["items"]) == 20
    assert all(item["restorable"] is True for item in result["items"])


def test_config_actions_are_dispatched_by_agent_handle(tmp_path: Path) -> None:
    (tmp_path / "env.multi").write_text("MINERU_MODEL_SOURCE=local\n", encoding="utf-8")
    agent = make_agent(tmp_path)

    assert agent.handle({"action": "config_schema"})["mode"] == "safe_apply"
    assert agent.handle({"action": "config_read"})["ok"] is True
    assert agent.handle(
        {
            "action": "config_validate",
            "values": {"MINERU_VLM_PAGE_TIMEOUT_SECONDS": "1200"},
        }
    )["valid"] is True
    assert agent.handle({"action": "config_history"})["mode"] == "safe_apply"


def test_render_env_file_preserves_comments_order_export_and_adds_descriptions(tmp_path: Path) -> None:
    env_file = tmp_path / "env.multi"
    env_file.write_text(
        "# header\n\nexport MINERU_MODEL_SOURCE=local\nUNKNOWN=value\n",
        encoding="utf-8",
    )

    rendered = ops_agent._render_env_file(
        env_file,
        {
            "MINERU_MODEL_SOURCE": "modelscope",
            "MINERU_VLM_PAGE_TIMEOUT_SECONDS": "1200",
        },
    )

    assert rendered.index("# header") < rendered.index("MINERU_MODEL_SOURCE")
    assert "export MINERU_MODEL_SOURCE=modelscope" in rendered
    assert "UNKNOWN=value" in rendered
    assert "单页 VLM 请求的软超时时间" in rendered
    assert "MINERU_VLM_PAGE_TIMEOUT_SECONDS=1200" in rendered


def test_config_plan_validates_compose_without_writing_or_leaking_secret(tmp_path: Path, monkeypatch) -> None:
    env_file = tmp_path / "env.multi"
    env_file.write_text(
        "MINERU_MODEL_SOURCE=local\nMINERU_OPS_AUTH_TOKEN=old-secret\n",
        encoding="utf-8",
    )
    agent = make_agent(tmp_path)
    calls = []

    def fake_run(command, cwd, timeout=180):
        calls.append((command, timeout))
        return {"ok": True, "output": "", "error": None}

    monkeypatch.setattr(ops_agent, "run_command", fake_run)
    result = agent.config_plan(
        {
            "MINERU_MODEL_SOURCE": "local",
            "MINERU_OPS_AUTH_TOKEN": "new-secret",
        }
    )

    assert result["ok"] is True
    assert result["compose_valid"] is True
    assert result["changes"] == [{"key": "MINERU_OPS_AUTH_TOKEN", "sensitive": True, "changed": True}]
    assert "new-secret" not in repr(result)
    assert env_file.read_text(encoding="utf-8") == "MINERU_MODEL_SOURCE=local\nMINERU_OPS_AUTH_TOKEN=old-secret\n"
    assert calls and calls[0][0][-2:] == ["config", "--quiet"]
    assert "--env-file" in calls[0][0]


def test_config_apply_recreates_services_and_rolls_back_on_failure(tmp_path: Path, monkeypatch) -> None:
    env_file = tmp_path / "env.multi"
    env_file.write_text("MINERU_MODEL_SOURCE=local\n", encoding="utf-8")
    agent = make_agent(tmp_path)
    commands = []
    recreate_results = iter([{"ok": False, "error": "recreate failed"}, {"ok": True, "output": ""}])

    def fake_run(command, cwd, timeout=180):
        commands.append(command)
        if "config" in command:
            return {"ok": True, "output": "", "error": None}
        return {"ok": True, "output": "", "error": None}

    monkeypatch.setattr(ops_agent, "run_command", fake_run)
    monkeypatch.setattr(agent, "configured_services", lambda: {"mineru-api-1", "mineru-router", "mineru-ops"})
    monkeypatch.setattr(agent, "_recreate_services", lambda services: next(recreate_results))
    monkeypatch.setattr(
        agent,
        "_wait_for_services",
        lambda services: {"ok": True, "services": {}, "elapsed_seconds": 0.0},
    )

    result = agent.config_apply({"MINERU_VLM_PAGE_TIMEOUT_SECONDS": "1200"})

    assert result["ok"] is False
    assert result["rolled_back"] is True
    assert result["steps"]
    assert result["original_error"] == "recreate failed"
    assert result["env_restored"] is True
    assert result["services_restored"] is True
    assert result["rollback_status"] == "completed"
    assert "manual_actions" in result
    assert "rollback_error" in result
    assert "MINERU_VLM_PAGE_TIMEOUT_SECONDS=600" not in env_file.read_text(encoding="utf-8")
    assert "MINERU_MODEL_SOURCE=local" in env_file.read_text(encoding="utf-8")


def test_ops_only_config_does_not_recreate_api_services(tmp_path: Path, monkeypatch) -> None:
    env_file = tmp_path / "env.multi"
    env_file.write_text("MINERU_OPS_PORT=19000\n", encoding="utf-8")
    agent = make_agent(tmp_path)
    monkeypatch.setattr(ops_agent, "run_command", lambda *args, **kwargs: {"ok": True, "output": "", "error": None})
    monkeypatch.setattr(agent, "configured_services", lambda: {"mineru-api-1", "mineru-router", "mineru-ops"})

    services, requires_ops_restart = agent._affected_services(["MINERU_OPS_PORT"])

    assert services == []
    assert requires_ops_restart is True


def _runtime_inspect(
    env: list[str] | None = None,
    mounts: list[dict] | None = None,
    *,
    path: str = "",
    args: list[str] | None = None,
    cmd: list[str] | None = None,
) -> dict:
    return {
        "Path": path,
        "Args": args or [],
        "Config": {
            "Image": "mineru:test",
            "Env": env or [],
            "Cmd": cmd or [],
        },
        "State": {
            "Status": "running",
            "Health": {"Status": "healthy"},
            "StartedAt": "2026-08-23T00:00:00Z",
        },
        "Mounts": mounts or [],
    }


def _effective_item(result: dict, key: str) -> dict:
    return next(item for item in result["items"] if item["key"] == key)


def _mock_effective_runtime(monkeypatch, agent, inspections: dict[str, dict]) -> None:
    service_names = set(inspections)
    containers = {service: f"{service}-container" for service in service_names}
    monkeypatch.setattr(agent, "configured_services", lambda: service_names)
    monkeypatch.setattr(
        agent,
        "services",
        lambda: {
            "ok": True,
            "services": {
                service: {
                    "container": container,
                    "state": "running",
                    "health": "healthy",
                }
                for service, container in containers.items()
            },
        },
    )
    monkeypatch.setattr(
        agent,
        "_inspect_container",
        lambda container: (
            inspections[next(service for service, name in containers.items() if name == container)],
            None,
        ),
    )


def test_config_effective_reads_space_separated_command_option(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "env.multi").write_text(
        "MINERU_VLM_CLIENT_HTTP_TIMEOUT=600\n",
        encoding="utf-8",
    )
    agent = make_agent(tmp_path)
    _mock_effective_runtime(
        monkeypatch,
        agent,
        {
            "mineru-api-1": _runtime_inspect(
                ["MINERU_VLM_CLIENT_HTTP_TIMEOUT=600"],
                path="python",
                args=["-m", "mineru.cli.client", "--http-timeout", "1200"],
            )
        },
    )

    detail = _effective_item(
        agent.config_effective(),
        "MINERU_VLM_CLIENT_HTTP_TIMEOUT",
    )["services"]["mineru-api-1"]

    assert detail["command_value"] == "1200"
    assert detail["effective_value"] == "1200"
    assert detail["effective_source"] == "command"


def test_config_effective_reads_equals_command_option(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "env.multi").write_text(
        "MINERU_VLM_CLIENT_HTTP_TIMEOUT=600\n",
        encoding="utf-8",
    )
    agent = make_agent(tmp_path)
    _mock_effective_runtime(
        monkeypatch,
        agent,
        {
            "mineru-api-1": _runtime_inspect(
                ["MINERU_VLM_CLIENT_HTTP_TIMEOUT=600"],
                path="python",
                args=["-m", "mineru.cli.client", "--http-timeout=1200"],
            )
        },
    )

    detail = _effective_item(
        agent.config_effective(),
        "MINERU_VLM_CLIENT_HTTP_TIMEOUT",
    )["services"]["mineru-api-1"]

    assert detail["effective_value"] == "1200"
    assert detail["effective_source"] == "command"


def test_config_effective_command_overrides_container_environment(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "env.multi").write_text(
        "MINERU_VLM_CLIENT_HTTP_TIMEOUT=1200\n",
        encoding="utf-8",
    )
    agent = make_agent(tmp_path)
    _mock_effective_runtime(
        monkeypatch,
        agent,
        {
            "mineru-api-1": _runtime_inspect(
                ["MINERU_VLM_CLIENT_HTTP_TIMEOUT=900"],
                path="python",
                args=["--http-timeout", "1200"],
            )
        },
    )

    detail = _effective_item(
        agent.config_effective(),
        "MINERU_VLM_CLIENT_HTTP_TIMEOUT",
    )["services"]["mineru-api-1"]

    assert detail["container_env_value"] == "900"
    assert detail["effective_value"] == "1200"
    assert detail["effective_source"] == "command"
    assert detail["status"] == "applied"


def test_config_effective_uses_known_default_for_single_api_service(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "env.multi").write_text("MINERU_MODEL_SOURCE=local\n", encoding="utf-8")
    agent = make_agent(tmp_path)
    _mock_effective_runtime(
        monkeypatch,
        agent,
        {"mineru-api": _runtime_inspect(path="python", args=["-m", "mineru.cli.client"])},
    )

    detail = _effective_item(
        agent.config_effective(),
        "MINERU_VLM_CLIENT_HTTP_TIMEOUT",
    )["services"]["mineru-api"]

    assert detail["default_value"] == "7200"
    assert detail["effective_value"] == "7200"
    assert detail["effective_source"] == "default"


def test_config_effective_reports_timeout_conflict(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "env.multi").write_text(
        "MINERU_VLM_PAGE_TIMEOUT_SECONDS=1200\n"
        "MINERU_VLM_CLIENT_HTTP_TIMEOUT=600\n",
        encoding="utf-8",
    )
    agent = make_agent(tmp_path)
    _mock_effective_runtime(
        monkeypatch,
        agent,
        {
            "mineru-api-1": _runtime_inspect(
                [
                    "MINERU_VLM_PAGE_TIMEOUT_SECONDS=1200",
                    "MINERU_VLM_CLIENT_HTTP_TIMEOUT=600",
                ]
            )
        },
    )

    result = agent.config_effective()
    page_detail = _effective_item(
        result,
        "MINERU_VLM_PAGE_TIMEOUT_SECONDS",
    )["services"]["mineru-api-1"]
    http_detail = _effective_item(
        result,
        "MINERU_VLM_CLIENT_HTTP_TIMEOUT",
    )["services"]["mineru-api-1"]

    assert result["overall"] == "conflict"
    assert page_detail["status"] == "conflict"
    assert http_detail["status"] == "conflict"


def test_config_effective_reports_inconsistent_api_instances(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "env.multi").write_text(
        "MINERU_VLM_CLIENT_HTTP_TIMEOUT=1200\n",
        encoding="utf-8",
    )
    agent = make_agent(tmp_path)
    _mock_effective_runtime(
        monkeypatch,
        agent,
        {
            "mineru-api-1": _runtime_inspect(
                path="python",
                args=["--http-timeout", "1200"],
            ),
            "mineru-api-2": _runtime_inspect(
                path="python",
                args=["--http-timeout=1800"],
            ),
        },
    )

    result = agent.config_effective()
    services = _effective_item(
        result,
        "MINERU_VLM_CLIENT_HTTP_TIMEOUT",
    )["services"]

    assert result["overall"] == "inconsistent"
    assert services["mineru-api-1"]["status"] == "inconsistent"
    assert services["mineru-api-2"]["status"] == "inconsistent"


def test_config_effective_degrades_when_compose_status_is_unavailable(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "env.multi").write_text(
        "MINERU_VLM_CLIENT_HTTP_TIMEOUT=1200\n",
        encoding="utf-8",
    )
    agent = make_agent(tmp_path)
    monkeypatch.setattr(agent, "configured_services", lambda: {"mineru-api-1"})
    monkeypatch.setattr(
        agent,
        "services",
        lambda: {"ok": False, "error": "compose failed"},
    )

    result = agent.config_effective()

    assert result["ok"] is True
    assert result["overall"] == "unknown"
    assert "compose failed" in result["warnings"]


def test_wait_for_services_fails_fast_when_compose_status_is_unavailable(tmp_path: Path, monkeypatch) -> None:
    agent = make_agent(tmp_path)
    monkeypatch.setattr(
        agent,
        "services",
        lambda: {"ok": False, "error": "compose failed"},
    )

    result = agent._wait_for_services(
        ["mineru-api-1"],
        timeout_seconds=999,
        poll_interval=0,
    )

    assert result["ok"] is False
    assert result["error"] == "compose failed"
    assert result["elapsed_seconds"] < 1


def test_wait_for_services_handles_empty_compose_response(tmp_path: Path, monkeypatch) -> None:
    agent = make_agent(tmp_path)
    monkeypatch.setattr(agent, "services", lambda: None)

    result = agent._wait_for_services(
        ["mineru-api-1"],
        timeout_seconds=1,
        poll_interval=0,
    )

    assert result["ok"] is False
    assert result["error"] == "无法读取 Compose 服务状态"


def test_redact_payload_masks_secrets_recursively() -> None:
    payload = {
        "error": "token=top-secret",
        "nested": [{"command": "echo top-secret"}],
    }

    result = ops_agent._redact_payload(payload, ["top-secret"])

    assert "top-secret" not in repr(result)
    assert ops_agent.MASKED_VALUE in repr(result)


def test_sha256_file_is_stable_and_changes_with_contents(tmp_path: Path) -> None:
    path = tmp_path / "env.multi"
    path.write_text("MINERU_MODEL_SOURCE=local\n", encoding="utf-8")

    first = ops_agent._sha256_file(path)
    assert first == ops_agent._sha256_file(path)

    path.write_text("MINERU_MODEL_SOURCE=modelscope\n", encoding="utf-8")
    assert ops_agent._sha256_file(path) != first


def test_config_read_returns_file_hash_config_hash_and_version(tmp_path: Path) -> None:
    env_file = tmp_path / "env.multi"
    env_file.write_text("MINERU_MODEL_SOURCE=local\n", encoding="utf-8")
    agent = make_agent(tmp_path)

    result = agent.config_read()

    assert result["ok"] is True
    assert result["sha256"] == ops_agent._sha256_file(env_file)
    assert result["config_hash"] == ops_agent._config_hash({"MINERU_MODEL_SOURCE": "local"})
    assert result["version"]
    assert result["version"].endswith(result["sha256"][:8])


def test_parse_environment_list_handles_equals_and_ignores_invalid_items() -> None:
    assert ops_agent._parse_environment_list(
        ["A=one", "B=value=with=equals", "NO_EQUALS", 42, "=missing-key"]
    ) == {"A": "one", "B": "value=with=equals"}


def test_config_status_reports_applied_when_runtime_matches_env(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "env.multi").write_text(
        "MINERU_MODEL_SOURCE=local\nMINERU_VLM_PAGE_TIMEOUT_SECONDS=1200\n",
        encoding="utf-8",
    )
    agent = make_agent(tmp_path)
    monkeypatch.setattr(agent, "configured_services", lambda: {"mineru-api-1"})
    monkeypatch.setattr(
        agent,
        "services",
        lambda: {"ok": True, "services": {"mineru-api-1": {"container": "api-1", "state": "running", "health": "healthy", "image": "mineru:test"}}},
    )
    monkeypatch.setattr(
        agent,
        "_inspect_container",
        lambda container: (_runtime_inspect(["MINERU_MODEL_SOURCE=local", "MINERU_VLM_PAGE_TIMEOUT_SECONDS=1200"]), None),
    )

    result = agent.config_status()

    assert result["ok"] is True
    assert result["overall"] == "applied"
    assert result["summary"]["applied"] == 1
    assert result["services"]["mineru-api-1"]["status"] == "applied"
    assert result["services"]["mineru-api-1"]["mismatched_keys"] == []


def test_config_status_prefers_api_command_options_over_container_env(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "env.multi").write_text("MINERU_VLM_CLIENT_HTTP_TIMEOUT=1200\n", encoding="utf-8")
    agent = make_agent(tmp_path)
    monkeypatch.setattr(agent, "configured_services", lambda: {"mineru-api-1"})
    monkeypatch.setattr(agent, "services", lambda: {"ok": True, "services": {"mineru-api-1": {"container": "api-1"}}})
    inspect = _runtime_inspect(["MINERU_VLM_CLIENT_HTTP_TIMEOUT=600"])
    inspect["Path"] = "python"
    inspect["Args"] = ["-m", "mineru.cli.fast_api", "--http-timeout", "1200"]
    monkeypatch.setattr(agent, "_inspect_container", lambda container: (inspect, None))

    result = agent.config_status()

    assert result["services"]["mineru-api-1"]["status"] == "applied"
    assert result["services"]["mineru-api-1"]["mismatched_keys"] == []


def test_config_status_reports_pending_restart_for_mismatch(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "env.multi").write_text("MINERU_VLM_PAGE_TIMEOUT_SECONDS=1200\n", encoding="utf-8")
    agent = make_agent(tmp_path)
    monkeypatch.setattr(agent, "configured_services", lambda: {"mineru-api-1"})
    monkeypatch.setattr(agent, "services", lambda: {"ok": True, "services": {"mineru-api-1": {"container": "api-1"}}})
    monkeypatch.setattr(
        agent,
        "_inspect_container",
        lambda container: (_runtime_inspect(["MINERU_VLM_PAGE_TIMEOUT_SECONDS=600"]), None),
    )

    result = agent.config_status()
    service = result["services"]["mineru-api-1"]

    assert result["overall"] == "pending_restart"
    assert service["status"] == "pending_restart"
    assert service["requires_restart"] is True
    assert service["mismatched_keys"] == [{"key": "MINERU_VLM_PAGE_TIMEOUT_SECONDS", "sensitive": False, "expected": "1200", "actual": "600"}]


def test_config_status_masks_sensitive_mismatch_values(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "env.multi").write_text("MINERU_OPS_AUTH_TOKEN=new-secret\n", encoding="utf-8")
    agent = make_agent(tmp_path)
    monkeypatch.setattr(agent, "configured_services", lambda: {"mineru-ops"})
    monkeypatch.setattr(agent, "services", lambda: {"ok": True, "services": {"mineru-ops": {"container": "ops-1"}}})
    monkeypatch.setattr(agent, "_inspect_container", lambda container: (_runtime_inspect(["MINERU_OPS_AUTH_TOKEN=old-secret"]), None))

    result = agent.config_status()
    mismatch = result["services"]["mineru-ops"]["mismatched_keys"][0]

    assert mismatch["sensitive"] is True
    assert mismatch["expected"] == ops_agent.MASKED_VALUE
    assert mismatch["actual"] == ops_agent.MASKED_VALUE
    assert "new-secret" not in repr(result)
    assert "old-secret" not in repr(result)


def test_config_status_keeps_inspect_failure_as_unknown_without_failing_endpoint(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "env.multi").write_text("MINERU_MODEL_SOURCE=local\n", encoding="utf-8")
    agent = make_agent(tmp_path)
    monkeypatch.setattr(agent, "configured_services", lambda: {"mineru-api-1"})
    monkeypatch.setattr(agent, "services", lambda: {"ok": True, "services": {"mineru-api-1": {"container": "api-1"}}})
    monkeypatch.setattr(agent, "_inspect_container", lambda container: (None, "docker inspect failed"))

    result = agent.config_status()

    assert result["ok"] is True
    assert result["overall"] == "unknown"
    assert result["services"]["mineru-api-1"]["status"] == "unknown"
    assert result["services"]["mineru-api-1"]["warnings"] == ["docker inspect failed"]


def test_config_status_reports_not_created_container(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "env.multi").write_text("MINERU_MODEL_SOURCE=local\n", encoding="utf-8")
    agent = make_agent(tmp_path)
    monkeypatch.setattr(agent, "configured_services", lambda: {"mineru-api-1"})
    monkeypatch.setattr(agent, "services", lambda: {"ok": True, "services": {"mineru-api-1": {"container": None}}})

    result = agent.config_status()

    assert result["overall"] == "unknown"
    assert result["summary"]["not_created"] == 1
    assert result["services"]["mineru-api-1"]["status"] == "not_created"


def test_service_specific_whitelist_does_not_report_api_only_keys_on_router(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "env.multi").write_text(
        "MINERU_MODEL_SOURCE=local\nMINERU_VLM_PAGE_TIMEOUT_SECONDS=1200\n",
        encoding="utf-8",
    )
    agent = make_agent(tmp_path)
    monkeypatch.setattr(agent, "configured_services", lambda: {"mineru-api-1", "mineru-router"})
    monkeypatch.setattr(
        agent,
        "services",
        lambda: {"ok": True, "services": {"mineru-api-1": {"container": "api-1"}, "mineru-router": {"container": "router-1"}}},
    )
    inspections = {
        "api-1": _runtime_inspect(["MINERU_MODEL_SOURCE=local"]),
        "router-1": _runtime_inspect(["MINERU_VLM_PAGE_TIMEOUT_SECONDS=1200"]),
    }
    monkeypatch.setattr(agent, "_inspect_container", lambda container: (inspections[container], None))

    result = agent.config_status()

    assert result["services"]["mineru-router"]["status"] == "applied"
    assert result["services"]["mineru-router"]["missing_keys"] == []


def test_unknown_service_cannot_be_reported_as_applied(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "env.multi").write_text("MINERU_MODEL_SOURCE=local\n", encoding="utf-8")
    agent = make_agent(tmp_path)
    monkeypatch.setattr(agent, "configured_services", lambda: {"other-service"})
    monkeypatch.setattr(agent, "services", lambda: {"ok": True, "services": {"other-service": {"container": "other-1"}}})
    monkeypatch.setattr(agent, "_inspect_container", lambda container: (_runtime_inspect(["MINERU_MODEL_SOURCE=local"]), None))

    result = agent.config_status()

    assert result["overall"] == "unknown"
    assert result["services"]["other-service"]["status"] == "unknown"
    assert result["services"]["other-service"]["warnings"]


def test_deep_diagnostics_returns_environment_mounts_models_and_devices(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "env.multi").write_text("MINERU_VLM_MODEL=/models/vlm\n", encoding="utf-8")
    agent = make_agent(tmp_path)
    status = {
        "ok": True,
        "generated_at": "2026-08-23T00:00:00+00:00",
        "env_file": {"path": "env.multi"},
        "overall": "applied",
        "warnings": [],
        "services": {"mineru-api-1": {"container": "api-1", "status": "applied", "requires_restart": False}},
    }
    monkeypatch.setattr(agent, "config_status", lambda: status)
    monkeypatch.setattr(
        agent,
        "_inspect_container",
        lambda container: (_runtime_inspect(
            ["MINERU_VLM_MODEL=/models/vlm", "MINERU_OPS_AUTH_TOKEN=secret"],
            [{"Type": "bind", "Source": "/host/models", "Destination": "/models", "RW": False}],
        ), None),
    )

    def fake_run(command, cwd, timeout=180):
        if command[:3] == ["docker", "exec", "api-1"] and "find" in command[-1]:
            return {"ok": True, "output": "0 4\n", "error": None}
        return {"ok": False, "output": "", "error": "command not found"}

    monkeypatch.setattr(ops_agent, "run_command", fake_run)
    result = agent.deep_diagnostics()
    service = result["services"]["mineru-api-1"]

    assert result["ok"] is True
    assert service["environment"]["MINERU_VLM_MODEL"] == "/models/vlm"
    assert service["environment"]["MINERU_OPS_AUTH_TOKEN"] == ops_agent.MASKED_VALUE
    assert service["mounts"] == [{"type": "bind", "source": "/host/models", "destination": "/models", "rw": False}]
    assert service["models"][0]["path"] == "/models/vlm"
    assert service["models"][0]["exists"] is True
    assert service["models"][0]["file_count"] == 4
    assert service["devices"]["npu"]["available"] is False
    assert service["devices"]["gpu"]["available"] is False
    assert "secret" not in repr(result)


def test_deep_diagnostics_degrades_when_container_tools_are_missing(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "env.multi").write_text("MINERU_MODEL_SOURCE=local\n", encoding="utf-8")
    agent = make_agent(tmp_path)
    monkeypatch.setattr(
        agent,
        "config_status",
        lambda: {"ok": True, "generated_at": "now", "env_file": {}, "overall": "applied", "warnings": [], "services": {"mineru-router": {"container": "router-1", "status": "applied"}}},
    )
    monkeypatch.setattr(agent, "_inspect_container", lambda container: (_runtime_inspect(["MINERU_MODEL_SOURCE=local"]), None))
    monkeypatch.setattr(ops_agent, "run_command", lambda *args, **kwargs: {"ok": False, "output": "", "error": "not found"})

    result = agent.deep_diagnostics()

    assert result["ok"] is True
    assert result["services"]["mineru-router"]["devices"]["npu"]["ok"] is False
    assert result["services"]["mineru-router"]["devices"]["gpu"]["ok"] is False


def test_agent_handle_dispatches_new_diagnostic_actions(tmp_path: Path, monkeypatch) -> None:
    agent = make_agent(tmp_path)
    monkeypatch.setattr(agent, "config_status", lambda: {"ok": True, "kind": "config"})
    monkeypatch.setattr(
        agent,
        "config_effective",
        lambda: {"ok": True, "kind": "effective"},
    )
    monkeypatch.setattr(agent, "deep_diagnostics", lambda: {"ok": True, "kind": "deep"})

    assert agent.handle({"action": "config_status"}) == {"ok": True, "kind": "config"}
    assert agent.handle({"action": "config_effective"}) == {
        "ok": True,
        "kind": "effective",
    }
    assert agent.handle({"action": "deep_diagnostics"}) == {"ok": True, "kind": "deep"}
