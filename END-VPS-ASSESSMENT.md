# End VPS assessment and migration constraints

This is an assessment of the supplied audit, not a deployable installer. No VPS
configuration has been changed. Addresses and states describe the snapshot, not
necessarily the current system.

## Intended topology

Dashboard clients → WireGuard on the middle VPS (79.175.149.221) → a separate
WireGuard tunnel carried by udp2raw → end VPS (5.75.198.81) → internet.
The dashboard should be served over HTTPS using a domain to be supplied during
installation. IPv4 client traffic must be routed through the end VPS; IPv6 must
be explicitly handled or blocked rather than allowed to leak around the tunnel.

## Existing end VPS services to preserve

- `wg1`, `wgrelay0`, `wgvpn0`, `udp2raw-relay`, and
  `wg-relay-firewall`, including the `inet wgrelay` nftables table.
- Docker's chains, nginx, Mattermost, SSH, PostgreSQL, and the existing
  dashboard. In particular, do not flush firewall rules or replace the
  existing global forwarding policy.
- The audited `udp2raw-end` listens on fake TCP port 2053 and forwards to
  `wg0` UDP port 51820. Both have active traffic in the snapshot. The
  WireGuard endpoint is localhost, so the audit does **not** establish the
  public address of the remote udp2raw client. Confirm ownership before
  retiring either service. Stopping them will disconnect existing users; the
  requested replacement cannot be guaranteed disruption-free for them.

## Safety requirements for an eventual installer

1. Inspect current ports, addresses, routes, rule priorities, firewall backend,
   existing unit files and running service ownership immediately before changes.
   Abort on conflicts; do not assume this old snapshot remains accurate.
2. Create a distinct tunnel and narrowly scoped forwarding/NAT rules. Existing
   source-address DROP rules and the independent `inet wgrelay` base chains
   must be considered together; an accept in one nftables base chain does not
   override a drop in another. Preserve SSH and all unrelated services.
3. Stage the new service separately, verify the WireGuard handshake and actual
   client internet access in both directions, and test reboot persistence and
   failure behavior before any retirement. A UDP2raw fake-TCP listener may not
   behave like a conventional TCP service in ordinary connectivity tests.
4. Route only the middle dashboard's client subnet into the new tunnel. Use a
   fail-closed policy so a tunnel failure does not silently send client traffic
   directly through the middle VPS. Avoid changing the middle VPS's default
   route for SSH or its public WireGuard transport.
5. Ask explicitly before stopping/disabling only `wg-quick@wg0` and
   `udp2raw-end`; retain recoverable backups and a tested rollback. Do not
   delete `/usr/local/bin/udp2raw`: `udp2raw-relay` also uses it. Stopping
   `wg0` may execute its `PostDown` commands, so review its configuration
   first; those commands could alter shared rules.
6. The supplied audit exposes an udp2raw shared key in a process command line.
   Treat it as compromised and rotate it with its peer. Do not store keys in
   this repository or paste private keys into chat. udp2raw's transport options
   shown in the audit are not a substitute for WireGuard encryption.
7. The middle dashboard should bind to localhost behind a TLS reverse proxy;
   the DNS name must point to the middle VPS and ports 80/443 must be reachable
   for ordinary HTTP-based certificate issuance. Securely provision the admin
   password and avoid printing it.

## Limitations

No installer has yet been validated against either live machine. A script cannot
promise that unrelated production services will never be disrupted; changes to
shared networking always carry risk. Keep an independent provider console and
recent backups available for any cutover.
