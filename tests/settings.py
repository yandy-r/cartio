"""Settings for the test run: the controller's own settings in local-development mode."""

import os

os.environ.setdefault("CARTIO_DEBUG", "1")

from cartio_controller.settings import *  # noqa: E402, F403
