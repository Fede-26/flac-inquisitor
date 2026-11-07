# Run behind a reverse proxy

This guide shows how to serve flac-inquisitor under its own host name, over
HTTPS, with a login in front of it.

The app has no authentication of its own. Anyone who can open it can move files
to the quarantine and delete them from there. If the page is reachable from
anything other than your own local network, put a login in front of it.

## What the app expects from the proxy

- **Its own host name**, such as `flac.example.lan`. The app serves its pages,
  API and static files from `/`; it does not support living under a sub-path
  such as `example.lan/flac/`.
- **Plain HTTP to the container**, port 8080. TLS ends at the proxy. Pages use
  relative links for their assets, so they load over HTTPS without
  mixed-content errors.
- **No special timeouts or WebSockets.** While a scan runs, the pages poll its
  status once a second with short requests. Spectrograms of long hi-res files can take a
  few seconds to render the first time; a 60-second read timeout is plenty.

## Caddy

```caddyfile
flac.example.lan {
    basic_auth {
        # Generate the hash with: caddy hash-password
        admin $2a$14$replace-with-your-hash
    }
    reverse_proxy flac-inquisitor:8080
}
```

`flac-inquisitor` is the Compose service name; use the host and port instead if
Caddy does not share a Docker network with the app.

## nginx

```nginx
server {
    listen 443 ssl;
    server_name flac.example.lan;
    ssl_certificate     /etc/ssl/flac.example.lan.crt;
    ssl_certificate_key /etc/ssl/flac.example.lan.key;

    # Create the file with: htpasswd -c /etc/nginx/flac.htpasswd admin
    auth_basic           "flac-inquisitor";
    auth_basic_user_file /etc/nginx/flac.htpasswd;

    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 60s;
    }
}
```

## Other proxies

Any proxy that forwards a host name to `http://<app host>:8080` works, as long
as it adds a login if the page is reachable from outside your network. With a
single sign-on service (Authelia, Authentik, oauth2-proxy), protect the whole
host. Only `/healthz` may stay open, for monitoring: it returns the status
and the version, nothing else.

## Check the result

Open `https://flac.example.lan`. The browser asks for the login, then shows the
Dashboard with its styles. If the page shows unstyled text, the proxy is
serving it under a sub-path or rewriting URLs: give it its own host name.
