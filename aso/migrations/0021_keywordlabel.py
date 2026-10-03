import django.db.models.deletion
from django.db import migrations, models

# The user's keyword tags became labels (the Insight badges are already
# "Insight tags"; KEYWORD_LABELS_PLAN.md). The Dashboard's change-count
# triggers on the old table are dropped first: SQLite may leave them pointing
# at the old name through the rename, and aso/apps.py creates them again for
# the new table after every migrate.
OLD_TRIGGERS = [f"aso_keywordtag_history_revision_{op}" for op in ("insert", "update", "delete")]


class Migration(migrations.Migration):

    dependencies = [
        ('aso', '0020_keywordtag'),
        # RenameModel also renames the model's content type; rolled back, that
        # step must see the content type table as it is today (no name column).
        ('contenttypes', '0002_remove_content_type_name'),
    ]

    operations = [
        migrations.RunSQL(
            [f"DROP TRIGGER IF EXISTS {name}" for name in OLD_TRIGGERS],
            reverse_sql=migrations.RunSQL.noop,
        ),
        migrations.RenameModel('KeywordTag', 'KeywordLabel'),
        migrations.AlterField(
            model_name='keywordlabel',
            name='keyword',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='labels', to='aso.keyword'),
        ),
        migrations.RenameIndex(
            model_name='keywordlabel',
            new_name='aso_keyword_name_4dda21_idx',
            old_name='aso_keyword_name_80645e_idx',
        ),
    ]
