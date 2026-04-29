# Generated for year ranking feature

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('songs', '0013_song_lyricist_composer'),
    ]

    operations = [
        migrations.AddField(
            model_name='song',
            name='year',
            field=models.IntegerField(blank=True, null=True),
        ),
        migrations.AddIndex(
            model_name='song',
            index=models.Index(fields=['year'], name='songs_song_year_idx'),
        ),
    ]
