# WireGuard over udp2raw between two VPSs

These scripts stage a **new** channel alongside the end VPS's existing
services. Dashboard client IPv4 traffic goes from the middle VPS to the end VPS
and exits through the end VPS. The middle VPS's own default route remains
unchanged. The configuration is intended for the audited Ubuntu 22.04 hosts:

| | Middle VPS | End VPS |
|---|---|---|
| Public address | 79.175.149.221 | 5.75.198.81 |
| Tunnel WG | `wgexit`, 10.199.74.2/30 | `wgexit`, 10.199.74.1/30 |
| Dashboard clients | `wg0`, 10.89.0.0/24, UDP 51820 | — |
| udp2raw | client, localhost UDP 40961 | fake TCP 2054 → localhost UDP 51901 |
| Dashboard | localhost TCP 3476 | existing dashboard untouched |

**Important:** This is an untested production migration, not a guarantee of
zero disruption. No changes have been made to either VPS by the author. Test
with a provider console available and take backups first. Do not run on an
existing installation without reviewing the scripts and current networking.
An interrupted installation can leave partially installed files/services; the
scripts deliberately refuse to overwrite them. Inspect and recover manually
rather than deleting files blindly. The upstream installers and package
managers also make system changes. Read `END-VPS-ASSESSMENT.md`.

The scripts clone specific source commits of udp2raw and the dashboard and
install packages from the servers' configured apt/Python repositories. Review
these upstream sources and ensure the servers can access them. udp2raw requires
root/raw sockets and uses `-a` to manage its own firewall rule. This is WireGuard
encryption carried inside udp2raw fake TCP, **not a promise of invisibility,
maximum speed, or immunity to filtering**.

## Prepare

1. Save current VPS backups and verify you can use your provider's out-of-band
   console. Confirm both VPSs have enough space and that the listed interfaces,
   ports, IP subnets, policy rule priority 11046 and routing table 10746 are free.
2. Allow UDP 51820 inbound to the middle VPS, and fake TCP 2054 inbound to the
   end VPS, in any **provider** firewall and host firewall. Fake TCP may not
   pass a firewall that requires a normal TCP connection. Do not flush or replace
   any existing firewall. The end's `wgexit` listens on UDP 51901; restrict
   public access to it in your existing firewall if desired. Do not block the
   localhost traffic from udp2raw.
3. Copy this repository to **each VPS**, for example:
   ```bash
   git clone --branch arena/dbe632b6-udp2rawwgtunnel https://github.com/HusainAlhajHasan1/udp2rawwgtunnel.git
   cd udp2rawwgtunnel
   ```
   This explicitly selects the branch containing these scripts. If the repository
   is private, GitHub access must already be configured on each VPS.
4. Generate a fresh random alphanumeric udp2raw password, 24–64 characters,
   and store it securely. Enter the **same** password on both machines when
   prompted; never put it in chat or a Git repository. WireGuard private keys
   remain on their respective servers. The udp2raw key in the original audit
   was exposed and should be rotated on its existing peers.

## Install

Run as root from the repository on **each server**:

```bash
# On end VPS
sudo bash scripts/end-vps.sh key
# On middle VPS
sudo bash scripts/middle-vps.sh key
```

Copy each printed **public** key to the other VPS. Do not copy the private key.
Then run:

```bash
# On end VPS: enter the middle VPS's public key and shared udp2raw password
sudo bash scripts/end-vps.sh install

# On middle VPS: enter the end VPS's public key, the same udp2raw password,
# and a new dashboard admin password (alphanumeric, at least 10 characters)
sudo bash scripts/middle-vps.sh install
```

The dashboard runs on `127.0.0.1:3476`, **not** publicly at the IP address.
An IP and port are not a domain and do not supply HTTPS. From your own computer:

```bash
ssh -L 3476:127.0.0.1:3476 root@79.175.149.221
```

Keep SSH open and visit `http://127.0.0.1:3476` locally. Sign in as `admin`
with the password entered at install. To access it publicly, obtain a domain
and set up a properly secured TLS reverse proxy separately; don't expose plain
HTTP or simply forward port 3476 to the internet. Create a test peer using the
dashboard; client configs use `79.175.149.221:51820`.

## Verify before retiring the old service

On both VPSs check `systemctl status wg-quick@wgexit` and `sudo wg show wgexit`.
A recent handshake is necessary but not sufficient: connect a test client,
check browsing/DNS and verify its public IPv4 is **5.75.198.81**. Test unrelated
services and SSH. Temporarily stop `vps-tunnel-middle` and verify the client
cannot reach the internet, then restart it and retest. Check boot persistence
in a planned maintenance window. Firewall/provider restrictions, other rules,
MTU and network filtering can require site-specific troubleshooting.

Only IPv4 is routed. The generated client configuration routes IPv4 only; it
**does not prevent the client device from using its normal IPv6 connection**.
Disable IPv6 on each client or configure a separately verified IPv6 tunnel and
kill switch to prevent IPv6 leaks. Custom dashboard peer routes can bypass the
VPN. No leak protection is promised on the client device.

Once all checks pass, on the end VPS only:

```bash
sudo bash scripts/end-vps.sh retire-old
```

This checks for a recent new handshake, verifies `wg0` hooks match the supplied
snapshot, saves a local backup and asks for explicit confirmation before stopping
and disabling **only** `udp2raw-end` and `wg-quick@wg0`. Their old users will
be disconnected. They are not deleted so they can be rolled back with:

```bash
sudo systemctl enable --now wg-quick@wg0 udp2raw-end
```

The old `wg0` PostDown removes one broad `eth0` masquerade rule; its PostUp adds
one back on rollback. Shared rules and their ordering must be checked manually.
Do not restore a full iptables snapshot blindly: it can disrupt Docker, udp2raw,
SSH and other live services. `udp2raw-relay`, `wgrelay0`, `wg1` and `wgvpn0`
are not intentionally stopped. If anything unexpected happens, stop and use
the provider console to diagnose rather than repeatedly running the installer.

### Recovery from the dashboard Python import error

If the middle installer stopped at `ModuleNotFoundError: No module named
'wgdash'` immediately after writing the dashboard configuration, update this
branch and run `bash scripts/middle-vps.sh finish-dashboard` on the middle VPS.
This guarded recovery is only for that partial installation: it refuses to run
if a dashboard database or service already exists, and it does not rebuild or
restart the tunnel. Enter a dashboard admin password when prompted. If the guard
refuses, inspect the existing dashboard state instead of deleting its data.
