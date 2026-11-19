# Experimental WaterWall setup — read before use

**Not production-validated.** The current VPS services and WireGuard endpoint
are not modified by the setup scripts, but adding three live processes and TCP
listeners still carries availability and security risk. There is no way to
promise that starting software on a shared VPS cannot affect other services.
Do not run on the end VPS without a maintenance window, direct SSH and provider
console access, independent backups, and explicit acceptance of that risk.
No live VPS has been tested or modified by this implementation.

## Files and fixed topology

- End: `scripts/waterwall-end.sh` -> shared `scripts/waterwall-install.py end`.
- Middle: `scripts/waterwall-middle.sh` -> shared installer `middle`.
- Both require the companion scripts from the same checkout; do not download
  just the two wrapper files. They install independent v1.5.42 processes.
- The end's three new TCP listeners are 18451, 18452 and 18453. The middle's
  loopback-only UDP listeners are 40970, 40971 and 40972. They target the
  existing end WireGuard socket 127.0.0.1:51901. No existing listener, nginx,
  WireGuard device, udp2raw instance, NAT, or firewall is intentionally changed.
- Reality uses the existing `vps-wstunnel-end` TLS server on localhost TCP
  8444 as its cover. It requires the `ws.hxmain.com` certificate and the
  wstunnel end service to remain functional. Its REALITY protocol is WaterWall
  v2, not necessarily interoperable with Xray REALITY.
- TLS mode uses TLS 1.3 plus a shared-secret authenticated encryption layer
  inside the TLS carrier. It does not provide TLS client certificates. Limit
  access to the new TCP ports to 79.175.149.221 using a separately reviewed
  host/provider firewall change. Installer does not open or alter firewall rules.
- TLS client verifies `ws.hxmain.com`. Certbot's manual certificate expires on
  2027-02-14 unless renewed; renewal must be followed by an agreed restart of
  the WaterWall TLS service and the cover wstunnel service to load the new
  certificate, which can interrupt tunnels. Keep certificate renewal planned.
- Each unit is capped at 192 MiB and 50% of one CPU (these limits may reduce
  measured throughput). Core host-wide BBR, TCP tuning and splice are disabled.
  Installation refuses to start with less than 1 GiB MemAvailable, but this is
  not a guarantee that other services won't be affected.

## Preflight and installation (only after approval)

Confirm direct out-of-band access, port availability, firewall policy, public
DNS and server TLS certificate first. Confirm GitHub release downloads can
reach release-assets.githubusercontent.com on both VPSs. An unsuccessful or
partial installation is intentionally not overwritten: inspect created files
and units before planning recovery. Never flush iptables/nftables.

Create one new **32 hex character** shared secret with `openssl rand -hex 16`.
Enter it twice on each VPS when prompted; do not paste it into chat or use the
existing wstunnel or udp2raw secrets. It is stored in root-only configuration
files and never intentionally displayed. No installer moves or stops the
existing services. The installers enable and start three *new* services, but
**do not switch the existing live or saved endpoint**.

From this checkout on the end VPS:

```bash
sudo bash scripts/waterwall-end.sh
```

From the same branch/commit on the middle VPS:

```bash
sudo bash scripts/waterwall-middle.sh
```

The installer refuses occupied expected ports, existing installation paths or
units, wrong host address, inactive existing `wgexit`/wstunnel, missing TLS
files, or download checksum mismatch. It checks service liveness only, **not**
TLS or WireGuard end-to-end. Service liveness is not proof the new tunnel works.
Back up important configuration outside this installer and maintain direct SSH.

## Switching, recovery and limitations

Once both sides are installed and checked, on the middle VPS only:

```bash
sudo tunnel status
sudo tunnel switch reality-mux
sudo tunnel switch reality
sudo tunnel switch tls-mux
sudo tunnel switch wstunnel
```

These commands can interrupt all existing VPN clients. The switcher requires
one `wgexit` peer, a saved endpoint matching the current live endpoint, an active
new service and wstunnel backup, `SaveConfig` not true, and a source policy
route to `1.1.1.1` via `wgexit`. It arms a 120-second systemd recovery timer,
changes the live endpoint, probes HTTPS to `https://1.1.1.1/cdn-cgi/trace` using
source 10.89.0.1, checks both WG transfer counters increased, then writes the
selected endpoint to `wgexit.conf` and cancels the timer. An unavailable probe
can cause a safe rollback even when a tunnel otherwise works. A successful
probe tests this one destination, not every client or the whole path. Existing
UDP flows and long-lived connections can interrupt when switching.

A failed health check restores the previous live and saved endpoint. The timer
is intended to recover from process termination if the switch stalls. `sudo
tunnel status` shows whether recovery is pending; `sudo tunnel rollback`
requests immediate restoration of the pending transaction. Do not edit wgexit's
peer or endpoint while a switch is in progress: the rollback deliberately
refuses to overwrite unexpected changes. If rollback itself fails, keep the
console session and inspect the failure; a timer is not a guarantee of recovery.
A reboot before a successful commit should load the previous saved config, but
power loss and systemd races have not been tested. A restart of wgexit during a
switch can invalidate assumptions; do not do it.

The services are enabled at boot. The old wstunnel services and udp2raw services
remain installed and are not stopped. Selection persists only after the check
succeeds; `tunnel switch wstunnel` selects the existing WebSocket endpoint.
Backup configuration and WireGuard private keys are sensitive: never paste
files or verbose service logs into chat.

## What was actually tested

- WaterWall 1.5.42 was built locally from source (not the pinned release archive).
  In two isolated Linux namespaces with separate kernel WireGuard peers, all
  three generated chains passed handshakes and ping. A locally injected test CA
  was used to verify certificate acceptance, wrong hostname rejection, and bad
  secret/untrusted certificate rejection. This is not an independent audit.
- Installer and switch backend were tested with mocked host commands and temp
  files, including collisions and simulated timer recovery. Unit syntax passed
  `systemd-analyze verify`.
- No real systemd manager is available in the sandbox. The actual timer, process
  kill, reboot, concurrency, restrictive unit settings and real download were
  **not** tested. The user's wstunnel server as Reality cover, actual cert,
  end-VPS firewall and services, and speed in Iran were **not** tested.
  Do not represent this as production-proven or use as sole recovery path.
