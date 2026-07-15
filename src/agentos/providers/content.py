"""Provider 无关的内容片段类型。"""

from dataclasses import dataclass, field
from typing import Literal, TypeAlias


@dataclass(frozen=True, slots=True)
class ProviderBinaryPayload:
    """Provider 二进制内容的不可变请求载荷。"""

    handle: str
    media_type: str
    data: bytes = field(repr=False)
    filename: str | None = None

    def __post_init__(self) -> None:
        if type(self.handle) is not str or not self.handle:
            raise TypeError("provider binary payload handle must be a non-empty str")
        if type(self.media_type) is not str or not self.media_type:
            raise TypeError("provider binary payload media_type must be a non-empty str")
        if type(self.data) is not bytes:
            raise TypeError("provider binary payload data must be bytes")
        if self.filename is not None and type(self.filename) is not str:
            raise TypeError("provider binary payload filename must be str or None")


@dataclass(frozen=True, slots=True)
class TextPart:
    """Provider 输入中的文本片段。"""

    text: str


@dataclass(frozen=True, slots=True)
class ImagePart:
    """Provider 输入中的一次性图片片段。"""

    payload: ProviderBinaryPayload
    detail: Literal["auto", "low", "high"] = "auto"

    def __post_init__(self) -> None:
        if type(self.payload) is not ProviderBinaryPayload:
            raise TypeError("ImagePart payload must be ProviderBinaryPayload")


@dataclass(frozen=True, slots=True)
class FilePart:
    """Provider 输入中的一次性文件片段。"""

    payload: ProviderBinaryPayload

    def __post_init__(self) -> None:
        if type(self.payload) is not ProviderBinaryPayload:
            raise TypeError("FilePart payload must be ProviderBinaryPayload")


ProviderContentPart: TypeAlias = TextPart | ImagePart | FilePart
