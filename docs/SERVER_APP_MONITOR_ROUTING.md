# Server App Monitor Routing

Last updated: 2026-05-27, Europe/Athens.

This note documents the routing change made so the server root IP opens Server App Monitor instead of the KAI app.
It also records the current unified production nginx shape: the `kai-nginx` container is the shared public edge for port `80` and `443`, routing requests by host name.

No secrets, tokens, sudo passwords, cookies, or patient/person data are stored here.

## Goal

- `https://10.4.51.232/` should open Server App Monitor.
- KAI should remain available through a hostname, not through the default IP vhost.
- One production nginx edge should own public `80/443` and route Server App Monitor, KAI, Guacamole, and shared app hostnames such as Chatty.
- HTTP on port `80` should redirect to HTTPS.

## Local Replica Files

Updated in this project:

- `KAI_APP_REPLICA/nginx.conf`
- `KAI_APP_REPLICA/docker-compose.remote.yml`

The local production-like compose profile now exposes both:

```yaml
ports:
  - "80:80"
  - "443:443"
```

The local `nginx.conf` now has:

- default HTTPS server for `server_name _`, proxying `/` to `http://10.4.51.232:4180`
- named HTTPS server for `kai-app` and `kai-app.251gh.local`, proxying `/` to `http://kai-app:5000`
- existing `/guacamole/` proxy kept under the named KAI server
- named HTTPS server for `chatty`, `chatty.251gh.local`, `chatbot`, and `chatbot.251gh.local`, proxying `/` to `http://chatty:3000`
- default HTTP server on port `80`, redirecting to HTTPS

The production edge is intentionally still the Docker nginx container from the KAI stack, not system nginx. This keeps the current public entrypoint in one place while preserving Docker-local routing to KAI and Guacamole.
Chatty must remain attached to the `kai_app_default` Docker network for the `chatty` upstream name to resolve from `kai-nginx`.

## Production Files

Applied on the VM `linuxsrv01` at `10.4.51.232`.

Production path:

```text
/home/kmh251/deployment/kai_app
```

Changed production files:

```text
/home/kmh251/deployment/kai_app/nginx.conf
/home/kmh251/deployment/kai_app/docker-compose.remote.yml
```

Backups created before the production change:

```text
/home/kmh251/deployment/kai_app/nginx.conf.backup-20260527-083521
/home/kmh251/deployment/kai_app/docker-compose.remote.yml.backup-20260527-083521
```

After the change, `kai-nginx` publishes:

```text
0.0.0.0:80->80/tcp
0.0.0.0:443->443/tcp
```

This means no second public nginx should also bind `80` or `443` on the host unless this routing model is deliberately replaced.

## Result

Verified after deployment:

```text
https://10.4.51.232/                         -> Server App Monitor
https://10.4.51.232/api/config              -> Server App Monitor API through nginx
http://10.4.51.232/                         -> 301 redirect to HTTPS
https://kai-app/ resolved to 10.4.51.232    -> KAI login redirect
https://chatty/ resolved to 10.4.51.232     -> Chatty through unified nginx
```

The KAI app is no longer the default response for direct IP access. It is now selected by the HTTP `Host` header.

## Why This Shape

Nginx chooses a server block by `server_name`.

Direct IP access usually sends `Host: 10.4.51.232`, so it lands in the default server block. That default block now proxies to Server App Monitor on port `4180`.

KAI still works when the request uses a KAI hostname such as `kai-app` or `kai-app.251gh.local`, because those names match the KAI HTTPS server block. Chatty works the same way for `chatty`, `chatty.251gh.local`, `chatbot`, and `chatbot.251gh.local`.

## Rollback

Rollback should restore the backed-up production files and restart/reload `kai-nginx`.

Example production rollback outline:

```bash
cd /home/kmh251/deployment/kai_app
sudo cp nginx.conf.backup-20260527-083521 nginx.conf
sudo cp docker-compose.remote.yml.backup-20260527-083521 docker-compose.remote.yml
docker compose -f docker-compose.remote.yml config --quiet
docker compose -f docker-compose.remote.yml up -d nginx
docker exec kai-nginx nginx -t
docker exec kai-nginx nginx -s reload
```

## Verification Commands

From a workstation that can reach the VM:

```bash
curl -k -I http://10.4.51.232
curl -k -I https://10.4.51.232
curl -k -sS https://10.4.51.232/api/config
curl -k -I --resolve kai-app:443:10.4.51.232 https://kai-app
curl -k -I --resolve chatty:443:10.4.51.232 https://chatty
```

From the server:

```bash
docker ps --filter name=kai-nginx --format '{{.Names}} {{.Ports}}'
docker exec kai-nginx nginx -t
```

Expected high-level result:

- direct IP opens Server App Monitor
- named `kai-app` host opens KAI
- named `chatty` host opens Chatty
- nginx config test passes
