"""Regression checks for intentional opening versus login tray startup."""

import contextlib
import io
import unittest
from unittest.mock import patch

from language_input_settings import app, appconfig, cli


@unittest.skipUnless(app._HAS_QT, "PySide6 is unavailable")
class SettingsStartupTests(unittest.TestCase):
    def start(self, saved_minimized, **options):
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.object(app, "acquire_single_instance", return_value=True))
            qt = stack.enter_context(patch.object(app, "QApplication"))
            qt.return_value.exec.return_value = 0
            stack.enter_context(patch.object(app, "_apply_theme"))
            stack.enter_context(patch.object(
                appconfig, "load_config",
                return_value=appconfig.AppConfig(start_minimized=saved_minimized),
            ))
            controller = stack.enter_context(patch.object(app, "SettingsApp"))
            self.assertEqual(0, app.run_gui(**options))
            return controller.return_value

    def test_shortcut_opens_even_with_saved_minimized_preference(self):
        controller = self.start(True, show=True)
        controller.start.assert_called_once_with(minimized=False)

    def test_login_starts_in_tray_even_with_saved_window_preference(self):
        controller = self.start(False, start_minimized=True)
        controller.start.assert_called_once_with(minimized=True)

    def test_plain_launch_still_honours_saved_preference(self):
        for saved in (False, True):
            with self.subTest(saved=saved):
                self.start(saved).start.assert_called_once_with(minimized=saved)

    def test_reopening_existing_instance_restores_it(self):
        with patch.object(app, "acquire_single_instance", return_value=False):
            with patch.object(app, "show_existing_instance") as restore:
                self.assertEqual(0, app.run_gui(show=True))
                restore.assert_called_once_with()

    def test_show_flag_reaches_gui_and_conflicts_with_minimized(self):
        with patch.object(cli, "dispatch", return_value=None):
            with patch.object(app, "run_gui", return_value=0) as launch:
                self.assertEqual(0, app.main(["--show"]))
                launch.assert_called_once_with(start_minimized=False, show=True)
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as error:
                app.main(["--show", "--start-minimized"])
        self.assertEqual(2, error.exception.code)


if __name__ == "__main__":
    unittest.main()
