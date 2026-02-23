#!/bin/bash
set -e

# Auto-generate SECRET_KEY on first run
if [ -z "$SECRET_KEY" ]; then
    if [ -f /app/data/.secret_key ]; then
        export SECRET_KEY=$(cat /app/data/.secret_key)
    else
        export SECRET_KEY=$(python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())")
        mkdir -p /app/data
        echo "$SECRET_KEY" > /app/data/.secret_key
    fi
fi

python manage.py migrate --noinput
python manage.py collectstatic --noinput

echo ""
echo "============================================"
echo "  RespectASO is ready!"
echo ""
echo "  Open in your browser:"
echo "    → http://localhost"
echo ""
echo "  Auto-start: enabled (restart: unless-stopped)"
echo "  This container will start automatically"
echo "  when Docker starts. No need to run"
echo "  'docker compose up' again after a reboot."
echo ""
echo "  (Port 80 in use? Change it in docker-compose.yml)"
echo "============================================"
echo ""

exec "$@"
