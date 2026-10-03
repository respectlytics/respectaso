from django.db import migrations, models

# The triggers themselves are created after every migrate (aso/apps.py,
# aso/history_revision.py). Rolling this migration back must drop them
# first: left behind, they would write to a table that no longer exists and
# every insert, update and delete on these tables would fail.
TRIGGERS = [
    f"{table}_history_revision_{operation}"
    for table in ("aso_searchresult", "aso_keyword", "aso_app")
    for operation in ("insert", "update", "delete")
]


class Migration(migrations.Migration):

    dependencies = [
        ('aso', '0018_help_text_reads_without_dashes'),
    ]

    operations = [
        migrations.CreateModel(
            name='HistoryRevision',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('value', models.BigIntegerField(default=0)),
            ],
        ),
        migrations.RunSQL(
            migrations.RunSQL.noop,
            reverse_sql=[f"DROP TRIGGER IF EXISTS {name}" for name in TRIGGERS],
        ),
    ]
