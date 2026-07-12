"""Provider 与 Context 内部投影对象的中立标记。"""


class InternalTranscriptValue:
    """标记不得进入业务消息或前端 Read Model 的内部值。"""

    __slots__ = ()
