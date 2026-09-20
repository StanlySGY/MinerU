"""覆盖 #4 的取消语义:排队中的任务不能被启动,运行中的任务会被中断。

API 侧是取消的执行者,Router 侧是转发者;两层都要能被单独验证。
"""
import asyncio
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from mineru.cli import router as router_module
from mineru.cli.fast_api import (
    AsyncParseTask,
    AsyncTaskManager,
    TASK_CANCELLED,
    TASK_PENDING,
    TASK_PROCESSING,
)
from mineru.cli.router import (
    RouterSettings,
    RouterTaskRecord,
    TASK_COMPLETED,
    create_app,
)


def _task(task_id: str, status: str, output_dir: Path) -> AsyncParseTask:
    return AsyncParseTask(
        task_id=task_id,
        status=status,
        backend="vlm-http-client",
        file_names=["a.pdf"],
        created_at="2026-01-01T00:00:00+00:00",
        output_dir=str(output_dir),
        effort="medium",
        parse_method="auto",
        lang_list=["ch"],
        formula_enable=True,
        table_enable=True,
        image_analysis=True,
        server_url=None,
        return_md=True,
        return_middle_json=False,
        return_model_output=False,
        return_content_list=False,
        return_images=False,
        response_format_zip=False,
        return_original_file=False,
        client_side_output_generation=False,
        start_page_id=0,
        end_page_id=0,
        page_timeout_seconds=600.0,
        page_connect_max_retries=1,
        vlm_batch_size=1,
        processing_window_size=8,
        upload_names=["a.pdf"],
        uploads=["a.pdf"],
    )


def test_cancel_unknown_task_reports_false():
    manager = AsyncTaskManager(fastapi_app=None)  # type: ignore[arg-type]
    assert asyncio.run(manager.cancel("nope")) is False


def test_cancel_pending_task_is_never_started(tmp_path: Path):
    """排队中的任务被取消后,dispatcher 出队时必须跳过它。"""
    manager = AsyncTaskManager(fastapi_app=None)  # type: ignore[arg-type]
    task = _task("queued", TASK_PENDING, tmp_path)
    manager.tasks[task.task_id] = task

    async def scenario():
        assert await manager.cancel("queued") is True
        assert manager.tasks.get("queued") is None
        # dispatcher 会检查这个标记来决定是否真的开工。
        assert "queued" in manager.cancelled_task_ids

    asyncio.run(scenario())


def test_cancel_running_task_cancels_its_processor(tmp_path: Path):
    """运行中的任务:取消要真的中断正在跑的协程,而不只是打标记。"""
    manager = AsyncTaskManager(fastapi_app=None)  # type: ignore[arg-type]
    task = _task("running", TASK_PROCESSING, tmp_path)
    manager.tasks[task.task_id] = task
    started = asyncio.Event()
    stopped = asyncio.Event()

    async def long_running():
        started.set()
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            stopped.set()
            raise

    async def scenario():
        processor = asyncio.create_task(long_running(), name="mineru-fastapi-task-running")
        manager.active_tasks.add(processor)
        manager.processors["running"] = processor
        await started.wait()

        assert await manager.cancel("running") is True

        assert stopped.is_set(), "取消必须传进正在执行的解析协程"
        assert processor.done()
        assert manager.processors.get("running") is None
        assert manager.tasks.get("running") is None

    asyncio.run(scenario())


def test_cancelled_is_terminal():
    from mineru.cli.fast_api import is_task_terminal
    assert is_task_terminal(TASK_CANCELLED)


# ───────────────────────── Router 侧的转发与注册表语义 ─────────────────────────


def _router_record(
    *,
    task_id: str = "router-task",
    status: str = TASK_PROCESSING,
    upstream_task_id: str = "upstream-task",
) -> RouterTaskRecord:
    return RouterTaskRecord(
        task_id=task_id,
        upstream_server_id="api-1",
        upstream_task_id=upstream_task_id,
        upstream_base_url="http://127.0.0.1:8999",
        backend="vlm-http-client",
        file_names=["a.pdf"],
        created_at="2026-01-01T00:00:00+00:00",
        status=status,
    )


class _FakeRegistry:
    """只实现 cancel 端点用到的几个方法,行为与真实注册表一致。"""

    def __init__(self, record: RouterTaskRecord | None):
        self.record = record
        self.removed: list[str] = []

    async def get(self, task_id: str):
        return self.record if self.record and self.record.task_id == task_id else None

    async def increment_upstream_error(self, task_id: str, error: str):
        if self.record is None or self.record.task_id != task_id:
            return None
        self.record.upstream_error_count += 1
        return self.record

    async def update_from_upstream_payload(self, task_id: str, payload: dict):
        if self.record is None or self.record.task_id != task_id:
            return None
        self.record.status = str(payload.get("status", self.record.status))
        return self.record

    async def remove(self, task_id: str):
        if self.record is None or self.record.task_id != task_id:
            return None
        removed, self.record = self.record, None
        self.removed.append(task_id)
        return removed


def _delete_task(handler, record, path):
    """用注入的 app.state 直接打 cancel 端点。

    不进入 lifespan:整个 worker pool 会被拉起来并覆盖注入的 state,而这里要测的
    正是注入进去的那个注册表。
    """
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        app = create_app(RouterSettings(upstream_urls=("http://127.0.0.1:8999",)))
        registry = _FakeRegistry(record)
        app.state.router_task_registry = registry
        app.state.http_client = client
        response = TestClient(app).delete(path)
    finally:
        asyncio.run(client.aclose())
    return response, registry


def test_router_cancel_forwards_delete_to_upstream_and_forgets_task():
    """未结束的任务:Router 必须把取消转发给上游 API,并丢掉本地记录。"""
    record = _router_record()
    calls: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        calls.append(f"{request.method} {request.url.path}")
        if request.method == "GET":
            # 上游确认任务还在跑,取消才有意义。
            return httpx.Response(
                200,
                json={"task_id": record.upstream_task_id, "status": TASK_PROCESSING},
            )
        return httpx.Response(
            200,
            json={"task_id": record.upstream_task_id, "cancelled": True},
        )

    response, registry = _delete_task(handler, record, f"/tasks/{record.task_id}")

    assert response.status_code == 200, response.text
    assert response.json()["cancelled"] is True
    assert calls == [
        f"GET /tasks/{record.upstream_task_id}",
        f"DELETE /tasks/{record.upstream_task_id}",
    ]
    assert registry.removed == [record.task_id]


def test_router_cancel_skips_upstream_delete_for_finished_task():
    """已结束的任务没有可中断的工作:不能再发一次 DELETE 给上游。"""
    record = _router_record(status=TASK_COMPLETED)
    calls: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        calls.append(f"{request.method} {request.url.path}")
        return httpx.Response(200, json={})

    response, registry = _delete_task(handler, record, f"/tasks/{record.task_id}")

    assert response.status_code == 200, response.text
    assert calls == [], "终态任务不该产生任何上游请求"
    assert registry.removed == [record.task_id]


def test_router_cancel_keeps_record_when_upstream_unreachable():
    """上游不可达时保留记录:否则任务还在跑,控制台却再也没有它的句柄。"""
    record = _router_record()

    async def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("upstream down")

    response, registry = _delete_task(handler, record, f"/tasks/{record.task_id}")

    assert response.status_code == 502, response.text
    assert registry.removed == []
    assert registry.record is record


def test_router_cancel_unknown_task_is_404():
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={})

    response, registry = _delete_task(handler, None, "/tasks/missing")

    assert response.status_code == 404
    assert registry.removed == []


def test_router_cancel_drops_record_even_if_upstream_rejects_delete():
    """上游对这个任务已无记录(404)时,本地记录仍要清掉。"""
    record = _router_record(status=TASK_COMPLETED, upstream_task_id="gone")

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="not found")

    response, registry = _delete_task(handler, record, f"/tasks/{record.task_id}")

    assert response.status_code == 200, response.text
    assert registry.removed == [record.task_id]


def test_router_cancel_drops_record_when_upstream_status_lookup_gone():
    """上游已在清理中丢了任务(status 端点 404):本地记录必须能被清掉。"""
    record = _router_record(status=TASK_PROCESSING, upstream_task_id="expired")

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="not found")

    response, registry = _delete_task(handler, record, f"/tasks/{record.task_id}")

    assert response.status_code == 200, response.text
    assert registry.removed == [record.task_id]


def test_router_task_registry_remove_returns_the_dropped_record():
    registry = router_module.RouterTaskRegistry(
        task_retention_seconds=0,
        cleanup_interval_seconds=60,
    )
    record = _router_record()

    async def scenario():
        registry._tasks[record.task_id] = record
        assert await registry.remove(record.task_id) is record
        assert await registry.get(record.task_id) is None
        # 再删一次必须返回 None,调用方据此报 404 而不是假装成功。
        assert await registry.remove(record.task_id) is None

    asyncio.run(scenario())
