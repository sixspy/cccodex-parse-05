from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("catalog", "0001_initial"),
    ]
    operations = [
        migrations.CreateModel(
            name="Watermark",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("mtime_ruler", models.BigIntegerField(default=0)),
                ("count_ruler", models.BigIntegerField(default=0)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("job", models.OneToOneField(on_delete=models.deletion.CASCADE, related_name="watermark", to="catalog.parsejob")),
            ],
            options={"ordering": ["job_id"]},
        ),
        migrations.CreateModel(
            name="AdvancePolicy",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("policy_date", models.DateField(unique=True)),
                ("method", models.CharField(choices=[("mtime", "跟源文件最大变更时刻"), ("count", "跟本任务上次成功解析条数")], max_length=16)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("locked_at", models.DateTimeField(auto_now=True)),
            ],
            options={"ordering": ["-policy_date"]},
        ),
        migrations.CreateModel(
            name="SourceRow",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("seq", models.IntegerField()),
                ("code_seq", models.IntegerField(default=0)),
                ("code", models.CharField(blank=True, default="", max_length=64)),
                ("updated_at", models.DateTimeField()),
                ("job", models.ForeignKey(on_delete=models.deletion.CASCADE, related_name="rows", to="catalog.parsejob")),
            ],
            options={"ordering": ["job_id", "seq"], "unique_together": {("job", "seq")}},
        ),
        migrations.CreateModel(
            name="ParseRun",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("mode", models.CharField(choices=[("full", "全量"), ("incremental", "增量")], max_length=16)),
                ("method", models.CharField(choices=[("mtime", "跟源文件最大变更时刻"), ("count", "跟本任务上次成功解析条数")], max_length=16)),
                ("reason", models.CharField(blank=True, default="", max_length=255)),
                ("status", models.CharField(choices=[("running", "执行中"), ("succeeded", "成功"), ("failed", "失败"), ("lost", "争抢落败")], default="running", max_length=16)),
                ("won", models.BooleanField(default=True)),
                ("parsed_count", models.IntegerField(default=0)),
                ("coded_count", models.IntegerField(default=0)),
                ("ruler_before", models.BigIntegerField(default=0)),
                ("ruler_after", models.BigIntegerField(default=0)),
                ("note", models.CharField(blank=True, default="", max_length=255)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("finished_at", models.DateTimeField(blank=True, null=True)),
                ("job", models.ForeignKey(on_delete=models.deletion.CASCADE, related_name="runs", to="catalog.parsejob")),
            ],
            options={"ordering": ["-id"]},
        ),
        migrations.CreateModel(
            name="AdvanceTrack",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("mode", models.CharField(choices=[("full", "全量"), ("incremental", "增量")], max_length=16)),
                ("method", models.CharField(choices=[("mtime", "跟源文件最大变更时刻"), ("count", "跟本任务上次成功解析条数")], max_length=16)),
                ("ruler_before", models.BigIntegerField(default=0)),
                ("ruler_after", models.BigIntegerField(default=0)),
                ("detail", models.CharField(max_length=255)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("job", models.ForeignKey(on_delete=models.deletion.CASCADE, related_name="tracks", to="catalog.parsejob")),
                ("run", models.ForeignKey(blank=True, null=True, on_delete=models.deletion.SET_NULL, related_name="tracks", to="catalog.parserun")),
            ],
            options={"ordering": ["-id"]},
        ),
        migrations.CreateModel(
            name="ContentionLog",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("mode", models.CharField(choices=[("full", "全量"), ("incremental", "增量")], max_length=16)),
                ("method", models.CharField(choices=[("mtime", "跟源文件最大变更时刻"), ("count", "跟本任务上次成功解析条数")], max_length=16)),
                ("detail", models.CharField(max_length=255)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("job", models.ForeignKey(on_delete=models.deletion.CASCADE, related_name="contention_logs", to="catalog.parsejob")),
                ("winner", models.ForeignKey(blank=True, null=True, on_delete=models.deletion.SET_NULL, related_name="won_contention_logs", to="catalog.parserun")),
                ("loser_run", models.OneToOneField(blank=True, null=True, on_delete=models.deletion.SET_NULL, related_name="contention_record", to="catalog.parserun")),
            ],
            options={"ordering": ["-id"]},
        ),
    ]
