"""Focused regression checks for persisted backends and model setup."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from language_input_settings import (
    appconfig, cli, env_config, gloss_badge, models_workflow, server,
)


class BackendSelectionTests(unittest.TestCase):
    def test_malformed_saved_choice_matches_engine_fail_closed_behavior(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(
            "os.environ", {"APPDATA": temp}
        ):
            self.assertIsNone(appconfig.saved_backend())
            path = appconfig.config_path()
            path.parent.mkdir(parents=True)
            path.write_text("{bad json", encoding="utf-8")
            self.assertEqual(appconfig.saved_backend(), "off")
            path.write_text('{"backend":"remote"}', encoding="utf-8")
            self.assertEqual(appconfig.saved_backend(), "remote")

    def test_saved_choice_is_used_only_without_an_environment_override(self):
        self.assertEqual(
            env_config.describe_effective_backend({}, "remote"),
            env_config.Backend.remote,
        )
        self.assertEqual(
            env_config.describe_effective_backend({}, "off"),
            env_config.Backend.off,
        )
        self.assertEqual(
            env_config.describe_effective_backend(
                {env_config.REMOTE_ENABLED: "local"}, "remote"
            ),
            env_config.Backend.local,
        )

    def test_local_apply_uses_explicit_override_until_config_is_saved(self):
        environment = env_config.build_server_env({}, "local")
        self.assertEqual(environment[env_config.REMOTE_ENABLED], "local")
        self.assertEqual(env_config.describe_backend(environment), env_config.Backend.local)

    def test_server_snapshot_uses_saved_remote_without_an_override(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(
            "os.environ", {"APPDATA": temp}
        ):
            config = appconfig.AppConfig(
                backend="remote", remote_url="https://example.invalid/v1", remote_model="m"
            )
            appconfig.save_config(config)
            snapshot = server._remote_snapshot({}, include_saved=True)
            self.assertEqual(snapshot["backend"], "remote")
            self.assertEqual(snapshot["url"], config.remote_url)
            self.assertEqual(snapshot["model"], "m")

    def test_cli_rejects_remote_without_required_fields_before_restart(self):
        args = SimpleNamespace(
            set_backend="remote", url="https://example.invalid/v1",
            model="", language="en", api_key=None
        )
        with patch.object(appconfig, "load_config", return_value=appconfig.AppConfig()), \
                patch.object(server, "apply_backend") as apply, \
                patch.object(cli, "_print"):
            self.assertEqual(cli.cmd_set_backend(args), 1)
            apply.assert_not_called()


class ModelWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.components = {
            "base": SimpleNamespace(requires=(), files=(SimpleNamespace(size=10),)),
            "target": SimpleNamespace(
                requires=("base",), files=(SimpleNamespace(size=20),)
            ),
        }

    def test_dependency_first_and_replace_only_selected_component(self):
        self.assertEqual(
            models_workflow.install_plan("target", self.components, set()),
            ["base", "target"],
        )
        self.assertEqual(
            models_workflow.install_plan("target", self.components, {"base"}),
            ["target"],
        )
        self.assertEqual(
            models_workflow.install_plan(
                "target", self.components, {"base", "target"}, replace=True
            ),
            ["target"],
        )

    def test_workflow_downloads_all_before_installing_in_dependency_order(self):
        calls = []

        def download(component, destination, *, sources, progress):
            calls.append(("download", component.component_id))
            progress(sum(item.size for item in component.files), 0, None)
            return {"total_bytes": sum(item.size for item in component.files)}

        def install(component, destination, root, *, replace):
            calls.append(("install", component.component_id, replace))
            return {"verification": {"ok": True}}

        components = {
            key: SimpleNamespace(component_id=key, **vars(component))
            for key, component in self.components.items()
        }
        progress = []
        with tempfile.TemporaryDirectory() as temp, patch.object(
            models_workflow.models_catalog, "load_components", return_value=components
        ), patch.object(
            models_workflow.models_catalog, "installed_ids", return_value=[]
        ), patch.object(
            models_workflow.models_download, "download_component", side_effect=download
        ), patch.object(
            models_workflow.models_install, "install_from_dir", side_effect=install
        ):
            result = models_workflow.download_and_install_component(
                "target", Path(temp), progress=lambda current, total, path: progress.append((current, total))
            )

        self.assertEqual(result["plan"], ["base", "target"])
        self.assertEqual(
            calls,
            [
                ("download", "base"),
                ("download", "target"),
                ("install", "base", False),
                ("install", "target", False),
            ],
        )
        self.assertEqual(progress[-1], (30, 30))

    def test_later_download_failure_does_not_install_any_component(self):
        components = {
            key: SimpleNamespace(component_id=key, **vars(component))
            for key, component in self.components.items()
        }

        def download(component, *_args, **_kwargs):
            if component.component_id == "target":
                raise RuntimeError("network unavailable")
            return {"total_bytes": 10}

        with tempfile.TemporaryDirectory() as temp, patch.object(
            models_workflow.models_catalog, "load_components", return_value=components
        ), patch.object(
            models_workflow.models_catalog, "installed_ids", return_value=[]
        ), patch.object(
            models_workflow.models_download, "download_component", side_effect=download
        ), patch.object(models_workflow.models_install, "install_from_dir") as install:
            with self.assertRaisesRegex(RuntimeError, "network unavailable"):
                models_workflow.download_and_install_component("target", Path(temp))
            install.assert_not_called()


class PlainGlossTests(unittest.TestCase):
    def test_plain_gloss_wrapper_tracks_shipped_filter_and_upgrades_old_copy(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            user = root / "user"
            installed = root / "installed" / "gloss_filter.lua"
            installed.parent.mkdir(parents=True)
            installed.write_text(
                'local M = {}\nfunction M.init(env) env.marker = "〔en·词〕 " end\nreturn M\n',
                encoding="utf-8",
            )
            shadow = gloss_badge.SHADOW_PATH(user)
            shadow.parent.mkdir(parents=True)
            shadow.write_text('env.marker = ""\n-- old full copy\n', encoding="utf-8")
            with patch.object(gloss_badge.paths, "rime_user_dir", return_value=user), \
                    patch.object(gloss_badge, "installed_filter_path", return_value=installed):
                self.assertTrue(gloss_badge.plain_gloss_state(user)["needs_update"])
                result = gloss_badge.apply_plain_gloss(deploy=False)
                self.assertTrue(result["ok"])
                content = shadow.read_text(encoding="utf-8")
                self.assertIn("loadfile(source)", content)
                self.assertIn('env.marker = ""', content)
                self.assertNotIn("old full copy", content)
                self.assertFalse(gloss_badge.plain_gloss_state(user)["needs_update"])
                self.assertIsNotNone(result["backup_path"])


if __name__ == "__main__":
    unittest.main()
