#!/bin/sh
# Ersetzt Platzhalter in den HAProxy-Config-Dateien durch Umgebungsvariablen.
# Eine gemeinsame conf.d/ Struktur im Repo, Knoten-Unterschiede nur über .env.
set -e

CFG_SRC_DIR="${HAPROXY_CFG_SRC_DIR:-/usr/local/etc/haproxy/conf.d}"
CFG_OUT_DIR="${HAPROXY_CFG_OUT_DIR:-/tmp/conf.d}"

NODE_NAME="${NODE_NAME:-agt-1}"
MESH_NODES="${MESH_NODES:-172.20.0.1,172.20.0.2,172.20.0.3}"
CLUSTER_MAXCONN="${CLUSTER_MAXCONN:-200}"

# Einzelne Mesh-IPs extrahieren (für __MESH_IP_*__ Platzhalter in Backends)
MESH_IP_1="$(echo "$MESH_NODES" | cut -d, -f1 | tr -d ' ')"
MESH_IP_2="$(echo "$MESH_NODES" | cut -d, -f2 | tr -d ' ')"
MESH_IP_3="$(echo "$MESH_NODES" | cut -d, -f3 | tr -d ' ')"
MESH_IP_1="${MESH_IP_1:-172.20.0.1}"
MESH_IP_2="${MESH_IP_2:-172.20.0.2}"
MESH_IP_3="${MESH_IP_3:-172.20.0.3}"

# Peers: lokaler Knoten ohne IP, alle anderen mit IP:50000
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
LINE1="${LINE1:-   server agt-1}"
LINE2="${LINE2:-   server agt-2 172.20.0.2:50000}"
LINE3="${LINE3:-   server agt-3 172.20.0.3:50000}"

# GEO_ALLOWED_COUNTRIES: kommasepariert → Regex für HAProxy-ACL
GEO_ALLOWED_COUNTRIES="${GEO_ALLOWED_COUNTRIES:-DE,AT,CH,FR,IT,LU,BE,NL}"
GEO_REGEX="$(echo "$GEO_ALLOWED_COUNTRIES" | sed 's/ *, */|/g' | sed 's/^/(/' | sed 's/$/)/')"

STATS_USER="${STATS_USER:-admin}"
STATS_PASSWORD="${STATS_PASSWORD:-change-me}"

# Alle .cfg-Dateien aus conf.d verarbeiten (Platzhalter ersetzen)
mkdir -p "$CFG_OUT_DIR"
for f in "$CFG_SRC_DIR"/*.cfg; do
  [ -f "$f" ] || continue
  # Peer-Zeilen zuerst (spezifisch, mit :50000), dann allgemeine __MESH_IP_*__
  sed -e "s|__NODE_NAME__|${NODE_NAME}|g" \
      -e "s|__CLUSTER_MAXCONN__|${CLUSTER_MAXCONN}|g" \
      -e "s|__STATS_USER__|${STATS_USER}|g" \
      -e "s|__STATS_PASSWORD__|${STATS_PASSWORD}|g" \
      -e "s#__GEO_ALLOWED_COUNTRIES_REGEX__#${GEO_REGEX}#g" \
      -e "s|   server agt-1 __MESH_IP_1__:50000|${LINE1}|" \
      -e "s|   server agt-2 __MESH_IP_2__:50000|${LINE2}|" \
      -e "s|   server agt-3 __MESH_IP_3__:50000|${LINE3}|" \
      -e "s|__MESH_IP_1__|${MESH_IP_1}|g" \
      -e "s|__MESH_IP_2__|${MESH_IP_2}|g" \
      -e "s|__MESH_IP_3__|${MESH_IP_3}|g" \
      "$f" > "$CFG_OUT_DIR/$(basename "$f")"
done

# Maps: Wenn geo.map/whitelist.map fehlen, permissive Defaults (Fail-Open: alle durchlassen).
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
SOCKET_DIR="${HAPROXY_SOCKET_DIR:-/var/run/haproxy-stat}"
mkdir -p "$SOCKET_DIR"
rm -f "$SOCKET_DIR/socket"
if ! chown 99:99 "$SOCKET_DIR" 2>/dev/null; then
  chmod 1777 "$SOCKET_DIR"
fi
exec setpriv --reuid=99 --regid=99 --init-groups -- "$@"
