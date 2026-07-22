# Deploy /analytics/ (Plan 2)

Servicio `doors-analytics-api` en `127.0.0.1:8447` publicado bajo `/analytics/` en el vhost
`/home/azcweb/conf/web/doors.azc.com.co/nginx.ssl.conf` (snapshot en `nginx.ssl.conf.snapshot`).

## Bloques agregados (con HTTP Basic Auth — OBLIGATORIO, expone logs de acceso)
```
location /analytics/api/ {
    auth_basic "AZC Accesos - Restringido";
    auth_basic_user_file /etc/nginx/doors-analytics.htpasswd;
    proxy_pass http://127.0.0.1:8447/api/;
    proxy_set_header Host $host;
}
location /analytics/ {
    auth_basic "AZC Accesos - Restringido";
    auth_basic_user_file /etc/nginx/doors-analytics.htpasswd;
    alias /home/azcweb/web/doors.azc.com.co/public_html/analytics/;
    index index.html;
}
```

## Credenciales
- htpasswd: `/etc/nginx/doors-analytics.htpasswd` (root:www-data 640), user `azcdoors`.
- Rotar: `htpasswd -B /etc/nginx/doors-analytics.htpasswd azcdoors` (password NO se versiona).

## Nota de seguridad
`doors.azc.com.co` es público. `/analytics/` sirve historial de accesos (tarjeta/puerta/hora/concedido)
→ NUNCA sin auth. En Plan 4 se unifica auth (cookie httpd) y se cubre también `/schedules/` y `/door-opener/`
que HOY siguen sin auth (deuda pre-existente).
