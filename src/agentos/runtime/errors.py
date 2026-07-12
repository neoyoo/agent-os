class AgentRunError(RuntimeError):
    pass


class AgentBusyError(AgentRunError):
    pass


class AgentStreamConsumerError(AgentRunError):
    pass


class AgentStreamClosedError(AgentRunError):
    pass


class ContinuationUnavailableError(AgentRunError):
    pass


class WaitingUnsupportedError(AgentRunError):
    pass


class RunProtocolError(AgentRunError):
    pass


class SyncAdapterEventLoopError(AgentRunError):
    pass


class SyncAdapterReentryError(AgentRunError):
    pass


class SyncAgentClosedError(AgentRunError):
    pass


class SyncStreamConsumerError(AgentRunError):
    pass
