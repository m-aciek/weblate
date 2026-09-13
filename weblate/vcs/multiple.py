# Copyright © Maciej Olko <maciej.olko@gmail.com>
#
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import os
from collections import defaultdict
from contextlib import ExitStack, contextmanager
from hashlib import sha256
from json import dumps, loads
from operator import attrgetter, itemgetter
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING

from django.utils.translation import gettext, gettext_lazy

from weblate.utils.files import is_unsafe_path
from weblate.utils.lock import WeblateLock
from weblate.vcs.base import Repository, RepositoryError, RepositoryLock
from weblate.vcs.models import VCS_REGISTRY

if TYPE_CHECKING:
    from collections.abc import Generator
    from datetime import datetime
    from types import TracebackType

    from weblate.trans.models import Component
    from weblate.vcs.base import RawCommitInfo


class MultipleRepositories(Repository):
    name = "Many repositories"  # limit length to 20
    push_label = gettext_lazy("This will push changes to the upstream repositories.")
    identifier = "many-repositories"
    metadata_dir_name = ".weblate-many-repositories"

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
        self._locks: list[RepositoryLock] = []
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
            repository.lock.replace_lock(
                RepositoryLock(
                    repository,
                    WeblateLock(
                        lock_path=os.path.dirname(
                            base_path := repository.path.rstrip("/").rstrip("\\")
                        ),
                        scope="repo",
                        key=f"{os.path.basename(base_path)}:{key}",
                        slug=os.path.basename(base_path),
                        file_template="{slug}.lock",
                        timeout=30,
                    ),
                )
            )
            self._locks.append(repository.lock)
        self.lock = MultiContextManager(*self._locks)

    @classmethod
    def is_supported(cls) -> bool:
        return True  # cannot check internal repos without instantiating the class, assuming True

    @classmethod
    def is_configured(cls) -> bool:
        return True  # cannot check internal repos without instantiating the class, assuming True

    @classmethod
    def get_version(cls) -> str:
        return "1"

    @classmethod
    def get_remote_branch(cls, repo: str) -> str:
        branches = set()
        for key, config in cls.parse_repo_config(repo).items():
            try:
                branch = VCS_REGISTRY[config["vcs"]].get_remote_branch(config["repo"])
            except RepositoryError as error:
                raise RepositoryError(
                    error.retcode,
                    gettext(
                        "Could not determine the default branch for repository %(key)s: %(error)s"
                    )
                    % {"key": key, "error": error},
                ) from error
            branches.add(branch)
        branches.discard("")
        if not branches:
            return super().get_remote_branch(repo)
        if len(branches) > 1:
            raise RepositoryError(
                0,
                gettext(
                    "Repositories use different default branches, please configure the branch explicitly."
                ),
            )
        return next(iter(branches))

    @classmethod
    def parse_repo_config(cls, config: str | None) -> dict[str, dict[str, str]]:
        if not config:
            raise RepositoryError(0, "Missing repositories configuration.")
        try:
            parsed = loads(config)
        except ValueError as error:
            raise RepositoryError(
                0, f"Invalid repositories configuration: {error}"
            ) from error
        if not isinstance(parsed, dict):
            raise RepositoryError(
                0, "Repositories configuration has to be a JSON object."
            )
        result: dict[str, dict[str, str]] = {}
        for key, value in parsed.items():
            if not isinstance(key, str):
                raise RepositoryError(0, "Repository key has to be a string.")
            if (
                "/" in key
                or "\\" in key
                or "\0" in key
                or is_unsafe_path(key)
                or key in {"", ".", ".."}
                or key.casefold() == cls.metadata_dir_name
            ):
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
                raise RepositoryError(
                    0, f"Unsupported vcs {vcs!r} for repository {key}."
                )
            if not repo or not isinstance(repo, str):
                raise RepositoryError(0, f"Missing repo URL for repository {key}.")
            result[key] = {"vcs": vcs, "repo": repo}
        if not result:
            raise RepositoryError(0, "No repositories configured.")
        return result

    def _iter_paths(self, files: list[str]) -> dict[Repository, list[str]]:
        result: dict[Repository, list[str]] = defaultdict(list)
        for filename in files:
            _key, repository, subpath = self._repository_for_path(filename)
            result[repository].append(subpath)
        return result

    def _normalize_path(self, path: str) -> str:
        if os.path.isabs(path):
            path = os.path.relpath(path, self.path)
        return path.replace(os.sep, "/")

    def _repository_for_path(self, path: str) -> tuple[str, Repository, str]:
        path = self._normalize_path(path)
        if is_unsafe_path(path) or "\0" in path:
            raise RepositoryError(0, f"Invalid repository path: {path}.")
        key, _, subpath = path.partition("/")
        if not subpath:
            msg = f"File path does not include repository key: {path}"
            raise RepositoryError(0, msg)
        try:
            repository = self.repositories_by_key[key]
        except KeyError as error:
            raise RepositoryError(
                0, f"Unknown repository key in path: {path}"
            ) from error
        return key, repository, subpath

    def _child_revisions(self, *, remote: bool) -> dict[str, str]:
        revisions = {}
        for key, repository in self.repositories_by_key.items():
            revision = (
                repository.last_remote_revision if remote else repository.last_revision
            ).strip()
            revisions[key] = revision
        return revisions

    @staticmethod
    def _revision_hash(revisions: dict[str, str]) -> str:
        digest = sha256()
        for key, revision in sorted(revisions.items()):
            digest.update(f"{key}\0{revision}\0".encode())
        return digest.hexdigest()

    def _snapshot_path(self, revision: str) -> Path:
        if len(revision) != 64 or any(
            char not in "0123456789abcdef" for char in revision
        ):
            raise RepositoryError(0, f"Invalid aggregate revision: {revision}.")
        metadata = Path(self.path) / self.metadata_dir_name
        directory = metadata / "revisions"
        path = directory / f"{revision}.json"
        if any(location.is_symlink() for location in (metadata, directory, path)):
            raise RepositoryError(
                0, "Aggregate revision metadata must not use symlinks."
            )
        return path

    def _store_revision(self, revisions: dict[str, str]) -> str:
        revision = self._revision_hash(revisions)
        path = self._snapshot_path(revision)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            # Keep the temporary file on the same filesystem for atomic replacement.
            with TemporaryDirectory(dir=path.parent) as tempdir:
                temporary = Path(tempdir) / path.name
                temporary.write_text(
                    dumps({"version": 1, "revisions": revisions}, sort_keys=True),
                    encoding="utf-8",
                )
                temporary.replace(path)
        except OSError as error:
            raise RepositoryError(
                0, f"Could not store aggregate revision {revision}: {error}"
            ) from error
        return revision

    def _combined_revision(self, *, remote: bool) -> str:
        return self._store_revision(self._child_revisions(remote=remote))

    def _resolve_revision(self, revision: str) -> dict[str, str]:
        path = self._snapshot_path(revision)
        try:
            snapshot = loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            # Older checkouts only stored hashes. Recover only an exact known state.
            for remote in (False, True):
                revisions = self._child_revisions(remote=remote)
                if self._revision_hash(revisions) == revision:
                    self._store_revision(revisions)
                    return revisions
            raise RepositoryError(
                0, f"Missing snapshot for aggregate revision {revision}."
            ) from None
        except (OSError, ValueError) as error:
            raise RepositoryError(
                0, f"Could not read aggregate revision {revision}: {error}"
            ) from error
        if (
            not isinstance(snapshot, dict)
            or not isinstance(version := snapshot.get("version"), int)
            or isinstance(version, bool)
            or version != 1
            or not isinstance(snapshot_revisions := snapshot.get("revisions"), dict)
            or snapshot_revisions.keys() != self.repositories_by_key.keys()
            or any(
                not isinstance(commit, str)
                or not commit
                or commit != commit.strip()
                or "\0" in commit
                for commit in snapshot_revisions.values()
            )
            or self._revision_hash(snapshot_revisions) != revision
        ):
            raise RepositoryError(
                0, f"Invalid snapshot for aggregate revision {revision}."
            )
        return snapshot_revisions

    def is_valid(self) -> bool:
        return all(repo.is_valid() for repo in self.repositories)

    def get_last_revision(self) -> str:
        return self._combined_revision(remote=False)

    @property
    def last_remote_revision(self) -> str:
        return self._combined_revision(remote=True)

    def clone_from(self, source: str) -> None:
        """Clone all repositories."""
        configs = self.parse_repo_config(source)
        for key, config in configs.items():
            self.repositories_by_key[key].clone_from(config["repo"])

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

    def set_committer(self, name: str, mail: str) -> None:
        for repository in self.repositories:
            repository.set_committer(name, mail)

    def update_remote(self) -> None:
        for repository in self.repositories:
            repository.update_remote()
        self.clean_revision_cache()

    def configure_branch(self, branch: str) -> None:
        for repository in self.repositories:
            repository.configure_branch(branch)
        self.clean_revision_cache()

    def status(self) -> str:
        return "\n".join(
            f"[{key}]\n{repository.status()}"
            for key, repository in self.repositories_by_key.items()
        )

    def push(self, branch: str) -> None:
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

    def reset_to_revision(self, revision: str) -> None:
        revisions = self._resolve_revision(revision)
        try:
            for key, repository in self.repositories_by_key.items():
                repository.reset_to_revision(revisions[key])
        finally:
            self.clean_revision_cache()

    def merge(
        self, abort: bool = False, message: str | None = None, no_ff: bool = False
    ) -> None:
        for repository in self.repositories:
            repository.merge(abort=abort, message=message, no_ff=no_ff)
        self.clean_revision_cache()

    def rebase(self, abort: bool = False) -> None:
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

    def count_missing(self) -> int:
        return sum(repository.count_missing() for repository in self.repositories)

    def count_outgoing(self, branch: str | None = None) -> int:
        return sum(
            repository.count_outgoing(branch) for repository in self.repositories
        )

    def get_outgoing_revisions(self, branch: str | None = None) -> list[str]:
        return [
            f"{key}/{revision}"
            for key, repository in self.repositories_by_key.items()
            for revision in repository.get_outgoing_revisions(branch)
        ]

    def get_tracked_outgoing_revisions(self) -> list[str]:
        return [
            f"{key}/{revision}"
            for key, repository in self.repositories_by_key.items()
            for revision in repository.get_tracked_outgoing_revisions()
        ]

    def get_push_revisions(self, branch: str | None = None) -> list[str]:
        return [
            f"{key}/{revision}"
            for key, repository in self.repositories_by_key.items()
            for revision in repository.get_push_revisions(branch)
        ]

    def _get_revision_info(self, revision: str) -> RawCommitInfo:
        revisions = self._resolve_revision(revision)
        infos = [
            repository.get_revision_info(revisions[key])
            for key, repository in self.repositories_by_key.items()
        ]
        latest = max(infos, key=itemgetter("commitdate"))
        result: RawCommitInfo = {
            "summary": gettext("Aggregate revision for many repositories"),
            "message": gettext("Aggregate revision for many repositories"),
            "author": latest["author"],
            "authordate": latest["authordate"].isoformat(),
            "commit": revision,
            "commitdate": latest["commitdate"].isoformat(),
            "revision": revision,
            "shortrevision": revision[:7],
        }
        for field in ("author_name", "author_email"):
            if field in latest:
                result[field] = latest[field]
        return result

    def commit(
        self,
        message: str,
        author: str | None = None,
        timestamp: datetime | None = None,
        files: list[str] | None = None,
    ) -> bool:
        changes = False
        if files is None:
            for repository in self.repositories:
                changes |= repository.commit(message, author, timestamp)
        else:
            for repository, repository_files in self._iter_paths(files).items():
                changes |= repository.commit(
                    message, author, timestamp, repository_files
                )
        self.clean_revision_cache()
        return changes

    def remove(self, files: list[str], message: str, author: str | None = None) -> None:
        for repository, repository_files in self._iter_paths(files).items():
            repository.remove(repository_files, message, author)
        self.clean_revision_cache()

    def get_object_hash(self, path: str) -> str:
        _key, repository, subpath = self._repository_for_path(path)
        return repository.get_object_hash(subpath)

    def get_file(self, path: str, revision: str) -> str:
        key, repository, subpath = self._repository_for_path(path)
        revisions = self._resolve_revision(revision)
        return repository.get_file(subpath, revisions[key])

    def cleanup(self) -> None:
        for repository in self.repositories:
            repository.cleanup()

    def get_changed_files(self, compare_to: str | None = None) -> list[str]:
        revisions = (
            self._resolve_revision(compare_to) if compare_to is not None else None
        )
        files: list[str] = []
        for key, repository in self.repositories_by_key.items():
            files.extend(
                f"{key}/{filename}"
                for filename in repository.get_changed_files(
                    revisions[key] if revisions is not None else None
                )
            )
        return files

    def list_remote_branches(self) -> list[str]:
        return sorted(
            branch
            for repository in self.repositories
            for branch in repository.list_remote_branches()
        )

    def compact(self) -> None:
        for repository in self.repositories:
            repository.compact()


class MultiContextManager:
    def __init__(self, *managers: RepositoryLock) -> None:
        self.managers = managers
        self._stacks: list[ExitStack] = []

    def __enter__(self) -> list[None]:
        stack = ExitStack()
        try:
            result = [stack.enter_context(manager) for manager in self.managers]
        except BaseException:
            stack.close()
            raise
        self._stacks.append(stack)
        return result

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None:
        stack = self._stacks.pop()
        return stack.__exit__(exc_type, exc_value, traceback)

    def reacquire(self) -> None:
        for manager in self.managers:
            manager.reacquire()

    @contextmanager
    def without_recovery(self) -> Generator[None]:
        with ExitStack() as stack:
            for manager in self.managers:
                stack.enter_context(manager.without_recovery())
            yield

    @property
    def is_locked(self) -> bool:
        return all(map(attrgetter("is_locked"), self.managers))
