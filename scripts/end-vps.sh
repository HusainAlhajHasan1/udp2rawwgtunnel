#!/usr/bin/env bash
set -Eeuo pipefail
source "$(dirname "$(readlink -f "$0")")/common.sh"
root
case "${1:-}" in
 key)
  apt-get update; apt-get install -y wireguard-tools; key_init; exit;;
 install)
  [[ -s /etc/udp2raw-wg/tunnel.key ]] || die 'First run end-vps.sh key'
  [[ ! -e /etc/wireguard/wgexit.conf && ! -e /etc/systemd/system/vps-tunnel-end.service ]] || die 'Existing tunnel configuration found; refusing overwrite'
  ip link show wgexit >/dev/null 2>&1 && die 'wgexit already exists'
  check_port t 2054; check_port u 51901
  [[ $(ip -4 route get 1.1.1.1 | awk '{for(i=1;i<=NF;i++) if($i=="dev") {print $(i+1); exit}}') == eth0 ]] || die 'Default outbound interface is not eth0; review before continuing'
  read_inputs; confirm 'This installs a new tunnel and narrowly scoped firewall rules. Existing services are left running.'
  install_deps; build_udp
  cat > /etc/wireguard/wgexit.conf <<EOC
[Interface]
Address = 10.199.74.1/30
ListenPort = 51901
PrivateKey = $(cat /etc/udp2raw-wg/tunnel.key)
MTU = 1280
[Peer]
PublicKey = $peer
AllowedIPs = 10.199.74.2/32, 10.89.0.0/24
EOC
  cat > /etc/udp2raw-wg/end.conf <<EOC
-s
-l 0.0.0.0:2054
-r 127.0.0.1:51901
-k $pass
--raw-mode faketcp
--cipher-mode aes128cbc
--auth-mode hmac_sha1
-a
EOC
  unset pass
  cat > /usr/local/libexec/vps-tunnel-end-firewall <<'EOC'
#!/bin/bash
set -e
ipt() { iptables -C "$@" 2>/dev/null || iptables -I "$@"; }
ipt FORWARD -i wgexit -o eth0 -s 10.89.0.0/24 -j ACCEPT
ipt FORWARD -i eth0 -o wgexit -d 10.89.0.0/24 -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT
iptables -t nat -C POSTROUTING -s 10.89.0.0/24 -o eth0 -j MASQUERADE 2>/dev/null || iptables -t nat -I POSTROUTING -s 10.89.0.0/24 -o eth0 -j MASQUERADE
EOC
  chmod 700 /usr/local/libexec/vps-tunnel-end-firewall
  cat > /etc/systemd/system/vps-tunnel-end-firewall.service <<'EOC'
[Unit]
Description=Scoped forwarding for VPS tunnel
After=network-online.target
Before=wg-quick@wgexit.service
[Service]
Type=oneshot
ExecStart=/usr/local/libexec/vps-tunnel-end-firewall
RemainAfterExit=yes
[Install]
WantedBy=multi-user.target
EOC
  mkdir -p /etc/systemd/system/wg-quick@wgexit.service.d
  cat > /etc/systemd/system/wg-quick@wgexit.service.d/vps-tunnel.conf <<'EOC'
[Unit]
Requires=vps-tunnel-end-firewall.service
After=vps-tunnel-end-firewall.service
EOC
  cat > /etc/systemd/system/vps-tunnel-end.service <<'EOC'
[Unit]
Description=Isolated udp2raw end VPS tunnel
After=network-online.target wg-quick@wgexit.service
Wants=network-online.target
Requires=wg-quick@wgexit.service
[Service]
ExecStart=/usr/local/libexec/vps-tunnel-udp2raw --conf-file /etc/udp2raw-wg/end.conf
Restart=on-failure
RestartSec=3
[Install]
WantedBy=multi-user.target
EOC
  echo 'net.ipv4.ip_forward=1' > /etc/sysctl.d/90-vps-tunnel.conf
  sysctl -w net.ipv4.ip_forward=1
  systemctl daemon-reload
  systemctl enable --now vps-tunnel-end-firewall.service wg-quick@wgexit.service vps-tunnel-end.service
  echo 'New end tunnel started. Confirm handshake and client access before retiring old services.';;
 retire-old)
  systemctl is-active --quiet vps-tunnel-end.service || die 'New udp2raw service is not active'
  hooks=$(grep -E '^[[:space:]]*(PreUp|PostUp|PreDown|PostDown)[[:space:]]*=' /etc/wireguard/wg0.conf || true)
  expected=$'PostUp = sysctl -w net.ipv4.ip_forward=1; iptables -t nat -A POSTROUTING -o eth0 -j MASQUERADE\nPostDown = iptables -t nat -D POSTROUTING -o eth0 -j MASQUERADE'
  [[ $hooks == "$expected" ]] || die 'wg0 hooks differ from the audited snapshot; manually review before retirement'
  [[ $(wg show wgexit latest-handshakes | awk 'NR==1{print $2}') =~ ^[0-9]+$ ]] || die 'Cannot read tunnel handshake'
  hs=$(wg show wgexit latest-handshakes | awk 'NR==1{print $2}'); (( hs > 0 && $(date +%s)-hs < 180 )) || die 'No recent new tunnel handshake'
  echo 'Stopping wg0 disconnects its existing users. Its PostDown removes one broad eth0 MASQUERADE rule.'
  echo 'This cannot guarantee other services will remain unaffected. Test new client access and keep a provider console open.'
  confirm 'Stop and disable ONLY wg-quick@wg0 and udp2raw-end?'
  install -d -m 700 /root/vps-tunnel-backup
  iptables-save > /root/vps-tunnel-backup/iptables-before-retirement.rules
  cp -a /etc/wireguard/wg0.conf /root/vps-tunnel-backup/wg0.conf
  systemctl cat udp2raw-end > /root/vps-tunnel-backup/udp2raw-end.unit
  systemctl disable --now udp2raw-end.service
  systemctl disable --now wg-quick@wg0.service
  echo 'Stopped old services, not deleted. Roll back with: systemctl enable --now wg-quick@wg0 udp2raw-end';;
 *) echo "Usage: $0 {key|install|retire-old}"; exit 2;;
esac
