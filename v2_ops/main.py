from __future__ import annotations

from v2_ops.app import create_ops_app
from v2_ops.settings import OpsWebSettings

settings = OpsWebSettings.from_env()
app = create_ops_app(settings)
