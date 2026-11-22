FROM python:3.14-slim@sha256:9aaa474097d094040649f2162a627c0049b408f52138d980916788d80f04802f

WORKDIR /app

# Users see RespectASO's own error pages, never Django's debug page
# (aso/error_views.py). Override with DEBUG=True only to develop.
ENV DEBUG=False
# The daily update check names this edition "docker" (aso/update_check.py).
ENV RESPECTASO_DOCKER=1

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN python manage.py collectstatic --noinput

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 8080

ENTRYPOINT ["/entrypoint.sh"]
CMD ["gunicorn", "core.wsgi:application", "--bind", "0.0.0.0:8080", "--workers", "1", "--threads", "2"]
