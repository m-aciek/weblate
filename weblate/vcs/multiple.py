# Copyright © Maciej Olko <maciej.olko@gmail.com>
#
# SPDX-License-Identifier: GPL-3.0-or-later
import os
from contextlib import ExitStack
from json import loads
from operator import attrgetter

from django.utils.translation import gettext_lazy

from weblate.utils.lock import WeblateLock
from weblate.vcs.base import Repository
from weblate.vcs.models import VCS_REGISTRY


class MultipleRepositories(Repository):
    name = "Many repositories"  # limit length to 20
    push_label = gettext_lazy("This will push changes to the upstream repositories.")
    identifier = "many-repositories"

    def __init__(
        self,
        path: str,
        *,
        branch: str | None = None,
        component=None,
        local: bool = False,
        skip_init: bool = False,
        repo: str = None,
    ):
        super().__init__(path, branch=branch, component=component, local=local, skip_init=skip_init)
        pull_urls = loads(repo)
        self.repositories: list[Repository] = []
        self._locks: list[WeblateLock] = []
        for key, repo in pull_urls.items():
            repository = VCS_REGISTRY[repo["vcs"]](
                self.path, branch=branch, component=component, local=local, skip_init=skip_init
            )
            self.repositories.append(repository)
            self._locks.append(
                WeblateLock(
                    lock_path=os.path.dirname(base_path := repository.path.rstrip("/").rstrip("\\")),
                    scope="repo",
                    # key=repo.component.pk if repo.component else os.path.basename(base_path),
                    key=f"{os.path.basename(base_path)}:{key}",
                    slug=os.path.basename(base_path),
                    file_template="{slug}.lock",
                    timeout=30,
                )
            )
        self.lock = MultiContextManager(*self._locks)

    @classmethod
    def is_supported(cls):
        return True  # cannot check internal repos without instantiating the class, assuming True

    @classmethod
    def is_configured(cls):
        return True  # cannot check internal repos without instantiating the class, assuming True

    @classmethod
    def get_version(cls):
        return 1  # cannot check internal repos without instantiating the class, assuming True

    def is_valid(self):
        # return all(repo.is_valid() for repo in self.repositories)
        return True

    def configure_remote(
        self, pull_url: str, push_url: str, branch: str, fast: bool = True
    ):
        pull_urls = loads(pull_url)
        for repository, url in zip(self.repositories, pull_urls):
            repository.configure_remote(pull_url, push_url, branch, fast)

    def set_committer(self, name, mail) -> None:
        for repository in self.repositories:
            repository.set_committer(name, mail)

    @property
    def last_remote_revision(self):
        return self.repositories[0].last_remote_revision

    def update_remote(self) -> None:
        for repository in self.repositories:
            repository.update_remote()

    def configure_branch(self, branch) -> None:
        for repository in self.repositories:
            repository.configure_branch(branch)


class MultiContextManager:
    def __init__(self, *managers):
        self.managers = managers
        self.stack = ExitStack()

    def __enter__(self):
        # Enter each context manager and store them in the stack
        self.entered = [self.stack.enter_context(manager) for manager in self.managers]
        return self.entered

    def __exit__(self, exc_type, exc_value, traceback):
        # Exit all context managers
        return self.stack.__exit__(exc_type, exc_value, traceback)

    @property
    def is_locked(self) -> bool:
        return all(map(attrgetter('is_locked'), self.managers))
