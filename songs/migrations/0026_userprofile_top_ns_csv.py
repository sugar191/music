from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("songs", "0025_move_aliases_out_of_credits"),
    ]

    operations = [
        migrations.AddField(
            model_name="userprofile",
            name="top_ns_csv",
            field=models.CharField(
                default="5,10,15,20",
                help_text="カンマ区切り。例: 5,10,15,20",
                max_length=64,
                verbose_name="表示するTOP",
            ),
        ),
    ]
