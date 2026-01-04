# ABOUTME: CLI default behavior tests for Ralph Orchestrator
# ABOUTME: Ensures default prompt path and agent choices are correct

import pytest
from unittest.mock import patch

from ralph_orchestrator.__main__ import main
from ralph_orchestrator.main import DEFAULT_PROMPT_FILE


def test_cli_uses_root_prompt_by_default():
    """Running without --prompt should look for PROMPT.md in the cwd."""
    with patch("sys.argv", ["ralph", "--dry-run"]):
        with patch("ralph_orchestrator.__main__.RalphOrchestrator"):
            with patch("ralph_orchestrator.__main__.Path") as mock_path:
                mock_path.return_value.exists.return_value = True
                mock_path.return_value.read_text.return_value = "# Task"
                with pytest.raises(SystemExit) as exc_info:
                    main()
                assert exc_info.value.code == 0
                mock_path.assert_any_call(DEFAULT_PROMPT_FILE)
