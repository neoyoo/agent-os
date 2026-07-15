# Agent OS SDK

Agent OS 鏄竴涓?context-first 鐨?Python agent runtime SDK銆傚綋鍓嶅叕寮€瀵煎叆鍖呭悕鏄?`agentos`锛岄」鐩彂甯冨悕鏄?`agent-os`锛岀増鏈负 `0.1.0`銆?

婧愮爜鏉ユ簮锛歔`pyproject.toml`](../pyproject.toml)锛孾`src/agentos/__init__.py`](../src/agentos/__init__.py)锛孾`tests/architecture/test_public_api.py`](../tests/architecture/test_public_api.py)

---

## 1. Core Philosophy

### 1.1 Context-First Architecture

褰撳墠 SDK 鐨勮繍琛岃竟鐣屽洿缁?`ProviderRequest` 缁勭粐锛歚ProviderRequestBuilder` 浠?`ContextRuntime.snapshot()` 璇诲彇鍙覆鏌撶姸鎬侊紝鐢?`ContextRenderer` 鐢熸垚 `system`锛屼粠 `MessageRuntime` materialize active messages锛屽苟闄勫甫 provider tool schemas銆?

榛樿 LLM 鍙 context 鐢?`ContextRenderer` 娓叉煋锛屽寘鍚?`Runtime Contract`銆乣Capability Plane`銆乣Context Management Rules`銆佸彲閫夌殑 `Declared Working State Schema`銆佸彲閫夌殑 `Working State`銆佸彲閫夌殑 `Inherited State`銆乣Compressed History`銆乣Memory Context`锛屼互鍙婁竴娆℃€?`Runtime Notice`銆傜┖ schema 鏃?renderer 浼氱渷鐣?declared schema 鍜?working state sections銆?

婧愮爜鏉ユ簮锛歔`src/agentos/runtime/provider_request_builder.py`](../src/agentos/runtime/provider_request_builder.py)锛孾`src/agentos/context/renderer.py`](../src/agentos/context/renderer.py)锛孾`src/agentos/context/runtime.py`](../src/agentos/context/runtime.py)锛孾`tests/context/test_renderer.py`](../tests/context/test_renderer.py)锛孾`tests/runtime/test_query_loop_boundaries.py`](../tests/runtime/test_query_loop_boundaries.py)

### 1.1.1 LLM-Visible Context

Agent OS 鐨勬牳蹇冧笉鏄妸鏇村鍘嗗彶娑堟伅濉炶繘 prompt锛岃€屾槸鎶?LLM 姣忚疆闇€瑕侀伒瀹堛€佸紩鐢ㄥ拰鍥炶皟鐨勫唴瀹圭粍缁囨垚绋冲畾鐨?context protocol銆傞粯璁ゆ覆鏌撻『搴忓涓嬶細

```text
Runtime Contract
  -> agent 韬唤銆佽涓鸿竟鐣屽拰瀹夊叏绾︽潫

Capability Plane
  -> 鍙敤宸ュ叿銆乧ontext protocol tools銆丮CP server銆乻kills 鐨?LLM 鍙璇存槑

Context Management Rules
  -> working state銆乻chema銆乧hapter銆乺ecall銆乼rust order 鐨勬洿鏂拌鍒?

Declared Working State Schema
  -> 褰撳墠 chapter 鐨勭粨鏋勫寲鐘舵€佸瓧娈碉紱鏈０鏄?schema 鏃剁渷鐣?

Working State
  -> 褰撳墠浠诲姟鐩爣銆佺害鏉熴€佸喅绛栥€佸凡楠岃瘉浜嬪疄銆佸紑鏀鹃棶棰樺拰涓嬩竴姝ワ紱鏈０鏄?schema 鏃剁渷鐣?

Inherited State
  -> 璺?chapter 缁ф壙鐨勭ǔ瀹氱洰鏍囥€佺害鏉熸垨鍐崇瓥锛涙棤缁ф壙鐘舵€佹椂鐪佺暐

Compressed History
  -> 琚帇缂╃殑鍘嗗彶 segment锛屽彧鏆撮湶 handle銆乼opic 鍜屾憳瑕?

Memory Context
  -> 璺?session 妫€绱㈠嚭鐨勯暱鏈熻蹇?

Runtime Notice
  -> 鏈疆涓€娆℃€х郴缁熼€氱煡锛涙秷璐瑰悗娓呴櫎
```

杩欎簺 section 鐨勪紭鍏堢骇涓嶅悓銆傚綋鍓?active messages 鍜屾湰杞姞杞界殑闄勪欢浠ｈ〃鏈€鏂颁簨瀹烇紱compressed history 鍜?memory context 鏄湁鎹熸垨妫€绱㈠緱鍒扮殑涓婁笅鏂囷紱working state 鏄?LLM 褰撳墠缁存姢鐨勪换鍔＄姸鎬侊紝涓嶅簲璇ヨ鐩栨洿鏂扮殑鐢ㄦ埛娑堟伅銆傞粯璁?trust order 鏄細

```text
1. Active messages and currently loaded attachments
2. Inherited state
3. Compressed history
4. Memory context
5. Working state
6. Attachment placeholders / previews
```

瀹屾暣鐨?LLM 鍙涓婁笅鏂囪寖鏂囪 [`docs/design/llm-context-only-example.md`](design/llm-context-only-example.md)銆傝繖涓枃浠跺彧灞曠ず搴旇杩涘叆 provider `system` 鐨勫唴瀹癸紝涓嶅睍绀?SDK 鍐呴儴 runtime metadata銆?

### 1.2 Zero-Dependency Core

`pyproject.toml` 鐨?core `dependencies` 涓虹┖锛汻edis銆丳ostgres銆丵drant銆丱penTelemetry 鍜?async HTTP transport 閮介€氳繃 optional extras 澹版槑銆傚搴?adapter 鍦ㄧ己灏?optional dependency 鏃朵細鍦ㄤ娇鐢ㄧ偣鎶涘嚭娓呮櫚鐨?`RuntimeError`銆?

褰撳墠 extras锛?

| Extra | 渚濊禆 | 褰撳墠鐢ㄩ€?|
|---|---|---|
| `redis` | `redis>=5.0` | `RedisHotSessionStore`銆乣RedisAgentMessageQueue` |
| `postgres` | `psycopg[binary]>=3.1`銆乣psycopg-pool>=3.2` | `PostgresDurableSessionStore`銆乣PostgresAgentRegistryStore`銆乣PostgresTaskStore` |
| `qdrant` | `qdrant-client>=1.9` | `QdrantRecallIndex` |
| `observability` | OpenTelemetry API/SDK/OTLP HTTP exporter | OTel 鍜?Langfuse OTLP tracer |
| `async-http` | `httpx>=0.27` | OpenAI-compatible async transport |
| `production-memory` | redis + qdrant + psycopg | 缁勫悎瀹夎鐢熶骇 memory adapters |

源码来源：[`pyproject.toml`](../pyproject.toml)、[`src/agentos/persistence/redis_session.py`](../src/agentos/persistence/redis_session.py)、[`src/agentos/multi/redis_queue.py`](../src/agentos/multi/redis_queue.py)、[`src/agentos/persistence/postgres.py`](../src/agentos/persistence/postgres.py)、[`src/agentos/registry/postgres.py`](../src/agentos/registry/postgres.py)、[`src/agentos/multi/postgres_tasks.py`](../src/agentos/multi/postgres_tasks.py)、[`src/agentos/recall/qdrant_index.py`](../src/agentos/recall/qdrant_index.py)、[`src/agentos/observability/otel.py`](../src/agentos/observability/otel.py)、[`src/agentos/providers/openai_compatible.py`](../src/agentos/providers/openai_compatible.py)

### 1.3 Protocol Boundaries

椤圭洰鍐呭澶勮竟鐣屼娇鐢?`typing.Protocol`锛歱rovider銆乤sync provider銆乧ontext snapshot provider銆乼ool handler銆丮CP client銆乻ession provider銆乧hannel auth銆乭ot/durable session store銆乺ecall index銆乪mbedding provider銆乻ession persistence銆乺egistry store銆丄2A transport銆乻ubagent factory 鍜?remote task submitter 绛夈€?

源码来源：[`src/agentos/providers/base.py`](../src/agentos/providers/base.py)、[`src/agentos/runtime/provider_request_builder.py`](../src/agentos/runtime/provider_request_builder.py)、[`src/agentos/capabilities/tools.py`](../src/agentos/capabilities/tools.py)、[`src/agentos/capabilities/mcp.py`](../src/agentos/capabilities/mcp.py)、[`src/agentos/channels/session.py`](../src/agentos/channels/session.py)、[`src/agentos/channels/auth.py`](../src/agentos/channels/auth.py)、[`src/agentos/persistence/session_store.py`](../src/agentos/persistence/session_store.py)、[`src/agentos/recall/index.py`](../src/agentos/recall/index.py)、[`src/agentos/persistence/base.py`](../src/agentos/persistence/base.py)、[`src/agentos/multi/coordinator.py`](../src/agentos/multi/coordinator.py)

---

## 2. Architecture Layers

褰撳墠婧愮爜涓殑涓昏鍒嗗眰濡備笅锛?

```text
agentos/
  builder.py                     # AgentBuilder 缁勮榛樿杩愯鏃?
  runtime/                       # Agent facade銆丵ueryLoop銆丳roviderRequestBuilder銆丼ession/Turn
  context/                       # ContextState銆乻chema銆乺enderer銆乸rojection銆丆ontextRuntime
  messages/                      # MessageStore銆丄ctiveWindow銆丮essageRuntime
  compression/                   # CompressionRuntime銆丒victor銆丆ompressor銆丆ompressionIndex
  recall/                        # RecallRuntime
  capabilities/                  # ToolRegistry銆乀oolExecutor銆乀oolCallRouter銆丮CP銆丼kills
  providers/                     # Provider 鍗忚鍜?OpenAI/Anthropic/OpenAI-compatible/Fake adapters
  channels/                      # HTTP銆丼SE銆丄SGI銆丄2A銆乻ession provider銆乤uth
  memory/                        # hot/durable store 鍗忚銆乮n-memory/Redis/Qdrant adapters銆丮emoryRuntime
  persistence/                   # SessionSnapshot銆乵emory/filesystem/sqlite/postgres persistence
  multi/                         # AgentCoordinator銆乀askStore/TaskTable銆丄gentMessageQueue/AgentInbox銆丳ostgres/Redis adapters
  registry/                      # persistent registry銆丳ostgres store銆乺esolver
  events/                        # typed EventBus
  hooks/                         # HookRegistry銆丠ookManager
  observability/                 # capture policy銆乮nstrumentation銆丱Tel/Langfuse helpers
  policies/                      # SecurityPolicy銆丅udgetPolicy
```

婧愮爜鏉ユ簮锛歔`src/agentos`](../src/agentos)锛孾`src/agentos/builder.py`](../src/agentos/builder.py)锛孾`tests/architecture/test_public_api.py`](../tests/architecture/test_public_api.py)

---

## 3. Context Protocol

### 3.1 ProviderRequest Shape

Provider 杈圭晫鎺ユ敹鏍囧噯鍖?`ProviderRequest`锛?

```python
ProviderRequest(
    system=system_envelope.text,
    messages=(
        ProviderInputItem.context_snapshot(snapshot.xml),
        ProviderInputItem.business_user("inspect the project"),
        ProviderInputItem.business_assistant(
            "",
            (
                ProviderToolCall(
                    id="call_1",
                    name="read_file",
                    arguments={"path": "README.md"},
                ),
            ),
        ),
        ProviderInputItem.tool_result("call_1", "file content"),
    ),
    tools=(ProviderToolSpec(...),),
)
```

`ProviderRequestBuilder.build()` 姣忔浠庢潈濞佺姸鎬侀噸鏂扮粍瑁呰姹傘€?`SystemEnvelope`
鍙壙杞藉彲淇℃寚浠わ紝`ContextSnapshot` 鍜屼笟鍔℃秷鎭垎鍒姇褰变负
`ProviderInputItem`銆?`MessageRuntime` 鍙鐞?`StoredMessage` 鐪熷€煎拰 ActiveWindow锛?
涓嶅啀鐢熸垚 Provider DTO銆?

婧愮爜鏉ユ簮锛歔`src/agentos/providers/base.py`](../src/agentos/providers/base.py)锛孾`src/agentos/providers/input.py`](../src/agentos/providers/input.py)锛孾`src/agentos/providers/tool_specs.py`](../src/agentos/providers/tool_specs.py)锛孾`src/agentos/runtime/provider_request_builder.py`](../src/agentos/runtime/provider_request_builder.py)锛孾`src/agentos/runtime/message_projection.py`](../src/agentos/runtime/message_projection.py)锛孾`tests/providers/test_provider_messages.py`](../tests/providers/test_provider_messages.py)

### 3.2 Built-in Context Tools

榛樿 context protocol tools 鐨勫崟涓€鏉ユ簮鏄?`CONTEXT_PROTOCOL_TOOL_DEFINITIONS` 鍜?`context_protocol_tool_specs()`锛?

| Tool | 褰撳墠浣滅敤 |
|---|---|
| `declare_schema` | 澹版槑褰撳墠 chapter 鐨?working state 瀛楁 |
| `update_state` | 鏇存柊涓€涓凡澹版槑 working state 瀛楁 |
| `extend_schema` | 鍦ㄥ凡鏈?schema 涓婅拷鍔犲瓧娈?|
| `start_chapter` | 寮€鍚柊 chapter锛屽苟閲嶇疆 working state |
| `recall_context` | 鎸?handle 鎴?query 鎭㈠鍘嬬缉鏂囨湰/鍘嗗彶锛屽苟浠?tool result 杩斿洖 |
| `load_attachment` | 灏嗗凡涓婁紶闄勪欢鍔犺浇鍒板綋鍓?user turn 鐨勫悗缁?provider request |

`ToolCallRouter.tool_specs()` 浼氭妸杩欎簺 context tools 涓庡閮ㄥ伐鍏枫€乻kill tool銆丮CP tools 涓€璧蜂綔涓?provider tools 鏆撮湶锛沗ToolCallRouter.execute_tool_call()` 浼氭妸 context tools 璺敱鍒?`ContextRuntime`銆乣RecallRuntime` 鎴?`AttachmentRuntime`銆?

婧愮爜鏉ユ簮锛歔`src/agentos/context_protocol.py`](../src/agentos/context_protocol.py)锛孾`src/agentos/capabilities/router.py`](../src/agentos/capabilities/router.py)锛孾`src/agentos/context/runtime.py`](../src/agentos/context/runtime.py)锛孾`src/agentos/recall/runtime.py`](../src/agentos/recall/runtime.py)锛孾`src/agentos/attachments/runtime.py`](../src/agentos/attachments/runtime.py)锛孾`tests/architecture/test_public_api.py`](../tests/architecture/test_public_api.py)

### 3.3 Compression And Recall Flow

`CompressionRuntime.maybe_compress()` 鍦?provider request 鏋勫缓鍓嶈繍琛屻€傚畠璇诲彇 active messages锛屼氦缁?`Evictor` 閫夋嫨瑕佸帇缂╃殑 message ids锛岀敤 compressor 鐢熸垚 `CompressedSegmentPackage`锛屽厛鍐?memory sink锛屽啀鎶?LLM 鍙 segment 杩藉姞鍒?`ContextRuntime`锛岃褰?`CompressionIndex`锛屾渶鍚庝粠 active window 绉婚櫎 refs銆?

`RecallRuntime.recall_context()` 支持两条路径：本地 handle 召回通过 `CompressionIndex` 读取 source refs；配置 `SegmentRepository` 后，handle/query 均可从 Session Store 和 RecallIndex 恢复原始消息。`ToolCallRouter` 将召回内容格式化为 `<recalled-context>` tool result；它不会伪装成新的 user/assistant message，也不会写入 system prompt。

附件使用独立的 `AttachmentRuntime`。首轮上传的图片会作为 user message 的 `ImagePart` 投影给 provider；后续如果模型需要重新查看附件，应调用 `load_attachment(handle="att:...")`。调用后，该附件会持续投影到当前 user turn 的后续 provider request，直到本轮输出最终结果并清理。下一个 turn 如需引用同一附件，模型必须显式重新调用 `load_attachment`。
**MIME types**: `AttachmentRuntime` 浠呮帴鍙?`image/gif`銆乣image/jpeg`銆乣image/png`銆乣image/webp`銆備笂浼?PDF 鎴栧叾浠栫被鍨嬪皢鎶?`AttachmentError("unsupported attachment MIME type")`銆俙FilePart` 绫讳粛淇濈暀锛岀洿鎺ユ瀯閫?provider message 涓嶅彈褰卞搷銆?

婧愮爜鏉ユ簮锛歔`src/agentos/runtime/query_loop.py`](../src/agentos/runtime/query_loop.py)锛孾`src/agentos/compression/runtime.py`](../src/agentos/compression/runtime.py)锛孾`src/agentos/compression/index.py`](../src/agentos/compression/index.py)锛孾`src/agentos/compression/compressor.py`](../src/agentos/compression/compressor.py)锛孾`src/agentos/recall/runtime.py`](../src/agentos/recall/runtime.py)锛孾`src/agentos/messages/runtime.py`](../src/agentos/messages/runtime.py)锛孾`tests/compression/test_runtime.py`](../tests/compression/test_runtime.py)锛孾`tests/recall/test_runtime.py`](../tests/recall/test_runtime.py)

---

## 4. Minimal Agent

### 4.1 鏈€灏忓彲杩愯绀轰緥

涓嶄緷璧栫涓夋柟鏈嶅姟鐨勬渶灏忕ず渚嬪彲浠ヤ娇鐢?`FakeProvider`锛?

```python
import asyncio

from agentos import AgentBuilder
from agentos.providers import FakeProvider


async def main() -> None:
    agent = (
        AgentBuilder()
        .provider(FakeProvider(["Hello from agentos."]))
        .build()
    )
    result = await agent.run("Hello")
    print(result.content)


asyncio.run(main())
```

鐪熷疄 OpenAI-compatible endpoint 鍙娇鐢細

```python
import asyncio

from agentos import AgentBuilder
from agentos.providers.openai_compatible import OpenAICompatibleProvider


async def main() -> None:
    provider = OpenAICompatibleProvider(
        api_key="...",
        base_url="https://api.openai.com/v1",
        model="gpt-4o",
    )
    agent = AgentBuilder().provider(provider).build()
    result = await agent.run("Hello")
    print(result.content)


asyncio.run(main())
```

婧愮爜鏉ユ簮锛歔`src/agentos/builder.py`](../src/agentos/builder.py)锛孾`src/agentos/runtime/agent.py`](../src/agentos/runtime/agent.py)锛孾`src/agentos/providers/fake.py`](../src/agentos/providers/fake.py)锛孾`src/agentos/providers/openai_compatible.py`](../src/agentos/providers/openai_compatible.py)锛孾`tests/runtime/test_agent_builder.py`](../tests/runtime/test_agent_builder.py)锛孾`tests/providers/test_openai_compatible.py`](../tests/providers/test_openai_compatible.py)

### 4.2 AgentBuilder 榛樿瑁呴厤

`AgentBuilder.build()` 鑷冲皯瑕佹眰 `.provider()`銆傞粯璁や細鍒涘缓 `MessageRuntime`銆乣ContextRuntime`銆乣RecallRuntime`銆乣ToolRegistry`銆乣ToolCallRouter`銆乣ContextRenderer` 鍜?`ProviderRequestBuilder`锛屾渶鍚庤繑鍥?`Agent` facade銆俙.tools([...])` 浼氭敞鍐屽閮?`RegisteredTool`锛沗.with_compression()` 浼氬垱寤?`CompressionRuntime`锛岄粯璁ら绠椾负 `max_active_messages=20`銆乣retain_latest_messages=6`銆?

婧愮爜鏉ユ簮锛歔`src/agentos/builder.py`](../src/agentos/builder.py)锛孾`src/agentos/capabilities/tools.py`](../src/agentos/capabilities/tools.py)锛孾`src/agentos/capabilities/registry.py`](../src/agentos/capabilities/registry.py)锛孾`src/agentos/capabilities/router.py`](../src/agentos/capabilities/router.py)

### 4.3 澶栭儴宸ュ叿

澶栭儴宸ュ叿浣跨敤 `RegisteredTool` 澹版槑 name銆乨escription銆丣SON schema parameters 鍜?handler銆俙ToolRegistry` 璐熻矗娉ㄥ唽鍜?provider schema 杈撳嚭锛沗ToolExecutor` 鎵ц handler锛屽苟鍦ㄦ墽琛屽墠搴旂敤 `SecurityPolicy`銆?

```python
from agentos import AgentBuilder
from agentos.capabilities import RegisteredTool
from agentos.providers import FakeProvider

def echo(args: dict[str, object]) -> str:
    return str(args["text"])

agent = (
    AgentBuilder()
    .provider(FakeProvider(["ready"]))
    .tools([
        RegisteredTool(
            name="echo",
            description="Echo text.",
            parameters={
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
            },
            handler=echo,
        )
    ])
    .build()
)
```

婧愮爜鏉ユ簮锛歔`src/agentos/capabilities/tools.py`](../src/agentos/capabilities/tools.py)锛孾`src/agentos/capabilities/registry.py`](../src/agentos/capabilities/registry.py)锛孾`src/agentos/capabilities/executor.py`](../src/agentos/capabilities/executor.py)锛孾`src/agentos/policies/security.py`](../src/agentos/policies/security.py)锛孾`tests/capabilities/test_tools.py`](../tests/capabilities/test_tools.py)

---

## 5. Web Agent

### 5.1 ASGI App

`AsgiAgentApp` 鏄棤妗嗘灦缁戝畾鐨?ASGI HTTP app銆傚畠渚濊禆 `AgentSessionProvider` 閫氳繃 `session_id` 鍙栧洖 agent锛屽苟鏀寔 auth policy銆佽姹備綋澶у皬闄愬埗銆丣SON turn銆丼SE turn銆佹樉寮?interrupt銆丼SE heartbeat銆乭ealth/readiness銆佸彲娉ㄥ叆 rate limiter銆丄SGI lifespan shutdown handlers 鍜屽彲閫?A2A server銆?

```python
from agentos import AsgiAgentApp
from agentos.channels import InMemoryAgentSessionProvider

sessions = InMemoryAgentSessionProvider(agent_factory=make_agent)
app = AsgiAgentApp(sessions=sessions)
```

婧愮爜鏉ユ簮锛歔`src/agentos/channels/asgi.py`](../src/agentos/channels/asgi.py)锛孾`src/agentos/channels/rate_limit.py`](../src/agentos/channels/rate_limit.py)锛孾`src/agentos/channels/session.py`](../src/agentos/channels/session.py)锛孾`src/agentos/channels/auth.py`](../src/agentos/channels/auth.py)锛孾`tests/channels/test_asgi_app.py`](../tests/channels/test_asgi_app.py)锛孾`tests/channels/test_health_endpoint.py`](../tests/channels/test_health_endpoint.py)锛孾`tests/channels/test_rate_limit.py`](../tests/channels/test_rate_limit.py)锛孾`tests/channels/test_session_provider.py`](../tests/channels/test_session_provider.py)

### 5.2 Endpoints

褰撳墠 `AsgiAgentApp` 璺敱锛?

| Method | Path | 琛屼负 |
|---|---|---|
| `GET` | `/health` | 杩斿洖 `{"status": "ok"}` |
| `GET` | `/v1/health` | 杩斿洖 `{"status": "ok"}` |
| `GET` | `/ready` / `/v1/ready` | 杩愯娉ㄥ叆鐨?readiness checks锛屽け璐ユ椂杩斿洖 503 |
| `POST` | `/v1/sessions/{session_id}/turns` | JSON turn锛屽唴閮ㄨ皟鐢?`HttpAgentChannel.handle_turn()` |
| `POST` | `/v1/sessions/{session_id}/turns/stream` | SSE turn锛屾秷璐?`await agent.run(..., stream=True)` 杩斿洖鐨勪簨浠舵祦 |
| `POST` | `/v1/sessions/{session_id}/interrupt` | 璇锋眰涓柇褰撳墠 session 鐨勮繍琛屼腑 turn |
| `POST` | `/a2a/tasks` | 褰撻厤缃?`a2a_server` 鏃跺鐞?inbound A2A task |
| `GET` | `/a2a/health` | 褰撻厤缃?`a2a_server` 鏃惰繑鍥?A2A health |

璇锋眰浣撶敱 `parse_channel_turn_request()` 瑙ｆ瀽锛岃姹?JSON object 涓瓨鍦ㄩ潪绌?`message`锛屽苟鏀寔 `thinking`銆乣show_thinking` 鍜屽彲閫?`max_message_length` 鏍￠獙銆係SE turn 榛樿姣?15 绉掑彂閫佷竴娆?heartbeat comment锛屽彲閫氳繃 `sse_heartbeat_interval_seconds=None` 鎴栭潪姝ｆ暟鍏抽棴銆傞厤缃?`SlidingWindowRateLimiter` 鍚庯紝turn endpoint 浼氭寜 `session_id` 闄愭祦锛岃秴闄愯繑鍥?429 鍜?`Retry-After` header銆?

婧愮爜鏉ユ簮锛歔`src/agentos/channels/asgi.py`](../src/agentos/channels/asgi.py)锛孾`src/agentos/channels/rate_limit.py`](../src/agentos/channels/rate_limit.py)锛孾`src/agentos/channels/http.py`](../src/agentos/channels/http.py)锛孾`src/agentos/channels/sse.py`](../src/agentos/channels/sse.py)锛孾`src/agentos/channels/types.py`](../src/agentos/channels/types.py)锛孾`tests/channels/test_asgi_app.py`](../tests/channels/test_asgi_app.py)锛孾`tests/channels/test_health_endpoint.py`](../tests/channels/test_health_endpoint.py)锛孾`tests/channels/test_rate_limit.py`](../tests/channels/test_rate_limit.py)锛孾`tests/channels/test_turn_request_parser.py`](../tests/channels/test_turn_request_parser.py)

---

## 6. Memory, Persistence, And Production Adapters

### 6.1 Segment Repository

`SegmentRepository` 连接 `SegmentHotStore`、`SegmentDurableStore` 和 `RecallIndex`。记录压缩片段时，它把 refs 写入热点 Store、把完整 package 写入持久 Store，并写入召回索引；按 query 召回时，它先检索候选 segment，再按 handle 恢复并去重原始消息。Episodic/Semantic `MemoryRuntime` 不参与压缩片段持久化。

源码来源：[`src/agentos/recall/segment_repository.py`](../src/agentos/recall/segment_repository.py)、[`src/agentos/recall/store.py`](../src/agentos/recall/store.py)、[`src/agentos/recall/index.py`](../src/agentos/recall/index.py)、[`src/agentos/recall/types.py`](../src/agentos/recall/types.py)、[`tests/recall/test_segment_repository.py`](../tests/recall/test_segment_repository.py)

### 6.2 Implemented Stores And Indexes

| 绫诲瀷 | 褰撳墠瀹炵幇 |
|---|---|
| Hot session store | `InMemoryHotSessionStore`銆乣RedisHotSessionStore` |
| Durable session store | `InMemoryDurableSessionStore`銆乣PostgresDurableSessionStore` |
| Recall index | `InMemoryRecallIndex`銆乣QdrantRecallIndex` |
| Session snapshot persistence | `MemoryPersistence`銆乣FileSystemPersistence`銆乣SQLitePersistence` |
| Agent registry store | `InMemoryAgentRegistryStore`銆乣JsonFileAgentRegistryStore`銆乣PostgresAgentRegistryStore` |
| Multi-agent task store | `TaskTable`銆乣PostgresTaskStore` |
| Multi-agent message queue | `AgentInbox`銆乣RedisAgentMessageQueue` |

源码来源：[`src/agentos/persistence/in_memory_session.py`](../src/agentos/persistence/in_memory_session.py)、[`src/agentos/persistence/redis_session.py`](../src/agentos/persistence/redis_session.py)、[`src/agentos/recall/in_memory_index.py`](../src/agentos/recall/in_memory_index.py)、[`src/agentos/recall/qdrant_index.py`](../src/agentos/recall/qdrant_index.py)、[`src/agentos/persistence/memory.py`](../src/agentos/persistence/memory.py)、[`src/agentos/persistence/filesystem.py`](../src/agentos/persistence/filesystem.py)、[`src/agentos/persistence/sqlite.py`](../src/agentos/persistence/sqlite.py)、[`src/agentos/persistence/postgres.py`](../src/agentos/persistence/postgres.py)

### 6.3 Production Schema Files

浠撳簱鎻愪緵 Postgres memory backend銆丳ostgres agent registry銆丳ostgres multi-agent task store銆丼QLite session persistence 鍜?Qdrant recall collection 鐨勮縼绉?鍒濆鍖栬剼鏈€係QLite persistence 婧愮爜娉ㄦ槑 schema 蹇呴』鐢辫縼绉绘祦绋嬮鍏堝噯澶囷紱Postgres adapters 涔熷彧鎵ц璇诲啓 SQL锛屼笉鍦ㄨ繍琛屾椂鍒涘缓琛ㄣ€?

婧愮爜鏉ユ簮锛歔`docs/migrations/2026-05-07-postgres-memory-backends.sql`](migrations/2026-05-07-postgres-memory-backends.sql)锛孾`docs/migrations/2026-05-07-postgres-agent-registry.sql`](migrations/2026-05-07-postgres-agent-registry.sql)锛孾`docs/migrations/2026-05-16-postgres-multi-agent-tasks.sql`](migrations/2026-05-16-postgres-multi-agent-tasks.sql)锛孾`docs/migrations/2026-05-07-sqlite-session-persistence.sql`](migrations/2026-05-07-sqlite-session-persistence.sql)锛孾`docs/migrations/2026-05-07-qdrant-recall-collection.py`](migrations/2026-05-07-qdrant-recall-collection.py)锛孾`src/agentos/persistence/sqlite.py`](../src/agentos/persistence/sqlite.py)锛孾`tests/architecture/test_public_api.py`](../tests/architecture/test_public_api.py)锛孾`tests/registry/test_remote_registry.py`](../tests/registry/test_remote_registry.py)锛孾`tests/multi/test_postgres_task_store.py`](../tests/multi/test_postgres_task_store.py)

---

## 7. Multi-Agent Coordination

褰撳墠 multi-agent 鏄湰鍦板崗璋冨櫒鍔犲彲閫?A2A remote dispatch銆俙AgentCoordinator` 鏀寔锛?

| 鑳藉姏 | 褰撳墠瀹炵幇 |
|---|---|
| `attach_agent` | 娉ㄥ唽鏈湴 `AgentCard`锛屽垱寤?inbox锛屽苟淇濆瓨鏈湴 agent 瀹炰緥 |
| `spawn` | 鍒涘缓 ephemeral subagent锛岄€氳繃 `SpawnExecutor` 鍦ㄧ嚎绋嬫睜鎵ц |
| `dispatch` | 鎸?capability 浠?registry 閫夋嫨 expert锛沞ndpoint-backed agent 璧?remote task executor |
| `collect_results` | drain inbox锛屽苟娑堣垂 parent 鍙鐨勭粓鎬?task results |
| `cancel` | queued task 鐩存帴鍙栨秷锛況unning task 鍐欏叆 cancel intent锛屽苟 best-effort interrupt 鏈湴鐩爣 agent |
| `execute_expert_envelope` | 鎵ц expert inbox 涓殑 `task_request` envelope |

`TaskStore` 鏄垎甯冨紡浠诲姟 truth source 杈圭晫锛沗TaskTable` 鏄?in-memory adapter锛宍PostgresTaskStore` 鏄?Postgres adapter銆俙AgentMessageQueue` 鏄?delivery / notification 杈圭晫锛沗AgentInbox` 鏄?in-memory adapter锛宍RedisAgentMessageQueue` 鏄?Redis Streams adapter銆俙OutboxReconciler` 浼氳ˉ鍙?Postgres outbox 涓湭鎶曢€掔殑 terminal result notification锛沗RedisAgentMessageQueue.reclaim_pending()` 鏀寔 Redis Streams pending reclaim 鍜?dead-letter锛沗RedisContinuationTrigger` 鏀寔 Redis Pub/Sub continuation 閫氱煡锛屽苟鍙?fallback 鍒?TaskStore polling銆傝繙绋?endpoint-backed agent 浠嶉€氳繃 `RemoteTaskExecutor` 鍜?`A2AAdapter` 鎻愪氦 HTTP JSON task銆?

婧愮爜鏉ユ簮锛歔`src/agentos/multi/coordinator.py`](../src/agentos/multi/coordinator.py)锛孾`src/agentos/multi/task_store.py`](../src/agentos/multi/task_store.py)锛孾`src/agentos/multi/tasks.py`](../src/agentos/multi/tasks.py)锛孾`src/agentos/multi/message_queue.py`](../src/agentos/multi/message_queue.py)锛孾`src/agentos/multi/inbox.py`](../src/agentos/multi/inbox.py)锛孾`src/agentos/multi/postgres_tasks.py`](../src/agentos/multi/postgres_tasks.py)锛孾`src/agentos/multi/redis_queue.py`](../src/agentos/multi/redis_queue.py)锛孾`src/agentos/multi/reconciler.py`](../src/agentos/multi/reconciler.py)锛孾`src/agentos/multi/redis_continuation.py`](../src/agentos/multi/redis_continuation.py)锛孾`src/agentos/multi/spawn.py`](../src/agentos/multi/spawn.py)锛孾`src/agentos/multi/remote.py`](../src/agentos/multi/remote.py)锛孾`src/agentos/multi/registry.py`](../src/agentos/multi/registry.py)锛孾`src/agentos/channels/a2a.py`](../src/agentos/channels/a2a.py)锛孾`tests/multi`](../tests/multi)锛孾`tests/channels/test_a2a_adapter.py`](../tests/channels/test_a2a_adapter.py)

---

## 8. Observability

### 8.1 Events

`events/` 鎻愪緵 typed dataclass events 鍜?observation-only `EventBus`銆俙EventBus.emit()` 浼氳褰?event 骞惰皟鐢?subscribers锛泂ubscriber 寮傚父浼氳褰曞埌 `subscriber_errors`锛屼笉浼氭敼鍙樻墽琛屾祦銆?

婧愮爜鏉ユ簮锛歔`src/agentos/events/types.py`](../src/agentos/events/types.py)锛孾`src/agentos/events/bus.py`](../src/agentos/events/bus.py)锛孾`tests/runtime/test_typed_events.py`](../tests/runtime/test_typed_events.py)

### 8.2 Instrumentation

`instrument_query_loop(loop, config)` 涓嶄慨鏀瑰師濮?loop锛岃€屾槸鐢?wrapper 鍖呰 provider銆乸rovider request builder銆乼ool router 鍜?compression runtime锛屽苟杩斿洖 `InstrumentedQueryLoop`銆俙CapturePolicy` 榛樿鏄?metadata-only锛況edacted/full 妯″紡闇€瑕佹樉寮忛€夋嫨銆?

`ObservabilityConfig.logging_enabled` 榛樿鍏抽棴銆傚紑鍚悗锛宍configure_structured_logger()` 浣跨敤鏍囧噯搴?logging 杈撳嚭 JSON lines锛宍QueryLoop` 璁板綍 `turn_start`銆乣provider_call`銆乣tool_exec` 鍜?`turn_end`銆?

婧愮爜鏉ユ簮锛歔`src/agentos/observability/instrument.py`](../src/agentos/observability/instrument.py)锛孾`src/agentos/observability/instrumented.py`](../src/agentos/observability/instrumented.py)锛孾`src/agentos/observability/config.py`](../src/agentos/observability/config.py)锛孾`src/agentos/observability/logging.py`](../src/agentos/observability/logging.py)锛孾`src/agentos/runtime/query_loop.py`](../src/agentos/runtime/query_loop.py)锛孾`tests/observability/test_query_loop_instrumentation.py`](../tests/observability/test_query_loop_instrumentation.py)锛孾`tests/observability/test_capture_policy.py`](../tests/observability/test_capture_policy.py)锛孾`tests/observability/test_structured_logging.py`](../tests/observability/test_structured_logging.py)

### 8.3 OTel And Langfuse

`create_otel_tracer()` 鍒涘缓 OTLP HTTP tracer锛沗create_langfuse_otel_tracer()` 浣跨敤 Langfuse OTLP endpoint 鍜?Basic Auth headers銆倀race context 鏀寔 incoming header extraction 鍜?outgoing header injection銆?

婧愮爜鏉ユ簮锛歔`src/agentos/observability/otel.py`](../src/agentos/observability/otel.py)锛孾`src/agentos/observability/langfuse.py`](../src/agentos/observability/langfuse.py)锛孾`src/agentos/observability/context.py`](../src/agentos/observability/context.py)锛孾`tests/observability/test_otel_config.py`](../tests/observability/test_otel_config.py)锛孾`tests/observability/test_trace_propagation.py`](../tests/observability/test_trace_propagation.py)

---

## 9. Hooks And Security

`HookManager` 鎵ц `HookRegistry` 涓尮閰嶇殑 hook銆傚綋鍓?hook names 鏄?`before_provider_call`銆乣after_provider_call`銆乣before_tool_call`銆乣after_tool_call`锛沨ook 鍙繑鍥?`allow`銆乣deny` 鎴?`modify`銆俙QueryLoop` 鍦?provider call 鍜?tool call 鍓嶅悗璋冪敤 hook manager銆?

`SecurityPolicy` 鏄伐鍏锋墽琛屽墠鐨勬渶灏忓畨鍏ㄧ瓥鐣ワ細`denied_tools` 浼樺厛锛宍allowed_tools` 鍙€夈€俙ToolCallRouter` 鍜?`ToolExecutor` 閮戒細鍦ㄥ伐鍏锋墽琛屽墠璋冪敤 `ensure_tool_allowed()`銆?

婧愮爜鏉ユ簮锛歔`src/agentos/hooks/base.py`](../src/agentos/hooks/base.py)锛孾`src/agentos/hooks/registry.py`](../src/agentos/hooks/registry.py)锛孾`src/agentos/hooks/manager.py`](../src/agentos/hooks/manager.py)锛孾`src/agentos/runtime/query_loop.py`](../src/agentos/runtime/query_loop.py)锛孾`src/agentos/policies/security.py`](../src/agentos/policies/security.py)锛孾`src/agentos/capabilities/router.py`](../src/agentos/capabilities/router.py)锛孾`src/agentos/capabilities/executor.py`](../src/agentos/capabilities/executor.py)锛孾`tests/hooks/test_runtime.py`](../tests/hooks/test_runtime.py)锛孾`tests/runtime/test_query_loop_hooks.py`](../tests/runtime/test_query_loop_hooks.py)

---

## 10. Quick Reference

### 10.1 Install From This Repo

```bash
pip install -e .
pip install -e ".[redis]"
pip install -e ".[postgres]"
pip install -e ".[qdrant]"
pip install -e ".[observability]"
pip install -e ".[async-http]"
pip install -e ".[production-memory]"
```

婧愮爜鏉ユ簮锛歔`pyproject.toml`](../pyproject.toml)

### 10.2 Key Public Names

Root package exports include `AgentBuilder`, `Agent`, `QueryLoop`, `ProviderRequestBuilder`, `Provider`, `ToolCallRouter`, `HookManager`, channel classes, multi-agent classes, memory adapters, and registry adapters.

婧愮爜鏉ユ簮锛歔`src/agentos/__init__.py`](../src/agentos/__init__.py)锛孾`tests/architecture/test_public_api.py`](../tests/architecture/test_public_api.py)

### 10.3 Verification

褰撳墠椤圭洰娴嬭瘯鍏ュ彛锛?

```bash
uv run pytest -q
```

婧愮爜鏉ユ簮锛歔`pyproject.toml`](../pyproject.toml)锛孾`README.md`](../README.md)


