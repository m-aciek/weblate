# Copyright © Michal Čihař <michal@weblate.org>
#
# SPDX-License-Identifier: GPL-3.0-or-later

# Generated by Django 4.2.3 on 2023-08-04 10:42

from django.db import migrations

from weblate.utils.fields import migrate_json_field


def trans_jsonfield(apps, schema_editor):
    db_alias = schema_editor.connection.alias
    migrate_json_field(apps.get_model("trans", "Alert"), db_alias, "details")
    migrate_json_field(apps.get_model("trans", "Change"), db_alias, "details")
    migrate_json_field(apps.get_model("trans", "Comment"), db_alias, "userdetails")
    migrate_json_field(
        apps.get_model("trans", "Component"),
        db_alias,
        "enforced_checks",
    )
    migrate_json_field(apps.get_model("trans", "Suggestion"), db_alias, "userdetails")


class Migration(migrations.Migration):
    dependencies = [
        ("trans", "0175_alert_details_new_change_details_new_and_more"),
    ]

    operations = [migrations.RunPython(trans_jsonfield, elidable=True)]
