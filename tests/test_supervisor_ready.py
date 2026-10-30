"""Offline manual-ready lifecycle, budget and error-summary regressions."""
from contextlib import ExitStack
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from btdos import capture, supervisor
from btdos.config import Plan
from btdos.doctor import inspect_plan_adapter
from btdos.evidence import verify_bundle
from btdos.report import render_report


class Clock:
    now = 0.0
    def monotonic(self):
        return self.now
    def sleep(self, seconds):
        self.now += seconds


class ReadySupervisorTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.plan = Plan(case='br-sdp', adapter='hci0', target='02:00:00:00:00:01',
                         local_address='02:00:00:00:00:02',baseline=0,recovery=0,
                         output_root=str(self.root/'runs'))
        self.clock = Clock()
        self.capture = Mock(name='capture')
        self.capture.name = 'hci'
        self.capture.alive.return_value = True
        self.capture.stop.return_value = dict(name='hci',file=None,forced=False,cleanup_confirmed=True)
        self.worker = Mock()
        self.worker.poll.return_value = self.worker.returncode = 0
        self.phases = []
        self.folder = None
        self.controllers = [dict(adapter='hci0',address=self.plan.local_address,supported_settings='br/edr le')]
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(supervisor,'time',SimpleNamespace(monotonic=self.clock.monotonic,sleep=self.clock.sleep)))
        self.enumerate = self.stack.enter_context(patch.object(supervisor,'adapters',return_value=self.controllers))
        self.inspect = self.stack.enter_context(patch.object(supervisor,'inspect_plan_adapter',side_effect=inspect_plan_adapter))
        self.diagnose = self.stack.enter_context(patch.object(supervisor,'diagnose',return_value={'adapters':self.controllers}))
        self.build = self.stack.enter_context(patch.object(supervisor,'build_captures',return_value=[self.capture]))
        self.stack.enter_context(patch.object(supervisor,'export_index',return_value={'status':'offline_fixture'}))
        self.popen = self.stack.enter_context(patch.object(supervisor.subprocess,'Popen',return_value=self.worker))

    def update(self,phase,path,elapsed):
        self.phases.append(phase)
        self.folder = path

    def run_fixture(self,poll=None):
        path,status = supervisor._run_locked(self.plan,self.update,ready_poll=poll)
        self.assertTrue(verify_bundle(path)['ok'])
        self.capture.stop.assert_called_once()
        return path,status,json.loads((path/'result.json').read_text(encoding='utf-8'))

    def test_default_path_no_wait_and_one_fresh_adapter_snapshot(self):
        path,status,result = self.run_fixture()
        self.assertEqual(status,'completed')
        self.assertNotIn('WAIT_TARGET_READY',self.phases)
        self.assertEqual(result['runtime_wall_budget_s'],self.plan.wall_budget)
        self.assertEqual(result['target_ready']['status'],'disabled')
        self.enumerate.assert_called_once_with()
        self.inspect.assert_called_once_with(self.plan,controllers=self.controllers)
        self.diagnose.assert_called_once_with(controllers=self.controllers)
        self.build.assert_called_once_with(self.plan,path)
        environment=json.loads((path/'environment.json').read_text(encoding='utf-8'))
        self.assertIn('adapter_snapshot_at',environment)
        self.assertIn('preparation_elapsed_s',environment)

    def test_confirmation_precedes_baseline_and_worker_and_adds_budget(self):
        answers=iter([None,'pending','ready'])
        def poll():
            self.assertEqual(self.phases[-1],'WAIT_TARGET_READY')
            self.popen.assert_not_called()
            return next(answers)
        path,status,result=self.run_fixture(poll)
        self.assertEqual(status,'completed')
        self.assertLess(self.phases.index('WAIT_TARGET_READY'),self.phases.index('BASELINE'))
        self.assertLess(self.phases.index('BASELINE'),self.phases.index('RUN'))
        self.assertEqual(result['runtime_wall_budget_s'],self.plan.wall_budget+60)
        self.assertEqual(result['target_ready']['status'],'ready')
        self.assertEqual(result['target_ready']['confirmation'],'manual_not_connectivity_proof')
        self.build.assert_called_once_with(self.plan,path,wall_budget=self.plan.wall_budget+60)
        self.popen.assert_called_once()

    def test_cancel_eof_timeout_and_invalid_poll_never_start_worker(self):
        for name in ['cancel','eof','timeout','invalid','late_ready']:
            with self.subTest(name=name):
                # A new case instance isolates mocks, output and virtual clocks.
                case=ReadySupervisorTests()
                case.setUp()
                try:
                    def poll():
                        if name=='eof':raise EOFError()
                        if name=='timeout':return None
                        if name=='late_ready':case.clock.sleep(61);return 'ready'
                        return 'cancel' if name=='cancel' else False
                    path,status,result=case.run_fixture(poll)
                    expected='interrupted' if name in ('cancel','eof') else 'target_not_ready' if name in ('timeout','late_ready') else 'failed'
                    self.assertEqual(status,expected)
                    case.popen.assert_not_called()
                    self.assertFalse(result['worker_started'])
                    self.assertTrue(result['cleanup_confirmed'])
                    self.assertFalse((path/'worker.jsonl').exists())
                    self.assertNotIn('BASELINE',case.phases)
                    if name in ('timeout','late_ready'):
                        self.assertGreaterEqual(result['target_ready']['elapsed_s'],60)
                        self.assertEqual(result['target_ready']['status'],'timed_out')
                finally:case.doCleanups()

    def test_stop_during_poll_wins_over_simultaneous_ready(self):
        def poll():
            (self.folder/'STOP').touch()
            return 'ready'
        _,status,result=self.run_fixture(poll)
        self.assertEqual(status,'interrupted')
        self.popen.assert_not_called()
        self.assertEqual(result['target_ready']['status'],'aborted')

    def test_capture_death_while_waiting_cleans_and_stops(self):
        def poll():
            self.capture.alive.return_value=False
            return None
        _,status,result=self.run_fixture(poll)
        self.assertEqual(status,'failed')
        self.assertIn('采集进程',result['error'])
        self.popen.assert_not_called()

    def test_wall_clock_not_reset_by_wait(self):
        def poll():
            self.clock.sleep(self.plan.wall_budget+61)
            return 'ready'
        _,status,result=self.run_fixture(poll)
        self.assertEqual(status,'failed')
        self.assertIn('墙钟预算',result['error'])
        self.popen.assert_not_called()

    def test_wrong_adapter_address_rejected_before_capture(self):
        self.controllers[0]['address']='02:00:00:00:00:03'
        path,status=supervisor._run_locked(self.plan,self.update,ready_poll=lambda:'ready')
        self.assertEqual(status,'failed')
        self.build.assert_not_called()
        self.popen.assert_not_called()
        self.assertTrue(verify_bundle(path)['ok'])
        result=json.loads((path/'result.json').read_text(encoding='utf-8'))
        self.assertFalse(result['worker_started'])
        self.assertTrue(result['cleanup_confirmed'])

    def test_worker_error_and_connection_stage_reach_result_and_report(self):
        self.worker.poll.return_value=self.worker.returncode=1
        def start(*args,**kwargs):
            out=kwargs['stdout']
            out.write((json.dumps({'event':'br_connect_failed','failure_stage':'connect','error':"TimeoutError('timed out')"})+'\n').encode())
            out.write((json.dumps({'event':'worker_end','status':'error_stop','error':"TimeoutError('timed out')"})+'\n').encode())
            out.flush()
            return self.worker
        self.popen.side_effect=start
        path,status,result=self.run_fixture()
        self.assertEqual(status,'worker_failed')
        self.assertIn('timed out',result['error'])
        self.assertEqual(result['worker_statistics']['last_connection_event']['failure_stage'],'connect')
        self.assertIn('timed out',(path/'report.md').read_text(encoding='utf-8'))

    def test_missing_worker_error_has_explicit_fallback(self):
        self.worker.poll.return_value=self.worker.returncode=1
        _,status,result=self.run_fixture()
        self.assertEqual(status,'worker_failed')
        self.assertIn('退出码1',result['error'])
        report=render_report(self.plan.to_dict(),dict(status='worker_failed',worker_statistics={}))
        self.assertNotIn('停止/错误：无已记录错误',report)

    def test_capture_ttl_uses_same_effective_budget_plus_existing_margin(self):
        with patch.object(capture,'system_binary',side_effect=lambda name:'/usr/bin/'+name),patch.object(capture.os,'geteuid',return_value=0,create=True):
            normal=capture.build_captures(self.plan,self.root)[0]
            waiting=capture.build_captures(self.plan,self.root,wall_budget=self.plan.wall_budget+60)[0]
        self.assertEqual(normal.argv[3],str(self.plan.wall_budget+15)+'s')
        self.assertEqual(waiting.argv[3],str(self.plan.wall_budget+75)+'s')

    def test_invalid_capture_ttl_cannot_disable_timeout(self):
        for value in (0,-1,False,float('nan'),float('inf'),'60'):
            with self.subTest(value=value),patch.object(capture,'system_binary') as tools:
                with self.assertRaises(ValueError):
                    capture.build_captures(self.plan,self.root,wall_budget=value)
                tools.assert_not_called()


if __name__=='__main__':
    unittest.main()
