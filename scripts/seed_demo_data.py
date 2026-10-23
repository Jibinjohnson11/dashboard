#!/usr/bin/env python
"""Development-only demo data. Equivalent to `python manage.py seed_demo_data`."""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

import django  # noqa: E402

django.setup()

from django.core.management import call_command  # noqa: E402

call_command("seed_demo_data", *sys.argv[1:])
