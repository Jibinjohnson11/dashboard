# Production image. Build: docker build -t seo-dashboard .
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    DJANGO_ENV=production

WORKDIR /app
RUN useradd --create-home --uid 10001 app

COPY requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt

COPY . .
# collectstatic needs a key at build time only; the real one comes from the runtime environment.
RUN DJANGO_SECRET_KEY=build-only-not-secret DJANGO_ALLOWED_HOSTS=localhost python manage.py collectstatic --noinput

USER app
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz/').status == 200 else 1)"
CMD ["gunicorn", "config.wsgi:application", "-c", "gunicorn.conf.py"]
