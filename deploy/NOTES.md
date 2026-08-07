# Deployment Notes

## Instagram OAuth Checklist

Instagram OAuth only works when the running backend, the desktop Server URL, and
Meta's registered redirect URI all point to the same stable public host.

1. Use a stable host for production, such as the OCI public IP, a real domain, or
   a named Cloudflare Tunnel. Avoid ad-hoc `*.trycloudflare.com` URLs because
   they rotate and invalidate the registered Meta redirect URI.
2. Set `API_URL` in the server `.env` to that stable host.
3. Set `META_GRAPH_API_VERSION` in the server `.env` to a supported version from
   the Meta Developer dashboard / Graph API Upgrade Tool.
4. Register this exact URI in Meta for Developers:
   `{API_URL}/api/oauth/instagram/callback`
5. Redeploy and restart the backend after changing `.env` or source code:
   `OCI_HOST=ubuntu@129.213.126.251 bash deploy/deploy.sh --migrate`
6. In ReelPush desktop Settings, use the same stable host as the Server URL,
   then reconnect Instagram.

## TODO: Open port 80 in OCI Security List

The server currently runs on port 8100 because the OCI cloud-level firewall (Security List)
only has port 8100 open. Port 80 is already allowed by iptables on the instance and Nginx is
ready to serve on port 80 — the only missing step is a console change.

**Steps to switch to port 80:**

1. Log in to cloud.oracle.com
2. Go to Networking → Virtual Cloud Networks → your VCN → Security Lists
3. Edit the Default Security List → Add Ingress Rule:
   - Source CIDR: `0.0.0.0/0`
   - IP Protocol: TCP
   - Destination Port Range: `80`
4. Update `nginx.conf`: change `listen 8100;` → `listen 80;`
5. Update `docker-compose.prod.yml` nginx ports: `"8100:8100"` → `"80:80"`
6. Update `reelpush_desktop.py`: `OFFICIAL_SERVER_URL = "http://129.213.126.251"` (no port)
7. Run `OCI_HOST=ubuntu@129.213.126.251 bash deploy/deploy.sh`
8. Optionally remove the port 8100 iptables rule on the server:
   `sudo iptables -D INPUT -p tcp --dport 8100 -j ACCEPT && sudo iptables-save | sudo tee /etc/iptables/rules.v4`
