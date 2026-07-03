# Copyright © Maciej Olko <maciej.olko@gmail.com>
#
# SPDX-License-Identifier: GPL-3.0-or-later
import os
from collections import defaultdict
from contextlib import ExitStack
from hashlib import sha256
from json import loads
from operator import attrgetter, itemgetter

from django.utils.translation import gettext, gettext_lazy

from weblate.utils.lock import WeblateLock
from weblate.vcs.base import Repository, RepositoryError
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
        repo: str | None = None,
    ) -> None:
        super().__init__(
            path,
            branch=branch,
            component=component,
            local=local,
        )
        self.repo_configs = self.parse_repo_config(
            repo if repo is not None else getattr(component, "repo", None)
        )
        self.repositories: list[Repository] = []
        self.repositories_by_key: dict[str, Repository] = {}
        self._locks: list[WeblateLock] = []
        for key, config in self.repo_configs.items():
            repo_path = os.path.join(self.path, key)
            repository = VCS_REGISTRY[config["vcs"]](
                repo_path,
                branch=branch,
                component=component,
                local=local,
                repo=config["repo"],
            )
            self.repositories.append(repository)
            self.repositories_by_key[key] = repository
            self._locks.append(
                WeblateLock(
                    lock_path=os.path.dirname(
                        base_path := repository.path.rstrip("/").rstrip("\\")
                    ),
                    scope="repo",
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

    @classmethod
    def parse_repo_config(cls, config: str | None) -> dict[str, dict[str, str]]:
        if not config:
            raise RepositoryError(0, "Missing repositories configuration.")
        try:
            parsed = loads(config)
        except ValueError as error:
            raise RepositoryError(0, f"Invalid repositories configuration: {error}") from error
        if not isinstance(parsed, dict):
            raise RepositoryError(0, "Repositories configuration has to be a JSON object.")
        result: dict[str, dict[str, str]] = {}
        for key, value in parsed.items():
            if not isinstance(key, str):
                raise RepositoryError(0, "Repository key has to be a string.")
            if "/" in key or "\\" in key or key in {"", ".", ".."}:
                raise RepositoryError(0, f"Invalid repository key: {key}.")
            if isinstance(value, str):
                result[key] = {"vcs": "git", "repo": value}
                continue
            if not isinstance(value, dict):
                raise RepositoryError(
                    0, f"Repository definition for {key} has to be object or string."
                )
            vcs = value.get("vcs")
            repo = value.get("repo")
            if not vcs or not isinstance(vcs, str):
                raise RepositoryError(0, f"Missing vcs for repository {key}.")
            if vcs not in VCS_REGISTRY:
                raise RepositoryError(0, f"Unsupported vcs {vcs!r} for repository {key}.")
            if not repo or not isinstance(repo, str):
                raise RepositoryError(0, f"Missing repo URL for repository {key}.")
            result[key] = {"vcs": vcs, "repo": repo}
        if not result:
            raise RepositoryError(0, "No repositories configured.")
        return result

    def _iter_paths(self, files: list[str]) -> dict[Repository, list[str]]:
        result: dict[Repository, list[str]] = defaultdict(list)
        for filename in files:
            key, _, subpath = filename.partition("/")
            if not subpath:
                msg = f"File path does not include repository key: {filename}"
                raise RepositoryError(0, msg)
            try:
                repository = self.repositories_by_key[key]
            except KeyError as error:
                raise RepositoryError(0, f"Unknown repository key in path: {filename}") from error
            result[repository].append(subpath)
        return result

    def _repository_for_path(self, path: str) -> tuple[str, Repository, str]:
        key, _, subpath = path.partition("/")
        if not subpath:
            msg = f"File path does not include repository key: {path}"
            raise RepositoryError(0, msg)
        try:
            repository = self.repositories_by_key[key]
        except KeyError as error:
            raise RepositoryError(0, f"Unknown repository key in path: {path}") from error
        return key, repository, subpath

    def _combined_revision(self, *, remote: bool) -> str:
        revisions = []
        for key, repository in self.repositories_by_key.items():
            revision = (
                repository.last_remote_revision if remote else repository.last_revision
            ).strip()
            revisions.append((key, revision))
        digest = sha256()
        for key, revision in sorted(revisions):
            digest.update(f"{key}\0{revision}\0".encode())
        return digest.hexdigest()

    def is_valid(self):
        return all(repo.is_valid() for repo in self.repositories)

    def get_last_revision(self):
        return self._combined_revision(remote=False)

    @property
    def last_remote_revision(self):
        return self._combined_revision(remote=True)

    def configure_remote(
        self, pull_url: str, push_url: str, branch: str, fast: bool = True
    ) -> None:
        pull_urls = self.parse_repo_config(pull_url)
        push_urls = self.parse_repo_config(push_url) if push_url else pull_urls

        for key, repository in self.repositories_by_key.items():
            pull_config = pull_urls[key]
            push_config = push_urls.get(key, pull_config)
            repository.configure_remote(
                pull_config["repo"], push_config["repo"], branch, fast
            )
        self.clean_revision_cache()

    def set_committer(self, name, mail) -> None:
        for repository in self.repositories:
            repository.set_committer(name, mail)

    def update_remote(self) -> None:
        for repository in self.repositories:
            repository.update_remote()
        self.clean_revision_cache()

    def configure_branch(self, branch) -> None:
        for repository in self.repositories:
            repository.configure_branch(branch)
        self.clean_revision_cache()

    def status(self):
        return "\n".join(
            f"[{key}]\n{repository.status()}"
            for key, repository in self.repositories_by_key.items()
        )

    def push(self, branch) -> None:
        for repository in self.repositories:
            repository.push(branch)
        self.clean_revision_cache()

    def unshallow(self) -> None:
        for repository in self.repositories:
            repository.unshallow()

    def reset(self) -> None:
        for repository in self.repositories:
            repository.reset()
        self.clean_revision_cache()

    def merge(
        self, abort: bool = False, message: str | None = None, no_ff: bool = False
    ) -> None:
        for repository in self.repositories:
            repository.merge(abort=abort, message=message, no_ff=no_ff)
        self.clean_revision_cache()

    def rebase(self, abort=False) -> None:
        for repository in self.repositories:
            repository.rebase(abort=abort)
        self.clean_revision_cache()

    def needs_commit(self, filenames: list[str] | None = None) -> bool:
        if filenames is None:
            return any(repository.needs_commit() for repository in self.repositories)
        for repository, repository_files in self._iter_paths(filenames).items():
            if repository.needs_commit(repository_files):
                return True
        return False

    def count_missing(self):
        return sum(repository.count_missing() for repository in self.repositories)

    def count_outgoing(self, branch: str | None = None):
        return sum(repository.count_outgoing(branch) for repository in self.repositories)

    def _get_revision_info(self, revision):
        infos = [
            repository.get_revision_info(repository.last_revision)
            for repository in self.repositories
        ]
        latest = max(infos, key=itemgetter("commitdate"))
        author_date = latest["authordate"]
        commit_date = latest["commitdate"]
        if hasattr(author_date, "isoformat"):
            author_date = author_date.isoformat()
        if hasattr(commit_date, "isoformat"):
            commit_date = commit_date.isoformat()
        return {
            "summary": gettext("Aggregate revision for many repositories"),
            "author": latest["author"],
            "authordate": author_date,
            "commit": revision,
            "commitdate": commit_date,
            "revision": revision,
            "shortrevision": revision[:7],
        }

    def commit(
        self,
        message: str,
        author: str | None = None,
        timestamp=None,
        files: list[str] | None = None,
    ) -> bool:
        changes = False
        if files is None:
            for repository in self.repositories:
                changes |= repository.commit(message, author, timestamp)
        else:
            for repository, repository_files in self._iter_paths(files).items():
                changes |= repository.commit(message, author, timestamp, repository_files)
        self.clean_revision_cache()
        return changes

    def remove(self, files: list[str], message: str, author: str | None = None) -> None:
        for repository, repository_files in self._iter_paths(files).items():
            repository.remove(repository_files, message, author)
        self.clean_revision_cache()

    def get_object_hash(self, path):
        _key, repository, subpath = self._repository_for_path(path)
        return repository.get_object_hash(subpath)

    def get_file(self, path, revision) -> str:
        _key, repository, subpath = self._repository_for_path(path)
        return repository.get_file(subpath, revision)

    def cleanup(self) -> None:
        for repository in self.repositories:
            repository.cleanup()

    def get_changed_files(self, compare_to: str | None = None):
        files = []
        for key, repository in self.repositories_by_key.items():
            files.extend(
                f"{key}/{filename}"
                for filename in repository.get_changed_files(compare_to)
            )
        return files

    def list_remote_branches(self):
        return sorted(
            branch
            for repository in self.repositories
            for branch in repository.list_remote_branches()
        )

    def compact(self) -> None:
        for repository in self.repositories:
            repository.compact()


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
