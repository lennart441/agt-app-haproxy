#!/bin/sh
# Ersetzt Platzhalter in der HAProxy-Config durch Umgebungsvariablen.
# Eine gemeinsame haproxy.cfg im Repo, Knoten-Unterschiede nur über .env.
set -e

CFG_SRC="${HAPROXY_CFG_SRC:-/usr/local/etc/haproxy/haproxy.cfg}"
CFG_OUT="${HAPROXY_CFG_OUT:-/tmp/haproxy.cfg}"

NODE_NAME="${NODE_NAME:-agt-1}"
# MESH_NODES: komma-getrennt, Reihenfolge = agt-1, agt-2, agt-3
MESH_NODES="${MESH_NODES:-172.20.0.1,172.20.0.2,172.20.0.3}"
# Clusterweites Verbindungslimit (Stick-Table "global", über Peers summiert)
CLUSTER_MAXCONN="${CLUSTER_MAXCONN:-200}"

# Peers: lokaler Knoten ohne IP, alle anderen mit IP:50000 (Reihenfolge agt-1, agt-2, agt-3)
idx=1
for ip in $(echo "$MESH_NODES" | tr ',' ' '); do
  name="agt-${idx}"
  if [ "$name" = "$NODE_NAME" ]; then
    eval "LINE${idx}='   server ${name}'"
  else
    eval "LINE${idx}='   server ${name} ${ip}:50000'"
  fi
  idx=$((idx + 1))
done
# Defaults falls MESH_NODES weniger als 3 Einträge hat
LINE1="${LINE1:-   server agt-1}"
LINE2="${LINE2:-   server agt-2 172.20.0.2:50000}"
LINE3="${LINE3:-   server agt-3 172.20.0.3:50000}"

# 1) NODE_NAME und CLUSTER_MAXCONN (für Stick-Table-Limit) ersetzen
# 2) Peers-Zeilen: lokaler Peer ohne IP (Template hat feste 172.20.0.x; ersetze durch generierte Zeilen)
sed -e "s|__NODE_NAME__|${NODE_NAME}|g" \
    -e "s|__CLUSTER_MAXCONN__|${CLUSTER_MAXCONN}|g" \
    -e "s|   server agt-1 172.20.0.1:50000|${LINE1}|" \
    -e "s|   server agt-2 172.20.0.2:50000|${LINE2}|" \
    -e "s|   server agt-3 172.20.0.3:50000|${LINE3}|" \
    "$CFG_SRC" > "$CFG_OUT"

# Stats-Socket: Verzeichnis anlegen, alten Socket entfernen.
# Container läuft als user 99:99 (haproxy), dann ist chown überflüssig und schlägt unter rootless Docker fehl.
SOCKET_DIR="${HAPROXY_SOCKET_DIR:-/var/run/haproxy-stat}"
mkdir -p "$SOCKET_DIR"
rm -f "$SOCKET_DIR/socket"

exec "$@"
