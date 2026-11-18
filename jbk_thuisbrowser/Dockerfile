ARG BUILD_FROM
FROM $BUILD_FROM

# Draait als root zodat het profiel in /data (Home Assistant-opslag) bewaard kan worden.
USER root

# Headless Chromium, alleen bereikbaar binnen Home Assistant (geen poort naar buiten).
# De Cloudflare-tunnel (Cloudflared-add-on) stuurt het verkeer hierheen.
# Browser sluit na 5 minuten zonder gebruik; ingelogde sessies blijven bewaard in /data/profile.
ENTRYPOINT ["sh", "-c", "CLI=/app/cli.js; [ -f $CLI ] || CLI=$(find / -path '*/@playwright/mcp/cli.js' 2>/dev/null | head -1); exec node $CLI --headless --browser chromium --no-sandbox --host 0.0.0.0 --port 8931 --allowed-hosts '*' --user-data-dir /data/profile --idle-timeout 300000 --shared-browser-context --viewport-size 1366x900"]
