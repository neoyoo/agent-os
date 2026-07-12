"""可信 SystemEnvelope 的固定 Markdown 渲染器。"""

from __future__ import annotations

from agentos.context.markdown_security import (
    generated_h1_is_safe,
    normalize_markdown_text,
    validate_no_h1,
)
from agentos.context.models import (
    AllowedContentPartKind,
    ContextBudgetExceededError,
    ContextProtocolError,
    RuntimeContract,
    RuntimeDirective,
    RuntimeDirectiveKind,
    SystemEnvelope,
    TrustedSkillInstruction,
)
from agentos.context.registry import (
    SystemEnvelopeBudgetPolicy,
    SystemSectionRegistry,
)
from agentos.tokens import TokenCounter

_DIRECTIVE_TEMPLATES: dict[RuntimeDirectiveKind, str] = {
    RuntimeDirectiveKind.BACKGROUND_TOOL_RUNNING: "后台 Tool 正在执行；不要轮询或重复发起同一操作。",
    RuntimeDirectiveKind.AWAITING_APPROVAL: "当前操作正在等待人工审批；在审批结果返回前不要继续该操作。",
}
_INVALID_DIRECTIVE = "runtime directive provider returned invalid DTO"
_INVALID_RUNTIME = "runtime contract provider returned invalid DTO"
_INVALID_SKILL = "trusted skill provider returned invalid DTO"
_INVALID_WORKSPACE = "workspace contract provider returned invalid DTO"
_REQUIRED_MISSING = "required system section is missing"

class ContextRenderer:
    """从可信 Section Registry 确定性生成 SystemEnvelope。"""

    __slots__ = ("_budget_policy", "_registry", "_token_counter")

    def __init__(
        self,
        *,
        registry: SystemSectionRegistry,
        token_counter: TokenCounter,
        budget_policy: SystemEnvelopeBudgetPolicy | None = None,
    ) -> None:
        """注入可信 Registry、TokenCounter 和不可变预算策略。"""

        if not isinstance(registry, SystemSectionRegistry):
            raise ContextProtocolError("system section registry is invalid")
        if budget_policy is not None and not isinstance(
            budget_policy,
            SystemEnvelopeBudgetPolicy,
        ):
            raise ContextProtocolError("system envelope budget policy is invalid")
        self._registry = registry
        self._token_counter = token_counter
        self._budget_policy = budget_policy or SystemEnvelopeBudgetPolicy()

    def render(self) -> SystemEnvelope:
        """按协议固定顺序收集并渲染可信章节。"""

        sections = [
            self._runtime_contract(),
            self._required_text_section(
                provider=self._registry.interaction_protocol_provider,
                method_name="interaction_protocol",
                title="Interaction Protocol",
                invalid_message="interaction protocol provider returned invalid DTO",
                budget=self._budget_policy.interaction_protocol,
            ),
            self._required_text_section(
                provider=self._registry.context_management_rules_provider,
                method_name="context_management_rules",
                title="Context Management Rules",
                invalid_message="context management provider returned invalid DTO",
                budget=self._budget_policy.context_management_rules,
            ),
        ]
        directives = self._runtime_directives()
        if directives is not None:
            sections.append(directives)
        sections.extend(self._trusted_skills())
        workspace = self._workspace_contract()
        if workspace is not None:
            sections.append(workspace)
        return SystemEnvelope(text="\n\n".join(sections) + "\n")

    def _runtime_contract(self) -> str:
        provider = self._registry.runtime_contract_provider
        if provider is None:
            raise ContextBudgetExceededError(_REQUIRED_MISSING)
        value = self._provider_value(provider, "runtime_contract", _INVALID_RUNTIME)
        if type(value) is not RuntimeContract:
            raise ContextProtocolError(_INVALID_RUNTIME)
        identity = _normalize_text(value.identity, _INVALID_RUNTIME)
        guardrails = _normalize_rules(value.security_guardrails)
        additional_rules = _normalize_rules(value.additional_rules)
        if not identity or not guardrails:
            raise ContextBudgetExceededError(_REQUIRED_MISSING)
        body = "\n".join(
            [
                "## Identity",
                "",
                identity,
                "",
                "## Security Guardrails",
                "",
                *(f"- {rule}" for rule in (*guardrails, *additional_rules)),
            ],
        )
        return self._checked_section(
            _section("Runtime Contract", body),
            self._budget_policy.runtime_contract,
            identity,
            *guardrails,
            *additional_rules,
        )

    def _required_text_section(
        self,
        *,
        provider: object | None,
        method_name: str,
        title: str,
        invalid_message: str,
        budget: int,
    ) -> str:
        if provider is None:
            raise ContextBudgetExceededError(_REQUIRED_MISSING)
        value = self._provider_value(provider, method_name, invalid_message)
        body = _normalize_text(value, invalid_message)
        if not body:
            raise ContextBudgetExceededError(_REQUIRED_MISSING)
        return self._checked_section(_section(title, body), budget, body)

    def _runtime_directives(self) -> str | None:
        provider = self._registry.runtime_directives_provider
        if provider is None:
            return None
        value = self._provider_value(
            provider,
            "runtime_directives",
            _INVALID_DIRECTIVE,
        )
        if type(value) is not tuple:
            raise ContextProtocolError(_INVALID_DIRECTIVE)
        lines = [self._directive_text(item) for item in value]
        if not lines:
            return None
        section = _section("Runtime Directives", "\n".join(f"- {x}" for x in lines))
        return self._checked_section(
            section,
            self._budget_policy.runtime_directives,
        )

    def _directive_text(self, value: object) -> str:
        if type(value) is not RuntimeDirective:
            raise ContextProtocolError(_INVALID_DIRECTIVE)
        kind = value.kind
        part = value.content_part_kind
        if type(kind) is not RuntimeDirectiveKind:
            raise ContextProtocolError(_INVALID_DIRECTIVE)
        if kind is RuntimeDirectiveKind.UNSUPPORTED_CONTENT_PART:
            if type(part) is not AllowedContentPartKind:
                raise ContextProtocolError(_INVALID_DIRECTIVE)
            return (
                f"当前 Provider 不支持 `{part.value}` ContentPart；"
                "不要声称已读取或处理该内容。"
            )
        if part is not None:
            raise ContextProtocolError(_INVALID_DIRECTIVE)
        return _DIRECTIVE_TEMPLATES[kind]

    def _trusted_skills(self) -> list[str]:
        provider = self._registry.trusted_skill_instructions_provider
        if provider is None:
            return []
        value = self._provider_value(provider, "items", _INVALID_SKILL)
        if type(value) is not tuple:
            raise ContextProtocolError(_INVALID_SKILL)
        selected: list[str] = []
        total = 0
        for item in value:
            section, title, body = self._trusted_skill_candidate(item)
            count = self._count_tokens(section)
            if (
                count > self._budget_policy.trusted_skill_per_item
                or total + count > self._budget_policy.trusted_skill_total
            ):
                break
            if not generated_h1_is_safe(title):
                raise ContextProtocolError("system section body contains reserved heading")
            validate_no_h1(body)
            selected.append(section)
            total += count
        return selected

    def _trusted_skill_candidate(self, value: object) -> tuple[str, str, str]:
        if type(value) is not TrustedSkillInstruction:
            raise ContextProtocolError(_INVALID_SKILL)
        skill_id = value.skill_id
        if type(skill_id) is not str:
            raise ContextProtocolError(_INVALID_SKILL)
        skill_id = normalize_markdown_text(skill_id)
        if not skill_id or len(skill_id.splitlines()) != 1:
            raise ContextProtocolError(_INVALID_SKILL)
        body = _normalize_text(value.text, _INVALID_SKILL)
        if not body:
            raise ContextProtocolError(_INVALID_SKILL)
        title = f"Trusted Skill: {skill_id}"
        return _section(title, body), title, body

    def _workspace_contract(self) -> str | None:
        provider = self._registry.workspace_contract_provider
        if provider is None:
            return None
        value = self._provider_value(
            provider,
            "workspace_contract",
            _INVALID_WORKSPACE,
        )
        body = _normalize_text(value, _INVALID_WORKSPACE)
        if not body:
            return None
        return self._checked_section(
            _section("Workspace Contract", body),
            self._budget_policy.workspace_contract,
            body,
        )

    def _checked_section(
        self,
        section: str,
        budget: int,
        *provider_fragments: str,
    ) -> str:
        if self._count_tokens(section) > budget:
            raise ContextBudgetExceededError("system section exceeds token budget")
        for fragment in provider_fragments:
            validate_no_h1(fragment)
        return section

    def _count_tokens(self, section: str) -> int:
        failed = False
        try:
            count = self._token_counter.count_text(section)
        except Exception:
            failed = True
            count = None
        if failed:
            raise ContextProtocolError("token counter failed")
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            raise ContextProtocolError("token counter returned invalid count")
        return count

    def _provider_value(
        self,
        provider: object,
        method_name: str,
        invalid_message: str,
    ) -> object:
        failed = False
        try:
            value = getattr(provider, method_name)()
        except Exception:
            failed = True
            value = None
        if failed:
            raise ContextProtocolError(invalid_message)
        return value


def _section(title: str, body: str) -> str:
    return f"# {title}\n\n{body}"


def _normalize_text(value: object, invalid_message: str) -> str:
    if type(value) is not str:
        raise ContextProtocolError(invalid_message)
    return normalize_markdown_text(value)


def _normalize_rules(values: object) -> tuple[str, ...]:
    if type(values) is not tuple:
        raise ContextProtocolError(_INVALID_RUNTIME)
    normalized: list[str] = []
    for value in values:
        rule = _normalize_text(value, _INVALID_RUNTIME)
        if not rule or len(rule.splitlines()) != 1:
            raise ContextProtocolError(_INVALID_RUNTIME)
        normalized.append(rule)
    return tuple(normalized)
