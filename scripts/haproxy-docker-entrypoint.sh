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

# GEO_ALLOWED_COUNTRIES: kommasepariert (z. B. DE,AT,CH) → Regex (DE|AT|CH) für HAProxy-ACL
GEO_ALLOWED_COUNTRIES="${GEO_ALLOWED_COUNTRIES:-DE,AT,CH,FR,IT,LU,BE,NL}"
GEO_REGEX="$(echo "$GEO_ALLOWED_COUNTRIES" | sed 's/ *, */|/g' | sed 's/^/(/' | sed 's/$/)/')"

# 1) NODE_NAME, CLUSTER_MAXCONN, Geo-Länder-Regex ersetzen
# 2) Peers-Zeilen: lokaler Peer ohne IP (Template hat feste 172.20.0.x; ersetze durch generierte Zeilen)
# GEO_REGEX enthält "|", daher # als sed-Delimiter statt |
sed -e "s|__NODE_NAME__|${NODE_NAME}|g" \
    -e "s|__CLUSTER_MAXCONN__|${CLUSTER_MAXCONN}|g" \
    -e "s#__GEO_ALLOWED_COUNTRIES_REGEX__#${GEO_REGEX}#g" \
    -e "s|   server agt-1 172.20.0.1:50000|${LINE1}|" \
    -e "s|   server agt-2 172.20.0.2:50000|${LINE2}|" \
    -e "s|   server agt-3 172.20.0.3:50000|${LINE3}|" \
    "$CFG_SRC" > "$CFG_OUT"

# Maps: Wenn geo.map/whitelist.map fehlen, permissive Defaults (Fail-Open: alle durchlassen).
# Geo-Manager überschreibt geo.map mit echter Liste.
MAP_DIR="${HAPROXY_MAP_DIR:-/usr/local/etc/haproxy/maps}"
mkdir -p "$MAP_DIR"
if [ ! -f "$MAP_DIR/geo.map" ]; then
  FIRST_GEO="${GEO_ALLOWED_COUNTRIES%%,*}"
  FIRST_GEO="${FIRST_GEO:-DE}"
  FIRST_GEO="$(echo "$FIRST_GEO" | tr -d ' ')"
  [ -z "$FIRST_GEO" ] && FIRST_GEO="DE"
  printf '0.0.0.0/0\t%s\n::/0\t%s\n' "$FIRST_GEO" "$FIRST_GEO" > "$MAP_DIR/geo.map"
fi
if [ ! -f "$MAP_DIR/whitelist.map" ]; then
  touch "$MAP_DIR/whitelist.map"
fi

# Stats-Socket: Verzeichnis anlegen, Rechte setzen, dann HAProxy als User 99 starten.
# chown 99:99 schlägt unter rootless Docker fehl → Fallback chmod 1777; setpriv startet HAProxy ohne root.
SOCKET_DIR="${HAPROXY_SOCKET_DIR:-/var/run/haproxy-stat}"
mkdir -p "$SOCKET_DIR"
rm -f "$SOCKET_DIR/socket"
if ! chown 99:99 "$SOCKET_DIR" 2>/dev/null; then
  chmod 1777 "$SOCKET_DIR"
fi
exec setpriv --reuid=99 --regid=99 --init-groups -- "$@"
