# HAProxy 3.2 mit setpriv für sicheres Privileg-Drop nach Socket-Setup.
# Entrypoint läuft als root (chown/chmod), HAProxy dann per setpriv als User 99.
FROM haproxy:3.2-alpine
USER root
RUN apk add --no-cache setpriv
# Default root, damit gemounteter Entrypoint chown ausführen kann; setpriv startet HAProxy als 99.
