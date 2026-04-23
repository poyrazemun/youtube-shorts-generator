"""
Exponential backoff retry decorator for all external API calls.
Delay formula: base_delay * 2^attempt  (2s → 4s → 8s for defaults).
"""
import functools
import logging
import time
from typing import Callable

logger = logging.getLogger(__name__)


def with_retry(
    max_retries: int = 3,
    base_delay: float = 2.0,
    exceptions: tuple = (Exception,),
) -> Callable:
    """
    Decorator factory: retries the wrapped function with exponential backoff.

    Args:
        max_retries: Max retry attempts after first failure (default: 3).
        base_delay: Base delay in seconds (default: 2.0). Doubles each attempt.
        exceptions: Exception types to catch and retry (default: all).
    """
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            for attempt in range(max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except exceptions as e:
                    if attempt < max_retries:
                        delay = base_delay * (2 ** attempt)
                        logger.warning(
                            f"[retry] {func.__name__} failed "
                            f"(attempt {attempt + 1}/{max_retries + 1}): "
                            f"{e} — retrying in {delay:.1f}s..."
                        )
                        time.sleep(delay)
                    else:
                        logger.error(
                            f"[retry] {func.__name__} failed after "
                            f"{max_retries + 1} attempts: {e}"
                        )
                        raise
        return wrapper
    return decorator
