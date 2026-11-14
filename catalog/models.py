from django.db import models

# 推进法：跟源文件最大变更时刻走 / 跟本任务上次成功解析条数走
METHOD_MTIME = "mtime"
METHOD_COUNT = "count"
METHOD_CHOICES = [
    (METHOD_MTIME, "跟源文件最大变更时刻"),
    (METHOD_COUNT, "跟本任务上次成功解析条数"),
]
METHOD_LABELS = dict(METHOD_CHOICES)

MODE_FULL = "full"
MODE_INCREMENTAL = "incremental"
MODE_CHOICES = [
    (MODE_FULL, "全量"),
    (MODE_INCREMENTAL, "增量"),
]
MODE_LABELS = dict(MODE_CHOICES)

RUN_RUNNING = "running"
RUN_SUCCEEDED = "succeeded"
RUN_FAILED = "failed"
RUN_LOST = "lost"
RUN_STATUS_CHOICES = [
    (RUN_RUNNING, "执行中"),
    (RUN_SUCCEEDED, "成功"),
    (RUN_FAILED, "失败"),
    (RUN_LOST, "争抢落败"),
]
RUN_STATUS_LABELS = dict(RUN_STATUS_CHOICES)


class ParseJob(models.Model):
    title = models.CharField(max_length=120)
    code = models.CharField(max_length=64, unique=True)
    status = models.CharField(max_length=32, default="待解析")
    source_uri = models.CharField(max_length=255)
    parsed = models.IntegerField(default=0)
    landed = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["id"]


class Watermark(models.Model):
    """一把水位尺：两种推进法各存一个尺位，当天由推进策略决定读哪个。"""

    job = models.OneToOneField(ParseJob, on_delete=models.CASCADE, related_name="watermark")
    # 跟源文件最大变更时刻：存变更时刻的微秒时间戳
    mtime_ruler = models.BigIntegerField(default=0)
    # 跟本任务上次成功解析条数：存累计成功解析（非空编码）条数
    count_ruler = models.BigIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["job_id"]

    def ruler_for(self, method):
        return self.mtime_ruler if method == METHOD_MTIME else self.count_ruler

    def set_ruler(self, method, value):
        if method == METHOD_MTIME:
            self.mtime_ruler = value
        else:
            self.count_ruler = value


class AdvancePolicy(models.Model):
    """当日推进策略：每天只许定一种推进法，写死后当天不得改弦。"""

    policy_date = models.DateField(unique=True)
    method = models.CharField(max_length=16, choices=METHOD_CHOICES)
    created_at = models.DateTimeField(auto_now_add=True)
    locked_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-policy_date"]

    @property
    def method_name(self):
        return METHOD_LABELS.get(self.method, self.method)


class SourceRow(models.Model):
    """源文件里的一行：seq 为稳定行序，code_seq 为带编码行专属序号（空行为 0），
    updated_at 为该行变更时刻，code 可空。"""

    job = models.ForeignKey(ParseJob, on_delete=models.CASCADE, related_name="rows")
    seq = models.IntegerField()
    code_seq = models.IntegerField(default=0)
    code = models.CharField(max_length=64, blank=True, default="")
    updated_at = models.DateTimeField()

    class Meta:
        ordering = ["job_id", "seq"]
        unique_together = [("job", "seq")]

    @property
    def has_code(self):
        return bool(self.code.strip())


class ParseRun(models.Model):
    """一次读盘执行。"""

    job = models.ForeignKey(ParseJob, on_delete=models.CASCADE, related_name="runs")
    mode = models.CharField(max_length=16, choices=MODE_CHOICES)
    method = models.CharField(max_length=16, choices=METHOD_CHOICES)
    reason = models.CharField(max_length=255, blank=True, default="")
    status = models.CharField(max_length=16, choices=RUN_STATUS_CHOICES, default=RUN_RUNNING)
    won = models.BooleanField(default=True)
    parsed_count = models.IntegerField(default=0)
    coded_count = models.IntegerField(default=0)
    ruler_before = models.BigIntegerField(default=0)
    ruler_after = models.BigIntegerField(default=0)
    note = models.CharField(max_length=255, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-id"]

    @property
    def mode_name(self):
        return MODE_LABELS.get(self.mode, self.mode)

    @property
    def method_name(self):
        return METHOD_LABELS.get(self.method, self.method)

    @property
    def status_name(self):
        return RUN_STATUS_LABELS.get(self.status, self.status)


class AdvanceTrack(models.Model):
    """水位尺推进轨迹，读盘详情里可展开。"""

    job = models.ForeignKey(ParseJob, on_delete=models.CASCADE, related_name="tracks")
    run = models.ForeignKey(ParseRun, on_delete=models.SET_NULL, null=True, blank=True, related_name="tracks")
    mode = models.CharField(max_length=16, choices=MODE_CHOICES)
    method = models.CharField(max_length=16, choices=METHOD_CHOICES)
    ruler_before = models.BigIntegerField(default=0)
    ruler_after = models.BigIntegerField(default=0)
    detail = models.CharField(max_length=255)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-id"]

    @property
    def mode_name(self):
        return MODE_LABELS.get(self.mode, self.mode)

    @property
    def method_name(self):
        return METHOD_LABELS.get(self.method, self.method)


class ContentionLog(models.Model):
    """同一读盘任务争抢时，落败一方的说明，只记先到、后到留痕。"""

    job = models.ForeignKey(ParseJob, on_delete=models.CASCADE, related_name="contention_logs")
    winner = models.ForeignKey(ParseRun, on_delete=models.SET_NULL, null=True, blank=True, related_name="won_contention_logs")
    loser_run = models.OneToOneField(ParseRun, on_delete=models.SET_NULL, null=True, blank=True, related_name="contention_record")
    mode = models.CharField(max_length=16, choices=MODE_CHOICES)
    method = models.CharField(max_length=16, choices=METHOD_CHOICES)
    detail = models.CharField(max_length=255)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-id"]

    @property
    def mode_name(self):
        return MODE_LABELS.get(self.mode, self.mode)

    @property
    def method_name(self):
        return METHOD_LABELS.get(self.method, self.method)
