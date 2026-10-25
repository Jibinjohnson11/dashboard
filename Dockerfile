# Production image. Build: docker build -t seo-dashboard .
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    DJANGO_ENV=production \
    DJANGO_DEPLOYED=true

WORKDIR /app
RUN useradd --create-home --uid 10001 app

COPY requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt

COPY . .

# Give the application user ownership of the static directory.
RUN mkdir -p /app/staticfiles \
    && chown -R app:app /app/staticfiles

# Collect static files using the non-root application user.
USER app

RUN DJANGO_SECRET_KEY=build-only-not-secret \
    DJANGO_ALLOWED_HOSTS=localhost \
    python manage.py collectstatic --noinput
EXPOSE 8000
# Same port as gunicorn, and a Host header that passes ALLOWED_HOSTS (127.0.0.1 isn't in it when deployed).
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s CMD python -c "import os,sys,urllib.request as r; host=os.environ.get('DJANGO_ALLOWED_HOSTS','localhost').split(',')[0].strip(); req=r.Request('http://127.0.0.1:%s/healthz/' % os.environ.get('PORT','8000'), headers={'Host': host}); sys.exit(0 if r.urlopen(req).status == 200 else 1)"
CMD ["gunicorn", "config.wsgi:application", "-c", "gunicorn.conf.py"]
