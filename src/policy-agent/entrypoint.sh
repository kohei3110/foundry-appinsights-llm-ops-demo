#!/bin/sh
set -eu

if [ "$(id -u)" -eq 0 ]; then
    if [ "${HOME:-}" = "/home/session" ]; then
        chown agent:agent "$HOME"
        install -d -o agent -g agent "$HOME/.sessions"
    else
        export HOME=/home/agent
    fi

    exec setpriv --reuid=agent --regid=agent --init-groups "$@"
fi

exec "$@"
