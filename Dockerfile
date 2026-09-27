FROM python:3.12-slim

WORKDIR /app

# Users see RespectASO's own error pages, never Django's debug page
# (aso/error_views.py). Override with DEBUG=True only to develop.
ENV DEBUG=False

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN python manage.py collectstatic --noinput

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 8080

ENTRYPOINT ["/entrypoint.sh"]
CMD ["gunicorn", "core.wsgi:application", "--bind", "0.0.0.0:8080", "--workers", "1", "--threads", "2"]
