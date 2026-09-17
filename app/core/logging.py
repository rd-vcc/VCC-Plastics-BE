"""Basic application logging setup.

Configures the root logger once at import time and exposes a module-level
``logger`` used by ``app.middleware.request_logging`` and
``app.middleware.error_handler``.
"""

import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

logger = logging.getLogger("vcc_plastics")
