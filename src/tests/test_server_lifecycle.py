"""Unit tests for server lifecycle helpers: URL classification, host/port
parsing, and the auto-start logic in e2e_test_v3.run_e2e_v3.

No real OpenCode server is started — every network / subprocess dependency is
replaced via monkeypatch or mock.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from harness.server.lifecycle import is_local_url, parse_host_port, start_server


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


class TestStartServerHostname:
    def test_custom_hostname_passed_to_subprocess(self) -> None:
        with patch("harness.server.lifecycle.shutil.which", return_value="/usr/bin/opencode"):
            with patch("harness.server.lifecycle.subprocess.Popen") as mock_popen:
                mock_proc = MagicMock()
                mock_popen.return_value = mock_proc

                _ = start_server("/tmp/work", port=4098, hostname="0.0.0.0")

                mock_popen.assert_called_once()
                # pylint: disable-next=unused-variable; silent
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
            "harness.server.lifecycle.start_server",
            MagicMock(),
            raising=False,
        )
        monkeypatch.setattr(
            "harness.server.lifecycle.stop_server",
            _dummy_stop,
            raising=False,
        )

        # pylint: disable-next=import-outside-toplevel; silent
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
            "harness.server.lifecycle.find_available_port",
            find_port,
            raising=False,
        )
        monkeypatch.setattr(
            "harness.server.lifecycle.start_server",
            start_mock,
            raising=False,
        )
        monkeypatch.setattr(
            "harness.server.lifecycle.wait_for_server",
            MagicMock(return_value=True),
            raising=False,
        )

        # pylint: disable-next=import-outside-toplevel; silent
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
            "harness.server.lifecycle.find_available_port",
            find_port,
            raising=False,
        )
        monkeypatch.setattr(
            "harness.server.lifecycle.start_server",
            start_mock,
            raising=False,
        )
        monkeypatch.setattr(
            "harness.server.lifecycle.wait_for_server",
            MagicMock(return_value=True),
            raising=False,
        )

        # pylint: disable-next=import-outside-toplevel; silent
        from harness.server.lifecycle import resolve_server_url

        url, proc = resolve_server_url(  # pylint: disable=unused-variable; silent
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
            "harness.server.lifecycle.health_check",
            health_mock,
            raising=False,
        )
        monkeypatch.setattr(
            "harness.server.lifecycle.is_local_url",
            MagicMock(return_value=True),
            raising=False,
        )
        monkeypatch.setattr(
            "harness.server.lifecycle.start_server",
            start_mock,
            raising=False,
        )
        monkeypatch.setattr(
            "harness.server.lifecycle.wait_for_server",
            MagicMock(return_value=True),
            raising=False,
        )

        # pylint: disable-next=import-outside-toplevel; silent
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
        start_mock = MagicMock()
        monkeypatch.setattr(
            "harness.server.lifecycle.start_server",
            start_mock,
            raising=False,
        )

        # pylint: disable-next=import-outside-toplevel; silent
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
            "harness.server.lifecycle.start_server",
            start_mock,
            raising=False,
        )

        # pylint: disable-next=import-outside-toplevel; silent
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
            "harness.server.lifecycle.start_server",
            start_mock,
            raising=False,
        )

        # pylint: disable-next=import-outside-toplevel; silent
        from harness.server.lifecycle import resolve_server_url

        url, proc = resolve_server_url(
            "http://127.0.0.1:4098",
            auto_start=False,
            work_dir="/tmp",
        )

        assert url == "http://127.0.0.1:4098"
        assert proc is None
        start_mock.assert_not_called()
