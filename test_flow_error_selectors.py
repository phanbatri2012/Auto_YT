import unittest
import sys
from pathlib import Path

# Add src to path
src_path = Path(__file__).resolve().parent / "src"
if str(src_path) not in sys.path:
    sys.path.insert(0, str(src_path))

from auto_yt.services.google_flow_worker import _is_valid_flow_error_text, GoogleFlowWorker

class TestFlowErrorFiltering(unittest.TestCase):
    def test_text_sanitizer_rejects_prompt_placeholders(self):
        prompts = [
            "image 20% Cinematic documentary film still, 35mm photography... keyboard_return",
            "Bạn muốn tạo gì? A somber office with failed peace talks",
            "Tuy nhiên, những đề nghị này cuối cùng không dẫn tới một thỏa thuận. keyboard_return",
            "Authentic documentary realism, natural moody lighting. Avoid: cartoon, anime, 3D CGI render, text, watermark",
            "Narrative scene: A somber, high-stakes political office in late 1970s Southeast Asia.",
        ]

        for p in prompts:
            self.assertFalse(_is_valid_flow_error_text(p), f"Should reject prompt text: {p}")

    def test_text_sanitizer_accepts_real_error(self):
        real_errors = [
            "Prompt violates our safety policy.",
            "Service temporarily unavailable. Please try again later.",
            "Quota exceeded for image generation.",
            "Failed to load project media.",
            "Network timeout while contacting Veo generation service.",
        ]

        for e in real_errors:
            self.assertTrue(_is_valid_flow_error_text(e), f"Should accept real error: {e}")

    def test_canvas_error_selectors_do_not_contain_virtual_item_container(self):
        import inspect
        src = inspect.getsource(GoogleFlowWorker.generate_scene)
        self.assertNotIn(".virtual-item-container:has-text('Failed')", src)
        self.assertNotIn("div:has-text('The agent failed')", src)
        self.assertNotIn("div:has-text('failed to generate')", src)
        self.assertNotIn("p:has-text('safety filters')", src)
        self.assertIn("canvas_err_selectors", src)
        self.assertIn("flow-error-tile", src)
        self.assertIn("initial_error_texts", src)
        self.assertIn("_get_existing_error_texts", src)

    def test_worker_has_error_snapshot_helper(self):
        self.assertTrue(hasattr(GoogleFlowWorker, "_get_existing_error_texts"))

if __name__ == "__main__":
    unittest.main()
