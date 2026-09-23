from django.db import migrations, models


class Migration(migrations.Migration):
    """The per-storefront ratings cache keeps the whole store profile now
    (count, average, release date), so it is named for what it holds."""

    dependencies = [
        ("aso", "0015_app_ratings_by_storefront"),
    ]

    operations = [
        migrations.RenameField(
            model_name="app",
            old_name="ratings_by_storefront",
            new_name="store_profiles",
        ),
        migrations.AlterField(
            model_name="app",
            name="store_profiles",
            field=models.JSONField(
                blank=True, default=dict,
                help_text=(
                    "What the App Store shows for this app in each storefront, as "
                    "last read: {code: {count, average, released, checked_at}}. "
                    "Written only by aso/app_profiles.py; the opportunity score reads it."
                ),
            ),
        ),
    ]
