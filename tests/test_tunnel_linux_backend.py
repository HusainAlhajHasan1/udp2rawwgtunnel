#!/usr/bin/env python3
"""Backend tests use temp files and simulated commands: no network changes."""
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
loader = importlib.machinery.SourceFileLoader('tunnel_test', str(Path(__file__).resolve().parents[1] / 'scripts/tunnel'))
spec = importlib.util.spec_from_loader(loader.name, loader)
tunnel = importlib.util.module_from_spec(spec)
loader.exec_module(tunnel)
from waterwall_switch_transaction import SwitchFailure


class Result:
    def __init__(self, output='', rc=0):
        self.stdout = output
        self.stderr = ''
        self.returncode = rc


class FakeSystem:
    peer = 'A' * 43 + '='

    def __init__(self):
        self.endpoint = '127.0.0.1:40962'
        self.rx = self.tx = 100
        self.health = True
        self.active = True
        self.scheduled = []

    def command(self, *args, **kwargs):
        if args == ('wg', 'show', 'wgexit', 'peers'):
            return Result(self.peer + '\n')
        if args == ('wg', 'show', 'wgexit', 'endpoints'):
            return Result(f'{self.peer}\t{self.endpoint}\n')
        if args == ('wg', 'show', 'wgexit', 'transfer'):
            return Result(f'{self.peer}\t{self.rx}\t{self.tx}\n')
        if args[:4] == ('wg', 'set', 'wgexit', 'peer'):
            self.endpoint = args[-1]
            return Result()
        if args[:3] == ('systemctl', 'is-active', '--quiet'):
            return Result(rc=0 if self.active else 3)
        if args[:3] == ('systemctl', 'stop', '--no-block'):
            return Result()
        if args[0] == 'systemd-run':
            self.scheduled.append(args)
            return Result()
        if args[0] == 'ip':
            return Result('1.1.1.1 from 10.89.0.1 dev wgexit table 10746\n')
        if args[0] == 'curl':
            if self.health:
                self.rx += 100
                self.tx += 100
            return Result(rc=0 if self.health else 28)
        raise AssertionError(f'unexpected command {args}')


@unittest.skipUnless(os.geteuid() == 0, 'uses root-owned temp files to exercise real file safety checks')
class LinuxBackendTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='ww-switch-tests-')
        root = Path(self.tmp.name)
        tunnel.STATE = root / 'state'
        tunnel.STATE.mkdir(mode=0o700)
        tunnel.CONF = root / 'wgexit.conf'
        self.original = '[Interface]\nPrivateKey = test-placeholder\n\n[Peer]\nEndpoint = 127.0.0.1:40962\n'
        tunnel.CONF.write_text(self.original)
        tunnel.CONF.chmod(0o600)
        self.fake = FakeSystem()
        self.patch = mock.patch.object(tunnel, 'command', self.fake.command)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.tmp.cleanup()

    def test_success_persists_and_disarms(self):
        b = tunnel.LinuxBackend('reality-mux')
        tunnel.switch_under_lock(b, 'reality-mux')
        self.assertEqual(self.fake.endpoint, '127.0.0.1:40970')
        self.assertEqual(tunnel.read_config()[1], '127.0.0.1:40970')
        self.assertFalse((tunnel.STATE / 'pending.json').exists())
        self.assertIn('PrivateKey = test-placeholder', tunnel.CONF.read_text())
        self.assertEqual(len(self.fake.scheduled), 1)
        self.assertEqual(tunnel.CONF.stat().st_mode & 0o777, 0o600)

    def test_health_failure_restores(self):
        self.fake.health = False
        with self.assertRaises(SwitchFailure):
            tunnel.switch_under_lock(tunnel.LinuxBackend('reality'), 'reality')
        self.assertEqual(self.fake.endpoint, '127.0.0.1:40962')
        self.assertEqual(tunnel.CONF.read_text(), self.original)
        self.assertFalse((tunnel.STATE / 'pending.json').exists())

    def test_timer_recovery_restores_partial_persistence(self):
        b = tunnel.LinuxBackend('tls-mux')
        b.preflight('tls-mux')
        snap = b.snapshot()
        b.arm_rollback(snap)
        b.set_live('tls-mux')
        b.persist('tls-mux')
        self.assertEqual(tunnel.read_config()[1], '127.0.0.1:40972')
        # Simulate an independent timer calling the internal rollback handler.
        with mock.patch.object(sys, 'argv', ['tunnel', '__rollback', snap.token]):
            tunnel.main()
        self.assertEqual(self.fake.endpoint, '127.0.0.1:40962')
        self.assertEqual(tunnel.CONF.read_text(), self.original)
        self.assertFalse((tunnel.STATE / 'pending.json').exists())
        # A stale timer must not overwrite a later state.
        with mock.patch.object(sys, 'argv', ['tunnel', '__rollback', snap.token]):
            tunnel.main()
        self.assertEqual(self.fake.endpoint, '127.0.0.1:40962')

    def test_external_change_is_not_overwritten(self):
        b = tunnel.LinuxBackend('reality')
        b.preflight('reality')
        snap = b.snapshot()
        b.arm_rollback(snap)
        self.fake.endpoint = '127.0.0.1:12345'
        with self.assertRaises(RuntimeError):
            b.restore(snap)
        self.assertEqual(self.fake.endpoint, '127.0.0.1:12345')
        self.assertTrue((tunnel.STATE / 'pending.json').exists())

    def test_live_and_saved_must_match_before_switch(self):
        self.fake.endpoint = '127.0.0.1:40970'
        with self.assertRaises(RuntimeError):
            tunnel.LinuxBackend('tls-mux').preflight('tls-mux')
        self.assertFalse((tunnel.STATE / 'pending.json').exists())

    def test_saveconfig_refused(self):
        tunnel.CONF.write_text(self.original + 'SaveConfig = true\n')
        with self.assertRaises(RuntimeError):
            tunnel.LinuxBackend('tls-mux').preflight('tls-mux')


if __name__ == '__main__':
    unittest.main()
