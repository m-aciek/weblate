# Copyright © Michal Čihař <michal@weblate.org>
#
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import json
import os
from contextlib import ExitStack
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING, ClassVar
from unittest.mock import call, patch

from django.test import SimpleTestCase

from weblate.vcs.base import Repository, RepositoryError, RepositoryLock
from weblate.vcs.git import GitRepository
from weblate.vcs.multiple import MultipleRepositories

if TYPE_CHECKING:
    from collections.abc import Iterator
    from contextlib import AbstractContextManager

    from weblate.trans.models import Component
    from weblate.vcs.base import RawCommitInfo


class FakeRepository(Repository):
    instances: ClassVar[dict[str, FakeRepository]] = {}

    @classmethod
    def is_supported(cls) -> bool:
        return True

    @classmethod
    def is_configured(cls) -> bool:
        return True

    @classmethod
    def get_version(cls) -> str:
        return "1"

    def __init__(
        self,
        path: str,
        *,
        branch: str | None = None,
        component: Component | None = None,
        local: bool = False,
        repo: str | None = None,
    ) -> None:
        super().__init__(
            path, branch=branch, component=component, local=local, repo=repo
        )
        self.key = os.path.basename(path)
        self.configure_remote_calls: list[tuple[str, str, str, bool]] = []
        self.clone_from_calls: list[str] = []
        self.commit_calls: list[list[str] | None] = []
        self.update_remote_locked_states: list[bool] = []
        self.changed_files: list[str] = []
        self._last_revision = f"local-{self.key}"
        self._last_remote_revision = f"remote-{self.key}"
        self.__class__.instances[self.key] = self

    def is_valid(self) -> bool:
        return True

    @classmethod
    def create_blank_repository(cls, _path: str) -> None:
        return

    def update_remote(self) -> None:
        self.update_remote_locked_states.append(self.lock.is_locked)

    def push(self, branch: str) -> None:
        return

    def reset(self) -> None:
        return

    def merge(
        self, abort: bool = False, message: str | None = None, no_ff: bool = False
    ) -> None:
        return

    def rebase(self, abort: bool = False) -> None:
        return

    def needs_commit(self, filenames: list[str] | None = None) -> bool:
        return bool(filenames)

    def _get_revision_info(self, revision: str) -> RawCommitInfo:
        return {
            "summary": f"summary {self.key}",
            "message": f"summary {self.key}",
            "author": "Test <test@example.com>",
            "authordate": "2024-01-01T00:00:00+00:00",
            "commit": revision,
            "commitdate": "2024-01-01T00:00:00+00:00",
            "revision": revision,
            "shortrevision": revision[:7],
        }

    def set_committer(self, name: str, mail: str) -> None:
        return

    def commit(
        self,
        message: str,
        author: str | None = None,
        timestamp: datetime | None = None,
        files: list[str] | None = None,
    ) -> bool:
        self.commit_calls.append(files)
        self._last_revision = f"{self._last_revision}-next"
        self.clean_revision_cache()
        return bool(files)

    def remove(self, files: list[str], message: str, author: str | None = None) -> None:
        return

    def configure_remote(
        self, pull_url: str, push_url: str, branch: str, fast: bool = True
    ) -> None:
        self.configure_remote_calls.append((pull_url, push_url, branch, fast))

    def clone_from(self, source: str) -> None:
        self.clone_from_calls.append(source)

    def configure_branch(self, branch: str) -> None:
        return

    def describe(self) -> str:
        return self._last_revision

    def get_file(self, path: str, revision: str) -> str:
        return f"{self.key}:{path}:{revision}"

    def get_object_hash(self, path: str) -> str:
        return f"{self.key}:{path}:hash"

    def cleanup(self) -> None:
        return

    def log_revisions(self, refspec: str) -> list[str]:
        return []

    def parse_changed_files(self, lines: list[str]) -> Iterator[str]:
        return iter(lines)

    @property
    def last_remote_revision(self) -> str:
        return self._last_remote_revision

    def get_last_revision(self) -> str:
        return self._last_revision

    def get_changed_files(self, compare_to: str | None = None) -> list[str]:
        return list(self.changed_files)


class MultipleRepositoriesTest(SimpleTestCase):
    def setUp(self) -> None:
        super().setUp()
        FakeRepository.instances = {}

    def create_repositories(self) -> str:
        return json.dumps(
            {
                "pl": {"vcs": "fake", "repo": "https://example.com/pl.git"},
                "fr": {"vcs": "fake", "repo": "https://example.com/fr.git"},
            }
        )

    def create_push_repositories(self) -> str:
        return json.dumps(
            {
                "pl": {"vcs": "fake", "repo": "ssh://example.com/pl.git"},
                "fr": {"vcs": "fake", "repo": "ssh://example.com/fr.git"},
            }
        )

    def test_clone_from_clones_each_subrepository(self) -> None:
        with (
            TemporaryDirectory() as tempdir,
            patch("weblate.vcs.multiple.VCS_REGISTRY", {"fake": FakeRepository}),
        ):
            multi = MultipleRepositories(
                tempdir, branch="main", local=True, repo=self.create_repositories()
            )
            multi.clone_from(self.create_repositories())

            self.assertEqual(
                FakeRepository.instances["pl"].clone_from_calls,
                ["https://example.com/pl.git"],
            )
            self.assertEqual(
                FakeRepository.instances["fr"].clone_from_calls,
                ["https://example.com/fr.git"],
            )

    def test_routes_commit_files_per_repository(self) -> None:
        with (
            TemporaryDirectory() as tempdir,
            patch("weblate.vcs.multiple.VCS_REGISTRY", {"fake": FakeRepository}),
        ):
            multi = MultipleRepositories(
                tempdir, branch="main", local=True, repo=self.create_repositories()
            )
            multi.commit("Update translations", files=["pl/about.po", "fr/about.po"])

            self.assertEqual(
                [["about.po"]], FakeRepository.instances["pl"].commit_calls
            )
            self.assertEqual(
                [["about.po"]], FakeRepository.instances["fr"].commit_calls
            )

    def test_routes_absolute_commit_files_per_repository(self) -> None:
        with (
            TemporaryDirectory() as tempdir,
            patch("weblate.vcs.multiple.VCS_REGISTRY", {"fake": FakeRepository}),
        ):
            multi = MultipleRepositories(
                tempdir, branch="main", local=True, repo=self.create_repositories()
            )
            multi.commit(
                "Update translations",
                files=[
                    os.path.join(tempdir, "pl", "about.po"),
                    os.path.join(tempdir, "fr", "about.po"),
                ],
            )

            self.assertEqual(
                [["about.po"]], FakeRepository.instances["pl"].commit_calls
            )
            self.assertEqual(
                [["about.po"]], FakeRepository.instances["fr"].commit_calls
            )

    def test_configures_pull_and_push_urls_per_repository(self) -> None:
        with (
            TemporaryDirectory() as tempdir,
            patch("weblate.vcs.multiple.VCS_REGISTRY", {"fake": FakeRepository}),
        ):
            multi = MultipleRepositories(
                tempdir, branch="main", local=True, repo=self.create_repositories()
            )
            multi.configure_remote(
                self.create_repositories(), self.create_push_repositories(), "main"
            )

            self.assertEqual(
                [
                    (
                        "https://example.com/pl.git",
                        "ssh://example.com/pl.git",
                        "main",
                        True,
                    )
                ],
                FakeRepository.instances["pl"].configure_remote_calls,
            )
            self.assertEqual(
                [
                    (
                        "https://example.com/fr.git",
                        "ssh://example.com/fr.git",
                        "main",
                        True,
                    )
                ],
                FakeRepository.instances["fr"].configure_remote_calls,
            )

    def test_update_remote_holds_subrepository_locks(self) -> None:
        with (
            TemporaryDirectory() as tempdir,
            patch("weblate.vcs.multiple.VCS_REGISTRY", {"fake": FakeRepository}),
        ):
            multi = MultipleRepositories(
                tempdir, branch="main", local=True, repo=self.create_repositories()
            )

            with multi.lock:
                multi.update_remote()

            self.assertEqual(
                FakeRepository.instances["pl"].update_remote_locked_states, [True]
            )
            self.assertEqual(
                FakeRepository.instances["fr"].update_remote_locked_states, [True]
            )

    def test_without_recovery_delegates_to_subrepository_locks(self) -> None:
        with (
            TemporaryDirectory() as tempdir,
            patch("weblate.vcs.multiple.VCS_REGISTRY", {"fake": FakeRepository}),
        ):
            multi = MultipleRepositories(
                tempdir, branch="main", local=True, repo=self.create_repositories()
            )

            with multi.lock.without_recovery(), multi.lock:
                multi.update_remote()

            self.assertEqual(
                FakeRepository.instances["pl"].update_remote_locked_states, [True]
            )
            self.assertEqual(
                FakeRepository.instances["fr"].update_remote_locked_states, [True]
            )

    def test_get_object_hash_routes_absolute_path(self) -> None:
        with (
            TemporaryDirectory() as tempdir,
            patch("weblate.vcs.multiple.VCS_REGISTRY", {"fake": FakeRepository}),
        ):
            multi = MultipleRepositories(
                tempdir, branch="main", local=True, repo=self.create_repositories()
            )

            result = multi.get_object_hash(os.path.join(tempdir, "pl", "about.po"))

            self.assertEqual(result, "pl:about.po:hash")

    def test_reacquire_delegates_to_subrepository_locks(self) -> None:
        with (
            TemporaryDirectory() as tempdir,
            patch("weblate.vcs.multiple.VCS_REGISTRY", {"fake": FakeRepository}),
        ):
            multi = MultipleRepositories(
                tempdir, branch="main", local=True, repo=self.create_repositories()
            )

            with (
                patch.object(
                    FakeRepository.instances["pl"].lock.lock_object, "reacquire"
                ) as pl_reacquire,
                patch.object(
                    FakeRepository.instances["fr"].lock.lock_object, "reacquire"
                ) as fr_reacquire,
            ):
                multi.lock.reacquire()

            pl_reacquire.assert_called_once_with()
            fr_reacquire.assert_called_once_with()

    def test_nested_lock_contexts_keep_outer_locks_active(self) -> None:
        with (
            TemporaryDirectory() as tempdir,
            patch("weblate.vcs.multiple.VCS_REGISTRY", {"fake": FakeRepository}),
        ):
            multi = MultipleRepositories(
                tempdir, branch="main", local=True, repo=self.create_repositories()
            )

            with multi.lock:
                with multi.lock:
                    self.assertTrue(
                        all(
                            repository.lock.is_locked
                            for repository in FakeRepository.instances.values()
                        )
                    )
                self.assertTrue(
                    all(
                        repository.lock.is_locked
                        for repository in FakeRepository.instances.values()
                    )
                )
            self.assertTrue(
                all(
                    not repository.lock.is_locked
                    for repository in FakeRepository.instances.values()
                )
            )

    def test_prefixes_changed_files(self) -> None:
        with (
            TemporaryDirectory() as tempdir,
            patch("weblate.vcs.multiple.VCS_REGISTRY", {"fake": FakeRepository}),
        ):
            multi = MultipleRepositories(
                tempdir, branch="main", local=True, repo=self.create_repositories()
            )
            FakeRepository.instances["pl"].changed_files = ["about.po"]
            FakeRepository.instances["fr"].changed_files = ["tutorial.po"]

            self.assertEqual(
                ["pl/about.po", "fr/tutorial.po"],
                multi.get_changed_files(),
            )

    def test_invalid_paths_raise_error(self) -> None:
        with (
            TemporaryDirectory() as tempdir,
            patch("weblate.vcs.multiple.VCS_REGISTRY", {"fake": FakeRepository}),
        ):
            multi = MultipleRepositories(
                tempdir, branch="main", local=True, repo=self.create_repositories()
            )
            with self.assertRaises(RepositoryError):
                multi.commit("Invalid", files=["about.po"])

    def test_git_subrepositories_keep_their_own_repo_url(self) -> None:
        config = json.dumps(
            {
                "pl": {"vcs": "git", "repo": "https://example.com/pl.git"},
                "fr": {"vcs": "git", "repo": "https://example.com/fr.git"},
            }
        )
        with TemporaryDirectory() as tempdir:
            multi = MultipleRepositories(
                tempdir, branch="main", local=True, repo=config
            )

        self.assertEqual(len(multi.repositories), 2)
        self.assertEqual(sorted(multi.repositories_by_key), ["fr", "pl"])
        self.assertEqual(
            [repository.repo for repository in multi.repositories],
            ["https://example.com/pl.git", "https://example.com/fr.git"],
        )

    def test_get_remote_branch_delegates_to_subrepositories(self) -> None:
        config = json.dumps(
            {
                "pl": {"vcs": "git", "repo": "https://example.com/pl.git"},
                "fr": {"vcs": "git", "repo": "https://example.com/fr.git"},
            }
        )
        with (
            patch("weblate.vcs.multiple.VCS_REGISTRY", {"git": GitRepository}),
            patch(
                "weblate.vcs.git.GitRepository.get_remote_branch",
                side_effect=["main", "main"],
            ) as get_remote_branch,
        ):
            branch = MultipleRepositories.get_remote_branch(config)

        self.assertEqual(branch, "main")
        self.assertEqual(
            get_remote_branch.call_args_list,
            [
                call("https://example.com/pl.git"),
                call("https://example.com/fr.git"),
            ],
        )

    def test_get_remote_branch_rejects_conflicting_subrepository_defaults(self) -> None:
        config = json.dumps(
            {
                "pl": {"vcs": "git", "repo": "https://example.com/pl.git"},
                "fr": {"vcs": "git", "repo": "https://example.com/fr.git"},
            }
        )
        with (
            patch("weblate.vcs.multiple.VCS_REGISTRY", {"git": GitRepository}),
            patch(
                "weblate.vcs.git.GitRepository.get_remote_branch",
                side_effect=["main", "master"],
            ),
            self.assertRaisesMessage(
                RepositoryError,
                "Repositories use different default branches, please configure the branch explicitly.",
            ),
        ):
            MultipleRepositories.get_remote_branch(config)

    def test_get_remote_branch_reports_repository_key_on_failure(self) -> None:
        config = json.dumps(
            {
                "pl": {"vcs": "git", "repo": "https://example.com/pl.git"},
                "fr": {"vcs": "git", "repo": "https://example.com/fr.git"},
            }
        )
        with (
            patch("weblate.vcs.multiple.VCS_REGISTRY", {"git": GitRepository}),
            patch(
                "weblate.vcs.git.GitRepository.get_remote_branch",
                side_effect=[RepositoryError(1, "boom"), "main"],
            ),
            self.assertRaisesMessage(
                RepositoryError,
                "Could not determine the default branch for repository pl: boom",
            ),
        ):
            MultipleRepositories.get_remote_branch(config)


class MultipleRepositoriesRevisionTest(SimpleTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.stack = self.enterContext(ExitStack())
        # unittest closes the stack and its temporary directory after the test.
        # pylint: disable-next=consider-using-with
        self.tempdir = self.stack.enter_context(TemporaryDirectory())
        self.stack.enter_context(
            patch("weblate.vcs.multiple.VCS_REGISTRY", {"fake": FakeRepository})
        )
        self.config = json.dumps(
            {
                key: {"vcs": "fake", "repo": f"https://example.com/{key}.git"}
                for key in ("pl", "fr")
            }
        )
        self.multi = self.new_backend()

    def new_backend(self) -> MultipleRepositories:
        return MultipleRepositories(
            self.tempdir, branch="main", local=True, repo=self.config
        )

    def snapshot_path(self, revision: str) -> Path:
        return (
            Path(self.tempdir)
            / ".weblate-many-repositories"
            / "revisions"
            / f"{revision}.json"
        )

    def test_push_detection_delegates_to_each_child(self) -> None:
        for method in (
            "get_outgoing_revisions",
            "get_tracked_outgoing_revisions",
            "get_push_revisions",
        ):
            for outgoing in (
                ([], []),
                (["same"], []),
                ([], ["same"]),
                (["same"], ["same"]),
            ):
                with (
                    self.subTest(method=method, outgoing=outgoing),
                    ExitStack() as stack,
                ):
                    mocks = [
                        stack.enter_context(
                            patch.object(child, method, return_value=values)
                        )
                        for child, values in zip(
                            self.multi.repositories, outgoing, strict=True
                        )
                    ]
                    args = (
                        ()
                        if method == "get_tracked_outgoing_revisions"
                        else ("translations",)
                    )
                    self.assertEqual(
                        getattr(self.multi, method)(*args),
                        [
                            f"{key}/{revision}"
                            for key, values in zip(("pl", "fr"), outgoing, strict=True)
                            for revision in values
                        ],
                    )
                    for mocked in mocks:
                        mocked.assert_called_once_with(*args)
                    if method == "get_push_revisions":
                        self.assertEqual(
                            self.multi.needs_push("translations"), any(outgoing)
                        )

    def test_child_push_errors_propagate(self) -> None:
        for method in (
            "get_outgoing_revisions",
            "get_tracked_outgoing_revisions",
            "get_push_revisions",
        ):
            with self.subTest(method=method), ExitStack() as stack:
                stack.enter_context(
                    patch.object(
                        self.multi.repositories[0], method, return_value=["commit"]
                    )
                )
                stack.enter_context(
                    patch.object(
                        self.multi.repositories[1],
                        method,
                        side_effect=RepositoryError(1, "child failed"),
                    )
                )
                with self.assertRaisesMessage(RepositoryError, "child failed"):
                    getattr(self.multi, method)()

    def test_snapshot_survives_new_backend_and_changes(self) -> None:
        revision = self.multi.last_revision
        self.multi.commit("Update", files=["pl/about.po"])
        multi = self.new_backend()
        with patch.object(
            multi.repositories[0], "get_file", wraps=multi.repositories[0].get_file
        ) as get_file:
            self.assertEqual(
                multi.get_file("pl/about.po", revision), "pl:about.po:local-pl"
            )
        get_file.assert_called_once_with("about.po", "local-pl")

    def test_snapshot_format_and_unchanged_hash(self) -> None:
        revision = self.multi.last_revision
        self.assertEqual(
            revision,
            "b379f71239887ba2db30965b34ecf4d54b458f0ee352ef8997d4dd75b05bd7c7",
        )
        self.assertEqual(
            json.loads(self.snapshot_path(revision).read_text(encoding="utf-8")),
            {
                "version": 1,
                "revisions": {"pl": "local-pl", "fr": "local-fr"},
            },
        )

    def test_missing_snapshot_only_recovers_current_states(self) -> None:
        for remote in (False, True):
            with self.subTest(remote=remote):
                revision = (
                    self.multi.last_remote_revision
                    if remote
                    else self.multi.last_revision
                )
                self.snapshot_path(revision).unlink()
                expected = "remote-pl" if remote else "local-pl"
                self.assertEqual(
                    self.multi.get_file("pl/about.po", revision),
                    f"pl:about.po:{expected}",
                )
                self.assertTrue(self.snapshot_path(revision).exists())
        revision = self.multi.last_revision
        self.snapshot_path(revision).unlink()
        self.multi.commit("Update", files=["pl/about.po"])
        with self.assertRaisesMessage(RepositoryError, "Missing snapshot"):
            self.multi.get_file("pl/about.po", revision)

    def test_invalid_snapshots_fail_without_child_dispatch(self) -> None:
        revision = self.multi.last_revision
        valid = {"version": 1, "revisions": {"pl": "local-pl", "fr": "local-fr"}}
        invalid_commits: tuple[object, ...] = (
            None,
            [],
            "",
            " local-pl",
            "other",
            "local\0pl",
        )
        snapshots = [
            "{",
            "null",
            "[]",
            "\ufeff{}",
            json.dumps({**valid, "version": 2}),
            json.dumps({**valid, "version": True}),
            json.dumps({"revisions": valid["revisions"]}),
            json.dumps({**valid, "revisions": []}),
            json.dumps({**valid, "revisions": {"pl": "local-pl"}}),
            *[
                json.dumps({**valid, "revisions": {"pl": value, "fr": "local-fr"}})
                for value in invalid_commits
            ],
        ]
        with patch.object(self.multi.repositories[0], "get_file") as get_file:
            for snapshot in snapshots:
                with self.subTest(snapshot=snapshot):
                    self.snapshot_path(revision).write_text(snapshot, encoding="utf-8")
                    with self.assertRaises(RepositoryError):
                        self.multi.get_file("pl/about.po", revision)
            get_file.assert_not_called()

    def test_invalid_revision_cannot_select_metadata_path(self) -> None:
        for revision in ("../other", "f" * 63, "F" * 64, "g" * 64):
            with (
                self.subTest(revision=revision),
                self.assertRaisesMessage(RepositoryError, "Invalid aggregate revision"),
            ):
                self.multi.get_changed_files(revision)

    def test_metadata_symlinks_are_rejected(self) -> None:
        revision = self.multi.last_revision
        path = self.snapshot_path(revision)
        path.unlink()
        path.symlink_to(Path(self.tempdir) / "outside.json")
        with self.assertRaisesMessage(RepositoryError, "must not use symlinks"):
            self.multi.get_file("pl/about.po", revision)

    def test_atomic_write_failure_preserves_snapshot(self) -> None:
        revision = self.multi.last_revision
        original = self.snapshot_path(revision).read_bytes()
        with (
            patch.object(Path, "replace", side_effect=OSError("write failed")),
            self.assertRaisesMessage(RepositoryError, "Could not store"),
        ):
            self.multi.get_last_revision()
        self.assertEqual(self.snapshot_path(revision).read_bytes(), original)
        self.assertEqual(
            list(self.snapshot_path(revision).parent.iterdir()),
            [self.snapshot_path(revision)],
        )

    def test_reserved_repository_keys(self) -> None:
        for key in (
            ".weblate-many-repositories",
            ".WEBLATE-MANY-REPOSITORIES",
            "../pl",
            "pl/fr",
            "pl\\fr",
            "pl\0fr",
            "C:",
        ):
            with (
                self.subTest(key=key),
                self.assertRaisesMessage(RepositoryError, "Invalid repository key"),
            ):
                MultipleRepositories.parse_repo_config(
                    json.dumps({key: "https://example.com/repo.git"})
                )

    def test_paths_cannot_escape_child(self) -> None:
        for path in ("pl/../fr/about.po", "pl/../../outside", "pl/..\\fr/about.po"):
            with (
                self.subTest(path=path),
                self.assertRaisesMessage(RepositoryError, "Invalid repository path"),
            ):
                self.multi.commit("Update", files=[path])

    def test_failed_lock_acquisition_releases_locks_and_allows_reuse(self) -> None:
        first = self.multi.repositories_by_key["pl"]
        second = self.multi.repositories_by_key["fr"]
        with (
            self.fail_second_lock(),
            self.assertRaisesMessage(RepositoryError, "locked"),
            self.multi.lock,
        ):
            self.fail("Acquisition should have failed")
        self.assertFalse(first.lock.is_locked)
        self.assertFalse(second.lock.is_locked)
        with self.multi.lock:
            self.assertTrue(self.multi.lock.is_locked)
        self.assertFalse(first.lock.is_locked)
        self.assertFalse(second.lock.is_locked)

    def test_nested_acquisition_failure_preserves_outer_locks(self) -> None:
        first = self.multi.repositories_by_key["pl"]
        second = self.multi.repositories_by_key["fr"]
        with self.multi.lock:
            with (
                self.fail_second_lock(),
                self.assertRaisesMessage(RepositoryError, "locked"),
                self.multi.lock,
            ):
                self.fail("Acquisition should have failed")
            self.assertTrue(self.multi.lock.is_locked)
            with self.multi.lock:
                self.assertTrue(self.multi.lock.is_locked)
        self.assertFalse(first.lock.is_locked)
        self.assertFalse(second.lock.is_locked)

    def fail_second_lock(self) -> AbstractContextManager:
        original = RepositoryLock.__enter__

        def enter(lock: RepositoryLock) -> None:
            if lock is self.multi.repositories[1].lock:
                raise RepositoryError(1, "locked")
            original(lock)

        return patch.object(RepositoryLock, "__enter__", enter)

    def test_body_exception_releases_all_locks(self) -> None:
        with self.assertRaisesMessage(ValueError, "body failed"), self.multi.lock:
            msg = "body failed"
            raise ValueError(msg)
        self.assertTrue(
            all(not child.lock.is_locked for child in self.multi.repositories)
        )


class MultipleRepositoriesGitTest(SimpleTestCase):
    def setUp(self) -> None:
        super().setUp()
        # pylint: disable-next=consider-using-with
        self.tempdir = self.enterContext(TemporaryDirectory())
        self.sources = {}
        configs = {}
        for key in ("pl", "fr"):
            source_path = Path(self.tempdir) / f"source-{key}"
            GitRepository.create_blank_repository(str(source_path))
            source = GitRepository(str(source_path), branch="main", local=True)
            with source.lock:
                source.execute(["checkout", "-b", "main"], remote_op="none")
                source.set_committer("Test", "test@example.com")
                (source_path / "about.po").write_text(
                    f"Original {key}\n", encoding="utf-8"
                )
                source.commit(
                    "Initial",
                    "Original <original@example.com>",
                    datetime(2024, 1, 1, tzinfo=UTC),
                    ["about.po"],
                )
            self.sources[key] = source
            configs[key] = {"vcs": "git", "repo": source_path.as_uri()}
        self.config = json.dumps(configs)
        self.path = str(Path(self.tempdir) / "checkout")
        self.multi = self.new_backend()
        with self.multi.lock:
            self.multi.clone_from(self.config)
            self.multi.configure_remote(self.config, self.config, "main")
            self.multi.set_committer("Test", "test@example.com")

    def new_backend(self) -> MultipleRepositories:
        return MultipleRepositories(
            self.path, branch="main", local=True, repo=self.config
        )

    def commit_file(
        self, repository: Repository, content: str, filename: str = "about.po"
    ) -> None:
        with repository.lock:
            Path(repository.path, filename).write_text(content, encoding="utf-8")
            repository.commit(
                "Changed",
                "Later <later@example.com>",
                datetime(2025, 1, 1, tzinfo=UTC),
                [filename],
            )

    def test_historical_files_comparisons_metadata_and_reset(self) -> None:
        revision = self.multi.last_revision
        self.commit_file(self.multi.repositories_by_key["pl"], "Changed pl\n")
        self.multi.clean_revision_cache()
        self.assertNotEqual(self.multi.last_revision, revision)
        self.assertEqual(self.multi.get_changed_files(revision), ["pl/about.po"])
        self.assertEqual(self.multi.get_file("pl/about.po", revision), "Original pl\n")
        # A new process must be able to resolve the historical mapping from disk.
        multi = self.new_backend()
        self.assertEqual(multi.get_changed_files(revision), ["pl/about.po"])
        info = multi.get_revision_info(revision)
        self.assertEqual(info["author"], "Original <original@example.com>")
        self.assertEqual(info["authordate"], datetime(2024, 1, 1, tzinfo=UTC))
        self.assertEqual(info["message"], "Aggregate revision for many repositories")
        self.assertEqual(info["revision"], revision)
        with multi.lock:
            multi.cleanup()
            multi.reset_to_revision(revision)
        self.assertEqual(multi.last_revision, revision)
        self.assertEqual(
            Path(multi.path, "pl/about.po").read_text(encoding="utf-8"), "Original pl\n"
        )
        self.assertEqual(multi.get_file("fr/about.po", revision), "Original fr\n")

    def test_comparison_after_remote_update(self) -> None:
        revision = self.multi.last_revision
        self.commit_file(self.sources["fr"], "Updated fr\n")
        with self.multi.lock:
            self.multi.update_remote()
            remote_revision = self.multi.last_remote_revision
            self.assertNotEqual(remote_revision, revision)
            self.assertEqual(self.multi.get_changed_files(), ["fr/about.po"])
            self.multi.merge()
        self.assertEqual(self.multi.get_changed_files(revision), ["fr/about.po"])
        self.assertEqual(self.multi.get_changed_files(), [])
        self.assertEqual(self.multi.get_file("fr/about.po", revision), "Original fr\n")
        self.assertEqual(
            self.new_backend().get_file("fr/about.po", remote_revision), "Updated fr\n"
        )

    def test_push_detection_and_separate_push_branch(self) -> None:
        self.assertFalse(self.multi.needs_push())
        self.assertFalse(self.multi.needs_push("translations"))
        for key in ("pl", "fr"):
            with self.subTest(key=key):
                self.commit_file(
                    self.multi.repositories_by_key[key], f"Changed {key}\n"
                )
                self.assertTrue(self.multi.needs_push())
                self.assertTrue(self.multi.needs_push("translations"))
                with self.multi.lock:
                    self.multi.push("translations")
                    for child in self.multi.repositories:
                        child.execute(
                            [
                                "fetch",
                                "origin",
                                "+refs/heads/translations:refs/remotes/origin/translations",
                            ],
                            remote_op="pull",
                        )
                self.assertFalse(self.multi.needs_push("translations"))
                self.assertTrue(self.multi.needs_push())
