#!/usr/bin/env bash
set -Eeuo pipefail
source "$(dirname "$(readlink -f "$0")")/common.sh"
root
dashboard_install(){
  local work
  work=$(mktemp -d)
  git clone https://github.com/HusainAlhajHasan1/wireguard-dashboard.git "$work/dashboard"
  git -C "$work/dashboard" checkout --detach "$DASH_COMMIT"
  # Upstream invokes python -m wgdash from the caller's working directory.
  # Point it at the installed package; omit its plaintext password summary.
  PYTHONPATH=/opt/wireguard-dashboard WGDASH_ADMIN_PASS="$adminpass" \
    bash "$work/dashboard/install.sh" --unattended --no-caddy --bind 127.0.0.1 --port 3476 --endpoint 79.175.149.221:51820 | sed '/^[[:space:]]*Password:/d'
  unset adminpass
  # Upstream's CLI wrapper has the same working-directory dependency.
  cat > /usr/local/bin/wgdash <<'EOC'
#!/bin/sh
PYTHONPATH=/opt/wireguard-dashboard${PYTHONPATH:+:$PYTHONPATH}
export PYTHONPATH
exec /opt/wireguard-dashboard/venv/bin/python -m wgdash.cli --config /etc/wireguard-dashboard/config.json "$@"
EOC
  chmod 755 /usr/local/bin/wgdash
  /usr/local/bin/wgdash set-setting default_allowed_ips '0.0.0.0/0'
  /usr/local/bin/wgdash set-setting default_mtu 1200
  systemctl is-active --quiet wireguard-dashboard.service || die 'Dashboard service failed; inspect journalctl -u wireguard-dashboard'
  rm -rf "$work"
  echo 'Dashboard is bound to 127.0.0.1:3476. Access it through SSH, not public HTTP.'
  echo 'IPv6 on client devices must be disabled or separately secured; this setup provides IPv4 only.'
}
read_dashboard_password(){
  read -rs -p 'Dashboard admin password (at least 10 alphanumeric characters): ' adminpass; echo
  [[ $adminpass =~ ^[A-Za-z0-9]{10,128}$ ]] || die 'Use 10-128 alphanumeric characters for dashboard password'
}
case "${1:-}" in
 key)
  apt-get update; apt-get install -y wireguard-tools; key_init; exit;;
 install)
  [[ -s /etc/udp2raw-wg/tunnel.key ]] || die 'First run middle-vps.sh key'
  for f in /etc/wireguard/wg0.conf /etc/wireguard/wgexit.conf /etc/wireguard/privatekey /etc/wireguard/publickey /etc/systemd/system/vps-tunnel-middle.service /etc/systemd/system/wireguard-dashboard.service /opt/wireguard-dashboard; do [[ ! -e $f ]] || die "$f already exists; refusing overwrite"; done
  for i in wg0 wgexit; do ip link show "$i" >/dev/null 2>&1 && die "$i already exists"; done
  check_port u 51820; check_port u 40961; check_port t 3476
  [[ -z $(ip -4 rule show | grep '^11046:') ]] || die 'Policy rule priority 11046 already used'
  [[ -z $(ip -4 route show table 10746 2>/dev/null) ]] || die 'Routing table 10746 already used'
  ip -4 route show table all | grep -Eq '(^| )10\.(89\.0|199\.74)\.' && die 'Tunnel address ranges conflict with existing routes'
  read_inputs
  read_dashboard_password
  confirm 'This installs WireGuard, udp2raw, firewall rules, policy routing and the dashboard on this VPS.'
  install_deps; build_udp
  wg genkey > /etc/udp2raw-wg/dashboard.key
  cp /etc/udp2raw-wg/dashboard.key /etc/wireguard/privatekey
  wg pubkey < /etc/wireguard/privatekey > /etc/wireguard/publickey
  cat > /etc/wireguard/wg0.conf <<EOC
[Interface]
Address = 10.89.0.1/24
ListenPort = 51820
PrivateKey = $(cat /etc/udp2raw-wg/dashboard.key)
MTU = 1280
EOC
  cat > /etc/wireguard/wgexit.conf <<EOC
[Interface]
Address = 10.199.74.2/30
PrivateKey = $(cat /etc/udp2raw-wg/tunnel.key)
MTU = 1280
Table = off
PostUp = ip -4 route replace table 10746 default dev wgexit metric 10
PostDown = ip -4 route del table 10746 default dev wgexit metric 10
[Peer]
PublicKey = $peer
AllowedIPs = 0.0.0.0/0
Endpoint = 127.0.0.1:40961
PersistentKeepalive = 25
EOC
  cat > /etc/udp2raw-wg/middle.conf <<EOC
-c
-l 127.0.0.1:40961
-r 5.75.198.81:2054
-k $pass
--raw-mode faketcp
--cipher-mode aes128cbc
--auth-mode hmac_sha1
-a
EOC
  unset pass
  cat > /usr/local/libexec/vps-tunnel-middle-network <<'EOC'
#!/bin/bash
set -e
# A persistent unreachable fallback prevents a policy lookup from falling back to main.
ip -4 route replace table 10746 unreachable default metric 32760
ip -4 rule show | grep -q '^11046:.*from 10.89.0.0/24 lookup 10746' || ip -4 rule add priority 11046 from 10.89.0.0/24 lookup 10746
ipt() { iptables -C "$@" 2>/dev/null || iptables -I "$@"; }
# Insert DROP first, then the narrow accepts above it.
ipt FORWARD -i wg0 -j DROP
ipt FORWARD -i wg0 -o wgexit -s 10.89.0.0/24 -j ACCEPT
ipt FORWARD -i wgexit -o wg0 -d 10.89.0.0/24 -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT
ip6tables -C FORWARD -i wg0 -j DROP 2>/dev/null || ip6tables -I FORWARD -i wg0 -j DROP
EOC
  chmod 700 /usr/local/libexec/vps-tunnel-middle-network
  cat > /etc/systemd/system/vps-tunnel-middle-network.service <<'EOC'
[Unit]
Description=Fail-closed forwarding for VPS tunnel
After=network-online.target
Before=wg-quick@wg0.service wg-quick@wgexit.service
[Service]
Type=oneshot
ExecStart=/usr/local/libexec/vps-tunnel-middle-network
RemainAfterExit=yes
[Install]
WantedBy=multi-user.target
EOC
  for iface in wg0 wgexit; do
   mkdir -p "/etc/systemd/system/wg-quick@$iface.service.d"
   cat > "/etc/systemd/system/wg-quick@$iface.service.d/vps-tunnel.conf" <<'EOC'
[Unit]
Requires=vps-tunnel-middle-network.service
After=vps-tunnel-middle-network.service
EOC
  done
  cat > /etc/systemd/system/vps-tunnel-middle.service <<'EOC'
[Unit]
Description=Isolated udp2raw middle VPS tunnel
After=network-online.target wg-quick@wgexit.service
Wants=network-online.target
Requires=wg-quick@wgexit.service
[Service]
ExecStart=/usr/local/libexec/vps-tunnel-udp2raw --conf-file /etc/udp2raw-wg/middle.conf
Restart=on-failure
RestartSec=3
[Install]
WantedBy=multi-user.target
EOC
  echo 'net.ipv4.ip_forward=1' > /etc/sysctl.d/90-vps-tunnel.conf
  sysctl -w net.ipv4.ip_forward=1
  systemctl daemon-reload
  systemctl enable --now vps-tunnel-middle-network wg-quick@wgexit vps-tunnel-middle wg-quick@wg0
  dashboard_install;;
 finish-dashboard)
  [[ -s /etc/wireguard/wg0.conf && -s /etc/wireguard/wgexit.conf && -f /etc/systemd/system/vps-tunnel-middle.service ]] || die 'Expected existing middle tunnel not found; inspect manually'
  [[ -d /opt/wireguard-dashboard && -f /etc/wireguard-dashboard/config.json ]] || die 'Expected partial dashboard install not found; inspect manually'
  [[ ! -e /var/lib/wireguard-dashboard/wgdash.db && ! -e /etc/systemd/system/wireguard-dashboard.service ]] || die 'Existing dashboard database or service found; refusing to overwrite'
  check_port t 3476
  read_dashboard_password
  confirm 'Finish the partial dashboard installation without changing the tunnel?'
  dashboard_install;;
 *) echo "Usage: $0 {key|install|finish-dashboard}"; exit 2;;
esac
