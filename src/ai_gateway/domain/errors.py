"""Domain error hierarchy.

Every failure that the gateway can express to a caller derives from :class:`DomainError`.
Adapters translate transport-specific failures into these errors so that use cases never
depend on a concrete transport or vendor SDK.
"""

from __future__ import annotations

from typing import Any


class DomainError(Exception):
    """Base class for all business-rule violations and gateway failures.

    Attributes:
        code: Stable, machine-readable error identifier surfaced to API clients.
        message: Human readable description of the failure.
        details: Optional structured context, safe for logging and API responses.
        http_status: Suggested HTTP status code for delivery adapters.
        retryable: Whether the caller may safely retry the same request.
    """

    code: str = "internal_error"
    http_status: int = 500
    retryable: bool = False

    def __init__(
        self,
        message: str,
        *,
        details: dict[str, Any] | None = None,
        code: str | None = None,
    ) -> None:
        """Initialise the error.

        Args:
            message: Human readable description of the failure.
            details: Optional structured context.
            code: Optional override of the class-level error code.
        """
        super().__init__(message)
        self.message = message
        self.details: dict[str, Any] = details or {}
        if code is not None:
            self.code = code

    def to_dict(self) -> dict[str, Any]:
        """Serialise the error into a transport-neutral mapping.

        Returns:
            A mapping with the error code, message and structured details.
        """
        return {"code": self.code, "message": self.message, "details": self.details}

    def __repr__(self) -> str:
        """Return an unambiguous representation for logs."""
        return f"{type(self).__name__}(code={self.code!r}, message={self.message!r})"


# --------------------------------------------------------------------------------------
# Validation and request shaping
# --------------------------------------------------------------------------------------
class ValidationError(DomainError):
    """Raised when caller-supplied data violates a business invariant."""

    code = "validation_error"
    http_status = 422


class PromptValidationError(ValidationError):
    """Raised when a prompt template or its variables are invalid."""

    code = "prompt_validation_error"


class UnsupportedCapabilityError(DomainError):
    """Raised when no model satisfies the capability requested by the caller."""

    code = "unsupported_capability"
    http_status = 400


# --------------------------------------------------------------------------------------
# Security
# --------------------------------------------------------------------------------------
class AuthenticationError(DomainError):
    """Raised when a caller cannot be identified."""

    code = "unauthenticated"
    http_status = 401


class AuthorizationError(DomainError):
    """Raised when an identified caller lacks the required permission."""

    code = "forbidden"
    http_status = 403


class TenantIsolationError(AuthorizationError):
    """Raised when a caller attempts to access another tenant's resources."""

    code = "tenant_isolation_violation"


class PromptInjectionError(DomainError):
    """Raised when the injection detector blocks a request."""

    code = "prompt_injection_detected"
    http_status = 400


class ContentPolicyError(DomainError):
    """Raised when input or output violates the configured content policy."""

    code = "content_policy_violation"
    http_status = 400


class SignatureVerificationError(AuthenticationError):
    """Raised when a signed request or webhook payload fails verification."""

    code = "invalid_signature"


# --------------------------------------------------------------------------------------
# Governance
# --------------------------------------------------------------------------------------
class RateLimitExceededError(DomainError):
    """Raised when a tenant exceeds its configured request rate."""

    code = "rate_limit_exceeded"
    http_status = 429
    retryable = True

    def __init__(
        self,
        message: str,
        *,
        retry_after_seconds: int = 1,
        details: dict[str, Any] | None = None,
    ) -> None:
        """Initialise the error.

        Args:
            message: Human readable description of the failure.
            retry_after_seconds: Hint for the caller's backoff.
            details: Optional structured context.
        """
        super().__init__(message, details=details)
        self.retry_after_seconds = retry_after_seconds


class QuotaExceededError(DomainError):
    """Raised when a tenant exhausts a daily or monthly quota."""

    code = "quota_exceeded"
    http_status = 429


class BudgetExceededError(QuotaExceededError):
    """Raised when a tenant exceeds its configured spend limit."""

    code = "budget_exceeded"


# --------------------------------------------------------------------------------------
# Resources
# --------------------------------------------------------------------------------------
class NotFoundError(DomainError):
    """Raised when a referenced aggregate does not exist."""

    code = "not_found"
    http_status = 404


class ConflictError(DomainError):
    """Raised when an operation conflicts with the current aggregate state."""

    code = "conflict"
    http_status = 409


# --------------------------------------------------------------------------------------
# Providers and routing
# --------------------------------------------------------------------------------------
class ProviderError(DomainError):
    """Base class for upstream provider failures."""

    code = "provider_error"
    http_status = 502
    retryable = True

    def __init__(
        self,
        message: str,
        *,
        provider: str = "unknown",
        details: dict[str, Any] | None = None,
        code: str | None = None,
    ) -> None:
        """Initialise the error.

        Args:
            message: Human readable description of the failure.
            provider: Identifier of the upstream provider that failed.
            details: Optional structured context.
            code: Optional override of the class-level error code.
        """
        merged = {"provider": provider, **(details or {})}
        super().__init__(message, details=merged, code=code)
        self.provider = provider


class ProviderTimeoutError(ProviderError):
    """Raised when an upstream provider exceeds the configured deadline."""

    code = "provider_timeout"
    http_status = 504


class ProviderRateLimitError(ProviderError):
    """Raised when an upstream provider throttles the gateway."""

    code = "provider_rate_limited"
    http_status = 429


class ProviderAuthenticationError(ProviderError):
    """Raised when upstream credentials are rejected."""

    code = "provider_authentication_failed"
    http_status = 502
    retryable = False


class ProviderBadResponseError(ProviderError):
    """Raised when an upstream response cannot be mapped to the domain model."""

    code = "provider_bad_response"
    retryable = False


class NoProviderAvailableError(DomainError):
    """Raised when routing cannot find a healthy provider for a request."""

    code = "no_provider_available"
    http_status = 503
    retryable = True


class CircuitOpenError(DomainError):
    """Raised when a circuit breaker short-circuits a call."""

    code = "circuit_open"
    http_status = 503
    retryable = True


class RequestCancelledError(DomainError):
    """Raised when a client disconnects or explicitly cancels an in-flight request."""

    code = "request_cancelled"
    http_status = 499


# --------------------------------------------------------------------------------------
# Agents and tools
# --------------------------------------------------------------------------------------
class ToolError(DomainError):
    """Raised when a registered tool fails during execution."""

    code = "tool_execution_failed"
    http_status = 500


class ToolNotFoundError(NotFoundError):
    """Raised when an agent requests a tool that is not registered."""

    code = "tool_not_found"


class AgentExecutionError(DomainError):
    """Raised when an agent run cannot complete successfully."""

    code = "agent_execution_failed"
    http_status = 500


class MaxIterationsExceededError(AgentExecutionError):
    """Raised when an agent exceeds its configured step budget."""

    code = "agent_max_iterations_exceeded"


__all__ = [
    "AgentExecutionError",
    "AuthenticationError",
    "AuthorizationError",
    "BudgetExceededError",
    "CircuitOpenError",
    "ConflictError",
    "ContentPolicyError",
    "DomainError",
    "MaxIterationsExceededError",
    "NoProviderAvailableError",
    "NotFoundError",
    "PromptInjectionError",
    "PromptValidationError",
    "ProviderAuthenticationError",
    "ProviderBadResponseError",
    "ProviderError",
    "ProviderRateLimitError",
    "ProviderTimeoutError",
    "QuotaExceededError",
    "RateLimitExceededError",
    "RequestCancelledError",
    "SignatureVerificationError",
    "TenantIsolationError",
    "ToolError",
    "ToolNotFoundError",
    "UnsupportedCapabilityError",
    "ValidationError",
]
