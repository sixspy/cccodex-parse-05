from datetime import datetime, timedelta

from django.utils import timezone

from catalog.models import ParseJob, SourceRow, Watermark

SEEDS = [
    ("活动页字段抽取", "promo_html", "待解析", "https://mock.local/promo"),
    ("报价单表格解析", "quote_xlsx", "待解析", "file://drops/quote.xlsx"),
    ("公告正文抽取", "notice_html", "待解析", "https://mock.local/notice"),
    ("目录索引解析", "index_json", "待解析", "https://mock.local/index.json"),
]

# 每个任务的源文件行：(行序, 编码, 距基准时刻的分钟增量)，编码留空即空编码行
ROW_PLAN = [
    (1, "P-1001", 5),
    (2, "P-1002", 12),
    (3, "", 18),
    (4, "P-1003", 26),
    (5, "P-1004", 34),
    (6, "", 41),
    (7, "P-1005", 52),
]

BASE_TIME = datetime(2026, 11, 14, 8, 0, 0)


def ensure_seed():
    if ParseJob.objects.count():
        return
    base = timezone.make_aware(BASE_TIME)
    for title, code, status, uri in SEEDS:
        job = ParseJob.objects.create(title=title, code=code, status=status, source_uri=uri)
        Watermark.objects.create(job_id=job.pk)
        code_seq = 0
        for seq, row_code, offset in ROW_PLAN:
            if row_code:
                code_seq += 1
            SourceRow.objects.create(
                job=job,
                seq=seq,
                code_seq=code_seq if row_code else 0,
                code=row_code,
                updated_at=base + timedelta(minutes=offset),
            )
