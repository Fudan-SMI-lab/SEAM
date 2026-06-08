"""Unit tests for server lifecycle helpers: URL classification, host/port
parsing, and the auto-start logic in e2e_test_v3.run_e2e_v3.

No real OpenCode server is started — every network / subprocess dependency is
replaced via monkeypatch or mock.
"""
from __future__ import annotations

import json
from unittest.mock import patch, MagicMock

import pytest

from harness.server.lifecycle import (
    collect_server_diagnostics,
    is_local_url,
    parse_host_port,
    start_server,
)


class TestIsLocalUrl:
    def test_localhost_http(self) -> None:
        assert is_local_url("http://localhost:4096")
        assert is_local_url("http://localhost:4096/agent")

    def test_loopback_ipv4(self) -> None:
        assert is_local_url("http://127.0.0.1:4098")
        assert is_local_url("https://127.0.0.1:4096/agent")

    def test_loopback_ipv6(self) -> None:
        assert is_local_url("http://[::1]:4096")

    def test_remote_urls(self) -> None:
        assert not is_local_url("http://10.0.0.1:4096")
        assert not is_local_url("http://192.168.1.100:5000")
        assert not is_local_url("https://example.com:443")

    def test_invalid_url_returns_false(self) -> None:
        assert not is_local_url("not-a-url")
        assert not is_local_url("")


class TestParseHostPort:
    def test_explicit_port(self) -> None:
        host, port = parse_host_port("http://127.0.0.1:4098")
        assert host == "127.0.0.1"
        assert port == 4098

    def test_implicit_port_http(self) -> None:
        host, port = parse_host_port("http://localhost")
        assert host == "localhost"
        assert port == 80

    def test_implicit_port_https(self) -> None:
        host, port = parse_host_port("https://example.com")
        assert host == "example.com"
        assert port == 443

    def test_default_port_fallback(self) -> None:
        host, port = parse_host_port("http://localhost", default_port=4096)
        assert host == "localhost"
        # Scheme is "http" so the scheme-default port (80) is used.
        assert port == 80

    def test_no_scheme_uses_default_port(self) -> None:
        host, port = parse_host_port("//127.0.0.1:5000", default_port=4096)
        assert host == "127.0.0.1"
        assert port == 5000

    def test_no_scheme_no_port_uses_default(self) -> None:
        host, port = parse_host_port("//127.0.0.1", default_port=4096)
        assert host == "127.0.0.1"
        assert port == 4096


class TestServerDiagnostics:
    def test_collects_process_config_and_openagent_models_without_secrets(
        self, tmp_path, monkeypatch,
    ) -> None:
        config_dir = tmp_path / ".opencode"
        config_dir.mkdir()
        _ = (config_dir / "opencode.jsonc").write_text(
            '{"provider": {"demo": {"apiKey": "opencode-secret"}}}',
            encoding="utf-8",
        )
        _ = (config_dir / "oh-my-openagent.json").write_text(
            """
            {
              // JSONC comments are accepted, but values are not logged.
              "agents": {
                "build": {"model": "openagent-build", "apiKey": "agent-secret"}
              },
              "categories": {
                "deep": {"model": "openagent-deep"}
              },
            }
            """,
            encoding="utf-8",
        )
        _ = (config_dir / "oh-my-opencode.json").write_text(
            json.dumps({
                "agents": {"legacy": {"model": "legacy-model"}},
                "token": "legacy-token",
            }),
            encoding="utf-8",
        )
        monkeypatch.setattr(
            "harness.server.lifecycle._find_opencode_serve_process",
            MagicMock(return_value={
                "pid": 4321,
                "cwd": str(tmp_path),
                "cmdline": ["opencode", "serve", "--port", "4098"],
            }),
        )

        diagnostics = collect_server_diagnostics(
            "http://127.0.0.1:4098",
            work_dir="/fallback/workdir",
        )

        assert diagnostics["source"] == "existing"
        assert diagnostics["pid"] == 4321
        assert diagnostics["cwd"] == str(tmp_path)
        assert diagnostics["config_base_dir"] == str(tmp_path)
        assert diagnostics["model_config_path"] == str(config_dir / "oh-my-openagent.json")
        assert diagnostics["models"] == ["openagent-build", "openagent-deep"]

        config_files = {
            item["name"]: item for item in diagnostics["config_files"]
        }
        assert config_files["opencode.jsonc"]["exists"] is True
        assert config_files["oh-my-openagent.json"]["exists"] is True
        assert config_files["oh-my-opencode.json"]["exists"] is True

        serialized = json.dumps(diagnostics)
        assert "opencode-secret" not in serialized
        assert "agent-secret" not in serialized
        assert "legacy-token" not in serialized
        assert "legacy-model" not in serialized

    def test_collects_legacy_models_when_openagent_config_missing(
        self, tmp_path, monkeypatch,
    ) -> None:
        config_dir = tmp_path / ".opencode"
        config_dir.mkdir()
        _ = (config_dir / "oh-my-opencode.json").write_text(
            json.dumps({
                "agents": {"qa": {"model": "legacy-qa"}},
                "categories": {"quick": {"model": "legacy-quick"}},
                "secret": "hidden-value",
            }),
            encoding="utf-8",
        )
        monkeypatch.setattr(
            "harness.server.lifecycle._find_opencode_serve_process",
            MagicMock(return_value=None),
        )

        diagnostics = collect_server_diagnostics(
            "http://localhost:4096",
            work_dir=str(tmp_path),
        )

        assert diagnostics["model_config_path"] == str(config_dir / "oh-my-opencode.json")
        assert diagnostics["models"] == ["legacy-qa", "legacy-quick"]
        assert "hidden-value" not in json.dumps(diagnostics)

    def test_auto_started_process_uses_pid_and_work_dir_fallback(
        self, tmp_path, monkeypatch,
    ) -> None:
        config_dir = tmp_path / ".opencode"
        config_dir.mkdir()
        _ = (config_dir / "oh-my-openagent.json").write_text(
            json.dumps({"agents": {"build": {"model": "auto-model"}}}),
            encoding="utf-8",
        )
        proc = MagicMock()
        proc.pid = 9876
        monkeypatch.setattr(
            "harness.server.lifecycle._process_info_from_pid",
            MagicMock(return_value={"pid": 9876, "cwd": None, "cmdline": []}),
        )

        diagnostics = collect_server_diagnostics(
            "http://127.0.0.1:4097",
            server_proc=proc,
            work_dir=str(tmp_path),
        )

        assert diagnostics["source"] == "auto-started"
        assert diagnostics["pid"] == 9876
        assert diagnostics["cwd"] is None
        assert diagnostics["config_base_dir"] == str(tmp_path)
        assert diagnostics["models"] == ["auto-model"]


class TestStartServerHostname:
    def test_custom_hostname_passed_to_subprocess(self) -> None:
        with patch("harness.server.lifecycle.shutil.which", return_value="/usr/bin/opencode"):
            with patch("harness.server.lifecycle.subprocess.Popen") as mock_popen:
                mock_proc = MagicMock()
                mock_popen.return_value = mock_proc

                _ = start_server("/tmp/work", port=4098, hostname="0.0.0.0")

                mock_popen.assert_called_once()
                call_args, call_kwargs = mock_popen.call_args
                cmd = call_args[0]
                assert "--hostname" in cmd
                idx = cmd.index("--hostname")
                assert cmd[idx + 1] == "0.0.0.0"
                assert "--port" in cmd
                idx = cmd.index("--port")
                assert cmd[idx + 1] == "4098"

    def test_default_hostname_is_loopback(self) -> None:
        with patch("harness.server.lifecycle.shutil.which", return_value="/usr/bin/opencode"):
            with patch("harness.server.lifecycle.subprocess.Popen") as mock_popen:
                mock_proc = MagicMock()
                mock_popen.return_value = mock_proc

                _ = start_server("/tmp/work", port=4096)

                call_args, _ = mock_popen.call_args
                cmd = call_args[0]
                idx = cmd.index("--hostname")
                assert cmd[idx + 1] == "127.0.0.1"


# ── Tests for resolve_server_url (the core auto-start logic) ──


def _dummy_stop(_proc: object) -> int:
    return 0


class TestResolveServerUrl:
    """Test resolve_server_url in isolation — all side effects mocked."""

    def test_none_url_auto_start_off_uses_default(self, monkeypatch) -> None:
        monkeypatch.setattr(
            "harness.server.lifecycle.start_server", MagicMock(),
            raising=False,
        )
        monkeypatch.setattr(
            "harness.server.lifecycle.stop_server", _dummy_stop,
            raising=False,
        )

        from harness.server.lifecycle import resolve_server_url

        url, proc = resolve_server_url(
            None,
            auto_start=False,
            default_url="http://127.0.0.1:4096",
            work_dir="/tmp",
        )

        assert url == "http://127.0.0.1:4096"
        assert proc is None

    def test_none_url_auto_start_on_starts_server(self, monkeypatch) -> None:
        find_port = MagicMock(return_value=4097)
        mock_proc = MagicMock()
        start_mock = MagicMock(return_value=mock_proc)
        monkeypatch.setattr(
            "harness.server.lifecycle.find_available_port", find_port,
            raising=False,
        )
        monkeypatch.setattr(
            "harness.server.lifecycle.start_server", start_mock,
            raising=False,
        )
        monkeypatch.setattr(
            "harness.server.lifecycle.wait_for_server",
            MagicMock(return_value=True),
            raising=False,
        )
        monkeypatch.setattr(
            "harness.server.lifecycle.check_session_capable",
            MagicMock(return_value=True),
            raising=False,
        )

        from harness.server.lifecycle import resolve_server_url

        url, proc = resolve_server_url(
            None,
            auto_start=True,
            default_url="http://127.0.0.1:4096",
            work_dir="/tmp",
        )

        assert url == "http://127.0.0.1:4097"
        assert proc is mock_proc
        start_mock.assert_called_once_with(work_dir="/tmp", port=4097)

    def test_none_url_auto_start_server_port_overrides(self, monkeypatch) -> None:
        find_port = MagicMock(return_value=4097)
        mock_proc = MagicMock()
        start_mock = MagicMock(return_value=mock_proc)
        monkeypatch.setattr(
            "harness.server.lifecycle.find_available_port", find_port,
            raising=False,
        )
        monkeypatch.setattr(
            "harness.server.lifecycle.start_server", start_mock,
            raising=False,
        )
        monkeypatch.setattr(
            "harness.server.lifecycle.wait_for_server",
            MagicMock(return_value=True),
            raising=False,
        )
        monkeypatch.setattr(
            "harness.server.lifecycle.check_session_capable",
            MagicMock(return_value=True),
            raising=False,
        )

        from harness.server.lifecycle import resolve_server_url

        url, proc = resolve_server_url(
            None,
            auto_start=True,
            work_dir="/tmp",
            server_port=5000,
        )

        assert url == "http://127.0.0.1:5000"
        start_mock.assert_called_once_with(work_dir="/tmp", port=5000)

    def test_local_url_unreachable_triggers_auto_start(self, monkeypatch) -> None:
        health_mock = MagicMock(return_value=False)
        mock_proc = MagicMock()
        start_mock = MagicMock(return_value=mock_proc)
        monkeypatch.setattr(
            "harness.server.lifecycle.health_check", health_mock,
            raising=False,
        )
        monkeypatch.setattr(
            "harness.server.lifecycle.is_local_url",
            MagicMock(return_value=True),
            raising=False,
        )
        monkeypatch.setattr(
            "harness.server.lifecycle.start_server", start_mock,
            raising=False,
        )
        monkeypatch.setattr(
            "harness.server.lifecycle.wait_for_server",
            MagicMock(return_value=True),
            raising=False,
        )
        monkeypatch.setattr(
            "harness.server.lifecycle.check_session_capable",
            MagicMock(return_value=True),
            raising=False,
        )

        from harness.server.lifecycle import resolve_server_url

        url, proc = resolve_server_url(
            "http://127.0.0.1:4098",
            auto_start=True,
            work_dir="/tmp",
        )

        assert url == "http://127.0.0.1:4098"
        assert proc is mock_proc
        health_mock.assert_called_once_with("http://127.0.0.1:4098/agent")
        start_mock.assert_called_once()
        call_kwargs = start_mock.call_args.kwargs
        assert call_kwargs.get("hostname") == "127.0.0.1"
        assert call_kwargs.get("port") == 4098

    def test_local_url_reachable_skips_auto_start(self, monkeypatch) -> None:
        monkeypatch.setattr(
            "harness.server.lifecycle.health_check",
            MagicMock(return_value=True),
            raising=False,
        )
        monkeypatch.setattr(
            "harness.server.lifecycle.check_session_capable",
            MagicMock(return_value=True),
            raising=False,
        )
        start_mock = MagicMock()
        monkeypatch.setattr(
            "harness.server.lifecycle.start_server", start_mock,
            raising=False,
        )

        from harness.server.lifecycle import resolve_server_url

        url, proc = resolve_server_url(
            "http://127.0.0.1:4098",
            auto_start=True,
            work_dir="/tmp",
        )

        assert url == "http://127.0.0.1:4098"
        assert proc is None
        start_mock.assert_not_called()

    def test_remote_url_unreachable_raises(self, monkeypatch) -> None:
        monkeypatch.setattr(
            "harness.server.lifecycle.health_check",
            MagicMock(return_value=False),
            raising=False,
        )
        monkeypatch.setattr(
            "harness.server.lifecycle.is_local_url",
            MagicMock(return_value=False),
            raising=False,
        )
        start_mock = MagicMock()
        monkeypatch.setattr(
            "harness.server.lifecycle.start_server", start_mock,
            raising=False,
        )

        from harness.server.lifecycle import resolve_server_url

        with pytest.raises(RuntimeError, match="remote"):
            resolve_server_url(
                "http://10.0.0.1:4096",
                auto_start=True,
                work_dir="/tmp",
            )

        start_mock.assert_not_called()

    def test_auto_start_off_does_not_start(self, monkeypatch) -> None:
        start_mock = MagicMock()
        monkeypatch.setattr(
            "harness.server.lifecycle.start_server", start_mock,
            raising=False,
        )

        from harness.server.lifecycle import resolve_server_url

        url, proc = resolve_server_url(
            "http://127.0.0.1:4098",
            auto_start=False,
            work_dir="/tmp",
        )

        assert url == "http://127.0.0.1:4098"
        assert proc is None
        start_mock.assert_not_called()


# ── Session-capable validation tests ──


class TestSessionCapableValidation:
    """Test that resolve_server_url validates session capability
    and handles /agent-OK-but-session-broken scenarios."""

    def test_session_capable_server_skips_auto_start(self, monkeypatch) -> None:
        """A server that passes both /agent and POST /session should
        return without starting a new process."""
        monkeypatch.setattr(
            "harness.server.lifecycle.health_check",
            MagicMock(return_value=True),
        )
        monkeypatch.setattr(
            "harness.server.lifecycle.check_session_capable",
            MagicMock(return_value=True),
        )
        start_mock = MagicMock()
        monkeypatch.setattr(
            "harness.server.lifecycle.start_server", start_mock,
        )

        from harness.server.lifecycle import resolve_server_url

        url, proc = resolve_server_url(
            "http://127.0.0.1:4098",
            auto_start=True,
            work_dir="/tmp",
        )

        assert url == "http://127.0.0.1:4098"
        assert proc is None
        start_mock.assert_not_called()

    def test_agent_true_session_false_local_fails_fast(
        self, monkeypatch,
    ) -> None:
        """When /agent passes but POST /session fails on a local URL,
        resolve_server_url must raise RuntimeError without restarting —
        a process is already bound to that port."""
        monkeypatch.setattr(
            "harness.server.lifecycle.health_check",
            MagicMock(return_value=True),
        )
        monkeypatch.setattr(
            "harness.server.lifecycle.check_session_capable",
            MagicMock(return_value=False),
        )
        monkeypatch.setattr(
            "harness.server.lifecycle.is_local_url",
            MagicMock(return_value=True),
        )
        start_mock = MagicMock()
        monkeypatch.setattr(
            "harness.server.lifecycle.start_server", start_mock,
        )

        from harness.server.lifecycle import resolve_server_url

        with pytest.raises(RuntimeError, match="unsafe"):
            resolve_server_url(
                "http://127.0.0.1:4098",
                auto_start=True,
                work_dir="/tmp",
            )

        start_mock.assert_not_called()

    def test_agent_true_session_false_remote_raises(
        self, monkeypatch,
    ) -> None:
        """When /agent passes but POST /session fails on a remote URL,
        resolve_server_url must raise RuntimeError without starting."""
        monkeypatch.setattr(
            "harness.server.lifecycle.health_check",
            MagicMock(return_value=True),
        )
        monkeypatch.setattr(
            "harness.server.lifecycle.check_session_capable",
            MagicMock(return_value=False),
        )
        monkeypatch.setattr(
            "harness.server.lifecycle.is_local_url",
            MagicMock(return_value=False),
        )
        start_mock = MagicMock()
        monkeypatch.setattr(
            "harness.server.lifecycle.start_server", start_mock,
        )

        from harness.server.lifecycle import resolve_server_url

        with pytest.raises(RuntimeError, match="POST /session failed"):
            resolve_server_url(
                "http://10.0.0.1:4096",
                auto_start=True,
                work_dir="/tmp",
            )

        start_mock.assert_not_called()

    def test_none_url_auto_start_session_fails_raises(
        self, monkeypatch,
    ) -> None:
        """When a fresh server starts but session check fails,
        RuntimeError is raised."""
        monkeypatch.setattr(
            "harness.server.lifecycle.find_available_port",
            MagicMock(return_value=4097),
        )
        monkeypatch.setattr(
            "harness.server.lifecycle.start_server",
            MagicMock(),
        )
        monkeypatch.setattr(
            "harness.server.lifecycle.wait_for_server",
            MagicMock(return_value=True),
        )
        monkeypatch.setattr(
            "harness.server.lifecycle.check_session_capable",
            MagicMock(return_value=False),
        )
        monkeypatch.setattr(
            "harness.server.lifecycle.stop_server", _dummy_stop,
        )

        from harness.server.lifecycle import resolve_server_url

        with pytest.raises(RuntimeError, match="POST /session failed"):
            resolve_server_url(
                None,
                auto_start=True,
                work_dir="/tmp",
            )

    def test_local_url_unreachable_still_triggers_restart(
        self, monkeypatch,
    ) -> None:
        """When /agent is unreachable on a local URL, auto-start still
        launches a new server.  This path is safe because the port is
        not occupied."""
        health_mock = MagicMock(return_value=False)
        start_mock = MagicMock()
        monkeypatch.setattr(
            "harness.server.lifecycle.health_check", health_mock,
            raising=False,
        )
        monkeypatch.setattr(
            "harness.server.lifecycle.is_local_url",
            MagicMock(return_value=True),
            raising=False,
        )
        monkeypatch.setattr(
            "harness.server.lifecycle.start_server", start_mock,
            raising=False,
        )
        monkeypatch.setattr(
            "harness.server.lifecycle.wait_for_server",
            MagicMock(return_value=True),
            raising=False,
        )
        monkeypatch.setattr(
            "harness.server.lifecycle.check_session_capable",
            MagicMock(return_value=True),
            raising=False,
        )

        from harness.server.lifecycle import resolve_server_url

        url, proc = resolve_server_url(
            "http://127.0.0.1:4098",
            auto_start=True,
            work_dir="/tmp",
        )

        assert url == "http://127.0.0.1:4098"
        assert proc is start_mock.return_value
        start_mock.assert_called_once()


# ── Session probe cleanup tests ──


class TestSessionProbeCleanup:
    """Verify that session probes clean up after themselves."""

    def test_session_probe_details_cleans_up_on_success(
        self, monkeypatch,
    ) -> None:
        """_session_probe_details must send DELETE /session/{id} after a
        successful POST /session probe, so health-check sessions are not
        leaked into the server."""
        requests_log: list[tuple[str, str]] = []

        class _ProbeResp:
            status = 200

            @staticmethod
            def read():
                return json.dumps(
                    {"ok": True, "data": {"id": "ses_probe_abc"}}
                ).encode()

        class _ProbeConn:
            def __init__(self, host, port, timeout):
                pass

            def request(self, method, url, body=None, headers=None):
                requests_log.append((method, url))

            def getresponse(self):
                return _ProbeResp

            def close(self):
                pass

        monkeypatch.setattr("http.client.HTTPConnection", _ProbeConn)

        from harness.server.lifecycle import _session_probe_details

        ok, status_val, _body = _session_probe_details(
            "http://127.0.0.1:4098",
        )

        assert ok is True
        assert status_val == 200

        post_calls = [r for r in requests_log if r[0] == "POST"]
        delete_calls = [r for r in requests_log if r[0] == "DELETE"]
        assert len(post_calls) == 1, "expected one POST /session"
        assert post_calls[0][1] == "/session"
        assert len(delete_calls) == 1, (
            "expected DELETE /session/{id} to clean up the probe"
        )
        assert delete_calls[0][1] == "/session/ses_probe_abc"

    def test_check_session_capable_cleans_up_on_success(
        self, monkeypatch,
    ) -> None:
        """check_session_capable (the public helper) must also clean up
        the session it creates during probing."""
        requests_log: list[tuple[str, str]] = []

        class _Resp:
            status = 200

            @staticmethod
            def read():
                return json.dumps(
                    {"ok": True, "data": {"id": "ses_abc"}}
                ).encode()

        class _Conn:
            def __init__(self, host, port, timeout):
                pass

            def request(self, method, url, body=None, headers=None):
                requests_log.append((method, url))

            def getresponse(self):
                return _Resp

            def close(self):
                pass

        monkeypatch.setattr("http.client.HTTPConnection", _Conn)

        from harness.server.lifecycle import check_session_capable

        result = check_session_capable("http://127.0.0.1:4098")

        assert result is True

        delete_calls = [r for r in requests_log if r[0] == "DELETE"]
        assert len(delete_calls) >= 1, (
            "check_session_capable must DELETE the probe session"
        )
        assert delete_calls[0][1] == "/session/ses_abc"

    def test_session_probe_details_no_cleanup_on_failure(
        self, monkeypatch,
    ) -> None:
        """On a failed POST /session (HTTP 500), no DELETE should be
        attempted — there is nothing to clean up."""
        requests_log: list[tuple[str, str]] = []

        class _FailResp:
            status = 500

            @staticmethod
            def read():
                return b'{"error": "internal"}'

        class _FailConn:
            def __init__(self, host, port, timeout):
                pass

            def request(self, method, url, body=None, headers=None):
                requests_log.append((method, url))

            def getresponse(self):
                return _FailResp

            def close(self):
                pass

        monkeypatch.setattr("http.client.HTTPConnection", _FailConn)

        from harness.server.lifecycle import _session_probe_details

        ok, status_val, _body = _session_probe_details(
            "http://127.0.0.1:4098",
        )

        assert ok is False
        assert status_val == 500

        delete_calls = [r for r in requests_log if r[0] == "DELETE"]
        assert len(delete_calls) == 0, (
            "no DELETE should be sent when POST /session fails"
        )
