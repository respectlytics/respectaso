from django.db import migrations

FIELDS = (("SearchResult", "difficulty_breakdown"), ("OpportunityScanResult", "difficulty_breakdown"))


def name_the_icons(apps, schema_editor):
    """Stored difficulty breakdowns name their notes' icons (aso/icons.py)
    instead of holding the emoji older versions stored."""
    from aso.icons import upgrade_stored

    for model_name, field in FIELDS:
        model = apps.get_model("aso", model_name)
        batch = []
        for row in model.objects.only("id", field).iterator(chunk_size=500):
            value, changed = upgrade_stored(getattr(row, field))
            if changed:
                setattr(row, field, value)
                batch.append(row)
            if len(batch) >= 500:
                model.objects.bulk_update(batch, [field])
                batch = []
        if batch:
            model.objects.bulk_update(batch, [field])


class Migration(migrations.Migration):

    dependencies = [
        ('aso', '0026_drop_run_speed_columns'),
    ]

    operations = [
        migrations.RunPython(name_the_icons, migrations.RunPython.noop),
    ]
