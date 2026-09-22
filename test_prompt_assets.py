import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent / "src"))

from auto_yt.services import prompt_assets


class PromptAssetsTests(unittest.TestCase):
    def test_strip_accents(self):
        self.assertEqual(prompt_assets.strip_accents("Bác Ba"), "bac ba")
        self.assertEqual(prompt_assets.strip_accents("Chùa Một Cột"), "chua mot cot")
        self.assertEqual(prompt_assets.strip_accents("Đinh Đoàn"), "dinh doan")

    def test_generate_asset_keywords(self):
        kws = prompt_assets.generate_asset_keywords("bac_ba")
        self.assertIn("bac ba", kws)
        self.assertIn("ba", kws)

    def test_list_prompt_assets_and_matching(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            fake_assets_dir = Path(temp_dir)
            with patch.object(prompt_assets, "PROMPT_ASSETS_DIR", fake_assets_dir):
                gkvs_dir = prompt_assets.get_prompt_asset_dir("GKVS")
                (gkvs_dir / "bac_ba.png").write_bytes(b"fake-image")
                (gkvs_dir / "chi_lan.jpg").write_bytes(b"fake-image-2")
                (gkvs_dir / "chua_mot_cot.png").write_bytes(b"fake-image-3")
                (gkvs_dir / "notes.txt").write_text("not an image")

                assets = prompt_assets.list_prompt_assets("GKVS")
                self.assertEqual(len(assets), 3)
                names = {a["name"] for a in assets}
                self.assertEqual(names, {"bac_ba", "chi_lan", "chua_mot_cot"})

                # Test matching:
                # 1. Matching Bác Ba
                m1 = prompt_assets.match_scene_reference("Lúc đó bác Ba đang ngồi uống nước chè", assets)
                self.assertIsNotNone(m1)
                self.assertEqual(m1["name"], "bac_ba")

                # 2. Matching Chị Lan (không dấu)
                m2 = prompt_assets.match_scene_reference("chi lan buoc vao nha", assets)
                self.assertIsNotNone(m2)
                self.assertEqual(m2["name"], "chi_lan")

                # 3. Matching địa danh Chùa Một Cột
                m3 = prompt_assets.match_scene_reference("Cả hai cùng đến Chùa Một Cột cầu an", assets)
                self.assertIsNotNone(m3)
                self.assertEqual(m3["name"], "chua_mot_cot")

                # 4. No match
                m4 = prompt_assets.match_scene_reference("Trời mưa tầm tã suốt đêm", assets)
                self.assertIsNone(m4)


    def test_resolve_prompt_folder_name(self):
        # When prompt_version is an id in prompts.json
        name_gkvs = prompt_assets.resolve_prompt_folder_name("v_1786603848860")
        self.assertEqual(name_gkvs, "GKVS")

        name_thhn = prompt_assets.resolve_prompt_folder_name("v_1784470764118")
        self.assertEqual(name_thhn, "Thấu Hiểu Hôn Nhân")

        # When already a friendly name
        name_direct = prompt_assets.resolve_prompt_folder_name("GKVS")
        self.assertEqual(name_direct, "GKVS")

    def test_legacy_directory_migration(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            fake_assets_dir = Path(temp_dir)
            with patch.object(prompt_assets, "PROMPT_ASSETS_DIR", fake_assets_dir):
                # Create a legacy folder named after raw ID
                legacy_dir = fake_assets_dir / "v_1786603848860"
                legacy_dir.mkdir(parents=True)
                (legacy_dir / "bac_ba.png").write_bytes(b"sample-data")

                # Resolving the directory should move the files to 'GKVS' and remove the legacy folder
                target_dir = prompt_assets.get_prompt_asset_dir("v_1786603848860")
                self.assertEqual(target_dir.name, "GKVS")
                self.assertTrue((target_dir / "bac_ba.png").exists())
                self.assertFalse(legacy_dir.exists())


if __name__ == "__main__":
    unittest.main()
