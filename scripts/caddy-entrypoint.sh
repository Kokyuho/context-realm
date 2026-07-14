#!/bin/sh
# =============================================================================
# ContextRealm — Caddy entrypoint
# =============================================================================
# Generates the runtime Caddyfile based on which deployment mode we're in.
# Two modes, controlled by REALM_DOMAIN:
#
#   1. REALM_DOMAIN is set (e.g. mcp.yourdomain.com):
#      Caddy auto-requests a Let's Encrypt cert and serves HTTPS on :443.
#      The local-only block is still generated but bound to 127.0.0.1:8443
#      so it isn't reachable from outside the host.
#
#   2. REALM_DOMAIN is empty:
#      Production block is omitted entirely. Caddy serves a self-signed
#      cert on https://localhost:8443.
#
# Generated file is written to /etc/caddy/Caddyfile.runtime, which the
# Caddy image sources via the CADDYFILE env var.
# =============================================================================
set -eu

OUT=/etc/caddy/Caddyfile.runtime

{
  echo '# Generated at runtime from Caddyfile.template — do not edit.'
  echo '{'
  echo '    admin off'
  echo '}'
} > "$OUT"

if [ -n "${REALM_DOMAIN:-}" ]; then
    cat >> "$OUT" <<EOF

# Production / VPS site. Let's Encrypt will issue a cert automatically
# once DNS for REALM_DOMAIN points at this host.
${REALM_DOMAIN} {
    reverse_proxy mcp:8765 {
        flush_interval -1
        transport http {
            keepalive off
        }
    }
}
EOF
else
    cat >> "$OUT" <<'EOF'

# Production block intentionally omitted (REALM_DOMAIN unset). Use
# https://localhost:8443 from the local-only block below to test the
# full TLS path on one machine. Set REALM_DOMAIN in .env and `docker
# compose up -d caddy` again to enable Let's Encrypt.
EOF
fi

# Local-only block is always generated: bound to 127.0.0.1:8443 so it
# stays unreachable from outside the host even when running side-by-side
# with the production block. Self-signed cert is fine for testing.
cat >> "$OUT" <<'EOF'

# Local-only site. Bound to loopback only so it cannot leak to a public
# interface. Browsers will warn about the self-signed cert; MCP clients
# that pin certs will fail (intentional — those clients should be using
# the production block).
:8443 {
    bind 127.0.0.1
    tls internal
    reverse_proxy mcp:8765 {
        flush_interval -1
        transport http {
            keepalive off
        }
    }
    log {
        output stdout
        format console
    }
}
EOF

echo "[entrypoint] Caddyfile written to $OUT"
echo "[entrypoint] Mode: $([ -n "${REALM_DOMAIN:-}" ] && echo "production (REALM_DOMAIN=${REALM_DOMAIN})" || echo "local-only")"

# Hand off to Caddy. The official caddy image expects either `caddy run`
# (default) or a command in the form `caddy <command>`. The CADDYFILE
# env var tells Caddy which file to load.
export CADDYFILE="$OUT"
exec caddy run --config "$CADDYFILE" --adapter caddyfile 2>&1