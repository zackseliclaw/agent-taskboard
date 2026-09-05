import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from configuration import ROOT, load_config
from taskboard import parse_args


class ConfigurationTest(unittest.TestCase):
    def test_defaults_and_paths_independent_of_working_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            config = load_config(Path(directory) / "missing.ini", environ={})
            self.assertEqual("User", config["identity"]["display_name"])
            self.assertEqual(ROOT / "data/taskboard.db", config["storage"]["database"])
            with patch.dict(os.environ, {}, clear=True):
                args = parse_args(["--display-name", "Reviewer", "--port", "9001"])
                self.assertEqual("Reviewer", args.config["identity"]["display_name"])
                self.assertEqual(9001, args.port)

    def test_file_environment_and_cli_precedence(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.ini"
            path.write_text('[identity]\ndisplay_name = Reviewer\n[server]\nport = 8000\n'
                            '[models]\nhigh = test-model\n[storage]\ndatabase = custom/db.sqlite\n')
            config = load_config(path, environ={"TASKBOARD_PORT": "8001"},
                                 overrides={"port": 8002})
            self.assertEqual(8002, config["server"]["port"])
            self.assertEqual("Reviewer", config["identity"]["display_name"])
            self.assertEqual("test-model", config["models"]["high"])
            self.assertEqual(ROOT / "custom/db.sqlite", config["storage"]["database"])
            self.assertEqual(8001, load_config(path, environ={"TASKBOARD_PORT": "8001"})["server"]["port"])

    def test_invalid_configuration_fails_clearly(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.ini"
            for content in ('broken = [', '[unknown]\nx = 1', '[server]\nport = true',
                            '[server]\nport = 70000', '[identity]\ndisplay_name = ',
                            '[models]\nunknown = value', '[storage]\ndatabase = ',
                            '[DEFAULT]\nhost = localhost', '[server]\nport=8000\nport=8001'):
                with self.subTest(content=content):
                    path.write_text(content)
                    with self.assertRaises(ValueError):
                        load_config(path, environ={})

    def test_blank_models_and_literal_values(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.ini"
            path.write_text(
                "# Whole-line comment\n[identity]\ndisplay_name = User\n"
                "[models]\nlow =\nmedium =  \nhigh = provider/model%name#tag;v1\n"
                "[storage]\ndatabase = data/100%/tasks.db\n"
            )
            config = load_config(path, environ={})
            self.assertEqual("", config["models"]["low"])
            self.assertEqual("", config["models"]["medium"])
            self.assertEqual("", config["models"]["very_high"])
            self.assertEqual("provider/model%name#tag;v1", config["models"]["high"])
            self.assertEqual(ROOT / "data/100%/tasks.db", config["storage"]["database"])
