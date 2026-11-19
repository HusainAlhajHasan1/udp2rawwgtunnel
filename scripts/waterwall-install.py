#!/usr/bin/env python3
"""Staged WaterWall installers: do not alter existing tunnel or host firewall."""
import getpass
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import urllib.request
import zipfile

from waterwall_config import ChainParameters, MODES, build_chains, build_core

VERSION = 'v1.5.42'
ARCHIVE = 'Waterwall-linux-gcc-x64-old-cpu.zip'
SHA256 = 'ce5b517fa144cccfb896b8d80404b020126e4df9d2def02ba05b54a35dd6aa42'
URL = f'https://github.com/radkesvat/WaterWall/releases/download/{VERSION}/{ARCHIVE}'
ROOT = Path('/etc/waterwall-wg')
LIB = Path('/usr/local/libexec/waterwall-wg')
BIN = LIB / 'Waterwall'
STATE = Path('/var/lib/waterwall-wg')
CLI = Path('/usr/local/sbin/tunnel')
UNIT = Path('/etc/systemd/system')
CERT = '/etc/letsencrypt/live/ws.hxmain.com/fullchain.pem'
KEY = '/etc/letsencrypt/live/ws.hxmain.com/privkey.pem'
PORTS = {'reality-mux': (40970, 18451), 'reality': (40971, 18452), 'tls-mux': (40972, 18453)}
SRC = Path(__file__).resolve().parent


def run(*args, check=True, timeout=30, data=None):
    return subprocess.run(args, input=data, check=check, timeout=timeout,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def insist(condition, message):
    if not condition:
        raise RuntimeError(message)


def write_new(path, data, mode=0o600):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
    with os.fdopen(fd, 'wb') as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())


def local_address_exists(ip):
    data = json.loads(run('ip', '-j', '-4', 'address', 'show').stdout)
    return any(a.get('local') == ip for iface in data for a in iface.get('addr_info', []))


def is_active(unit):
    return run('systemctl', 'is-active', '--quiet', unit, check=False).returncode == 0


def listening(proto, port):
    return bool(run('ss', '-H', '-l', '-n', '-' + proto, f'sport = :{port}').stdout.strip())


def check_cert():
    for path in (CERT, KEY):
        insist(Path(path).is_file(), f'missing TLS file: {path}')
    run('openssl', 'x509', '-in', CERT, '-noout', '-checkend', '86400')
    output = run('openssl', 'x509', '-in', CERT, '-noout', '-checkhost', 'ws.hxmain.com').stdout
    insist(b'does match' in output, 'certificate does not cover ws.hxmain.com')
    cert_pub = run('openssl', 'x509', '-in', CERT, '-pubkey', '-noout').stdout
    cert_der = run('openssl', 'pkey', '-pubin', '-outform', 'DER', data=cert_pub).stdout
    key_der = run('openssl', 'pkey', '-in', KEY, '-pubout', '-outform', 'DER').stdout
    insist(hashlib.sha256(cert_der).digest() == hashlib.sha256(key_der).digest(), 'certificate and key do not match')


def get_binary(dest):
    # Download to an isolated temp file first; no production state is modified.
    with urllib.request.urlopen(URL, timeout=30) as response, open(dest, 'wb') as f:
        total = 0
        while True:
            chunk = response.read(65536)
            if not chunk:
                break
            total += len(chunk)
            insist(total <= 20 * 1024 * 1024, 'release archive too large')
            f.write(chunk)
    digest = hashlib.sha256(Path(dest).read_bytes()).hexdigest()
    insist(digest == SHA256, 'release archive checksum mismatch')
    with zipfile.ZipFile(dest) as z:
        names = z.namelist()
        insist(names == ['Waterwall'], 'unexpected release archive layout')
        info = z.getinfo('Waterwall')
        insist(info.file_size < 40 * 1024 * 1024 and not stat.S_ISLNK(info.external_attr >> 16), 'invalid executable in archive')
        return z.read('Waterwall')


def unit_text(role, mode):
    directory = ROOT / mode
    return f'''[Unit]
Description=Isolated WaterWall WireGuard {role} {mode}
Wants=network-online.target
After=network-online.target
[Service]
Type=simple
WorkingDirectory={directory}
ExecStart={BIN} -c:core.json
Restart=on-failure
RestartSec=3
LimitNOFILE=524288
MemoryMax=192M
CPUQuota=50%
TasksMax=64
NoNewPrivileges=yes
CapabilityBoundingSet=
PrivateTmp=yes
ProtectSystem=strict
ProtectHome=yes
ProtectKernelTunables=yes
ProtectKernelModules=yes
ProtectControlGroups=yes
ReadWritePaths={directory / 'log'}
UMask=0077
[Install]
WantedBy=multi-user.target
'''


def install(role):
    insist(os.geteuid() == 0, 'run as root')
    insist(os.uname().machine == 'x86_64', 'only x86_64 is supported')
    meminfo = Path('/proc/meminfo').read_text()
    match = re.search(r'(?m)^MemAvailable:\s+(\d+)\s+kB$', meminfo)
    insist(match is not None and int(match.group(1)) >= 1024 * 1024,
           'less than 1 GiB available memory; refusing to add three services')
    for program in ('ip', 'ss', 'systemctl', 'systemd-run', 'wg', 'openssl', 'curl'):
        insist(shutil.which(program), f'missing executable: {program}')
    insist(not ROOT.exists() and not LIB.exists(), 'existing or partial WaterWall installation; inspect first')
    if role == 'middle':
        insist(not STATE.exists() and not CLI.exists() and not CLI.is_symlink() and not shutil.which('tunnel'),
               'existing state or tunnel command; refusing overwrite')
    address = '79.175.149.221' if role == 'middle' else '5.75.198.81'
    insist(local_address_exists(address), f'expected local IPv4 address {address}; wrong server?')
    insist(is_active('wg-quick@wgexit'), 'wgexit is not active')
    if role == 'middle':
        insist(is_active('vps-wstunnel-middle') and listening('u', 40962), 'existing wstunnel client unavailable')
    else:
        insist(is_active('vps-wstunnel-end') and listening('t', 8444), 'existing wstunnel TLS cover unavailable')
        insist(listening('u', 51901), 'WireGuard UDP port 51901 not listening')
        check_cert()
    for mode, (local_port, carrier_port) in PORTS.items():
        name = f'waterwall-wg-{role}-{mode}.service'
        insist(not (UNIT / name).exists(), f'unit already exists: {name}')
        insist(not listening('u' if role == 'middle' else 't', local_port if role == 'middle' else carrier_port),
               f'port already listening for {mode}')
    print('This stages separate WaterWall services. It does NOT switch WireGuard or modify the firewall.')
    print('Both servers need the SAME 32-character hex secret; generate one with: openssl rand -hex 16')
    secret = getpass.getpass('Shared NEW WaterWall secret (32 hex characters): ').strip().lower()
    confirm = getpass.getpass('Repeat secret: ').strip().lower()
    insist(secret == confirm and re.fullmatch(r'[0-9a-f]{32}', secret), 'secrets differ or are not 32 hex characters')
    with tempfile.TemporaryDirectory(prefix='ww-release-') as tmp:
        binary = get_binary(Path(tmp) / 'release.zip')
    configs = {}
    for mode, (local_port, carrier_port) in PORTS.items():
        salt = hashlib.sha256(('waterwall-v1:' + mode + ':' + secret).encode()).hexdigest()[:32]
        params = ChainParameters(mode=mode, middle_port=local_port, carrier_port=carrier_port,
            end_wireguard_port=51901, end_address='5.75.198.81', tls_name='ws.hxmain.com',
            certificate_path=CERT, certificate_key_path=KEY, secret=secret, salt=salt,
            cover_address='127.0.0.1', cover_port=8444)
        middle, end = build_chains(params)
        configs[mode] = middle if role == 'middle' else end
    # Refuse collisions again immediately before creating any files.
    insist(not ROOT.exists() and not LIB.exists(), 'installation path appeared during preflight')
    ROOT.mkdir(mode=0o700)
    LIB.mkdir(mode=0o755)
    write_new(BIN, binary, 0o755)
    for mode in MODES:
        directory = ROOT / mode
        directory.mkdir(mode=0o700)
        (directory / 'log').mkdir(mode=0o700)
        write_new(directory / 'core.json', json.dumps(build_core(), indent=2).encode() + b'\n')
        write_new(directory / 'config.json', json.dumps(configs[mode], indent=2).encode() + b'\n')
        write_new(UNIT / f'waterwall-wg-{role}-{mode}.service', unit_text(role, mode).encode(), 0o644)
    if role == 'middle':
        STATE.mkdir(mode=0o700)
        for name in ('waterwall_switch_transaction.py',):
            write_new(LIB / name, (SRC / name).read_bytes(), 0o644)
        write_new(CLI, (SRC / 'tunnel').read_bytes(), 0o755)
    run('systemctl', 'daemon-reload')
    units = [f'waterwall-wg-{role}-{mode}.service' for mode in MODES]
    for unit in units:
        run('systemctl', 'enable', '--now', unit, timeout=60)
        insist(is_active(unit), f'{unit} failed to start')
    print('Installed and enabled separate WaterWall services; WireGuard endpoint was NOT changed.')
    print('Services being active does not prove connectivity. Do not switch until explicitly tested.')
    print('No firewall rules or other services were changed. Restrict the new TCP ports to the middle IP using a reviewed firewall plan.')


if __name__ == '__main__':
    try:
        if len(sys.argv) != 2 or sys.argv[1] not in ('middle', 'end'):
            raise RuntimeError('usage: waterwall-install.py {middle|end}')
        install(sys.argv[1])
    except (Exception, KeyboardInterrupt) as e:
        print('WaterWall setup failed:', e, file=sys.stderr)
        print('Do not retry blindly if installation files were created; inspect partial state.', file=sys.stderr)
        sys.exit(1)
