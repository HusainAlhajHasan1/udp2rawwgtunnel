# WaterWall: feasibility and safety notes (not an installer)

Status: experimental implementation. Do not deploy to production without reviewing
WATERWALL-TRIAL.md and its explicit limitations; real systemd recovery and VPS
coexistence have not been tested.

## Requested transports

- `reality-mux`: WaterWall Reality + Mux, carrying WireGuard datagrams.
- `reality`: WaterWall Reality without Mux, carrying WireGuard datagrams.
- `tls-mux`: TLS 1.3 + Mux, carrying WireGuard datagrams.
- `wstunnel`: restore the currently working transport.

This must preserve the existing production services, including udp2raw-relay,
nginx, and the end host's other WireGuard installations. Never flush firewall
or routing tables. The current working WireGuard endpoint on the middle is
127.0.0.1:40962 and its saved config has been changed to match; preserve it.

## Upstream evidence

- Upstream WaterWall release v1.5.42 and source were reviewed. GitHub reports
  its `Waterwall-linux-gcc-x64-old-cpu.zip` release asset SHA-256 as
  `ce5b517fa144cccfb896b8d80404b020126e4df9d2def02ba05b54a35dd6aa42`.
  This is release metadata, not an independently verified downloaded asset.
- In that release, `tests/cases/mux_udp_boundaries_probe/config.json` combines
  UdpListener -> UdpOverTcpClient -> MuxClient -> TcpConnector, and the inverse
  chain ending in UdpConnector. The test does not validate the full requested
  Mux + TLS or Mux + Reality chain or a real WireGuard peer.
- Reality nodes in this WaterWall version use their own password/salt based
  protocol and a cover destination; do not assume Xray REALITY interoperability.
- TlsServer supports min-version and max-version; TlsClient's context allows
  TLS 1.2 through 1.3, so enforcing TLS 1.3 on the server requires validation
  of the actual handshake. Peer certificate validation must not be disabled.
- The TLS-only listener must also authenticate the remote peer or restrict the
  listener safely. TLS with only a server certificate does not authenticate the
  client. Do not expose an unauthenticated relay to UDP 51901 on the Internet.

## Required work before deployment

1. Pin a release and validate the executable checksum; inspect or build from
   source as appropriate. Test CPU compatibility and download availability.
2. Implement separate WireGuard UDP loopback listeners and isolated end VPS
   ports. Check for TCP and UDP conflicts, and account for existing firewall
   rules and provider firewall without changing them blindly. Preserve all
   unrelated units and firewall state.
3. Confirm exact release configuration and authentication for each full chain,
   including Reality cover destination, TLS verification and TLS 1.3. Test
   correct and incorrect credentials and peer certificate/hostname rejection.
4. Test all three transports end-to-end with a separate WireGuard pair and
   real traffic, multiple datagrams, both directions, MUX, process restart,
   reboot, packet loss and adverse connectivity. Never count service `active`
   or an old WireGuard handshake as proof of connectivity.
5. Design atomic, reversible switching with direct SSH/console access, an
   automatic rollback deadline and a validated fresh handshake and real
   client traffic before confirmation. Ensure rollback returns both the live
   endpoint and saved configuration to the previously verified mode. Preserve
   `wstunnel` on reboot. Do not expose credentials in shell history or logs.
6. Confirm what happens if policy routing rule priority 11046 is removed: it
   disappeared once while the oneshot network service remained active. Restore
   only the narrowly scoped rule after inspecting conflicts and the fail-closed
   path; do not globally reset routing or firewall state.

The downloaded working tree inspected was commit
`852580ada98c192dc291d1206c91ac57bdbdc707`; the version-specific checks above
used tag `v1.5.42`. Sources:
https://github.com/radkesvat/WaterWall
https://github.com/radkesvat/WaterWall/releases/tag/v1.5.42
https://github.com/radkesvat/WaterWall/blob/v1.5.42/tests/cases/mux_udp_boundaries_probe/config.json

## Local test progress

An experimental loopback probe is in `tests/waterwall_loopback.py`; it is a
**test only**, not either installer. On the Arena sandbox it exercised three
locally constructed chains against a locally built WaterWall v1.5.42 executable:
`reality-mux`, `reality`, and `tls-mux`. Three nonempty UDP datagrams (including
1280 bytes) and replies passed for each mode. The TLS server in the test was
configured to allow TLS 1.3 only. The test intentionally disables certificate
verification to use a throwaway self-signed certificate and MUST NOT be copied
into production. This does not establish peer authentication, certificate
validation, real WireGuard compatibility, robustness, speed, or suitability
under Iranian filtering. It does not test the requested switch command or the
production VPS topology.

A separate upstream `mux_udp_boundaries_probe` passed on loopback. Python syntax
and existing shell syntax checks passed. The v1.5.42 release download was blocked
by this sandbox's restricted access to release-assets.githubusercontent.com. A
local source build succeeded after substituting a Git clone of the pinned OpenSSL
source for the inaccessible release archive and disabling unneeded VMess and
QUIC components; it is not the official release artifact. Neither the resulting
binary nor its modifications were placed in this repository. A privileged Linux
network namespace and kernel WireGuard interface could be created locally. At
that initial probe, `wg` userspace tools were not yet available and no
WireGuard end-to-end test had been performed; see the subsequent test below.

A subsequent isolated test, `tests/waterwall_wireguard_netns.py`, used two
Linux network namespaces connected by veth, each with a kernel WireGuard
interface. For each of the three chains, it recreated the peers, established a
new WireGuard handshake, and received an ICMP reply through the tunnel. It also
confirmed that WireGuard did not connect during tests with a wrong Reality
password, a self-signed certificate with TLS verification enabled, and a wrong
TLS-mode inner EncryptionClient password. The TLS test includes a separate
shared-secret AEAD layer as well as TLS and MUX; the positive tests still use a
self-signed certificate and turn certificate verification off. That is for
local testing only, not an acceptable production setting. The encryption
component and its security properties have not been independently audited, and
rejecting wrong passwords is not a full security audit.

At this point in development no installer or switching implementation had been
tested. See the follow-up sections below for more recent work. Do not deploy
the test configuration on a VPS.

## Follow-up: certificate verification and shared configuration builder

`scripts/waterwall_config.py` now builds the middle/end node chains in memory;
it does not install, write secrets, start services, or switch traffic. The
loopback and namespace tests use this same builder. It defaults to TLS
verification on, validates basic inputs, binds ingress and WireGuard egress to
loopback, and enables TLS 1.3 only on the TLS server. TLS mode includes the
WaterWall authenticated-encryption layer inside TLS and MUX. The caller must
still generate strong secrets, avoid unsafe default ports, and secure storage.

To test certificate verification without Internet access, a one-day local CA
and a `localhost` leaf certificate with SAN were generated, and the local test
binary's bundled trust store was replaced with the test CA (never deploy that
binary). In isolated network namespaces, the TLS/MUX/WireGuard chain passed
with verification enabled and that CA-signed certificate. The same setup did
not connect when the trusted certificate's hostname did not match. Separate
negative tests rejected an unrelated self-signed certificate and wrong shared
secret. These are local functional tests, not an audit, not a test of a public
CA or the official release artifact, and not validation of the user's actual
certificate. Reality with certificate verification enabled is still untested.
The CA and key were ephemeral, not stored in the repository.

This section describes the state before the later switching/installer work below.

## Safety and switching development

WaterWall 1.5.42 defaults to splice and TCP tuning on Linux. Depending on
permissions and host configuration, startup can raise system-wide
`fs.pipe-user-pages-soft` and `net.ipv4.tcp_mem`; optional BBR tuning changes
additional system-wide settings. Such writes are unacceptable on the user's
end VPS without review. `build_core()` now explicitly sets `splice`, `tcp-tune`
and `try-enabling-bbr` to false. The functional UDP and namespace WireGuard
smoke tests were repeated using those settings, successfully. This reduces known
startup side effects; it is not proof that no other runtime effects are possible.

`scripts/waterwall_switch_transaction.py` contains only a backend-independent
switch state machine. The mock tests cover successful selection, failed
connectivity, errors after changing live/saved state, failed restoration,
partial timer setup, and failed timer cleanup. In particular a possibly active
timer's snapshot is retained on failure. That first state-machine test used only mocks. See the later implementation and
its limitations below.


## Experimental installers and switch implementation

`scripts/waterwall-middle.sh` and `scripts/waterwall-end.sh` call the shared
Python installer. It downloads a pinned archive with SHA-256 validation and
stages independent services. `scripts/tunnel` implements switch, saved config
persistence, source-routed HTTPS probe, lock, a 120-second systemd timer and
rollback. The tests exercise installer filesystem behavior and actual switch
backend logic using temporary files and mocked external commands. `systemd-analyze
verify` accepted the unit syntax. None of these results is an end-to-end systemd
run: this sandbox does not have a systemd manager. In particular process death,
timer firing, concurrent callers, power loss/reboot, unit hardening, actual
GitHub release artifact execution, firewall behavior, and coexistence on either
VPS are untested. The official release download is blocked by the sandbox's
network allowlist. No current user server was touched.

The verified local-CA experiment was later repeated with certificate checking
on for all three modes, including Reality over a local OpenSSL cover server;
the production binary's normal trust store was restored after the experiment.
It does not test the user's wstunnel server as the Reality cover.
