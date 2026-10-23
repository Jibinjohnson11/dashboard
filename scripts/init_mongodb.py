#!/usr/bin/env python
"""Create MongoDB collections and indexes. Equivalent to `python manage.py init_mongodb`."""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

import django  # noqa: E402

django.setup()

from django.core.management import call_command  # noqa: E402

call_command("init_mongodb")
