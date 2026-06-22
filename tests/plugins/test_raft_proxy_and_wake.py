"""Tests for the raft adapter's proxy env cleanup and wake prompt.

Verifies that:
1. ``_spawn_bridge()`` strips conflicting ``all_proxy``/``ALL_PROXY`` env vars
   from the bridge subprocess environment and sets ``NO_PROXY``.
2. ``_wake_prompt()`` includes the critical "send() is a no-op" instructions.
3. The ``register()`` platform hint includes the critical message.
"""

import os
import subprocess
from unittest.mock import MagicMock, patch

import pytest

from plugins.platforms.raft.adapter import RaftAdapter, register


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def raft_adapter():
    """Create a RaftAdapter with a mock config to avoid heavy imports."""
    mock_config = MagicMock()
    mock_config.extra = {
        "host": "127.0.0.1",
        "port": "9999",
        "path": "/raft",
        "bridge_token": "test-bridge-token-12345",
    }
    return RaftAdapter(mock_config)


# ---------------------------------------------------------------------------
# _spawn_bridge proxy env cleanup
# ---------------------------------------------------------------------------

class TestSpawnBridgeProxyCleanup:
    """Tests that _spawn_bridge strips conflicting proxy vars."""

    def test_cleans_all_proxy_env_vars(self, raft_adapter):
        """ALL_PROXY and all_proxy are removed from the spawned process env."""
        with (
            patch("plugins.platforms.raft.adapter.shutil.which", return_value="/usr/bin/raft"),
            patch("plugins.platforms.raft.adapter.subprocess.Popen") as mock_popen,
            patch.dict(os.environ, {
                "RAFT_PROFILE": "test-profile",
                "all_proxy": "socks5://127.0.0.1:7890",
                "ALL_PROXY": "socks5://127.0.0.1:7890",
                "HTTPS_PROXY": "http://127.0.0.1:7890",
            }, clear=False),
        ):
            raft_adapter._spawn_bridge(port=14273)

            assert mock_popen.called, "Popen should have been called"
            call_kwargs = mock_popen.call_args[1]
            spawned_env = call_kwargs["env"]

            # all_proxy variants must be stripped
            assert "all_proxy" not in spawned_env, (
                "all_proxy must be removed from bridge subprocess env"
            )
            assert "ALL_PROXY" not in spawned_env, (
                "ALL_PROXY must be removed from bridge subprocess env"
            )

            # NO_PROXY must be set to bypass localhost + raft API
            assert spawned_env.get("NO_PROXY") == "127.0.0.1,localhost,api.raft.build", (
                "NO_PROXY must include 127.0.0.1, localhost, and api.raft.build"
            )

    def test_preserves_other_env_vars(self, raft_adapter):
        """Non-proxy env vars (including RAFT_CHANNEL_TOKEN) are preserved."""
        with (
            patch("plugins.platforms.raft.adapter.shutil.which", return_value="/usr/bin/raft"),
            patch("plugins.platforms.raft.adapter.subprocess.Popen") as mock_popen,
            patch.dict(os.environ, {
                "RAFT_PROFILE": "test-profile",
                "PATH": "/usr/bin:/bin",
                "HOME": "/root",
                "all_proxy": "socks5://127.0.0.1:7890",
            }, clear=False),
        ):
            raft_adapter._spawn_bridge(port=14273)

            call_kwargs = mock_popen.call_args[1]
            spawned_env = call_kwargs["env"]

            # PATH and HOME must survive
            assert spawned_env.get("PATH") == "/usr/bin:/bin"
            assert spawned_env.get("HOME") == "/root"
            # RAFT_CHANNEL_TOKEN must be injected
            assert spawned_env.get("RAFT_CHANNEL_TOKEN") == "test-bridge-token-12345"

    def test_no_proxy_set_even_when_no_proxy_env(self, raft_adapter):
        """NO_PROXY is set even when there were no proxy env vars to clean."""
        with (
            patch("plugins.platforms.raft.adapter.shutil.which", return_value="/usr/bin/raft"),
            patch("plugins.platforms.raft.adapter.subprocess.Popen") as mock_popen,
            patch.dict(os.environ, {
                "RAFT_PROFILE": "test-profile",
                "PATH": "/usr/bin",
            }, clear=True),
        ):
            raft_adapter._spawn_bridge(port=14273)

            call_kwargs = mock_popen.call_args[1]
            spawned_env = call_kwargs["env"]

            assert spawned_env.get("NO_PROXY") == "127.0.0.1,localhost,api.raft.build"


# ---------------------------------------------------------------------------
# _wake_prompt
# ---------------------------------------------------------------------------

class TestWakePrompt:
    """Tests for the static _wake_prompt() method."""

    def test_contains_noop_warning(self):
        """Wake prompt warns that send() is a no-op."""
        prompt = RaftAdapter._wake_prompt()
        assert "intentionally a no-op" in prompt or "no-op" in prompt.lower(), (
            "Wake prompt must warn that send() is a no-op"
        )

    def test_contains_raft_cli_send_instruction(self):
        """Wake prompt instructs the agent to use `raft message send`."""
        prompt = RaftAdapter._wake_prompt()
        assert "raft --profile" in prompt, (
            "Wake prompt must include `raft --profile` send instruction"
        )
        assert "message send" in prompt, (
            "Wake prompt must mention `message send`"
        )

    def test_uses_raft_profile_env(self):
        """Wake prompt interpolates RAFT_PROFILE env var."""
        with patch.dict(os.environ, {"RAFT_PROFILE": "my-agent"}, clear=True):
            prompt = RaftAdapter._wake_prompt()
            assert "--profile my-agent" in prompt, (
                "Wake prompt should use the RAFT_PROFILE env var value"
            )

    def test_falls_back_to_default_profile(self):
        """Wake prompt uses 'your-agent-profile' fallback when RAFT_PROFILE is unset."""
        with patch.dict(os.environ, {}, clear=True):
            prompt = RaftAdapter._wake_prompt()
            assert "--profile your-agent-profile" in prompt, (
                "Wake prompt should use fallback profile name"
            )


# ---------------------------------------------------------------------------
# register platform_hint
# ---------------------------------------------------------------------------

class TestRegisterPlatformHint:
    """Tests that register() sets a platform hint with the no-op warning."""

    def test_platform_hint_contains_noop_warning(self):
        """register() platform_hint warns about send() being a no-op."""
        ctx = MagicMock()
        with patch.dict(os.environ, {"RAFT_PROFILE": "test-profile"}, clear=True):
            register(ctx)

        call = ctx.register_platform.call_args
        assert call is not None, "register_platform must be called"
        kwargs = call[1] if len(call) > 1 else {}

        hint = kwargs.get("platform_hint", "")
        assert "no-op" in hint.lower() or "not automatically delivered" in hint.lower(), (
            "Platform hint must warn about send() being a no-op"
        )
        assert "raft --profile" in hint, (
            "Platform hint must include raft CLI send instruction"
        )

    def test_platform_hint_interpolates_profile(self):
        """Platform hint uses RAFT_PROFILE from environment."""
        ctx = MagicMock()
        with patch.dict(os.environ, {"RAFT_PROFILE": "my-custom-profile"}, clear=True):
            register(ctx)

        call = ctx.register_platform.call_args
        kwargs = call[1] if len(call) > 1 else {}
        hint = kwargs.get("platform_hint", "")

        assert "my-custom-profile" in hint, (
            "Platform hint should contain the resolved RAFT_PROFILE value"
        )
