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

    result = agent.config_apply({"MINERU_VLM_PAGE_TIMEOUT_SECONDS": "1200"})

    assert result["ok"] is False
    assert result["rolled_back"] is True
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
