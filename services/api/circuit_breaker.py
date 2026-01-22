"""
Circuit Breaker pattern implementation for external service calls.

Uses tenacity library for retry logic with circuit breaker semantics.
Protects against cascading failures when external services (S3, webhooks) are down.

States:
- CLOSED: Normal operation, requests flow through
- OPEN: Service is down, requests fail fast without calling service
- HALF_OPEN: Testing if service recovered, limited requests allowed

Usage:
    from circuit_breaker import with_circuit_breaker, storage_breaker, webhook_breaker

    @with_circuit_breaker(storage_breaker)
    def upload_to_s3(data):
        # S3 operations here
        pass
"""

import time
import logging
from typing import Callable, Any, Optional, Dict
from functools import wraps
from enum import Enum
from threading import Lock

from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
    RetryCallState,
)

from exceptions import CircuitBreakerOpenError

logger = logging.getLogger(__name__)


class CircuitState(Enum):
    """Circuit breaker states."""
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreaker:
    """
    Circuit breaker implementation for protecting external service calls.

    Tracks failures and opens the circuit when threshold is exceeded.
    Automatically attempts recovery after timeout period.
    """

    def __init__(
        self,
        name: str,
        failure_threshold: int = 5,
        recovery_timeout: float = 30.0,
        half_open_max_calls: int = 3,
    ):
        """
        Initialize circuit breaker.

        Args:
            name: Name for logging and identification
            failure_threshold: Number of failures before opening circuit
            recovery_timeout: Seconds to wait before attempting recovery
            half_open_max_calls: Max calls allowed in half-open state
        """
        self.name = name
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.half_open_max_calls = half_open_max_calls

        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._last_failure_time: Optional[float] = None
        self._half_open_calls = 0
        self._lock = Lock()

    @property
    def state(self) -> CircuitState:
        """Get current circuit state, potentially transitioning to half-open."""
        with self._lock:
            if self._state == CircuitState.OPEN:
                # Check if recovery timeout has passed
                if self._last_failure_time:
                    elapsed = time.time() - self._last_failure_time
                    if elapsed >= self.recovery_timeout:
                        self._state = CircuitState.HALF_OPEN
                        self._half_open_calls = 0
                        logger.info(
                            f"Circuit breaker '{self.name}' transitioning to HALF_OPEN "
                            f"after {elapsed:.1f}s"
                        )
            return self._state

    def _can_execute(self) -> bool:
        """Check if request can proceed."""
        state = self.state  # This may trigger state transition

        if state == CircuitState.CLOSED:
            return True
        elif state == CircuitState.OPEN:
            return False
        elif state == CircuitState.HALF_OPEN:
            with self._lock:
                if self._half_open_calls < self.half_open_max_calls:
                    self._half_open_calls += 1
                    return True
                return False
        return False

    def record_success(self):
        """Record a successful call."""
        with self._lock:
            if self._state == CircuitState.HALF_OPEN:
                # Service recovered, close the circuit
                self._state = CircuitState.CLOSED
                self._failure_count = 0
                self._half_open_calls = 0
                logger.info(f"Circuit breaker '{self.name}' CLOSED - service recovered")
            elif self._state == CircuitState.CLOSED:
                # Reset failure count on success
                self._failure_count = 0

    def record_failure(self, error: Exception):
        """Record a failed call."""
        with self._lock:
            self._failure_count += 1
            self._last_failure_time = time.time()

            if self._state == CircuitState.HALF_OPEN:
                # Failure during recovery, back to open
                self._state = CircuitState.OPEN
                logger.warning(
                    f"Circuit breaker '{self.name}' OPEN - "
                    f"recovery attempt failed: {error}"
                )
            elif self._state == CircuitState.CLOSED:
                if self._failure_count >= self.failure_threshold:
                    self._state = CircuitState.OPEN
                    logger.warning(
                        f"Circuit breaker '{self.name}' OPEN - "
                        f"failure threshold ({self.failure_threshold}) exceeded: {error}"
                    )

    def get_status(self) -> Dict[str, Any]:
        """Get circuit breaker status for monitoring."""
        with self._lock:
            status = {
                "name": self.name,
                "state": self._state.value,
                "failure_count": self._failure_count,
                "failure_threshold": self.failure_threshold,
            }

            if self._last_failure_time and self._state == CircuitState.OPEN:
                elapsed = time.time() - self._last_failure_time
                status["time_until_half_open"] = max(0, self.recovery_timeout - elapsed)

            return status

    def reset(self):
        """Manually reset the circuit breaker."""
        with self._lock:
            self._state = CircuitState.CLOSED
            self._failure_count = 0
            self._last_failure_time = None
            self._half_open_calls = 0
            logger.info(f"Circuit breaker '{self.name}' manually reset")


def with_circuit_breaker(
    breaker: CircuitBreaker,
    fallback: Optional[Callable] = None,
):
    """
    Decorator that wraps a function with circuit breaker protection.

    Args:
        breaker: CircuitBreaker instance to use
        fallback: Optional fallback function to call when circuit is open

    Usage:
        @with_circuit_breaker(storage_breaker)
        def upload_file(data):
            # S3 upload logic
            pass
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs):
            if not breaker._can_execute():
                logger.warning(
                    f"Circuit breaker '{breaker.name}' is OPEN, "
                    f"rejecting call to {func.__name__}"
                )
                if fallback:
                    return fallback(*args, **kwargs)

                # Calculate time until reset
                reset_time = None
                if breaker._last_failure_time:
                    elapsed = time.time() - breaker._last_failure_time
                    reset_time = max(0, breaker.recovery_timeout - elapsed)

                raise CircuitBreakerOpenError(
                    service=breaker.name,
                    reset_time=reset_time,
                )

            try:
                result = func(*args, **kwargs)
                breaker.record_success()
                return result
            except Exception as e:
                breaker.record_failure(e)
                raise

        return wrapper
    return decorator


def create_retry_decorator(
    max_attempts: int = 3,
    min_wait: float = 1.0,
    max_wait: float = 30.0,
    retry_exceptions: tuple = (Exception,),
    on_retry: Optional[Callable[[RetryCallState], None]] = None,
):
    """
    Create a tenacity retry decorator with exponential backoff.

    Args:
        max_attempts: Maximum retry attempts
        min_wait: Minimum wait time between retries (seconds)
        max_wait: Maximum wait time between retries (seconds)
        retry_exceptions: Tuple of exception types to retry on
        on_retry: Callback function called on each retry

    Returns:
        Configured retry decorator
    """
    def log_retry(retry_state: RetryCallState):
        """Log retry attempts."""
        logger.warning(
            f"Retry attempt {retry_state.attempt_number} for "
            f"{retry_state.fn.__name__ if retry_state.fn else 'unknown'}: "
            f"{retry_state.outcome.exception() if retry_state.outcome else 'unknown error'}"
        )
        if on_retry:
            on_retry(retry_state)

    return retry(
        stop=stop_after_attempt(max_attempts),
        wait=wait_exponential(multiplier=min_wait, max=max_wait),
        retry=retry_if_exception_type(retry_exceptions),
        before_sleep=log_retry,
        reraise=True,
    )


# Pre-configured circuit breakers for different services

storage_breaker = CircuitBreaker(
    name="storage",
    failure_threshold=5,
    recovery_timeout=30.0,
    half_open_max_calls=2,
)

webhook_breaker = CircuitBreaker(
    name="webhooks",
    failure_threshold=10,  # Higher threshold for webhooks
    recovery_timeout=60.0,  # Longer recovery for external endpoints
    half_open_max_calls=3,
)

database_breaker = CircuitBreaker(
    name="database",
    failure_threshold=3,  # Lower threshold for critical service
    recovery_timeout=15.0,  # Shorter recovery for database
    half_open_max_calls=1,
)


# Retry decorators for different use cases

retry_storage = create_retry_decorator(
    max_attempts=3,
    min_wait=1.0,
    max_wait=10.0,
    retry_exceptions=(ConnectionError, TimeoutError),
)

retry_webhook = create_retry_decorator(
    max_attempts=3,
    min_wait=1.0,
    max_wait=30.0,
    retry_exceptions=(ConnectionError, TimeoutError),
)

retry_database = create_retry_decorator(
    max_attempts=2,
    min_wait=0.5,
    max_wait=5.0,
)


def get_all_breaker_status() -> Dict[str, Dict[str, Any]]:
    """Get status of all circuit breakers for monitoring."""
    return {
        "storage": storage_breaker.get_status(),
        "webhooks": webhook_breaker.get_status(),
        "database": database_breaker.get_status(),
    }


def reset_all_breakers():
    """Reset all circuit breakers (for testing or manual recovery)."""
    storage_breaker.reset()
    webhook_breaker.reset()
    database_breaker.reset()
