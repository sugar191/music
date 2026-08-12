"""
UserProfile.user を ForeignKey から OneToOneField に変更する。

ForeignKey のままだと同じユーザーのプロフィールを複数作れてしまい、
どれが使われるかは取得順まかせだった（参照側は .filter(...).first()）。

OneToOneField は DB に UNIQUE 制約を張るため、既存データに同一ユーザーの
重複行が残っているとマイグレーションが失敗する。そこで AlterField の前に
重複を1件へ寄せる。残すのは「birth_year が入っている行のうち最小id」、
どの行も未入力なら単純に最小id。
"""

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


def dedupe_userprofiles(apps, schema_editor):
    UserProfile = apps.get_model("songs", "UserProfile")

    seen_user_ids = (
        UserProfile.objects.values_list("user_id", flat=True).distinct()
    )
    for user_id in list(seen_user_ids):
        rows = list(UserProfile.objects.filter(user_id=user_id).order_by("id"))
        if len(rows) <= 1:
            continue

        # birth_year が入っている行を優先して残す
        keep = next((r for r in rows if r.birth_year is not None), rows[0])
        UserProfile.objects.filter(user_id=user_id).exclude(id=keep.id).delete()


def noop(apps, schema_editor):
    """重複の削除は元に戻せないが、逆方向マイグレーション自体は許可する。"""


class Migration(migrations.Migration):

    dependencies = [
        ("songs", "0019_delete_artistsongview"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.RunPython(dedupe_userprofiles, noop),
        migrations.AlterField(
            model_name="userprofile",
            name="user",
            field=models.OneToOneField(
                on_delete=django.db.models.deletion.CASCADE,
                to=settings.AUTH_USER_MODEL,
            ),
        ),
    ]
