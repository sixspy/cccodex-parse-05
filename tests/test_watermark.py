import pytest
from datetime import timedelta

from django.utils import timezone

from catalog.models import (
    METHOD_COUNT,
    METHOD_MTIME,
    MODE_FULL,
    MODE_INCREMENTAL,
    AdvancePolicy,
    ParseJob,
    ParseRun,
    RUN_LOST,
    RUN_RUNNING,
    RUN_SUCCEEDED,
    SourceRow,
    Watermark,
)
from catalog.services import (
    ServiceError,
    finish_parse,
    get_today_policy,
    get_watermark,
    set_today_policy,
    start_parse,
    to_micro,
    today,
)

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("seeded_db")]


@pytest.fixture
def auth_client(client):
    client.post("/login", {"username": "admin", "password": "123456"})
    return client


def _job():
    return ParseJob.objects.order_by("id").first()


def _refresh(obj):
    obj.refresh_from_db()
    return obj


# ---------- 推进策略：每天一种、写死不改、作业中不可改 ----------

def test_policy_locked_once_set_same_day():
    set_today_policy(METHOD_MTIME)
    # 写死后当天不许改弦
    with pytest.raises(ServiceError):
        set_today_policy(METHOD_COUNT)
    policy = get_today_policy()
    assert policy.method == METHOD_MTIME
    assert policy.policy_date == today()


def test_policy_rejects_unknown_method():
    with pytest.raises(ServiceError):
        set_today_policy("bogus")


def test_policy_change_blocked_while_run_in_progress():
    set_today_policy(METHOD_MTIME)
    job = _job()
    run = start_parse(job, MODE_INCREMENTAL, METHOD_MTIME, "")
    assert run.status == RUN_RUNNING
    # 读盘仍在执行时不得改推进策略（今天已写死，且任何 running 都拦）
    with pytest.raises(ServiceError):
        set_today_policy(METHOD_COUNT)
    # 作业结束后当天仍不能改弦
    finish_parse(run)
    with pytest.raises(ServiceError):
        set_today_policy(METHOD_COUNT)


def test_policy_cannot_be_set_first_time_while_another_job_running():
    # 先以服务层直接造出一个执行中读盘，再尝试首次写策略
    job = _job()
    running = ParseRun.objects.create(
        job=job, mode=MODE_INCREMENTAL, method=METHOD_MTIME, status=RUN_RUNNING,
    )
    with pytest.raises(ServiceError):
        set_today_policy(METHOD_COUNT)
    running.status = RUN_SUCCEEDED
    running.save()
    policy = set_today_policy(METHOD_COUNT)
    assert policy.method == METHOD_COUNT


# ---------- 推进写歪：带的推进法与策略不符必须拦住 ----------

def test_parse_with_mismatched_method_is_blocked():
    set_today_policy(METHOD_MTIME)
    job = _job()
    with pytest.raises(ServiceError) as exc:
        start_parse(job, MODE_INCREMENTAL, METHOD_COUNT, "")
    assert "写歪" in str(exc.value)
    assert ParseRun.objects.count() == 0  # 拦住，不留读盘
    assert _refresh(get_watermark(job)).mtime_ruler == 0


def test_parse_without_policy_is_blocked():
    job = _job()
    with pytest.raises(ServiceError):
        start_parse(job, MODE_INCREMENTAL, METHOD_MTIME, "")


def test_view_reports_skew_message(auth_client):
    job = _job()
    set_today_policy(METHOD_MTIME)
    r = auth_client.post(
        f"/jobs/{job.id}/parse/",
        {"mode": MODE_INCREMENTAL, "method": METHOD_COUNT, "reason": ""},
    )
    assert r.status_code == 302
    body = auth_client.get(f"/jobs/{job.id}/").content.decode("utf-8")
    assert "推进写歪" in body


# ---------- 增量边界：只收尺位之后的变更 ----------

def test_incremental_mtime_only_collects_after_ruler():
    set_today_policy(METHOD_MTIME)
    job = _job()
    rows = list(job.rows.order_by("seq"))
    coded = [r for r in rows if r.has_code]
    # 先把尺推到第 3 条带编码行的变更时刻
    first = start_parse(job, MODE_FULL, METHOD_MTIME, "初始化水位尺")
    finish_parse(first)
    wm = _refresh(get_watermark(job))
    third_micro = to_micro(coded[2].updated_at)
    Watermark.objects.filter(pk=wm.pk).update(mtime_ruler=third_micro)

    run = start_parse(job, MODE_INCREMENTAL, METHOD_MTIME, "")
    finish_parse(run)
    expected_coded = [r for r in coded if to_micro(r.updated_at) > third_micro]
    # 空编码行落在尺后也会被收到，只是不推动尺
    expected_blank = [
        r for r in job.rows.all()
        if not r.has_code and to_micro(r.updated_at) > third_micro
    ]
    assert run.coded_count == len(expected_coded)
    assert run.parsed_count == len(expected_coded) + len(expected_blank)
    # 尺位恰好停在剩余带编码行的最大变更时刻
    assert _refresh(get_watermark(job)).mtime_ruler == to_micro(max(r.updated_at for r in expected_coded))


def test_incremental_count_only_collects_after_ruler():
    set_today_policy(METHOD_COUNT)
    job = _job()
    rows = list(job.rows.order_by("seq"))
    coded_count = sum(1 for r in rows if r.has_code)

    first = start_parse(job, MODE_FULL, METHOD_COUNT, "初始化水位尺")
    finish_parse(first)
    assert _refresh(get_watermark(job)).count_ruler == coded_count

    # 新增两条带编码行（行序靠后），增量只能收到这两条
    last_seq = max(r.seq for r in rows)
    base = timezone.now()
    SourceRow.objects.create(job=job, seq=last_seq + 1, code_seq=coded_count + 1, code="N-2001", updated_at=base)
    SourceRow.objects.create(job=job, seq=last_seq + 2, code_seq=coded_count + 2, code="N-2002", updated_at=base + timedelta(minutes=1))

    run = start_parse(job, MODE_INCREMENTAL, METHOD_COUNT, "")
    finish_parse(run)
    assert run.coded_count == 2
    assert _refresh(get_watermark(job)).count_ruler == coded_count + 2


def test_incremental_beyond_ruler_collects_nothing():
    set_today_policy(METHOD_COUNT)
    job = _job()
    run = start_parse(job, MODE_FULL, METHOD_COUNT, "初始化水位尺")
    finish_parse(run)
    again = start_parse(job, MODE_INCREMENTAL, METHOD_COUNT, "")
    finish_parse(again)
    assert again.parsed_count == 0
    wm = _refresh(get_watermark(job))
    assert wm.count_ruler == sum(1 for r in job.rows.all() if r.has_code)


# ---------- 全量可越尺，但必须填原因 ----------

def test_full_parse_requires_reason():
    set_today_policy(METHOD_MTIME)
    job = _job()
    with pytest.raises(ServiceError) as exc:
        start_parse(job, MODE_FULL, METHOD_MTIME, "   ")
    assert "原因" in str(exc.value)


def test_full_parse_can_jump_over_ruler_with_reason():
    set_today_policy(METHOD_MTIME)
    job = _job()
    wm = get_watermark(job)
    Watermark.objects.filter(pk=wm.pk).update(mtime_ruler=10**15)  # 人为把尺抬得很高
    run = start_parse(job, MODE_FULL, METHOD_MTIME, "源文件重放，需要全量越尺重算")
    assert run.reason != ""
    finish_parse(run)
    wm = _refresh(get_watermark(job))
    max_micro = max(to_micro(r.updated_at) for r in job.rows.all() if r.has_code)
    # 全量越过/回退尺位后，重算到源文件真实最大变更时刻
    assert wm.mtime_ruler == max_micro


# ---------- 空编码行不推尺 ----------

def test_blank_code_rows_never_move_ruler():
    set_today_policy(METHOD_COUNT)
    job = _job()
    run = start_parse(job, MODE_INCREMENTAL, METHOD_COUNT, "")
    finish_parse(run)
    # 收的行里含空编码行
    assert run.parsed_count > run.coded_count
    assert any(not r.has_code for r in job.rows.all())
    # 尺位只按带编码行数走
    assert _refresh(get_watermark(job)).count_ruler == run.coded_count


def test_blank_only_does_not_move_mtime_ruler():
    set_today_policy(METHOD_MTIME)
    job = _job()
    # 把尺抬到覆盖全部带编码行，只剩空编码行在“尺后”
    coded = [r for r in job.rows.all() if r.has_code]
    top = max(to_micro(r.updated_at) for r in coded)
    Watermark.objects.filter(job=job).update(mtime_ruler=top)
    blank = [r for r in job.rows.all() if not r.has_code][0]
    SourceRow.objects.filter(pk=blank.pk).update(
        updated_at=timezone.now() + timedelta(hours=2)
    )
    run = start_parse(job, MODE_INCREMENTAL, METHOD_MTIME, "")
    finish_parse(run)
    assert run.parsed_count == 1  # 收到那条空编码行
    assert run.coded_count == 0
    assert _refresh(get_watermark(job)).mtime_ruler == top  # 尺不动


# ---------- 争抢：同一任务只记先到 ----------

def test_contention_only_first_wins():
    set_today_policy(METHOD_COUNT)
    job = _job()
    first = start_parse(job, MODE_INCREMENTAL, METHOD_COUNT, "")
    assert first.status == RUN_RUNNING and first.won

    second = start_parse(job, MODE_FULL, METHOD_COUNT, "补一次全量")
    assert second.status == RUN_LOST
    assert second.won is False
    assert second.ruler_after == second.ruler_before  # 落败不推尺

    # 先到跑完正常推尺
    finish_parse(first)
    wm = _refresh(get_watermark(job))
    assert wm.count_ruler == first.coded_count

    logs = list(job.contention_logs.all())
    assert len(logs) == 1
    log = logs[0]
    assert log.winner_id == first.id
    assert log.loser_run_id == second.id
    assert "先到" in log.detail

    # 详情页能展开争用落败说明
    from django.test import Client
    c = Client()
    c.post("/login", {"username": "admin", "password": "123456"})
    page = c.get(f"/jobs/{job.id}/").content.decode("utf-8")
    assert "争用落败说明" in page
    assert f"#{second.id}" in page


def test_lost_run_cannot_finish_or_move_ruler():
    set_today_policy(METHOD_COUNT)
    job = _job()
    start_parse(job, MODE_INCREMENTAL, METHOD_COUNT, "")
    loser = start_parse(job, MODE_INCREMENTAL, METHOD_COUNT, "")
    before = _refresh(get_watermark(job)).count_ruler
    # 对落败 run 调 finish 应原样返回，不产生轨迹、不动尺
    finished = finish_parse(loser)
    assert finished.status == RUN_LOST
    assert _refresh(get_watermark(job)).count_ruler == before
    assert job.tracks.count() == 0


# ---------- 页面：列表分栏、详情可展开 ----------

def test_list_separates_ruler_values_and_method_name(auth_client):
    set_today_policy(METHOD_MTIME)
    body = auth_client.get("/jobs/").content.decode("utf-8")
    # 推进法名称与两个尺值分栏
    assert "尺值·最大变更时刻" in body
    assert "尺值·成功解析条数" in body
    assert "推进法名" in body
    assert "跟源文件最大变更时刻" in body
    job = _job()
    wm = get_watermark(job)
    assert str(wm.mtime_ruler) in body and str(wm.count_ruler) in body


def test_list_shows_today_parsed_and_landed(auth_client):
    set_today_policy(METHOD_COUNT)
    job = _job()
    run = start_parse(job, MODE_INCREMENTAL, METHOD_COUNT, "")
    finish_parse(run)
    body = auth_client.get("/jobs/").content.decode("utf-8")
    assert "今日已析" in body and "落档数" in body
    assert str(run.parsed_count) in body


def test_detail_shows_tracks_and_source_rows(auth_client):
    set_today_policy(METHOD_COUNT)
    job = _job()
    run = start_parse(job, MODE_INCREMENTAL, METHOD_COUNT, "")
    finish_parse(run)
    body = auth_client.get(f"/jobs/{job.id}/").content.decode("utf-8")
    assert "推进轨迹" in body
    assert "尺位前" in body and "尺位后" in body
    assert "空编码" in body and "不推尺" in body


def test_policy_form_on_list_locks_method(auth_client):
    r = auth_client.post("/policy/", {"method": METHOD_MTIME, "next": "/jobs/"})
    assert r.status_code == 302
    policy = AdvancePolicy.objects.get(policy_date=today())
    assert policy.method == METHOD_MTIME
    body = auth_client.get("/jobs/").content.decode("utf-8")
    assert "已写死" in body


def test_view_full_without_reason_shows_error(auth_client):
    set_today_policy(METHOD_MTIME)
    job = _job()
    auth_client.post(f"/jobs/{job.id}/parse/", {"mode": MODE_FULL, "method": METHOD_MTIME, "reason": ""})
    body = auth_client.get(f"/jobs/{job.id}/").content.decode("utf-8")
    assert "原因" in body
    assert ParseRun.objects.count() == 0


def test_empty_incremental_keeps_last_landed_baseline():
    set_today_policy(METHOD_COUNT)
    job = _job()
    first = start_parse(job, MODE_FULL, METHOD_COUNT, "初始化")
    finish_parse(first)
    assert job.parsed == first.parsed_count and first.parsed_count > 0
    empty = start_parse(job, MODE_INCREMENTAL, METHOD_COUNT, "")
    finish_parse(empty)
    job.refresh_from_db()
    # 空增量不把落档基准冲成 0
    assert job.parsed == first.parsed_count


def test_view_policy_change_blocked_when_running(auth_client):
    set_today_policy(METHOD_MTIME)
    job = _job()
    start_parse(job, MODE_INCREMENTAL, METHOD_MTIME, "")  # 留下执行中读盘
    r = auth_client.post("/policy/", {"method": METHOD_COUNT, "next": "/jobs/"}, follow=True)
    body = r.content.decode("utf-8")
    assert "执行" in body and ("不得改" in body or "不许改" in body)
    assert get_today_policy().method == METHOD_MTIME


def test_ruler_values_are_clickable_links(auth_client):
    body = auth_client.get("/jobs/").content.decode("utf-8")
    job = _job()
    # 尺值单元格里的读数本身是可点链接
    assert f'href="/jobs/{job.id}/">{get_watermark(job).mtime_ruler}</a>' in body

