# Copyright © Michal Čihař <michal@weblate.org>
#
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import json
import os
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from weblate.vcs.base import Repository, RepositoryError
from weblate.vcs.multiple import MultipleRepositories


class FakeRepository(Repository):
    instances: dict[str, FakeRepository] = {}

    @classmethod
    def is_supported(cls):
        return True

    @classmethod
    def is_configured(cls):
        return True

    @classmethod
    def get_version(cls):
        return 1

    def __init__(self, path, **kwargs) -> None:
        super().__init__(path, local=True, **kwargs)
        self.key = os.path.basename(path)
        self.configure_remote_calls = []
        self.commit_calls = []
        self.changed_files = []
        self._last_revision = f"local-{self.key}"
        self._last_remote_revision = f"remote-{self.key}"
        self.__class__.instances[self.key] = self

    def is_valid(self):
        return True

    @classmethod
    def create_blank_repository(cls, _path: str) -> None:
        return

    def update_remote(self) -> None:
        return

    def push(self, branch) -> None:
        return

    def reset(self) -> None:
        return

    def merge(
        self, abort: bool = False, message: str | None = None, no_ff: bool = False
    ) -> None:
        return

    def rebase(self, abort=False) -> None:
        return

    def needs_commit(self, filenames: list[str] | None = None) -> bool:
        return bool(filenames)

    def _get_revision_info(self, revision):
        return {
            "summary": f"summary {self.key}",
            "author": "Test <test@example.com>",
            "authordate": "2024-01-01T00:00:00+00:00",
            "commit": revision,
            "commitdate": "2024-01-01T00:00:00+00:00",
            "revision": revision,
            "shortrevision": revision[:7],
        }

    def set_committer(self, name, mail) -> None:
        return

    def commit(
        self,
        message: str,
        author: str | None = None,
        timestamp=None,
        files: list[str] | None = None,
    ) -> bool:
        self.commit_calls.append(files)
        self._last_revision = f"{self._last_revision}-next"
        return bool(files)

    def remove(self, files: list[str], message: str, author: str | None = None) -> None:
        return

    def configure_remote(
        self, pull_url: str, push_url: str, branch: str, fast: bool = True
    ) -> None:
        self.configure_remote_calls.append((pull_url, push_url, branch, fast))

    def configure_branch(self, branch) -> None:
        return

    def describe(self) -> str:
        return self._last_revision

    def get_file(self, path, revision) -> str:
        return f"{self.key}:{path}:{revision}"

    def cleanup(self) -> None:
        return

    def log_revisions(self, refspec) -> list[str]:
        return []

    def parse_changed_files(self, lines: list[str]):
        return iter(lines)

    @property
    def last_remote_revision(self):
        return self._last_remote_revision

    def get_last_revision(self):
        return self._last_revision

    def get_changed_files(self, compare_to: str | None = None):
        return list(self.changed_files)


class MultipleRepositoriesTest(TestCase):
    def setUp(self) -> None:
        super().setUp()
        FakeRepository.instances = {}

    def create_repositories(self):
        return json.dumps(
            {
                "pl": {"vcs": "fake", "repo": "https://example.com/pl.git"},
                "fr": {"vcs": "fake", "repo": "https://example.com/fr.git"},
            }
        )

    def create_push_repositories(self):
        return json.dumps(
            {
                "pl": {"vcs": "fake", "repo": "ssh://example.com/pl.git"},
                "fr": {"vcs": "fake", "repo": "ssh://example.com/fr.git"},
            }
        )

    def test_routes_commit_files_per_repository(self) -> None:
        with TemporaryDirectory() as tempdir, patch(
            "weblate.vcs.multiple.VCS_REGISTRY", {"fake": FakeRepository}
        ):
            multi = MultipleRepositories(
                tempdir, branch="main", local=True, repo=self.create_repositories()
            )
            multi.commit("Update translations", files=["pl/about.po", "fr/about.po"])

            self.assertEqual([["about.po"]], FakeRepository.instances["pl"].commit_calls)
            self.assertEqual([["about.po"]], FakeRepository.instances["fr"].commit_calls)

    def test_configures_pull_and_push_urls_per_repository(self) -> None:
        with TemporaryDirectory() as tempdir, patch(
            "weblate.vcs.multiple.VCS_REGISTRY", {"fake": FakeRepository}
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

    def test_prefixes_changed_files(self) -> None:
        with TemporaryDirectory() as tempdir, patch(
            "weblate.vcs.multiple.VCS_REGISTRY", {"fake": FakeRepository}
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
        with TemporaryDirectory() as tempdir, patch(
            "weblate.vcs.multiple.VCS_REGISTRY", {"fake": FakeRepository}
        ):
            multi = MultipleRepositories(
                tempdir, branch="main", local=True, repo=self.create_repositories()
            )
            with self.assertRaises(RepositoryError):
                multi.commit("Invalid", files=["about.po"])
