"""Worker-side parameter RPC tests; no sudo, sockets, or Bluetooth access."""
import asyncio
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from btdos import cases


class LinkWaitTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name).resolve()
        self.args = SimpleNamespace(parameter_rpc_dir=str(self.folder), stop_file=str(self.folder / "STOP"))

    async def reply(self, transform=None):
        async def server():
            request = self.folder / "link-request-1.json"
            while not request.exists():
                await asyncio.sleep(0)
            value = json.loads(request.read_text(encoding="utf-8"))
            self.assertEqual(set(value), {"schema_version", "sequence", "request_id"})
            response = dict(value, result={"status": "requested_unverified"})
            if transform:
                response = transform(response)
            (self.folder / "link-response-1.json").write_text(json.dumps(response), encoding="utf-8")
        task = asyncio.create_task(server())
        try:
            return await cases.request_link_parameters(self.args)
        finally:
            if not task.done():
                task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    async def test_matching_response_and_no_command_arguments_in_request(self):
        with patch.object(cases, "log"):
            result = await self.reply()
        self.assertEqual(result, {"status": "requested_unverified"})
        self.assertEqual(self.args._link_rpc_sequence, 1)

    async def test_response_with_wrong_id_is_rejected(self):
        with patch.object(cases, "log"), self.assertRaisesRegex(ValueError, "match"):
            await self.reply(lambda response: dict(response, request_id="0" * 32))

    async def test_unknown_response_fields_are_rejected(self):
        with patch.object(cases, "log"), self.assertRaises(ValueError):
            await self.reply(lambda response: dict(response, argv=["anything"]))

    async def test_stop_before_request_creates_no_file(self):
        (self.folder / "STOP").touch()
        with self.assertRaisesRegex(RuntimeError, "STOP"):
            await cases.request_link_parameters(self.args)
        self.assertFalse(list(self.folder.glob("link-request-*")))

    async def test_stop_while_waiting_does_not_retry_request(self):
        async def stop(*arguments):
            (self.folder / "STOP").touch()
        with patch.object(cases.asyncio, "sleep", side_effect=stop), patch.object(cases, "log"):
            with self.assertRaisesRegex(RuntimeError, "STOP"):
                await cases.request_link_parameters(self.args)
        self.assertEqual(len(list(self.folder.glob("link-request-*.json"))), 1)

    async def test_wait_deadline_is_bounded(self):
        clock = iter((0, 0, 21))
        async def no_wait(*arguments):
            pass
        with patch.object(cases, "time", SimpleNamespace(monotonic=lambda: next(clock))), \
                patch.object(cases.asyncio, "sleep", side_effect=no_wait), patch.object(cases, "log"):
            with self.assertRaisesRegex(TimeoutError, "supervisor"):
                await cases.request_link_parameters(self.args)

    async def test_invalid_internal_directory_does_not_write(self):
        self.args.parameter_rpc_dir = str(self.folder / "other")
        with self.assertRaises(ValueError):
            await cases.request_link_parameters(self.args)

    async def test_existing_request_is_not_overwritten(self):
        path = self.folder / "link-request-1.json"
        path.write_text("existing", encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "already exists"):
            await cases.request_link_parameters(self.args)
        self.assertEqual(path.read_text(), "existing")


if __name__ == "__main__":
    unittest.main()
