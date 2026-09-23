"""Export the storefront registry for the website.

respectaso.com states what RespectASO covers, and the only way that stays true
is for the website to read this export rather than retype the list. The web
repo commits the output as docs/data/countries_export.json and its own test
asserts the page count matches it.

    manage.py export_countries > ../respectaso-web/docs/data/countries_export.json
"""

import datetime as dt
import json

from django.core.management.base import BaseCommand

from aso import countries


class Command(BaseCommand):
    help = "Print the supported storefronts as JSON, for the website."

    def add_arguments(self, parser):
        parser.add_argument(
            "--indent", type=int, default=1,
            help="JSON indent (default 1, which keeps the diff readable).",
        )

    def handle(self, *args, **options):
        payload = {
            "generated": dt.date.today().isoformat(),
            "total": len(countries.CODES),
            "regions": list(countries.REGIONS),
            "countries": [
                {
                    "code": c.code,
                    "name": c.name,
                    "region": c.region,
                    "listing_language": c.locales[0] if c.locales else "",
                    "apple_data": countries.apple_reports_data(c.code),
                }
                for region in countries.REGIONS
                for c in countries.by_region()[region]
            ],
        }
        self.stdout.write(json.dumps(payload, indent=options["indent"], ensure_ascii=False))
