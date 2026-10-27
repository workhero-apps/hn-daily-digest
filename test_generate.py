import json
import os
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

import generate


class WeeklyAggregateTests(unittest.TestCase):
    def test_iso_week_for_2026_september_7(self):
        self.assertEqual(generate.iso_week_key("07-09-2026"), "2026-W37")
        self.assertEqual(generate.week_label("2026-W37"), "Week 37 began 7 Sep 2026")

    def test_dedupes_highest_score_then_later_day_and_orders(self):
        grouped = generate.grouped_week_stories([
            ("07-09-2026", [
                {"id": 1, "title": "older", "score": 50},
                {"id": 2, "title": "second", "score": 80},
            ]),
            ("08-09-2026", [
                {"id": 1, "title": "higher", "score": 90},
                {"id": 2, "title": "later tie", "score": 80},
            ]),
        ])
        stories = grouped["2026-W37"]
        self.assertEqual([story["id"] for story in stories], [1, 2])
        self.assertEqual(stories[0]["title"], "higher")
        self.assertEqual(stories[1]["title"], "later tie")
        self.assertEqual(stories[1]["archive_day"], "08-09-2026")

    def test_week_render_reuses_existing_archive_url_without_copying_or_fetching_images(self):
        with tempfile.TemporaryDirectory() as tempdir:
            old_cwd = os.getcwd()
            os.chdir(tempdir)
            try:
                os.makedirs("history/07-09-2026/screenshots")
                story = {
                    "id": 42, "title": "Reuse image", "url": "https://example.test",
                    "score": 100, "comments": 2, "author": "ada", "time": 0,
                    "screenshot_rank": 1, "screenshot_path": "screenshots/1.jpg",
                    "screenshot_available": True,
                }
                with open("history/07-09-2026/stories.json", "w", encoding="utf-8") as output:
                    json.dump([story], output)
                with open("history/07-09-2026/screenshots/1.jpg", "wb") as output:
                    output.write(b"archived screenshot")
                with patch("generate.shutil.copy2", side_effect=AssertionError("weekly render copied an image")), \
                     patch("generate.urllib.request.urlopen", side_effect=AssertionError("weekly render fetched an image")):
                    generate.generate_weeks(["07-09-2026"])
                with open("weeks/2026-W37/index.html", encoding="utf-8") as output:
                    page = output.read()
                self.assertIn('<img src="/hn-daily-digest/history/07-09-2026/screenshots/1.jpg"', page)
                self.assertFalse(os.path.exists("weeks/2026-W37/screenshots"))
            finally:
                os.chdir(old_cwd)

    def test_week_render_uses_fallback_when_available_manifest_image_is_missing(self):
        with tempfile.TemporaryDirectory() as tempdir:
            old_cwd = os.getcwd()
            os.chdir(tempdir)
            try:
                os.makedirs("history/07-09-2026/screenshots")
                story = {
                    "id": 43, "title": "Missing image", "url": "https://example.test",
                    "score": 100, "comments": 2, "author": "ada", "time": 0,
                    "screenshot_rank": 1, "screenshot_path": "screenshots/1.jpg",
                    "screenshot_available": True,
                }
                with open("history/07-09-2026/stories.json", "w", encoding="utf-8") as output:
                    json.dump([story], output)
                with patch("generate.shutil.copy2", side_effect=AssertionError("weekly render copied an image")), \
                     patch("generate.urllib.request.urlopen", side_effect=AssertionError("weekly render fetched an image")):
                    generate.generate_weeks(["07-09-2026"])
                with open("weeks/2026-W37/index.html", encoding="utf-8") as output:
                    page = output.read()
                self.assertIn("Screenshot unavailable", page)
                self.assertNotIn("/hn-daily-digest/history/07-09-2026/screenshots/1.jpg", page)
                self.assertNotIn("<img ", page)
                self.assertFalse(os.path.exists("weeks/2026-W37/screenshots"))
            finally:
                os.chdir(old_cwd)

    def test_week_render_uses_fallback_for_traversal_screenshot_path(self):
        with tempfile.TemporaryDirectory() as tempdir:
            old_cwd = os.getcwd()
            os.chdir(tempdir)
            try:
                os.makedirs("history/07-09-2026/screenshots")
                with open("outside.jpg", "wb") as output:
                    output.write(b"outside archive")
                story = {
                    "id": 44, "title": "Traversal image", "url": "https://example.test",
                    "score": 100, "comments": 2, "author": "ada", "time": 0,
                    "screenshot_rank": 1, "screenshot_path": "screenshots/../../outside.jpg",
                    "screenshot_available": True,
                }
                with open("history/07-09-2026/stories.json", "w", encoding="utf-8") as output:
                    json.dump([story], output)

                generate.generate_weeks(["07-09-2026"])

                with open("weeks/2026-W37/index.html", encoding="utf-8") as output:
                    page = output.read()
                self.assertIn("Screenshot unavailable", page)
                self.assertNotIn("outside.jpg", page)
                self.assertNotIn("<img ", page)
            finally:
                os.chdir(old_cwd)

    def test_week_render_uses_fallback_for_symlinked_archive_day(self):
        with tempfile.TemporaryDirectory() as tempdir:
            old_cwd = os.getcwd()
            os.chdir(tempdir)
            try:
                os.makedirs("outside-archive/screenshots")
                story = {
                    "id": 46, "title": "Symlinked archive day", "url": "https://example.test",
                    "score": 100, "comments": 2, "author": "ada", "time": 0,
                    "screenshot_rank": 1, "screenshot_path": "screenshots/1.jpg",
                    "screenshot_available": True,
                }
                with open("outside-archive/stories.json", "w", encoding="utf-8") as output:
                    json.dump([story], output)
                with open("outside-archive/screenshots/1.jpg", "wb") as output:
                    output.write(b"outside archive screenshot")
                os.mkdir("history")
                try:
                    os.symlink(os.path.abspath("outside-archive"), "history/07-09-2026")
                except (NotImplementedError, OSError):
                    self.skipTest("symlinks are unavailable")

                generate.generate_weeks(["07-09-2026"])

                with open("weeks/2026-W37/index.html", encoding="utf-8") as output:
                    page = output.read()
                self.assertIn("Screenshot unavailable", page)
                self.assertNotIn("/hn-daily-digest/history/07-09-2026/screenshots/1.jpg", page)
                self.assertNotIn("<img ", page)
            finally:
                os.chdir(old_cwd)

    def test_week_render_uses_fallback_for_symlinked_screenshots_directory(self):
        with tempfile.TemporaryDirectory() as tempdir:
            old_cwd = os.getcwd()
            os.chdir(tempdir)
            try:
                os.makedirs("history/07-09-2026")
                os.makedirs("outside-screenshots")
                story = {
                    "id": 47, "title": "Symlinked screenshots directory", "url": "https://example.test",
                    "score": 100, "comments": 2, "author": "ada", "time": 0,
                    "screenshot_rank": 1, "screenshot_path": "screenshots/1.jpg",
                    "screenshot_available": True,
                }
                with open("history/07-09-2026/stories.json", "w", encoding="utf-8") as output:
                    json.dump([story], output)
                with open("outside-screenshots/1.jpg", "wb") as output:
                    output.write(b"outside screenshot")
                try:
                    os.symlink(os.path.abspath("outside-screenshots"), "history/07-09-2026/screenshots")
                except (NotImplementedError, OSError):
                    self.skipTest("symlinks are unavailable")

                generate.generate_weeks(["07-09-2026"])

                with open("weeks/2026-W37/index.html", encoding="utf-8") as output:
                    page = output.read()
                self.assertIn("Screenshot unavailable", page)
                self.assertNotIn("/hn-daily-digest/history/07-09-2026/screenshots/1.jpg", page)
                self.assertNotIn("<img ", page)
            finally:
                os.chdir(old_cwd)

    def test_week_render_uses_fallback_for_symlink_to_outside_archive(self):
        with tempfile.TemporaryDirectory() as tempdir:
            old_cwd = os.getcwd()
            os.chdir(tempdir)
            try:
                os.makedirs("history/07-09-2026/screenshots")
                with open("outside.jpg", "wb") as output:
                    output.write(b"outside archive")
                try:
                    os.symlink(os.path.abspath("outside.jpg"), "history/07-09-2026/screenshots/1.jpg")
                except (NotImplementedError, OSError):
                    self.skipTest("symlinks are unavailable")
                with open("history/07-09-2026/index.html", "w", encoding="utf-8") as output:
                    output.write('''<div class="card"><img src="screenshots/1.jpg">
                        <a class="discuss" href="https://news.ycombinator.com/item?id=45">Discuss</a></div>''')
                records = generate.backfill_manifest("07-09-2026")
                self.assertEqual(len(records), 1)
                self.assertFalse(records[0]["screenshot_available"])
                self.assertIsNone(records[0]["screenshot_path"])

                generate.generate_weeks(["07-09-2026"])

                with open("weeks/2026-W37/index.html", encoding="utf-8") as output:
                    page = output.read()
                self.assertIn("Screenshot unavailable", page)
                self.assertNotIn("/hn-daily-digest/history/07-09-2026/screenshots/1.jpg", page)
                self.assertNotIn("<img ", page)
            finally:
                os.chdir(old_cwd)


class ScreenshotCaptureTests(unittest.TestCase):
    def test_capture_screenshots_uses_local_playwright_and_returns_successful_ranks(self):
        calls = []

        class FakePage:
            def goto(self, url, **kwargs): calls.append(("goto", url, kwargs))
            def wait_for_timeout(self, milliseconds): calls.append(("wait", milliseconds))
            def screenshot(self, **kwargs):
                calls.append(("screenshot", kwargs))
                with open(kwargs["path"], "wb") as output:
                    output.write(b"jpeg")
            def close(self): calls.append(("close",))

        class FakeContext:
            def new_page(self): return FakePage()
            def close(self): calls.append(("context-close",))

        class FakeBrowser:
            def new_context(self, **kwargs): calls.append(("context", kwargs)); return FakeContext()
            def close(self): calls.append(("browser-close",))

        class FakePlaywright:
            chromium = types.SimpleNamespace(launch=lambda **kwargs: FakeBrowser())

        class FakeManager:
            def __enter__(self): return FakePlaywright()
            def __exit__(self, *args): return False

        fake_sync = types.ModuleType("playwright.sync_api")
        fake_sync.sync_playwright = lambda: FakeManager()
        fake_package = types.ModuleType("playwright")
        with tempfile.TemporaryDirectory() as tempdir, patch.dict(sys.modules, {"playwright": fake_package, "playwright.sync_api": fake_sync}):
            successful = generate.capture_screenshots(
                [{"id": 1, "url": "https://example.test/story"}], tempdir
            )
            self.assertEqual(successful, {1})
            self.assertTrue(os.path.exists(os.path.join(tempdir, "1.jpg")))
        self.assertIn(("goto", "https://example.test/story", {"wait_until": "domcontentloaded", "timeout": 25000}), calls)


if __name__ == "__main__":
    unittest.main()
