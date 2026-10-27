import importlib
import pkgutil
import os
from typing import Dict, Callable, List

PLUGIN_DIR = os.path.join(os.path.dirname(__file__), "..", "plugins")


class PluginRegistry:
    def __init__(self):
        self._registry: Dict[str, Dict] = {}

    def register(
        self, name: str, func: Callable, spec: Dict = None,
        needs_chat: bool = False, needs_group: bool = False, owner_only: bool = False,
    ):
        """needs_chat: the tool acts on the chat itself (reminders do), so
        it's only offered to requests that say which chat they're for.
        needs_group: only offered in a group chat (take_to_dm).
        owner_only: only usable by requests marked as from the owner
        (run_python, ask_claude, read_chat). Still listed for everyone in a
        chat - so the prompt is the same whoever speaks in a group, and the
        model server's prompt cache holds - but refused when someone else's
        request calls it (see tools.run_tool_calls)."""
        self._registry[name] = {
            "callable": func, "spec": spec, "needs_chat": needs_chat or needs_group, "needs_group": needs_group,
            "owner_only": owner_only,
        }

    def needs_group(self, name: str) -> bool:
        return bool((self._registry.get(name) or {}).get("needs_group"))

    def needs_chat(self, name: str) -> bool:
        return bool((self._registry.get(name) or {}).get("needs_chat"))

    def owner_only(self, name: str) -> bool:
        return bool((self._registry.get(name) or {}).get("owner_only"))

    def get(self, name: str):
        return self._registry.get(name)

    def all_specs(self) -> List[Dict]:
        specs = []
        for name, info in self._registry.items():
            if info.get("spec"):
                specs.append(info["spec"])
        return specs


registry = PluginRegistry()


def load_plugins(path: str = None, app=None):
    """Import every module in plugins/: each may expose register(registry)
    to add model tools, and/or register_routes(app) to add HTTP endpoints
    (e.g. the optional voice plugin's /v1/transcribe and /v1/speak)."""
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
            if app is not None and hasattr(module, "register_routes"):
                module.register_routes(app)
        except Exception as exc:
            # Keep the loader tolerant - one broken plugin (e.g. a missing
            # dependency) shouldn't take down the whole server - but never
            # silent. A swallowed exception here previously meant a plugin
            # could vanish with zero indication anywhere why.
            print(f"[plugins] failed to load '{full_name}': {exc}")
            continue
