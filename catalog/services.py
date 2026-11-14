"""水位尺与读盘推进的全部业务规则都收口在这里。

规则要点：
- 当日推进策略只许定一种推进法，写死后当天不改；读盘执行中禁止改策略。
- 读盘带上的推进法必须与当日策略一致，否则判定“推进写歪”并拦住。
- 全量可越过水位尺，但必须填原因；增量只收尺位之后的变更。
- 编码空着的行照样可读，但不推动尺。
- 同一读盘任务争抢只记先到，后到留争抢落败说明。
"""

from django.db import transaction
from django.utils import timezone

from catalog.models import (
    AdvancePolicy,
    AdvanceTrack,
    ContentionLog,
    METHOD_CHOICES,
    METHOD_MTIME,
    MODE_FULL,
    MODE_INCREMENTAL,
    ParseRun,
    RUN_LOST,
    RUN_RUNNING,
    RUN_SUCCEEDED,
    Watermark,
)

METHOD_VALUES = {m for m, _ in METHOD_CHOICES}
MODE_VALUES = {MODE_FULL, MODE_INCREMENTAL}


class ServiceError(Exception):
    """业务规则被违反时抛出，消息可直接上页面。"""


def today():
    return timezone.localdate()


def now():
    return timezone.now()


def to_micro(dt):
    return int(dt.timestamp() * 1_000_000)


def get_today_policy():
    return AdvancePolicy.objects.filter(policy_date=today()).first()


def has_running_run(exclude_job=None):
    qs = ParseRun.objects.filter(status=RUN_RUNNING)
    if exclude_job is not None:
        qs = qs.exclude(job_id=exclude_job.id)
    return qs.exists()


def set_today_policy(method):
    """写定当日推进策略。当天已写定则只能读不能改；有读盘在执行时不许写。"""
    if method not in METHOD_VALUES:
        raise ServiceError("推进法只能二选一：跟源文件最大变更时刻，或跟本任务上次成功解析条数")
    if has_running_run():
        raise ServiceError("读盘仍在执行时不得改推进策略")
    existing = get_today_policy()
    if existing is not None:
        if existing.method != method:
            raise ServiceError("当日推进策略已写死，当天不许改弦")
        return existing
    return AdvancePolicy.objects.create(policy_date=today(), method=method)


def get_watermark(job):
    # 显式用 job_id：OneToOneField 的反向描述符同名，Watermark(job=...) 会被截胡
    wm, _ = Watermark.objects.get_or_create(job_id=job.pk)
    return wm


def _coded_rows(job):
    return [r for r in job.rows.all() if r.has_code]


def _window_rows(job, mode, method, ruler):
    """圈定本次读盘收的行。全量收全部；增量只收尺位之后。"""
    rows = list(job.rows.all())
    if mode == MODE_FULL:
        return rows
    if method == METHOD_MTIME:
        return [r for r in rows if to_micro(r.updated_at) > ruler]
    # count 法：尺位 = 已吃掉的非空编码行数；带编码行有独立 code_seq，
    # code_seq > ruler 即尺后新编码行；空编码行按行序落在最后已吃编码行之后才算尺后
    coded = sorted(_coded_rows(job), key=lambda r: r.seq)
    if ruler >= len(coded):
        return []
    last_eaten_seq = coded[ruler - 1].seq if ruler > 0 else 0
    return [
        r for r in rows
        if (r.has_code and r.code_seq > ruler) or (not r.has_code and r.seq > last_eaten_seq)
    ]


def start_parse(job, mode, method, reason):
    """起一次读盘：校验规则、处理争抢，返回处于执行中或已落败的 run。"""
    if mode not in MODE_VALUES:
        raise ServiceError("读盘方式只能是全量或增量")
    if method not in METHOD_VALUES:
        raise ServiceError("推进法只能二选一：跟源文件最大变更时刻，或跟本任务上次成功解析条数")

    policy = get_today_policy()
    if policy is None:
        raise ServiceError("今日推进策略尚未写定，请先写定本日推进法再点读盘")
    if policy.method != method:
        raise ServiceError(
            "水位尺推进写歪了：本次读盘带的推进法与今日推进策略不符，"
            f"今日策略为「{policy.method_name}」"
        )

    if mode == MODE_FULL and not reason.strip():
        raise ServiceError("全量读盘可以越过水位尺，但必须填写越尺原因")
    if mode == MODE_INCREMENTAL and reason.strip():
        # 增量不需要原因，留着无害，统一落库时清洗即可
        reason = ""

    wm = get_watermark(job)
    ruler_before = wm.ruler_for(method)

    with transaction.atomic():
        winner = (
            ParseRun.objects.select_for_update()
            .filter(job=job, status=RUN_RUNNING)
            .order_by("id")
            .first()
        )
        run = ParseRun(
            job=job,
            mode=mode,
            method=method,
            reason=reason.strip(),
            status=RUN_RUNNING,
            ruler_before=ruler_before,
        )
        if winner is not None:
            # 同一读盘任务争抢：只记先到，后到直接落败留痕
            run.won = False
            run.status = RUN_LOST
            run.ruler_after = ruler_before
            run.note = f"争抢落败：先到读盘 #{winner.id} 仍在执行，本次不推动水位尺"
            run.finished_at = now()
            run.save()
            ContentionLog.objects.create(
                job=job,
                winner=winner,
                loser_run=run,
                mode=mode,
                method=method,
                detail=(
                    f"同一读盘任务争抢，只记先到：先到读盘 #{winner.id}（{winner.mode_name}）"
                    f"持尺执行中，后到读盘 #{run.id} 落败，未收行未推尺"
                ),
            )
            return run
        run.save()
    return run


def finish_parse(run):
    """把执行中的读盘跑完：收行、算尺位、落推进轨迹。就地更新并返回传入对象。"""
    with transaction.atomic():
        locked = ParseRun.objects.select_for_update().get(pk=run.pk)
        if locked.status != RUN_RUNNING:
            return run

        job = run.job
        wm = get_watermark(job)
        window = _window_rows(job, run.mode, run.method, run.ruler_before)
        coded_window = [r for r in window if r.has_code]
        blank_window = [r for r in window if not r.has_code]

        if run.method == METHOD_MTIME:
            pushed = max((to_micro(r.updated_at) for r in coded_window), default=run.ruler_before)
            ruler_after = max(run.ruler_before, pushed)
            if coded_window:
                detail = (
                    f"按最大变更时刻推尺：收 {len(coded_window)} 条带编码行、"
                    f"{len(blank_window)} 条空编码行，尺位 {run.ruler_before} → {ruler_after}"
                )
            else:
                detail = f"尺位之后无带编码变更，{len(blank_window)} 条空编码行不推尺，尺位维持 {ruler_after}"
        else:
            coded_total = len(_coded_rows(job))
            if run.mode == MODE_FULL:
                ruler_after = coded_total
            else:
                ruler_after = run.ruler_before + len(coded_window)
            if coded_window or blank_window:
                detail = (
                    f"按上次成功解析条数推尺：收 {len(coded_window)} 条带编码行、"
                    f"{len(blank_window)} 条空编码行，尺位 {run.ruler_before} → {ruler_after}"
                )
            else:
                detail = f"尺位之后无新行，尺位维持 {ruler_after}"

        run.parsed_count = len(window)
        run.coded_count = len(coded_window)
        run.ruler_after = ruler_after
        run.status = RUN_SUCCEEDED
        run.finished_at = now()
        run.save()

        wm.set_ruler(run.method, ruler_after)
        wm.save(update_fields=["mtime_ruler", "count_ruler", "updated_at"])

        AdvanceTrack.objects.create(
            job=job,
            run=run,
            mode=run.mode,
            method=run.method,
            ruler_before=run.ruler_before,
            ruler_after=ruler_after,
            detail=detail,
        )

        # 空增量不产生新解析结果，保留上次有效解析数，避免把落档基准冲成 0
        if run.parsed_count > 0 or not job.runs.exclude(pk=run.pk).filter(status=RUN_SUCCEEDED).exists():
            job.parsed = run.parsed_count
        job.status = "已解析待入库"
        job.save(update_fields=["parsed", "status"])
        return run
