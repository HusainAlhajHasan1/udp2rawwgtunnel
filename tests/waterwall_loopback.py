#!/usr/bin/env python3
"""Experimental localhost protocol probe; NOT a deployment or security test.

Requires a locally built WaterWall v1.5.42 executable and OpenSSL. All listeners
are loopback-only; the temporary self-signed TLS certificate is deliberately not
verified. Never use these settings on a server.
"""
import argparse
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from waterwall_config import ChainParameters, build_chains, build_core


def free_port(kind):
    with socket.socket(socket.AF_INET, kind) as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


def config(mode, ingress, carrier, service, cover, cert, key):
    params = ChainParameters(mode=mode, middle_port=ingress, carrier_port=carrier,
        end_wireguard_port=service, end_address='127.0.0.1', tls_name='localhost',
        certificate_path=str(cert), certificate_key_path=str(key),
        secret='testonlysharedsecret2026', salt='testonlysharedsalt2026',
        cover_address='127.0.0.1', cover_port=cover, verify_tls=False)
    client, server = build_chains(params)
    return {'name': 'local-only-' + mode, 'nodes': client['nodes'] + server['nodes']}


def run(mode, binary, root, cert, key):
    ingress, service = free_port(socket.SOCK_DGRAM), free_port(socket.SOCK_DGRAM)
    while service == ingress:
        service = free_port(socket.SOCK_DGRAM)
    carrier, cover = free_port(socket.SOCK_STREAM), free_port(socket.SOCK_STREAM)
    while carrier == cover:
        cover = free_port(socket.SOCK_STREAM)
    folder = root / mode
    folder.mkdir()
    (folder / 'log').mkdir()
    core = build_core()
    for name in ('internal', 'core', 'network', 'dns'):
        core['log'][name]['loglevel'] = 'DEBUG'
    (folder / 'core.json').write_text(json.dumps(core))
    (folder / 'config.json').write_text(json.dumps(config(mode, ingress, carrier, service, cover, cert, key)))
    cover_process = None
    with open(folder / 'waterwall.log', 'wb') as logfile, socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as receiver, socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender:
        receiver.bind(('127.0.0.1', service))
        receiver.settimeout(6)
        sender.settimeout(6)
        try:
            if mode.startswith('reality'):
                cover_process = subprocess.Popen(['openssl', 's_server', '-quiet', '-accept',
                    f'127.0.0.1:{cover}', '-cert', str(cert), '-key', str(key)],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                time.sleep(0.5)  # Let the test-only cover listener bind before WaterWall connects.
                if cover_process.poll() is not None:
                    raise RuntimeError('test cover server failed to start')
            proc = subprocess.Popen([str(binary), '-c:core.json'], cwd=folder, stdout=logfile, stderr=logfile)
            deadline = time.monotonic() + 8
            while time.monotonic() < deadline:
                if proc.poll() is not None:
                    raise RuntimeError(f'{mode}: WaterWall exited ({proc.returncode}); see {folder}/waterwall.log')
                if 'starting workers' in (folder / 'waterwall.log').read_text(errors='replace'):
                    break
                time.sleep(.05)
            else:
                raise RuntimeError(f'{mode}: startup timed out; see {folder}/waterwall.log')
            for payload in (b'hello', os.urandom(1280), b'last'):
                sender.sendto(payload, ('127.0.0.1', ingress))
                data, peer = receiver.recvfrom(65535)
                if data != payload:
                    raise AssertionError('datagram altered')
                receiver.sendto(b'R' + data, peer)
                reply, _ = sender.recvfrom(65535)
                if reply != b'R' + payload:
                    raise AssertionError('reply altered')
            print(f'{mode}: UDP datagrams passed in both directions (loopback, verification disabled)')
        except socket.timeout as exc:
            raise RuntimeError(f'{mode}: UDP timed out; see {folder}/waterwall.log') from exc
        finally:
            if 'proc' in locals():
                proc.terminate()
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
            if cover_process:
                cover_process.terminate()
                cover_process.wait(timeout=3)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('binary', type=Path, help='locally built WaterWall 1.5.42')
    args = parser.parse_args()
    binary = args.binary.resolve(strict=True)
    with tempfile.TemporaryDirectory(prefix='ww-loopback-') as tmp:
        root = Path(tmp)
        cert, key = root / 'cert.pem', root / 'key.pem'
        subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes',
                        '-keyout', str(key), '-out', str(cert), '-days', '1',
                        '-subj', '/CN=localhost'], stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, check=True)
        for mode in ('reality-mux', 'reality', 'tls-mux'):
            try:
                run(mode, binary, root, cert, key)
            except Exception:
                log = root / mode / 'waterwall.log'
                if log.exists():
                    print(log.read_text(errors='replace')[-12000:])
                raise


if __name__ == '__main__':
    main()
