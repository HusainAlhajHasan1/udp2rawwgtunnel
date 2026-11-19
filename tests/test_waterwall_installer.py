#!/usr/bin/env python3
"""Installer tests with all host paths and system calls mocked."""
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS))
loader = importlib.machinery.SourceFileLoader('installer_test', str(SCRIPTS / 'waterwall-install.py'))
spec = importlib.util.spec_from_loader(loader.name, loader)
installer = importlib.util.module_from_spec(spec)
loader.exec_module(installer)


@unittest.skipUnless(os.geteuid() == 0, 'installer requires root; all filesystem paths are redirected to temp')
class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='ww-install-tests-')
        root = Path(self.temp.name)
        installer.ROOT = root / 'etc' / 'waterwall-wg'
        installer.LIB = root / 'libexec' / 'waterwall-wg'
        installer.BIN = installer.LIB / 'Waterwall'
        installer.STATE = root / 'state'
        installer.CLI = root / 'sbin' / 'tunnel'
        installer.UNIT = root / 'units'
        for p in (installer.ROOT.parent, installer.LIB.parent, installer.CLI.parent, installer.UNIT):
            p.mkdir(parents=True, exist_ok=True)
        self.commands = []
        self.patches = [
            mock.patch.object(installer, 'get_binary', return_value=b'test executable'),
            mock.patch.object(installer, 'local_address_exists', return_value=True),
            mock.patch.object(installer, 'is_active', return_value=True),
            mock.patch.object(installer, 'check_cert'),
            mock.patch.object(installer.getpass, 'getpass', return_value='a' * 32),
            mock.patch.object(installer.shutil, 'which', side_effect=lambda x: None if x == 'tunnel' else '/usr/bin/' + x),
            mock.patch.object(installer, 'run', side_effect=self.fake_run),
            mock.patch.object(installer, 'listening', side_effect=self.fake_listening),
        ]
        for patch in self.patches:
            patch.start()

    def fake_run(self, *args, **kwargs):
        self.commands.append(args)
        return mock.Mock(returncode=0, stdout=b'', stderr=b'')

    def fake_listening(self, proto, port):
        return (proto, port) in (('u', 40962), ('u', 51901), ('t', 8444))

    def tearDown(self):
        for patch in reversed(self.patches):
            patch.stop()
        self.temp.cleanup()

    def test_both_roles_write_only_separate_state_and_units(self):
        for role in ('end', 'middle'):
            with self.subTest(role=role):
                installer.install(role)
                for mode in installer.MODES:
                    directory = installer.ROOT / mode
                    core = json.loads((directory / 'core.json').read_text())
                    self.assertFalse(core['misc']['splice'])
                    self.assertFalse(core['misc']['tcp-tune'])
                    self.assertFalse(core['misc']['try-enabling-bbr'])
                    self.assertEqual((directory / 'config.json').stat().st_mode & 0o777, 0o600)
                    unit = (installer.UNIT / f'waterwall-wg-{role}-{mode}.service').read_text()
                    self.assertIn('ProtectKernelTunables=yes', unit)
                if role == 'middle':
                    self.assertTrue(installer.CLI.is_file())
                    self.assertTrue((installer.LIB / 'waterwall_switch_transaction.py').is_file())
                for args in self.commands:
                    self.assertNotIn(args[0], ('iptables', 'nft', 'sysctl', 'wg'))
                    if args[0] == 'systemctl' and 'enable' in args:
                        self.assertTrue(args[-1].startswith('waterwall-wg-'))
                # Use an entirely fresh temp tree for the second server.
                if role == 'end':
                    self.tearDown()
                    self.setUp()

    def test_collision_aborts_before_creating_files_or_starting_services(self):
        installer.ROOT.mkdir()
        (installer.ROOT / 'keep').write_text('do not change')
        with self.assertRaises(RuntimeError):
            installer.install('end')
        self.assertEqual((installer.ROOT / 'keep').read_text(), 'do not change')
        self.assertFalse(installer.LIB.exists())
        self.assertEqual(self.commands, [])

    def test_used_port_aborts_before_download(self):
        with mock.patch.object(installer, 'listening', return_value=True), \
             mock.patch.object(installer, 'get_binary') as download:
            with self.assertRaises(RuntimeError):
                installer.install('end')
            download.assert_not_called()
        self.assertFalse(installer.ROOT.exists())


if __name__ == '__main__':
    unittest.main()
