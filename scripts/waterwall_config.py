"""WaterWall v1.5.42 chain builder. No files, services or networking are modified.

This is an internal component, not an installer. The full generated production
configuration must be tested and security-reviewed before deployment. In
particular TLS server authentication here relies on the WaterWall Encryption
nodes in addition to WireGuard, not on a client TLS certificate.
"""
from dataclasses import dataclass
import ipaddress
import re

MODES = ('reality-mux', 'reality', 'tls-mux')
_DNS = re.compile(r'^(?=.{1,253}$)(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)*[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?$')


@dataclass(frozen=True)
class ChainParameters:
    mode: str
    middle_port: int
    carrier_port: int
    end_wireguard_port: int
    end_address: str
    tls_name: str
    certificate_path: str
    certificate_key_path: str
    secret: str
    salt: str
    cover_address: str
    cover_port: int
    # For isolated tests only. Normal use must leave this True.
    verify_tls: bool = True


def build_core(config_path='config.json'):
    """Conservative core defaults; do not enable host-wide tuning or splice.

    Config paths are filenames controlled by the installer; no secrets here.
    """
    if not re.fullmatch(r'[A-Za-z0-9._-]+\.json', config_path) or config_path in ('.json', '..json'):
        raise ValueError('invalid configuration filename')
    return {
        'configs': [config_path],
        'misc': {'workers': 1, 'ram-profile': 'client', 'mtu': 1500,
                 'splice': False, 'tcp-tune': False, 'try-enabling-bbr': False},
        'log': {'path': 'log/', **{
            name: {'loglevel': 'ERROR', 'file': name + '.log', 'console': True}
            for name in ('internal', 'core', 'network', 'dns')}},
    }


def _node(name, kind, settings=None, next_name=None):
    result = {'name': name, 'type': kind, 'settings': settings or {}}
    if next_name:
        result['next'] = next_name
    return result


def build_chains(p: ChainParameters):
    """Return middle and end chain dictionaries; caller owns secure storage."""
    if p.mode not in MODES:
        raise ValueError('unsupported transport')
    for port in (p.middle_port, p.carrier_port, p.end_wireguard_port, p.cover_port):
        if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
            raise ValueError('invalid port')
    ipaddress.IPv4Address(p.end_address)
    ipaddress.IPv4Address(p.cover_address)
    if not _DNS.fullmatch(p.tls_name):
        raise ValueError('invalid TLS name')
    if not p.certificate_path.startswith('/') or not p.certificate_key_path.startswith('/'):
        raise ValueError('certificate paths must be absolute')
    for value in (p.secret, p.salt):
        if not re.fullmatch(r'[A-Za-z0-9]{16,32}', value):
            raise ValueError('secret and salt must each contain 16-32 alphanumeric characters')
    if not isinstance(p.verify_tls, bool):
        raise ValueError('verify_tls must be a bool')
    reality = p.mode.startswith('reality')
    mux = p.mode != 'reality'
    settings = {'password': p.secret, 'salt': p.salt, 'algorithm': 'chacha20-poly1305',
                'kdf-iterations': 12000}
    client = [
        _node('ingress', 'UdpListener', {'address': '127.0.0.1', 'port': p.middle_port}, 'uot-c'),
        _node('uot-c', 'UdpOverTcpClient', next_name='mux-c' if mux else 'protect-c'),
    ]
    if mux:
        client.append(_node('mux-c', 'MuxClient', {'mode': 'fixed-connections-count',
                                                    'per-worker-connections-count': 1},
                            'protect-c' if reality else 'auth-c'))
    if reality:
        client.append(_node('protect-c', 'RealityClient', dict(settings, sni=p.tls_name, verify=p.verify_tls), 'out'))
    else:
        client.extend([
            _node('auth-c', 'EncryptionClient', settings.copy(), 'protect-c'),
            _node('protect-c', 'TlsClient', {'sni': p.tls_name, 'verify': p.verify_tls}, 'out'),
        ])
    client.append(_node('out', 'TcpConnector', {'address': p.end_address, 'port': p.carrier_port,
                                                'nodelay': True, 'fastopen': False}))
    server = [_node('in', 'TcpListener', {'address': p.end_address, 'port': p.carrier_port,
                                          'nodelay': True}, 'protect-s')]
    if reality:
        server.extend([
            _node('protect-s', 'RealityServer', dict(settings, destination='cover',
                                                       **{'sniffing-attempts': 8}), 'mux-s' if mux else 'uot-s'),
            _node('cover', 'TcpConnector', {'address': p.cover_address, 'port': p.cover_port,
                                            'nodelay': True, 'fastopen': False}),
        ])
    else:
        server.extend([
            _node('protect-s', 'TlsServer', {'cert-file': p.certificate_path, 'key-file': p.certificate_key_path,
                                             'sni': p.tls_name, 'min-version': '1.3', 'max-version': '1.3'}, 'auth-s'),
            _node('auth-s', 'EncryptionServer', settings.copy(), 'mux-s'),
        ])
    if mux:
        server.append(_node('mux-s', 'MuxServer', next_name='uot-s'))
    server.extend([
        _node('uot-s', 'UdpOverTcpServer', next_name='service'),
        _node('service', 'UdpConnector', {'address': '127.0.0.1', 'port': p.end_wireguard_port}),
    ])
    return ({'name': p.mode + '-middle', 'nodes': client},
            {'name': p.mode + '-end', 'nodes': server})
