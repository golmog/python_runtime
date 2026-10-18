"""Optional Python environments for FF plugins. Importing does not install anything."""
from .runtime import RuntimeManager, RuntimeError


def get_runtime():
    """Resolve FF settings lazily so plugin import order does not matter."""
    from .setup import P
    from framework import F
    return RuntimeManager.from_settings(P.ModelSetting.get, F.config['path_data'])
