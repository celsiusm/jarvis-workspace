#!/bin/sh
# Jarvis Workspace — container entrypoint.
#
# Starts Jarvis as the non-root user `jarvis` with the UID/GID the host asks for
# (PUID/PGID; Unraid uses 99/100, most Linux hosts 1000/1000). That way:
#   - the files the agents create in /proyectos belong to YOU, not to root;
#   - Claude Code's autonomous mode works (it refuses to run that way as root).
# PUID=0 keeps everything as root (not recommended).
set -e

PUID="${PUID:-1000}"
PGID="${PGID:-1000}"

# Already started as a non-root user (docker run --user …): nothing to switch.
if [ "$(id -u)" != "0" ]; then
    exec "$@"
fi

if [ "$PUID" = "0" ]; then
    export HOME=/root
    exec "$@"
fi

case "$PUID$PGID" in
    *[!0-9]*) echo "[jarvis] PUID/PGID must be numbers (got '$PUID'/'$PGID')" >&2; exit 1 ;;
esac

# Re-map the `jarvis` user/group to the requested ids (-o: the id may already
# exist, e.g. gid 100 = `users` on Debian).
if [ "$(id -g jarvis)" != "$PGID" ]; then groupmod -o -g "$PGID" jarvis; fi
if [ "$(id -u jarvis)" != "$PUID" ]; then usermod -o -u "$PUID" -g "$PGID" jarvis; fi

# Jarvis writes its state in /app/data and the CLIs' logins in $HOME. Fix the
# owner only when it differs (a recursive chown on every start would be slow).
for d in /app/data /home/jarvis; do
    mkdir -p "$d"
    if [ "$(stat -c %u "$d")" != "$PUID" ] || [ "$(stat -c %g "$d")" != "$PGID" ]; then
        chown -R "$PUID:$PGID" "$d"
    fi
done

export HOME=/home/jarvis USER=jarvis
exec setpriv --reuid="$PUID" --regid="$PGID" --init-groups "$@"
