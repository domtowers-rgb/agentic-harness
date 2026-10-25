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
    assert plugins_mod.registry.get("get_current_time") is not None


def test_plugins_can_add_endpoints(tmp_path, monkeypatch):
    import sys
    import types
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from agentic_harness import plugins as loader

    # The loader lists the folder, then imports "plugins.<name>" - so
    # stand the module in under that name.
    (tmp_path / "pinger.py").write_text("")
    pinger = types.ModuleType("plugins.pinger")
    pinger.register_routes = lambda app: app.add_api_route("/v1/ping", lambda: {"pong": True}, methods=["GET"])
    monkeypatch.setitem(sys.modules, "plugins.pinger", pinger)

    app = FastAPI()
    loader.load_plugins(str(tmp_path), app=app)
    assert TestClient(app).get("/v1/ping").json() == {"pong": True}

def test_missing_voice_libraries_skip_the_plugin(monkeypatch, capsys):
    """Without the voice libraries, the plugin fails to import - so the
    loader skips it, logs why, and its endpoints never exist."""
    import importlib.util
    import sys
    real_find_spec = importlib.util.find_spec
    monkeypatch.setattr(importlib.util, "find_spec", lambda name, *a, **k: None if name == "piper" else real_find_spec(name, *a, **k))
    monkeypatch.delitem(sys.modules, "plugins.voice", raising=False)

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from agentic_harness import plugins as loader
    app = FastAPI()
    loader.load_plugins(app=app)

    assert "failed to load 'plugins.voice'" in capsys.readouterr().out
    assert TestClient(app).post("/v1/speak", json={"text": "hi"}).status_code == 404
