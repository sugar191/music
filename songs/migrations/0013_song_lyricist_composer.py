# Generated for lyricist/composer ranking feature

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('songs', '0012_userprofile'),
    ]

    operations = [
        migrations.AddField(
            model_name='song',
            name='lyricist',
            field=models.CharField(blank=True, max_length=200, null=True),
        ),
        migrations.AddField(
            model_name='song',
            name='composer',
            field=models.CharField(blank=True, max_length=200, null=True),
        ),
        migrations.AddIndex(
            model_name='song',
            index=models.Index(fields=['lyricist'], name='songs_song_lyricis_idx'),
        ),
        migrations.AddIndex(
            model_name='song',
            index=models.Index(fields=['composer'], name='songs_song_compose_idx'),
        ),
    ]
