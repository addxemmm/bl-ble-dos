"""Evidence integrity tests run in temporary directories without hardware."""

import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest

from btdos.evidence import EvidenceBundle, verify_bundle


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.plan = {"case": "br-sdp", "adapter": "hci0", "target": "02:00:00:00:00:01"}

    def bundle(self, **kwargs):
        bundle = EvidenceBundle(self.root / "runs", self.plan, **kwargs)
        bundle.emit("unit_test_event", count=2)
        bundle.finalize("completed")
        return bundle

    def manifest(self, bundle):
        return json.loads((bundle.path / "manifest.json").read_text(encoding="utf-8"))

    def rewrite_manifest(self, bundle, value):
        (bundle.path / "manifest.json").write_text(json.dumps(value), encoding="utf-8")

    def assert_invalid(self, path):
        result = verify_bundle(path)
        self.assertFalse(result["ok"])
        self.assertTrue(result["errors"])

    def test_finalized_bundle_is_self_consistent(self):
        bundle = self.bundle()
        self.assertEqual(verify_bundle(bundle.path), {"ok": True, "errors": []})
        manifest = self.manifest(bundle)
        names = {item["path"] for item in manifest["files"]}
        self.assertEqual(names, {".gitignore", "plan.json", "events.jsonl", "result.json", "report.md"})
        self.assertEqual((bundle.path / ".gitignore").read_text(encoding="utf-8"), "*\n!.gitignore\n")
        for item in manifest["files"]:
            with self.subTest(path=item["path"]):
                content = (bundle.path / item["path"]).read_bytes()
                self.assertEqual(item["size"], len(content))
                self.assertEqual(item["sha256"], hashlib.sha256(content).hexdigest())

    def test_run_directories_are_unique(self):
        first = self.bundle()
        second = self.bundle()
        self.assertNotEqual(first.path, second.path)
        self.assertTrue(first.path.is_dir())
        self.assertTrue(second.path.is_dir())

    def test_events_are_complete_json_lines_and_monotonic(self):
        bundle = self.bundle(simulation=True)
        events = [json.loads(line) for line in bundle.events_path.read_text(encoding="utf-8").splitlines()]
        self.assertEqual([row["event"] for row in events], ["bundle_created", "unit_test_event", "bundle_finalized"])
        self.assertTrue(all(row["simulation"] for row in events))
        timestamps = [row["monotonic_ns"] for row in events]
        self.assertEqual(timestamps, sorted(timestamps))
        self.assertTrue(all(row["utc"] for row in events))

    def test_sealed_bundle_rejects_more_events_or_second_finalize(self):
        bundle = self.bundle()
        with self.assertRaises(RuntimeError):
            bundle.emit("late_event")
        with self.assertRaises(RuntimeError):
            bundle.finalize("completed")

    def test_same_length_hash_tampering_is_detected(self):
        bundle = self.bundle()
        path = bundle.path / "plan.json"
        content = path.read_bytes()
        replacement = bytes([content[0] ^ 1]) + content[1:]
        self.assertEqual(len(content), len(replacement))
        path.write_bytes(replacement)
        self.assert_invalid(bundle.path)

    def test_missing_and_unlisted_files_are_detected(self):
        for mutation in ("delete", "append"):
            with self.subTest(mutation=mutation):
                bundle = self.bundle()
                if mutation == "delete":
                    (bundle.path / "plan.json").unlink()
                else:
                    (bundle.path / "unexpected.txt").write_text("unlisted", encoding="utf-8")
                self.assert_invalid(bundle.path)

    def test_missing_required_manifest_entry_is_detected(self):
        bundle = self.bundle()
        manifest = self.manifest(bundle)
        manifest["files"] = [item for item in manifest["files"] if item["path"] != "plan.json"]
        self.rewrite_manifest(bundle, manifest)
        self.assert_invalid(bundle.path)

    def test_duplicate_manifest_entries_are_rejected(self):
        bundle = self.bundle()
        manifest = self.manifest(bundle)
        manifest["files"].append(dict(manifest["files"][0]))
        self.rewrite_manifest(bundle, manifest)
        self.assert_invalid(bundle.path)

    def test_manifest_paths_cannot_escape_bundle_or_hash_manifest_itself(self):
        for name in ("../outside", "/outside", "C:/outside", "sub\\outside", "manifest.json"):
            with self.subTest(name=name):
                bundle = self.bundle()
                manifest = self.manifest(bundle)
                manifest["files"].append({"path": name, "size": 0, "sha256": "0" * 64})
                self.rewrite_manifest(bundle, manifest)
                self.assert_invalid(bundle.path)

    def test_malformed_manifest_returns_structured_failure(self):
        for value in (None, [], "invalid", {}, {"schema_version": 1, "files": [None]}):
            with self.subTest(value=value):
                bundle = self.bundle()
                self.rewrite_manifest(bundle, value)
                self.assert_invalid(bundle.path)

    def test_nonexistent_bundle_returns_structured_failure(self):
        self.assert_invalid(self.root / "does-not-exist")

    def make_symlink(self, link, target, directory=False):
        try:
            link.symlink_to(target, target_is_directory=directory)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"Symlink creation not available on this runner: {exc}")

    def test_unlisted_symlink_is_rejected(self):
        bundle = self.bundle()
        external = self.root / "external.txt"
        external.write_text("outside evidence", encoding="utf-8")
        self.make_symlink(bundle.path / "linked.txt", external)
        self.assert_invalid(bundle.path)

    def test_manifested_symlink_is_rejected(self):
        bundle = self.bundle()
        original = bundle.path / "plan.json"
        external = self.root / "external-plan.json"
        external.write_bytes(original.read_bytes())
        original.unlink()
        self.make_symlink(original, external)
        self.assert_invalid(bundle.path)

    def test_symlinked_bundle_root_is_rejected(self):
        bundle = self.bundle()
        linked = self.root / "linked-run"
        self.make_symlink(linked, bundle.path, directory=True)
        self.assert_invalid(linked)

    def test_creation_and_sealing_reject_symlinks(self):
        actual_root = self.root / "actual"
        actual_root.mkdir()
        linked = self.root / "linked"
        self.make_symlink(linked, actual_root, directory=True)
        with self.assertRaises(ValueError):
            EvidenceBundle(linked, self.plan)
        bundle = EvidenceBundle(actual_root, self.plan)
        external = self.root / "external"
        external.write_text("external", encoding="utf-8")
        self.make_symlink(bundle.path / "linked", external)
        with self.assertRaises(ValueError):
            bundle.finalize("completed")

    @unittest.skipIf(os.name == "nt", "POSIX permissions are not represented by Windows chmod")
    def test_bundle_is_private_to_current_user(self):
        bundle = self.bundle()
        self.assertEqual(bundle.path.stat().st_mode & 0o777, 0o700)


if __name__ == "__main__":
    unittest.main()
