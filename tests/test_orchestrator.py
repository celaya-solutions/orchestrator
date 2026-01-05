# ABOUTME: Test suite for Ralph Orchestrator core functionality
# ABOUTME: Validates orchestration loop, safety mechanisms, and metrics

"""Tests for Ralph Orchestrator."""

import asyncio
import unittest
from unittest.mock import patch, MagicMock
from pathlib import Path
import tempfile

from ralph_orchestrator.orchestrator import RalphOrchestrator
from ralph_orchestrator.metrics import Metrics, CostTracker
from ralph_orchestrator.safety import SafetyGuard
from ralph_orchestrator.context import ContextManager


class TestMetrics(unittest.TestCase):
    """Test metrics tracking."""
    
    def test_metrics_initialization(self):
        """Test metrics initialization."""
        metrics = Metrics()
        
        self.assertEqual(metrics.iterations, 0)
        self.assertEqual(metrics.successful_iterations, 0)
        self.assertEqual(metrics.failed_iterations, 0)
        self.assertEqual(metrics.errors, 0)
    
    def test_success_rate_calculation(self):
        """Test success rate calculation."""
        metrics = Metrics()
        
        # Test with no iterations
        self.assertEqual(metrics.success_rate(), 0.0)
        
        # Test with some successes and failures
        metrics.successful_iterations = 8
        metrics.failed_iterations = 2
        self.assertEqual(metrics.success_rate(), 0.8)
    
    def test_metrics_to_dict(self):
        """Test converting metrics to dictionary."""
        metrics = Metrics()
        metrics.iterations = 10
        metrics.successful_iterations = 8
        
        data = metrics.to_dict()
        self.assertEqual(data["iterations"], 10)
        self.assertEqual(data["successful_iterations"], 8)
        self.assertIn("elapsed_hours", data)
        self.assertIn("success_rate", data)


class TestCostTracker(unittest.TestCase):
    """Test cost tracking."""
    
    def test_cost_tracker_initialization(self):
        """Test cost tracker initialization."""
        tracker = CostTracker()
        
        self.assertEqual(tracker.total_cost, 0.0)
        self.assertEqual(len(tracker.costs_by_tool), 0)
        self.assertEqual(len(tracker.usage_history), 0)
    
    def test_add_usage_claude(self):
        """Test adding Claude usage."""
        tracker = CostTracker()
        
        # Add 1000 input tokens and 500 output tokens
        cost = tracker.add_usage("claude", 1000, 500)
        
        # Claude costs: $0.003 per 1K input, $0.015 per 1K output
        expected_cost = (1000/1000) * 0.003 + (500/1000) * 0.015
        self.assertAlmostEqual(cost, expected_cost, places=5)
        self.assertAlmostEqual(tracker.total_cost, expected_cost, places=5)
        self.assertIn("claude", tracker.costs_by_tool)
    
    def test_add_usage_free_tier(self):
        """Test adding usage for free tools."""
        tracker = CostTracker()
        
        cost = tracker.add_usage("ollama", 10000, 5000)
        
        self.assertEqual(cost, 0.0)
        self.assertEqual(tracker.total_cost, 0.0)
    
    def test_get_summary(self):
        """Test getting cost summary."""
        tracker = CostTracker()
        tracker.add_usage("claude", 1000, 500)
        tracker.add_usage("gemini", 1000, 500)
        
        summary = tracker.get_summary()
        self.assertIn("total_cost", summary)
        self.assertIn("costs_by_tool", summary)
        self.assertEqual(summary["usage_count"], 2)


class TestSafetyGuard(unittest.TestCase):
    """Test safety mechanisms."""
    
    def test_safety_guard_initialization(self):
        """Test safety guard initialization."""
        guard = SafetyGuard(
            max_iterations=50,
            max_runtime=3600,
            max_cost=5.0
        )
        
        self.assertEqual(guard.max_iterations, 50)
        self.assertEqual(guard.max_runtime, 3600)
        self.assertEqual(guard.max_cost, 5.0)
    
    def test_iteration_limit_check(self):
        """Test iteration limit checking."""
        guard = SafetyGuard(max_iterations=10)
        
        # Within limit
        result = guard.check(5, 100, 1.0)
        self.assertTrue(result.passed)
        
        # At limit
        result = guard.check(10, 100, 1.0)
        self.assertFalse(result.passed)
        self.assertIn("iterations", result.reason)
    
    def test_runtime_limit_check(self):
        """Test runtime limit checking."""
        guard = SafetyGuard(max_runtime=3600)
        
        # Within limit
        result = guard.check(5, 1800, 1.0)
        self.assertTrue(result.passed)
        
        # Over limit
        result = guard.check(5, 3700, 1.0)
        self.assertFalse(result.passed)
        self.assertIn("runtime", result.reason)
    
    def test_cost_limit_check(self):
        """Test cost limit checking."""
        guard = SafetyGuard(max_cost=5.0)
        
        # Within limit
        result = guard.check(5, 100, 2.5)
        self.assertTrue(result.passed)
        
        # Over limit
        result = guard.check(5, 100, 5.5)
        self.assertFalse(result.passed)
        self.assertIn("cost", result.reason)
    
    def test_consecutive_failure_tracking(self):
        """Test consecutive failure tracking."""
        guard = SafetyGuard(consecutive_failure_limit=3)
        
        # Record some failures
        guard.record_failure()
        guard.record_failure()
        
        # Still within limit
        result = guard.check(1, 100, 1.0)
        self.assertTrue(result.passed)
        
        # Hit the limit
        guard.record_failure()
        result = guard.check(1, 100, 1.0)
        self.assertFalse(result.passed)
        self.assertIn("failures", result.reason)
        
        # Success resets counter
        guard.record_success()
        result = guard.check(1, 100, 1.0)
        self.assertTrue(result.passed)


class TestContextManager(unittest.TestCase):
    """Test context management."""
    
    def test_context_manager_initialization(self):
        """Test context manager initialization."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.md', delete=False) as f:
            f.write("# Test Prompt\n\nThis is a test.")
            prompt_file = Path(f.name)
        
        try:
            manager = ContextManager(prompt_file)
            self.assertIsNotNone(manager.stable_prefix)
        finally:
            prompt_file.unlink()
    
    def test_context_summarization(self):
        """Test context summarization."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.md', delete=False) as f:
            f.write("# Task\n" + "x" * 10000)  # Large content
            prompt_file = Path(f.name)
        
        try:
            manager = ContextManager(prompt_file, max_context_size=1000)
            prompt = manager.get_prompt()
            
            # Should be summarized to fit within limit
            self.assertLess(len(prompt), 1100)  # Some margin for metadata
        finally:
            prompt_file.unlink()
    
    def test_error_tracking(self):
        """Test error feedback tracking."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.md', delete=False) as f:
            f.write("# Test")
            prompt_file = Path(f.name)
        
        try:
            manager = ContextManager(prompt_file)
            
            # Add some errors
            manager.add_error_feedback("Connection timeout")
            manager.add_error_feedback("API rate limit")
            
            # Check errors are tracked
            self.assertEqual(len(manager.error_history), 2)
            
            # Add more errors to test limit
            for i in range(10):
                manager.add_error_feedback(f"Error {i}")
            
            # Should keep only recent errors
            self.assertLessEqual(len(manager.error_history), 5)
        finally:
            prompt_file.unlink()


class TestRalphOrchestrator(unittest.TestCase):
    """Test main orchestrator."""
    
    @patch('ralph_orchestrator.orchestrator.ClaudeAdapter')
    @patch('ralph_orchestrator.orchestrator.OllamaAdapter')
    @patch('ralph_orchestrator.orchestrator.GeminiAdapter')
    def test_orchestrator_initialization(self, mock_gemini, mock_ollama, mock_claude):
        """Test orchestrator initialization."""
        # Mock adapters
        mock_claude_instance = MagicMock()
        mock_claude_instance.available = True
        mock_claude.return_value = mock_claude_instance
        
        with tempfile.NamedTemporaryFile(mode='w', suffix='.md', delete=False) as f:
            f.write("# Test")
            prompt_file = f.name
        
        try:
            orchestrator = RalphOrchestrator(
                prompt_file_or_config=prompt_file,
                primary_tool="claude",
                max_iterations=10
            )
            
            self.assertEqual(orchestrator.max_iterations, 10)
            self.assertEqual(orchestrator.primary_tool, "claude")
            self.assertIsNotNone(orchestrator.metrics)
            self.assertIsNotNone(orchestrator.safety_guard)
        finally:
            Path(prompt_file).unlink()
    
    # Task completion detection has been removed - orchestrator runs until limits


class TestIterationTelemetry(unittest.TestCase):
    """Test per-iteration telemetry capture in orchestrator."""

    @patch('ralph_orchestrator.orchestrator.ClaudeAdapter')
    @patch('ralph_orchestrator.orchestrator.OllamaAdapter')
    @patch('ralph_orchestrator.orchestrator.GeminiAdapter')
    def test_orchestrator_has_iteration_stats(self, mock_gemini, mock_ollama, mock_claude):
        """Test orchestrator initializes iteration_stats."""
        mock_claude_instance = MagicMock()
        mock_claude_instance.available = True
        mock_claude.return_value = mock_claude_instance

        with tempfile.NamedTemporaryFile(mode='w', suffix='.md', delete=False) as f:
            f.write("# Test Task\n- [ ] TASK_COMPLETE")
            prompt_file = f.name

        try:
            orchestrator = RalphOrchestrator(
                prompt_file_or_config=prompt_file,
                primary_tool="claude",
                max_iterations=5
            )

            # Should have iteration_stats
            self.assertIsNotNone(orchestrator.iteration_stats)
            self.assertEqual(len(orchestrator.iteration_stats.iterations), 0)
        finally:
            Path(prompt_file).unlink()

    @patch('ralph_orchestrator.orchestrator.ClaudeAdapter')
    @patch('ralph_orchestrator.orchestrator.OllamaAdapter')
    @patch('ralph_orchestrator.orchestrator.GeminiAdapter')
    def test_determine_trigger_reason_initial(self, mock_gemini, mock_ollama, mock_claude):
        """Test _determine_trigger_reason returns INITIAL for first iteration."""
        mock_claude_instance = MagicMock()
        mock_claude_instance.available = True
        mock_claude.return_value = mock_claude_instance

        with tempfile.NamedTemporaryFile(mode='w', suffix='.md', delete=False) as f:
            f.write("# Test Task")
            prompt_file = f.name

        try:
            orchestrator = RalphOrchestrator(
                prompt_file_or_config=prompt_file,
                primary_tool="claude",
            )

            reason = orchestrator._determine_trigger_reason()
            self.assertEqual(reason, "initial")
        finally:
            Path(prompt_file).unlink()

    @patch('ralph_orchestrator.orchestrator.ClaudeAdapter')
    @patch('ralph_orchestrator.orchestrator.OllamaAdapter')
    @patch('ralph_orchestrator.orchestrator.GeminiAdapter')
    def test_determine_trigger_reason_task_incomplete(self, mock_gemini, mock_ollama, mock_claude):
        """Test _determine_trigger_reason returns TASK_INCOMPLETE after first iteration."""
        mock_claude_instance = MagicMock()
        mock_claude_instance.available = True
        mock_claude.return_value = mock_claude_instance

        with tempfile.NamedTemporaryFile(mode='w', suffix='.md', delete=False) as f:
            f.write("# Test Task")
            prompt_file = f.name

        try:
            orchestrator = RalphOrchestrator(
                prompt_file_or_config=prompt_file,
                primary_tool="claude",
            )

            # Simulate first iteration completed successfully
            orchestrator.metrics.iterations = 1
            orchestrator.metrics.successful_iterations = 1

            reason = orchestrator._determine_trigger_reason()
            self.assertEqual(reason, "task_incomplete")
        finally:
            Path(prompt_file).unlink()

    @patch('ralph_orchestrator.orchestrator.ClaudeAdapter')
    @patch('ralph_orchestrator.orchestrator.OllamaAdapter')
    @patch('ralph_orchestrator.orchestrator.GeminiAdapter')
    def test_determine_trigger_reason_recovery(self, mock_gemini, mock_ollama, mock_claude):
        """Test _determine_trigger_reason returns RECOVERY after failures."""
        mock_claude_instance = MagicMock()
        mock_claude_instance.available = True
        mock_claude.return_value = mock_claude_instance

        with tempfile.NamedTemporaryFile(mode='w', suffix='.md', delete=False) as f:
            f.write("# Test Task")
            prompt_file = f.name

        try:
            orchestrator = RalphOrchestrator(
                prompt_file_or_config=prompt_file,
                primary_tool="claude",
            )

            # Simulate failures - all iterations failed
            orchestrator.metrics.iterations = 3
            orchestrator.metrics.successful_iterations = 0
            orchestrator.metrics.failed_iterations = 3

            reason = orchestrator._determine_trigger_reason()
            self.assertEqual(reason, "recovery")
        finally:
            Path(prompt_file).unlink()

    @patch('ralph_orchestrator.orchestrator.ClaudeAdapter')
    @patch('ralph_orchestrator.orchestrator.OllamaAdapter')
    @patch('ralph_orchestrator.orchestrator.GeminiAdapter')
    def test_iteration_telemetry_disabled(self, mock_gemini, mock_ollama, mock_claude):
        """Test orchestrator with iteration_telemetry=False."""
        mock_claude_instance = MagicMock()
        mock_claude_instance.available = True
        mock_claude.return_value = mock_claude_instance

        with tempfile.NamedTemporaryFile(mode='w', suffix='.md', delete=False) as f:
            f.write("# Test Task")
            prompt_file = f.name

        try:
            orchestrator = RalphOrchestrator(
                prompt_file_or_config=prompt_file,
                primary_tool="claude",
                iteration_telemetry=False,
            )

            # iteration_stats should be None when telemetry disabled
            self.assertIsNone(orchestrator.iteration_stats)
        finally:
            Path(prompt_file).unlink()

    @patch('ralph_orchestrator.orchestrator.ClaudeAdapter')
    @patch('ralph_orchestrator.orchestrator.OllamaAdapter')
    @patch('ralph_orchestrator.orchestrator.GeminiAdapter')
    def test_custom_output_preview_length(self, mock_gemini, mock_ollama, mock_claude):
        """Test orchestrator with custom output_preview_length."""
        mock_claude_instance = MagicMock()
        mock_claude_instance.available = True
        mock_claude.return_value = mock_claude_instance

        with tempfile.NamedTemporaryFile(mode='w', suffix='.md', delete=False) as f:
            f.write("# Test Task")
            prompt_file = f.name

        try:
            orchestrator = RalphOrchestrator(
                prompt_file_or_config=prompt_file,
                primary_tool="claude",
                output_preview_length=200,
            )

            self.assertEqual(orchestrator.output_preview_length, 200)
            self.assertIsNotNone(orchestrator.iteration_stats)
            self.assertEqual(orchestrator.iteration_stats.max_preview_length, 200)
        finally:
            Path(prompt_file).unlink()


class TestAutoSelection(unittest.TestCase):
    """Tests for auto agent selection priority."""

    @patch('ralph_orchestrator.orchestrator.ClaudeAdapter')
    @patch('ralph_orchestrator.orchestrator.GeminiAdapter')
    @patch('ralph_orchestrator.orchestrator.OllamaAdapter')
    def test_auto_prefers_ollama(self, mock_ollama, mock_gemini, mock_claude):
        """Auto mode should pick Ollama first when available."""
        mock_ollama.return_value.available = True
        mock_gemini.return_value.available = True
        mock_claude.return_value.available = True

        with tempfile.NamedTemporaryFile(mode='w', suffix='.md', delete=False) as f:
            f.write("# Test Task")
            prompt_file = f.name

        try:
            orchestrator = RalphOrchestrator(
                prompt_file_or_config=prompt_file,
                primary_tool="auto",
            )
            self.assertEqual(orchestrator.primary_tool, "ollama")
            self.assertTrue(orchestrator.allow_fallbacks)
        finally:
            Path(prompt_file).unlink()

    @patch('ralph_orchestrator.orchestrator.ClaudeAdapter')
    @patch('ralph_orchestrator.orchestrator.GeminiAdapter')
    @patch('ralph_orchestrator.orchestrator.OllamaAdapter')
    def test_auto_falls_back_to_gemini(self, mock_ollama, mock_gemini, mock_claude):
        """Auto mode should fall back to Gemini when Ollama is unavailable."""
        mock_ollama.return_value.available = False
        mock_gemini.return_value.available = True
        mock_claude.return_value.available = True

        with tempfile.NamedTemporaryFile(mode='w', suffix='.md', delete=False) as f:
            f.write("# Test Task")
            prompt_file = f.name

        try:
            orchestrator = RalphOrchestrator(
                prompt_file_or_config=prompt_file,
                primary_tool="auto",
            )
            self.assertEqual(orchestrator.primary_tool, "gemini")
        finally:
            Path(prompt_file).unlink()


class TestAutoSelectionPriority(unittest.TestCase):
    """Test auto adapter selection priority."""

    def _create_prompt(self):
        tmp = tempfile.NamedTemporaryFile(mode='w', suffix='.md', delete=False)
        tmp.write("# Test Task")
        tmp.flush()
        tmp.close()
        return tmp.name

    def test_auto_prefers_ollama(self):
        prompt_file = self._create_prompt()
        adapters = {
            "ollama": MagicMock(),
            "gemini": MagicMock(),
            "claude": MagicMock(),
        }
        for adapter in adapters.values():
            adapter.available = True

        try:
            with patch.object(RalphOrchestrator, "_initialize_adapters", return_value=adapters):
                orchestrator = RalphOrchestrator(
                    prompt_file_or_config=prompt_file,
                    primary_tool="auto",
                )
                self.assertEqual(orchestrator.current_adapter_name, "ollama")
                self.assertIs(orchestrator.current_adapter, adapters["ollama"])
        finally:
            Path(prompt_file).unlink()

    def test_auto_falls_back_to_gemini(self):
        prompt_file = self._create_prompt()
        adapters = {
            "gemini": MagicMock(),
            "claude": MagicMock(),
        }
        for adapter in adapters.values():
            adapter.available = True

        try:
            with patch.object(RalphOrchestrator, "_initialize_adapters", return_value=adapters):
                orchestrator = RalphOrchestrator(
                    prompt_file_or_config=prompt_file,
                    primary_tool="auto",
                )
                self.assertEqual(orchestrator.current_adapter_name, "gemini")
                self.assertIs(orchestrator.current_adapter, adapters["gemini"])
        finally:
            Path(prompt_file).unlink()

    def test_auto_uses_claude_last(self):
        prompt_file = self._create_prompt()
        adapters = {
            "claude": MagicMock(),
        }
        adapters["claude"].available = True

        try:
            with patch.object(RalphOrchestrator, "_initialize_adapters", return_value=adapters):
                orchestrator = RalphOrchestrator(
                    prompt_file_or_config=prompt_file,
                    primary_tool="auto",
                )
                self.assertEqual(orchestrator.current_adapter_name, "claude")
                self.assertIs(orchestrator.current_adapter, adapters["claude"])
        finally:
            Path(prompt_file).unlink()

    def test_explicit_agent_override(self):
        prompt_file = self._create_prompt()
        adapters = {
            "ollama": MagicMock(),
            "claude": MagicMock(),
        }
        for adapter in adapters.values():
            adapter.available = True

        try:
            with patch.object(RalphOrchestrator, "_initialize_adapters", return_value=adapters):
                orchestrator = RalphOrchestrator(
                    prompt_file_or_config=prompt_file,
                    primary_tool="claude",
                )
                self.assertEqual(orchestrator.current_adapter_name, "claude")
                self.assertIs(orchestrator.current_adapter, adapters["claude"])
            self.assertFalse(orchestrator.allow_fallbacks)
        finally:
            Path(prompt_file).unlink()


class TestOrchestratorCleanup(unittest.TestCase):
    """Tests for orchestrator cleanup hooks."""

    def test_shutdown_adapters_invokes_shutdown_hook(self):
        """Ensure adapters with shutdown hooks are called."""

        class DummyAdapter:
            name = "dummy"
            available = True

            def __init__(self):
                self.shutdown_called = False

            async def shutdown(self):
                self.shutdown_called = True

        with tempfile.TemporaryDirectory() as tmpdir:
            prompt_file = Path(tmpdir) / "prompt.md"
            prompt_file.write_text("test prompt")

            adapter = DummyAdapter()

            with patch.object(
                RalphOrchestrator, "_initialize_adapters", return_value={"dummy": adapter}
            ), patch.object(
                RalphOrchestrator, "_select_adapter", return_value=("dummy", adapter)
            ):
                orchestrator = RalphOrchestrator(
                    prompt_file_or_config=prompt_file,
                    primary_tool="dummy",
                    max_iterations=1,
                )
                asyncio.run(orchestrator._shutdown_adapters())

            self.assertTrue(adapter.shutdown_called)


if __name__ == "__main__":
    unittest.main()
