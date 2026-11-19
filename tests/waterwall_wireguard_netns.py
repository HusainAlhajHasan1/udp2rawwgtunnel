#!/usr/bin/env python3
"""WireGuard integration smoke tests: isolated Linux namespaces, not a deployer.

Run only as root inside fresh private mount and network namespaces, e.g.:
  sudo unshare -m -n python3 tests/waterwall_wireguard_netns.py /path/Waterwall /path/wg
Requires iproute2, ping and openssl. The throwaway cert is deliberately NOT
verified; these local tests are not security, switching, or performance tests.
"""
import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
sys.dont_write_bytecode = True
from waterwall_loopback import build_core, config


def cmd(*args, **kwargs):
    return subprocess.run(args, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, **kwargs)


def ns(name, *args, **kwargs):
    return cmd('ip', 'netns', 'exec', name, *args, **kwargs)


def launch(name, args, cwd, log):
    return subprocess.Popen(['ip', 'netns', 'exec', name, *map(str, args)], cwd=cwd,
                            stdout=log, stderr=log, start_new_session=True)


def stop(proc):
    if proc and proc.poll() is None:
        os.killpg(proc.pid, signal.SIGTERM)
        try:
            proc.wait(timeout=4)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait()


def mkdir_config(folder, nodes):
    folder.mkdir()
    (folder / 'log').mkdir()
    core = build_core()
    for name in ('internal', 'core', 'network', 'dns'):
        core['log'][name]['loglevel'] = 'DEBUG'
    (folder / 'core.json').write_text(json.dumps(core))
    (folder / 'config.json').write_text(json.dumps({'name': 'netns-local-only', 'nodes': nodes}))


def make_wireguard(wg, root):
    for name in ('ww-middle', 'ww-end'):
        cmd('ip', 'netns', 'add', name)
        ns(name, 'ip', 'link', 'set', 'lo', 'up')
    cmd('ip', 'link', 'add', 'veth-m', 'type', 'veth', 'peer', 'name', 'veth-e')
    cmd('ip', 'link', 'set', 'veth-m', 'netns', 'ww-middle')
    cmd('ip', 'link', 'set', 'veth-e', 'netns', 'ww-end')
    for name, iface, addr in (('ww-middle', 'veth-m', '192.0.2.1/24'), ('ww-end', 'veth-e', '192.0.2.2/24')):
        ns(name, 'ip', 'addr', 'add', addr, 'dev', iface)
        ns(name, 'ip', 'link', 'set', iface, 'up')
        ns(name, 'ip', 'link', 'add', 'wgtest', 'type', 'wireguard')
    keys = []
    for label in ('middle', 'end'):
        priv = cmd(str(wg), 'genkey').stdout
        keyfile = root / (label + '.key')
        keyfile.write_bytes(priv)
        keyfile.chmod(0o600)
        pub = subprocess.run([str(wg), 'pubkey'], input=priv, check=True, stdout=subprocess.PIPE).stdout.decode().strip()
        keys.append((keyfile, pub))
    ns('ww-middle', str(wg), 'set', 'wgtest', 'listen-port', '34111', 'private-key', str(keys[0][0]),
       'peer', keys[1][1], 'allowed-ips', '10.253.0.2/32', 'endpoint', '127.0.0.1:34113',
       'persistent-keepalive', '1')
    ns('ww-end', str(wg), 'set', 'wgtest', 'listen-port', '34112', 'private-key', str(keys[1][0]),
       'peer', keys[0][1], 'allowed-ips', '10.253.0.1/32')
    for name, addr, dest in (('ww-middle', '10.253.0.1/32', '10.253.0.2/32'),
                              ('ww-end', '10.253.0.2/32', '10.253.0.1/32')):
        ns(name, 'ip', 'addr', 'add', addr, 'dev', 'wgtest')
        ns(name, 'ip', 'link', 'set', 'wgtest', 'up')
        ns(name, 'ip', 'route', 'add', dest, 'dev', 'wgtest')
    return keys[0][1], keys[1][1]


def reset_peers(wg, middle_pub, end_pub):
    # Remove and recreate both peers so every mode must negotiate a new handshake.
    ns('ww-middle', str(wg), 'set', 'wgtest', 'peer', end_pub, 'remove')
    ns('ww-end', str(wg), 'set', 'wgtest', 'peer', middle_pub, 'remove')
    ns('ww-end', str(wg), 'set', 'wgtest', 'peer', middle_pub, 'allowed-ips', '10.253.0.1/32')
    ns('ww-middle', str(wg), 'set', 'wgtest', 'peer', end_pub, 'allowed-ips', '10.253.0.2/32',
       'endpoint', '127.0.0.1:34113', 'persistent-keepalive', '1')


def run(mode, binary, wg, root, cert, key, rejection=None, verify_tls=False):
    all_nodes = config(mode, 34113, 34114, 34112, 34115, cert, key)['nodes']
    if verify_tls:
        next(n for n in all_nodes if n['name'] == 'protect-c')['settings']['verify'] = True
    if rejection == 'bad-reality-password':
        next(n for n in all_nodes if n['name'] == 'protect-c')['settings']['password'] = 'wrong-test-password'
    elif rejection == 'untrusted-tls':
        next(n for n in all_nodes if n['name'] == 'protect-c')['settings']['verify'] = True
    elif rejection == 'bad-tls-secret':
        next(n for n in all_nodes if n['name'] == 'auth-c')['settings']['password'] = 'wrong-test-password'
    elif rejection == 'wrong-hostname':
        # Server accepts this SNI, but the trusted certificate has SAN localhost only.
        next(n for n in all_nodes if n['name'] == 'protect-c')['settings']['sni'] = 'other.localhost'
        next(n for n in all_nodes if n['name'] == 'protect-s')['settings']['sni'] = 'other.localhost'
    names = {'ingress', 'uot-c', 'mux-c', 'auth-c', 'protect-c', 'out'}
    client = [n for n in all_nodes if n['name'] in names]
    server = [n for n in all_nodes if n['name'] not in names]
    next(n for n in client if n['name'] == 'out')['settings']['address'] = '192.0.2.2'
    next(n for n in server if n['name'] == 'in')['settings']['address'] = '192.0.2.2'
    base = root / (mode + ('-' + rejection if rejection else ''))
    base.mkdir()
    c, s = base / 'client', base / 'server'
    mkdir_config(c, client)
    mkdir_config(s, server)
    processes = []
    with open(base / 'client.log', 'wb') as clog, open(base / 'server.log', 'wb') as slog, open(base / 'cover.log', 'wb') as coverlog:
        try:
            if mode.startswith('reality'):
                processes.append(launch('ww-end', ['openssl', 's_server', '-quiet', '-accept', '127.0.0.1:34115',
                    '-cert', str(cert), '-key', str(key)], s, coverlog))
                time.sleep(.3)
            processes.append(launch('ww-end', [binary, '-c:core.json'], s, slog))
            processes.append(launch('ww-middle', [binary, '-c:core.json'], c, clog))
            deadline = time.monotonic() + 8
            while time.monotonic() < deadline:
                if any(p.poll() is not None for p in processes):
                    raise RuntimeError(f'{mode}: process exited unexpectedly')
                if all('starting workers' in (base / f'{name}.log').read_text(errors='replace') for name in ('client', 'server')):
                    break
                time.sleep(.05)
            else:
                raise RuntimeError(f'{mode}: startup timed out')
            # Retry briefly while transport and WireGuard negotiate.
            deadline = time.monotonic() + (8 if rejection else 15)
            while True:
                ping = subprocess.run(['ip', 'netns', 'exec', 'ww-middle', 'ping', '-n', '-c', '1', '-W', '1',
                                       '-I', 'wgtest', '10.253.0.2'], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                if ping.returncode == 0:
                    if rejection:
                        raise AssertionError(f'{mode}: unexpectedly accepted {rejection}')
                    break
                if time.monotonic() >= deadline:
                    if rejection:
                        handshake = ns('ww-middle', str(wg), 'show', 'wgtest', 'latest-handshakes').stdout.decode().split()
                        if len(handshake) != 2 or int(handshake[1]) != 0:
                            raise AssertionError(f'{mode}: handshake despite {rejection}')
                        print(f'{mode}: no WireGuard connection with {rejection}')
                        return
                    raise RuntimeError(f'{mode}: WireGuard ping failed')
            handshake = ns('ww-middle', str(wg), 'show', 'wgtest', 'latest-handshakes').stdout.decode().split()
            if len(handshake) != 2 or int(handshake[1]) <= 0:
                raise AssertionError(f'{mode}: missing WireGuard handshake')
            print(f'{mode}: fresh WireGuard handshake and ICMP reply across two network namespaces')
        except Exception:
            for n in ('client', 'server', 'cover'):
                print(f'--- {mode} {n} ---\n' + (base / (n + '.log')).read_text(errors='replace')[-8000:])
            raise
        finally:
            for p in reversed(processes):
                stop(p)


def main():
    p = argparse.ArgumentParser()
    p.add_argument('waterwall', type=Path)
    p.add_argument('wg', type=Path)
    p.add_argument('--verified-cert', type=Path, help='local test CA-signed localhost leaf')
    p.add_argument('--verified-key', type=Path, help='key for --verified-cert')
    args = p.parse_args()
    if bool(args.verified_cert) != bool(args.verified_key):
        p.error('--verified-cert and --verified-key must be supplied together')
    if os.geteuid() != 0:
        p.error('root required inside private network and mount namespaces')
    binary, wg = args.waterwall.resolve(strict=True), args.wg.resolve(strict=True)
    # Refuse to modify the initial namespace, even if run directly as root.
    for kind in ('mnt', 'net'):
        if os.stat(f'/proc/self/ns/{kind}').st_ino == os.stat(f'/proc/1/ns/{kind}').st_ino:
            p.error('run inside fresh private mount AND network namespaces via unshare -m -n')
    cmd('mount', '--make-rprivate', '/')
    with tempfile.TemporaryDirectory(prefix='waterwall-wg-netns-') as tmp:
        root = Path(tmp)
        # Hide any pre-existing named namespaces: tests must never delete them.
        netns_root = root / 'netns'
        netns_root.mkdir()
        Path('/run/netns').mkdir(parents=True, exist_ok=True)
        cmd('mount', '--bind', str(netns_root), '/run/netns')
        cert, key = root / 'cert.pem', root / 'cert.key'
        cmd('openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-keyout', str(key),
            '-out', str(cert), '-days', '1', '-subj', '/CN=localhost')
        try:
            middle_pub, end_pub = make_wireguard(wg, root)
            for mode in ('reality-mux', 'reality', 'tls-mux'):
                reset_peers(wg, middle_pub, end_pub)
                run(mode, binary, wg, root, cert, key)
            for mode, rejection in (('reality-mux', 'bad-reality-password'), ('tls-mux', 'untrusted-tls'),
                                    ('tls-mux', 'bad-tls-secret')):
                reset_peers(wg, middle_pub, end_pub)
                run(mode, binary, wg, root, cert, key, rejection)
            if args.verified_cert:
                verified_cert = args.verified_cert.resolve(strict=True)
                verified_key = args.verified_key.resolve(strict=True)
                for mode in ('reality-mux', 'reality', 'tls-mux'):
                    reset_peers(wg, middle_pub, end_pub)
                    (root / mode).rename(root / (mode + '-unverified'))
                    run(mode, binary, wg, root, verified_cert, verified_key, verify_tls=True)
                reset_peers(wg, middle_pub, end_pub)
                run('tls-mux', binary, wg, root, verified_cert, verified_key,
                    rejection='wrong-hostname', verify_tls=True)
        finally:
            for name in ('ww-middle', 'ww-end'):
                subprocess.run(['ip', 'netns', 'del', name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


if __name__ == '__main__':
    main()
