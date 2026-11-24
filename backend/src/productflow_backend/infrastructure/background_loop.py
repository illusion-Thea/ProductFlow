"""Retry transient polling failures; model execution is never retried here."""

import logging
import time
from collections.abc import Callable

from sqlalchemy.exc import DBAPIError, OperationalError

logger = logging.getLogger(__name__)


def run_polling_loop(step: Callable[[], object], interval: float) -> None:
    while True:
        try:
            worked = step()
        except DBAPIError as exc:
            code = getattr(exc.orig, "sqlstate", "") or ""
            if not (
                exc.connection_invalidated
                or isinstance(exc, OperationalError)
                or code.startswith("08")
                or code in {"40001", "40P01", "55P03", "57P01", "53300"}
            ):
                raise
            # DB exception text can contain SQL parameters; log a safe category.
            logger.warning("background_poll_retry error_type=%s sqlstate=%s", type(exc).__name__, code)
            time.sleep(max(interval, 0.5))
        else:
            if not worked:
                time.sleep(interval)
