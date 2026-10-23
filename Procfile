release: python manage.py migrate --noinput && python manage.py createcachetable && python manage.py init_mongodb
web: gunicorn config.wsgi:application -c gunicorn.conf.py
