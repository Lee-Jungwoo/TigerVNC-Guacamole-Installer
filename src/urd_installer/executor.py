"""Safe command, file, lock, and state primitives used by providers."""

from __future__ import annotations

import base64
import contextlib
import copy
import fcntl
import grp
import json
import logging
import os
import pwd
import re
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Iterator, Mapping, MutableMapping, Optional, Sequence, Tuple, Union

from .model import CommandResult, DirectoryEnsureResult, ExecutionError, FileInstallResult


DEFAULT_STATE_PATH = Path("/var/lib/urd-installer/state.json")
DEFAULT_LOCK_PATH = Path("/run/lock/urd-installer.lock")
STATE_PATH_ENV = "URD_STATE_PATH"
STATE_DIR_ENV = "URD_STATE_DIR"
LOCK_PATH_ENV = "URD_LOCK_PATH"
MAX_STATE_BYTES = 4 * 1024 * 1024


def state_path_from_env() -> Path:
    explicit = os.environ.get(STATE_PATH_ENV)
    if explicit:
        return Path(explicit).expanduser()
    directory = os.environ.get(STATE_DIR_ENV)
    if directory:
        return Path(directory).expanduser() / "state.json"
    return DEFAULT_STATE_PATH


def lock_path_from_env(state_path: Optional[Path] = None) -> Path:
    explicit = os.environ.get(LOCK_PATH_ENV)
    if explicit:
        return Path(explicit).expanduser()
    if os.environ.get(STATE_PATH_ENV) or os.environ.get(STATE_DIR_ENV):
        selected = state_path or state_path_from_env()
        return selected.with_name(selected.name + ".lock")
    return DEFAULT_LOCK_PATH


class SecretRedactor:
    """Redact known values and common inline credential assignments."""

    _INLINE = re.compile(
        r"(?i)(password|passwd|passphrase|token|secret|api[_-]?key|credential)(\s*[:=]\s*)([^\s,;]+)"
    )

    def __init__(self, secrets: Sequence[Union[str, bytes]] = ()) -> None:
        self._secrets = []  # type: list[str]
        for secret in secrets:
            self.add(secret)

    def add(self, secret: Union[str, bytes, None]) -> None:
        if secret is None:
            return
        if isinstance(secret, bytes):
            value = secret.decode("utf-8", errors="replace")
        else:
            value = str(secret)
        if value and value not in self._secrets:
            self._secrets.append(value)
            self._secrets.sort(key=len, reverse=True)

    def redact(self, value: Any) -> str:
        rendered = str(value)
        for secret in self._secrets:
            rendered = rendered.replace(secret, "<redacted>")
        return self._INLINE.sub(lambda match: match.group(1) + match.group(2) + "<redacted>", rendered)

    def redact_argv(self, argv: Sequence[str]) -> Tuple[str, ...]:
        return tuple(self.redact(item) for item in argv)

    def redact_object(self, value: Any, key: str = "") -> Any:
        if isinstance(value, Mapping):
            return {str(k): self.redact_object(v, str(k)) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [self.redact_object(item, key) for item in value]
        if value is not None and not isinstance(value, (Mapping, list, tuple)):
            sensitive_key = re.search(
                r"(?i)(password|passwd|passphrase|token|secret|api[_-]?key|credential)", key
            )
            reference_key = re.search(r"(?i)(_file|_path|_env|_command)$", key)
            if sensitive_key and not reference_key:
                return "<redacted>"
            if isinstance(value, str):
                return self.redact(value)
        return value


class CommandRunner:
    """Execute only explicit argv sequences; shell execution is impossible."""

    def __init__(
        self,
        *,
        dry_run: bool = False,
        non_interactive: bool = False,
        redactor: Optional[SecretRedactor] = None,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self.dry_run = dry_run
        self.non_interactive = non_interactive
        self.redactor = redactor or SecretRedactor()
        self.logger = logger or logging.getLogger("urd_installer.executor")

    @staticmethod
    def _validate_argv(argv: Sequence[Union[str, os.PathLike]]) -> Tuple[str, ...]:
        if isinstance(argv, (str, bytes)):
            raise TypeError("argv must be a sequence, never a shell command string")
        converted = tuple(os.fspath(item) for item in argv)
        if not converted:
            raise ValueError("argv must not be empty")
        for item in converted:
            if not isinstance(item, str):
                raise TypeError("every argv item must be str or PathLike")
            if not item or "\x00" in item:
                raise ValueError("argv contains an empty value or NUL byte")
        return converted

    def prepare_argv(
        self, argv: Sequence[Union[str, os.PathLike]], *, require_root: bool = False
    ) -> Tuple[str, ...]:
        prepared = self._validate_argv(argv)
        euid = os.geteuid() if hasattr(os, "geteuid") else -1
        if require_root and euid != 0:
            sudo = shutil.which("sudo")
            if sudo is None:
                raise ExecutionError(
                    "root privileges are required for {}, but sudo is unavailable".format(
                        self.redactor.redact_argv(prepared)[0]
                    )
                )
            prefix = (sudo, "-n", "--") if self.non_interactive else (sudo, "--")
            prepared = prefix + prepared
        return prepared

    def run(
        self,
        argv: Sequence[Union[str, os.PathLike]],
        *,
        require_root: bool = False,
        check: bool = True,
        timeout: Optional[float] = None,
        cwd: Optional[Union[str, os.PathLike]] = None,
        env: Optional[Mapping[str, str]] = None,
        input_text: Optional[str] = None,
        changed: bool = False,
    ) -> CommandResult:
        prepared = self.prepare_argv(argv, require_root=require_root)
        safe_argv = self.redactor.redact_argv(prepared)
        self.logger.info("run: %s", shlex.join(safe_argv))
        child_env = None
        if env is not None:
            child_env = os.environ.copy()
            for key, value in env.items():
                if not isinstance(key, str) or not isinstance(value, str) or "\x00" in key + value:
                    raise ValueError("subprocess environment keys and values must be NUL-free strings")
                child_env[key] = value
        if input_text is not None and not isinstance(input_text, str):
            raise TypeError("input_text must be a string")
        if self.dry_run:
            return CommandResult(prepared, 0, "", "", changed=changed)
        try:
            completed = subprocess.run(
                list(prepared),
                shell=False,
                stdin=subprocess.PIPE if input_text is not None else subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                input=input_text,
                timeout=timeout,
                cwd=os.fspath(cwd) if cwd is not None else None,
                env=child_env,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise ExecutionError(
                "failed to execute {}: {}".format(shlex.join(safe_argv), self.redactor.redact(exc))
            ) from exc
        result = CommandResult(
            argv=prepared,
            returncode=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
            changed=changed and completed.returncode == 0,
        )
        if check and not result.ok:
            stderr = self.redactor.redact(result.stderr.strip())
            stdout = self.redactor.redact(result.stdout.strip())
            detail = stderr or stdout or "no output"
            raise ExecutionError(
                "command failed with exit {}: {}: {}".format(
                    result.returncode, shlex.join(safe_argv), detail
                )
            )
        return result


class AtomicFileInstaller:
    """Compare, back up, and atomically replace managed files."""

    def __init__(self, runner: CommandRunner) -> None:
        self.runner = runner

    @staticmethod
    def _reject_symlink_ancestors(path: Path) -> None:
        current = path.parent
        while current != current.parent:
            try:
                info = current.lstat()
            except FileNotFoundError:
                current = current.parent
                continue
            except OSError as exc:
                raise ExecutionError("cannot inspect parent {}: {}".format(current, exc)) from exc
            if stat.S_ISLNK(info.st_mode):
                raise ExecutionError("refusing path beneath symlink directory: {}".format(current))
            current = current.parent

    @staticmethod
    def _identity(value: Optional[Union[str, int]], *, user: bool) -> Optional[int]:
        if value is None:
            return None
        if isinstance(value, int) or str(value).isdigit():
            return int(value)
        try:
            return pwd.getpwnam(str(value)).pw_uid if user else grp.getgrnam(str(value)).gr_gid
        except KeyError as exc:
            kind = "user" if user else "group"
            raise ExecutionError("unknown {}: {}".format(kind, value)) from exc

    def _privileged_stat(self, path: Path, *, follow: bool = False) -> os.stat_result:
        if not follow:
            link = self.runner.run(("test", "-L", str(path)), require_root=True, check=False)
            if link.returncode == 0:
                raise ExecutionError("refusing symlink path: {}".format(path))
        option = "-Lc" if follow else "-c"
        result = self.runner.run(
            ("stat", option, "%f:%u:%g", str(path)), require_root=True
        )
        try:
            raw_mode, raw_uid, raw_gid = result.stdout.strip().split(":", 2)
            mode = int(raw_mode, 16)
            uid = int(raw_uid)
            gid = int(raw_gid)
        except (TypeError, ValueError) as exc:
            raise ExecutionError("cannot parse privileged stat for {}".format(path)) from exc
        return os.stat_result((mode, 0, 0, 0, uid, gid, 0, 0, 0, 0))

    def _check_target(self, path: Path) -> Optional[os.stat_result]:
        try:
            info = path.lstat()
        except FileNotFoundError:
            return None
        except PermissionError:
            if self.runner.dry_run:
                return None
            link = self.runner.run(("test", "-L", str(path)), require_root=True, check=False)
            if link.returncode == 0:
                raise ExecutionError("refusing to replace symlink: {}".format(path))
            exists = self.runner.run(("test", "-e", str(path)), require_root=True, check=False)
            if exists.returncode != 0:
                return None
            info = self._privileged_stat(path)
        except OSError as exc:
            raise ExecutionError("cannot inspect {}: {}".format(path, exc)) from exc
        if stat.S_ISLNK(info.st_mode):
            raise ExecutionError("refusing to replace symlink: {}".format(path))
        if not stat.S_ISREG(info.st_mode):
            raise ExecutionError("managed path is not a regular file: {}".format(path))
        return info

    def _read(self, path: Path) -> bytes:
        try:
            return path.read_bytes()
        except PermissionError:
            encode_script = (
                "import base64,pathlib,sys;"
                "sys.stdout.write(base64.b64encode(pathlib.Path(sys.argv[1]).read_bytes()).decode('ascii'))"
            )
            result = self.runner.run(
                (sys.executable, "-c", encode_script, os.fspath(path)), require_root=True
            )
            try:
                return base64.b64decode(result.stdout.encode("ascii"), validate=False)
            except (ValueError, UnicodeError) as exc:
                raise ExecutionError("cannot decode privileged read of {}".format(path)) from exc
        except OSError as exc:
            raise ExecutionError("cannot read {}: {}".format(path, exc)) from exc

    def read(
        self,
        path: Union[str, os.PathLike],
        *,
        require_regular: bool = True,
        no_symlink: bool = True,
    ) -> bytes:
        target = Path(path).expanduser()
        if not target.is_absolute():
            target = target.resolve()
        self._reject_symlink_ancestors(target)
        try:
            info = target.lstat() if no_symlink else target.stat()
        except PermissionError:
            if self.runner.dry_run:
                raise ExecutionError("dry-run cannot read permission-restricted file: {}".format(target))
            info = self._privileged_stat(target, follow=not no_symlink)
        except OSError as exc:
            raise ExecutionError("cannot inspect {}: {}".format(target, exc)) from exc
        if no_symlink and stat.S_ISLNK(info.st_mode):
            raise ExecutionError("refusing to read symlink: {}".format(target))
        if require_regular and not stat.S_ISREG(info.st_mode):
            raise ExecutionError("path is not a regular file: {}".format(target))
        return self._read(target)

    @staticmethod
    def _missing_directories(directory: Path) -> Tuple[Path, ...]:
        missing = []
        current = directory
        while not current.exists():
            missing.append(current)
            parent = current.parent
            if parent == current:
                break
            current = parent
        return tuple(reversed(missing))

    @staticmethod
    def _direct_directory_write_possible(directory: Path) -> bool:
        current = directory
        while not current.exists() and current.parent != current:
            current = current.parent
        return os.access(str(current), os.W_OK)

    @staticmethod
    def _create_directories(
        directory: Path,
        *,
        final_mode: int,
        uid: Optional[int],
        gid: Optional[int],
    ) -> None:
        missing = AtomicFileInstaller._missing_directories(directory)
        try:
            for item in missing:
                item.mkdir(mode=final_mode if item == directory else 0o755)
                created = item.stat()
                requested_uid = uid if uid is not None and uid != created.st_uid else -1
                requested_gid = gid if gid is not None and gid != created.st_gid else -1
                if requested_uid != -1 or requested_gid != -1:
                    os.chown(str(item), requested_uid, requested_gid)
            if missing:
                os.chmod(str(directory), final_mode)
        except OSError as exc:
            raise ExecutionError("cannot create directory {}: {}".format(directory, exc)) from exc

    def ensure_directory(
        self,
        path: Union[str, os.PathLike],
        *,
        mode: int = 0o755,
        owner: Optional[Union[str, int]] = None,
        group: Optional[Union[str, int]] = None,
    ) -> DirectoryEnsureResult:
        target = Path(path).expanduser()
        if not target.is_absolute():
            target = target.resolve()
        self._reject_symlink_ancestors(target)
        if mode < 0 or mode > 0o7777:
            raise ValueError("invalid directory mode: {!r}".format(mode))
        uid = self._identity(owner, user=True)
        gid = self._identity(group, user=False)
        try:
            info = target.lstat()
        except FileNotFoundError:
            info = None
        except PermissionError:
            if self.runner.dry_run:
                info = None
            else:
                link = self.runner.run(("test", "-L", str(target)), require_root=True, check=False)
                if link.returncode == 0:
                    raise ExecutionError("refusing to manage symlink directory: {}".format(target))
                exists = self.runner.run(("test", "-e", str(target)), require_root=True, check=False)
                info = self._privileged_stat(target) if exists.returncode == 0 else None
        except OSError as exc:
            raise ExecutionError("cannot inspect directory {}: {}".format(target, exc)) from exc
        if info is not None:
            if stat.S_ISLNK(info.st_mode):
                raise ExecutionError("refusing to manage symlink directory: {}".format(target))
            if not stat.S_ISDIR(info.st_mode):
                raise ExecutionError("managed directory path is not a directory: {}".format(target))
            mode_matches = stat.S_IMODE(info.st_mode) == mode
            owner_matches = (uid is None or info.st_uid == uid) and (gid is None or info.st_gid == gid)
            if mode_matches and owner_matches:
                return DirectoryEnsureResult(target, False, "already in desired state")
        if self.runner.dry_run:
            return DirectoryEnsureResult(target, True, "would ensure directory")

        euid = os.geteuid() if hasattr(os, "geteuid") else -1
        egid = os.getegid() if hasattr(os, "getegid") else -1
        if info is None:
            ownership_change = (uid is not None and uid != euid) or (gid is not None and gid != egid)
        else:
            ownership_change = (uid is not None and uid != info.st_uid) or (
                gid is not None and gid != info.st_gid
            )
        direct = euid == 0 or (self._direct_directory_write_possible(target) and not ownership_change)
        if info is None:
            if direct:
                self._create_directories(target, final_mode=mode, uid=uid, gid=gid)
            else:
                argv = ["install", "-d", "-m", "{:04o}".format(mode)]
                if uid is not None:
                    argv.extend(("-o", str(uid)))
                if gid is not None:
                    argv.extend(("-g", str(gid)))
                argv.append(str(target))
                self.runner.run(argv, require_root=True, changed=True)
        else:
            if direct:
                try:
                    os.chmod(str(target), mode)
                    requested_uid = uid if uid is not None and uid != info.st_uid else -1
                    requested_gid = gid if gid is not None and gid != info.st_gid else -1
                    if requested_uid != -1 or requested_gid != -1:
                        os.chown(str(target), requested_uid, requested_gid)
                except OSError as exc:
                    raise ExecutionError("cannot update directory {}: {}".format(target, exc)) from exc
            else:
                self.runner.run(("chmod", "{:04o}".format(mode), target), require_root=True, changed=True)
                if uid is not None and gid is not None:
                    self.runner.run(("chown", "{}:{}".format(uid, gid), target), require_root=True, changed=True)
                elif uid is not None:
                    self.runner.run(("chown", str(uid), target), require_root=True, changed=True)
                elif gid is not None:
                    self.runner.run(("chgrp", str(gid), target), require_root=True, changed=True)
        return DirectoryEnsureResult(target, True, "directory ensured")

    @staticmethod
    def _fsync_directory(directory: Path) -> None:
        try:
            descriptor = os.open(os.fspath(directory), os.O_RDONLY)
        except OSError:
            return
        try:
            os.fsync(descriptor)
        except OSError:
            pass
        finally:
            os.close(descriptor)

    def _write_direct(self, path: Path, data: bytes, mode: int, uid: Optional[int], gid: Optional[int]) -> None:
        if not path.parent.exists():
            self._create_directories(path.parent, final_mode=0o755, uid=uid, gid=gid)
        descriptor, temp_name = tempfile.mkstemp(prefix=".{}.urd-".format(path.name), dir=str(path.parent))
        temp_path = Path(temp_name)
        try:
            os.fchmod(descriptor, mode)
            if uid is not None or gid is not None:
                created = os.fstat(descriptor)
                requested_uid = uid if uid is not None and uid != created.st_uid else -1
                requested_gid = gid if gid is not None and gid != created.st_gid else -1
                if requested_uid != -1 or requested_gid != -1:
                    os.fchown(descriptor, requested_uid, requested_gid)
            with os.fdopen(descriptor, "wb", closefd=True) as stream:
                descriptor = -1
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(str(temp_path), str(path))
            self._fsync_directory(path.parent)
        except OSError as exc:
            raise ExecutionError("atomic install failed for {}: {}".format(path, exc)) from exc
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            try:
                temp_path.unlink()
            except FileNotFoundError:
                pass

    def _write_privileged(
        self, path: Path, data: bytes, mode: int, uid: Optional[int], gid: Optional[int]
    ) -> None:
        staging_fd, staging_name = tempfile.mkstemp(prefix="urd-stage-")
        destination_temp = path.parent / ".{}.urd-{}.tmp".format(path.name, uuid.uuid4().hex)
        try:
            with os.fdopen(staging_fd, "wb", closefd=True) as stream:
                staging_fd = -1
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            if not path.parent.exists():
                directory_argv = ["install", "-d", "-m", "0755"]
                if uid is not None:
                    directory_argv.extend(("-o", str(uid)))
                if gid is not None:
                    directory_argv.extend(("-g", str(gid)))
                directory_argv.append(str(path.parent))
                self.runner.run(directory_argv, require_root=True, changed=True)
            argv = ["install", "-m", "{:04o}".format(mode)]
            if uid is not None:
                argv.extend(("-o", str(uid)))
            if gid is not None:
                argv.extend(("-g", str(gid)))
            argv.extend((staging_name, str(destination_temp)))
            self.runner.run(argv, require_root=True, changed=True)
            self.runner.run(("mv", "-f", destination_temp, path), require_root=True, changed=True)
        finally:
            if staging_fd >= 0:
                os.close(staging_fd)
            try:
                Path(staging_name).unlink()
            except FileNotFoundError:
                pass

    def _write(self, path: Path, data: bytes, mode: int, uid: Optional[int], gid: Optional[int]) -> None:
        writable = self._direct_directory_write_possible(path.parent)
        if (hasattr(os, "geteuid") and os.geteuid() == 0) or writable:
            self._write_direct(path, data, mode, uid, gid)
        else:
            self._write_privileged(path, data, mode, uid, gid)

    def install(
        self,
        path: Union[str, os.PathLike],
        content: Union[str, bytes],
        *,
        mode: int = 0o644,
        owner: Optional[Union[str, int]] = None,
        group: Optional[Union[str, int]] = None,
        backup: bool = True,
    ) -> FileInstallResult:
        target = Path(path).expanduser()
        if not target.is_absolute():
            target = target.resolve()
        self._reject_symlink_ancestors(target)
        if mode < 0 or mode > 0o7777:
            raise ValueError("invalid file mode: {!r}".format(mode))
        data = content.encode("utf-8") if isinstance(content, str) else bytes(content)
        info = self._check_target(target)
        uid = self._identity(owner, user=True)
        gid = self._identity(group, user=False)
        existing = self._read(target) if info is not None else None
        content_matches = existing == data
        mode_matches = info is not None and stat.S_IMODE(info.st_mode) == mode
        owner_matches = info is not None and (uid is None or info.st_uid == uid) and (gid is None or info.st_gid == gid)
        if content_matches and mode_matches and owner_matches:
            return FileInstallResult(target, False, reason="already in desired state")
        if self.runner.dry_run:
            return FileInstallResult(target, True, reason="would atomically install")

        backup_path = None  # type: Optional[Path]
        if info is not None and backup:
            timestamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
            backup_path = target.with_name(
                "{}.urd-backup.{}.{}".format(target.name, timestamp, uuid.uuid4().hex[:8])
            )
            self._write(
                backup_path,
                existing or b"",
                stat.S_IMODE(info.st_mode),
                info.st_uid,
                info.st_gid,
            )

        if info is not None:
            if uid is None:
                uid = info.st_uid
            if gid is None:
                gid = info.st_gid
        self._write(target, data, mode, uid, gid)
        return FileInstallResult(target, True, backup_path, "atomically installed")


class StateStore:
    """Root-safe JSON state protected by an advisory flock."""

    def __init__(
        self,
        runner: CommandRunner,
        path: Optional[Union[str, os.PathLike]] = None,
        lock_path: Optional[Union[str, os.PathLike]] = None,
    ) -> None:
        self.runner = runner
        self.files = AtomicFileInstaller(runner)
        self.path = Path(path).expanduser() if path is not None else state_path_from_env()
        self.lock_path = (
            Path(lock_path).expanduser() if lock_path is not None else lock_path_from_env(self.path)
        )

    def _ensure_lock_file(self) -> None:
        if self.lock_path.exists():
            info = self.lock_path.lstat()
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
                raise ExecutionError("unsafe state lock path: {}".format(self.lock_path))
            return
        result = self.files.install(self.lock_path, b"", mode=0o666, backup=False)
        if self.runner.dry_run and result.changed:
            return

    @contextlib.contextmanager
    def lock(self, timeout: float = 30.0) -> Iterator[None]:
        if self.runner.dry_run:
            yield
            return
        self._ensure_lock_file()
        flags = os.O_RDWR | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(str(self.lock_path), flags)
        except OSError as exc:
            raise ExecutionError("cannot open state lock {}: {}".format(self.lock_path, exc)) from exc
        deadline = time.monotonic() + timeout
        try:
            while True:
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise ExecutionError("timed out waiting for state lock {}".format(self.lock_path))
                    time.sleep(0.1)
            yield
        finally:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            finally:
                os.close(descriptor)

    def load(self) -> Dict[str, Any]:
        if not self.path.exists():
            return {"schema_version": 1, "resources": {}}
        try:
            raw = self.files.read(self.path, require_regular=True, no_symlink=True)
        except ExecutionError:
            raise
        except OSError as exc:
            raise ExecutionError("cannot read state {}: {}".format(self.path, exc)) from exc
        if len(raw) > MAX_STATE_BYTES:
            raise ExecutionError("state file exceeds {} bytes".format(MAX_STATE_BYTES))
        try:
            value = json.loads(raw.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise ExecutionError("invalid state JSON in {}: {}".format(self.path, exc)) from exc
        if not isinstance(value, dict) or value.get("schema_version") != 1:
            raise ExecutionError("unsupported state structure in {}".format(self.path))
        return value

    def save(self, state: Mapping[str, Any]) -> FileInstallResult:
        safe_state = self.runner.redactor.redact_object(copy.deepcopy(dict(state)))
        safe_state["schema_version"] = 1
        rendered = json.dumps(safe_state, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
        return self.files.install(self.path, rendered, mode=0o600, backup=True)

    @contextlib.contextmanager
    def transaction(self) -> Iterator[MutableMapping[str, Any]]:
        with self.lock():
            state = self.load()
            before = json.dumps(state, sort_keys=True, separators=(",", ":"))
            yield state
            after = json.dumps(state, sort_keys=True, separators=(",", ":"))
            if after != before:
                self.save(state)


class Executor:
    """Facade passed to planner/provider apply and uninstall functions."""

    def __init__(
        self,
        *,
        dry_run: bool = False,
        non_interactive: bool = False,
        secrets: Sequence[Union[str, bytes]] = (),
        state_path: Optional[Union[str, os.PathLike]] = None,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self.redactor = SecretRedactor(secrets)
        self.runner = CommandRunner(
            dry_run=dry_run,
            non_interactive=non_interactive,
            redactor=self.redactor,
            logger=logger,
        )
        self.files = AtomicFileInstaller(self.runner)
        self.state = StateStore(self.runner, path=state_path)
        self.dry_run = dry_run
        self.non_interactive = non_interactive

    def run(self, argv: Sequence[Union[str, os.PathLike]], **kwargs: Any) -> CommandResult:
        return self.runner.run(argv, **kwargs)

    def install_file(self, path: Union[str, os.PathLike], content: Union[str, bytes], **kwargs: Any) -> FileInstallResult:
        return self.files.install(path, content, **kwargs)

    def read_file(
        self,
        path: Union[str, os.PathLike],
        *,
        require_regular: bool = True,
        no_symlink: bool = True,
    ) -> bytes:
        """Read a managed file, using sudo when ordinary access is denied."""

        return self.files.read(path, require_regular=require_regular, no_symlink=no_symlink)

    def ensure_directory(
        self,
        path: Union[str, os.PathLike],
        *,
        mode: int = 0o755,
        owner: Optional[Union[str, int]] = None,
        group: Optional[Union[str, int]] = None,
    ) -> DirectoryEnsureResult:
        return self.files.ensure_directory(path, mode=mode, owner=owner, group=group)

    def register_secret(self, value: Union[str, bytes, None]) -> None:
        self.redactor.add(value)

    @contextlib.contextmanager
    def locked(self, timeout: float = 30.0) -> Iterator[None]:
        with self.state.lock(timeout=timeout):
            yield
