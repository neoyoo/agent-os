---
name: agentos v3 sdk architecture
description: agentos v3 閲嶅啓鐗?SDK 宸ョ▼楠ㄦ灦銆備互 context protocol 浣滀负璁ょ煡妯″瀷锛屼互 ai-knowledge 姒傚康妯″潡浣滀负 SDK 杩愯鏃剁粨鏋勩€?
type: architecture
status: inbox
date: 2026-05-03
relates_to:
  - ideas/2026-05-02-neoagent-context-protocol-v3.md
  - ideas/2026-05-03-neoagent-llm-context-only-example.md
  - wiki/_index.md
  - docs/superpowers/specs/2026-04-14-neoagent-sdk-skill-design.md
---

# agentos v3 SDK Architecture

## 1. 椤跺眰鍘熷垯

agentos v3 鐨勯噸鍐欎笉浠ユ棫 neoagent 鍖呯粨鏋勪负鏋舵瀯绾︽潫锛岃€屼互涓や釜涓婂眰璁捐浣滀负杈圭晫锛?

```text
Context protocol 鍐冲畾 agent 鐨勮鐭ユā鍨嬨€?
ai-knowledge 妯″潡浣撶郴鍐冲畾 SDK 鐨勫伐绋嬮鏋躲€?
鏃?neoagent 浠ｇ爜鍙綔涓哄眬閮ㄥ疄鐜板弬鑰冿紝涓嶄綔涓烘灦鏋勭害鏉熴€?
```

杩欐剰鍛崇潃锛?

- LLM 姣忚疆鐪嬪埌浠€涔堛€佹€庝箞缁存姢 working state銆佹€庝箞鍘嬬缉鍜屽彫鍥烇紝鐢?context protocol 鍐冲畾銆?
- SDK 鏈夊摢浜涜繍琛屾椂妯″潡銆佹ā鍧椾箣闂存€庝箞鍗忎綔锛岀敱 ai-knowledge 鐨勬蹇靛湴鍥惧喅瀹氥€?
- 鏃т唬鐮佷腑鍙鐢ㄧ殑 provider銆乼ool銆丮CP銆侀厤缃€佹祴璇曠粡楠屽彲浠ユ惉锛屼絾鏃?prompt / working memory / compression 鎶借薄涓嶈兘鐩存帴鎼€?

---

## 2. ai-knowledge 鈫?agentos v3 妯″潡鏄犲皠

| ai-knowledge 姒傚康 | v3 妯″潡 | 璐ｄ换 |
|---|---|---|
| `query-loop` | `runtime/query_loop.py` | Agent 涓诲惊鐜拰 turn 璋冨害 |
| `runtime-state` | `runtime/session.py`, `runtime/turn.py` | session銆乼urn銆佽繍琛岀姸鎬佺敓鍛藉懆鏈?|
| `context-management` | `context/`, `messages/`, `compression/`, `recall/` | 涓婁笅鏂囨姇褰便€佺獥鍙ｃ€佸帇缂┿€佹仮澶?|
| `prompt-system` | `context/renderer.py`, `context/projection.py` | 娓叉煋 LLM 鍙 context锛屼笉鍋氭棫寮?PromptBuilder 鎷兼帴 |
| `tool-system` | `capabilities/tools.py`, `capabilities/executor.py` | 宸ュ叿娉ㄥ唽銆佹潈闄愩€佹墽琛屻€佺粨鏋滃洖鍐?|
| `memory-system` | `memory/` | 璺?session memory 鐨勬彁鍙栥€佸瓨鍌ㄣ€佸彫鍥?|
| `mcp-skills` | `capabilities/mcp.py`, `capabilities/skills.py` | MCP 杩炴帴鍜?skill 鍔犺浇 |
| `multi-agent` | `multi/` | subagent 娲惧彂銆侀殧绂汇€佺粨鏋滃洖鏀?|
| `agent-registry-discovery` | `registry/`, `multi/registry.py`, `channels/` | AgentCard銆佽兘鍔涘彂鐜般€佽繙绋?agent 瀵诲潃 |
| `hooks` | `events/`, `hooks/` | 瑙傚療鍨嬩簨浠跺拰鍙嫤鎴?hook 鎵╁睍鐐?|
| `session-recovery` | `persistence/`, `runtime/session.py` | 鏂偣鎭㈠鍜屾寔涔呭寲 |
| `evaluation-observability` | `observability/`, `eval/` | 浜嬩欢鏃ュ織銆乼race銆丩angfuse/OTel銆佽瘎浼?|
| `finetuning-system` | `finetuning/`, `eval/`, `observability/` | 璁粌鏁版嵁瀵煎嚭銆乸rompt 浼樺寲銆佸井璋冭瘎浼扮閬?|
| `channel-remote` | `channels/` | CLI / HTTP / 鏈嶅姟鍖栧叆鍙?|
| `sandbox-isolation` | `policies/security.py`, `capabilities/executor.py` | 宸ュ叿鏉冮檺鍜屾墽琛岄殧绂?|

`capabilities/skills.py` 鐨勫唴瀹规潵婧愯竟鐣屾槸 async `SkillContentSource`銆?
`SkillRegistry.aload(...)` 鍙湪鍚姩闃舵鍔犺浇 metadata锛宍load_skill` 鍜?
`load_skill_resource` 浣滀负 async tool handler 鎵ц锛屼緵 Redis銆丠TTP 鎴?
filesystem-backed source 鍦ㄤ笉闃诲 async query loop 鐨勬儏鍐典笅鎸夐渶鍔犺浇姝ｆ枃鍜岃祫婧愩€?

---

## 3. 鐩爣鍖呯粨鏋?

```text
agentos/
  runtime/
    agent.py
    query_loop.py
    provider_request_builder.py
    session.py
    turn.py

  hooks/
    base.py
    registry.py
    manager.py

  events/
    bus.py
    types.py

  context/
    state.py
    schema.py
    renderer.py
    projection.py
    runtime.py
    chapter.py

  messages/
    types.py
    store.py
    window.py
    runtime.py

  compression/
    evictor.py
    compressor.py
    index.py
    runtime.py

  recall/
    runtime.py

  capabilities/
    registry.py
    tools.py
    executor.py
    router.py
    context_tools.py
    skills.py
    mcp.py

  providers/
    base.py
    openai.py
    anthropic.py
    stream.py

  memory/
    extractor.py
    retriever.py
    store.py
    runtime.py

  observability/
    events.py
    traces.py
    langfuse.py
    otel.py

  persistence/
    base.py
    memory.py
    sqlite.py
    filesystem.py

  policies/
    budget.py
    security.py
    tool_policy.py

  multi/
    orchestrator.py
    worker.py
    result.py
    registry.py

  registry/
    agent_card.py
    discovery.py
    in_memory.py
    resolver.py

  channels/
    base.py
    cli.py
    http.py

  eval/
    runner.py
    cases.py
    metrics.py

  finetuning/
    dataset.py
    exporter.py
    prompt_optimizer.py
    runner.py
```

---

## 4. 鍛藉悕瑙勫垯

鍛藉悕蹇呴』琛ㄨ揪瀵硅薄鑱岃矗锛岃€屼笉鏄彧琛ㄨ揪瀹冣€滃睘浜?runtime鈥濄€傛棫 `neoagent` 鐨勬竻鏅板懡鍚嶅彲浠ヤ綔涓洪鏍煎弬鑰冿紝浣嗕笉鑳芥妸鏃?prompt / working memory / compression 鏋舵瀯甯﹀洖 v3銆?

鍏叡鍛藉悕瑙勫垯锛?

- Python import 鍖呭悕缁熶竴涓?`agentos`锛岄伒寰?PEP 8 lowercase package naming銆?
- 瀵瑰绀轰緥銆佹祴璇曞拰鏂囨。涓殑 import 閮戒娇鐢?`agentos`銆?
- 绫诲拰鍑芥暟缁存姢涓枃 docstring锛涘崗璁爣璇嗙銆乸rovider/tool/schema 鍚嶇О淇濈暀鑻辨枃銆?
- `Runtime` 鍙敤浜庨暱鏈熸寔鏈夊瓙绯荤粺鐘舵€佸苟鍗忚皟璇ラ鍩熺敓鍛藉懆鏈熺殑瀵硅薄锛屼緥濡?`ContextRuntime`銆乣MessageRuntime`銆乣CompressionRuntime`銆乣RecallRuntime`銆?

鑱岃矗鍚庣紑瑙勫垯锛?

| 鍚庣紑 | 璇箟 | 绀轰緥 |
|---|---|---|
| `Loop` | agent 涓诲惊鐜拰 turn 鐘舵€佹満 | `QueryLoop` |
| `Builder` | 缁勮鍊硷紝涓嶆寔鏈夌敓鍛藉懆鏈熺姸鎬?| `ProviderRequestBuilder` |
| `Provider` | 妯″瀷鍚庣杈圭晫 | `Provider`, `OpenAICompatibleProvider` |
| `Registry` | 鍚嶇О鍒板璞?schema 鐨勬敞鍐岃〃 | `ToolRegistry` |
| `Executor` | 鎵ц鍏蜂綋鍓綔鐢?| `ToolExecutor` |
| `Router` | 鍒嗗彂璇锋眰鍒版纭墽琛岃矾寰?| `ToolCallRouter` |
| `Manager` | 绠＄悊鍙嫤鎴瓥鐣ユ垨鍗忚皟瑙勫垯 | `HookManager` |
| `Bus` | 瑙傚療鍨?pub/sub锛屼笉鏀瑰彉鎵ц | `EventBus` |
| `Event` | 宸插彂鐢熶簨瀹炵殑绫诲瀷鍖栦簨浠?| `ProviderRequestBuiltEvent` |

褰撳墠鏍囧噯鍛藉悕锛?

| 鑱岃矗 | 鏍囧噯鍚?| 鍘熷洜 |
|---|---|---|
| query/turn 鎵ц寰幆 | `QueryLoop` | 瀵归綈 ai-knowledge 鐨?`query-loop`銆?|
| provider request 缁勮 | `ProviderRequestBuilder` | 鏄庣‘鍙瀯寤?provider request銆?|
| 妯″瀷鍚庣鍗忚 | `Provider` | provider 鏄ā鍨嬪悗绔竟鐣岋紝涓嶆嫢鏈?SDK runtime銆?|
| provider tool call 鍒嗗彂 | `ToolCallRouter` | 鎶?tool call 鍒嗗彂鍒?context tool 鎴栧閮ㄥ伐鍏枫€?|
| hook 绛栫暐鍗忚皟 | `HookManager` | hook 鏄彲鎷︽埅绛栫暐绠＄悊锛屼笉鏄繍琛屾椂涓诲惊鐜€?|
| runtime 鐢熷懡鍛ㄦ湡浜嬪疄 | typed `*Event` dataclass | 鍩虹浜嬩欢蹇呴』鍙彂鐜般€佸彲璁㈤槄銆佸彲娴嬭瘯銆?|

Event 涓?Hook 蹇呴』鍒嗗紑锛?

- `EventBus` 鍙彂甯冭瀵熷瀷 typed events锛宧andler 涓嶆敼鍙樻墽琛岀粨鏋溿€?
- `HookManager` 绠?pre/post hook锛屽彲杩斿洖 allow / deny / modify銆?
- trace / observability 涓嶈濉炶繘 hook锛涢渶瑕佺湅瀹屾暣 LLM 涓婁笅鏂囨椂锛屼紭鍏堝湪 provider 杈圭晫鎴?typed event subscriber 涓婂疄鐜般€?

---

## 5. 鏍稿績鏁版嵁娴?

```text
User input
  鈫?
MessageRuntime.append_user()
  鈫?
QueryLoop
  鈫?
ContextRuntime.prepare_for_request()
  鈹溾攢 apply pending context state
  鈹溾攢 maybe compress active messages
  鈹溾攢 maybe inject recalled context
  鈹斺攢 render LLM-visible context
  鈫?
ProviderRequestBuilder.build()
  鈹溾攢 system = ContextRuntime.render()
  鈹溾攢 messages = MessageRuntime.materialize_active()
  鈹斺攢 tools = ToolRegistry.provider_tool_specs()
  鈫?
Provider.complete()
  鈫?
MessageRuntime.append_assistant()
  鈫?
ToolCallRouter.execute(tool_calls)
  鈹溾攢 ContextToolExecutor 鈫?ContextRuntime
  鈹溾攢 ToolExecutor 鈫?files/db/shell/http
  鈹斺攢 Skill/MCP executors
  鈫?
MessageRuntime.append_tool_results()
  鈫?
loop until assistant final response
```

`QueryLoop` 鍙仛璋冨害锛屼笉鐩存帴鎷?prompt銆佷笉鐩存帴鏀?context銆佷笉鐩存帴鎵ц鍏蜂綋宸ュ叿銆?

---

## 6. Context Runtime

Context Runtime 鏄?agent 鐨勮鐭ユā鍨嬪疄鐜般€?

### 6.1 璐ｄ换

- 淇濆瓨 `ContextState`銆?
- 绠＄悊 `WorkingStateSchema` 鍜?`WorkingState`銆?
- 鎵ц context protocol tools銆?
- 娓叉煋 LLM 鍙涓婁笅鏂囥€?
- 绠＄悊 compressed history銆乮nherited state銆乵emory context 鐨?projection銆?
- 榛樿涓嶅悜 prompt 鏆撮湶 runtime metadata銆?

### 6.2 鍏抽敭瀵硅薄

```text
ContextState
WorkingStateSchema
WorkingState
WorkingStateField
InheritedState
CompressedSegment
MemoryContext
ContextProjection
```

### 6.3 榛樿鍙 context sections

涓?`agentos LLM 鍙涓婁笅鏂囪寖鏂嘸 瀵归綈锛?

```text
Runtime Contract
Capability Plane
Context Management Rules
Declared Working State Schema
Working State
Compressed History
Memory Context
```

璺?chapter 鍦烘櫙鍙互鍦?`Working State` 鍜?`Compressed History` 涔嬮棿棰濆娓叉煋 `Inherited State`銆傛棤 inherited state 鏃朵笉娓叉煋璇ユ锛屼繚鎸侀粯璁や竷娈电粨鏋勩€?

### 6.4 Context tools

```text
declare_schema
update_state
extend_schema
start_chapter
recall_context
load_attachment
```

缁熶竴浣跨敤 `recall_context` 鍛藉悕銆傚畠鍙洖鐨勬槸鍘嬬缉鏂囨湰/鍘嗗彶瀵瑰簲鐨勫師濮嬫秷鎭墖娈碉紝涓嶆槸鏌愪釜鍥哄畾 turn锛涙棫璁捐绗旇涓殑 `recall_turn` 搴旇涓鸿鍙栦唬鐨勬棫绉般€傚彫鍥炲唴瀹圭敱 `ToolCallRouter` 鏍煎紡鍖栦负鏍囧噯 tool result锛屼笉浣滀负 system prompt 鎴栦复鏃?user/assistant message 娉ㄥ叆銆?

附件使用独立的 `load_attachment(handle="att:...")` 工具重新加载。`AttachmentRuntime` 当前稳定面向 image 投影；加载后附件会持续进入当前 user turn 的后续 provider request，直到本轮输出最终结果并清理。下一个 turn 如需再次引用同一附件，模型必须重新调用 `load_attachment`。
`read_state`銆乣abort_chapter`銆乣mark_important` 涓嶄綔涓洪粯璁?LLM 鍙宸ュ叿锛屽彲浣滀负 debug/ops 鑳藉姏鍚庣画娣诲姞銆?

### 6.5 涓嶅仛浠€涔?

- 涓嶅瓨鍌?provider 鍘熷 messages銆?
- 涓嶆墽琛屽閮ㄥ伐鍏枫€?
- 涓嶇洿鎺ョ鐞?session 鎸佷箙鍖栥€?
- 涓嶆妸 `session_id`銆乣trace_id`銆乣message_id`銆乣compression_id` 绛?runtime metadata 娓叉煋杩涢粯璁?prompt銆?

---

## 7. Message Runtime

Message Runtime 鏄?messages 鐪熷€兼簮鍜?active window 绠＄悊鍣ㄣ€?

### 7.1 璐ｄ换

- append-only 淇濆瓨鍘熷 messages銆?
- 缁存姢 `ActiveWindow`銆?
- 淇濇姢 `tool_use` / `tool_result` 閰嶅銆?
- 缁?provider request materialize active messages銆?

### 7.2 鍏抽敭瀵硅薄

```text
Message
MessageRef
MessageStore
ActiveWindow
TemporaryMessage
```

### 7.3 鍘嬬缉杈圭晫

鍘嬬缉鍙粠 `ActiveWindow` 绉婚櫎 message refs锛屼笉鍒犻櫎 `MessageStore` 鍘熸枃銆?

---

## 8. Compression + Recall

Compression 鍜?Recall 鏄?Message Runtime 涓?Context Runtime 鐨勬ˉ銆?

### 8.1 鍘嬬缉娴佺▼

```text
BudgetPolicy detects overflow
  鈫?
Evictor selects contiguous message refs
  鈫?
Compressor reads original messages from MessageStore
  鈫?
CompressedSegment is created
  鈫?
CompressionIndex maps seg handle to source message refs
  鈫?
ActiveWindow removes selected refs
  鈫?
ContextState.M3 appends segment
```

### 8.2 Recall 娴佺▼

```text
LLM calls recall_context(handle="seg_1")
  鈫?
RecallRuntime looks up CompressionIndex
  鈫?
MessageStore returns source messages
  鈫?
ToolCallRouter formats messages into a <recalled-context> tool result
  鈫?
MessageRuntime appends the tool result to the normal message sequence
```

### 8.3 Compressor 绫诲瀷

```text
RuleBasedCompressor      # 娴嬭瘯銆乫allback銆佺‘瀹氭€ф憳瑕?
LLMCompressor            # 鐪熷疄鎽樿
```

---

## 9. Capability Plane + Tool Routing

Capability Plane 缁熶竴澹版槑 tools銆乻kills銆丮CP锛沗ToolCallRouter` 璐熻矗鎶?provider tool calls 璺敱鍒?context tools銆佸閮?tools銆乻kills 鎴?MCP銆?

### 9.1 璐ｄ换

- 娉ㄥ唽宸ュ叿銆?
- 鏆撮湶 provider tool schemas銆?
- 涓?ContextRenderer 鎻愪緵 capability registry 鐨?LLM 鍙鎽樿鎶曞奖锛涢粯璁?prompt 鍙睍绀哄伐鍏峰垎缁勩€丮CP server 鎽樿鍜?skill frontmatter/when-to-use锛屼笉灞曠ず瀹屾暣 input schema銆?
- 閫氳繃 `ToolCallRouter` 璺敱 tool calls銆?
- 閫氳繃 `ToolExecutor` 鎵ц澶栭儴宸ュ叿銆?
- 搴旂敤 tool policy 鍜?security policy銆?
- 灏?tool results 鍐欏洖 Message Runtime銆?
- 灏?context tool calls 璺敱鍒?Context Runtime銆?

### 9.2 宸ュ叿鍒嗙被

```text
Context tools      # declare_schema / update_state / extend_schema / start_chapter / recall_context / load_attachment
Builtin tools      # read_file / edit_file / run_shell / ask_user 绛?
MCP tools          # 鏉ヨ嚜 MCP server
Skill tool         # 鍔犺浇 skill 鎸囦护
Subagent tool      # 娲惧彂瀛?agent
```

### 9.3 Skills

Skills 灞炰簬 capability plane锛屼笉灞炰簬 context projection銆?

鍙傝€?Claude Code 鐨勬柟鍚戯細

- system 涓彧鍒?skill 鎽樿銆?
- 閫氳繃 `Skill` tool 鍔犺浇鍏蜂綋 skill銆?
- 鍔犺浇鍚庣殑 skill 鍐呭浣滀负 meta message 娉ㄥ叆銆?

---

## 10. Provider Boundary

`Provider` 鏄ā鍨嬪悗绔竟鐣岋紝涓嶇煡閬?context 鍐呴儴缁嗚妭銆?

### 10.1 杈撳叆

```text
ProviderRequest
  system: rendered context
  messages: active messages
  tools: provider tool schemas
```

### 10.2 璐ｄ换

- 閫傞厤 OpenAI / Anthropic 绛?provider銆?
- 澶勭悊 tool call schema銆?
- 澶勭悊 streaming銆?
- 杩斿洖鏍囧噯鍖?`ProviderResponse`銆?
- 涓婃姤 usage 缁?Observability銆?

---

## 11. Observability

Observability 鏄?runtime metadata 鐨勫綊瀹匡紝涓嶆薄鏌?prompt銆?

### 11.1 璁板綍鍐呭

```text
session_id
turn_id
message_id
trace_id
span_id
tool_call_id
schema_id
projection_id
compression_id
recall events
budget events
provider usage
tool execution events
```

### 11.2 杈撳嚭

```text
Internal event log
Langfuse adapter
OTel adapter
Debug projection
Eval traces
```

---

## 12. Persistence

Persistence 缁?runtime 鎻愪緵鍙浛鎹㈠瓨鍌ㄣ€?

### 12.1 绗竴鎵瑰疄鐜?

```text
MemoryPersistence
SQLitePersistence
FileSystemPersistence
```

### 12.2 瀛樺偍瀵硅薄

```text
sessions
turns
messages
context state
compressed segments
compression indexes
memory facts
observability events
```

---

## 13. Multi-Agent

Multi-Agent 鏄嫭绔嬭兘鍔涳紝涓嶅叡浜富 agent 鐨?context state銆?

### 13.1 鍘熷垯

- 姣忎釜 subagent 鏈夌嫭绔?`ContextRuntime`銆乣MessageRuntime`銆乣ToolCallRouter`銆?
- 涓?agent 鍙湅鍒?subagent 鐨?tool result銆?
- 涓?agent 鎯冲惛鏀?subagent 鍙戠幇锛屽繀椤绘樉寮?`update_state`銆?
- Subagent 鏉冮檺涓嶈兘瓒呰繃鐖?agent銆?

### 13.2 涓嶅仛浠€涔?

- 涓嶅厑璁?subagent 鐩存帴璇诲啓涓?agent working state銆?
- 涓嶉粯璁ゅ叡浜?active messages銆?
- 涓嶉粯璁ょ户鎵垮叏閮ㄥ伐鍏枫€?

---

## 14. Agent Registry + Finetuning Extensions

杩欎袱涓ā鍧楁潵鑷?ai-knowledge 鐨勫畬鏁?L1 姒傚康瑕嗙洊锛屼絾涓嶈繘鍏ユ棭鏈熶笂涓嬫枃涓婚摼銆?

### 14.1 Agent Registry + Discovery

Agent Registry 璁?agent 鎴愪负鍙鍧€銆佸彲鍙戠幇銆佸彲鎸夎兘鍔涘尮閰嶇殑瀹炰綋銆?

鏃╂湡瀹炵幇淇濇寔杞婚噺锛?

```text
AgentCard          # name / description / capabilities / version / endpoint
AgentRegistry      # register / unregister / resolve / list
InMemoryRegistry   # 鍗曡繘绋嬫祴璇曚笌鏈湴寮€鍙?
```

鍚庣画杩滅▼閮ㄧ讲鍐嶆墿灞曪細

```text
StaticResolver     # 鏂囦欢鎴?well-known URL
ServiceResolver    # k8s / Nacos / 鑷缓 registry
A2AAdapter         # 璺ㄨ繘绋?agent 璋冪敤鍗忚閫傞厤
```

Registry 涓?`multi/` 鐨勮竟鐣岋細

- `registry/` 璐熻矗澹版槑鍜屽彂鐜?agent銆?
- `multi/` 璐熻矗璋冨害 subagent銆佹潈闄愰檷绾у拰缁撴灉鍥炴敹銆?
- `channels/` 璐熻矗鎶婅繙绋?agent 鏆撮湶鍒?CLI / HTTP / 鏈嶅姟鍏ュ彛銆?

### 14.2 Finetuning System

Finetuning System 涓嶆敼鍙?runtime loop 鐨勭涓€闃舵琛屼负锛屽畠娑堣垂 eval銆乼race銆乼ool trajectory 鍜屼汉宸ユ爣娉ㄦ暟鎹紝鐢ㄤ簬鍚庢湡浼樺寲妯″瀷鎴?prompt銆?

鏃╂湡鍙鐣欏鍑鸿竟鐣岋細

```text
TrainingExampleExporter
TrajectoryDataset
PromptOptimizationRun
FinetuningRun
```

瑙勫垯锛?

- 璁粌鏁版嵁瀵煎嚭鍙兘璇诲彇 observability / eval / persistence 涓殑璁板綍锛屼笉鍙嶅悜姹℃煋榛樿 prompt銆?
- prompt optimization 灞炰簬 `finetuning/` 涓?`eval/` 鐨勫崗浣滐紝涓嶆浛浠?`context/renderer.py` 鐨勯粯璁ゆ覆鏌撹鍒欍€?
- 寰皟妯″瀷閫夋嫨鍜岃缁冩墽琛屾槸鍚庢湡鎵╁睍锛屼笉杩涘叆 Phase 1-7 鐨勪富閾俱€?

---

## 15. 鏃?neoagent 浠ｇ爜澶嶇敤瑙勫垯

### 15.1 鍙互鍙傝€冩垨鎼繍

- Provider API 璋冪敤缁嗚妭銆?
- Tool calling schema 閫傞厤缁忛獙銆?
- MCP client 鐢熷懡鍛ㄦ湡绠＄悊銆?
- Skill discovery 缁忛獙銆?
- 閰嶇疆鍔犺浇銆?
- 浜嬩欢涓庤瀵熻€呭疄鐜扮粡楠屻€?
- 娴嬭瘯 fixtures銆?

### 15.2 涓嶈鎼繍

- 鏃?8 灞?prompt renderer銆?
- 鏃?working memory 瀛楁妯″瀷銆?
- 鏃?PromptBuilder 鎷兼帴鎶借薄銆?
- 鏃?compressed history schema銆?
- 鏃?memory context 娉ㄥ叆鏂瑰紡銆?
- 浠讳綍鎶?runtime metadata 鐩存帴娓叉煋杩?prompt 鐨勯€昏緫銆?

---

## 16. 瀹炴柦闃舵

### 瀹炵幇鏈熼獙璇侀闄?

浠ヤ笅闂涓嶅啀閫氳繃缁х画鎵╁啓璁捐鏂囨。瑙ｅ喅锛岃€屾槸鍦ㄥ搴?phase 鐢ㄦ祴璇曞拰鏈€灏忓疄鐜伴獙璇侊細

| 椋庨櫓鐐?| 楠岃瘉鏃舵満 | 楠屾敹鏂瑰紡 |
|---|---|---|
| Compressor 涓嶈兘鍒囨柇 `tool_use` / `tool_result` 閰嶅銆?| Phase 2 鍐?`Evictor` 鏃?| 鐢?message window 娴嬭瘯瑕嗙洊閰嶅淇濇姢锛岀‘璁ゅ帇缂╁彧绉婚櫎瀹屾暣鍙帇缂╁尯闂淬€?|
| M2 瀛楁椤哄簭蹇呴』绋冲畾銆?| Phase 1 鍐?renderer golden tests 鏃?| 榛樿 renderer 鎸?declared schema 瀛楁椤哄簭娓叉煋锛屼笉鎸夊瓧姣嶆垨 runtime metadata 閲嶆帓銆?|
| Recall 娉ㄥ叆鐨勪复鏃舵秷鎭笉鑳界牬鍧?provider message 搴忓垪绾︽潫銆?| Phase 2 寮曞叆 recall runtime锛孭hase 3 鎺ョ湡瀹?provider 鍓?| 鐢?fake provider 鍜?provider adapter 娴嬭瘯瑕嗙洊涓存椂娑堟伅鎻掑叆涓庝笅涓€娆?request 鑷姩绉婚櫎銆?|
| Subagent 鐨?context 鍒濆鍖栫瓥鐣ヨ鏄惧紡銆?| Phase 7 鍋?multi-agent 鏃?| 鏄庣‘鏄?fork 鐖堕厤缃繕鏄嫭绔嬪垵濮嬪寲锛涢粯璁や笉鍏变韩涓?agent active messages 鎴?working state銆?|
| Schema 妯℃澘搴撶殑鍒嗗彂璺緞瑕佸拰 skill 寤惰繜鍔犺浇鍏煎銆?| Phase 5 鍋?skills 鏃?| 灏?schema template 浣滀负鍐呯疆 skill/cookbook 鑳藉姏娴嬭瘯锛屼笉鎻愬墠杩涘叆榛樿 prompt銆?|
| 琛屼负鍙嶉 hint 閫氶亾涓嶈兘姹℃煋榛樿 runtime metadata 杈圭晫銆?| 鍚庢湡澧為噺 | 鑻ラ渶瑕佸弽棣堢粰 LLM锛屼紭鍏堣璁′负鏄惧紡 M3 鎶曞奖鎴栫嫭绔?debug/ops 閫氶亾锛屽苟澧炲姞 golden tests銆?|

### ai-knowledge 瑕嗙洊琛?

| ai-knowledge 姒傚康 | 钀藉湴 phase | 璇存槑 |
|---|---:|---|
| `prompt-system` | Phase 1 | `ContextRenderer` 娓叉煋榛樿 LLM-visible context銆?|
| `query-loop` | Phase 1 | `QueryLoop` 褰㈡垚鏈€灏?turn 璋冨害銆?|
| `runtime-state` | Phase 3 | session銆乼urn銆乪vent bus 鍜岀敓鍛藉懆鏈熺姸鎬佺嫭绔嬪嚭鏉ャ€?|
| `context-management` | Phase 1-2 | Phase 1 鍋?context/messages 涓婚摼锛孭hase 2 鍋?compression/recall銆?|
| `tool-system` | Phase 4 | Tool registry銆乪xecutor銆乪xternal tool result routing銆?|
| `memory-system` | Phase 7 | Memory extractor/retriever/store 涓?M3 memory projection銆?|
| `multi-agent` | Phase 7 | Subagent 闅旂璋冨害銆?|
| `agent-registry-discovery` | Phase 7 | AgentCard銆乺egistry銆乺esolver 涓?multi-agent/channels 鍗忎綔銆?|
| `finetuning-system` | Phase 8 | 璁粌鏁版嵁瀵煎嚭銆乸rompt optimization銆乫inetuning/eval extensions銆?|
| `hooks` | Phase 3 | typed `EventBus` 鍜?`HookManager` 鍏堜簬 provider/tool loop 寤虹珛銆?|
| `mcp-skills` | Phase 5 | Skills銆丮CP registry銆乻chema template skill銆?|
| `channel-remote` | Phase 8 | CLI / HTTP / 杩滅▼鍏ュ彛銆?|
| `session-recovery` | Phase 6 | Persistence 涓?session restore銆?|
| `evaluation-observability` | Phase 6, Phase 8 | Phase 6 鍋?runtime events/traces锛孭hase 8 鍋?eval/finetuning 鑱斿姩銆?|
| `sandbox-isolation` | Phase 4 | SecurityPolicy銆乀oolPolicy 鍜?executor 闅旂銆?|

### Phase 1: Context + Messages 涓婚摼

- ContextState
- ContextRenderer
- Context tools
- MessageStore
- ActiveWindow
- ProviderRequestBuilder
- FakeProvider

楠屾敹锛?

- 鑳芥覆鏌?context-only 鑼冩枃缁撴瀯銆?
- 鑳介€氳繃 tools 鏇存柊 working state銆?
- 鑳界敓鎴?provider request銆?
- 榛樿 prompt 涓嶆毚闇?runtime metadata銆?

### Phase 2: Compression + Recall

- BudgetPolicy
- Evictor
- RuleBasedCompressor
- CompressionIndex
- recall_context

楠屾敹锛?

- 鏃?messages 浠?active window 绉婚櫎銆?
- 鍘熸枃浠嶅湪 MessageStore銆?
- prompt 鍑虹幇 `seg_1`銆?
- `recall_context("seg_1")` 鑳戒复鏃舵仮澶嶅師鏂囥€?

### Phase 3: Runtime State + Hooks Foundation

- SessionState
- TurnState
- EventBus
- HookRegistry
- HookManager

楠屾敹锛?

- session / turn 鐢熷懡鍛ㄦ湡涓嶉潬鏁ｈ惤瀛楁缁存姢銆?
- runtime loop 鍏抽敭鑺傜偣鑳藉彂鍑虹被鍨嬪寲浜嬩欢銆?
- hook 榛樿鍙锛涘け璐ョ瓥鐣ュ拰鎵ц椤哄簭鏄庣‘銆?
- 榛樿 prompt 涓嶆毚闇?hook/runtime metadata銆?

### Phase 4: Providers + Tools

- OpenAI provider
- Anthropic provider
- ToolRegistry
- ToolExecutor
- SecurityPolicy

楠屾敹锛?

- 鑳藉畬鎴愮湡瀹?provider tool-call loop銆?
- 宸ュ叿缁撴灉杩涘叆 MessageRuntime銆?
- context tools 鍜?external tools 璺敱娓呮櫚銆?

### Phase 5: Skills + MCP

- Skill registry
- Skill tool
- MCP registry
- MCP tool adapter
- Schema template skill

楠屾敹锛?

- system 鍙垪 skill/MCP 鎽樿銆?
- 閫氳繃 tool 鍔犺浇 skill銆?
- MCP tools 杩涘叆 capability plane銆?
- schema template 浣滀负鍐呯疆 skill/cookbook 鑳藉姏鍒嗗彂锛屼笉鎻愬墠杩涘叆榛樿 prompt銆?

### Phase 6: Persistence + Session Recovery + Observability

- SQLite/File persistence
- Event log
- Langfuse adapter
- OTel adapter
- debug projection

楠屾敹锛?

- session 鍙仮澶嶃€?
- message/context/compression/recall 浜嬩欢鍙拷韪€?
- 榛樿 prompt 浠嶄笉鏆撮湶 runtime metadata銆?

### Phase 7: Memory + Multi-Agent + Agent Registry

- Memory extractor/retriever/store
- subagent orchestration
- AgentCard
- AgentRegistry
- AgentResolver

楠屾敹锛?

- memory context 鍙彫鍥炲苟娓叉煋銆?
- subagent 闅旂杩愯銆?
- 涓?agent 鍙€氳繃 tool result 鍚告敹 subagent 鍙戠幇銆?
- agent registry 鏀寔鏈湴娉ㄥ唽銆佹寜鍚嶇О瑙ｆ瀽鍜屾寜鑳藉姏鏋氫妇銆?

### Phase 8: Channels + Remote + Finetuning/Eval Extensions

- CLI/HTTP channels
- remote channel adapter
- eval runner
- training example exporter
- prompt optimization runner

楠屾敹锛?

- agent 鍙€氳繃涓嶅悓 channel 鏆撮湶銆?
- eval cases 鑳藉鐢?runtime traces銆?
- finetuning/export 鍙秷璐硅娴嬩笌璇勪及鏁版嵁锛屼笉鍙嶅悜淇敼榛樿 context protocol銆?

---

## 17. 涓€鍙ヨ瘽鏋舵瀯鍥?

```text
AgentOs
  -> QueryLoop
      -> ContextRuntime      # agent cognition
      -> MessageRuntime      # truth source + active window
      -> ToolCallRouter      # context tools / external tools / skills / MCP
      -> Provider            # model backend
      -> Observability       # runtime metadata, not prompt
      -> Persistence         # recovery and storage
```

