"""Provider 工具 schema 值类型和序列化边界。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from agentos.providers.json_values import FrozenJsonObject, freeze_json, thaw_json


@dataclass(frozen=True, slots=True, init=False)
class ProviderFunctionSpec:
    """OpenAI-style function tool schema 的 function 部分。"""

    name: str
    description: str
    parameters: FrozenJsonObject

    def __init__(
        self,
        name: str,
        description: str,
        parameters: dict[str, object] | FrozenJsonObject | None = None,
    ) -> None:
        frozen = freeze_json({} if parameters is None else parameters)
        if not isinstance(frozen, FrozenJsonObject):
            raise TypeError("provider function parameters must be a JSON object")
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "description", description)
        object.__setattr__(self, "parameters", frozen)


@dataclass(frozen=True, slots=True)
class ProviderToolSpec:
    """Provider 工具 schema，保留 canonical function 形态。"""

    function: ProviderFunctionSpec
    type: Literal["function"] = "function"

    def __getitem__(self, key: str) -> object:
        """提供只读 dict-style schema 访问。"""

        return provider_tool_spec_to_dict(self)[key]

    def get(self, key: str, default: object = None) -> object:
        """提供只读 dict-style schema 访问。"""

        return provider_tool_spec_to_dict(self).get(key, default)

    def __eq__(self, other: object) -> bool:
        """支持值对象和 canonical dict 比较。"""

        if isinstance(other, ProviderToolSpec):
            return self.type == other.type and self.function == other.function
        if isinstance(other, dict):
            return provider_tool_spec_to_dict(self) == other
        return False


def provider_tool_spec_to_dict(spec: ProviderToolSpec) -> dict[str, object]:
    """把强类型 Provider 工具 schema 转成 canonical dict。"""

    if not isinstance(spec, ProviderToolSpec):
        spec = provider_tool_spec_from_dict(spec)
    return {
        "type": spec.type,
        "function": {
            "name": spec.function.name,
            "description": spec.function.description,
            "parameters": thaw_json(spec.function.parameters),
        },
    }


def provider_tool_spec_from_dict(value: object) -> ProviderToolSpec:
    """把 canonical function-tool dict 标准化为 ProviderToolSpec。"""

    if isinstance(value, ProviderToolSpec):
        return value
    if not isinstance(value, dict):
        raise ValueError("provider tool spec must be an object")
    if value.get("type") != "function":
        raise ValueError("provider tool spec type must be 'function'")
    function = value.get("function")
    if not isinstance(function, dict):
        raise ValueError("provider tool spec requires function object")
    name = function.get("name")
    description = function.get("description")
    parameters = function.get("parameters", {})
    if not isinstance(name, str) or not name:
        raise ValueError("provider function spec requires name")
    if not isinstance(description, str):
        raise ValueError("provider function spec requires description")
    if not isinstance(parameters, dict):
        raise ValueError("provider function parameters must be an object")
    return ProviderToolSpec(
        function=ProviderFunctionSpec(
            name=name,
            description=description,
            parameters=parameters,
        ),
    )


__all__ = [
    "ProviderFunctionSpec",
    "ProviderToolSpec",
    "provider_tool_spec_from_dict",
    "provider_tool_spec_to_dict",
]
