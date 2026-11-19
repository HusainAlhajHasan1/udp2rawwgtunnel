#!/usr/bin/env python3
import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from waterwall_switch_transaction import Snapshot, SwitchFailure, RollbackFailure, switch_under_lock


class FakeBackend:
    def __init__(self, fail=None, health=True):
        self.fail = set(fail or ())
        self.health = health
        self.live = self.saved = 'wstunnel'
        self.timer = False
        self.retained = False
        self.events = []

    def _record(self, name):
        self.events.append(name)
        if name in self.fail:
            raise RuntimeError('injected ' + name)

    def preflight(self, target):
        self._record('preflight')

    def snapshot(self):
        self._record('snapshot')
        self.retained = True
        return Snapshot('test-token', self.live)

    def arm_rollback(self, snap):
        # Simulate a possible partial systemd failure (timer installed, error returned).
        self.timer = True
        self._record('arm')

    def set_live(self, target):
        self.live = target
        self._record('live')

    def verify_connectivity(self, target):
        self._record('probe')
        return self.health

    def persist(self, target):
        self.saved = target
        self._record('persist')

    def restore(self, snap):
        self._record('restore')
        self.live = self.saved = snap.old_mode

    def disarm_rollback(self, snap):
        self._record('disarm')
        self.timer = False

    def discard_snapshot(self, snap):
        self._record('discard')
        self.retained = False


class TransactionTests(unittest.TestCase):
    def test_success_all_modes(self):
        for target in ('reality-mux', 'reality', 'tls-mux', 'wstunnel'):
            with self.subTest(target=target):
                b = FakeBackend()
                switch_under_lock(b, target)
                self.assertEqual((b.live, b.saved), (target, target))
                self.assertFalse(b.timer)
                self.assertFalse(b.retained)
                self.assertEqual(b.events, ['preflight', 'snapshot', 'arm', 'live', 'probe', 'persist', 'disarm', 'discard'])

    def test_failed_health_or_partial_write_restores_both(self):
        for fail in (['live'], ['probe'], ['persist']):
            with self.subTest(fail=fail):
                b = FakeBackend(fail=fail)
                with self.assertRaises(SwitchFailure):
                    switch_under_lock(b, 'reality')
                self.assertEqual((b.live, b.saved), ('wstunnel', 'wstunnel'))
                self.assertFalse(b.timer)
                self.assertFalse(b.retained)
        b = FakeBackend(health=False)
        with self.assertRaises(SwitchFailure):
            switch_under_lock(b, 'tls-mux')
        self.assertEqual((b.live, b.saved), ('wstunnel', 'wstunnel'))

    def test_failed_restore_keeps_timer_and_snapshot(self):
        b = FakeBackend(fail={'persist', 'restore'})
        with self.assertRaises(RollbackFailure):
            switch_under_lock(b, 'tls-mux')
        self.assertTrue(b.timer)
        self.assertTrue(b.retained)
        self.assertNotIn('discard', b.events)

    def test_timer_failure_keeps_snapshot(self):
        b = FakeBackend(fail={'arm'})
        with self.assertRaises(SwitchFailure):
            switch_under_lock(b, 'reality')
        self.assertEqual((b.live, b.saved), ('wstunnel', 'wstunnel'))
        self.assertTrue(b.retained)
        self.assertTrue(b.timer)
        self.assertNotIn('discard', b.events)

    def test_disarm_failure_keeps_snapshot(self):
        b = FakeBackend(fail={'disarm'})
        with self.assertRaises(SwitchFailure):
            switch_under_lock(b, 'reality')
        self.assertEqual((b.live, b.saved), ('reality', 'reality'))
        self.assertTrue(b.timer)
        self.assertTrue(b.retained)

    def test_invalid_target_has_no_effect(self):
        b = FakeBackend()
        with self.assertRaises(ValueError):
            switch_under_lock(b, 'bogus')
        self.assertEqual(b.events, [])


if __name__ == '__main__':
    unittest.main()
