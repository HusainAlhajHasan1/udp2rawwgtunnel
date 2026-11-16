# Temporary WebSocket transport trial

This is a **staged, untested** alternative transport for the existing wgexit
peer. It keeps the old udp2raw processes and WireGuard configuration in place.
The trial switches the existing WireGuard peer's live endpoint: it is **not** an
independent second WireGuard interface, so all current dashboard clients can
briefly lose connectivity during switching or when the trial fails. The two
transports cannot carry the same peer concurrently. No speed improvement or
resistance to filtering is guaranteed. TCP-over-TCP can perform poorly under
loss. Keep an SSH session to the middle VPS over its public IP and an
out-of-band console open. Do not perform during critical use.

## Prerequisites

- A DNS name resolving **directly and exclusively** to `5.75.198.81` over IPv4
  on both VPSs. If Cloudflare manages it, use a DNS-only record; proxying is
  not supported for this port. The installer aborts if its IPv4 lookup doesn't
  match. Do not change the existing `hxmain.com` record blindly.
- An existing unexpired, publicly trusted certificate covering that name and
  matching private key on the end VPS. Full chain and private key are required.
  Use an existing certificate only if it covers the chosen name. Certificate
  provisioning and renewal are **not** handled by this script; don't edit nginx
  or Certbot configuration without a separate plan. Certificate verification
  is enabled on the client and must not be bypassed.
- TCP 8444 available and reachable on the end VPS, and localhost UDP 40962 free
  on the middle VPS. Provider/host firewall changes are not made by this script.
  When permitting TCP 8444, restrict ingress to middle VPS `79.175.149.221`
  where possible; do not alter existing firewall rules without review.
- GitHub release assets accessible from both VPSs. The installer downloads the
  upstream x86-64 release v11.1.0 and checks the pinned SHA-256. It creates a
  separate binary, root-only secret files, and systemd units. A random path
  prefix and destination restriction reduce exposure, but the prefix is **not a
  substitute for strong client authentication** and could be visible to local
  privileged users or logs. WireGuard provides its own authentication.
- Existing wgexit and udp2raw tunnel functioning. No full NAT, firewall,
  service or WireGuard config is replaced. New services are started but **not
  enabled** on reboot. On a normal reboot the unchanged WireGuard configuration
  and udp2raw service should restore the original transport; verify it.

## Setup

Fetch the current branch on both VPSs. Create a **new** shared random secret on
one VPS with `openssl rand -hex 24` and securely copy it to the other; do not
paste it into chat or put it in git. Each script prompts for the secret without
echoing it. Use the same secret on both, distinct from the udp2raw password.

On the end VPS:

```bash
sudo bash scripts/wstunnel-trial.sh setup-end
```

Enter the DNS name, absolute paths to the existing full chain and private key,
and the shared secret. This starts a restricted server on `5.75.198.81:8444`.
It checks certificate expiry, hostname and matching key, but the client will
validate the certificate trust chain when connecting.

On the middle VPS:

```bash
sudo bash scripts/wstunnel-trial.sh setup-middle
```

Enter the same domain and secret. This starts a client bound to
`127.0.0.1:40962`; it does **not** change the WireGuard endpoint yet. UDP 40962
is carried over TLS WebSockets to the end's `127.0.0.1:51901` WireGuard port.
Start of the client service alone doesn't prove end-to-end TLS or connectivity.

## Switch and rollback

On the middle VPS, only during an agreed maintenance window:

```bash
sudo bash scripts/wstunnel-trial.sh switch
```

The command requires exactly one wgexit peer and the original udp2raw endpoint,
then asks for confirmation. It changes only the **live** wgexit endpoint.
`vps-tunnel-middle` stays running. Wait for a **new** WireGuard handshake and
verify real client internet access. Service liveness or a pre-switch handshake
alone is not enough. If not working, restore immediately:

```bash
sudo bash scripts/wstunnel-trial.sh restore
```

The restore command checks that the original udp2raw client is active and
returns the live endpoint to `127.0.0.1:40961`. Check a new handshake and client
connectivity. There is no automatic timed rollback: keep direct SSH/console
access available. If the trial crashes or TLS fails, it can interrupt clients
until restored. The saved `wgexit.conf` is never edited.

After restoring, stop the optional trial services separately:

```bash
# Middle VPS
sudo systemctl stop vps-wstunnel-middle
# End VPS
sudo systemctl stop vps-wstunnel-end
```

This leaves files and units in place for inspection and does not uninstall or
modify existing services. Do not stop `vps-tunnel-middle` or
`udp2raw-relay` when stopping the trial. If a partial setup fails, the installer
intentionally refuses to overwrite its files; inspect manually rather than
deleting them blindly. Commands and logs may contain path secrets; redact them
before sharing.
