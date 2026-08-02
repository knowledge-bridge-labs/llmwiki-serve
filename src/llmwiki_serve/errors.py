from __future__ import annotations


class LlmWikiUserError(ValueError):
    """Actionable error safe to return through public request surfaces."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int = 400,
        json_rpc_code: int = -32602,
    ) -> None:
        super().__init__(message)
        self.safe_message = message
        self.status_code = status_code
        self.json_rpc_code = json_rpc_code
