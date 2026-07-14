# AgentOS 单一异步 QueryLoop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 AgentOS Kernel 收敛为一个原生异步 `QueryLoop`、一条 `AgentStream` 事件流和一个 `Agent.run(..., stream=...)` 公共入口，并在 `agentos.sync` 提供不复制控制流的同步适配。

**Architecture:** `Agent` 只负责输入标准化和 outcome 投影，`QueryLoop.execute(RunRequest)` 是唯一 Turn/Provider/Tool 编排入口，`AgentStream` 拥有单次执行租约、消费、取消和清理生命周期。Provider 与 Tool 的同步实现只在 executor/bridge 边界适配；公共切换在一个原子提交中同步迁移 Builder、Profile、Channel、A2A、Multi-agent、Observability、示例、文档和 Public API，不保留旧 API alias 或动态 fallback。

**Tech Stack:** Python 3.11、`asyncio`、dataclasses、typing overload/Protocol、pytest、Ruff、现有 AgentOS Provider/Tool/Message/Context/Hook/EventBus 组件。

---

## 实施纪律

- 前置任务只能新增未从 `agentos` 或 `agentos.runtime` 导出的基础能力，不得接管正式执行路径，也不得形成第二个可调用 Loop。
- Task 6 是唯一公共切换提交。它必须由单一 Owner 串行修改共享运行时文件；其他开发者只可准备不重叠的补丁清单和测试建议。
- Task 6 提交结束时必须同时完成 Core、消费者、导出、版本、迁移文档、API policy/inventory 和旧实现删除。禁止提交半迁移状态。
- 每个任务严格执行 Red -> 确认失败原因 -> 最小实现 -> Green -> commit。不得在提交之间保留失败测试。
- Tasks 1-5 的 commit 步骤执行前，必须分别完成一次 Spec Compliance Review 和一次 Code Quality Review，结论无阻断项后才运行工作包门禁并提交；Task 6/6B 在原子提交前做同样双层 Review。
- 不增加兼容 alias、`__getattr__`、`hasattr()` 能力探测、同步 QueryLoop、第二套 Runner 或旧方法转发。
- `runtime/query_loop.py` 目标少于 500 行，`runtime/agent.py` 与 `runtime/agent_stream.py` 各少于 250 行，`runtime/provider_attempt.py` 少于 250 行。

原子切换前 no-new-responsibility 行数上限固定为：`channels/asgi.py` 2200、`channels/a2a_operations.py` 4646、`multi/team.py` 2036、`multi/coordinator.py` 720、`observability/instrumented.py` 1273。Task 6 必须通过明确的新模块提取使这些文件不增长；规模 baseline 不允许上调上述数值。

## 文件职责图

| 文件 | 单一职责 |
|---|---|
| `src/agentos/runtime/run.py` | `RunInput`、`RunRequest`、`RunOptions`、outcome 与 WAITING 值类型 |
| `src/agentos/runtime/errors.py` | Agent/Run/Stream 稳定契约错误 |
| `src/agentos/runtime/agent_stream.py` | 执行租约、Stream 状态机、取消和幂等 cleanup |
| `src/agentos/runtime/provider_attempt.py` | 唯一异步 Provider attempt runner 与 sync Provider bridge |
| `src/agentos/runtime/tool_scheduler.py` | 单批 Tool 调度、有界并发、结果顺序和 sync Tool bridge |
| `src/agentos/runtime/query_loop.py` | 唯一异步 Turn/Provider/Tool 编排 |
| `src/agentos/runtime/agent.py` | 公共 facade、输入标准化、事件流 outcome collector |
| `src/agentos/sync/agent.py` | 长生命周期 owner thread/Runner 与同步 Agent facade |
| `src/agentos/sync/stream.py` | 同步 Stream iterator/context manager |
| `src/agentos/observability/query_loop.py` | 保留 `AgentStream` 生命周期的 QueryLoop instrumentation wrapper |

### Task 1: 建立运行值类型和稳定错误边界

**Files:**
- Create: `src/agentos/runtime/run.py`
- Create: `src/agentos/runtime/errors.py`
- Create: `src/agentos/runtime/waiting.py`
- Create: `tests/runtime/test_run_contracts.py`
- Create: `tests/runtime/test_waiting_runtime_contract.py`

- [ ] **Step 1: 写入值类型和错误契约失败测试**

```python
from agentos.attachments import Attachment, UrlSource
from agentos.runtime.errors import AgentBusyError, AgentRunError
from agentos.runtime.run import (
    AgentResult,
    AgentWaiting,
    LocalContinuationInput,
    RunRequest,
    UserTurnInput,
    WaitReason,
)
from agentos.runtime.stream_events import RunOptions
from agentos.runtime.waiting import WaitingCommit, WaitingRuntime


def test_run_request_is_immutable_and_typed() -> None:
    attachment = Attachment(
        handle="art_1",
        filename="drawing.png",
        mime_type="image/png",
        size_bytes=128,
        source=UrlSource("https://example.invalid/drawing.png"),
    )
    request = RunRequest(
        input=UserTurnInput("分析图纸", attachments=(attachment,)),
        options=RunOptions(show_thinking=True),
    )
    assert request.input.attachments == (attachment,)


def test_waiting_outcome_is_distinct_from_completed_outcome() -> None:
    reason = WaitReason(kind="human_input", handle="approval_1")
    assert AgentWaiting("run_1", reason) != AgentResult("")


def test_busy_error_is_an_agent_run_error() -> None:
    assert issubclass(AgentBusyError, AgentRunError)
    assert LocalContinuationInput() == LocalContinuationInput()


def test_waiting_runtime_exposes_only_authoritative_commit() -> None:
    public_methods = {name for name in vars(WaitingRuntime) if not name.startswith("_")}
    assert public_methods == {"commit_waiting"}
    assert tuple(WaitingCommit.__dataclass_fields__) == ("run_id", "reason")
```

- [ ] **Step 2: 运行测试并确认只因模块/类型尚不存在而失败**

Run: `python -m pytest tests/runtime/test_run_contracts.py tests/runtime/test_waiting_runtime_contract.py -q`

Expected: FAIL，错误为 `ModuleNotFoundError`，不是 fixture 或环境错误。

- [ ] **Step 3: 实现不可变运行模型、WAITING 事件和错误层级**

```python
# src/agentos/runtime/run.py
@dataclass(frozen=True, slots=True)
class UserTurnInput:
    content: str
    attachments: tuple[Attachment, ...] = ()


@dataclass(frozen=True, slots=True)
class LocalContinuationInput:
    pass


RunInput: TypeAlias = str | UserTurnInput | LocalContinuationInput


@dataclass(frozen=True, slots=True)
class RunRequest:
    input: UserTurnInput | LocalContinuationInput
    options: RunOptions = field(default_factory=RunOptions)


WaitReasonKind: TypeAlias = Literal[
    "human_input", "timer", "remote_result", "resource_availability", "retry_backoff"
]


@dataclass(frozen=True, slots=True)
class WaitReason:
    kind: WaitReasonKind
    handle: str
    detail: str | None = None


@dataclass(frozen=True, slots=True)
class AgentResult:
    content: str


@dataclass(frozen=True, slots=True)
class AgentWaiting:
    run_id: str
    reason: WaitReason


RunOutcome: TypeAlias = AgentResult | AgentWaiting
```

`run.py` 在本任务从现有 `stream_events.py` 导入 `RunOptions`，不提前移动该公开类型；移动 `RunOptions` 和新增 `TurnStreamWaiting` 留到 Task 6 原子切换。在 `errors.py` 定义设计规范第 17 节的完整错误层级。这些新类型此时不加入稳定 facade。

```python
# src/agentos/runtime/waiting.py
@dataclass(frozen=True, slots=True)
class WaitingCommit:
    run_id: str
    reason: WaitReason


class WaitingRuntime(Protocol):
    async def commit_waiting(
        self,
        *,
        turn_id: str,
        reason: WaitReason,
    ) -> WaitingCommit: ...
```

`WaitingRuntime` 只是内部 Port，不包含 PostgreSQL/Redis 实现。它的唯一语义是：成功返回代表权威状态已完成 `RUNNING -> WAITING`；抛错时 QueryLoop 不得发出 `TurnStreamWaiting`。`TurnStatus` 属于已治理的公开 `TurnState`，本任务不修改 `turn.py`，状态扩展留到 Task 6 原子切换。

- [ ] **Step 4: 运行目标测试和受影响类型测试**

Run: `python -m pytest tests/runtime/test_run_contracts.py tests/runtime/test_waiting_runtime_contract.py -q`

Expected: PASS。

- [ ] **Step 5: 检查格式并提交私有基础类型**

```powershell
python -m compileall -q src tests
python -m ruff check src tests
git diff --check
git add src/agentos/runtime/run.py src/agentos/runtime/errors.py src/agentos/runtime/waiting.py tests/runtime/test_run_contracts.py tests/runtime/test_waiting_runtime_contract.py
git commit -m "feat(runtime): add run contracts and waiting outcome"
```

### Task 2: 实现 event-loop-neutral 执行租约和 AgentStream 状态机

**Files:**
- Create: `src/agentos/runtime/agent_stream.py`
- Create: `tests/runtime/test_agent_stream.py`
- Create: `tests/runtime/test_agent_stream_races.py`

- [ ] **Step 1: 写入未消费关闭、单消费者和 busy 租约失败测试**

```python
def test_unconsumed_stream_close_releases_lease() -> None:
    async def run() -> None:
        lease = ExecutionLease()
        cleaned = 0

        async def events():
            yield TurnStreamCompleted("done")

        async def cleanup() -> None:
            nonlocal cleaned
            cleaned += 1

        stream = lease.open_stream(events(), cleanup=cleanup)
        await stream.aclose()
        assert cleaned == 1
        replacement = lease.open_stream(events(), cleanup=cleanup)
        await replacement.aclose()

    asyncio.run(run())


def test_active_lease_rejects_second_stream_immediately() -> None:
    async def run() -> None:
        lease = ExecutionLease()

        async def events():
            yield TurnStreamCompleted("done")

        async def cleanup() -> None:
            return None

        first = lease.open_stream(events(), cleanup=cleanup)
        with pytest.raises(AgentBusyError):
            lease.open_stream(events(), cleanup=cleanup)
        await first.aclose()

    asyncio.run(run())
```

- [ ] **Step 2: 写入 `__anext__`/`aclose`、consumer 内联关闭和外部关闭竞态测试**

```python
def test_consumer_can_close_itself_without_self_cancel_or_self_wait() -> None:
    async def run() -> None:
        lease = ExecutionLease()

        async def events():
            yield TurnStreamStarted("hello")
            await asyncio.Event().wait()

        async def cleanup() -> None:
            return None

        stream = lease.open_stream(events(), cleanup=cleanup)
        await stream.__anext__()
        await stream.aclose()
        assert stream.closed

    asyncio.run(run())


def test_external_close_cancels_consumer_and_waits_for_shared_cleanup() -> None:
    async def run() -> None:
        lease = ExecutionLease()
        started = asyncio.Event()
        cleanup_finished = asyncio.Event()

        async def events():
            started.set()
            await asyncio.Event().wait()
            yield TurnStreamCompleted("unreachable")

        async def cleanup() -> None:
            cleanup_finished.set()

        stream = lease.open_stream(events(), cleanup=cleanup)
        consumer = asyncio.create_task(anext(stream))
        await started.wait()
        await stream.aclose()
        assert consumer.cancelled()
        assert cleanup_finished.is_set()

    asyncio.run(run())
```

同时覆盖：`execute/open_stream` 返回前不启动 generator；重复关闭；关闭后迭代结束；关闭后重新进入 context 抛 `AgentStreamClosedError`；第二 Task 消费抛 `AgentStreamConsumerError`；CREATED interrupt 不启动 generator；成功/失败/取消各只清理一次；同一空闲 lease 在两次独立 `asyncio.run()` 的 event loop 中顺序复用，证明门闩不绑定 event loop。

- [ ] **Step 3: 运行测试并确认状态机尚不存在**

Run: `python -m pytest tests/runtime/test_agent_stream.py tests/runtime/test_agent_stream_races.py -q`

Expected: FAIL，错误指向缺少 `ExecutionLease`/`AgentStream`。

- [ ] **Step 4: 实现确定性状态机和共享 cleanup completion**

按以下完整算法实现 `agent_stream.py`，不得自由改变竞态胜负：

1. `_StreamState` 只含 `CREATED/RUNNING/CLOSING/CLOSED`；`ExecutionLease` 用 `threading.Lock` 和一个 `_active: AgentStream | None`，`open_stream()` 在锁内立即检查并设置 active，冲突抛 `AgentBusyError`。
2. `AgentStream.__init__()` 保存 async generator、cleanup callback、pending sync work tracker、release callback；创建时不访问 running loop、不创建 Task、不推进 generator。
3. 第一次 `__anext__()` 在同一 lock 中执行 `CREATED -> RUNNING` 并登记 `asyncio.current_task()` 与 `get_running_loop()`；若已 CLOSING/CLOSED 直接等待共享 cleanup completion 后抛 `StopAsyncIteration`；若 consumer 已登记为另一 Task，抛 `AgentStreamConsumerError`。
4. `__anext__()` 调用 generator `anext()`；收到正常 terminal 或 `StopAsyncIteration` 时由当前 consumer 内联调用 `_finish_close()`；非 cancellation 异常先保存同一异常对象，由 generator 负责产出一次 `TurnStreamFailed`，下一次推进时重抛；`CancelledError` 直接进入 finally cleanup 后原样传播。
5. `aclose()` 在 lock 中原子进入 CLOSING。调用者就是 consumer 时直接内联 `_finish_close()`，绝不 cancel/await 自己；调用者是其他 Task 时，通过登记 loop 的 `call_soon_threadsafe(consumer.cancel)` 取消 consumer，并等待共享 completion；CREATED 状态由 closer 自己接管 cleanup，generator 不得启动。
6. `_finish_close()` 用 `_cleanup_owner` 标志保证只有一个 owner：先 `aclose()` generator，再 await pending sync work tracker 收敛，再 await cleanup callback，最后在 lock 中设置 CLOSED、清空 consumer/loop、调用 lease release，并解析同一个 completion。所有异常路径都执行该顺序，cleanup 异常不得覆盖原始运行异常。
7. `__aenter__()` 只允许 CREATED/RUNNING；CLOSED 抛 `AgentStreamClosedError`。`__aexit__()` 总是 await `aclose()`。`closed` 是只读 property。

实现完成后逐条把上述 7 条对应到 `tests/runtime/test_agent_stream.py` 或 `test_agent_stream_races.py` 的测试名，确保没有依赖 `sleep()` 猜测时序。

- [ ] **Step 5: 运行 Stream 生命周期测试**

Run: `python -m pytest tests/runtime/test_agent_stream.py tests/runtime/test_agent_stream_races.py -q`

Expected: PASS，且 asyncio debug 模式无 pending task 警告。

- [ ] **Step 6: 提交私有 Stream 基础**

```powershell
python -m compileall -q src tests
python -m ruff check src tests
git diff --check
git add src/agentos/runtime/agent_stream.py tests/runtime/test_agent_stream.py tests/runtime/test_agent_stream_races.py
git commit -m "feat(runtime): add deterministic agent stream lifecycle"
```

### Task 3: 准备私有异步 Provider attempt 候选实现

**Files:**
- Create: `src/agentos/runtime/_provider_attempt_async.py`
- Modify: `src/agentos/runtime/provider_attempt_state.py`
- Modify: `src/agentos/runtime/_async_bridge.py`
- Create: `tests/runtime/test_provider_attempt_candidate.py`
- Test: `tests/runtime/test_async_provider_attempt_rebuild.py`
- Test: `tests/runtime/test_provider_attempt_rebuild.py`
- Test: `tests/runtime/test_async_bridge_nested_cancel.py`

- [ ] **Step 1: 把 Provider capability 优先级和 receipt/retry 契约写入私有候选测试**

```python
@pytest.mark.parametrize(
    ("provider", "expected_capability"),
    [
        (NativeAsyncStreamProvider(), "async_stream"),
        (NativeAsyncCompleteProvider(), "async_complete"),
        (SyncStreamProvider(), "sync_stream"),
        (SyncCompleteProvider(), "sync_complete"),
    ],
)
def test_provider_capability_priority(provider, expected_capability) -> None:
    async def run() -> None:
        runner = candidate_runner(provider)
        events = [event async for event in runner.run_stream(None)]
        assert provider.calls == [expected_capability]
        assert isinstance(events[-1], ProviderStreamCompleted)

    asyncio.run(run())
```

保留并合并现有测试：每次 retry 重建请求、before hook 替换请求时不消费原 receipt、可见 delta 后不 retry、缺少 completion 不消费 temporary refs、取消等待 sync bridge 回收。

`NativeAsyncStreamProvider`、`NativeAsyncCompleteProvider`、`SyncStreamProvider`、`SyncCompleteProvider` 和 `candidate_runner()` 必须在 `test_provider_attempt_candidate.py` 中完整定义；实现分别复用现有 `test_async_provider_attempt_rebuild.py` 的 fake response/event 构造，不依赖未声明 fixture。

- [ ] **Step 2: 运行候选测试并确认私有模块尚不存在**

Run: `python -m pytest tests/runtime/test_provider_attempt_candidate.py tests/runtime/test_async_provider_attempt_rebuild.py tests/runtime/test_async_bridge_nested_cancel.py -q`

Expected: FAIL，新增测试只因 `agentos.runtime._provider_attempt_async` 或 `AsyncProviderAttemptCandidate` 尚不存在。

- [ ] **Step 3: 在私有模块实现异步候选 Runner**

把现有 `async_provider_attempt.py` 的 `async_provider_stream_events()` 与 `AsyncProviderAttemptRunner` 完整复制到 `_provider_attempt_async.py`，只做以下机械改名：`async_provider_stream_events -> provider_stream_events`、`AsyncProviderAttemptRunner -> AsyncProviderAttemptCandidate`。保留原有 `ProviderAttemptState`、`_await_cleanup_preserving_cancellation`、`iterate_sync_in_executor`、retry、receipt 和 hook 时序，不改算法。测试文件中的 `candidate_runner(provider)` 按现有 `test_async_provider_attempt_rebuild.py` fixture 方式显式传入 request factory、hooks、receipt consumer、retry policy 和 request id factory。

候选实现只放在 `_provider_attempt_async.py`，不得修改当前同步 `provider_attempt.py`，否则旧正式 QueryLoop 会在原子切换前失效。此提交暂不删除 `async_provider_attempt.py`，也不切换正式 QueryLoop；Task 6 再把已验证候选内容迁入 canonical `provider_attempt.py` 并删除候选文件。共享 attempt state 保持唯一时序。

- [ ] **Step 4: 运行 Provider/bridge 测试并提交**

```powershell
python -m pytest tests/runtime/test_provider_attempt_candidate.py tests/runtime/test_async_provider_attempt_rebuild.py tests/runtime/test_async_bridge_nested_cancel.py tests/runtime/test_query_loop.py -q
python -m compileall -q src tests
python -m ruff check src tests
git diff --check
git add src/agentos/runtime/_provider_attempt_async.py src/agentos/runtime/provider_attempt_state.py src/agentos/runtime/_async_bridge.py tests/runtime/test_provider_attempt_candidate.py tests/runtime/test_async_provider_attempt_rebuild.py tests/runtime/test_async_bridge_nested_cancel.py
git commit -m "feat(runtime): prepare async provider attempt candidate"
```

### Task 4: 准备私有 Level 1 Tool 调度候选

**Files:**
- Create: `src/agentos/runtime/_tool_scheduler.py`
- Create: `tests/runtime/test_tool_scheduler_candidate.py`

- [ ] **Step 1: 写入完整调度算法的确定性失败测试**

```python
def test_candidate_preserves_order_and_fifo_limit() -> None:
    async def run() -> None:
        probe = ConcurrencyProbe()
        scheduler = ToolSchedulerCandidate(max_parallel_calls=2)
        results = await scheduler.execute_batch(
            calls=(_parallel("slow"), _parallel("fast"), _parallel("queued")),
            policy_for=probe.policy_for,
            execute=probe.execute,
        )
        assert [item.tool_call.id for item in results] == ["slow", "fast", "queued"]
        assert probe.start_order == ["slow", "fast", "queued"]
        assert probe.max_concurrency == 2
        assert probe.active_when_started["queued"] == {"slow"}

    asyncio.run(run())


def test_candidate_applies_exclusive_barriers() -> None:
    async def run() -> None:
        probe = ConcurrencyProbe()
        scheduler = ToolSchedulerCandidate(max_parallel_calls=8)
        await scheduler.execute_batch(
            calls=(_parallel("a"), _exclusive("b"), _parallel("c")),
            policy_for=probe.policy_for,
            execute=probe.execute,
        )
        assert probe.overlaps == {"a": set(), "b": set(), "c": set()}

    asyncio.run(run())
```

同一文件还必须覆盖：`max_parallel_calls` 拒绝 `bool/0/负数`；默认策略 EXCLUSIVE；连续 PARALLEL_SAFE 段有界 FIFO；失败取消尚未开始的调用并收敛已提交 Task/Future；取消后不启动新副作用；结果按原 Provider index 排序；批次失败不返回部分成功结果；同步 Future 结束前 cleanup completion 不解析。

`test_tool_scheduler_candidate.py` 在文件内定义 `_parallel/_exclusive` 返回真实 `ProviderToolCall`，并由 `ConcurrencyProbe.policy_for(call)` 按 call id 返回策略；Probe 用 `asyncio.Event` barrier 让 `fast` 先完成、`slow` 保持运行，并记录 start order、`active_when_started`、overlap 和 cancellation，不使用 `sleep()`。这条断言证明空闲槽位出现时 `queued` 立即按 FIFO 补位，而不是等待固定 chunk 全部完成。

- [ ] **Step 2: 运行候选测试并确认私有模块尚不存在**

Run: `python -m pytest tests/runtime/test_tool_scheduler_candidate.py -q`

Expected: FAIL，只因 `agentos.runtime._tool_scheduler.ToolSchedulerCandidate` 尚不存在。

- [ ] **Step 3: 实现不接管正式 Loop 的候选 scheduler**

```python
@dataclass(frozen=True, slots=True)
class ScheduledToolCallResult:
    index: int
    tool_call: ProviderToolCall
    result: ToolExecutionResult


@dataclass(slots=True)
class ToolSchedulerCandidate:
    max_parallel_calls: int = 8

    def __post_init__(self) -> None:
        if isinstance(self.max_parallel_calls, bool) or self.max_parallel_calls < 1:
            raise ValueError("max_parallel_calls must be an integer greater than zero")

    async def execute_batch(
        self,
        *,
        calls: tuple[ProviderToolCall, ...],
        policy_for: Callable[[ProviderToolCall], ToolConcurrencyPolicy],
        execute: Callable[[ProviderToolCall], Awaitable[ToolExecutionResult]],
    ) -> tuple[ScheduledToolCallResult, ...]:
        results: list[ScheduledToolCallResult | None] = [None] * len(calls)

        async def execute_one(index: int) -> ScheduledToolCallResult:
            call = calls[index]
            return ScheduledToolCallResult(index, call, await execute(call))

        async def execute_parallel_segment(segment: list[int]) -> None:
            pending: dict[asyncio.Task[ScheduledToolCallResult], int] = {}
            next_offset = 0

            def fill_available_slots() -> None:
                nonlocal next_offset
                while (
                    len(pending) < self.max_parallel_calls
                    and next_offset < len(segment)
                ):
                    item_index = segment[next_offset]
                    next_offset += 1
                    task = asyncio.create_task(execute_one(item_index))
                    pending[task] = item_index

            fill_available_slots()
            try:
                while pending:
                    done, _ = await asyncio.wait(
                        pending,
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    first_error: BaseException | None = None
                    for task in sorted(done, key=pending.__getitem__):
                        item_index = pending.pop(task)
                        try:
                            results[item_index] = task.result()
                        except BaseException as error:
                            if first_error is None:
                                first_error = error
                    if first_error is not None:
                        for task in pending:
                            task.cancel()
                        await asyncio.gather(*pending, return_exceptions=True)
                        raise first_error
                    fill_available_slots()
            except BaseException:
                for task in pending:
                    task.cancel()
                await asyncio.gather(*pending, return_exceptions=True)
                raise

        index = 0
        while index < len(calls):
            if policy_for(calls[index]) is ToolConcurrencyPolicy.EXCLUSIVE:
                results[index] = await execute_one(index)
                index += 1
                continue

            segment: list[int] = []
            while (
                index < len(calls)
                and policy_for(calls[index]) is ToolConcurrencyPolicy.PARALLEL_SAFE
            ):
                segment.append(index)
                index += 1
            await execute_parallel_segment(segment)

        return tuple(cast(ScheduledToolCallResult, item) for item in results)
```

候选模块内部临时定义私有策略枚举，不修改 `RegisteredTool`、MCP、Builder 或公开导出。Task 6 把验证通过的算法迁入 canonical `tool_scheduler.py`，并在同一原子提交中接通完整公开声明。

- [ ] **Step 4: 运行候选测试和现有双 Loop 回归测试**

Run: `python -m pytest tests/runtime/test_tool_scheduler_candidate.py tests/runtime/test_query_loop.py tests/runtime/test_async_query_loop_native.py -q`

Expected: PASS，现有正式执行路径行为不变。

- [ ] **Step 5: 提交私有候选**

```powershell
python -m compileall -q src tests
python -m ruff check src tests
git diff --check
git add src/agentos/runtime/_tool_scheduler.py tests/runtime/test_tool_scheduler_candidate.py
git commit -m "feat(runtime): prepare bounded tool scheduler candidate"
```

### Task 5: 建立同步 owner thread 私有基础

**Files:**
- Create: `src/agentos/runtime/_sync_host.py`
- Create: `tests/runtime/test_sync_host.py`

- [ ] **Step 1: 写入长生命周期 Runner、重入拒绝和线程安全 submission 测试**

```python
def test_sync_host_reuses_one_owner_thread_and_runner() -> None:
    async def owner_identity() -> tuple[int, int]:
        return threading.get_ident(), id(asyncio.get_running_loop())

    with SyncHost() as host:
        first = host.submit(owner_identity()).result()
        second = host.submit(owner_identity()).result()
    assert first == second


def test_sync_host_rejects_owner_thread_reentry() -> None:
    async def reenter(host: SyncHost) -> None:
        host.submit(asyncio.sleep(0))

    with SyncHost() as host:
        with pytest.raises(SyncAdapterReentryError):
            host.submit(reenter(host)).result()
```

另加：外部已有 running loop 的线程调用同步边界抛 `SyncAdapterEventLoopError`、关闭幂等、关闭后 submission 抛 `SyncAgentClosedError`。

- [ ] **Step 2: 运行测试并确认 host 尚不存在**

Run: `python -m pytest tests/runtime/test_sync_host.py -q`

Expected: FAIL，缺少 `_sync_host.SyncHost`。

- [ ] **Step 3: 实现专用 owner thread、`asyncio.Runner` 和 dispatcher**

按以下算法实现 `SyncHost`：构造时创建 daemon owner thread、`threading.Event` ready/closed 和受锁保护的 closed 状态；owner thread 内只创建一次 `asyncio.Runner`，用 `runner.run(_dispatcher())` 驱动一个 `asyncio.Queue[(Awaitable, concurrent.futures.Future)]`。`submit()` 先拒绝 closed、owner-thread reentry 和调用线程已有 running loop，再通过 `loop.call_soon_threadsafe(queue.put_nowait, item)` 提交；dispatcher 为每个 awaitable `create_task()`，把 result/exception/cancel 原样写入对应 concurrent Future。`close()` 幂等地提交 sentinel，等待 dispatcher 收敛所有已接受 Task，退出 Runner 并 join owner thread。整个类不得调用 `asyncio.run()`、不得构造 Agent/QueryLoop、不得允许两个线程直接驱动 Runner。

- [ ] **Step 4: 运行 host 测试并提交**

```powershell
python -m pytest tests/runtime/test_sync_host.py -q
python -m compileall -q src tests
python -m ruff check src tests
git diff --check
git add src/agentos/runtime/_sync_host.py tests/runtime/test_sync_host.py
git commit -m "feat(runtime): add private synchronous event loop host"
```

### Task 6: 原子切换唯一 QueryLoop 和全部公共消费者

**Files:**
- Modify: `src/agentos/runtime/query_loop.py`
- Modify: `src/agentos/runtime/agent.py`
- Modify: `src/agentos/runtime/provider_attempt.py`
- Modify: `src/agentos/runtime/provider_request_builder.py`
- Modify: `src/agentos/runtime/query_loop_support.py`
- Modify: `src/agentos/runtime/turn.py`
- Modify: `src/agentos/runtime/stream_events.py`
- Modify: `src/agentos/runtime/stream_serializers.py`
- Modify: `src/agentos/runtime/profile.py`
- Modify: `src/agentos/runtime/__init__.py`
- Modify: `src/agentos/builder.py`
- Modify: `src/agentos/__init__.py`
- Create: `src/agentos/providers/json_values.py`
- Modify: `src/agentos/providers/messages.py`
- Modify: `src/agentos/providers/input_serialization.py`
- Modify: `src/agentos/providers/openai.py`
- Modify: `src/agentos/providers/openai_compatible.py`
- Modify: `src/agentos/capabilities/tools.py`
- Modify: `src/agentos/capabilities/router.py`
- Modify: `src/agentos/capabilities/mcp.py`
- Modify: `src/agentos/capabilities/executor.py`
- Create: `src/agentos/runtime/tool_scheduler.py`
- Create: `src/agentos/sync/errors.py`
- Create: `src/agentos/sync/agent.py`
- Create: `src/agentos/sync/stream.py`
- Create: `src/agentos/sync/__init__.py`
- Modify: `src/agentos/channels/http.py`
- Modify: `src/agentos/channels/types.py`
- Modify: `src/agentos/channels/sse.py`
- Modify: `src/agentos/channels/sse_turns.py`
- Modify: `src/agentos/channels/asgi.py`
- Modify: `src/agentos/channels/a2a_server.py`
- Modify: `src/agentos/channels/a2a_operations.py`
- Modify: `src/agentos/channels/session.py`
- Modify: `src/agentos/channels/durable_session.py`
- Modify: `src/agentos/channels/__init__.py`
- Create: `src/agentos/channels/turn_execution.py`
- Create: `src/agentos/channels/a2a_execution.py`
- Modify: `src/agentos/multi/coordinator.py`
- Modify: `src/agentos/multi/continuation.py`
- Modify: `src/agentos/multi/team.py`
- Modify: `src/agentos/multi/__init__.py`
- Create: `src/agentos/multi/sync_agent_registry.py`
- Create: `src/agentos/multi/continuation_runner.py`
- Modify: `src/agentos/observability/instrumented.py`
- Modify: `src/agentos/observability/instrument.py`
- Modify: `src/agentos/observability/__init__.py`
- Create: `src/agentos/observability/query_loop.py`
- Modify: `src/agentos/readiness.py`
- Modify: `src/agentos/capabilities/backend.py`
- Modify: `src/agentos/cli/main.py`
- Modify: `src/agentos/examples/streaming_agent.py`
- Modify: `src/agentos/examples/persistent_agent.py`
- Modify: `src/agentos/examples/mcp_agent.py`
- Modify: `src/agentos/examples/small_openai_agent.py`
- Delete: `src/agentos/runtime/async_query_loop.py`
- Delete: `src/agentos/runtime/async_provider_attempt.py`
- Delete: `src/agentos/runtime/_async_provider_bridge.py`
- Delete: `src/agentos/runtime/_provider_attempt_async.py`
- Delete: `src/agentos/runtime/_tool_scheduler.py`
- Test: `tests/runtime/test_query_loop.py`
- Create: `tests/runtime/test_query_loop_contract.py`
- Create: `tests/runtime/test_agent_api.py`
- Create: `tests/runtime/_query_loop_contract_fixtures.py`
- Test: `tests/runtime/test_agent_stream_api.py`
- Test: `tests/runtime/test_streaming_query_loop.py`
- Test: `tests/runtime/test_query_loop_hooks.py`
- Test: `tests/runtime/test_query_loop_boundaries.py`
- Test: `tests/runtime/test_async_agent_api.py`
- Test: `tests/runtime/test_async_query_loop_native.py`
- Test: `tests/runtime/test_async_provider_attempt_rebuild.py`
- Test: `tests/runtime/test_provider_retry.py`
- Test: `tests/runtime/test_streaming_tool_loop.py`
- Test: `tests/runtime/test_tool_loop.py`
- Test: `tests/runtime/test_session_recovery.py`
- Test: `tests/runtime/test_skill_mcp_tool_loop.py`
- Test: `tests/runtime/test_tool_result_budget.py`
- Test: `tests/runtime/test_stream_serializers.py`
- Test: `tests/runtime/test_agent_builder.py`
- Test: `tests/runtime/test_runtime_profile.py`
- Create: `tests/runtime/test_waiting_query_loop.py`
- Test: `tests/capabilities/test_tools.py`
- Test: `tests/capabilities/test_mcp.py`
- Test: `tests/capabilities/test_execution_backend.py`
- Test: `tests/capabilities/test_tool_handler_async_dispatch.py`
- Test: `tests/providers/test_provider_messages.py`
- Test: `tests/providers/test_adapters.py`
- Test: `tests/providers/test_openai_compatible.py`
- Create: `tests/sync/test_sync_agent.py`
- Create: `tests/sync/test_sync_stream.py`
- Test: `tests/channels/test_http_channel.py`
- Test: `tests/channels/test_sse_channel.py`
- Test: `tests/channels/test_asgi_app.py`
- Test: `tests/channels/test_asgi_app_async.py`
- Test: `tests/channels/test_a2a_server.py`
- Test: `tests/channels/test_a2a_operations.py`
- Test: `tests/channels/test_durable_session_provider.py`
- Test: `tests/multi/test_continuation.py`
- Test: `tests/multi/test_coordinator_dispatch.py`
- Test: `tests/multi/test_coordinator_spawn.py`
- Test: `tests/multi/test_coordination_integration.py`
- Test: `tests/multi/test_team_worker_runner.py`
- Test: `tests/observability/test_query_loop_instrumentation.py`
- Test: `tests/observability/test_instrumented_provider.py`
- Test: `tests/observability/test_structured_logging.py`
- Test: `tests/observability/test_streaming_provider_span.py`
- Test: `tests/attachments/test_turn_scoped_image_lifecycle.py`
- Test: `tests/attachments/test_attachment_runtime.py`
- Test: `tests/examples/test_small_openai_agent.py`
- Test: `tests/architecture/test_module_size_baseline.py`

先建立 `_query_loop_contract_fixtures.py`：迁移并改名现有 `test_async_agent_api.py::build_agent_with_response`、`test_async_query_loop_native.py::_request_builder/_TwoStepProvider` 和 `test_sse_channel.py::RecordingProvider`，统一导出 `make_recording_agent()`、`make_query_loop()`、`make_sse_channel()`、`make_coordinator()`。`make_recording_agent()` 返回 `(Agent, Recorder)`，Recorder 明确定义 `control_flow_runs` 和 `provider_calls` 计数；`make_query_loop()` 返回真实 QueryLoop 并接受可选 `waiting_runtime`。同一文件定义 `RecordingWaitingRuntime`（append `state_committed` 后返回 `WaitingCommit`）、`FailingWaitingRuntime`（抛 `RuntimeError("commit failed")`）、`running_turn()`、`agent_stream_from()` 和 `collect_events()`；所有 helper 都只组装真实 runtime 类型，不 mock QueryLoop 私有方法。下面测试引用的 helper 均来自该文件。

- [ ] **Step 1: 写入唯一公开 Agent/QueryLoop 契约测试**

```python
def test_agent_has_one_run_entry_and_both_modes_share_events() -> None:
    async def run() -> None:
        agent, recorder = make_recording_agent()
        outcome = await agent.run("hello")
        stream = await agent.run("hello", stream=True)
        async with stream:
            streamed = [event async for event in stream]
        assert outcome == AgentResult("answer")
        assert recorder.control_flow_runs == 2
        assert isinstance(streamed[-1], TurnStreamCompleted)
        assert not hasattr(agent, "async_run")
        assert not hasattr(agent, "async_stream")
        assert not hasattr(agent, "stream")

    asyncio.run(run())


def test_query_loop_rejects_concurrent_run_until_stream_cleanup() -> None:
    async def run() -> None:
        loop = make_query_loop()
        stream = await loop.execute(RunRequest(UserTurnInput("one")))
        with pytest.raises(AgentBusyError):
            await loop.execute(RunRequest(UserTurnInput("two")))
        await stream.aclose()
        replacement = await loop.execute(RunRequest(UserTurnInput("three")))
        await replacement.aclose()

    asyncio.run(run())
```

增加 collector protocol tests：缺少 terminal、重复 completed、completed 与 waiting 同时出现均抛 `RunProtocolError`；失败流先给一个 `TurnStreamFailed(error)` 再重抛同一异常；外部 cancellation 不伪造 terminal。

- [ ] **Step 2: 写入 LocalContinuation、WAITING 和 interrupt 契约测试**

```python
def test_local_continuation_requires_pending_notice() -> None:
    async def run() -> None:
        agent, _ = make_recording_agent()
        with pytest.raises(ContinuationUnavailableError):
            await agent.run(LocalContinuationInput())

    asyncio.run(run())


def test_waiting_commit_precedes_event_and_outcome_projection() -> None:
    async def run() -> None:
        order: list[str] = []
        runtime = RecordingWaitingRuntime(order, run_id="run_1")
        loop = make_query_loop(waiting_runtime=runtime)
        event = await loop._commit_waiting(
            turn=running_turn("turn_1"),
            reason=WaitReason("human_input", "approval_1"),
        )
        order.append("event_observed")
        assert order == ["state_committed", "event_observed"]
        outcome = await collect_events(agent_stream_from(event))
        assert outcome == AgentWaiting("run_1", event.reason)

    asyncio.run(run())


def test_waiting_commit_failure_emits_no_event() -> None:
    async def run() -> None:
        loop = make_query_loop(waiting_runtime=FailingWaitingRuntime())
        turn = running_turn("turn_1")
        with pytest.raises(RuntimeError, match="commit failed"):
            await loop._commit_waiting(
                turn=turn,
                reason=WaitReason("human_input", "approval_1"),
            )
        assert turn.status == "running"

    asyncio.run(run())


def test_interrupt_created_stream_prevents_turn_start() -> None:
    async def run() -> None:
        agent, _ = make_recording_agent()
        stream = await agent.run("hello", stream=True)
        assert agent.interrupt() is True
        async with stream:
            assert [event async for event in stream] == []
        assert agent.interrupt() is False

    asyncio.run(run())
```

未配置权威 waiting runtime 时明确测试 `WaitingUnsupportedError`；成功提交后当前 Turn 标记 `waiting`，Stream 清理并释放 lease。`WaitingRuntime.commit_waiting()` 抛错时不得改变 Turn 终态、不得发 waiting event。Port 只接收 `turn_id` 与类型化 `WaitReason`，不访问具体 PostgreSQL/Redis；本阶段不导出 durable command 类型。

- [ ] **Step 3: 写入 async serializer 和 SyncAgent 公共契约测试**

```python
def test_iter_sse_only_projects_existing_stream() -> None:
    async def run() -> None:
        agent, recorder = make_recording_agent()
        stream = await agent.run("hello", stream=True)
        async with stream:
            chunks = [chunk async for chunk in iter_sse(stream)]
        assert chunks[-1].startswith("event: done")
        assert recorder.provider_calls == 1

    asyncio.run(run())


def test_sync_agent_uses_same_agent_and_closes_stream() -> None:
    agent, _ = make_recording_agent()
    with SyncAgent(agent) as sync_agent:
        assert sync_agent.run("hello") == AgentResult("answer")
        with sync_agent.run("hello", stream=True) as stream:
            assert list(stream)[-1] == TurnStreamCompleted("answer")
```

补充：SyncAgent running-loop 拒绝、owner-thread reentry、concurrent busy、关闭幂等、关闭后调用失败、SyncAgentStream 单消费线程、跨线程 close、convenience streaming stream 拥有临时 SyncAgent。

- [ ] **Step 4: 写入 Channel、A2A、Multi-agent 和 Observability 迁移测试**

```python
def test_sse_disconnect_closes_stream_before_session_release() -> None:
    async def run() -> None:
        order = []
        channel = make_sse_channel(order)
        response = channel.stream_turn("hello")
        await anext(response)
        await response.aclose()
        assert order == ["stream_closed", "query_lease_released", "session_released"]

    asyncio.run(run())


def test_coordinator_borrows_shared_sync_agent() -> None:
    agent, _ = make_recording_agent()
    sync_agent = SyncAgent(agent)
    coordinator = make_coordinator()
    coordinator.attach_agent("worker", sync_agent)
    coordinator.detach_agent("worker")
    assert not sync_agent.closed
    sync_agent.close()
```

Observability 测试必须断言 wrapper 返回具体 `AgentStream`，且 close/cancel/WAITING/failure 仍由原 Stream 控制，不降级为裸 async generator。

- [ ] **Step 5: 运行新增契约测试并确认旧 API/旧 Loop 使其失败**

Run: `python -m pytest tests/runtime/test_query_loop_contract.py tests/runtime/test_agent_api.py tests/sync tests/channels/test_http_channel.py tests/channels/test_sse_channel.py tests/multi/test_continuation.py tests/observability/test_query_loop_instrumentation.py -q`

Expected: FAIL，失败集中在旧同步 `QueryLoop`、旧 Agent 方法、缺少 `agentos.sync` 和消费者仍调用旧入口。

- [ ] **Step 6: 将 QueryLoop 改为唯一 async `execute()` 控制流**

按下面的逐函数迁移表实施，不能保留 facade 委托：

| 新函数 | 唯一实现来源与修改 |
|---|---|
| `execute(RunRequest) -> AgentStream` | 新写：校验 request，在返回前通过 `ExecutionLease.open_stream()` 立即占租约；只创建 `_execute_events(request)` generator，不推进它。 |
| `_execute_events(request)` | 新写分派：`UserTurnInput` 调 `_prepare_user_turn()`，`LocalContinuationInput` 调 `_prepare_continuation_turn()`，然后只进入 `_run_provider_tool_events()`；finally 清 attachment/runtime notice/Turn 临时资源。 |
| `_prepare_user_turn()` | 迁移当前同步 `QueryLoop._prepare_user_message()` 与 `run_turn_stream()` 中 start-turn/user-message/hook/event 逻辑，attachments 从 `UserTurnInput.attachments` 读取。 |
| `_prepare_continuation_turn()` | 迁移当前 `AsyncQueryLoop.run_continuation_stream()`；无 notice 抛 `ContinuationUnavailableError`，不追加 user StoredMessage。 |
| `_run_provider_tool_events()` | 迁移当前 `AsyncQueryLoop._run_provider_loop_stream()`；删除所有 `sync_loop` 调用，Provider 使用 canonical async Runner，Tool 批次使用 canonical scheduler。 |
| `_provider_attempt_events()` | 迁移当前 `AsyncQueryLoop._provider_attempt_events()`，request factory/before/after/receipt 全部指向本 QueryLoop 的明确 helper。 |
| `_before/_after_provider_call`、`_before/_after_tool_call`、`_dispatch_hook`、`_event_context` | 从当前同步 `QueryLoop` 移入 `query_loop_support.py` 的有类型 helper 或保留为 QueryLoop 小方法；不得通过另一个 Loop 实例复用。 |
| `_commit_waiting(turn, reason)` | 若无 Port 抛 `WaitingUnsupportedError`；先 await `WaitingRuntime.commit_waiting(turn_id=turn.id, reason=reason)`，成功后 `turn.mark_waiting()` 并返回 `TurnStreamWaiting(commit.run_id, commit.reason)`；Port 异常时不改 Turn、不发事件。 |

把 Task 3 已验证候选实现迁入 canonical `provider_attempt.py`，再删除候选文件。保留 ProviderRequest 每 attempt 重建、Hook/EventBus、receipt、compression、attachment mount 和临时 notice 清理语义。此步骤同时把 `RunOptions` 移入 `runtime/run.py`、增加 `TurnStreamWaiting`、扩展 `TurnStatus`/`TurnState.mark_waiting()` 并更新所有 import，不能保留旧位置 alias。失败控制流必须先 yield 一个 `TurnStreamFailed(error)`，随后在下一次推进时重抛同一对象；外部 cancellation 直接传播。

- [ ] **Step 7: 将 Agent 改为统一 async `run()` facade 和严格 collector**

```python
@overload
async def run(self, input: RunInput, *, stream: Literal[False] = False, options: RunOptions | None = None) -> RunOutcome: ...
@overload
async def run(self, input: RunInput, *, stream: Literal[True], options: RunOptions | None = None) -> AgentStream: ...
@overload
async def run(self, input: RunInput, *, stream: bool, options: RunOptions | None = None) -> RunOutcome | AgentStream: ...

async def run(self, input: RunInput, *, stream: bool = False, options: RunOptions | None = None) -> RunOutcome | AgentStream:
    request = RunRequest(self._normalize_input(input), options or RunOptions())
    events = await self._query_loop.execute(request)
    if stream:
        return events
    return await self._collect_outcome(events)
```

删除 `async_run/stream/async_stream/run_continuation/stream_continuation/stream_jsonl/stream_sse/run_with_callbacks/clear_interrupt/interrupted`。`interrupt()` 返回 bool 并只作用于当前 lease。

- [ ] **Step 8: 写入 Level 1 Tool 公开契约失败测试**

在已列出的 capability/provider/QueryLoop/Builder 测试中先加入以下 RED cases：

- `RegisteredTool` 默认 EXCLUSIVE、显式 PARALLEL_SAFE、原始 nested schema/metadata 修改不影响注册对象、序列化两次不共享容器。
- Router 未知 Tool/Context Tool 保守独占；MCP server-only、tool-only、readOnlyHint-only 均独占，只有 server+tool 双 opt-in 并行。
- Builder 默认 8，拒绝 `True/0/-1` 和重复 `.max_parallel_calls()`。
- OpenAI/Compatible 的 `parallel_tool_calls` 发送/省略能力矩阵。
- QueryLoop duplicate reservation 在启动 Task 前完成；batch 失败不部分追加结果并清 ActiveWindow；requested/result-appended 顺序稳定；取消收敛 Task/Future。

- [ ] **Step 9: 运行 Tool 公开契约测试并确认失败原因**

Run: `python -m pytest tests/runtime/test_streaming_tool_loop.py tests/runtime/test_tool_loop.py tests/runtime/test_tool_result_budget.py tests/capabilities/test_tools.py tests/capabilities/test_mcp.py tests/capabilities/test_execution_backend.py tests/capabilities/test_tool_handler_async_dispatch.py tests/providers/test_provider_messages.py tests/providers/test_adapters.py tests/providers/test_openai_compatible.py tests/runtime/test_agent_builder.py -q`

Expected: FAIL，新增断言分别指向缺少 `ToolConcurrencyPolicy`、递归 freeze/thaw、Router/MCP policy、Builder limit、Provider flag 或 QueryLoop batch semantics；现有无关测试继续通过。

- [ ] **Step 10: 把完整 Level 1 Tool 并发契约接入唯一 Loop**

把 Task 4 候选算法迁入 `runtime/tool_scheduler.py` 并删除候选文件，同时完成以下不可拆分契约：

- `providers/json_values.py` 提供递归 defensive copy/freeze/thaw；`ProviderFunctionSpec` 与 `ProviderRequest.tools` 保持递归不可变，两次序列化返回互不共享的 dict/list。
- `ToolConcurrencyPolicy` 只有 `EXCLUSIVE` 和 `PARALLEL_SAFE`；`RegisteredTool.concurrency_policy` 默认 EXCLUSIVE，metadata 中的非正式布尔值无效。
- `ToolCallRouter.concurrency_policy_for()` 对未知 Tool 和 Context Tool 保守返回 EXCLUSIVE。
- MCP 只有 Server `supports_parallel_tool_calls=true` 与 Tool `parallel_safe=true` 双 opt-in 才映射 PARALLEL_SAFE；`readOnlyHint` 单独存在不授权并发。
- `AgentBuilder.max_parallel_calls(value)` 默认 8，拒绝 `bool`、小于 1 和重复配置；只构造一个 scheduler。
- OpenAI 有工具且能力明确时发送 `parallel_tool_calls`；OpenAI-Compatible 默认省略，只有 adapter capability opt-in 时发送。
- QueryLoop 在调度前串行完成 duplicate reservation；scheduler 内不修改 MessageRuntime、ActiveWindow、Hook 或 EventBus。全部调用成功后再按原 index 串行应用 budget、写 Tool Result 和发 result-appended event。
- 任一调用失败时取消/收敛兄弟 Task/Future，不部分写 Tool Result，移除本批 assistant ref，保持 MessageStore append-only，并传播原始失败；取消后不启动新 Tool。
- started/completed 观测可按实际并发顺序出现，但 requested/result-appended 和 Provider Tool Result 顺序必须保持原 index；结构化观测包含 batch index、policy、queue wait、execution duration、limit 和 batch size。

Run: `python -m pytest tests/runtime/test_tool_scheduler_candidate.py tests/runtime/test_streaming_tool_loop.py tests/runtime/test_tool_loop.py tests/runtime/test_tool_result_budget.py tests/capabilities/test_tools.py tests/capabilities/test_mcp.py tests/capabilities/test_execution_backend.py tests/capabilities/test_tool_handler_async_dispatch.py tests/providers/test_provider_messages.py tests/providers/test_adapters.py tests/providers/test_openai_compatible.py tests/runtime/test_agent_builder.py -q`

Expected: PASS；同步和异步 Tool 都只经唯一 async scheduler，FIFO、独占屏障、MCP 双 opt-in、重复 reservation、batch failure atomicity、ActiveWindow cleanup 和 Future convergence 均有确定性测试。

- [ ] **Step 11: 完成 Builder/Profile、sync adapter 和静态消费者切换**

`AgentBuilder.build()` 只构造 `QueryLoop`；删除 `build_async()` 与 `loop_mode`。`SyncAgent` 使用 Task 5 的 `SyncHost`，`SyncAgentStream` 只跨线程驱动 `AgentStream`，不复制执行逻辑。

`agentos.sync.errors` 必须从 `runtime/errors.py` 重新导出同一组 `Sync*Error` 类对象，不能复制定义出两套不相等的异常类型。

Channel/A2A 改为 async；SSE/ASGI 断连必须先关闭 Stream 再释放 session。`ChannelTurnResult.status` 收窄为 `completed/waiting/failed`，WAITING 使用 HTTP 202 并携带类型化 `run_id` 与 `WaitReason`，不得丢失 reason 或伪装 completed；SSE/JSONL 把 `TurnStreamWaiting` 序列化为 `waiting` event，递归 dataclass payload 必须 JSON-safe。A2A 对 `human_input` 映射现有 `input-required`，其他 wait kind 保持 non-terminal working 状态并保留 wait metadata。把 Stream close/session release 顺序提取到 `channels/turn_execution.py`，把 A2A async task/operation 调度提取到 `channels/a2a_execution.py`，避免继续扩大 `asgi.py` 与 `a2a_operations.py`。Coordinator 接收共享 `SyncAgent`，owned/borrowed 生命周期放入 `multi/sync_agent_registry.py`；`LocalContinuationTrigger` 借用同一 Mapping，静态 continuation 调用放入 `multi/continuation_runner.py`。Distributed async worker 继续持有裸 `Agent` 并直接 await。`InstrumentedQueryLoop` 在本步骤直接提取到 `observability/query_loop.py`，包装并返回原始 `AgentStream`，不在超大 `instrumented.py` 中新增第二套生命周期。

- [ ] **Step 12: 删除双 Loop、双 Runner 和所有动态 fallback**

删除三个旧文件，并用下列命令定位剩余运行时代码引用：

```powershell
rg -n "AsyncQueryLoop|AsyncProviderAttemptRunner|sync_loop|build_async|\.async_run\(|run_turn_stream|run_continuation_stream|clear_interrupt|hasattr\(.*stream|getattr\(.*stream" src tests
```

Expected: 仅尚待本 Task 更新的测试/文档匹配；`src/` 中不得存在旧 Agent/Loop 入口或能力探测 fallback。Provider 的合法 `async_stream()` 和 ExecutionBackend/Workspace 的领域方法不属于删除目标，必须按接收者类型人工分类，不能机械改名。

- [ ] **Step 13: 运行原子切换目标测试和规模目标**

Run: `python -m pytest tests/runtime tests/capabilities tests/attachments tests/channels tests/multi tests/observability tests/examples -q`

Expected: PASS。随后运行 `python -m pytest tests/architecture/test_module_size_baseline.py -q`；`query_loop.py < 500`、`agent.py < 250`、`agent_stream.py < 250`、`provider_attempt.py < 250`，并且 `asgi.py`、`a2a_operations.py`、`multi/team.py`、`coordinator.py`、`observability/instrumented.py` 均不得高于 cutover 前记录的行数。任何失败必须在同一工作区修复后才能提交，不允许把公共切换拆成多个红/绿交替提交。

#### Task 6B: 在同一原子提交内更新 Public API、版本、规模基线和当前文档

> Task 6B 是 Task 6 原子提交的后半段，不是独立任务，不得单独分派或提交。

**Files:**
- Modify: `docs/api-stability.md`
- Modify: `docs/public-api-stability.json`
- Modify: `docs/public-api-inventory.json`
- Modify: `docs/governance/agentos-module-size-baseline.json`
- Create: `docs/migrations/0.2-single-async-query-loop.md`
- Modify: `CHANGELOG.md`
- Modify: `pyproject.toml`
- Modify: `src/agentos/__init__.py`
- Modify: `README.md`
- Modify: `docs/readme-online.md`
- Modify: `docs/production-readiness.md`
- Modify: `docs/release-hardening.md`
- Modify: `docs/agentos-objective-coverage-audit.md`
- Modify: `docs/superpowers/specs/2026-07-11-agentos-level1-parallel-tool-calls-design.md`
- Modify: `docs/superpowers/plans/2026-07-10-agentos-message-provider-boundary-implementation-plan.md`
- Modify: `docs/superpowers/plans/2026-07-10-agentos-context-first-sdk-master-implementation-plan.md`
- Test: `tests/architecture/test_public_api.py`
- Test: `tests/architecture/test_public_api_inventory.py`
- Test: `tests/architecture/test_root_facade_contract.py`
- Test: `tests/docs/test_production_readiness_docs.py`
- Test: `tests/docs/test_objective_coverage_audit_docs.py`

- [ ] **Step 1: 按精确 delta 更新稳定导出，不触碰无关 API**

对 `docs/public-api-stability.json` 和对应 `__all__` 只应用以下 delta：

- `agentos.stable`：删除 `AsyncQueryLoop`；保留当前其余全部条目原分类不变，不新增 sync 类型。
- `agentos.runtime.stable`：删除 `AsyncQueryLoop`；保留当前其余 stable 条目；把现有 experimental 的 `AgentResult`、`RunOptions` 移入 stable；新增 `AgentWaiting`、`RunOutcome`、`RunRequest`、`RunInput`、`UserTurnInput`、`LocalContinuationInput`、`WaitReason`、`TurnStreamWaiting`、`AgentStream`、`iter_jsonl`、`iter_sse`、`AgentRunError`、`AgentBusyError`、`AgentStreamConsumerError`、`AgentStreamClosedError`、`ContinuationUnavailableError`、`WaitingUnsupportedError`、`RunProtocolError`。
- `agentos.runtime.experimental`：只移除已升为 stable 的 `AgentResult` 与 `RunOptions`；其余现有 experimental 条目原样保留。
- 新增 `agentos.sync.stable`：`SyncAgent`、`SyncAgentStream`、`run`、`SyncAdapterEventLoopError`、`SyncAdapterReentryError`、`SyncAgentClosedError`、`SyncStreamConsumerError`；experimental 为空。

测试先保存切换前 policy 集合，断言切换后集合严格等于“原集合 - removals + additions”，从而阻止误删无关 stable/experimental 导出。Root 不导出 sync 类型；不得导出未实现 durable command 类型。

- [ ] **Step 2: 更新迁移表、版本和历史替代标记**

把 `pyproject.toml` 与 `agentos.__version__` 统一改为 `0.2.0a1`。迁移文档逐项列出：`build_async -> build`、`async_run -> await run`、`async_stream/stream -> await run(stream=True)`、continuation 输入、serializer、interrupt 和 Coordinator 的 `SyncAgent` 边界。历史规范只添加“被 2026-07-12 单 Loop 规范替代”的醒目标记，不全文改写历史记录。

- [ ] **Step 3: 生成 inventory 并运行 Public API/文档门禁**

```powershell
python scripts/generate_public_api_inventory.py --policy docs/public-api-stability.json --output docs/public-api-inventory.json
python -m pytest tests/architecture/test_public_api.py tests/architecture/test_public_api_inventory.py tests/architecture/test_root_facade_contract.py tests/docs/test_production_readiness_docs.py tests/docs/test_objective_coverage_audit_docs.py -q
```

Expected: PASS；inventory 中没有 `AsyncQueryLoop` 或被删除的 Agent/Builder 方法，无关现有导出数量与分类保持不变。

- [ ] **Step 4: 执行非历史旧名漂移门禁**

```powershell
rg -n "AsyncQueryLoop|AsyncProviderAttemptRunner|sync_loop|build_async|\.async_run\(|run_turn_stream|run_continuation_stream|clear_interrupt" src tests docs --glob "!docs/superpowers/specs/**" --glob "!docs/superpowers/plans/**" --glob "!docs/migrations/0.2-single-async-query-loop.md"
```

Expected: 无匹配。另执行 `rg -n "Agent.*async_stream|agent\.async_stream|Agent.*async_run|agent\.async_run" README.md docs src tests`，只允许迁移文档“旧 API”列匹配。Provider 的 `async_stream()`、ExecutionBackend/Workspace 的领域 `async_run()` 保留，不得因名字相同误删。

- [ ] **Step 5: 执行原子提交前完整验证**

```powershell
python -m pytest -q
python -m compileall -q src tests
python -m ruff check src tests
python scripts/generate_module_size_baseline.py --root src/agentos --output docs/governance/agentos-module-size-baseline.json
python -m pytest tests/architecture/test_module_size_baseline.py -q
python scripts/generate_public_api_inventory.py --policy docs/public-api-stability.json --output docs/public-api-inventory.json
python -m pytest tests/architecture/test_public_api.py tests/architecture/test_public_api_inventory.py -q
git diff --check
```

Expected: 全部退出码 0；规模 baseline 的 diff 只允许核心拆分/消费者提取带来的下降、新文件记录以及删除文件移除。`asgi.py`、`a2a_operations.py`、`multi/team.py`、`coordinator.py`、`observability/instrumented.py` 的行数不得高于 cutover 前基线；任何上调都必须先拆分，不能记录例外。`git status --short` 只包含 Task 6/6B 列出的切换文件、生成 inventory 和规模 baseline。

- [ ] **Step 6: 创建唯一公共切换提交**

```powershell
git add src tests docs README.md CHANGELOG.md pyproject.toml
git commit -m "refactor(runtime): unify agent execution on async query loop"
```

### Task 7: Spec Compliance Review、Code Quality Review 和最终验证

**Files:**
- Review: `docs/superpowers/specs/2026-07-12-agentos-single-async-query-loop-design.md`
- Review: 本计划列出的全部源文件、测试和文档

- [ ] **Step 1: 执行 Spec Compliance Review**

逐项核对设计规范第 21 节。必须提供文件/测试证据确认：唯一 Loop、唯一 Runner、统一 `Agent.run`、共享事件流、WAITING、Local/Durable 边界、执行租约、AgentStream 竞态、Provider/Tool bridge、Builder/Profile、Channel/A2A/Multi、`agentos.sync`、无旧 alias、原子 public cutover。

- [ ] **Step 2: 执行 Code Quality Review**

重点检查：单文件职责、重复控制流、取消时 pending task/future、锁顺序、自取消/自等待、线程所有权、session release 顺序、错误信息敏感数据、动态 fallback、未关闭 Stream、未处理 `AgentWaiting`、公共类型导出漂移。

- [ ] **Step 3: 修复所有阻断项并运行对应目标测试**

每个修复先补充能复现问题的测试，再最小修改实现。Review 结论必须为 `APPROVED` 或 `APPROVED. No blocking findings.`；correctness、lifecycle 或 public contract 问题必须在本阶段关闭。

- [ ] **Step 4: 执行最终全量验证**

```powershell
python -m pytest -q
python -m compileall -q src tests
python -m ruff check src tests
python -m pytest tests/architecture/test_module_size_baseline.py -q
python scripts/generate_public_api_inventory.py --policy docs/public-api-stability.json --output docs/public-api-inventory.json
python -m pytest tests/architecture/test_public_api.py tests/architecture/test_public_api_inventory.py -q
git diff --check
git status --short
```

Expected: 所有命令退出码 0，inventory 无非预期 diff，工作区干净或只包含明确待提交的 review 修复。

- [ ] **Step 5: 提交 review 修复和验收证据**

```powershell
git add src tests docs
git commit -m "test(runtime): close single query loop review findings"
```

若 Review 无需代码修复，则不创建空提交；在交付说明中记录完整命令及通过结果。

## 完成定义

- 仓库只有一个原生异步 `QueryLoop` 和一个异步 `ProviderAttemptRunner`。
- `Agent` 只有 `await run(input, stream=...)`；非流式只是事件流 collector。
- `AgentStream` 在成功、失败、取消、未消费关闭、外部 close 和 WAITING 路径都确定性释放资源与租约。
- Channel、A2A、Multi-agent、Observability、CLI 和示例均使用静态统一入口。
- 同步用户入口只存在于 `agentos.sync`，不复制 Kernel 控制流。
- 不存在旧 API alias、动态 fallback、`sync_loop` 或未实现 durable 类型的伪导出。
- Public API、版本、CHANGELOG、迁移文档、inventory、规模基线和测试全部同步。
- Spec Compliance Review 与 Code Quality Review 无阻断项，全量门禁通过。
