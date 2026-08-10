"""Tests for agentbox resource discovery, classification, and mounts."""

from __future__ import annotations

import importlib.util
import json
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

        resources = agentbox.parse_container_entries(json.loads(payload))

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

        resources = agentbox.parse_container_entries(json.loads(payload))

        self.assertEqual(
            [resource.name for resource in resources],
            ["agentbox-app-abc123-shell-1"],
        )

    def test_unlabeled_container_has_no_project_path(self):
        payload = '[{"Names": ["agentbox-app-abc123-shell-1"], "Labels": {}}]'

        resources = agentbox.parse_container_entries(json.loads(payload))

        self.assertIsNone(resources[0].project_path)

    def test_tolerates_missing_labels_key(self):
        payload = '[{"Names": ["agentbox-app-abc123-shell-1"]}]'

        resources = agentbox.parse_container_entries(json.loads(payload))

        self.assertIsNone(resources[0].project_path)


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

        resources = agentbox.parse_volume_entries(json.loads(payload))

        self.assertEqual(resources[0].kind, "volume")
        self.assertEqual(resources[0].project_path, "/home/u/app")

    def test_marks_shared_volumes_as_shared(self):
        payload = """
        [
          {"Name": "agentbox-shared", "Labels": {}},
          {"Name": "agentbox-app-abc123-home", "Labels": {}}
        ]
        """

        resources = agentbox.parse_volume_entries(json.loads(payload))
        shared = {
            resource.name: resource.shared for resource in resources
        }

        self.assertEqual(
            shared,
            {
                "agentbox-shared": True,
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

        resources = agentbox.parse_image_entries(json.loads(payload))

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

        resources = agentbox.parse_image_entries(json.loads(payload))

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

        resources = agentbox.parse_image_entries(json.loads(f"[{entry},{entry}]"))

        self.assertEqual([r.identifier for r in resources], ["sha256:same"])

    def test_ignores_the_published_base_image(self):
        payload = """
        [
          {"Id": "sha256:1", "Names": ["ghcr.io/gflorio/agentbox:latest"]}
        ]
        """

        self.assertEqual(agentbox.parse_image_entries(json.loads(payload)), [])


class SelectForRemovalTest(unittest.TestCase):
    def resource(self, name, project_path, *, shared=False):
        return agentbox.Resource(
            kind="volume",
            name=name,
            identifier=name,
            project_path=project_path,
            shared=shared,
        )

    def select(self, resources, *, all_resources=False, existing=()):
        return [
            resource.name
            for resource in agentbox.select_for_removal(
                resources,
                all_resources=all_resources,
                directory_exists=lambda path: path in existing,
            )
        ]

    def test_keeps_a_resource_whose_directory_still_exists(self):
        resources = [self.resource("a", "/home/u/app")]

        self.assertEqual(self.select(resources, existing={"/home/u/app"}), [])

    def test_removes_a_resource_whose_directory_is_gone(self):
        resources = [self.resource("a", "/home/u/gone")]

        self.assertEqual(self.select(resources), ["a"])

    def test_keeps_an_unlabeled_resource(self):
        """Without a project path there is no directory to check."""

        self.assertEqual(self.select([self.resource("a", None)]), [])

    def test_keeps_a_shared_volume_even_when_labeled(self):
        resources = [self.resource("shared", "/home/u/gone", shared=True)]

        self.assertEqual(self.select(resources), [])

    def test_checks_each_resource_against_its_own_directory(self):
        resources = [
            self.resource("live-one", "/home/u/app"),
            self.resource("dead-one", "/home/u/gone"),
        ]

        self.assertEqual(
            self.select(resources, existing={"/home/u/app"}), ["dead-one"]
        )

    def test_all_mode_removes_everything_including_shared_state(self):
        resources = [
            self.resource("live-one", "/home/u/app"),
            self.resource("dead-one", "/home/u/gone"),
            self.resource("shared", None, shared=True),
        ]

        self.assertEqual(
            self.select(resources, all_resources=True, existing={"/home/u/app"}),
            ["live-one", "dead-one", "shared"],
        )


class GlobalConfigMountsTest(unittest.TestCase):
    def setUp(self):
        import tempfile

        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.source = Path(self.temporary.name)

    def entries(self):
        return agentbox.global_config_entries(self.source)

    def test_returns_nothing_when_the_directory_is_absent(self):
        self.assertEqual(
            agentbox.global_config_entries(self.source / "missing"),
            [],
        )

    def test_includes_every_subdirectory(self):
        (self.source / "agents").mkdir()
        (self.source / "skills").mkdir()

        self.assertEqual(self.entries(), ["agents", "skills"])

    def test_includes_node_modules_so_global_plugins_resolve(self):
        (self.source / "node_modules").mkdir()

        self.assertEqual(self.entries(), ["node_modules"])

    def test_includes_package_json(self):
        (self.source / "package.json").write_text("{}", encoding="utf-8")

        self.assertEqual(self.entries(), ["package.json"])

    def test_excludes_host_opencode_config(self):
        (self.source / "opencode.jsonc").write_text("{}", encoding="utf-8")

        self.assertEqual(self.entries(), [])

    def test_excludes_unrelated_top_level_files(self):
        (self.source / "package-lock.json").write_text("{}", encoding="utf-8")
        (self.source / ".gitignore").write_text("x", encoding="utf-8")

        self.assertEqual(self.entries(), [])


class PodmanRunCommandTest(unittest.TestCase):
    def setUp(self):
        import tempfile

        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)

        self.root = Path(self.temporary.name) / "app"
        self.root.mkdir()

        self.home = home = Path(self.temporary.name) / "home"
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

    def mount_destinations(self):
        return [
            argument.split(":")[1]
            for argument in self.volume_arguments()
        ]

    def test_labels_the_container_with_the_project_path(self):
        self.assertIn(
            f"{agentbox.PROJECT_PATH_LABEL}={self.root}",
            self.command,
        )

    def test_mounts_the_shared_volume_at_a_neutral_path(self):
        self.assertIn(
            f"{agentbox.SHARED_VOLUME}:{agentbox.SHARED_PATH}:rw,U",
            self.volume_arguments(),
        )

    def test_does_not_mount_over_the_opencode_data_directory(self):
        """Session history must stay in the per-project agent home."""

        self.assertNotIn(
            agentbox.OPENCODE_DATA_PATH,
            self.mount_destinations(),
        )

    def test_never_mounts_below_the_agent_home(self):
        """
        Podman creates missing intermediate mountpoints as root inside the
        home volume, which makes their parents unwritable by the agent. Every
        mount under the home must therefore be exactly one level deep.
        """

        for destination in self.mount_destinations():
            if not destination.startswith(f"{agentbox.AGENT_HOME}/"):
                continue

            relative = destination[len(agentbox.AGENT_HOME) + 1:]

            self.assertNotIn(
                "/",
                relative,
                f"{destination} would need a root-owned intermediate directory",
            )

    def test_mounts_the_host_config_directory_once(self):
        self.assertIn(
            f"{self.home}/.config/opencode:"
            f"{agentbox.GLOBAL_CONFIG_MOUNT}:ro,z",
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
            f'ln -sfn "{agentbox.SHARED_PATH}/auth.json" '
            f'"{agentbox.OPENCODE_DATA_PATH}/auth.json"',
            self.script(),
        )

    def test_links_the_selected_model_into_the_shared_volume(self):
        self.assertIn(
            f'ln -sfn "{agentbox.SHARED_PATH}/model.json" '
            f'"{agentbox.OPENCODE_STATE_PATH}/model.json"',
            self.script(),
        )

    def test_links_global_config_entries_into_place(self):
        wrapped = agentbox.container_entrypoint(
            ["opencode"],
            config_entries=["agents", "package.json"],
        )

        for name in ("agents", "package.json"):
            self.assertIn(
                f'ln -sfn "{agentbox.GLOBAL_CONFIG_MOUNT}/{name}" '
                f'"{agentbox.GLOBAL_CONFIG_PATH}/{name}"',
                wrapped[2],
            )

    def test_creates_the_state_directory_it_links_into(self):
        script = self.script()

        self.assertIn(f'mkdir -p "{agentbox.OPENCODE_STATE_PATH}"', script)

    def test_does_not_create_the_shared_mountpoint(self):
        """
        The shared path is a mount, already present and owned by the agent.
        Creating it would mask a mount failure.
        """

        self.assertNotIn(
            f'mkdir -p "{agentbox.SHARED_PATH}"',
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
            f'"{agentbox.SHARED_PATH}/auth.json"',
            script,
        )
        self.assertIn(
            f'[ ! -L "{agentbox.OPENCODE_DATA_PATH}/auth.json" ]',
            script,
        )

    def test_rescues_a_model_written_over_the_symlink(self):
        script = self.script()

        self.assertIn(
            f'cp -f "{agentbox.OPENCODE_STATE_PATH}/model.json" '
            f'"{agentbox.SHARED_PATH}/model.json"',
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


class ParseArgumentsTest(unittest.TestCase):
    """A bare invocation must be `run` with every parser default applied."""

    def test_bare_invocation_defaults_to_run(self):
        args = agentbox.parse_arguments([])

        self.assertEqual(args.command, "run")
        self.assertEqual(args.kind, "opencode")
        self.assertEqual(
            args.container_command,
            ["opencode", "--auto", agentbox.WORKSPACE_PATH],
        )
        self.assertFalse(args.rebuild)
        self.assertFalse(args.no_snapshot)
        self.assertIsNone(args.name)
        self.assertEqual(args.network, "slirp4netns")
        self.assertEqual(args.opencode_args, [])

    def test_bare_invocation_keeps_global_options(self):
        args = agentbox.parse_arguments(["--project", "/home/u/app"])

        self.assertEqual(args.command, "run")
        self.assertEqual(args.project, Path("/home/u/app"))

    def test_auth_skips_the_snapshot(self):
        self.assertFalse(agentbox.parse_arguments(["auth"]).snapshot)

    def test_auth_does_not_disable_snapshots_for_other_commands(self):
        """
        `--no-snapshot` is one action object shared by every subparser, so a
        per-subparser default for it would silently disable the recovery
        snapshot everywhere.
        """

        for command in ("run", "shell", []):
            arguments = [command] if command else []

            with self.subTest(command=command or "<bare>"):
                args = agentbox.parse_arguments(arguments)

                self.assertTrue(args.snapshot)
                self.assertFalse(args.no_snapshot)

    def test_commands_without_a_project_root_are_not_session_commands(self):
        self.assertNotIn("clean", agentbox.COMMANDS)


class ProjectResourcesTest(unittest.TestCase):
    def setUp(self):
        self.root = Path("/home/u/app")
        self.marker = f"agentbox-{agentbox.project_key(self.root)}"

    def select(self, resources):
        original = agentbox.discover_resources
        agentbox.discover_resources = lambda: resources
        self.addCleanup(setattr, agentbox, "discover_resources", original)

        return [r.name for r in agentbox.project_resources(self.root)]

    def resource(self, kind, name, project_path=None, *, shared=False):
        return agentbox.Resource(
            kind=kind,
            name=name,
            identifier=name,
            project_path=project_path,
            shared=shared,
        )

    def test_matches_containers_volumes_and_images_by_name(self):
        resources = [
            self.resource("container", f"{self.marker}-shell-1"),
            self.resource("volume", f"{self.marker}-home"),
            self.resource("image", f"localhost/{self.marker}:dev"),
            self.resource("container", "agentbox-other-999-shell-1"),
        ]

        self.assertEqual(len(self.select(resources)), 3)

    def test_matches_by_label_when_the_name_does_not_carry_the_key(self):
        resources = [self.resource("volume", "agentbox-legacy", str(self.root))]

        self.assertEqual(self.select(resources), ["agentbox-legacy"])

    def test_never_includes_shared_state(self):
        resources = [
            self.resource(
                "volume", agentbox.SHARED_VOLUME, str(self.root), shared=True
            )
        ]

        self.assertEqual(self.select(resources), [])


if __name__ == "__main__":
    unittest.main()
