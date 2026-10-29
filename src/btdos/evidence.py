"""Private, immutable-by-default run bundles and reproducible integrity checks."""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import tempfile
import time

from . import __version__


def utc():
    return datetime.now(timezone.utc).isoformat()


def write_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def digest(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def _no_symlink(path):
    path = Path(path).absolute()
    for component in (path, *path.parents):
        if component.is_symlink():
            raise ValueError(f"不接受符号链接证据路径: {component}")


class EvidenceBundle:
    def __init__(self, root, plan, simulation=False):
        root = Path(root).absolute()
        _no_symlink(root)
        root.mkdir(parents=True, exist_ok=True)
        self.path = Path(tempfile.mkdtemp(prefix=datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ-"), dir=root))
        os.chmod(self.path, 0o700)
        self.plan = dict(plan)
        self.simulation = bool(simulation)
        self.started = utc()
        self.sealed = False
        self.events_path = self.path / "events.jsonl"
        # Protect custom output roots from accidentally publishing private evidence.
        (self.path / ".gitignore").write_text("*\n!.gitignore\n", encoding="utf-8")
        write_json(self.path / "plan.json", self.plan)
        self.emit("bundle_created", version=__version__)

    def emit(self, event, **data):
        if self.sealed:
            raise RuntimeError("证据包已封存")
        row = dict(schema_version=1, utc=utc(), monotonic_ns=time.monotonic_ns(),
                   event=event, simulation=self.simulation, **data)
        with self.events_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
            handle.flush()
        return row

    def finalize(self, status, extra=None):
        if self.sealed:
            raise RuntimeError("证据包已封存")
        extra = dict(extra or {})
        self.emit("bundle_finalized", status=status)
        metadata = dict(schema_version=1, tool_version=__version__,
                        simulation=self.simulation, status=status,
                        started=self.started, finished=utc(), **extra)
        write_json(self.path / "result.json", metadata)
        from .report import render_report
        (self.path / "report.md").write_text(render_report(self.plan, metadata), encoding="utf-8")
        files = []
        for path in sorted(self.path.rglob("*")):
            if path.is_symlink():
                raise ValueError("封存拒绝符号链接")
            if path.is_file() and path.name != "manifest.json":
                files.append(dict(path=path.relative_to(self.path).as_posix(),
                                  size=path.stat().st_size, sha256=digest(path)))
        write_json(self.path / "manifest.json", dict(schema_version=1, simulation=self.simulation,
                   status=status, files=files))
        self.sealed = True
        return self.path


def verify_bundle(path):
    root = Path(path).absolute()
    errors = []
    try:
        _no_symlink(root)
        manifest_path = root / "manifest.json"
        _no_symlink(manifest_path)
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if not isinstance(manifest, dict) or manifest.get("schema_version") != 1 or not isinstance(manifest.get("files"), list):
            raise ValueError("不支持的 manifest")
        seen = set()
        for item in manifest["files"]:
            name = item["path"]
            relative = PurePosixPath(name)
            if not isinstance(name, str) or relative.is_absolute() or ".." in relative.parts or "\\" in name or ":" in name or name == "manifest.json":
                errors.append(f"非法清单路径: {name}")
                continue
            if name in seen:
                errors.append(f"重复清单路径: {name}")
                continue
            seen.add(name)
            candidate = root.joinpath(*relative.parts)
            try:
                _no_symlink(candidate)
                if not candidate.is_file():
                    errors.append(f"缺失文件: {name}")
                elif candidate.stat().st_size != item["size"] or digest(candidate) != item["sha256"]:
                    errors.append(f"哈希/长度不匹配: {name}")
            except (OSError, ValueError) as exc:
                errors.append(str(exc))
        for required in ("plan.json", "events.jsonl", "result.json", "report.md"):
            if required not in seen:
                errors.append(f"清单缺少必备文件: {required}")
        for candidate in root.rglob("*"):
            if candidate.is_symlink():
                errors.append(f"符号链接: {candidate.name}")
            elif candidate.is_file() and candidate.name != "manifest.json" and candidate.relative_to(root).as_posix() not in seen:
                errors.append(f"未列入清单: {candidate.relative_to(root).as_posix()}")
    except (OSError, ValueError, TypeError, KeyError) as exc:
        errors.append(str(exc))
    return dict(ok=not errors, errors=errors)
