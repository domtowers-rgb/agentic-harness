import importlib
import pkgutil
import os
from typing import Dict, Callable, List

PLUGIN_DIR = os.path.join(os.path.dirname(__file__), "..", "plugins")


class PluginRegistry:
    def __init__(self):
        self._registry: Dict[str, Dict] = {}

    def register(self, name: str, func: Callable, spec: Dict = None):
        self._registry[name] = {"callable": func, "spec": spec}

    def get(self, name: str):
        return self._registry.get(name)

    def all_specs(self) -> List[Dict]:
        specs = []
        for name, info in self._registry.items():
            if info.get("spec"):
                specs.append(info["spec"])
        return specs


registry = PluginRegistry()


def load_plugins(path: str = None):
    path = path or PLUGIN_DIR
    if not os.path.isdir(path):
        return
    # Import by file name
    for finder, name, ispkg in pkgutil.iter_modules([path]):
        full_name = f"plugins.{name}"
        try:
            module = importlib.import_module(full_name)
            if hasattr(module, "register"):
                module.register(registry)
        except Exception:
            # keep loader minimal and tolerant
            continue
