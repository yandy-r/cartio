"""Settings for the test run: the controller's own settings in local-development mode."""

import base64
import os

os.environ.setdefault("CARTIO_DEBUG", "1")
# A fixed test key for credentials. Real controllers read theirs from CARTIO_MASTER_KEY_FILE.
os.environ.setdefault(
    "CARTIO_MASTER_KEY", base64.b64encode(b"cartio test key, never prod use!").decode()
)

from cartio_controller.settings import *  # noqa: E402, F403
