#!/usr/bin/env bash
set -Eeuo pipefail
export DEBIAN_FRONTEND=noninteractive
umask 077
UDP_COMMIT=4208db6e27c46f3ccec8b98722af7ec23bc62e73
DASH_COMMIT=d821a5622f247eba11721e326d8b59422a07dc1b
die(){ echo "ERROR: $*" >&2; exit 1; }
root(){ [[ $EUID == 0 ]] || die 'Run as root'; command -v systemctl >/dev/null || die 'systemd required'; }
install_deps(){ apt-get update && apt-get install -y wireguard-tools iptables iproute2 git build-essential python3-venv curl ca-certificates; }
key_init(){
 mkdir -p /etc/udp2raw-wg /etc/wireguard; chmod 700 /etc/udp2raw-wg /etc/wireguard
 if [[ ! -s /etc/udp2raw-wg/tunnel.key ]]; then wg genkey > /etc/udp2raw-wg/tunnel.key; fi
 wg pubkey < /etc/udp2raw-wg/tunnel.key > /etc/udp2raw-wg/tunnel.pub
 echo 'Tunnel public key (copy to the other server):'; cat /etc/udp2raw-wg/tunnel.pub
}
read_inputs(){
 read -r -p 'Other VPS tunnel public key: ' peer
 [[ $peer =~ ^[A-Za-z0-9+/]{43}=$ ]] || die 'Invalid WireGuard public key'
 printf '%s' "$peer" | base64 -d >/dev/null 2>&1 || die 'Invalid key encoding'
 read -r -s -p 'Shared udp2raw password (same on both; 24-64 alphanumeric characters): ' pass; echo
 [[ $pass =~ ^[A-Za-z0-9]{24,64}$ ]] || die 'Invalid password format'
}
build_udp(){
 [[ ! -e /usr/local/libexec/vps-tunnel-udp2raw ]] || die 'Tunnel binary already exists; refusing overwrite'
 local work; work=$(mktemp -d); git clone https://github.com/wangyu-/udp2raw.git "$work/src"
 git -C "$work/src" checkout --detach "$UDP_COMMIT"
 make -C "$work/src" dynamic
 install -D -m 0755 "$work/src/udp2raw_dynamic" /usr/local/libexec/vps-tunnel-udp2raw
 rm -rf "$work"
}
check_port(){ local proto=$1 port=$2; if ss -H -l -n -$proto | awk '{print $4}' | grep -Eq "[:.]${port}$"; then die "Port $port ($proto) is already listening"; fi; }
confirm(){ local answer; read -r -p "$1 Type YES to continue: " answer; [[ $answer == YES ]] || die 'Cancelled'; }
