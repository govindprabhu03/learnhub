"""Logging + optional Sentry error reporting (set SENTRY_DSN). Sentry's FastAPI/Starlette integrations switch on
automatically, so unhandled request errors are reported; failed background jobs are reported via capture()."""
import logging

import config


def init(service="web"):
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)  # one INFO line per request is noise
    if config.SENTRY_DSN:
        import sentry_sdk
        sentry_sdk.init(dsn=config.SENTRY_DSN, environment=config.ENVIRONMENT, server_name=service,
                        traces_sample_rate=config.SENTRY_TRACES, send_default_pii=False)  # never ships user data
        logging.getLogger("observability").info("Sentry enabled (%s, %s)", service, config.ENVIRONMENT)


def capture(exc):
    if config.SENTRY_DSN:
        import sentry_sdk
        sentry_sdk.capture_exception(exc)
