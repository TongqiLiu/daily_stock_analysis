# -*- coding: utf-8 -*-
"""
Tests for agent_chat_stream SSE cleanup exception handling.

Verifies that:
- quiet periods emit heartbeat comments instead of a synthetic timeout error.
- client disconnects trigger cooperative cancellation.
- asyncio.CancelledError during cleanup is silently ignored (no warning).
- Other exceptions during cleanup emit a WARNING log entry.
"""

import asyncio
import os
import sys
import threading
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from tests.litellm_stub import ensure_litellm_stub

# Stub optional heavy deps before importing agent endpoint, without overriding a real install
ensure_litellm_stub()


class TestAgentSSECleanup(unittest.IsolatedAsyncioTestCase):
    """Test heartbeat, cancellation, and cleanup behavior in the SSE helper."""

    async def _run_cleanup(self, fut_exception):
        """Consume a terminal event, then exercise executor cleanup."""
        loop = asyncio.get_event_loop()
        fut = loop.create_future()
        if isinstance(fut_exception, BaseException):
            fut.set_exception(fut_exception)
        else:
            fut.set_result(None)

        import api.v1.endpoints.agent as agent_mod

        queue = asyncio.Queue()
        await queue.put({"type": "done", "success": True})
        cancel_event = threading.Event()
        events = []
        async for event in agent_mod._stream_agent_events(
            queue,
            fut,
            cancel_event,
            "test-session",
            heartbeat_seconds=0.01,
            cleanup_timeout_seconds=0.01,
        ):
            events.append(event)
        self.assertEqual(len(events), 1)
        self.assertFalse(cancel_event.is_set())

    async def test_quiet_period_emits_heartbeat_not_timeout(self):
        import api.v1.endpoints.agent as agent_mod

        loop = asyncio.get_running_loop()
        fut = loop.create_future()
        cancel_event = threading.Event()
        generator = agent_mod._stream_agent_events(
            asyncio.Queue(),
            fut,
            cancel_event,
            "heartbeat-session",
            heartbeat_seconds=0.01,
            cleanup_timeout_seconds=0.01,
        )

        event = await generator.__anext__()
        self.assertEqual(event, ": heartbeat\n\n")
        self.assertNotIn("分析超时", event)
        await generator.aclose()
        self.assertTrue(cancel_event.is_set())
        fut.set_result(None)

    async def test_terminal_event_does_not_cancel_completed_executor(self):
        import api.v1.endpoints.agent as agent_mod

        loop = asyncio.get_running_loop()
        fut = loop.create_future()
        fut.set_result(None)
        queue = asyncio.Queue()
        await queue.put({"type": "done", "success": True})
        cancel_event = threading.Event()

        events = [
            event
            async for event in agent_mod._stream_agent_events(
                queue,
                fut,
                cancel_event,
                "done-session",
                heartbeat_seconds=0.01,
                cleanup_timeout_seconds=0.01,
            )
        ]

        self.assertEqual(len(events), 1)
        self.assertIn('"type": "done"', events[0])
        self.assertFalse(cancel_event.is_set())

    async def test_cancelled_error_is_silent(self):
        """CancelledError must NOT produce a warning log."""
        import api.v1.endpoints.agent as agent_mod

        with self.assertLogs(agent_mod.logger, level="WARNING") as cm:
            # We need at least one log message for assertLogs to succeed;
            # emit a sentinel so the context manager doesn't fail on zero messages.
            agent_mod.logger.warning("sentinel")
            await self._run_cleanup(asyncio.CancelledError())

        # Only the sentinel should be present; no cleanup warning.
        self.assertEqual(len(cm.output), 1)
        self.assertIn("sentinel", cm.output[0])

    async def test_runtime_error_emits_warning(self):
        """Non-CancelledError exceptions must emit a WARNING log."""
        import api.v1.endpoints.agent as agent_mod

        with self.assertLogs(agent_mod.logger, level="WARNING") as cm:
            await self._run_cleanup(RuntimeError("simulated executor crash"))

        self.assertTrue(
            any("cleanup error" in msg for msg in cm.output),
            f"Expected 'cleanup error' in log output, got: {cm.output}",
        )

    async def test_value_error_emits_warning(self):
        """ValueError also triggers a WARNING log."""
        import api.v1.endpoints.agent as agent_mod

        with self.assertLogs(agent_mod.logger, level="WARNING") as cm:
            await self._run_cleanup(ValueError("bad value"))

        self.assertTrue(any("cleanup error" in msg for msg in cm.output))


if __name__ == "__main__":
    unittest.main()
