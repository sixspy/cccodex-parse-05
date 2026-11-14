from django.conf import settings
from django.contrib import messages
from django.http import HttpResponseRedirect
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods, require_POST

from catalog.models import (
    METHOD_CHOICES,
    METHOD_LABELS,
    RUN_RUNNING,
    ParseJob,
)
from catalog.services import (
    ServiceError,
    finish_parse,
    get_today_policy,
    get_watermark,
    has_running_run,
    set_today_policy,
    start_parse,
    today,
)


def _authed(request) -> bool:
    return request.session.get("user") == settings.ADMIN_USER


def root(request):
    return HttpResponseRedirect("/jobs/" if _authed(request) else "/login")


@require_http_methods(["GET", "POST"])
def login_page(request):
    err = ""
    if request.method == "POST":
        user = request.POST.get("username", "").strip()
        pwd = request.POST.get("password", "")
        if user == settings.ADMIN_USER and pwd == settings.ADMIN_PASSWORD:
            request.session["user"] = user
            return HttpResponseRedirect("/jobs/")
        err = "账号或密码错误"
    return render(request, "login.html", {"error": err, "lab": settings.LAB_NAME})


def logout_view(request):
    request.session.flush()
    return HttpResponseRedirect("/login")


def _job_summary(job, policy):
    wm = get_watermark(job)
    runs = list(job.runs.all())
    local_today = today()
    today_runs = [
        r for r in runs
        if r.status == "succeeded" and r.won and timezone.localdate(r.created_at) == local_today
    ]
    return {
        "job": job,
        "wm": wm,
        "active_method": policy.method if policy else "",
        "active_method_name": policy.method_name if policy else "今日未写定",
        "today_parsed": sum(r.parsed_count for r in today_runs),
        "landed": job.landed,
        "running": any(r.status == RUN_RUNNING for r in runs),
    }


def job_list(request):
    if not _authed(request):
        return HttpResponseRedirect("/login")
    policy = get_today_policy()
    rows = [_job_summary(job, policy) for job in ParseJob.objects.all()]
    return render(request, "jobs_list.html", {
        "rows": rows,
        "policy": policy,
        "method_choices": METHOD_CHOICES,
        "any_running": has_running_run(),
        "lab": settings.LAB_NAME,
        "user": request.session["user"],
    })


def job_detail(request, job_id: int):
    if not _authed(request):
        return HttpResponseRedirect("/login")
    job = get_object_or_404(ParseJob, pk=job_id)
    wm = get_watermark(job)
    policy = get_today_policy()
    runs = list(job.runs.all())
    running = any(r.status == RUN_RUNNING for r in runs)
    return render(request, "job_detail.html", {
        "job": job,
        "wm": wm,
        "policy": policy,
        "method_choices": METHOD_CHOICES,
        "method_labels": METHOD_LABELS,
        "runs": runs,
        "tracks": list(job.tracks.all()),
        "contention_logs": list(job.contention_logs.all()),
        "source_rows": list(job.rows.all()),
        "running": running,
        "lab": settings.LAB_NAME,
        "user": request.session["user"],
    })


@require_POST
def job_parse(request, job_id: int):
    if not _authed(request):
        return HttpResponseRedirect("/login")
    job = get_object_or_404(ParseJob, pk=job_id)
    mode = request.POST.get("mode", "")
    method = request.POST.get("method", "")
    reason = request.POST.get("reason", "")
    try:
        run = start_parse(job, mode, method, reason)
        if not run.won:
            messages.warning(request, f"读盘 #{run.id} 争抢落败：同一任务只记先到，本次未收行未推尺。")
        else:
            # 本实验台读盘为同步模拟：起盘即跑完，保持“执行中”状态的窗口由并发测试覆盖
            finish_parse(run)
            messages.success(
                request,
                f"读盘 #{run.id} 完成（{run.mode_name} · {run.method_name}），"
                f"收 {run.parsed_count} 行，尺位 {run.ruler_before} → {run.ruler_after}。",
            )
    except ServiceError as exc:
        messages.error(request, str(exc))
    return HttpResponseRedirect(f"/jobs/{job.id}/")


@require_POST
def job_land(request, job_id: int):
    if not _authed(request):
        return HttpResponseRedirect("/login")
    job = get_object_or_404(ParseJob, pk=job_id)
    if job.status == "已解析待入库":
        job.landed = job.parsed
        job.status = "已入库"
        job.save(update_fields=["landed", "status"])
        messages.success(request, f"已落档 {job.landed} 条。")
    else:
        messages.error(request, "当前状态不允许入库，需先成功读盘解析。")
    return HttpResponseRedirect(f"/jobs/{job.id}/")


@require_POST
def policy_set(request):
    if not _authed(request):
        return HttpResponseRedirect("/login")
    method = request.POST.get("method", "")
    try:
        policy = set_today_policy(method)
        messages.success(request, f"今日推进策略已写死为「{policy.method_name}」，当天不许改弦。")
    except ServiceError as exc:
        messages.error(request, str(exc))
    nxt = request.POST.get("next") or "/jobs/"
    return HttpResponseRedirect(nxt)
