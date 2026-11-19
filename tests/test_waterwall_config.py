#!/usr/bin/env python3
import dataclasses
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from waterwall_config import ChainParameters, MODES, build_chains, build_core


class ChainTests(unittest.TestCase):
    def setUp(self):
        self.p = ChainParameters(mode='tls-mux', middle_port=40970, carrier_port=8450,
                                 end_wireguard_port=51901, end_address='192.0.2.2',
                                 tls_name='ws.example.com', certificate_path='/cert.pem',
                                 certificate_key_path='/key.pem', secret='a' * 32, salt='b' * 32,
                                 cover_address='192.0.2.3', cover_port=443)

    def test_generated_endpoints_and_secure_defaults(self):
        for mode in MODES:
            with self.subTest(mode=mode):
                middle, end = build_chains(dataclasses.replace(self.p, mode=mode))
                m = {n['name']: n for n in middle['nodes']}
                e = {n['name']: n for n in end['nodes']}
                self.assertEqual(m['ingress']['settings']['address'], '127.0.0.1')
                self.assertEqual(e['service']['settings'], {'address': '127.0.0.1', 'port': 51901})
                self.assertTrue(m['protect-c']['settings']['verify'])
                self.assertEqual('mux-c' in m, mode != 'reality')
                self.assertEqual('mux-s' in e, mode != 'reality')
                if mode == 'tls-mux':
                    self.assertEqual(e['protect-s']['settings']['min-version'], '1.3')
                    self.assertEqual(e['protect-s']['settings']['max-version'], '1.3')
                    self.assertEqual(m['auth-c']['settings']['password'], e['auth-s']['settings']['password'])
                else:
                    self.assertEqual(m['protect-c']['settings']['password'], e['protect-s']['settings']['password'])

    def test_core_disables_host_wide_tuning(self):
        misc = build_core()['misc']
        self.assertFalse(misc['splice'])
        self.assertFalse(misc['tcp-tune'])
        self.assertFalse(misc['try-enabling-bbr'])
        for filename in ('../secret.json', '/root/config.json', 'foo\n.json'):
            with self.subTest(filename=filename), self.assertRaises(ValueError):
                build_core(filename)

    def test_reject_invalid_inputs(self):
        invalid = [{'mode': 'wstunnel'}, {'middle_port': 0}, {'carrier_port': True},
                   {'end_address': 'example.com'}, {'cover_address': '::1'},
                   {'tls_name': 'host;echo BAD'}, {'certificate_path': '../key'},
                   {'certificate_key_path': 'relative'}, {'secret': 'short'},
                   {'salt': 'wrong-characters-are-rejected'}, {'verify_tls': 'false'}]
        for changes in invalid:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                build_chains(dataclasses.replace(self.p, **changes))


if __name__ == '__main__':
    unittest.main()
