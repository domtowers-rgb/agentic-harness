import importlib
import socket
import sys

import pytest


class TestCalculator:
    def test_basic_arithmetic(self):
        from plugins.calculator import calculate
        assert calculate("2 * (3 + 4)") == {"result": 14}

    def test_functions_and_constants(self):
        from plugins.calculator import calculate
        assert calculate("sqrt(16)") == {"result": 4.0}

    def test_rejects_code_injection(self):
        from plugins.calculator import calculate
        result = calculate('__import__("os").system("echo pwned")')
        assert "error" in result

    def test_rejects_attribute_access(self):
        from plugins.calculator import calculate
        result = calculate("(1).__class__")
        assert "error" in result

    def test_rejects_garbage_input(self):
        from plugins.calculator import calculate
        result = calculate("not a valid expression $$$")
        assert "error" in result


class TestCurrentTime:
    def test_defaults_to_utc(self):
        from plugins.current_time import get_current_time
        result = get_current_time()
        assert result["timezone"] == "UTC"
        assert "T" in result["iso"]

    def test_named_timezone(self):
        from plugins.current_time import get_current_time
        result = get_current_time("Europe/London")
        assert result["timezone"] == "Europe/London"
        assert "error" not in result

    def test_unknown_timezone_errors_gracefully(self):
        from plugins.current_time import get_current_time
        result = get_current_time("Not/AZone")
        assert "error" in result


class TestFileOps:
    @pytest.fixture(autouse=True)
    def _sandbox(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AGENTIC_FILES_DIR", str(tmp_path))
        import plugins.file_ops as file_ops
        importlib.reload(file_ops)
        self.file_ops = file_ops
        yield

    def test_write_then_read(self):
        self.file_ops.write_file("note.txt", "hello")
        assert self.file_ops.read_file("note.txt") == {"content": "hello", "truncated": False}

    def test_list_files(self):
        self.file_ops.write_file("a.txt", "x")
        result = self.file_ops.list_files()
        assert "a.txt" in result["entries"]

    def test_write_creates_parent_dirs(self):
        result = self.file_ops.write_file("nested/dir/note.txt", "hi")
        assert result["status"] == "written"
        assert self.file_ops.read_file("nested/dir/note.txt")["content"] == "hi"

    def test_read_missing_file(self):
        result = self.file_ops.read_file("nope.txt")
        assert "error" in result

    def test_list_missing_dir(self):
        result = self.file_ops.list_files("nope")
        assert "error" in result

    @pytest.mark.parametrize("path", ["../../../etc/passwd", "/etc/passwd"])
    def test_path_escape_rejected(self, path):
        # These use "/" as the separator, so they're meaningful traversal
        # attempts on every platform.
        result = self.file_ops.read_file(path)
        assert "error" in result
        assert "sandbox" in result["error"]

    @pytest.mark.skipif(sys.platform != "win32", reason="backslash is only a path separator on Windows")
    def test_windows_backslash_escape_rejected(self):
        # On POSIX this string is just an inert literal filename (backslash
        # isn't a separator there), so it correctly stays inside the sandbox
        # and hits "no such file" instead - not a security gap, just not a
        # meaningful traversal attempt on that platform.
        result = self.file_ops.read_file("..\\..\\secret.txt")
        assert "error" in result
        assert "sandbox" in result["error"]

    def test_write_too_large_rejected(self, monkeypatch):
        monkeypatch.setattr(self.file_ops, "MAX_WRITE_CHARS", 10)
        result = self.file_ops.write_file("big.txt", "x" * 11)
        assert "error" in result


class TestFetchUrl:
    def test_rejects_bad_scheme(self):
        from plugins.fetch_url import fetch_url
        assert "error" in fetch_url("ftp://example.com")

    def test_rejects_no_hostname(self):
        from plugins.fetch_url import fetch_url
        assert "error" in fetch_url("http://")

    def test_rejects_loopback(self):
        from plugins.fetch_url import fetch_url
        assert "error" in fetch_url("http://127.0.0.1:9/")

    def test_rejects_link_local_metadata_address(self):
        from plugins.fetch_url import fetch_url
        assert "error" in fetch_url("http://169.254.169.254/latest/meta-data/")

    def test_rejects_unresolvable_host(self):
        from plugins.fetch_url import fetch_url
        result = fetch_url("https://this-domain-should-not-exist-xyz123.invalid")
        assert "error" in result

    def test_fetches_real_public_url(self):
        from plugins.fetch_url import fetch_url
        result = fetch_url("https://example.com")
        if "error" in result:
            pytest.skip(f"network unavailable: {result['error']}")
        assert result["status"] == 200
        assert len(result["content"]) > 0

    def test_dns_resolved_only_once(self, monkeypatch):
        calls = []
        real_getaddrinfo = socket.getaddrinfo

        def spy(host, *args, **kwargs):
            calls.append(host)
            return real_getaddrinfo(host, *args, **kwargs)

        monkeypatch.setattr(socket, "getaddrinfo", spy)
        from plugins.fetch_url import fetch_url
        result = fetch_url("https://example.com")
        if "error" in result:
            pytest.skip(f"network unavailable: {result['error']}")
        assert calls.count("example.com") == 1


class TestWebSearch:
    def test_missing_api_key_reports_error(self, monkeypatch):
        monkeypatch.delenv("BRAVE_API_KEY", raising=False)
        from plugins.web_search import web_search
        result = web_search("test query")
        assert "error" in result
        assert "BRAVE_API_KEY" in result["error"]


class TestShellExec:
    def _reload(self, monkeypatch, enabled, files_dir):
        if enabled:
            monkeypatch.setenv("AGENTIC_ENABLE_SHELL", "1")
        else:
            monkeypatch.delenv("AGENTIC_ENABLE_SHELL", raising=False)
        monkeypatch.setenv("AGENTIC_FILES_DIR", str(files_dir))
        import plugins.shell_exec as shell_exec
        return importlib.reload(shell_exec)

    def test_disabled_by_default(self, monkeypatch, tmp_path):
        module = self._reload(monkeypatch, enabled=False, files_dir=tmp_path)
        registered = {}

        class FakeRegistry:
            def register(self, name, func, spec):
                registered[name] = func

        module.register(FakeRegistry())
        assert registered == {}

    def test_enabled_via_env_var(self, monkeypatch, tmp_path):
        module = self._reload(monkeypatch, enabled=True, files_dir=tmp_path)
        registered = {}

        class FakeRegistry:
            def register(self, name, func, spec):
                registered[name] = func

        module.register(FakeRegistry())
        assert "run_command" in registered
        result = registered["run_command"]("echo hi")
        assert result["exit_code"] == 0
        assert "hi" in result["stdout"]

    def test_empty_command_rejected(self, monkeypatch, tmp_path):
        module = self._reload(monkeypatch, enabled=True, files_dir=tmp_path)
        result = module.run_command("   ")
        assert "error" in result

    def test_unknown_command_reports_error(self, monkeypatch, tmp_path):
        module = self._reload(monkeypatch, enabled=True, files_dir=tmp_path)
        result = module.run_command("this-command-should-not-exist-xyz")
        assert "error" in result
