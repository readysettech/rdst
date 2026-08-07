from unittest.mock import patch

from shared.ui.prompts import Prompt


def test_blank_text_prompt_returns_empty_string():
    with patch("shared.ui.prompts.RichPrompt.ask", return_value=None):
        value = Prompt.ask(">", default="", show_default=False)

    assert value == ""
    assert value.strip() == ""
