#!/usr/bin/env bash
# One-time provisioning for chatMCD on the droplet. Run as root.
#
#   ssh root@droplet 'bash -s' < deploy/provision.sh
#
# Creates the service user, the venv and the systemd unit, and installs the
# nginx vhost. It does NOT request a certificate: certbot writes into the vhost
# it finds, so run it afterwards.
set -euo pipefail

APP=chatmcd
APP_DIR=/opt/${APP}
DOMAIN=chatmcd.mdeller.com

id -u "$APP" >/dev/null 2>&1 || useradd --system --home "$APP_DIR" --shell /usr/sbin/nologin "$APP"
install -d -o "$APP" -g "$APP" "$APP_DIR" "$APP_DIR/var"

if [[ ! -x "$APP_DIR/.venv/bin/python" ]]; then
  python3 -m venv "$APP_DIR/.venv"
  chown -R "$APP:$APP" "$APP_DIR/.venv"
fi
sudo -u "$APP" "$APP_DIR/.venv/bin/pip" install --quiet --upgrade pip
if [[ -f "$APP_DIR/chatmcd/requirements.txt" ]]; then
  sudo -u "$APP" "$APP_DIR/.venv/bin/pip" install --quiet -r "$APP_DIR/chatmcd/requirements.txt"
else
  echo "note: rsync the repo first, then re-run to install requirements"
fi

if [[ -f "$APP_DIR/deploy/chatmcd-web.service" ]]; then
  install -m 0644 "$APP_DIR/deploy/chatmcd-web.service" /etc/systemd/system/
  systemctl daemon-reload
  systemctl enable --now chatmcd-web.service || true
fi

if [[ -f "$APP_DIR/deploy/nginx-chatmcd.conf" ]]; then
  install -m 0644 "$APP_DIR/deploy/nginx-chatmcd.conf" "/etc/nginx/sites-available/${APP}"
  ln -sf "/etc/nginx/sites-available/${APP}" "/etc/nginx/sites-enabled/${APP}"
  nginx -t && systemctl reload nginx
fi

cat <<EOF

Provisioned. Remaining steps, in order:

  1. Put the real secrets in ${APP_DIR}/.env  (copy .env.example; HF_TOKEN is required)
     chown ${APP}:${APP} ${APP_DIR}/.env && chmod 600 ${APP_DIR}/.env
  2. certbot --nginx -d ${DOMAIN}
     mdeller.com's certificate does not cover subdomains; this needs its own.
  3. Patch the certbot-written listen line for HTTP/2. This droplet runs
     nginx 1.24, where http2 is a listen parameter:
       listen 443 ssl http2;
     NOT the 'http2 on;' form from 1.25.1. Check what the other vhosts use:
       grep -h "listen 443" /etc/nginx/sites-available/*
     Getting it wrong fails 'nginx -t', nginx keeps the old config, the site
     stays up, and the patch silently does not apply.
  4. systemctl restart ${APP}-web && curl -s https://${DOMAIN}/api/health
  5. Add chatMCD to mdeller-landing/apps.json (at the TOP) and ./deploy.sh there.
     Beacon: "^/static/img/favicon\\\\.svg\\\\b" — the landing page counts hits by
     watching for a sub-resource only a rendering browser fetches.
EOF
