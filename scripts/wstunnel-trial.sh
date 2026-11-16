#!/usr/bin/env bash
# Temporary transport trial. Does not change wg-quick configs, routes or firewalls.
set -Eeuo pipefail
umask 077
[[ $EUID -eq 0 ]] || { echo 'Run as root' >&2; exit 1; }
VERSION=11.1.0
SHA=512edf4e7bb64fc133522220123514868fae75b5bae4124b53e396787276aa23
BIN=/usr/local/libexec/vps-wstunnel-trial
DIR=/etc/vps-wstunnel-trial
die(){ echo "ERROR: $*" >&2; exit 1; }
check_tools(){ for x in curl tar sha256sum systemctl ss openssl wg getent; do command -v "$x" >/dev/null || die "Missing required tool: $x"; done; [[ $(uname -m) == x86_64 ]] || die 'Only x86-64 is supported by this installer'; }
read_domain(){
 read -r -p 'DNS-only TLS domain pointing to 5.75.198.81: ' domain
 [[ ${#domain} -le 253 && $domain =~ ^[a-zA-Z0-9]([a-zA-Z0-9.-]*[a-zA-Z0-9])?$ && $domain == *.* && $domain != *..* ]] || die 'Invalid domain'
 # TLS URL is resolved locally; require a direct, unambiguous IPv4 destination.
 ips=$(getent ahostsv4 "$domain" | awk '{print $1}' | sort -u || true)
 [[ "$ips" == 5.75.198.81 ]] || die 'Domain must resolve only to 5.75.198.81 over IPv4 (Cloudflare proxy must be off)'
}
read_secret(){
 read -rs -p 'Shared random path secret (32-64 alphanumeric characters): ' secret; echo
 [[ $secret =~ ^[a-zA-Z0-9]{32,64}$ ]] || die 'Secret must be 32-64 alphanumeric characters'
}
check_port(){
 local proto=$1 port=$2
 if ss -H -l -n -"$proto" | awk '{print $4}' | grep -Eq "[:.]${port}$"; then die "Port $port already in use"; fi
}
install_bin(){
 [[ ! -e "$BIN" ]] || die "$BIN already exists; refusing to overwrite"
 local tmp; tmp=$(mktemp -d)
 curl -fL --retry 2 --connect-timeout 15 -o "$tmp/release.tar.gz" "https://github.com/erebe/wstunnel/releases/download/v${VERSION}/wstunnel_${VERSION}_linux_amd64.tar.gz"
 printf '%s  %s\n' "$SHA" "$tmp/release.tar.gz" | sha256sum -c - || die 'Release checksum failed'
 # Extract only the known binary, not arbitrary release paths.
 tar -xzf "$tmp/release.tar.gz" -C "$tmp" wstunnel
 install -D -m 0755 "$tmp/wstunnel" "$BIN"
 rm -rf "$tmp"
}
new_setup(){
 [[ ! -e "$DIR" && ! -e "$BIN" ]] || die 'Existing or partial trial installation found; inspect manually before retrying'
}
one_peer(){
 mapfile -t peers < <(wg show wgexit peers)
 [[ ${#peers[@]} == 1 && ${peers[0]} =~ ^[A-Za-z0-9+/]{43}=$ ]] || die 'wgexit must have exactly one peer'
 peer=${peers[0]}
}
case ${1:-} in
 setup-end)
  check_tools; new_setup; check_port t 8444
  ip -4 -o addr show | grep -q 'inet 5\.75\.198\.81/' || die 'Run setup-end on the end VPS'
  ss -H -l -n -u | awk '{print $4}' | grep -Eq '[:.]51901$' || die 'WireGuard UDP port 51901 not listening'
  read_domain
  read -r -p 'Existing full certificate chain PEM file: ' cert
  read -r -p 'Existing matching TLS private key PEM file: ' key
  [[ $cert =~ ^/[a-zA-Z0-9_./-]+$ && $key =~ ^/[a-zA-Z0-9_./-]+$ && -r $cert && -r $key ]] || die 'Certificate and key must be readable absolute paths without spaces'
  openssl x509 -in "$cert" -noout -checkend 86400 >/dev/null || die 'Certificate is invalid or expires within 24 hours'
  openssl x509 -in "$cert" -noout -checkhost "$domain" | grep -q 'does match' || die 'Certificate does not match domain'
  cert_pub=$(openssl x509 -in "$cert" -pubkey -noout | openssl pkey -pubin -outform DER | sha256sum | awk '{print $1}')
  key_pub=$(openssl pkey -in "$key" -pubout -outform DER | sha256sum | awk '{print $1}')
  [[ $cert_pub == "$key_pub" ]] || die 'Certificate and private key do not match'
  read_secret
  [[ ! -e /etc/systemd/system/vps-wstunnel-end.service ]] || die 'Trial unit already exists'
  install_bin
  install -d -m 700 "$DIR"
  printf 'WSTUNNEL_PATH=%s\n' "$secret" > "$DIR/end.env"; unset secret
  cat > /etc/systemd/system/vps-wstunnel-end.service <<EOC
[Unit]
Description=Temporary WireGuard WebSocket transport server
After=network-online.target wg-quick@wgexit.service
[Service]
Type=simple
EnvironmentFile=$DIR/end.env
UnsetEnvironment=HTTP_PROXY HTTPS_PROXY ALL_PROXY http_proxy https_proxy all_proxy
ExecStart=$BIN server --restrict-to 127.0.0.1:51901 --restrict-http-upgrade-path-prefix \${WSTUNNEL_PATH} --tls-certificate $cert --tls-private-key $key wss://5.75.198.81:8444
Restart=on-failure
RestartSec=3
EOC
  systemctl daemon-reload; systemctl start vps-wstunnel-end.service
  systemctl is-active --quiet vps-wstunnel-end.service || die 'Server did not start; inspect its journal (redact secrets)'
  echo 'Trial server started on TCP 8444; not enabled at boot. Existing services left running.';;
 setup-middle)
  check_tools; new_setup; check_port u 40962
  ip -4 -o addr show | grep -q 'inet 79\.175\.149\.221/' || die 'Run setup-middle on the middle VPS'
  wg show wgexit >/dev/null 2>&1 || die 'wgexit not present'
  read_domain; read_secret
  [[ ! -e /etc/systemd/system/vps-wstunnel-middle.service ]] || die 'Trial unit already exists'
  install_bin
  install -d -m 700 "$DIR"
  printf 'WSTUNNEL_PATH=%s\n' "$secret" > "$DIR/middle.env"; unset secret
  cat > /etc/systemd/system/vps-wstunnel-middle.service <<EOC
[Unit]
Description=Temporary WireGuard WebSocket transport client
After=network-online.target wg-quick@wgexit.service
[Service]
Type=simple
EnvironmentFile=$DIR/middle.env
UnsetEnvironment=HTTP_PROXY HTTPS_PROXY ALL_PROXY http_proxy https_proxy all_proxy
ExecStart=$BIN client --tls-verify-certificate --dns-resolver system://0.0.0.0 --dns-resolver-prefer-ipv4 --http-upgrade-path-prefix \${WSTUNNEL_PATH} -L udp://127.0.0.1:40962:127.0.0.1:51901?timeout_sec=0 wss://$domain:8444
Restart=on-failure
RestartSec=3
EOC
  systemctl daemon-reload; systemctl start vps-wstunnel-middle.service
  systemctl is-active --quiet vps-wstunnel-middle.service || die 'Client did not start; inspect its journal (redact secrets)'
  echo 'Trial client started on localhost UDP 40962; not enabled at boot. WireGuard endpoint unchanged.';;
 switch)
  one_peer
  systemctl is-active --quiet vps-wstunnel-middle.service || die 'Trial client is not active'
  endpoint=$(wg show wgexit endpoints | awk -v peer="$peer" '$1==peer {print $2}')
  [[ "$endpoint" == 127.0.0.1:40961 ]] || die "Expected udp2raw endpoint 127.0.0.1:40961; found $endpoint; refusing switch"
  read -r -p 'Existing clients may disconnect. Type YES to switch the running endpoint: ' ans
  [[ $ans == YES ]] || die 'Cancelled'
  wg set wgexit peer "$peer" endpoint 127.0.0.1:40962
  echo 'Switched temporarily. Verify a NEW handshake and client internet access. If it fails, run restore immediately.'
  echo 'The udp2raw service remains running; saved WireGuard config was not changed.';;
 restore)
  one_peer
  endpoint=$(wg show wgexit endpoints | awk -v peer="$peer" '$1==peer {print $2}')
  [[ "$endpoint" == 127.0.0.1:40962 || "$endpoint" == 127.0.0.1:40961 ]] || die "Unexpected endpoint $endpoint; refusing to overwrite"
  systemctl is-active --quiet vps-tunnel-middle.service || die 'Original udp2raw client not running; start and check it before restoring'
  wg set wgexit peer "$peer" endpoint 127.0.0.1:40961
  echo 'Restored original endpoint. Verify a NEW handshake and client access.';;
 *) echo "Usage: $0 {setup-end|setup-middle|switch|restore}"; exit 2;;
esac
