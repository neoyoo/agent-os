from __future__ import annotations

from dataclasses import dataclass
import json
import logging
from typing import Protocol

from agentos.observability.config import ObservabilityConfig, default_redactor
from agentos.observability.context import current_trace_ids


class StructuredLogger(Protocol):
    """QueryLoop 使用的结构化日志边界。"""

    def log(self, event: str, **fields: object) -> None:
        """写出一个结构化 runtime 事件。"""


class StructuredLogFormatter(logging.Formatter):
    """把 logging record 格式化成一行 JSON。"""

    def format(self, record: logging.LogRecord) -> str:
        """返回稳定 JSON log line。"""

        payload = {
            "level": record.levelname.lower(),
            "event": record.getMessage(),
        }
        extra = getattr(record, "agentos", None)
        if isinstance(extra, dict):
            payload.update(extra)
        return json.dumps(
            default_redactor(payload),
            ensure_ascii=False,
            separators=(",", ":"),
        )


@dataclass(slots=True)
class PythonStructuredLogger:
    """标准 logging-backed structured logger。"""

    logger: logging.Logger

    def log(self, event: str, **fields: object) -> None:
        """写出一条 INFO 级别结构化日志。"""

        trace_ids = current_trace_ids()
        if trace_ids.trace_id is not None:
            fields.setdefault("trace_id", trace_ids.trace_id)
        if trace_ids.span_id is not None:
            fields.setdefault("span_id", trace_ids.span_id)
        self.logger.info(event, extra={"agentos": fields})


def configure_structured_logger(
    config: ObservabilityConfig,
) -> StructuredLogger | None:
    """根据 ObservabilityConfig 创建结构化 logger；默认关闭。"""

    if not config.logging_enabled:
        return None
    logger = logging.getLogger(config.logger_name)
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(StructuredLogFormatter())
        logger.addHandler(handler)
    logger.setLevel(config.logging_level)
    return PythonStructuredLogger(logger)
