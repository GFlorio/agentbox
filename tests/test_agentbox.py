"""Tests for agentbox resource discovery, classification, and mounts."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import unittest


def load_agentbox():
    script = Path(__file__).resolve().parent.parent / "agentbox"

    spec = importlib.util.spec_from_loader(
        "agentbox",
        importlib.machinery.SourceFileLoader("agentbox", str(script)),
    )

    module = importlib.util.module_from_spec(spec)
    sys.modules["agentbox"] = module
    spec.loader.exec_module(module)

    return module


agentbox = load_agentbox()


class ParseContainersTest(unittest.TestCase):
    def test_reads_name_and_project_path_label(self):
        payload = """
        [
          {
            "Names": ["agentbox-app-abc123-shell-1"],
            "Labels": {"dev.agentbox.project-path": "/home/u/app"}
          }
        ]
        """

        resources = agentbox.parse_container_entries(payload)

        self.assertEqual(len(resources), 1)
        self.assertEqual(resources[0].kind, "container")
        self.assertEqual(resources[0].name, "agentbox-app-abc123-shell-1")
        self.assertEqual(resources[0].project_path, "/home/u/app")

    def test_ignores_containers_not_created_by_agentbox(self):
        payload = """
        [
          {"Names": ["some-other-container"], "Labels": {}},
          {"Names": ["agentbox-app-abc123-shell-1"], "Labels": {}}
        ]
        """

        resources = agentbox.parse_container_entries(payload)

        self.assertEqual(
            [resource.name for resource in resources],
            ["agentbox-app-abc123-shell-1"],
        )

    def test_unlabeled_container_has_no_project_path(self):
        payload = '[{"Names": ["agentbox-app-abc123-shell-1"], "Labels": {}}]'

        resources = agentbox.parse_container_entries(payload)

        self.assertIsNone(resources[0].project_path)

    def test_tolerates_missing_labels_key(self):
        payload = '[{"Names": ["agentbox-app-abc123-shell-1"]}]'

        resources = agentbox.parse_container_entries(payload)

        self.assertIsNone(resources[0].project_path)

    def test_returns_nothing_for_unparseable_payload(self):
        self.assertEqual(agentbox.parse_container_entries("not json"), [])


class ParseVolumesTest(unittest.TestCase):
    def test_reads_name_and_project_path_label(self):
        payload = """
        [
          {
            "Name": "agentbox-app-abc123-home",
            "Labels": {"dev.agentbox.project-path": "/home/u/app"}
          }
        ]
        """

        resources = agentbox.parse_volume_entries(payload)

        self.assertEqual(resources[0].kind, "volume")
        self.assertEqual(resources[0].project_path, "/home/u/app")

    def test_marks_shared_volumes_as_shared(self):
        payload = """
        [
          {"Name": "agentbox-shared-data", "Labels": {}},
          {"Name": "agentbox-shared-state", "Labels": {}},
          {"Name": "agentbox-app-abc123-home", "Labels": {}}
        ]
        """

        resources = agentbox.parse_volume_entries(payload)
        shared = {
            resource.name: resource.shared for resource in resources
        }

        self.assertEqual(
            shared,
            {
                "agentbox-shared-data": True,
                "agentbox-shared-state": True,
                "agentbox-app-abc123-home": False,
            },
        )


class ParseImagesTest(unittest.TestCase):
    def test_reads_id_and_project_path_label(self):
        payload = """
        [
          {
            "Id": "sha256:deadbeef",
            "Names": ["localhost/agentbox-app-abc123:dev"],
            "Labels": {"dev.agentbox.project-path": "/home/u/app"}
          }
        ]
        """

        resources = agentbox.parse_image_entries(payload)

        self.assertEqual(resources[0].kind, "image")
        self.assertEqual(resources[0].identifier, "sha256:deadbeef")
        self.assertEqual(resources[0].name, "localhost/agentbox-app-abc123:dev")
        self.assertEqual(resources[0].project_path, "/home/u/app")

    def test_ignores_images_not_created_by_agentbox(self):
        payload = """
        [
          {"Id": "sha256:1", "Names": ["docker.io/library/ubuntu:24.04"]},
          {"Id": "sha256:2", "Names": ["localhost/agentbox-app-abc123:dev"]}
        ]
        """

        resources = agentbox.parse_image_entries(payload)

        self.assertEqual(
            [resource.identifier for resource in resources],
            ["sha256:2"],
        )

    def test_reports_a_multi_tagged_image_once(self):
        """podman repeats an image entry once per tag."""

        entry = """
          {
            "Id": "sha256:same",
            "Names": [
              "localhost/agentbox-a-1:dev",
              "localhost/agentbox-b-2:dev"
            ]
          }
        """

        resources = agentbox.parse_image_entries(f"[{entry},{entry}]")

        self.assertEqual([r.identifier for r in resources], ["sha256:same"])

    def test_ignores_the_published_base_image(self):
        payload = """
        [
          {"Id": "sha256:1", "Names": ["ghcr.io/gflorio/agentbox:latest"]}
        ]
        """

        self.assertEqual(agentbox.parse_image_entries(payload), [])


class ClassifyResourcesTest(unittest.TestCase):
    def resource(self, name, project_path, *, shared=False):
        return agentbox.Resource(
            kind="volume",
            name=name,
            identifier=name,
            project_path=project_path,
            shared=shared,
        )

    def test_labeled_resource_with_existing_directory_is_live(self):
        groups = agentbox.classify_resources(
            [self.resource("a", "/home/u/app")],
            directory_exists=lambda path: True,
        )

        self.assertEqual([r.name for r in groups["live"]], ["a"])
        self.assertEqual(groups["dangling"], [])
        self.assertEqual(groups["unknown"], [])

    def test_labeled_resource_with_missing_directory_is_dangling(self):
        groups = agentbox.classify_resources(
            [self.resource("a", "/home/u/gone")],
            directory_exists=lambda path: False,
        )

        self.assertEqual([r.name for r in groups["dangling"]], ["a"])
        self.assertEqual(groups["live"], [])

    def test_unlabeled_resource_is_unknown(self):
        groups = agentbox.classify_resources(
            [self.resource("a", None)],
            directory_exists=lambda path: False,
        )

        self.assertEqual([r.name for r in groups["unknown"]], ["a"])
        self.assertEqual(groups["dangling"], [])

    def test_shared_volume_is_unknown_even_when_labeled(self):
        groups = agentbox.classify_resources(
            [self.resource("agentbox-shared-data", "/home/u/app", shared=True)],
            directory_exists=lambda path: False,
        )

        self.assertEqual(
            [r.name for r in groups["unknown"]],
            ["agentbox-shared-data"],
        )

    def test_classifies_each_resource_against_its_own_directory(self):
        existing = {"/home/u/app"}

        groups = agentbox.classify_resources(
            [
                self.resource("live-one", "/home/u/app"),
                self.resource("dead-one", "/home/u/gone"),
            ],
            directory_exists=lambda path: path in existing,
        )

        self.assertEqual([r.name for r in groups["live"]], ["live-one"])
        self.assertEqual([r.name for r in groups["dangling"]], ["dead-one"])


class SelectForRemovalTest(unittest.TestCase):
    def setUp(self):
        self.groups = {
            "live": [self.resource("live-one")],
            "dangling": [self.resource("dead-one")],
            "unknown": [self.resource("mystery")],
        }

    def resource(self, name):
        return agentbox.Resource(
            kind="volume",
            name=name,
            identifier=name,
            project_path=None,
            shared=False,
        )

    def test_default_mode_removes_only_dangling(self):
        selected = agentbox.select_for_removal(self.groups, all_resources=False)

        self.assertEqual([r.name for r in selected], ["dead-one"])

    def test_all_mode_removes_every_category(self):
        selected = agentbox.select_for_removal(self.groups, all_resources=True)

        self.assertEqual(
            sorted(r.name for r in selected),
            ["dead-one", "live-one", "mystery"],
        )


class GlobalConfigMountsTest(unittest.TestCase):
    def setUp(self):
        import tempfile

        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.source = Path(self.temporary.name)

    def mounts(self):
        return agentbox.global_config_mounts(self.source)

    def test_returns_nothing_when_the_directory_is_absent(self):
        self.assertEqual(
            agentbox.global_config_mounts(self.source / "missing"),
            [],
        )

    def test_mounts_every_subdirectory(self):
        (self.source / "agents").mkdir()
        (self.source / "skills").mkdir()

        self.assertEqual(
            [container for _, container in self.mounts()],
            [
                f"{agentbox.AGENT_HOME}/.config/opencode/agents",
                f"{agentbox.AGENT_HOME}/.config/opencode/skills",
            ],
        )

    def test_mounts_node_modules_so_global_plugins_resolve(self):
        (self.source / "node_modules").mkdir()

        self.assertEqual(
            [container for _, container in self.mounts()],
            [f"{agentbox.AGENT_HOME}/.config/opencode/node_modules"],
        )

    def test_mounts_package_json(self):
        (self.source / "package.json").write_text("{}", encoding="utf-8")

        self.assertEqual(
            [container for _, container in self.mounts()],
            [f"{agentbox.AGENT_HOME}/.config/opencode/package.json"],
        )

    def test_excludes_host_opencode_config(self):
        (self.source / "opencode.jsonc").write_text("{}", encoding="utf-8")

        self.assertEqual(self.mounts(), [])

    def test_excludes_unrelated_top_level_files(self):
        (self.source / "package-lock.json").write_text("{}", encoding="utf-8")
        (self.source / ".gitignore").write_text("x", encoding="utf-8")

        self.assertEqual(self.mounts(), [])


class PodmanRunCommandTest(unittest.TestCase):
    def setUp(self):
        import tempfile

        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)

        self.root = Path(self.temporary.name) / "app"
        self.root.mkdir()

        home = Path(self.temporary.name) / "home"
        (home / ".agents").mkdir(parents=True)
        (home / ".config" / "opencode" / "skills").mkdir(parents=True)

        self.command = agentbox.podman_run_command(
            self.root,
            ["opencode"],
            kind="shell",
            session="s1",
            network="slirp4netns",
            home=home,
        )

    def volume_arguments(self):
        return [
            self.command[index + 1]
            for index, item in enumerate(self.command)
            if item == "--volume"
        ]

    def test_labels_the_container_with_the_project_path(self):
        self.assertIn(
            f"{agentbox.PROJECT_PATH_LABEL}={self.root}",
            self.command,
        )

    def test_mounts_the_shared_data_volume_at_a_neutral_path(self):
        self.assertIn(
            f"{agentbox.SHARED_DATA_VOLUME}:"
            f"{agentbox.SHARED_DATA_PATH}:rw,U",
            self.volume_arguments(),
        )

    def test_does_not_mount_over_the_opencode_data_directory(self):
        """Session history must stay in the per-project agent home."""

        destinations = [
            argument.split(":")[1]
            for argument in self.volume_arguments()
        ]

        self.assertNotIn(agentbox.OPENCODE_DATA_PATH, destinations)

    def test_mounts_the_shared_state_volume_over_opencode_state(self):
        self.assertIn(
            f"{agentbox.SHARED_STATE_VOLUME}:"
            f"{agentbox.AGENT_HOME}/.local/state/opencode:rw,U",
            self.volume_arguments(),
        )

    def test_host_directories_use_the_shared_selinux_label(self):
        read_only = [
            argument
            for argument in self.volume_arguments()
            if ":ro," in argument
        ]

        self.assertTrue(read_only)

        for argument in read_only:
            self.assertTrue(
                argument.endswith(":ro,z"),
                f"{argument} must use the shared SELinux label",
            )


class ContainerEntrypointTest(unittest.TestCase):
    def script(self, command=("opencode", "/workspace")):
        wrapped = agentbox.container_entrypoint(list(command))

        self.assertEqual(wrapped[:2], ["bash", "-lc"])

        return wrapped[2]

    def test_links_the_credential_into_the_shared_volume(self):
        self.assertIn(
            f'ln -sfn "{agentbox.SHARED_DATA_PATH}/auth.json" '
            f'"{agentbox.OPENCODE_DATA_PATH}/auth.json"',
            self.script(),
        )

    def test_runs_the_requested_command_last(self):
        self.assertTrue(
            self.script().rstrip().endswith("exec opencode /workspace")
        )

    def test_replaces_the_process_so_signals_reach_the_agent(self):
        self.assertIn("exec ", self.script())

    def test_quotes_arguments_containing_spaces(self):
        script = self.script(["opencode", "run", "two words"])

        self.assertTrue(script.rstrip().endswith("exec opencode run 'two words'"))

    def test_rescues_a_credential_written_over_the_symlink(self):
        """
        An atomic write replaces the symlink with a real file. The next start
        must copy it into the shared volume before relinking, or the login is
        stranded in one worktree.
        """

        script = self.script()

        self.assertIn(
            f'cp -f "{agentbox.OPENCODE_DATA_PATH}/auth.json" '
            f'"{agentbox.SHARED_DATA_PATH}/auth.json"',
            script,
        )
        self.assertIn(
            f'[ ! -L "{agentbox.OPENCODE_DATA_PATH}/auth.json" ]',
            script,
        )

    def test_creates_both_directories_before_linking(self):
        script = self.script()

        self.assertLess(
            script.index("mkdir -p"),
            script.index("ln -sfn"),
        )


class PodmanRunUsesEntrypointTest(PodmanRunCommandTest):
    def test_container_command_is_wrapped(self):
        self.assertEqual(self.command[-3:-1], ["bash", "-lc"])
        self.assertTrue(self.command[-1].rstrip().endswith("exec opencode"))


if __name__ == "__main__":
    unittest.main()
