from agentic_harness import plugins as plugins_mod


def test_load_plugins_logs_and_continues_on_failure(capsys, monkeypatch):
    """A plugin that fails to import (e.g. a missing dependency) must not
    crash the loader, and must leave a visible trace - previously this was
    swallowed silently, so a broken plugin would just vanish with no
    indication anywhere why."""
    real_import_module = plugins_mod.importlib.import_module

    def fake_import(name):
        if name == "plugins.totally_broken_plugin":
            raise ModuleNotFoundError("No module named 'pptx'")
        return real_import_module(name)

    monkeypatch.setattr(plugins_mod.importlib, "import_module", fake_import)

    real_iter_modules = plugins_mod.pkgutil.iter_modules

    def fake_iter_modules(paths):
        yield from real_iter_modules(paths)
        yield (None, "totally_broken_plugin", False)

    monkeypatch.setattr(plugins_mod.pkgutil, "iter_modules", fake_iter_modules)

    plugins_mod.load_plugins()

    captured = capsys.readouterr()
    assert "totally_broken_plugin" in captured.out
    assert "No module named 'pptx'" in captured.out
    # real plugins still loaded fine despite the injected failure
    assert plugins_mod.registry.get("calculate") is not None
