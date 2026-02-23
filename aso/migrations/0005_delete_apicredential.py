# Generated manually — Apple Search Ads credentials removed (adds zero value)

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('aso', '0004_apicredential_org_id'),
    ]

    operations = [
        migrations.DeleteModel(
            name='ApiCredential',
        ),
    ]
