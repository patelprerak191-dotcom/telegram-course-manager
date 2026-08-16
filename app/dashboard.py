"""Private web dashboard for the single-owner Telegram Course Manager."""

from __future__ import annotations

import hmac
import hashlib
import os
import re
import time
from datetime import datetime, timedelta, timezone
from collections import defaultdict

from fastapi import APIRouter, Depends, HTTPException, Body, UploadFile, File, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response, RedirectResponse
from aiogram import Bot
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from app.database.supabase_client import supabase

router = APIRouter(prefix="/dashboard", tags=["dashboard"])
def _dashboard_username() -> str:
    return os.getenv("DASHBOARD_ADMIN_USERNAME", os.getenv("ADMIN_DASHBOARD_USERNAME", "admin"))

def _dashboard_password() -> str | None:
    return os.getenv("DASHBOARD_ADMIN_PASSWORD", os.getenv("ADMIN_DASHBOARD_PASSWORD"))

def _session_secret() -> str:
    return os.getenv("DASHBOARD_SESSION_SECRET") or f"{_dashboard_username()}::{_dashboard_password() or ''}"

def _make_session(username: str) -> str:
    payload = f"{username}|{int(time.time())}"
    signature = hmac.new(_session_secret().encode(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}|{signature}"

def _valid_session(value: str | None) -> str | None:
    if not value:
        return None
    try:
        username, issued, signature = value.rsplit("|", 2)
        payload = f"{username}|{issued}"
        expected = hmac.new(_session_secret().encode(), payload.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected):
            return None
        if not hmac.compare_digest(username, _dashboard_username()):
            return None
        return username
    except (ValueError, TypeError):
        return None

def require_dashboard_auth(request: Request) -> str:
    username = _valid_session(request.cookies.get("dashboard_session"))
    if username:
        return username
    if request.url.path in {"/dashboard", "/dashboard/"}:
        raise HTTPException(status_code=303, headers={"Location": "/dashboard/login"})
    raise HTTPException(status_code=401, detail="Dashboard login required.")


def rows(table: str, select: str, *, limit: int = 5000):
    response = supabase.table(table).select(select).limit(limit).execute()
    return response.data or []


def log_activity(action: str, entity_type: str | None = None, entity_id: str | None = None, description: str = "", metadata: dict | None = None):
    """Best-effort audit logging. Missing table must never break existing operations."""
    try:
        supabase.table("dashboard_activity_logs").insert({
            "action": action,
            "entity_type": entity_type,
            "entity_id": str(entity_id) if entity_id is not None else None,
            "description": description,
            "metadata": metadata or {},
        }).execute()
    except Exception as exc:
        print("Dashboard activity log skipped:", repr(exc))


def _parse_dt(value):
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt
    except (TypeError, ValueError):
        return None


@router.get("/api/analytics")
async def dashboard_analytics(_: str = Depends(require_dashboard_auth)):
    now = datetime.now(timezone.utc)
    users = rows("users", "id", limit=10000)
    subs = rows("subscriptions", "id,status,is_lifetime,expires_at", limit=20000)
    payments = rows("payment_requests", "id,amount,currency,status,submitted_at", limit=20000)
    approved = [p for p in payments if p.get("status") == "approved"]
    def revenue_since(start):
        total = 0.0
        count = 0
        for p in approved:
            dt = _parse_dt(p.get("submitted_at"))
            if dt and dt >= start:
                try: total += float(p.get("amount") or 0)
                except (TypeError, ValueError): pass
                count += 1
        return round(total, 2), count
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week_start = today_start - timedelta(days=today_start.weekday())
    month_start = today_start.replace(day=1)
    all_revenue = sum(float(p.get("amount") or 0) for p in approved)
    today, today_count = revenue_since(today_start)
    week, week_count = revenue_since(week_start)
    month, month_count = revenue_since(month_start)
    return {
        "customers": len(users),
        "active_subscriptions": sum(1 for s in subs if s.get("status") == "active"),
        "pending_payments": sum(1 for p in payments if p.get("status") == "pending"),
        "revenue": {"today": today, "week": week, "month": month, "all_time": round(all_revenue, 2)},
        "payment_counts": {"today": today_count, "week": week_count, "month": month_count, "all_time": len(approved)},
        "currency": next((p.get("currency") for p in approved if p.get("currency")), "INR"),
    }


@router.get("/api/activity")
async def dashboard_activity(_: str = Depends(require_dashboard_auth)):
    try:
        response = supabase.table("dashboard_activity_logs").select("id,action,entity_type,entity_id,description,metadata,created_at").order("created_at", desc=True).limit(100).execute()
        return response.data or []
    except Exception as exc:
        print("Dashboard activity read error:", repr(exc))
        return []


def build_stats() -> dict:
    now = datetime.now(timezone.utc)
    seven_days = now + timedelta(days=7)
    users = rows("users", "id", limit=10000)
    subscriptions = rows("subscriptions", "id,status,is_lifetime,expires_at", limit=10000)
    payments = rows("payment_requests", "id,amount,currency,status,course_id,plan_id,submitted_at", limit=10000)
    active_subs = [r for r in subscriptions if r.get("status") == "active"]
    expired_subs = [r for r in subscriptions if r.get("status") == "expired"]
    lifetime = [r for r in active_subs if r.get("is_lifetime") is True]
    expiring = 0
    for sub in active_subs:
        if sub.get("is_lifetime") or not sub.get("expires_at"):
            continue
        try:
            dt = datetime.fromisoformat(str(sub["expires_at"]).replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            if now <= dt <= seven_days:
                expiring += 1
        except (TypeError, ValueError):
            pass
    approved = [p for p in payments if p.get("status") == "approved"]
    pending = [p for p in payments if p.get("status") == "pending"]
    revenue = 0.0
    month_revenue = 0.0
    for payment in approved:
        try:
            amount = float(payment.get("amount") or 0)
        except (TypeError, ValueError):
            amount = 0.0
        revenue += amount
        submitted = payment.get("submitted_at")
        if submitted:
            try:
                dt = datetime.fromisoformat(str(submitted).replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                if dt.month == now.month and dt.year == now.year:
                    month_revenue += amount
            except (TypeError, ValueError):
                pass
    currency = next((p.get("currency") for p in approved if p.get("currency")), "INR")
    return {
        "customers": len(users),
        "active_subscriptions": len(active_subs),
        "lifetime_active": len(lifetime),
        "expiring_7_days": expiring,
        "expired_subscriptions": len(expired_subs),
        "pending_payments": len(pending),
        "approved_payments": len(approved),
        "total_revenue": round(revenue, 2),
        "month_revenue": round(month_revenue, 2),
        "currency": currency,
        "average_payment": round(revenue / len(approved), 2) if approved else 0,
    }


@router.get("/api/notifications")
async def dashboard_notifications(_: str = Depends(require_dashboard_auth)):
    """Build actionable dashboard alerts from existing database state."""
    now = datetime.now(timezone.utc)
    users = rows("users", "id,first_name,last_name,username,telegram_user_id,created_at", limit=10000)
    subs = rows("subscriptions", "id,user_id,status,is_lifetime,expires_at", limit=20000)
    payments = rows("payment_requests", "id,user_id,status,payment_number,submitted_at,amount,currency", limit=20000)
    notifications = []

    pending = [x for x in payments if x.get("status") == "pending"]
    if pending:
        notifications.append({"type":"payment","level":"warning","title":f"{len(pending)} payment{'s' if len(pending)!=1 else ''} pending","detail":"Payment requests are waiting for review.","count":len(pending),"target":"payments"})

    expiring3 = 0
    expiring7 = 0
    expired = 0
    for sub in subs:
        if sub.get("is_lifetime") or not sub.get("expires_at"):
            continue
        dt = _parse_dt(sub.get("expires_at"))
        if not dt:
            continue
        if sub.get("status") == "expired" or dt < now:
            expired += 1
            continue
        delta = dt - now
        if delta <= timedelta(days=3):
            expiring3 += 1
        elif delta <= timedelta(days=7):
            expiring7 += 1
    if expiring3:
        notifications.append({"type":"expiry","level":"danger","title":f"{expiring3} subscription{'s' if expiring3!=1 else ''} expire within 3 days","detail":"Review access before it expires.","count":expiring3,"target":"students"})
    if expiring7:
        notifications.append({"type":"expiry","level":"warning","title":f"{expiring7} subscription{'s' if expiring7!=1 else ''} expire within 7 days","detail":"These customers may need a renewal reminder.","count":expiring7,"target":"students"})
    if expired:
        notifications.append({"type":"expired","level":"danger","title":f"{expired} expired subscription{'s' if expired!=1 else ''}","detail":"Check access and renewal status.","count":expired,"target":"students"})

    day_ago = now - timedelta(days=1)
    new_students = [u for u in users if (_parse_dt(u.get("created_at")) or datetime.min.replace(tzinfo=timezone.utc)) >= day_ago]
    if new_students:
        notifications.append({"type":"student","level":"info","title":f"{len(new_students)} new student{'s' if len(new_students)!=1 else ''}","detail":"Registered in the last 24 hours.","count":len(new_students),"target":"students"})

    return {"count":len(notifications), "notifications":notifications[:20]}


@router.get("/api/settings/health")
async def dashboard_settings_health(_: str = Depends(require_dashboard_auth)):
    """Read-only system health checks. Never exposes secrets/tokens."""
    result = {
        "dashboard": {"status": "online"},
        "supabase": {"status": "unknown"},
        "admin_bot": {"configured": bool(os.getenv("ADMIN_BOT_TOKEN")), "status": "not_checked"},
        "customer_bot": {"configured": bool(os.getenv("CUSTOMER_BOT_TOKEN")), "status": "not_checked"},
        "mode": os.getenv("DASHBOARD_ONLY", "false"),
        "dashboard_user": os.getenv("ADMIN_DASHBOARD_USERNAME", "admin"),
        "password_configured": bool(os.getenv("ADMIN_DASHBOARD_PASSWORD")),
    }
    try:
        supabase.table("users").select("id").limit(1).execute()
        result["supabase"] = {"status": "connected"}
    except Exception as exc:
        result["supabase"] = {"status": "error", "detail": str(exc)[:180]}

    async def bot_status(env_name: str):
        token = os.getenv(env_name)
        if not token:
            return {"configured": False, "status": "missing"}
        bot = Bot(token=token)
        try:
            me = await bot.get_me()
            return {"configured": True, "status": "connected", "username": me.username, "name": me.full_name}
        except Exception as exc:
            return {"configured": True, "status": "error", "detail": str(exc)[:180]}
        finally:
            await bot.session.close()

    result["admin_bot"] = await bot_status("ADMIN_BOT_TOKEN")
    result["customer_bot"] = await bot_status("CUSTOMER_BOT_TOKEN")
    return result


@router.get("/api/settings/export")
async def dashboard_settings_export(_: str = Depends(require_dashboard_auth)):
    """Export operational data as JSON. Secrets and credentials are never included."""
    data = {}
    exports = {
        "courses": "id,name,description,telegram_chat_id,status,created_at",
        "plans": "id,course_id,name,price,currency,plan_type,duration_days,description,is_active,created_at",
        "users": "id,telegram_user_id,username,first_name,last_name,created_at",
        "subscriptions": "id,user_id,course_id,plan_id,status,started_at,expires_at,is_lifetime,joined_channel_at,revoked_at,payment_request_id",
        "payment_requests": "id,user_id,course_id,plan_id,amount,currency,status,payment_number,submitted_at,approved_at,reviewed_at,rejection_reason,admin_note",
    }
    for table_name, select in exports.items():
        try:
            data[table_name] = rows(table_name, select, limit=20000)
        except Exception as exc:
            data[table_name] = {"error": str(exc)[:180]}
    data["exported_at"] = datetime.now(timezone.utc).isoformat()
    log_activity("data_exported", "system", None, "Dashboard data export generated", {"tables": list(exports)})
    import json
    body = json.dumps(data, ensure_ascii=False, default=str, indent=2)
    return Response(content=body, media_type="application/json", headers={"Content-Disposition": 'attachment; filename="courseflow-dashboard-export.json"'})


@router.get("/api/courses/{course_id}/lessons")
async def dashboard_course_lessons(course_id: str, _: str = Depends(require_dashboard_auth)):
    try:
        response = supabase.table("course_lessons").select("id,course_id,title,description,content_type,content_url,telegram_message_id,sort_order,is_published,created_at,updated_at").eq("course_id", course_id).order("sort_order").order("created_at").execute()
        return response.data or []
    except Exception as exc:
        print("Dashboard course lessons read error:", repr(exc)); return []

@router.post("/api/courses/{course_id}/lessons")
async def dashboard_create_lesson(course_id: str, payload: dict = Body(...), _: str = Depends(require_dashboard_auth)):
    title=(payload.get("title") or "").strip()
    if not title: raise HTTPException(status_code=400, detail="Lesson title is required.")
    content_type=(payload.get("content_type") or "video").strip().lower()
    if content_type not in {"video","telegram","link","text"}: content_type="video"
    try: sort_order=int(payload.get("sort_order") or 0)
    except (TypeError,ValueError): sort_order=0
    data={"course_id":course_id,"title":title,"description":(payload.get("description") or "").strip(),"content_type":content_type,"content_url":(payload.get("content_url") or "").strip() or None,"telegram_message_id":(payload.get("telegram_message_id") or "").strip() or None,"sort_order":sort_order,"is_published":bool(payload.get("is_published",True))}
    try: response=supabase.table("course_lessons").insert(data).execute()
    except Exception as exc: raise HTTPException(status_code=500, detail=str(exc))
    if not response.data: raise HTTPException(status_code=500, detail="Lesson was not created.")
    log_activity("lesson_created","lesson",response.data[0].get("id"),f"Created lesson: {title}",{"course_id":course_id})
    return {"ok":True,"lesson":response.data[0]}

@router.patch("/api/courses/{course_id}/lessons/{lesson_id}")
async def dashboard_update_lesson(course_id: str, lesson_id: str, payload: dict = Body(...), _: str = Depends(require_dashboard_auth)):
    current=supabase.table("course_lessons").select("id,title").eq("id",lesson_id).eq("course_id",course_id).limit(1).execute()
    if not current.data: raise HTTPException(status_code=404, detail="Lesson not found.")
    update={}
    if "title" in payload:
        title=(payload.get("title") or "").strip()
        if not title: raise HTTPException(status_code=400, detail="Lesson title is required.")
        update["title"]=title
    for key in ("description","content_url","telegram_message_id"):
        if key in payload: update[key]=(payload.get(key) or "").strip() or None
    if payload.get("content_type") in {"video","telegram","link","text"}: update["content_type"]=payload["content_type"]
    if "is_published" in payload: update["is_published"]=bool(payload["is_published"])
    if "sort_order" in payload:
        try: update["sort_order"]=int(payload.get("sort_order") or 0)
        except (TypeError,ValueError): pass
    update["updated_at"]=datetime.now(timezone.utc).isoformat()
    response=supabase.table("course_lessons").update(update).eq("id",lesson_id).eq("course_id",course_id).execute()
    log_activity("lesson_updated","lesson",lesson_id,f"Updated lesson: {update.get('title',current.data[0].get('title'))}",{"course_id":course_id})
    return {"ok":True,"lesson":response.data[0] if response.data else None}

@router.delete("/api/courses/{course_id}/lessons/{lesson_id}")
async def dashboard_delete_lesson(course_id: str, lesson_id: str, _: str = Depends(require_dashboard_auth)):
    current=supabase.table("course_lessons").select("id,title").eq("id",lesson_id).eq("course_id",course_id).limit(1).execute()
    if not current.data: raise HTTPException(status_code=404, detail="Lesson not found.")
    supabase.table("course_lessons").delete().eq("id",lesson_id).eq("course_id",course_id).execute()
    log_activity("lesson_deleted","lesson",lesson_id,f"Deleted lesson: {current.data[0].get('title','')}",{"course_id":course_id})
    return {"ok":True}

@router.get("/login", response_class=HTMLResponse)
async def dashboard_login_page(request: Request):
    if _valid_session(request.cookies.get("dashboard_session")):
        return RedirectResponse("/dashboard", status_code=303)
    return HTMLResponse(LOGIN_HTML)

@router.post("/login", response_class=HTMLResponse)
async def dashboard_login(request: Request):
    password = _dashboard_password()
    if not password:
        return HTMLResponse(LOGIN_HTML.replace("<!--ERROR-->", '<div class="login-error">Dashboard password is not configured. Add DASHBOARD_ADMIN_PASSWORD to your environment.</div>'), status_code=503)
    from urllib.parse import parse_qs
    form = parse_qs((await request.body()).decode("utf-8"))
    username = str(form.get("username", [""])[0])
    submitted_password = str(form.get("password", [""])[0])
    if not (hmac.compare_digest(username, _dashboard_username()) and hmac.compare_digest(submitted_password, password)):
        return HTMLResponse(LOGIN_HTML.replace("<!--ERROR-->", '<div class="login-error">Invalid username or password.</div>'), status_code=401)
    response = RedirectResponse("/dashboard", status_code=303)
    response.set_cookie("dashboard_session", _make_session(username), max_age=60*60*24*365, httponly=True, samesite="lax", secure=os.getenv("DASHBOARD_COOKIE_SECURE", "false").lower()=="true", path="/dashboard")
    return response

@router.post("/logout")
async def dashboard_logout():
    response = RedirectResponse("/dashboard/login", status_code=303)
    response.delete_cookie("dashboard_session", path="/dashboard")
    return response

@router.get("", response_class=HTMLResponse)
async def dashboard_home(_: str = Depends(require_dashboard_auth)):
    return HTMLResponse(DASHBOARD_HTML)


@router.get("/api/overview")
async def dashboard_overview(_: str = Depends(require_dashboard_auth)):
    return build_stats()


@router.get("/api/courses")
async def dashboard_courses(_: str = Depends(require_dashboard_auth)):
    courses = rows("courses", "id,name,description,status,created_at", limit=1000)
    plans = rows("plans", "id,course_id,name,price,currency,plan_type,duration_days,is_active", limit=5000)
    channels = rows("channels", "id,course_id,channel_title,channel_username,is_active,bot_is_admin,can_invite_users,can_manage_members", limit=2000)
    counts = defaultdict(int)
    channel_map = {}
    for plan in plans:
        counts[plan.get("course_id")] += 1
    for channel in channels:
        channel_map.setdefault(channel.get("course_id"), []).append(channel)
    for course in courses:
        course["plan_count"] = counts.get(course.get("id"), 0)
        course["is_active"] = course.get("status") == "active"
        course_channels = channel_map.get(course.get("id"), [])
        course["group_connected"] = bool(course_channels)
        course["group_title"] = (course_channels[0].get("channel_title") if course_channels else None)
    return courses


def _course_slug(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (name or "").strip().lower()).strip("-")
    return slug or "course"


def _unique_course_slug(name: str, exclude_id: str | None = None) -> str:
    base = _course_slug(name)
    slug = base
    counter = 2
    while True:
        q = supabase.table("courses").select("id").eq("slug", slug).limit(1).execute()
        if not q.data or (exclude_id and q.data[0].get("id") == exclude_id):
            return slug
        slug = f"{base}-{counter}"
        counter += 1


@router.get("/api/courses/{course_id}")
async def dashboard_course_detail(course_id: str, _: str = Depends(require_dashboard_auth)):
    response = supabase.table("courses").select("id,name,description,status,slug,sort_order,created_at").eq("id", course_id).limit(1).execute()
    if not response.data:
        raise HTTPException(status_code=404, detail="Course not found.")
    course = response.data[0]
    plans = rows("plans", "id,course_id,name,price,currency,plan_type,duration_days,is_active,created_at,description,qr_code_path", limit=1000)
    plans = [p for p in plans if p.get("course_id") == course_id]
    channels = rows("channels", "id,telegram_chat_id,channel_username,channel_title,is_active,bot_is_admin,can_invite_users,can_manage_members", limit=1000)
    channels = [c for c in channels if c.get("course_id") == course_id]
    course["plans"] = plans
    try:
        lr=supabase.table("course_lessons").select("id,course_id,title,description,content_type,content_url,telegram_message_id,sort_order,is_published,created_at,updated_at").eq("course_id",course_id).order("sort_order").order("created_at").execute()
        course["lessons"] = lr.data or []
    except Exception:
        course["lessons"] = []
    course["channels"] = channels
    course["plan_count"] = len(plans)
    course["group_connected"] = bool(channels)
    return course


@router.post("/api/courses")
async def dashboard_create_course(payload: dict = Body(...), _: str = Depends(require_dashboard_auth)):
    name = (payload.get("name") or "").strip()
    description = (payload.get("description") or "").strip()
    status = (payload.get("status") or "active").strip().lower()
    if not name:
        raise HTTPException(status_code=400, detail="Course name is required.")
    if status not in {"active", "inactive"}:
        raise HTTPException(status_code=400, detail="Status must be active or inactive.")
    slug = _unique_course_slug(name)
    try:
        response = supabase.table("courses").insert({
            "name": name,
            "slug": slug,
            "description": description,
            "status": status,
            "sort_order": 0,
        }).execute()
    except Exception as exc:
        print("Dashboard create course error:", repr(exc))
        raise HTTPException(status_code=500, detail=str(exc))
    if not response.data:
        raise HTTPException(status_code=500, detail="Course was not created.")
    log_activity("course_created", "course", response.data[0].get("id"), f"Created course: {name}")
    return {"ok": True, "course": response.data[0]}


@router.patch("/api/courses/{course_id}")
async def dashboard_update_course(course_id: str, payload: dict = Body(...), _: str = Depends(require_dashboard_auth)):
    current = supabase.table("courses").select("id,name,description,status,slug").eq("id", course_id).limit(1).execute()
    if not current.data:
        raise HTTPException(status_code=404, detail="Course not found.")
    update = {}
    if "name" in payload:
        name = (payload.get("name") or "").strip()
        if not name:
            raise HTTPException(status_code=400, detail="Course name cannot be empty.")
        update["name"] = name
        if name != current.data[0].get("name"):
            update["slug"] = _unique_course_slug(name, exclude_id=course_id)
    if "description" in payload:
        update["description"] = (payload.get("description") or "").strip()
    if "status" in payload:
        status = (payload.get("status") or "").strip().lower()
        if status not in {"active", "inactive"}:
            raise HTTPException(status_code=400, detail="Status must be active or inactive.")
        update["status"] = status
    if not update:
        raise HTTPException(status_code=400, detail="No course changes supplied.")
    try:
        response = supabase.table("courses").update(update).eq("id", course_id).execute()
    except Exception as exc:
        print("Dashboard update course error:", repr(exc))
        raise HTTPException(status_code=500, detail=str(exc))
    log_activity("course_updated", "course", course_id, f"Updated course: {name if 'name' in locals() else course_id}")
    return {"ok": True, "course": response.data[0] if response.data else {"id": course_id, **update}}


@router.post("/api/courses/{course_id}/toggle")
async def dashboard_toggle_course(course_id: str, _: str = Depends(require_dashboard_auth)):
    current = supabase.table("courses").select("id,status,name").eq("id", course_id).limit(1).execute()
    if not current.data:
        raise HTTPException(status_code=404, detail="Course not found.")
    new_status = "inactive" if current.data[0].get("status") == "active" else "active"
    response = supabase.table("courses").update({"status": new_status}).eq("id", course_id).execute()
    log_activity("course_status_changed", "course", course_id, f"Course status changed to {new_status}")
    return {"ok": True, "status": new_status, "course": response.data[0] if response.data else {"id": course_id, "status": new_status}}


@router.delete("/api/courses/{course_id}")
async def dashboard_delete_course(course_id: str, _: str = Depends(require_dashboard_auth)):
    current = supabase.table("courses").select("id,name").eq("id", course_id).limit(1).execute()
    if not current.data:
        raise HTTPException(status_code=404, detail="Course not found.")
    checks = {
        "plans": supabase.table("plans").select("id").eq("course_id", course_id).limit(1).execute().data,
        "subscriptions": supabase.table("subscriptions").select("id").eq("course_id", course_id).limit(1).execute().data,
        "payments": supabase.table("payment_requests").select("id").eq("course_id", course_id).limit(1).execute().data,
        "groups": supabase.table("channels").select("id").eq("course_id", course_id).limit(1).execute().data,
    }
    used = [name for name, data in checks.items() if data]
    if used:
        raise HTTPException(status_code=409, detail="Course cannot be deleted because it has related " + ", ".join(used) + ". Deactivate it instead.")
    try:
        response = supabase.table("courses").delete().eq("id", course_id).execute()
    except Exception as exc:
        print("Dashboard delete course error:", repr(exc))
        raise HTTPException(status_code=500, detail=str(exc))
    log_activity("course_deleted", "course", course_id, "Course deleted")
    return {"ok": True, "deleted": bool(response.data), "course_id": course_id}



@router.get("/api/courses/{course_id}/telegram")
async def dashboard_course_telegram(course_id: str, _: str = Depends(require_dashboard_auth)):
    course = supabase.table("courses").select("id,name,status").eq("id", course_id).limit(1).execute()
    if not course.data:
        raise HTTPException(status_code=404, detail="Course not found.")
    channels = supabase.table("channels").select(
        "id,course_id,telegram_chat_id,channel_username,channel_title,is_active,bot_is_admin,can_invite_users,can_manage_members"
    ).eq("course_id", course_id).limit(10).execute().data or []
    result=[]
    for ch in channels:
        invites = supabase.table("invite_links").select(
            "id,status,telegram_invite_link,sent_at,expires_at,revoked_at"
        ).eq("channel_id", ch["id"]).order("sent_at", desc=True).limit(10).execute().data or []
        item=dict(ch)
        # chat_type is derived live from Telegram; no DB migration is required.
        item["chat_type"] = "unknown"
        item["telegram_link"] = None
        try:
            bot = await _admin_bot()
            try:
                chat = await bot.get_chat(chat_id=ch["telegram_chat_id"])
                item["chat_type"] = getattr(chat, "type", "unknown")
                username = getattr(chat, "username", None) or ch.get("channel_username")
                item["channel_username"] = username
                item["channel_title"] = getattr(chat, "title", None) or ch.get("channel_title")
                if username:
                    item["telegram_link"] = f"https://t.me/{username}"
            finally:
                await bot.session.close()
        except Exception:
            pass
        item["invite_count"]=len(invites)
        item["latest_invite"]=next((x for x in invites if x.get("status") != "revoked"), None)
        result.append(item)
    pending = supabase.table("telegram_connection_requests").select("id,connection_code,status,telegram_chat_id,chat_type,channel_title,channel_username,bot_is_admin,can_invite_users,can_manage_members,created_at,expires_at,verified_at").eq("course_id", course_id).eq("status", "pending").order("created_at", desc=True).limit(1).execute().data or []
    course_invites = supabase.table("course_invite_links").select(
        "id,course_id,channel_id,telegram_invite_link,status,created_at,revoked_at,expires_at"
    ).eq("course_id", course_id).order("created_at", desc=True).limit(10).execute().data or []
    active_course_invite = next((x for x in course_invites if x.get("status") == "active" and not x.get("revoked_at")), None)
    return {"course": course.data[0], "channels": result, "pending_request": pending[0] if pending else None, "course_invite": active_course_invite, "course_invite_history": course_invites}


@router.post("/api/courses/{course_id}/telegram/request")
async def dashboard_request_course_telegram_connection(course_id: str, _: str = Depends(require_dashboard_auth)):
    """Create a persistent connection request for the existing Admin Bot."""
    course = supabase.table("courses").select("id,name,status").eq("id", course_id).limit(1).execute()
    if not course.data:
        raise HTTPException(status_code=404, detail="Course not found.")
    admin_id = os.getenv("ADMIN_TELEGRAM_ID")
    if not admin_id:
        raise HTTPException(status_code=503, detail="ADMIN_TELEGRAM_ID is not configured.")
    import secrets
    code = "CONNECT-" + secrets.token_hex(4).upper()
    expires_at = (datetime.now(timezone.utc) + timedelta(minutes=15)).isoformat()
    try:
        supabase.table("telegram_connection_requests").update({"status": "cancelled"}).eq("course_id", course_id).eq("status", "pending").execute()
        saved = supabase.table("telegram_connection_requests").insert({
            "course_id": course_id, "admin_telegram_id": int(admin_id), "connection_code": code, "status": "pending", "expires_at": expires_at,
        }).execute()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Could not create Telegram connection request. Run the V29 SQL migration first. {exc}")
    if not saved.data:
        raise HTTPException(status_code=500, detail="Connection request was not created.")
    log_activity("course_telegram_connection_requested", "course", course_id, f"Generated Admin Bot connection code for {course.data[0]['name']}")
    return {"ok": True, "course_id": course_id, "course_name": course.data[0]["name"], "connection_code": code, "expires_at": expires_at, "command": f"/connect {code}", "message": "Add the Admin Bot as administrator to the private group/channel, then send this command inside that destination."}

@router.post("/api/courses/{course_id}/telegram/invite")
async def dashboard_generate_course_invite(course_id: str, _: str = Depends(require_dashboard_auth)):
    """Create exactly one active, one-use invite link for the course.

    Any previously active course-level invite is revoked before the new link is
    created. This is separate from per-student subscription invite_links.
    """
    course = supabase.table("courses").select("id,name,status").eq("id", course_id).limit(1).execute()
    if not course.data:
        raise HTTPException(status_code=404, detail="Course not found.")
    channels = supabase.table("channels").select(
        "id,telegram_chat_id,channel_title,is_active,bot_is_admin,can_invite_users"
    ).eq("course_id", course_id).eq("is_active", True).limit(1).execute().data or []
    if not channels:
        raise HTTPException(status_code=409, detail="Connect a Telegram group, supergroup or channel to this course first.")
    channel = channels[0]
    if not channel.get("bot_is_admin") or not channel.get("can_invite_users"):
        raise HTTPException(status_code=409, detail="Admin Bot must be administrator with Invite Users permission.")

    bot = await _admin_bot()
    try:
        # Revoke any previously active course-level links first.
        existing = supabase.table("course_invite_links").select(
            "id,telegram_invite_link,channel_id"
        ).eq("course_id", course_id).eq("status", "active").is_("revoked_at", "null").limit(50).execute().data or []
        for old in existing:
            try:
                await bot.revoke_chat_invite_link(
                    chat_id=channel["telegram_chat_id"],
                    invite_link=old["telegram_invite_link"],
                )
            except Exception as exc:
                print("Course invite revoke before regeneration skipped:", repr(exc))
            supabase.table("course_invite_links").update({
                "status": "revoked",
                "revoked_at": datetime.now(timezone.utc).isoformat(),
            }).eq("id", old["id"]).execute()

        invite = await bot.create_chat_invite_link(
            chat_id=channel["telegram_chat_id"],
            name=f"Course {course.data[0]['name']}",
            member_limit=1,
        )
        saved = supabase.table("course_invite_links").insert({
            "course_id": course_id,
            "channel_id": channel["id"],
            "telegram_invite_link": invite.invite_link,
            "status": "active",
            "created_at": datetime.now(timezone.utc).isoformat(),
        }).execute()
        if not saved.data:
            try:
                await bot.revoke_chat_invite_link(chat_id=channel["telegram_chat_id"], invite_link=invite.invite_link)
            except Exception:
                pass
            raise HTTPException(status_code=500, detail="Invite was created in Telegram but could not be saved.")
        log_activity("course_telegram_invite_created", "course", course_id, f"Created one-use invite for {course.data[0]['name']}")
        return {"ok": True, "invite_link": invite.invite_link, "course_id": course_id, "one_time": True}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Could not generate Telegram invite: {exc}")
    finally:
        try:
            await bot.session.close()
        except Exception:
            pass


@router.post("/api/courses/{course_id}/telegram/invite/revoke")
async def dashboard_revoke_course_invite(course_id: str, _: str = Depends(require_dashboard_auth)):
    course = supabase.table("courses").select("id,name").eq("id", course_id).limit(1).execute()
    if not course.data:
        raise HTTPException(status_code=404, detail="Course not found.")
    active = supabase.table("course_invite_links").select(
        "id,telegram_invite_link,channel_id"
    ).eq("course_id", course_id).eq("status", "active").is_("revoked_at", "null").order("created_at", desc=True).limit(20).execute().data or []
    if not active:
        return {"ok": True, "revoked": 0}
    bot = await _admin_bot()
    revoked = 0
    try:
        for row in active:
            channel = supabase.table("channels").select("telegram_chat_id").eq("id", row["channel_id"]).limit(1).execute().data or []
            if channel:
                try:
                    await bot.revoke_chat_invite_link(
                        chat_id=channel[0]["telegram_chat_id"],
                        invite_link=row["telegram_invite_link"],
                    )
                except Exception as exc:
                    print("Course invite revoke warning:", repr(exc))
            supabase.table("course_invite_links").update({
                "status": "revoked",
                "revoked_at": datetime.now(timezone.utc).isoformat(),
            }).eq("id", row["id"]).execute()
            revoked += 1
        log_activity("course_telegram_invite_revoked", "course", course_id, f"Revoked {revoked} course invite link(s)")
        return {"ok": True, "revoked": revoked}
    finally:
        try:
            await bot.session.close()
        except Exception:
            pass


@router.get("/api/courses/{course_id}/telegram/request")
async def dashboard_course_telegram_connection_request(course_id: str, _: str = Depends(require_dashboard_auth)):
    rows_ = supabase.table("telegram_connection_requests").select("id,course_id,connection_code,status,telegram_chat_id,chat_type,channel_title,channel_username,bot_is_admin,can_invite_users,can_manage_members,created_at,expires_at,verified_at").eq("course_id", course_id).order("created_at", desc=True).limit(1).execute().data or []
    return {"request": rows_[0] if rows_ else None}

@router.post("/api/courses/{course_id}/telegram/connect")
async def dashboard_connect_course_telegram(course_id: str, payload: dict = Body(...), _: str = Depends(require_dashboard_auth)):
    course = supabase.table("courses").select("id,name,status").eq("id", course_id).limit(1).execute()
    if not course.data:
        raise HTTPException(status_code=404, detail="Course not found.")
    raw_chat_id = str(payload.get("telegram_chat_id") or "").strip()
    if not raw_chat_id:
        raise HTTPException(status_code=400, detail="Telegram Chat ID or @username is required.")

    bot = await _admin_bot()
    try:
        try:
            chat = await bot.get_chat(chat_id=int(raw_chat_id))
        except ValueError:
            chat = await bot.get_chat(chat_id=raw_chat_id)
        chat_type = getattr(chat, "type", "unknown")
        if chat_type not in {"group", "supergroup", "channel"}:
            raise HTTPException(status_code=400, detail="Only Telegram groups, supergroups and channels can be connected to a course.")
        me = await bot.get_me()
        member = await bot.get_chat_member(chat_id=chat.id, user_id=me.id)
        status = getattr(member, "status", None)
        is_admin = status in {"administrator", "creator"}
        can_invite = bool(getattr(member, "can_invite_users", False))
        can_manage = bool(getattr(member, "can_manage_chat", False) or getattr(member, "can_manage_video_chats", False))
        if not is_admin:
            raise HTTPException(status_code=409, detail="Customer/Admin bot is not an administrator of this Telegram destination.")
        if not can_invite:
            raise HTTPException(status_code=409, detail="Bot is an administrator but cannot create invite links. Enable Invite Users permission.")
        title = getattr(chat, "title", None) or ("Telegram Channel" if chat_type == "channel" else "Telegram Group")
        username = getattr(chat, "username", None)
        existing = supabase.table("channels").select("id").eq("course_id", course_id).limit(1).execute().data
        data = {
            "course_id": course_id,
            "telegram_chat_id": str(chat.id),
            "channel_username": username,
            "channel_title": title,
            "is_active": True,
            "bot_is_admin": True,
            "can_invite_users": can_invite,
            "can_manage_members": can_manage,
        }
        if existing:
            saved = supabase.table("channels").update(data).eq("id", existing[0]["id"]).execute()
        else:
            saved = supabase.table("channels").insert(data).execute()
        if not saved.data:
            raise HTTPException(status_code=500, detail="Telegram destination could not be saved.")
        log_activity("course_telegram_connected", "course", course_id, f"Connected Telegram {chat_type}: {title}", {"chat_id": str(chat.id), "chat_type": chat_type})
        return {
            "ok": True,
            "chat_id": str(chat.id),
            "chat_type": chat_type,
            "chat_title": title,
            "chat_username": username,
            "bot_status": status,
            "bot_is_admin": is_admin,
            "can_invite_users": can_invite,
            "can_manage_members": can_manage,
            "telegram_link": f"https://t.me/{username}" if username else None,
            "message": f"Telegram {chat_type} verified and connected to this course.",
        }
    finally:
        await bot.session.close()


@router.post("/api/courses/{course_id}/telegram/test")
async def dashboard_test_course_telegram(course_id: str, _: str = Depends(require_dashboard_auth)):
    course = supabase.table("courses").select("id,name").eq("id", course_id).limit(1).execute()
    if not course.data:
        raise HTTPException(status_code=404, detail="Course not found.")
    channel = supabase.table("channels").select(
        "id,telegram_chat_id,channel_username,channel_title,is_active,bot_is_admin,can_invite_users,can_manage_members"
    ).eq("course_id", course_id).limit(1).execute().data
    if not channel:
        raise HTTPException(status_code=404, detail="No Telegram destination is connected to this course.")
    channel=channel[0]
    bot=await _admin_bot()
    try:
        chat=await bot.get_chat(chat_id=channel["telegram_chat_id"])
        me=await bot.get_me()
        member=await bot.get_chat_member(chat_id=channel["telegram_chat_id"], user_id=me.id)
        chat_type=getattr(chat, "type", "unknown")
        can_invite=bool(getattr(member, "can_invite_users", False))
        can_manage=bool(getattr(member, "can_manage_chat", False) or getattr(member, "can_manage_video_chats", False))
        is_admin=getattr(member, "status", None) in {"administrator", "creator"}
        update={
            "channel_title": getattr(chat, "title", None) or channel.get("channel_title") or ("Telegram Channel" if chat_type == "channel" else "Telegram Group"),
            "channel_username": getattr(chat, "username", None),
            "is_active": True,
            "bot_is_admin": is_admin,
            "can_invite_users": can_invite,
            "can_manage_members": can_manage,
        }
        supabase.table("channels").update(update).eq("id", channel["id"]).execute()
        return {
            "ok": True,
            "course_name": course.data[0]["name"],
            "chat_title": update["channel_title"],
            "chat_username": update["channel_username"],
            "chat_id": channel["telegram_chat_id"],
            "chat_type": chat_type,
            "bot_status": getattr(member, "status", "unknown"),
            "bot_is_admin": is_admin,
            "can_invite_users": can_invite,
            "can_manage_members": can_manage,
            "telegram_link": f"https://t.me/{update['channel_username']}" if update.get("channel_username") else None,
            "message": "Telegram connection verified and permissions refreshed.",
        }
    except Exception as exc:
        if isinstance(exc, HTTPException):
            raise
        raise HTTPException(status_code=502, detail=f"Telegram connection test failed: {exc}")
    finally:
        await bot.session.close()


@router.get("/api/students")
async def dashboard_students(_: str = Depends(require_dashboard_auth)):
    users = rows("users", "id,telegram_user_id,username,first_name,last_name,created_at", limit=1000)
    subscriptions = rows("subscriptions", "id,user_id,course_id,plan_id,status,expires_at,is_lifetime,started_at", limit=5000)
    courses = {x["id"]: x for x in rows("courses", "id,name", limit=2000)}
    plans = {x["id"]: x for x in rows("plans", "id,name", limit=5000)}
    by_user = defaultdict(list)
    for sub in subscriptions:
        by_user[sub.get("user_id")].append(sub)
    for user in users:
        subs = by_user.get(user.get("id"), [])
        active = [x for x in subs if x.get("status") == "active"]
        user["active_courses"] = len(active)
        user["subscription_status"] = "active" if active else ("pending" if any(x.get("status") == "pending" for x in subs) else ("expired" if subs else "none"))
        user["course_names"] = [courses.get(x.get("course_id"), {}).get("name") for x in active if courses.get(x.get("course_id"), {}).get("name")]
        user["plan_names"] = [plans.get(x.get("plan_id"), {}).get("name") for x in active if plans.get(x.get("plan_id"), {}).get("name")]
        user["course_ids"] = [x.get("course_id") for x in active if x.get("course_id")]
    return users


@router.get("/api/subscriptions")
async def dashboard_subscriptions(_: str = Depends(require_dashboard_auth)):
    subs = rows("subscriptions", "id,user_id,course_id,plan_id,status,started_at,expires_at,is_lifetime,joined_channel_at,revoked_at,payment_request_id", limit=10000)
    users = {x["id"]: x for x in rows("users", "id,telegram_user_id,username,first_name,last_name", limit=10000)}
    courses = {x["id"]: x for x in rows("courses", "id,name,status", limit=3000)}
    plans = {x["id"]: x for x in rows("plans", "id,name,price,currency,plan_type,duration_days,is_active", limit=10000)}
    now = datetime.now(timezone.utc)
    for sub in subs:
        u, c, pl = users.get(sub.get("user_id"), {}), courses.get(sub.get("course_id"), {}), plans.get(sub.get("plan_id"), {})
        sub.update({"customer_name": " ".join(x for x in [u.get("first_name"), u.get("last_name")] if x) or "Unknown", "username": u.get("username"), "telegram_user_id": u.get("telegram_user_id"), "course_name": c.get("name", "Unknown course"), "course_status": c.get("status"), "plan_name": pl.get("name", "Unknown plan"), "plan": pl})
        expiry = _parse_dt(sub.get("expires_at"))
        if sub.get("status") == "active" and not sub.get("is_lifetime") and expiry:
            days = (expiry - now).total_seconds() / 86400
            sub["management_status"] = "expired" if days < 0 else ("expiring" if days <= 7 else "active")
            sub["days_remaining"] = max(0, int(days + 0.999))
        elif sub.get("status") == "active" and sub.get("is_lifetime"):
            sub["management_status"], sub["days_remaining"] = "lifetime", None
        else:
            sub["management_status"], sub["days_remaining"] = sub.get("status") or "unknown", None
    return subs


@router.post("/api/subscriptions/{subscription_id}/change-plan")
async def dashboard_change_subscription_plan(subscription_id: str, payload: dict = Body(...), _: str = Depends(require_dashboard_auth)):
    plan_id = str(payload.get("plan_id") or "")
    if not plan_id:
        raise HTTPException(status_code=400, detail="plan_id is required.")
    sub_resp = supabase.table("subscriptions").select("id,user_id,course_id,status,is_lifetime,expires_at,plan_id").eq("id", subscription_id).limit(1).execute()
    if not sub_resp.data:
        raise HTTPException(status_code=404, detail="Subscription not found.")
    sub = sub_resp.data[0]
    if sub.get("status") != "active":
        raise HTTPException(status_code=409, detail="Only active subscriptions can change plan.")
    plan_resp = supabase.table("plans").select("id,course_id,name,plan_type,duration_days,is_active").eq("id", plan_id).limit(1).execute()
    if not plan_resp.data:
        raise HTTPException(status_code=404, detail="Target plan not found.")
    plan = plan_resp.data[0]
    if str(plan.get("course_id")) != str(sub.get("course_id")):
        raise HTTPException(status_code=409, detail="The new plan must belong to the same course.")
    if not plan.get("is_active"):
        raise HTTPException(status_code=409, detail="The target plan is inactive.")
    update = {"plan_id": plan_id}
    if plan.get("plan_type") == "lifetime":
        update.update({"is_lifetime": True, "expires_at": None})
    elif sub.get("is_lifetime"):
        duration = int(plan.get("duration_days") or 0)
        if duration <= 0:
            raise HTTPException(status_code=409, detail="Target plan has no valid duration.")
        update.update({"is_lifetime": False, "expires_at": (datetime.now(timezone.utc) + timedelta(days=duration)).isoformat()})
    updated = supabase.table("subscriptions").update(update).eq("id", subscription_id).eq("status", "active").execute()
    if not updated.data:
        raise HTTPException(status_code=409, detail="Subscription could not be updated.")
    log_activity("subscription_plan_changed", "subscription", subscription_id, "Subscription plan changed", {"user_id": sub.get("user_id"), "from_plan_id": sub.get("plan_id"), "to_plan_id": plan_id})
    return {"ok": True, "subscription": updated.data[0]}


@router.get("/api/payments")
async def dashboard_payments(_: str = Depends(require_dashboard_auth)):
    payments = rows(
        "payment_requests",
        "id,user_id,course_id,plan_id,amount,currency,status,payment_number,submitted_at,reviewed_at,rejection_reason,screenshot_path,admin_note",
        limit=500,
    )
    users = {x["id"]: x for x in rows("users", "id,telegram_user_id,username,first_name,last_name", limit=5000)}
    courses = {x["id"]: x for x in rows("courses", "id,name", limit=2000)}
    plans = {x["id"]: x for x in rows("plans", "id,name,price,currency,plan_type,duration_days", limit=5000)}
    for payment in payments:
        user = users.get(payment.get("user_id"), {})
        payment["customer_name"] = " ".join(x for x in [user.get("first_name"), user.get("last_name")] if x) or "Unknown"
        payment["username"] = user.get("username")
        payment["telegram_user_id"] = user.get("telegram_user_id")
        payment["course_name"] = courses.get(payment.get("course_id"), {}).get("name", "Unknown course")
        payment["plan_name"] = plans.get(payment.get("plan_id"), {}).get("name", "Unknown plan")
        payment["approved_at"] = payment.get("reviewed_at") if payment.get("status") == "approved" else None
    return payments


@router.get("/api/payments/{payment_id}")
async def dashboard_payment_detail(payment_id: str, _: str = Depends(require_dashboard_auth)):
    response = supabase.table("payment_requests").select(
        "id,user_id,course_id,plan_id,amount,currency,status,payment_number,submitted_at,reviewed_at,rejection_reason,screenshot_path,admin_note"
    ).eq("id", payment_id).limit(1).execute()
    if not response.data:
        raise HTTPException(status_code=404, detail="Payment not found.")
    payment = response.data[0]
    payment["approved_at"] = payment.get("reviewed_at") if payment.get("status") == "approved" else None
    user_resp = supabase.table("users").select("id,telegram_user_id,username,first_name,last_name").eq("id", payment["user_id"]).limit(1).execute()
    course_resp = supabase.table("courses").select("id,name,status").eq("id", payment["course_id"]).limit(1).execute()
    plan_resp = supabase.table("plans").select("id,name,price,currency,plan_type,duration_days,is_active").eq("id", payment["plan_id"]).limit(1).execute()
    payment["user"] = user_resp.data[0] if user_resp.data else {}
    payment["course"] = course_resp.data[0] if course_resp.data else {}
    payment["plan"] = plan_resp.data[0] if plan_resp.data else {}
    sub = supabase.table("subscriptions").select("id,status,is_lifetime,expires_at").eq("payment_request_id", payment_id).limit(1).execute()
    payment["subscription"] = sub.data[0] if sub.data else None
    payment["screenshot_url"] = None
    path = payment.get("screenshot_path")
    if path:
        try:
            signed = supabase.storage.from_("payment-qr").create_signed_url(path, 900)
            payment["screenshot_url"] = signed.get("signedURL") if isinstance(signed, dict) else None
        except Exception as exc:
            print("Payment screenshot URL error:", repr(exc))
    return payment


@router.post("/api/payments/{payment_id}/approve")
async def dashboard_approve_payment(payment_id: str, _: str = Depends(require_dashboard_auth)):
    response = supabase.table("payment_requests").select(
        "id,payment_number,user_id,course_id,plan_id,amount,currency,status"
    ).eq("id", payment_id).limit(1).execute()
    if not response.data:
        raise HTTPException(status_code=404, detail="Payment request not found.")
    payment = response.data[0]
    if payment.get("status") != "pending":
        raise HTTPException(status_code=409, detail=f"Payment is not pending. Current status: {payment.get('status')}")

    active = supabase.table("subscriptions").select("id,is_lifetime,status").eq("user_id", payment["user_id"]).eq("course_id", payment["course_id"]).eq("status", "active").limit(1).execute()
    if active.data and active.data[0].get("is_lifetime"):
        raise HTTPException(status_code=409, detail="Customer already has active lifetime access for this course. Payment was not approved.")

    try:
        from app.admin_bot.main import provision_course_access, write_audit_log, create_admin_notification
        bot = await _admin_bot()
        try:
            supabase.table("payment_requests").update({
                "status": "approved",
                "reviewed_at": datetime.now(timezone.utc).isoformat(),
            }).eq("id", payment_id).execute()
            result = await provision_course_access(bot, payment)
        finally:
            await bot.session.close()

        await write_audit_log(
            0, "APPROVE_PAYMENT", target_user_id=payment.get("user_id"),
            course_id=payment.get("course_id"), plan_id=payment.get("plan_id"),
            details={"payment_number": payment.get("payment_number"), "amount": payment.get("amount"), "renewed": bool(result.get("renewed")), "source": "dashboard"},
        )
        await create_admin_notification(
            "PAYMENT_APPROVED", "Payment Approved",
            f"Payment #{payment.get('payment_number')} approved from dashboard. Amount: ₹{payment.get('amount')}",
            severity="success", metadata={"payment_id": payment.get("id"), "user_id": payment.get("user_id"), "source": "dashboard"},
        )
        return {"ok": True, "payment": payment, "result": result}
    except Exception as exc:
        print("Dashboard approve payment error:", repr(exc))
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/api/payments/{payment_id}/reject")
async def dashboard_reject_payment(payment_id: str, payload: dict | None = Body(default=None), _: str = Depends(require_dashboard_auth)):
    response = supabase.table("payment_requests").select("id,payment_number,user_id,amount,status").eq("id", payment_id).limit(1).execute()
    if not response.data:
        raise HTTPException(status_code=404, detail="Payment request not found.")
    payment = response.data[0]
    if payment.get("status") != "pending":
        raise HTTPException(status_code=409, detail=f"Payment is already {payment.get('status')}.")
    reason = ((payload or {}).get("reason") or "").strip()[:500]
    update = {"status": "rejected", "reviewed_at": datetime.now(timezone.utc).isoformat(), "reviewed_by": None}
    if reason:
        update["rejection_reason"] = reason
    updated = supabase.table("payment_requests").update(update).eq("id", payment_id).eq("status", "pending").execute()
    if not updated.data:
        raise HTTPException(status_code=409, detail="Payment was changed before it could be rejected.")
    try:
        from app.admin_bot.main import create_admin_notification
        await create_admin_notification(
            "PAYMENT_REJECTED", "Payment Rejected",
            f"Payment #{payment.get('payment_number')} rejected from dashboard. Amount: ₹{payment.get('amount')}",
            severity="warning", metadata={"payment_id": payment.get("id"), "user_id": payment.get("user_id"), "source": "dashboard"},
        )
    except Exception as exc:
        print("Dashboard rejection notification error:", repr(exc))
    log_activity("payment_rejected", "payment", payment.get("id"), f"Payment #{payment.get('payment_number')} rejected")
    return {"ok": True, "payment": updated.data[0]}


@router.post("/api/broadcast/preview")
async def dashboard_broadcast_preview(payload: dict = Body(...), _: str = Depends(require_dashboard_auth)):
    audience = str(payload.get("audience") or "all").strip()
    course_id = payload.get("course_id")
    message = str(payload.get("message") or "").strip()
    if audience not in {"all", "active", "expiring", "course"}:
        raise HTTPException(status_code=400, detail="Invalid audience.")
    if not message:
        raise HTTPException(status_code=400, detail="Message is required.")
    if len(message) > 4096:
        raise HTTPException(status_code=400, detail="Telegram messages are limited to 4096 characters.")
    if audience == "course" and not course_id:
        raise HTTPException(status_code=400, detail="Select a course.")

    users = rows("users", "id,telegram_user_id,username,first_name,last_name", limit=10000)
    recipients = [u for u in users if u.get("telegram_user_id")]
    if audience in {"active", "expiring", "course"}:
        subs = rows("subscriptions", "user_id,course_id,status,expires_at,is_lifetime", limit=20000)
        by_user = defaultdict(list)
        now = datetime.now(timezone.utc)
        for sub in subs:
            if sub.get("status") != "active":
                continue
            if audience == "course" and str(sub.get("course_id")) != str(course_id):
                continue
            if audience == "expiring":
                if sub.get("is_lifetime"):
                    continue
                exp = _parse_dt(sub.get("expires_at"))
                if not exp or exp < now or exp > now + timedelta(days=7):
                    continue
            by_user[sub.get("user_id")].append(sub)
        recipients = [u for u in recipients if u.get("id") in by_user]

    sample = []
    for u in recipients[:8]:
        name = " ".join(x for x in [u.get("first_name"), u.get("last_name")] if x) or "Telegram user"
        sample.append({"name": name, "username": u.get("username"), "telegram_user_id": u.get("telegram_user_id")})
    return {"audience": audience, "course_id": course_id, "recipient_count": len(recipients), "sample": sample, "message": message}

@router.post("/api/broadcast/send")
async def dashboard_broadcast_send(payload: dict = Body(...), _: str = Depends(require_dashboard_auth)):
    if not payload.get("confirm"):
        raise HTTPException(status_code=400, detail="Broadcast confirmation is required.")
    preview = await dashboard_broadcast_preview(payload, _)
    recipients = []
    users = rows("users", "id,telegram_user_id", limit=10000)
    recipients = [u for u in users if u.get("telegram_user_id")]
    audience = preview["audience"]
    course_id = preview.get("course_id")
    if audience in {"active", "expiring", "course"}:
        subs = rows("subscriptions", "user_id,course_id,status,expires_at,is_lifetime", limit=20000)
        by_user = defaultdict(list)
        now = datetime.now(timezone.utc)
        for sub in subs:
            if sub.get("status") != "active":
                continue
            if audience == "course" and str(sub.get("course_id")) != str(course_id):
                continue
            if audience == "expiring":
                if sub.get("is_lifetime"):
                    continue
                exp = _parse_dt(sub.get("expires_at"))
                if not exp or exp < now or exp > now + timedelta(days=7):
                    continue
            by_user[sub.get("user_id")].append(sub)
        recipients = [u for u in recipients if u.get("id") in by_user]

    # Broadcasts to customers must be sent by the CUSTOMER bot, not the admin bot.
    # The admin bot is reserved for owner/admin operations.
    bot = await _customer_bot()
    sent = failed = 0
    errors = []
    try:
        for u in recipients:
            try:
                await bot.send_message(chat_id=int(u["telegram_user_id"]), text=preview["message"])
                sent += 1
            except Exception as exc:
                failed += 1
                if len(errors) < 10:
                    errors.append({"telegram_user_id": u.get("telegram_user_id"), "error": str(exc)})
    finally:
        await bot.session.close()
    try:
        supabase.table("broadcast_logs").insert({
            "audience": audience, "course_id": course_id, "message": preview["message"],
            "recipient_count": len(recipients), "success_count": sent, "failed_count": failed
        }).execute()
    except Exception:
        pass
    return {"recipient_count": len(recipients), "sent": sent, "failed": failed, "errors": errors}

@router.get("/api/plans")
async def dashboard_plans(_: str = Depends(require_dashboard_auth)):
    plans = rows("plans", "id,course_id,name,price,currency,plan_type,duration_days,is_active,created_at,description,qr_code_path", limit=2000)
    courses = {c["id"]: c for c in rows("courses", "id,name,status", limit=1000)}
    for plan in plans:
        course = courses.get(plan.get("course_id"), {})
        plan["course_name"] = course.get("name", "Unknown course")
        plan["course_status"] = course.get("status")
    return plans


@router.post("/api/courses/{course_id}/plans")
async def dashboard_create_plan(course_id: str, payload: dict = Body(...), _: str = Depends(require_dashboard_auth)):
    course = supabase.table("courses").select("id,name").eq("id", course_id).limit(1).execute()
    if not course.data:
        raise HTTPException(status_code=404, detail="Course not found.")
    name = (payload.get("name") or "").strip()
    plan_type = (payload.get("plan_type") or "fixed").strip().lower()
    currency = (payload.get("currency") or "INR").strip().upper()
    description = (payload.get("description") or "").strip()
    qr_code_path = (payload.get("qr_code_path") or "").strip()
    try: price = float(payload.get("price"))
    except (TypeError, ValueError): raise HTTPException(status_code=400, detail="Price must be a number.")
    if not name: raise HTTPException(status_code=400, detail="Plan name is required.")
    if price <= 0: raise HTTPException(status_code=400, detail="Price must be greater than 0.")
    if plan_type not in {"fixed", "lifetime"}: raise HTTPException(status_code=400, detail="Plan type must be fixed or lifetime.")
    duration_days = None
    if plan_type == "fixed":
        try: duration_days = int(payload.get("duration_days"))
        except (TypeError, ValueError): raise HTTPException(status_code=400, detail="Duration must be a whole number of days.")
        if duration_days <= 0: raise HTTPException(status_code=400, detail="Duration must be greater than 0 days.")
    if not qr_code_path: raise HTTPException(status_code=400, detail="Payment QR is required for a plan.")
    duplicate = supabase.table("plans").select("id").eq("course_id", course_id).eq("name", name).limit(1).execute()
    if duplicate.data: raise HTTPException(status_code=409, detail="A plan with this name already exists for this course.")
    data = {"course_id": course_id, "name": name, "plan_type": plan_type, "price": price, "currency": currency, "duration_days": duration_days, "description": description or f"{name} subscription", "qr_code_path": qr_code_path, "is_active": True}
    try: response = supabase.table("plans").insert(data).execute()
    except Exception as exc: print("Dashboard create plan error:", repr(exc)); raise HTTPException(status_code=500, detail=str(exc))
    if not response.data: raise HTTPException(status_code=500, detail="Plan was not created.")
    log_activity("plan_created", "plan", response.data[0].get("id"), f"Created plan: {name}", {"course_id": course_id})
    return {"ok": True, "plan": response.data[0]}


@router.patch("/api/plans/{plan_id}")
async def dashboard_update_plan(plan_id: str, payload: dict = Body(...), _: str = Depends(require_dashboard_auth)):
    current = supabase.table("plans").select("id,course_id,name,plan_type,price,currency,duration_days,description,is_active,qr_code_path").eq("id", plan_id).limit(1).execute()
    if not current.data: raise HTTPException(status_code=404, detail="Plan not found.")
    old = current.data[0]; update = {}
    if "name" in payload:
        name=(payload.get("name") or "").strip()
        if not name: raise HTTPException(status_code=400, detail="Plan name is required.")
        if name != old.get("name"):
            dup=supabase.table("plans").select("id").eq("course_id", old["course_id"]).eq("name", name).limit(1).execute()
            if dup.data and dup.data[0].get("id") != plan_id: raise HTTPException(status_code=409, detail="A plan with this name already exists for this course.")
        update["name"]=name
    if "price" in payload:
        try: price=float(payload.get("price"))
        except (TypeError, ValueError): raise HTTPException(status_code=400, detail="Price must be a number.")
        if price<=0: raise HTTPException(status_code=400, detail="Price must be greater than 0.")
        update["price"]=price
    pt=(payload.get("plan_type") or old.get("plan_type") or "fixed").strip().lower()
    if pt not in {"fixed","lifetime"}: raise HTTPException(status_code=400, detail="Plan type must be fixed or lifetime.")
    if "plan_type" in payload: update["plan_type"]=pt
    if "duration_days" in payload or "plan_type" in payload:
        if pt=="lifetime": update["duration_days"]=None
        else:
            try: days=int(payload.get("duration_days", old.get("duration_days")))
            except (TypeError, ValueError): raise HTTPException(status_code=400, detail="Duration must be a whole number of days.")
            if days<=0: raise HTTPException(status_code=400, detail="Duration must be greater than 0 days.")
            update["duration_days"]=days
    if "description" in payload: update["description"]=(payload.get("description") or "").strip()
    if "currency" in payload: update["currency"]=(payload.get("currency") or "INR").strip().upper()
    if payload.get("qr_code_path"): update["qr_code_path"]=str(payload["qr_code_path"]).strip()
    if not update: raise HTTPException(status_code=400, detail="No plan changes supplied.")
    try: response=supabase.table("plans").update(update).eq("id", plan_id).execute()
    except Exception as exc: print("Dashboard update plan error:", repr(exc)); raise HTTPException(status_code=500, detail=str(exc))
    log_activity("plan_updated", "plan", plan_id, "Plan updated")
    return {"ok":True,"plan":response.data[0] if response.data else {"id":plan_id,**old,**update}}


@router.post("/api/plans/{plan_id}/toggle")
async def dashboard_toggle_plan(plan_id: str, _: str = Depends(require_dashboard_auth)):
    current=supabase.table("plans").select("id,is_active").eq("id",plan_id).limit(1).execute()
    if not current.data: raise HTTPException(status_code=404, detail="Plan not found.")
    new_active=not bool(current.data[0].get("is_active")); response=supabase.table("plans").update({"is_active":new_active}).eq("id",plan_id).execute()
    log_activity("plan_status_changed", "plan", plan_id, f"Plan status changed to {new_active}")
    return {"ok":True,"is_active":new_active,"plan":response.data[0] if response.data else {"id":plan_id,"is_active":new_active}}


@router.delete("/api/plans/{plan_id}")
async def dashboard_delete_plan(plan_id: str, _: str = Depends(require_dashboard_auth)):
    current=supabase.table("plans").select("id,course_id,name").eq("id",plan_id).limit(1).execute()
    if not current.data: raise HTTPException(status_code=404, detail="Plan not found.")
    checks={"subscriptions":supabase.table("subscriptions").select("id").eq("plan_id",plan_id).limit(1).execute().data,"payments":supabase.table("payment_requests").select("id").eq("plan_id",plan_id).limit(1).execute().data}
    used=[k for k,v in checks.items() if v]
    if used: raise HTTPException(status_code=409, detail="Plan cannot be deleted because it has related "+", ".join(used)+". Deactivate it instead.")
    try: response=supabase.table("plans").delete().eq("id",plan_id).execute()
    except Exception as exc: print("Dashboard delete plan error:",repr(exc)); raise HTTPException(status_code=500,detail=str(exc))
    log_activity("plan_deleted", "plan", plan_id, "Plan deleted")
    return {"ok":True,"deleted":bool(response.data),"plan_id":plan_id}


@router.post("/api/plans/qr-upload")
async def dashboard_upload_plan_qr(file: UploadFile = File(...), _: str = Depends(require_dashboard_auth)):
    content_type=(file.content_type or "").lower()
    if content_type not in {"image/jpeg","image/jpg","image/png","image/webp"}: raise HTTPException(status_code=400,detail="QR must be JPG, PNG or WEBP.")
    data=await file.read()
    if not data or len(data)>5*1024*1024: raise HTTPException(status_code=400,detail="QR image must be between 1 byte and 5 MB.")
    import secrets
    ext=".png" if content_type=="image/png" else ".webp" if content_type=="image/webp" else ".jpg"
    path=f"dashboard/{secrets.token_hex(8)}_plan_qr{ext}"
    try: supabase.storage.from_("payment-qr").upload(path,data,{"content-type":content_type,"upsert":"false"})
    except Exception as exc: print("Dashboard plan QR upload error:",repr(exc)); raise HTTPException(status_code=500,detail=str(exc))
    return {"ok":True,"qr_code_path":path}


async def _customer_bot():
    token = os.getenv("CUSTOMER_BOT_TOKEN")
    if not token:
        raise HTTPException(status_code=503, detail="CUSTOMER_BOT_TOKEN is not configured.")
    return Bot(token=token)

async def _admin_bot():
    token = os.getenv("ADMIN_BOT_TOKEN")
    if not token:
        raise HTTPException(status_code=503, detail="ADMIN_BOT_TOKEN is not configured.")
    return Bot(token=token)


def _parse_dt(value):
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


@router.get("/api/students/{user_id}")
async def dashboard_student_detail(user_id: str, _: str = Depends(require_dashboard_auth)):
    user_resp = supabase.table("users").select(
        "id,telegram_user_id,username,first_name,last_name,created_at"
    ).eq("id", user_id).limit(1).execute()
    if not user_resp.data:
        raise HTTPException(status_code=404, detail="Student not found.")
    user = user_resp.data[0]

    subs = rows(
        "subscriptions",
        "id,user_id,course_id,plan_id,status,started_at,expires_at,is_lifetime,joined_channel_at,revoked_at,payment_request_id",
        limit=1000,
    )
    subs = [x for x in subs if x.get("user_id") == user_id]
    courses = {x["id"]: x for x in rows("courses", "id,name,status", limit=1000)}
    plans = {x["id"]: x for x in rows("plans", "id,name,price,currency,plan_type,duration_days,is_active", limit=5000)}
    for sub in subs:
        sub["course_name"] = courses.get(sub.get("course_id"), {}).get("name", "Unknown course")
        sub["course_status"] = courses.get(sub.get("course_id"), {}).get("status")
        sub["plan_name"] = plans.get(sub.get("plan_id"), {}).get("name", "Unknown plan")
        sub["plan"] = plans.get(sub.get("plan_id"), {})

    payments = rows(
        "payment_requests",
        "id,user_id,payment_number,course_id,plan_id,amount,currency,status,submitted_at,reviewed_at,rejection_reason",
        limit=2000,
    )
    payments = [x for x in payments if x.get("user_id") == user_id]
    for p in payments:
        p["approved_at"] = p.get("reviewed_at") if p.get("status") == "approved" else None
    # The table projection above intentionally avoids assuming optional user fields;
    # user_id is always available in the existing payment_requests schema.
    for p in payments:
        p["course_name"] = courses.get(p.get("course_id"), {}).get("name", "Unknown course")
        p["plan_name"] = plans.get(p.get("plan_id"), {}).get("name", "Unknown plan")

    activity = []
    try:
        activity_rows = supabase.table("dashboard_activity_logs").select(
            "id,action,entity_type,entity_id,description,metadata,created_at"
        ).order("created_at", desc=True).limit(500).execute().data or []
        for item in activity_rows:
            meta = item.get("metadata") or {}
            if str(item.get("entity_id") or "") == str(user_id) or str(meta.get("user_id") or "") == str(user_id):
                activity.append(item)
        activity = activity[:100]
    except Exception as exc:
        print("Student activity read skipped:", repr(exc))

    return {"user": user, "subscriptions": subs, "payments": payments, "activity": activity}


@router.post("/api/students/{user_id}/extend")
async def dashboard_extend_student(user_id: str, payload: dict = Body(...), _: str = Depends(require_dashboard_auth)):
    subscription_id = str(payload.get("subscription_id") or "")
    try:
        days = int(payload.get("days"))
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="days must be a number.")
    if days not in (7, 30, 90):
        raise HTTPException(status_code=400, detail="Allowed extension: 7, 30 or 90 days.")

    resp = supabase.table("subscriptions").select(
        "id,user_id,status,expires_at,is_lifetime"
    ).eq("id", subscription_id).eq("user_id", user_id).limit(1).execute()
    if not resp.data:
        raise HTTPException(status_code=404, detail="Subscription not found.")
    sub = resp.data[0]
    if sub.get("status") != "active":
        raise HTTPException(status_code=409, detail="Only active subscriptions can be extended.")
    if sub.get("is_lifetime"):
        raise HTTPException(status_code=409, detail="Lifetime access cannot be extended.")
    old = _parse_dt(sub.get("expires_at"))
    if not old:
        raise HTTPException(status_code=409, detail="Subscription has no expiry date.")
    now = datetime.now(timezone.utc)
    new_expiry = max(old, now) + timedelta(days=days)
    updated = supabase.table("subscriptions").update({
        "expires_at": new_expiry.isoformat(),
        "status": "active",
        "revoked_at": None,
    }).eq("id", subscription_id).eq("status", "active").execute()
    if not updated.data:
        raise HTTPException(status_code=409, detail="Subscription changed before update.")
    log_activity("subscription_extended", "subscription", subscription.get("id") if subscription else subscription_id, "Subscription extended", {"user_id": user_id})
    return {"ok": True, "subscription": updated.data[0], "added_days": days}


@router.post("/api/students/{user_id}/revoke")
async def dashboard_revoke_student(user_id: str, payload: dict = Body(...), _: str = Depends(require_dashboard_auth)):
    subscription_id = str(payload.get("subscription_id") or "")
    resp = supabase.table("subscriptions").select(
        "id,user_id,course_id,status,is_lifetime"
    ).eq("id", subscription_id).eq("user_id", user_id).limit(1).execute()
    if not resp.data:
        raise HTTPException(status_code=404, detail="Subscription not found.")
    sub = resp.data[0]
    if sub.get("status") != "active":
        raise HTTPException(status_code=409, detail="Subscription is already inactive.")

    now = datetime.now(timezone.utc).isoformat()
    updated = supabase.table("subscriptions").update({
        "status": "revoked", "revoked_at": now
    }).eq("id", subscription_id).eq("status", "active").execute()
    if not updated.data:
        raise HTTPException(status_code=409, detail="Subscription could not be revoked.")

    bot = await _admin_bot()
    invite_errors = []
    try:
        invites = (supabase.table("invite_links").select(
            "id,channel_id,telegram_invite_link"
        ).eq("subscription_id", subscription_id).is_("revoked_at", "null").limit(100).execute()).data or []
        for invite in invites:
            channel = (supabase.table("channels").select("telegram_chat_id").eq("id", invite["channel_id"]).limit(1).execute()).data
            if channel:
                try:
                    await bot.revoke_chat_invite_link(
                        chat_id=channel[0]["telegram_chat_id"],
                        invite_link=invite["telegram_invite_link"],
                    )
                except Exception as e:
                    invite_errors.append(str(e))
            supabase.table("invite_links").update({
                "status": "revoked", "revoked_at": now
            }).eq("id", invite["id"]).execute()

        channel = (supabase.table("channels").select(
            "telegram_chat_id,bot_is_admin,can_manage_members,is_active"
        ).eq("course_id", sub["course_id"]).eq("is_active", True).limit(1).execute()).data
        user_row = supabase.table("users").select("telegram_user_id").eq("id", user_id).limit(1).execute().data
        if channel and user_row and channel[0].get("bot_is_admin") and channel[0].get("can_manage_members"):
            try:
                await bot.ban_chat_member(chat_id=channel[0]["telegram_chat_id"], user_id=user_row[0]["telegram_user_id"])
                await bot.unban_chat_member(chat_id=channel[0]["telegram_chat_id"], user_id=user_row[0]["telegram_user_id"], only_if_banned=True)
                supabase.table("subscriptions").update({"joined_channel_at": None}).eq("id", subscription_id).execute()
            except Exception as e:
                invite_errors.append(f"Member removal: {e}")
    finally:
        await bot.session.close()
    log_activity("access_revoked", "subscription", subscription.get("id") if subscription else subscription_id, "Telegram course access revoked", {"user_id": user_id})
    return {"ok": True, "subscription": updated.data[0], "warnings": invite_errors[:5]}


@router.post("/api/students/{user_id}/grant")
async def dashboard_grant_student(user_id: str, payload: dict = Body(...), _: str = Depends(require_dashboard_auth)):
    plan_id = str(payload.get("plan_id") or "")
    plan_resp = supabase.table("plans").select(
        "id,course_id,name,plan_type,price,currency,duration_days,is_active,description"
    ).eq("id", plan_id).eq("is_active", True).limit(1).execute()
    if not plan_resp.data:
        raise HTTPException(status_code=404, detail="Active plan not found.")
    plan = plan_resp.data[0]
    course_resp = supabase.table("courses").select("id,name,status").eq("id", plan["course_id"]).limit(1).execute()
    user_resp = supabase.table("users").select("id,telegram_user_id,username,first_name,last_name").eq("id", user_id).limit(1).execute()
    if not course_resp.data or not user_resp.data:
        raise HTTPException(status_code=404, detail="Customer or course not found.")
    course, user = course_resp.data[0], user_resp.data[0]
    if course.get("status") != "active":
        raise HTTPException(status_code=409, detail="This course is inactive. Activate the course before granting access.")

    active = supabase.table("subscriptions").select("id,is_lifetime,status").eq("user_id", user_id).eq("course_id", plan["course_id"]).eq("status", "active").limit(1).execute()
    if active.data:
        raise HTTPException(status_code=409, detail="Customer already has active access for this course.")

    started = datetime.now(timezone.utc)
    lifetime = plan.get("plan_type") == "lifetime"
    expires = None if lifetime else (started + timedelta(days=int(plan.get("duration_days") or 0))).isoformat()
    if not lifetime and not plan.get("duration_days"):
        raise HTTPException(status_code=400, detail="This plan has no duration.")

    channel = supabase.table("channels").select(
        "id,telegram_chat_id,channel_title,is_active,bot_is_admin,can_invite_users"
    ).eq("course_id", plan["course_id"]).eq("is_active", True).limit(1).execute().data
    if not channel:
        raise HTTPException(status_code=409, detail="No active Telegram group is configured for this course.")
    channel = channel[0]
    if not channel.get("bot_is_admin") or not channel.get("can_invite_users"):
        raise HTTPException(status_code=409, detail="Admin Bot lacks Telegram invite permission for this course.")

    bot = await _admin_bot()
    subscription = None
    try:
        sub_resp = supabase.table("subscriptions").insert({
            "user_id": user_id, "course_id": plan["course_id"], "plan_id": plan_id,
            "status": "active", "started_at": started.isoformat(), "expires_at": expires,
            "is_lifetime": lifetime,
        }).execute()
        if not sub_resp.data:
            raise RuntimeError("Subscription was not created.")
        subscription = sub_resp.data[0]
        invite_kwargs = {
            "chat_id": channel["telegram_chat_id"],
            "name": f"Manual Grant {user.get('telegram_user_id')}",
            "member_limit": 1,
        }
        if expires:
            invite_kwargs["expire_date"] = int((started + timedelta(days=int(plan["duration_days"]))).timestamp())
        invite = await bot.create_chat_invite_link(**invite_kwargs)
        inv_resp = supabase.table("invite_links").insert({
            "subscription_id": subscription["id"], "channel_id": channel["id"],
            "telegram_invite_link": invite.invite_link, "status": "sent", "sent_at": datetime.now(timezone.utc).isoformat(), "expires_at": expires,
        }).execute()
        if not inv_resp.data:
            raise RuntimeError("Invite link record was not created.")
        # Reuse the existing customer notification implementation without starting the polling bot.
        try:
            from app.admin_bot.main import send_customer_access_message
            await send_customer_access_message(user, course, plan, invite.invite_link)
        except Exception as notify_error:
            # Access is still provisioned; surface notification failure to the operator.
            return {"ok": True, "subscription": subscription, "warning": f"Access granted, but customer notification failed: {notify_error}"}
        log_activity("access_granted", "subscription", subscription.get("id") if subscription else subscription_id, "Telegram course access granted", {"user_id": user_id})
        return {"ok": True, "subscription": subscription, "invite_link": invite.invite_link}
    except Exception:
        if subscription:
            supabase.table("subscriptions").delete().eq("id", subscription["id"]).execute()
        raise
    finally:
        await bot.session.close()


@router.post("/api/students/{user_id}/invite")
async def dashboard_generate_student_invite(user_id: str, payload: dict = Body(...), _: str = Depends(require_dashboard_auth)):
    subscription_id = str(payload.get("subscription_id") or "")
    sub_resp = supabase.table("subscriptions").select(
        "id,user_id,course_id,plan_id,status,is_lifetime,expires_at"
    ).eq("id", subscription_id).eq("user_id", user_id).limit(1).execute()
    if not sub_resp.data:
        raise HTTPException(status_code=404, detail="Subscription not found.")
    sub = sub_resp.data[0]
    if sub.get("status") != "active":
        raise HTTPException(status_code=409, detail="Only active subscriptions can receive an invite.")

    user_resp = supabase.table("users").select("id,telegram_user_id,first_name,last_name").eq("id", user_id).limit(1).execute()
    if not user_resp.data:
        raise HTTPException(status_code=404, detail="Student not found.")
    course_resp = supabase.table("courses").select("id,name,status").eq("id", sub["course_id"]).limit(1).execute()
    if not course_resp.data:
        raise HTTPException(status_code=404, detail="Course not found.")
    course = course_resp.data[0]
    if course.get("status") != "active":
        raise HTTPException(status_code=409, detail="Course is inactive.")

    channel_resp = supabase.table("channels").select(
        "id,telegram_chat_id,channel_title,is_active,bot_is_admin,can_invite_users"
    ).eq("course_id", sub["course_id"]).eq("is_active", True).limit(1).execute()
    if not channel_resp.data:
        raise HTTPException(status_code=409, detail="No active Telegram group is configured for this course.")
    channel = channel_resp.data[0]
    if not channel.get("bot_is_admin") or not channel.get("can_invite_users"):
        raise HTTPException(status_code=409, detail="Admin Bot lacks Telegram invite permission for this course.")

    bot = await _admin_bot()
    try:
        invite_kwargs = {
            "chat_id": channel["telegram_chat_id"],
            "name": f"Dashboard Invite {user_resp.data[0].get('telegram_user_id')}",
            "member_limit": 1,
        }
        expires = _parse_dt(sub.get("expires_at"))
        if expires:
            invite_kwargs["expire_date"] = int(expires.timestamp())
        invite = await bot.create_chat_invite_link(**invite_kwargs)
        inv_resp = supabase.table("invite_links").insert({
            "subscription_id": subscription_id,
            "channel_id": channel["id"],
            "telegram_invite_link": invite.invite_link,
            "status": "sent",
            "sent_at": datetime.now(timezone.utc).isoformat(),
            "expires_at": sub.get("expires_at"),
        }).execute()
        if not inv_resp.data:
            raise RuntimeError("Invite link record was not created.")
        return {"ok": True, "invite_link": invite.invite_link, "course_name": course["name"]}
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Could not generate Telegram invite: {exc}")
    finally:
        await bot.session.close()


LOGIN_HTML = r'''
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>CourseFlow — Admin Login</title>
<style>
:root{color-scheme:dark;--bg:#080a0f;--panel:#11151d;--line:#252c39;--text:#f5f7fb;--muted:#8c95a6;--accent:#ff4f70;--accent2:#ff6b8a;--good:#5ee7a5;font-family:Inter,ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}
*{box-sizing:border-box}html,body{min-height:100%;margin:0}body{min-height:100vh;display:grid;place-items:center;overflow:auto;color:var(--text);background:radial-gradient(circle at 18% 8%,rgba(255,79,112,.13),transparent 27%),radial-gradient(circle at 84% 20%,rgba(155,108,255,.10),transparent 25%),linear-gradient(145deg,#080a0f,#0d1017)}
body:before{content:"";position:fixed;inset:0;pointer-events:none;opacity:.28;background-image:linear-gradient(rgba(255,255,255,.035) 1px,transparent 1px),linear-gradient(90deg,rgba(255,255,255,.035) 1px,transparent 1px);background-size:28px 28px;mask-image:linear-gradient(to bottom,#000,transparent)}
.login-shell{position:relative;width:min(430px,calc(100vw - 32px));padding:1px}.glow{position:absolute;inset:-70px;background:radial-gradient(circle,rgba(255,79,112,.16),transparent 58%);filter:blur(20px);pointer-events:none}.login-card{position:relative;border:1px solid var(--line);border-radius:22px;background:rgba(14,18,25,.92);box-shadow:0 30px 100px rgba(0,0,0,.48);padding:34px;backdrop-filter:blur(20px)}
.brand{display:flex;align-items:center;gap:11px;margin-bottom:32px}.brand-mark{width:34px;height:34px;border-radius:10px;background:linear-gradient(135deg,var(--accent),#ff7a62);box-shadow:0 0 30px rgba(255,79,112,.35);position:relative}.brand-mark:after{content:"";position:absolute;width:9px;height:9px;background:#fff;border-radius:50%;top:8px;left:8px;box-shadow:10px 9px 0 rgba(255,255,255,.58)}.brand b{font-size:18px;letter-spacing:-.035em}.brand span{display:block;color:var(--muted);font-size:10px;margin-top:2px}.eyebrow{font-size:10px;letter-spacing:.16em;text-transform:uppercase;color:var(--accent);font-weight:800}.login-card h1{margin:9px 0 9px;font-size:31px;letter-spacing:-.05em}.subtitle{color:var(--muted);font-size:13px;line-height:1.6;margin:0 0 26px}.field{margin-top:16px}.field label{display:block;color:#aeb6c6;font-size:11px;font-weight:700;margin-bottom:7px}.field input{width:100%;height:46px;border:1px solid var(--line);border-radius:11px;background:#0b0f16;color:var(--text);padding:0 13px;outline:none;transition:.18s}.field input:focus{border-color:rgba(255,79,112,.62);box-shadow:0 0 0 3px rgba(255,79,112,.10)}.login-btn{width:100%;height:46px;margin-top:22px;border:1px solid rgba(255,79,112,.5);border-radius:11px;color:#fff;background:linear-gradient(135deg,var(--accent),var(--accent2));font-weight:800;cursor:pointer;box-shadow:0 10px 30px rgba(255,79,112,.20)}.login-btn:hover{filter:brightness(1.05);transform:translateY(-1px)}.login-error{border:1px solid rgba(255,104,124,.28);background:rgba(255,104,124,.08);color:#ff9aaa;border-radius:10px;padding:10px 12px;font-size:12px;line-height:1.45;margin-bottom:14px}.secure-note{display:flex;align-items:center;gap:7px;margin-top:18px;color:#687184;font-size:10px}.secure-dot{width:6px;height:6px;border-radius:50%;background:var(--good);box-shadow:0 0 10px rgba(94,231,165,.65)}
@media(max-width:520px){.login-card{padding:26px 22px}.login-card h1{font-size:28px}}
</style>

<style>
/* V32.3 safe visual polish — no functionality changes */
.brand-mark{display:grid!important;place-items:center!important;overflow:hidden!important}
.brand-mark svg{width:100%!important;height:100%!important;display:block!important}
.nav .ico{display:inline-grid!important;place-items:center!important;width:34px!important;height:34px!important;border-radius:10px!important}
.nav .ico svg{width:18px!important;height:18px!important;fill:none!important;stroke:currentColor!important;stroke-width:1.7!important;stroke-linecap:round!important;stroke-linejoin:round!important}
.nav button{gap:10px!important}
</style>

<style>
/* ==========================================================
   V32.4 — UI POLISH ONLY
   Keep V32.3 functionality untouched.
   ========================================================== */
:root{
  --v-accent:#8b5cf6;
  --v-accent2:#22d3ee;
  --v-surface:rgba(15,23,42,.88);
  --v-surface2:rgba(17,25,39,.72);
  --v-border:rgba(148,163,184,.14);
  --v-text:#f8fafc;
  --v-muted:#94a3b8;
}
body{
  letter-spacing:-.005em;
}
.sidebar{
  backdrop-filter:blur(18px);
}
.brand{
  gap:11px!important;
}
.brand-mark{
  position:relative!important;
  flex:0 0 42px!important;
  border-radius:14px!important;
  background:linear-gradient(135deg,var(--v-accent),var(--v-accent2))!important;
  box-shadow:0 12px 35px rgba(139,92,246,.25)!important;
}
.brand-mark:after{
  content:"";
  position:absolute;
  inset:1px;
  border-radius:13px;
  border:1px solid rgba(255,255,255,.22);
  pointer-events:none;
}
.nav button{
  position:relative;
  overflow:hidden;
}
.nav button.active:after{
  content:"";
  position:absolute;
  right:8px;
  width:5px;
  height:5px;
  border-radius:50%;
  background:#22d3ee;
  box-shadow:0 0 12px rgba(34,211,238,.8);
}
.main{
  max-width:1700px;
}
.topbar{
  border-bottom:1px solid rgba(148,163,184,.10)!important;
}
.hero-card{
  position:relative;
}
.hero-card:after{
  content:"";
  position:absolute;
  width:260px;height:260px;
  right:-90px;bottom:-120px;
  border-radius:50%;
  background:radial-gradient(circle,rgba(34,211,238,.12),transparent 65%);
  pointer-events:none;
}
.stat{
  transition:transform .18s ease,border-color .18s ease,box-shadow .18s ease!important;
}
.stat:hover{
  transform:translateY(-3px);
  border-color:rgba(139,92,246,.25)!important;
  box-shadow:0 22px 55px rgba(0,0,0,.22)!important;
}
.stat-value{
  font-variant-numeric:tabular-nums;
}
.panel{
  transition:border-color .18s ease,box-shadow .18s ease;
}
.panel:hover{
  border-color:rgba(148,163,184,.18)!important;
}
.panel-title{
  min-height:68px;
}
.panel-title .action-btn,
.section-head .action-btn{
  white-space:nowrap;
}
.filter-bar{
  padding:14px 18px!important;
  background:rgba(255,255,255,.012);
  border-bottom:1px solid rgba(148,163,184,.08);
}
.search-input,.select-dark,.textarea-dark{
  transition:border-color .16s ease,box-shadow .16s ease,background .16s ease!important;
}
.search-input:hover,.select-dark:hover,.textarea-dark:hover{
  background:#0b111d!important;
}
.action-btn{
  min-height:38px!important;
}
.action-btn.primary{
  background:linear-gradient(135deg,#8b5cf6,#6366f1)!important;
}
.row-actions{
  align-items:center!important;
}
.status{
  white-space:nowrap;
}
.course-tabs{
  position:sticky;
  top:0;
  z-index:10;
  padding:10px 0;
  background:linear-gradient(180deg,#101827 75%,rgba(16,24,39,0));
  backdrop-filter:blur(10px);
}
.course-tab{
  animation:vTabIn .16s ease-out;
}
@keyframes vTabIn{
  from{opacity:.55;transform:translateY(3px)}
  to{opacity:1;transform:none}
}
.detail-card{
  box-shadow:0 12px 35px rgba(0,0,0,.12)!important;
}
.plan-card{
  transition:transform .16s ease,border-color .16s ease,background .16s ease!important;
}
.plan-card:hover{
  transform:translateY(-2px);
  border-color:rgba(139,92,246,.24)!important;
  background:linear-gradient(145deg,rgba(20,30,48,.94),rgba(10,16,27,.94))!important;
}
.settings-row{
  min-height:42px;
}
.modal-actions{
  position:sticky!important;
  bottom:0!important;
  z-index:12;
  padding:14px 0 2px!important;
  background:linear-gradient(180deg,rgba(10,16,27,0),rgba(10,16,27,.98) 35%)!important;
}
.modal-body{
  scrollbar-width:thin;
}
.modal-body::-webkit-scrollbar{
  width:8px;
}
.modal-body::-webkit-scrollbar-thumb{
  background:rgba(139,92,246,.42);
  border-radius:99px;
}
.notice{
  border-radius:13px!important;
}
.empty{
  min-height:80px;
  display:flex;
  align-items:center;
  justify-content:center;
  padding:18px!important;
}
@media(max-width:760px){
  .panel-title{
    min-height:auto;
  }
  .filter-bar{
    gap:8px!important;
  }
  .course-tabs{
    overflow-x:auto;
    flex-wrap:nowrap!important;
  }
  .course-tabs .action-btn{
    flex:0 0 auto;
  }
  .modal-actions{
    padding-bottom:8px!important;
  }
}
</style>
</head>
<body>
<div class="login-shell"><div class="glow"></div><section class="login-card">
  <div class="brand"><div class="brand-mark"></div><div><b>CourseFlow</b><span>Private Control Center</span></div></div>
  <div class="eyebrow">Administrator access</div>
  <h1>Welcome back.</h1>
  <p class="subtitle">Sign in to manage your courses, students, payments and Telegram access from one private dashboard.</p>
  <!--ERROR-->
  <form method="post" action="/dashboard/login" autocomplete="on">
    <div class="field"><label for="username">Admin ID</label><input id="username" name="username" type="text" autocomplete="username" placeholder="Enter admin ID" required autofocus></div>
    <div class="field"><label for="password">Password</label><input id="password" name="password" type="password" autocomplete="current-password" placeholder="Enter password" required></div>
    <button class="login-btn" type="submit">Sign in to Dashboard</button>
  </form>
  <div class="secure-note"><span class="secure-dot"></span> Private dashboard · Session stays active until logout</div>
</section></div>
</body></html>
'''

DASHBOARD_HTML = r'''
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>CourseFlow — Private Control Center</title>
<style>
:root{
  color-scheme:dark;
  --bg:#080a0f;--bg2:#0d1017;--panel:#11151d;--panel2:#151a23;
  --line:#232936;--line2:#303747;--text:#f5f7fb;--muted:#8c95a6;
  --accent:#ff4f70;--accent2:#ff6b8a;--violet:#9b6cff;--cyan:#5ee7d5;
  --good:#5ee7a5;--warn:#ffd166;--danger:#ff687c;
  --shadow:0 18px 60px rgba(0,0,0,.35);
  font-family:Inter,ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;
}
*{box-sizing:border-box}html{scroll-behavior:smooth;height:100%;overflow:hidden}body{margin:0;min-height:100vh;height:100%;overflow:hidden;color:var(--text);background:
radial-gradient(circle at 18% 4%,rgba(255,79,112,.10),transparent 24%),
radial-gradient(circle at 88% 16%,rgba(155,108,255,.08),transparent 23%),
linear-gradient(var(--bg),var(--bg2));}
body:before{content:"";position:fixed;inset:0;pointer-events:none;opacity:.34;background-image:linear-gradient(rgba(255,255,255,.035) 1px,transparent 1px),linear-gradient(90deg,rgba(255,255,255,.035) 1px,transparent 1px);background-size:28px 28px;mask-image:linear-gradient(to bottom,#000 0%,rgba(0,0,0,.65) 65%,transparent 100%)}
button,input{font:inherit}.app{position:relative;display:grid;grid-template-columns:244px minmax(0,1fr);min-height:100vh}.sidebar{position:sticky;top:0;height:100vh;border-right:1px solid var(--line);background:rgba(8,10,15,.84);backdrop-filter:blur(18px);padding:22px 16px;display:flex;flex-direction:column;z-index:10}.brand{display:flex;align-items:center;gap:10px;padding:6px 10px 28px}.brand-mark{width:30px;height:30px;border-radius:9px;background:linear-gradient(135deg,var(--accent),#ff7a62);box-shadow:0 0 28px rgba(255,79,112,.35);position:relative}.brand-mark:after{content:"";position:absolute;width:8px;height:8px;background:#fff;border-radius:50%;top:7px;left:7px;box-shadow:9px 8px 0 rgba(255,255,255,.6)}.brand b{font-size:17px;letter-spacing:-.03em}.brand span{display:block;color:var(--muted);font-size:10px;margin-top:2px}.eyebrow{font-size:10px;letter-spacing:.14em;text-transform:uppercase;color:#687184;padding:0 10px 10px}.nav{display:grid;gap:6px}.nav button{width:100%;text-align:left;border:1px solid transparent;background:transparent;color:#aeb6c6;padding:11px 12px;border-radius:11px;cursor:pointer;display:flex;align-items:center;gap:10px;transition:.18s}.nav button:hover{background:#11161f;color:#fff;border-color:#1f2633}.nav button.active{color:#fff;background:linear-gradient(90deg,rgba(255,79,112,.16),rgba(255,79,112,.04));border-color:rgba(255,79,112,.22);box-shadow:inset 3px 0 var(--accent)}.ico{width:18px;height:18px;display:grid;place-items:center;color:#8992a4}.nav button.active .ico{color:var(--accent)}.sidebar-bottom{margin-top:auto;border-top:1px solid var(--line);padding:15px 8px 0}.health{display:flex;align-items:center;gap:8px;font-size:12px;color:#b9c1ce}.dot{width:7px;height:7px;border-radius:50%;background:var(--good);box-shadow:0 0 12px rgba(94,231,165,.75)}.main{min-width:0;height:100vh;min-height:0;overflow-y:auto;overflow-x:hidden;padding:26px 30px 50px;scrollbar-gutter:stable}.topbar{display:flex;align-items:center;justify-content:space-between;gap:18px;margin-bottom:30px}.crumb{color:var(--muted);font-size:12px}.crumb strong{color:#dfe4ed}.top-actions{display:flex;gap:8px}.ghost,.primary{border:1px solid var(--line2);border-radius:10px;padding:9px 13px;color:#e9edf4;background:#10141c;cursor:pointer}.ghost:hover{background:#171c26}.primary{border-color:rgba(255,79,112,.5);background:linear-gradient(135deg,#ff4f70,#ff5d7d);box-shadow:0 8px 30px rgba(255,79,112,.2);font-weight:700}.hero{display:grid;grid-template-columns:minmax(0,1.25fr) minmax(310px,.75fr);gap:18px;margin-bottom:18px}.hero-card,.terminal{border:1px solid var(--line);border-radius:18px;background:linear-gradient(145deg,rgba(20,24,33,.92),rgba(12,15,22,.94));box-shadow:var(--shadow);overflow:hidden}.hero-card{padding:30px;position:relative}.hero-card:after{content:"";position:absolute;width:280px;height:280px;right:-100px;top:-130px;background:radial-gradient(circle,rgba(255,79,112,.20),transparent 67%);pointer-events:none}.kicker{font-size:10px;letter-spacing:.16em;text-transform:uppercase;color:var(--accent);font-weight:800}.hero h1{font-size:clamp(30px,4vw,52px);line-height:.98;letter-spacing:-.055em;margin:12px 0 14px;max-width:650px}.hero p{color:#929bab;max-width:620px;line-height:1.65;margin:0}.terminal{padding:0}.terminal-head{height:40px;border-bottom:1px solid var(--line);display:flex;align-items:center;gap:7px;padding:0 15px;color:#687184;font-size:11px}.term-dot{width:8px;height:8px;border-radius:50%;background:#ff5f57}.term-dot:nth-child(2){background:#febc2e}.term-dot:nth-child(3){background:#28c840}.terminal-body{padding:20px 20px 24px;font-family:"SFMono-Regular",Consolas,monospace;font-size:12px;line-height:1.9;color:#b9c1cf}.pink{color:var(--accent)}.green{color:var(--good)}.purple{color:#b58dff}.stat-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-bottom:18px}.stat{border:1px solid var(--line);background:rgba(16,20,28,.88);border-radius:15px;padding:17px;min-height:105px;position:relative;overflow:hidden}.stat:after{content:"";position:absolute;width:100px;height:100px;right:-50px;bottom:-50px;background:radial-gradient(circle,rgba(255,79,112,.13),transparent 65%)}.stat-label{font-size:11px;text-transform:uppercase;letter-spacing:.09em;color:#7f8899}.stat-value{font-size:25px;font-weight:800;letter-spacing:-.04em;margin-top:9px}.stat-meta{font-size:11px;color:#6f7889;margin-top:6px}.stat.accent .stat-value{color:#ff7893}.workspace{border-top:1px solid var(--line);padding-top:20px}.section-head{display:flex;justify-content:space-between;align-items:end;gap:15px;margin:0 0 12px}.section-head h2{margin:0;font-size:20px;letter-spacing:-.03em}.section-head p{margin:4px 0 0;color:#737d8e;font-size:12px}.tabs{display:flex;gap:7px;overflow:auto;padding-bottom:10px}.tabs button{white-space:nowrap;border:1px solid var(--line);background:#10141b;color:#8f98a9;padding:9px 13px;border-radius:9px;cursor:pointer}.tabs button.active{color:#fff;border-color:rgba(255,79,112,.4);background:rgba(255,79,112,.10)}.view-shell.hidden{display:none}.module-view{display:grid;gap:16px}.module-view>.panel{display:none}.module-view>.panel.active{display:block}.notification-wrap{position:relative}.bell-btn{position:relative}.notification-badge{display:inline-grid;place-items:center;min-width:18px;height:18px;padding:0 5px;margin-left:4px;border-radius:999px;background:var(--accent);color:#fff;font-size:10px;font-weight:800}.notification-panel{position:absolute;right:0;top:46px;width:min(380px,calc(100vw - 32px));max-height:430px;overflow:auto;border:1px solid var(--line2);background:#0d1118;border-radius:14px;box-shadow:0 25px 80px rgba(0,0,0,.55);display:none;z-index:500}.notification-panel.open{display:block}.notification-head{display:flex;justify-content:space-between;align-items:center;padding:12px 14px;border-bottom:1px solid var(--line)}.notification-head .ghost{padding:5px 8px}.notification-item{display:grid;grid-template-columns:10px 1fr auto;gap:10px;padding:13px 14px;border-bottom:1px solid #1e2530;cursor:pointer}.notification-item:hover{background:rgba(255,255,255,.025)}.notification-dot{width:8px;height:8px;border-radius:50%;margin-top:5px;background:var(--cyan)}.notification-dot.warning{background:var(--warn)}.notification-dot.danger{background:var(--danger)}.notification-title{font-size:12px;font-weight:800}.notification-detail{font-size:11px;color:var(--muted);margin-top:3px;line-height:1.45}.notification-count{font-size:11px;color:#aeb6c6}.panel{display:none;border:1px solid var(--line);background:rgba(14,18,25,.9);border-radius:16px;overflow:hidden;box-shadow:var(--shadow)}.panel.active{display:block}.panel-title{display:flex;align-items:center;justify-content:space-between;padding:17px 18px;border-bottom:1px solid var(--line)}.panel-title b{font-size:14px}.panel-title span{color:#687184;font-size:11px}.table-wrap{overflow:auto}table{width:100%;border-collapse:collapse;font-size:12px;min-width:700px}th,td{text-align:left;padding:13px 14px;border-bottom:1px solid #1e2530;vertical-align:top}th{color:#687184;font-size:10px;text-transform:uppercase;letter-spacing:.08em;font-weight:700}td{color:#cbd1dc}tbody tr:hover{background:rgba(255,255,255,.018)}.muted{color:#697486;font-size:11px;line-height:1.5}.status{padding:4px 8px;border-radius:999px;font-size:10px;display:inline-block;border:1px solid #303848;background:#181e29;color:#b7bfcc}.active-status{background:rgba(94,231,165,.08);border-color:rgba(94,231,165,.25);color:var(--good)}.pending-status{background:rgba(255,209,102,.08);border-color:rgba(255,209,102,.25);color:var(--warn)}.danger-status{background:rgba(255,104,124,.08);border-color:rgba(255,104,124,.25);color:var(--danger)}.empty{padding:36px;text-align:center;color:#6d7687}.footer-note{margin-top:18px;color:#5f6879;font-size:10px;text-align:center}
.settings-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px;padding:18px}.settings-card{border:1px solid var(--line);border-radius:14px;background:#0e131b;padding:16px}.bot-roles-card{grid-column:1/-1}.bot-roles-list{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px;margin-top:14px}.bot-role{border:1px solid var(--line);border-radius:12px;background:#10151e;padding:14px}.bot-role-head{display:flex;justify-content:space-between;align-items:flex-start;gap:10px}.bot-role-name{font-size:13px;font-weight:800}.bot-role-sub{font-size:10px;color:var(--muted);margin-top:3px}.bot-role-meta{display:grid;gap:7px;margin-top:12px}.bot-role-row{display:flex;justify-content:space-between;gap:12px;border-bottom:1px solid #1e2530;padding-bottom:7px;font-size:11px}.bot-role-row:last-child{border-bottom:0;padding-bottom:0}.bot-role-label{color:#737d8e}.bot-role-value{color:#d6dbe5;text-align:right;word-break:break-word}.bot-role-uses{margin-top:12px;color:#aeb6c6;font-size:11px;line-height:1.65}.bot-role-uses b{color:#e5e9f0}@media(max-width:850px){.settings-grid{grid-template-columns:1fr}.bot-roles-card{grid-column:auto}.bot-roles-list{grid-template-columns:1fr}}.settings-card-head{display:flex;align-items:flex-start;justify-content:space-between;gap:12px}.settings-list{margin-top:14px;display:grid;gap:8px}.settings-row{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:10px 0;border-bottom:1px solid #1e2530;font-size:12px;color:#cbd1dc}.settings-row:last-child{border-bottom:0}.settings-actions{margin-top:14px}@media(max-width:850px){.settings-grid{grid-template-columns:1fr}}
@media(max-width:1050px){.app{grid-template-columns:78px 1fr}.sidebar{padding:18px 10px}.brand{justify-content:center;padding:6px 0 25px}.brand>div:last-child,.eyebrow,.nav button span:last-child,.sidebar-bottom span{display:none}.nav button{justify-content:center}.main{padding:22px}.hero{grid-template-columns:1fr}.stat-grid{grid-template-columns:repeat(2,1fr)}}
@media(max-width:650px){.app{display:block}.sidebar{position:sticky;height:auto;top:0;border-right:0;border-bottom:1px solid var(--line);padding:10px;flex-direction:row;align-items:center}.brand{padding:0;margin-right:10px}.nav{display:flex;flex:1;overflow:auto}.nav button{min-width:44px;width:auto}.sidebar-bottom{display:none}.main{padding:16px}.topbar{margin-bottom:18px}.hero-card{padding:22px}.hero h1{font-size:34px}.stat-grid{grid-template-columns:1fr 1fr}.stat{min-height:95px}.stat-value{font-size:21px}.top-actions .ghost{display:none}}

.form-grid{display:grid;grid-template-columns:1fr 220px;gap:14px}.form-field{display:grid;gap:7px}.form-field label{font-size:10px;text-transform:uppercase;letter-spacing:.09em;color:#7f8899}.textarea-dark,.select-dark{width:100%;background:#0c1017;border:1px solid var(--line2);color:var(--text);padding:10px 12px;border-radius:10px;outline:none}.textarea-dark{resize:vertical;min-height:120px}.select-dark:focus,.textarea-dark:focus{border-color:rgba(255,79,112,.55);box-shadow:0 0 0 3px rgba(255,79,112,.08)}
.plan-list{display:grid;gap:10px;margin-top:18px}.plan-card{border:1px solid var(--line);background:#0d1219;border-radius:12px;padding:13px}.plan-card-head{display:flex;justify-content:space-between;gap:10px;align-items:flex-start}.upload-row{display:flex;gap:8px;align-items:center;flex-wrap:wrap}.file-input{max-width:100%;color:#9aa4b5;font-size:11px}.search-input{width:min(420px,100%);background:#0c1017;border:1px solid var(--line2);color:var(--text);padding:10px 12px;border-radius:10px;outline:none}.search-input:focus{border-color:rgba(255,79,112,.55);box-shadow:0 0 0 3px rgba(255,79,112,.08)}
.row-actions{display:flex;gap:6px;flex-wrap:wrap}.action-btn{border:1px solid var(--line2);background:#10151e;color:#dfe5ef;border-radius:8px;padding:7px 9px;cursor:pointer;font-size:12px}.action-btn:hover{border-color:rgba(255,79,112,.45);color:#fff}.action-btn.primary{background:linear-gradient(135deg,#ff4f70,#ff5d7d);border-color:#ff4f70;color:#fff}.action-btn.danger{border-color:rgba(255,104,124,.4);color:#ff9aaa}.action-btn.good{border-color:rgba(94,231,165,.35);color:#8df0ba}
.modal-backdrop{position:fixed;inset:0;background:rgba(0,0,0,.72);backdrop-filter:blur(10px);z-index:100;display:none;align-items:center;justify-content:center;padding:20px;isolation:isolate}.modal-backdrop.plan-layer{z-index:400}.modal-backdrop.course-layer{z-index:100}.modal-backdrop.student-layer{z-index:200}.modal-backdrop.payment-layer{z-index:200}.modal-backdrop.open{display:flex}.modal-open{overflow:hidden}.modal{width:min(920px,100%);max-height:88vh;overflow:auto;border:1px solid var(--line2);border-radius:18px;background:#0d1118;box-shadow:0 30px 100px rgba(0,0,0,.65)}.modal-head{position:sticky;top:0;z-index:2;display:flex;align-items:center;justify-content:space-between;padding:20px;border-bottom:1px solid var(--line);background:rgba(13,17,24,.94);backdrop-filter:blur(16px)}.modal-head h3{margin:4px 0 0;font-size:24px}.modal-body{padding:20px}.telegram-box{margin-top:12px;padding:14px;border:1px solid var(--line2);border-radius:14px;background:rgba(255,255,255,.02)}.telegram-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px}.telegram-grid .detail-card{min-height:88px}@media(max-width:900px){.telegram-grid{grid-template-columns:1fr}}.detail-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:10px}.detail-card{border:1px solid var(--line);border-radius:12px;background:#11161f;padding:14px}.detail-label{font-size:10px;text-transform:uppercase;letter-spacing:.1em;color:#737d90}.detail-value{font-weight:700;margin-top:6px;word-break:break-word}.sub-card{border:1px solid var(--line);border-radius:14px;padding:16px;margin-top:12px;background:linear-gradient(145deg,#11161f,#0d1118)}.sub-head{display:flex;justify-content:space-between;gap:10px;align-items:flex-start}.select-dark{background:#0b0f15;border:1px solid var(--line2);color:#fff;border-radius:9px;padding:9px;min-width:180px}.modal-actions{display:flex;gap:8px;flex-wrap:wrap;margin-top:12px}.telegram-connect-panel{border:1px solid var(--line2);border-radius:14px;padding:16px;background:linear-gradient(145deg,#11161f,#0d1118)}.telegram-destination-head{display:flex;justify-content:space-between;align-items:flex-start;gap:12px}.telegram-destination-head h3{margin:4px 0;font-size:18px}.telegram-connect-row{display:flex;gap:8px;align-items:center;flex-wrap:wrap}.telegram-connect-row .search-input{flex:1;min-width:240px}.telegram-connect-row .action-btn{white-space:nowrap}.warning-box{border:1px solid rgba(255,209,102,.25);background:rgba(255,209,102,.06);border-radius:10px;padding:10px;color:#f5d88a;font-size:12px}.success-box{border:1px solid rgba(94,231,165,.25);background:rgba(94,231,165,.06);border-radius:10px;padding:10px;color:#9af2c1;font-size:12px}
@media(max-width:850px){.detail-grid{grid-template-columns:1fr 1fr}.panel-title{gap:12px;align-items:flex-start;flex-direction:column}}@media(max-width:560px){.detail-grid{grid-template-columns:1fr}.modal{max-height:94vh}}
.broadcast-grid{display:grid;grid-template-columns:minmax(0,1.05fr) minmax(320px,.95fr);gap:16px;padding:18px}.broadcast-form,.broadcast-preview{border:1px solid var(--line);border-radius:14px;background:#0e131b;padding:16px}.broadcast-form label{display:block;font-size:11px;text-transform:uppercase;letter-spacing:.08em;color:#8c95a6;margin-bottom:7px}.broadcast-preview .preview-count{font-size:34px;font-weight:800}.broadcast-sample{margin-top:12px;display:grid;gap:7px}.broadcast-sample div{padding:9px 10px;border:1px solid var(--line);border-radius:9px;background:#11161f}.broadcast-send{margin-top:14px}@media(max-width:850px){.broadcast-grid{grid-template-columns:1fr}}

.filter-bar{display:flex;gap:10px;flex-wrap:wrap;padding:0 18px 14px}.filter-bar .search-input,.filter-bar .select-dark{min-width:170px}.filter-bar .search-input{flex:1}.activity-grid{display:grid;grid-template-columns:1.1fr .9fr;gap:14px}.analytics-cards{display:grid;grid-template-columns:repeat(4,1fr);gap:12px}.mini-stat{border:1px solid var(--line);background:#10141c;border-radius:14px;padding:16px}.mini-stat .value{font-size:24px;font-weight:800;margin-top:7px}.mini-stat .label{font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:.08em}.activity-list{display:grid;gap:8px}.activity-item{display:grid;grid-template-columns:150px 1fr auto;gap:12px;align-items:center;border:1px solid var(--line);background:#10141c;border-radius:12px;padding:12px}.activity-action{font-weight:800;color:#fff}.activity-time{font-size:11px;color:var(--muted)}.activity-desc{font-size:13px;color:#c8ced9}.activity-pill{font-size:10px;padding:5px 8px;border-radius:999px;background:rgba(94,231,165,.1);color:var(--good);border:1px solid rgba(94,231,165,.2)}@media(max-width:1050px){.analytics-cards{grid-template-columns:repeat(2,1fr)}.activity-grid{grid-template-columns:1fr}}@media(max-width:650px){.analytics-cards{grid-template-columns:1fr 1fr}.activity-item{grid-template-columns:1fr}.activity-pill{justify-self:start}}


/* V26 STUDENT PROFILE — scoped visual layer */
.student-profile{padding:20px;overflow:visible}.profile-hero{display:grid;grid-template-columns:auto 1fr auto;align-items:center;gap:14px;padding:18px;border:1px solid var(--line);border-radius:15px;background:linear-gradient(145deg,#121823,#0d1118)}.profile-avatar{width:54px;height:54px;border-radius:16px;display:grid;place-items:center;background:linear-gradient(135deg,var(--accent),var(--violet));font-size:22px;font-weight:900;color:#fff;box-shadow:0 12px 35px rgba(255,79,112,.22)}.profile-main h2{margin:3px 0 5px;font-size:24px;letter-spacing:-.04em}.profile-subline{font-size:11px;color:var(--muted)}.profile-joined{text-align:right;font-size:10px;color:var(--muted);line-height:1.6}.profile-joined b{color:#dce1ea;font-size:11px}.profile-stats{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin:12px 0}.profile-tabs{display:flex;gap:7px;overflow:auto;padding:4px 0 12px;border-bottom:1px solid var(--line)}.profile-tab{border:1px solid var(--line);background:#10151e;color:#8f98a9;padding:9px 13px;border-radius:9px;cursor:pointer;white-space:nowrap}.profile-tab.active{color:#fff;border-color:rgba(255,79,112,.4);background:rgba(255,79,112,.10)}.student-tab{display:none;padding-top:4px}.student-tab.active{display:block}.history-list{display:grid;gap:8px}.history-item{display:flex;justify-content:space-between;align-items:flex-start;gap:14px;border:1px solid var(--line);background:#10151e;border-radius:12px;padding:12px}.history-right{text-align:right;display:grid;justify-items:end;gap:5px;min-width:130px}.history-right .status{white-space:nowrap}@media(max-width:700px){.profile-hero{grid-template-columns:auto 1fr}.profile-joined{grid-column:2;text-align:left}.profile-stats{grid-template-columns:1fr 1fr}.history-item{flex-direction:column}.history-right{justify-items:start;text-align:left}.student-profile{padding:14px}}
/* V22.2 POLISHED UI — visual layer only; existing markup/actions preserved */
:root{
 --bg:#07090d;--bg2:#0b0e14;--panel:#0f131b;--panel2:#131923;--panel3:#171d28;
 --line:#202734;--line2:#2b3444;--text:#f7f8fb;--muted:#8791a3;--muted2:#657083;
 --accent:#ff4f70;--accent2:#ff718b;--violet:#8b7cff;--cyan:#52dfd0;
 --good:#50d99a;--warn:#f3c65b;--danger:#ff687c;
 --shadow:0 20px 70px rgba(0,0,0,.32);--radius:16px;
}
html{background:var(--bg)}body{background:radial-gradient(900px 500px at 15% -10%,rgba(255,79,112,.10),transparent 60%),radial-gradient(800px 500px at 95% 0%,rgba(139,124,255,.08),transparent 60%),linear-gradient(180deg,#07090d 0%,#0a0d12 100%);font-size:13px}
body:before{opacity:.16;background-size:32px 32px}
.app{grid-template-columns:250px minmax(0,1fr)}
.sidebar{background:rgba(8,10,14,.92);border-right:1px solid #1c222d;padding:20px 14px;backdrop-filter:blur(22px)}
.brand{padding:6px 10px 24px}.brand-mark{width:32px;height:32px;border-radius:10px}.brand b{font-size:16px}.brand span{font-size:9px;letter-spacing:.08em;text-transform:uppercase;color:#687286}
.eyebrow{font-size:9px;letter-spacing:.16em;color:#596477;padding:4px 10px 9px}.nav{gap:4px}.nav button{height:42px;padding:0 12px;border-radius:10px;color:#8e98a9}.nav button:hover{background:#11161e;border-color:#1b2330;color:#e8ebf2}.nav button.active{background:linear-gradient(90deg,rgba(255,79,112,.15),rgba(255,79,112,.045));border-color:rgba(255,79,112,.2);box-shadow:inset 3px 0 var(--accent)}
.sidebar-bottom{padding:14px 9px 0}.health{font-size:11px;color:#8e98a9}.dot{width:6px;height:6px}
.main{padding:22px 34px 48px;max-width:1600px;width:100%;margin:0 auto}.topbar{min-height:48px;margin-bottom:24px}.crumb{font-size:11px;color:#687286}.crumb strong{font-size:14px;color:#eef1f6}.top-actions{gap:7px}.ghost,.primary{height:38px;padding:0 12px;border-radius:10px}.ghost{background:#0e131a;border-color:#232b38;color:#aeb7c6}.ghost:hover{background:#151b24;border-color:#30394a}.primary{background:linear-gradient(135deg,#ff4f70,#ff6683);border-color:rgba(255,115,139,.45);box-shadow:0 7px 24px rgba(255,79,112,.16)}
.hero{grid-template-columns:minmax(0,1.45fr) minmax(280px,.75fr);gap:14px;margin-bottom:14px}.hero-card,.terminal{border-radius:18px;border-color:#202735;background:linear-gradient(145deg,rgba(17,21,29,.96),rgba(11,14,20,.96));box-shadow:0 16px 50px rgba(0,0,0,.22)}.hero-card{padding:28px}.hero h1{font-size:clamp(28px,3.2vw,44px);margin:10px 0 12px}.hero p{font-size:13px;line-height:1.65;color:#8b95a7}.kicker{font-size:9px}.terminal-head{height:38px}.terminal-body{padding:17px 18px;font-size:11px;line-height:1.85}
.stat-grid{grid-template-columns:repeat(4,minmax(0,1fr));gap:10px;margin-bottom:14px}.stat{min-height:100px;padding:15px;border-radius:14px;background:linear-gradient(145deg,#10151d,#0d1118);border-color:#202734}.stat-label{font-size:9px;color:#697486}.stat-value{font-size:24px;margin-top:8px}.stat-meta{font-size:10px;color:#697486}.workspace{border-top:1px solid #1d2430;padding-top:18px}
.section-head{margin-bottom:10px}.section-head h2{font-size:18px}.section-head p{font-size:11px;color:#6e788a}.panel{border-radius:15px;background:rgba(13,17,24,.96);border-color:#202734;box-shadow:0 12px 40px rgba(0,0,0,.16)}.panel-title{padding:15px 16px;border-bottom-color:#1e2530}.panel-title b{font-size:13px}.panel-title span{font-size:10px}
.filter-bar{padding:12px 16px 13px;background:#0d1118;border-bottom:1px solid #1e2530;gap:8px}.search-input,.select-dark,.textarea-dark{background:#090d13!important;border:1px solid #27303d!important;color:#e8ecf3!important;border-radius:9px}.search-input{height:36px;padding:0 11px}.select-dark{height:36px;padding:0 10px}.textarea-dark{padding:10px;line-height:1.5}.search-input:focus,.select-dark:focus,.textarea-dark:focus{outline:none;border-color:rgba(255,79,112,.55)!important;box-shadow:0 0 0 3px rgba(255,79,112,.08)}
.table-wrap{overflow:auto}table{min-width:760px;font-size:12px}th{background:#0d1118;color:#667185;font-size:9px;padding:11px 14px;border-bottom:1px solid #222a36;position:sticky;top:0;z-index:1}td{padding:13px 14px;border-bottom:1px solid #1b222d;color:#c9d0dc}tbody tr{transition:background .15s}tbody tr:hover{background:rgba(255,255,255,.022)}
.status{font-size:9px;padding:4px 8px;background:#171c25;border-color:#2b3442}.active-status{background:rgba(80,217,154,.08);border-color:rgba(80,217,154,.24)}.pending-status{background:rgba(243,198,91,.08);border-color:rgba(243,198,91,.24)}.danger-status{background:rgba(255,104,124,.08);border-color:rgba(255,104,124,.24)}
.action-btn{height:32px;padding:0 10px;border-radius:8px;border:1px solid #2a3342;background:#11161e;color:#c7ced9;cursor:pointer;transition:.15s}.action-btn:hover{background:#181f29;border-color:#3a4557;color:#fff;transform:translateY(-1px)}.action-btn.primary{background:linear-gradient(135deg,#ff4f70,#ff6683);border-color:rgba(255,115,139,.4);color:#fff}.action-btn.good{background:rgba(80,217,154,.08);border-color:rgba(80,217,154,.25);color:#70e5aa}.action-btn.danger{background:rgba(255,104,124,.07);border-color:rgba(255,104,124,.22);color:#ff8293}.row-actions,.modal-actions{gap:6px}
.empty{padding:44px 20px;color:#697486;font-size:12px}.muted{font-size:10px;color:#6d7788}
.overview-cards{gap:10px}.analytics-cards{gap:10px}.mini-stat{border-radius:13px;background:#10151d;border-color:#202734;padding:15px}.mini-stat .value{font-size:22px}.mini-stat .label{font-size:9px}
.activity-list{gap:7px}.activity-item{grid-template-columns:155px 1fr auto;padding:12px;border-radius:11px;background:#10151a;border-color:#202734}.activity-action{font-size:12px}.activity-desc{font-size:12px}.activity-pill{font-size:9px}
.settings-grid{padding:16px;gap:12px}.settings-card{border-radius:13px;background:#0f141c;border-color:#202734;padding:15px}.bot-roles-list{gap:10px}.bot-role{border-radius:11px;background:#11161e;border-color:#252e3c;padding:14px}.bot-role-name{font-size:13px}.bot-role-label,.bot-role-meta{font-size:9px}
.broadcast-grid{padding:16px;gap:12px}.broadcast-form,.broadcast-preview{border-radius:13px;background:#0f141c;border-color:#202734;padding:15px}.broadcast-form label{font-size:9px}.broadcast-preview .preview-count{font-size:32px}
.plan-list{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px;padding:16px}.plan-card{margin:0;border:1px solid #202734;border-radius:12px;background:#10151d;padding:14px}.plan-card-head{gap:10px}.plan-card b{font-size:13px}
.telegram-grid{gap:10px}.telegram-box{border:1px solid #202734!important;background:#0f141c!important;border-radius:12px!important;padding:14px!important}.detail-grid{gap:10px}.detail-card{border:1px solid #202734;border-radius:11px;background:#10151d;padding:13px}.detail-label{font-size:9px;color:#667185;text-transform:uppercase;letter-spacing:.07em}.detail-value{font-size:13px;color:#e1e6ee;margin-top:5px}
.modal-backdrop{background:rgba(2,4,8,.74);backdrop-filter:blur(8px);z-index:1000;overflow:auto;align-items:flex-start}.modal{width:min(920px,calc(100vw - 30px));max-height:calc(100vh - 40px);min-height:0;display:flex;flex-direction:column;border:1px solid #30394a;border-radius:18px;background:#0b0f15;box-shadow:0 30px 100px rgba(0,0,0,.62);overflow:hidden;margin:auto}.modal-head{flex:0 0 auto;padding:16px 18px;background:#0e131b;border-bottom:1px solid #202734}.modal-body{padding:18px;flex:1 1 auto;min-height:0;overflow-y:auto;overflow-x:hidden;overscroll-behavior:contain;scrollbar-gutter:stable}.sub-card{border-radius:12px;background:#10151d;border-color:#202734;padding:14px}.sub-head b{font-size:13px}
.notification-panel{background:#0c1118;border-color:#2a3341;border-radius:14px}.notification-head{padding:12px 14px}.notification-item{padding:12px 14px}.notification-item:hover{background:#121821}
.footer-note{margin-top:16px;color:#535d6d}
/* V25.1 GLOBAL SCROLL: every page/view and dynamic detail surface stays reachable */
.view-shell,.module-view{min-height:0}
.view-shell{overflow:visible}
.module-view{overflow:visible}
.panel.active{min-width:0}
.modal-backdrop.open{overflow:auto}
.modal-body>*{max-width:100%}
@media(max-width:1100px){.app{grid-template-columns:220px minmax(0,1fr)}.main{padding:20px 22px 42px}.hero{grid-template-columns:1fr}.stat-grid{grid-template-columns:repeat(2,1fr)}.plan-list{grid-template-columns:1fr}}
@media(max-width:760px){.app{display:block;height:100vh}.main{height:calc(100vh - 58px);min-height:0;overflow-y:auto;overflow-x:hidden}.sidebar{position:sticky;height:auto;top:0;padding:10px 12px;z-index:100;flex-direction:row;align-items:center;gap:10px;overflow-x:auto}.brand{padding:0 8px;flex:0 0 auto}.brand span,.eyebrow,.sidebar-bottom{display:none}.nav{display:flex;gap:5px;overflow-x:auto}.nav button{width:auto;white-space:nowrap;height:38px;padding:0 10px}.nav button span:not(.ico){display:none}.main{padding:16px 14px 34px}.topbar{margin-bottom:16px}.hero-card{padding:22px}.hero h1{font-size:30px}.stat-grid{grid-template-columns:1fr 1fr}.settings-grid,.bot-roles-list{grid-template-columns:1fr}.broadcast-grid{grid-template-columns:1fr}.activity-item{grid-template-columns:1fr}.modal{width:calc(100vw - 18px);max-height:calc(100vh - 18px);margin:9px auto}.modal-body{padding:14px}}
@media(max-width:480px){.stat-grid{grid-template-columns:1fr}.top-actions .ghost{padding:0 9px}.section-head{align-items:flex-start;flex-direction:column}.filter-bar{display:grid;grid-template-columns:1fr}.filter-bar>*{width:100%!important;min-width:0!important}.table-wrap table{min-width:680px}}

/* V25.3 MODAL SCROLL CONTAINMENT: nested detail bodies must get a real scroll viewport */
.modal > [id$="ModalBody"]{flex:1 1 auto;min-height:0;display:flex;flex-direction:column;overflow:hidden}
.modal > [id$="ModalBody"] > .modal-body{flex:1 1 auto;min-height:0;height:auto;max-height:none;overflow-y:auto!important;overflow-x:hidden!important;overscroll-behavior:contain;scrollbar-gutter:stable}
#courseModalBody{min-height:0;overflow:hidden}
#courseModalBody > .modal-body{min-height:0;overflow-y:auto!important}
.course-layer .modal{min-height:0}
.course-layer #courseModalBody{flex:1 1 auto;min-height:0;display:flex;flex-direction:column;overflow:hidden}
.course-layer #courseModalBody > .modal-body{flex:1 1 auto;min-height:0;overflow-y:auto!important;overflow-x:hidden!important}
/* V25.2 COURSE DETAIL UI: prevent section overlap and guarantee reachable content */
.course-layer{align-items:center!important;padding:18px!important}
.course-layer .modal{
  width:min(980px,calc(100vw - 32px));
  height:min(900px,calc(100vh - 36px));
  max-height:calc(100vh - 36px);
  min-height:0;
  display:flex;
  flex-direction:column;
  overflow:hidden;
}
.course-layer .modal-head{flex:0 0 auto;position:sticky;top:0}
.course-layer .modal-body{
  display:block;
  flex:1 1 auto;
  min-height:0;
  height:auto;
  overflow-y:auto!important;
  overflow-x:hidden!important;
  padding:20px!important;
  overscroll-behavior:contain;
  scrollbar-gutter:stable;
}
.course-layer .modal-body > *{
  position:relative;
  clear:both;
  width:100%;
  box-sizing:border-box;
}
.course-layer .modal-body .form-grid,
.course-layer .modal-body .detail-grid,
.course-layer .modal-body .telegram-grid{
  width:100%;
  min-width:0;
}
.course-layer .modal-body .section-head{
  display:flex!important;
  position:relative;
  align-items:center!important;
  justify-content:space-between!important;
  gap:16px;
  width:100%;
  min-height:44px;
  margin:24px 0 10px!important;
  clear:both;
}
.course-layer .modal-body .section-head > div{min-width:0;flex:1 1 auto}
.course-layer .modal-body .section-head .action-btn{flex:0 0 auto}
.course-layer .modal-body .plan-list{
  display:grid!important;
  position:relative;
  width:100%;
  grid-template-columns:repeat(2,minmax(0,1fr));
  gap:12px;
  padding:0!important;
  margin:0 0 8px!important;
  clear:both;
}
.course-layer .modal-body .plan-card{
  position:relative;
  min-width:0;
  height:auto;
  overflow:visible;
  box-sizing:border-box;
}
.course-layer .modal-body .telegram-box{
  position:relative;
  display:block;
  width:100%;
  height:auto;
  min-height:0;
  overflow:visible;
  clear:both;
  box-sizing:border-box;
}
.course-layer .modal-body .modal-actions{
  position:relative;
  display:flex!important;
  width:100%;
  clear:both;
  padding:14px 0 4px;
  margin-top:18px!important;
  border-top:1px solid #202734;
  background:#0b0f15;
}
.content-layer{z-index:600!important}
.plan-layer{z-index:650!important}
.course-layer{z-index:300!important}
@media(max-width:760px){
  .course-layer{padding:9px!important}
  .course-layer .modal{width:calc(100vw - 18px);height:calc(100vh - 18px);max-height:calc(100vh - 18px)}
  .course-layer .modal-body{padding:14px!important}
  .course-layer .modal-body .section-head{align-items:flex-start!important;flex-direction:column!important}
  .course-layer .modal-body .section-head .action-btn{width:100%}
  .course-layer .modal-body .plan-list{grid-template-columns:1fr}
}
</style>

<style>

/* =========================================================
   V32.1 — VISIBLE PREMIUM UI OVERRIDE
   This block intentionally comes LAST so it overrides the
   dashboard's earlier CSS without changing API/JS logic.
   ========================================================= */
:root{
  --bg:#060811!important;
  --bg2:#0a0e18!important;
  --panel:#0d1320!important;
  --panel2:#111a2a!important;
  --line:rgba(148,163,184,.14)!important;
  --line2:rgba(148,163,184,.22)!important;
  --text:#f8fafc!important;
  --muted:#94a3b8!important;
  --accent:#8b5cf6!important;
  --accent2:#22d3ee!important;
  --violet:#a78bfa!important;
  --cyan:#22d3ee!important;
  --good:#34d399!important;
  --warn:#fbbf24!important;
  --danger:#fb7185!important;
}
html,body{background:#060811!important}
body{
  background:
    radial-gradient(900px 500px at 0% -10%,rgba(139,92,246,.22),transparent 58%),
    radial-gradient(850px 500px at 100% 0%,rgba(34,211,238,.13),transparent 55%),
    linear-gradient(135deg,#060811 0%,#090d17 48%,#070a12 100%)!important;
}
.app{background:transparent!important}
.sidebar{
  background:linear-gradient(180deg,rgba(8,12,22,.98),rgba(5,8,15,.98))!important;
  border-right:1px solid rgba(148,163,184,.12)!important;
  box-shadow:16px 0 60px rgba(0,0,0,.28)!important;
  position:relative;
}
.sidebar:before{
  content:"";position:absolute;left:0;top:0;bottom:0;width:2px;
  background:linear-gradient(180deg,#8b5cf6,#22d3ee,transparent 85%);
  opacity:.8;
}
.brand{padding:18px 14px 28px!important}
.brand-mark{
  width:40px!important;height:40px!important;border-radius:13px!important;
  background:linear-gradient(135deg,#8b5cf6,#22d3ee)!important;
  box-shadow:0 0 34px rgba(139,92,246,.38)!important;
}
.brand b{font-size:19px!important;color:#fff}
.brand span{color:#8190a6!important}
.eyebrow{color:#a78bfa!important}
.nav{gap:7px!important}
.nav button{
  min-height:46px!important;
  border:1px solid transparent!important;
  border-radius:13px!important;
  color:#aab6c7!important;
  padding:0 13px!important;
  transition:.16s ease!important;
}
.nav button:hover{
  color:#fff!important;
  background:rgba(139,92,246,.09)!important;
  border-color:rgba(139,92,246,.15)!important;
  transform:translateX(2px);
}
.nav button.active{
  color:#fff!important;
  background:linear-gradient(90deg,rgba(139,92,246,.23),rgba(34,211,238,.05))!important;
  border-color:rgba(139,92,246,.28)!important;
  box-shadow:inset 3px 0 #8b5cf6,0 10px 30px rgba(0,0,0,.18)!important;
}
.nav button .ico{color:#a78bfa!important}
.sidebar-bottom{border-top:1px solid rgba(148,163,184,.10)!important;padding-top:18px!important}
.health .dot{box-shadow:0 0 12px rgba(52,211,153,.8)!important}

.main{
  background:transparent!important;
  padding:28px 34px 70px!important;
}
.topbar{
  position:sticky!important;top:0!important;z-index:900!important;
  margin:0 -34px 28px!important;padding:18px 34px!important;
  background:rgba(6,8,17,.78)!important;
  border-bottom:1px solid rgba(148,163,184,.10)!important;
  backdrop-filter:blur(18px)!important;
}
.crumb{color:#64748b!important;font-size:12px!important}
.crumb strong{color:#f8fafc!important;font-size:14px!important}
.top-actions .ghost,.ghost{
  border:1px solid rgba(148,163,184,.15)!important;
  background:rgba(15,23,42,.62)!important;
  color:#cbd5e1!important;
  border-radius:11px!important;
}
.top-actions .ghost:hover,.ghost:hover{
  border-color:rgba(139,92,246,.35)!important;
  color:#fff!important;
  background:rgba(139,92,246,.10)!important;
}

.hero{
  gap:18px!important;
}
.hero-card{
  min-height:190px!important;
  padding:34px!important;
  border:1px solid rgba(139,92,246,.22)!important;
  border-radius:22px!important;
  background:
    radial-gradient(circle at 85% 15%,rgba(34,211,238,.12),transparent 32%),
    radial-gradient(circle at 10% 100%,rgba(139,92,246,.16),transparent 38%),
    linear-gradient(135deg,rgba(17,24,39,.95),rgba(10,15,26,.96))!important;
  box-shadow:0 25px 80px rgba(0,0,0,.25)!important;
  overflow:hidden;
}
.hero h1{
  max-width:720px!important;
  font-size:38px!important;
  line-height:1.08!important;
  letter-spacing:-.045em!important;
  color:#fff!important;
}
.hero p{max-width:700px!important;color:#9aa8ba!important;line-height:1.7!important}
.kicker{
  color:#a78bfa!important;
  letter-spacing:.16em!important;
  font-weight:800!important;
}

.terminal{
  border:1px solid rgba(34,211,238,.16)!important;
  border-radius:18px!important;
  background:rgba(4,8,15,.82)!important;
  box-shadow:0 20px 55px rgba(0,0,0,.24)!important;
}

.stat-grid{gap:14px!important;margin-top:18px!important}
.stat{
  min-height:118px!important;
  padding:20px!important;
  border:1px solid rgba(148,163,184,.13)!important;
  border-radius:17px!important;
  background:linear-gradient(145deg,rgba(17,25,39,.94),rgba(10,15,24,.94))!important;
  box-shadow:0 15px 45px rgba(0,0,0,.16)!important;
  position:relative;overflow:hidden;
}
.stat:after{
  content:"";position:absolute;right:-35px;top:-35px;width:100px;height:100px;
  border-radius:50%;background:radial-gradient(circle,rgba(139,92,246,.18),transparent 68%);
}
.stat-label{color:#8fa0b5!important}
.stat-value{color:#fff!important;font-size:27px!important}
.stat-meta{color:#64748b!important}

.panel{
  margin-top:18px!important;
  border:1px solid rgba(148,163,184,.13)!important;
  border-radius:18px!important;
  background:linear-gradient(145deg,rgba(14,20,32,.96),rgba(9,14,23,.96))!important;
  box-shadow:0 18px 60px rgba(0,0,0,.16)!important;
  overflow:hidden!important;
}
.panel-title{
  padding:20px 22px!important;
  border-bottom:1px solid rgba(148,163,184,.10)!important;
  background:linear-gradient(180deg,rgba(255,255,255,.025),transparent)!important;
}
.panel-title b{font-size:16px!important;color:#fff!important}
.panel-title span{color:#7f8da1!important}

.table-wrap{
  border:0!important;
  border-radius:0!important;
  background:transparent!important;
  overflow-x:auto!important;
}
table{min-width:760px!important}
thead th{
  background:rgba(255,255,255,.025)!important;
  color:#8fa0b5!important;
  font-size:10px!important;
  letter-spacing:.10em!important;
  text-transform:uppercase!important;
}
tbody tr{background:transparent!important;transition:.15s ease!important}
tbody tr:hover{background:rgba(139,92,246,.055)!important}
tbody td{border-bottom:1px solid rgba(148,163,184,.08)!important}

.action-btn{
  border:1px solid rgba(148,163,184,.15)!important;
  border-radius:10px!important;
  background:rgba(15,23,42,.72)!important;
  color:#dbe4ef!important;
  transition:.16s ease!important;
}
.action-btn:hover{transform:translateY(-1px)!important;border-color:rgba(139,92,246,.35)!important}
.action-btn.primary,.primary{
  background:linear-gradient(135deg,#8b5cf6,#6366f1)!important;
  border-color:rgba(167,139,250,.55)!important;
  color:#fff!important;
  box-shadow:0 10px 28px rgba(99,102,241,.22)!important;
}
.action-btn.good{
  background:rgba(52,211,153,.10)!important;
  border-color:rgba(52,211,153,.22)!important;
  color:#6ee7b7!important;
}
.action-btn.danger{
  background:rgba(251,113,133,.08)!important;
  border-color:rgba(251,113,133,.20)!important;
  color:#fda4af!important;
}

input.search-input,select.select-dark,textarea.textarea-dark,input{
  background:#080d16!important;
  border:1px solid rgba(148,163,184,.16)!important;
  color:#f8fafc!important;
  border-radius:11px!important;
}
input.search-input:focus,select.select-dark:focus,textarea.textarea-dark:focus,input:focus{
  border-color:rgba(139,92,246,.60)!important;
  box-shadow:0 0 0 3px rgba(139,92,246,.11)!important;
}

.detail-card,.telegram-connect-panel,.settings-card,.plan-card,.broadcast-preview{
  background:linear-gradient(145deg,rgba(17,25,39,.90),rgba(10,15,24,.90))!important;
  border-color:rgba(148,163,184,.13)!important;
  border-radius:15px!important;
}
.telegram-connect-panel{
  border-color:rgba(139,92,246,.22)!important;
  box-shadow:0 18px 50px rgba(0,0,0,.18)!important;
}

.status{
  border-radius:999px!important;
  padding:5px 10px!important;
  font-weight:750!important;
}
.active-status{background:rgba(52,211,153,.10)!important;color:#6ee7b7!important;border-color:rgba(52,211,153,.20)!important}
.pending-status{background:rgba(251,191,36,.10)!important;color:#fcd34d!important;border-color:rgba(251,191,36,.20)!important}
.danger-status{background:rgba(251,113,133,.10)!important;color:#fda4af!important;border-color:rgba(251,113,133,.20)!important}

.modal-backdrop{
  background:rgba(2,5,12,.78)!important;
  backdrop-filter:blur(8px)!important;
}
.modal{
  border:1px solid rgba(148,163,184,.18)!important;
  border-radius:22px!important;
  background:linear-gradient(145deg,#111827,#0a101b)!important;
  box-shadow:0 40px 120px rgba(0,0,0,.58)!important;
}
.modal-header{
  background:linear-gradient(180deg,rgba(255,255,255,.035),transparent)!important;
  border-bottom-color:rgba(148,163,184,.11)!important;
}
.modal-body{
  scrollbar-color:rgba(139,92,246,.50) transparent!important;
}
.modal-body::-webkit-scrollbar{width:9px}
.modal-body::-webkit-scrollbar-thumb{
  background:linear-gradient(180deg,#8b5cf6,#22d3ee);
  border-radius:99px;
}

.empty{
  border:1px dashed rgba(148,163,184,.17)!important;
  border-radius:14px!important;
  background:rgba(255,255,255,.012)!important;
}
.notice{border-radius:13px!important}

@media(max-width:1100px){
  .main{padding:22px 22px 50px!important}
  .topbar{margin-left:-22px!important;margin-right:-22px!important;padding-left:22px!important;padding-right:22px!important}
}
@media(max-width:760px){
  .main{padding:14px 12px 40px!important}
  .topbar{margin:-14px -12px 18px!important;padding:14px 12px!important}
  .hero h1{font-size:30px!important}
  .hero-card{padding:24px!important}
}

</style>
</head>
<body>
<div class="app">
<aside class="sidebar">
  <div class="brand"><div class="brand-mark"></div><div><b>CourseFlow</b><span>Telegram Control Center</span></div></div>
  <div class="eyebrow">Workspace</div>
  <div class="nav">
    <button class="active" onclick="showPanel('overview',this)"><span class="ico">⌂</span><span>Overview</span></button>
    <button onclick="showPanel('students',this)"><span class="ico">◉</span><span>Students</span></button>
     <button onclick="showPanel('subscriptions',this)"><span class="ico">◌</span><span>Subscriptions</span></button>
    <button onclick="showPanel('courses',this)"><span class="ico">▦</span><span>Courses</span></button>
    <button onclick="showPanel('payments',this)"><span class="ico">₹</span><span>Payments</span></button>
    <button onclick="showPanel('plans',this)"><span class="ico">◇</span><span>Plan Overview</span></button>
    <button onclick="showPanel('broadcast',this);loadBroadcast()"><span class="ico">↗</span><span>Broadcast</span></button>
    <button onclick="showPanel('analytics',this);loadAnalytics()"><span class="ico">◈</span><span>Analytics</span></button>
    <button onclick="showPanel('activity',this);loadActivity()"><span class="ico">◷</span><span>Activity Log</span></button>
    <button onclick="showPanel('settings',this);loadSettings()"><span class="ico">⚙</span><span>Settings</span></button>
  </div>
  <div class="sidebar-bottom"><div class="health"><span class="dot"></span><span>Dashboard online</span></div><form method="post" action="/dashboard/logout" style="margin-top:12px"><button type="submit" class="ghost" style="width:100%;display:flex;align-items:center;justify-content:center;gap:8px">🚪 <span>Logout</span></button></form></div>
</aside>
<main class="main">
  <div class="topbar"><div class="crumb">Workspace <span>/</span> <strong id="pageTitle">Overview</strong></div><div class="top-actions"><div class="notification-wrap"><button class="ghost bell-btn" onclick="toggleNotifications()" aria-label="Notifications">🔔 <span id="notificationBadge" class="notification-badge" style="display:none">0</span></button><div id="notificationPanel" class="notification-panel"><div class="notification-head"><b>Notifications</b><button class="ghost" onclick="loadNotifications()">↻</button></div><div id="notificationList"><div class="empty">Loading…</div></div></div></div><button class="ghost" onclick="refreshAll()">↻ Refresh</button></div></div>

  <section id="overviewView" class="view-shell overview-view">
    <section class="hero">
      <div class="hero-card hero">
        <div><div class="kicker">Private control center</div><h1>Your Telegram course business, without opening Telegram.</h1><p>Monitor students, courses, subscriptions and payments from one focused control surface. Your existing bot flow stays underneath.</p></div>
      </div>
      <div class="terminal"><div class="terminal-head"><span class="term-dot"></span><span class="term-dot"></span><span class="term-dot"></span><span style="margin-left:5px">courseflow — live status</span></div><div class="terminal-body"><div><span class="pink">$</span> system status</div><div>dashboard <span class="green">✓ online</span></div><div>supabase <span class="green">✓ connected</span></div><div>telegram engine <span class="green">✓ ready</span></div><div>access mode <span class="purple">private</span></div><div><span class="pink">$</span> awaiting operator…</div></div></div>
    </section>
    <section class="stat-grid" id="stats"></section>
    <section id="overview" class="panel active">
      <div class="panel-title"><div><b>Command overview</b><div class="muted">Live snapshot from your existing database</div></div><span id="lastUpdated">—</span></div>
      <div style="padding:18px;display:grid;grid-template-columns:repeat(3,1fr);gap:12px" class="overview-cards">
        <div class="stat"><div class="stat-label">Average payment</div><div class="stat-value" id="avgPayment">—</div><div class="stat-meta">Across approved payments</div></div>
        <div class="stat"><div class="stat-label">Approved payments</div><div class="stat-value" id="approvedPayments">—</div><div class="stat-meta">Successful transactions</div></div>
        <div class="stat accent"><div class="stat-label">Needs attention</div><div class="stat-value" id="attention">—</div><div class="stat-meta">Pending + expiring soon</div></div>
      </div>
    </section>
  </section>

  <section id="moduleView" class="view-shell module-view">
    <div id="students" class="panel"><div class="panel-title"><div><b>Students</b><span>Manage customer access without opening Telegram</span></div></div><div class="filter-bar"><input id="studentSearch" class="search-input" placeholder="Search name, username or Telegram ID…" oninput="renderStudents()"><select id="studentStatusFilter" class="select-dark" onchange="renderStudents()"><option value="">All statuses</option><option value="active">Active</option><option value="pending">Pending</option><option value="expired">Expired</option><option value="none">No subscription</option></select><select id="studentCourseFilter" class="select-dark" onchange="renderStudents()"><option value="">All courses</option></select></div><div id="studentsTable" class="table-wrap"></div></div>
     <div id="subscriptions" class="panel"><div class="panel-title"><div><b>Subscriptions</b><span>Manage active, expiring, expired and lifetime course access.</span></div><button class="action-btn" onclick="loadSubscriptions()">↻ Refresh</button></div><div class="filter-bar"><input id="subscriptionSearch" class="search-input" placeholder="Search student, username, course or plan…" oninput="renderSubscriptions()"><select id="subscriptionStatusFilter" class="select-dark" onchange="renderSubscriptions()"><option value="">All statuses</option><option value="active">Active</option><option value="expiring">Expiring soon</option><option value="lifetime">Lifetime</option><option value="expired">Expired</option><option value="revoked">Revoked</option><option value="pending">Pending</option></select><select id="subscriptionCourseFilter" class="select-dark" onchange="renderSubscriptions()"><option value="">All courses</option></select></div><div id="subscriptionsTable" class="table-wrap"></div></div>
    <div id="courses" class="panel"><div class="panel-title"><div><b>Courses</b><span>Catalog, status & course controls</span></div><button class="action-btn primary" onclick="openCourseCreate()">+ New Course</button></div><div class="filter-bar"><input id="courseSearch" class="search-input" placeholder="Search course name, description or slug…" oninput="renderCourses()"><select id="courseStatusFilter" class="select-dark" onchange="renderCourses()"><option value="">All statuses</option><option value="active">Active</option><option value="inactive">Inactive</option></select></div><div id="coursesTable" class="table-wrap"></div></div>
    <div id="payments" class="panel"><div class="panel-title"><b>Payments</b><span>Payment requests</span></div><div class="filter-bar"><input id="paymentSearch" class="search-input" placeholder="Search payment, student, username or Telegram ID…" oninput="renderPayments()"><select id="paymentStatusFilter" class="select-dark" onchange="renderPayments()"><option value="">All statuses</option><option value="pending">Pending</option><option value="approved">Approved</option><option value="rejected">Rejected</option></select><select id="paymentCourseFilter" class="select-dark" onchange="renderPayments()"><option value="">All courses</option></select><input id="paymentFrom" class="search-input" type="date" onchange="renderPayments()"><input id="paymentTo" class="search-input" type="date" onchange="renderPayments()"></div><div id="paymentsTable" class="table-wrap"></div></div>
    <div id="plans" class="panel"><div class="panel-title"><div><b>Plan Overview</b><span>Read-only overview of all course plans. Create and edit plans from Courses → Manage → Plans.</span></div></div><div class="filter-bar"><input id="planSearch" class="search-input" placeholder="Search plan or course…" oninput="renderPlans()"><select id="planCourseFilter" class="select-dark" onchange="renderPlans()"><option value="">All courses</option></select><select id="planStatusFilter" class="select-dark" onchange="renderPlans()"><option value="">All statuses</option><option value="active">Active</option><option value="inactive">Inactive</option></select></div><div id="plansTable" class="table-wrap"></div></div>
    <div id="broadcast" class="panel"><div class="panel-title"><div><b>Broadcast</b><span>Preview recipients before sending a Telegram message.</span></div></div><div class="broadcast-grid"><div class="broadcast-form"><label>Audience</label><select id="broadcastAudience" class="select-dark" onchange="broadcastAudienceChanged()"><option value="all">All students</option><option value="active">Active students</option><option value="expiring">Expiring in next 7 days</option><option value="course">Students of a specific course</option></select><select id="broadcastCourse" class="select-dark" style="display:none;margin-top:10px"></select><label style="margin-top:14px">Message</label><textarea id="broadcastMessage" class="textarea-dark" rows="9" maxlength="4096" placeholder="Write your announcement…"></textarea><div class="muted" style="margin-top:6px">Maximum 4096 characters. The dashboard will not send until you review the recipient count.</div><div class="modal-actions"><button class="action-btn primary" onclick="previewBroadcast()">Preview Recipients</button></div></div><div class="broadcast-preview" id="broadcastPreview"><div class="empty">Choose an audience and preview the recipients before sending.</div></div></div></div>
    <div id="analytics" class="panel"><div class="panel-title"><div><b>Analytics</b><span>Revenue and operational metrics from the existing database.</span></div><button class="action-btn" onclick="loadAnalytics()">↻ Refresh</button></div><div class="analytics-cards" id="analyticsCards"></div><div class="section-head" style="margin-top:18px"><div><h2 style="font-size:18px">Revenue</h2><p>Approved payment revenue by period.</p></div></div><div class="analytics-cards" id="revenueCards"></div></div>
    <div id="activity" class="panel"><div class="panel-title"><div><b>Activity Log</b><span>Recent dashboard actions. Existing operations continue even if the optional log table is unavailable.</span></div><button class="action-btn" onclick="loadActivity()">↻ Refresh</button></div><div class="filter-bar"><input id="activitySearch" class="search-input" placeholder="Search action or description…" oninput="renderActivity()"><select id="activityActionFilter" class="select-dark" onchange="renderActivity()"><option value="">All actions</option></select><input id="activityFrom" class="search-input" type="date" onchange="renderActivity()"><input id="activityTo" class="search-input" type="date" onchange="renderActivity()"></div><div id="activityList" class="activity-list"><div class="empty">Loading activity…</div></div></div>
    <div id="settings" class="panel"><div class="panel-title"><div><b>Settings & Security</b><span>Private dashboard configuration and read-only system health.</span></div><button class="action-btn" onclick="loadSettings()">↻ Refresh</button></div><div class="settings-grid"><div class="settings-card"><div class="settings-card-head"><div><b>System Health</b><div class="muted">Live connectivity checks without exposing secrets.</div></div><span id="settingsOverall" class="status">Checking…</span></div><div id="healthList" class="settings-list"><div class="empty">Loading…</div></div></div><div class="settings-card"><div class="settings-card-head"><div><b>Dashboard Security</b><div class="muted">Authentication is controlled by environment variables.</div></div></div><div id="securityInfo" class="settings-list"></div></div><div class="settings-card"><div class="settings-card-head"><div><b>Data Export</b><div class="muted">Download operational data for backup or review. Credentials are excluded.</div></div></div><div class="settings-actions"><button class="action-btn primary" onclick="exportDashboardData()">⬇ Export JSON Backup</button></div></div><div class="settings-card bot-roles-card"><div class="settings-card-head"><div><b>Telegram Bot Roles</b><div class="muted">Live bot identity and role information. Tokens are never displayed.</div></div></div><div id="botRoles" class="bot-roles-list"><div class="empty">Loading bot information…</div></div></div></div></div>


  </section>
  <div id="lessonModal" class="modal-backdrop content-layer" onclick="if(event.target===this)closeLessonModal()"><div class="modal"><div class="modal-head"><div><div class="eyebrow">Course content</div><h3 id="lessonModalTitle">Add Lesson</h3></div><button class="ghost" onclick="closeLessonModal()">Close</button></div><div id="lessonModalBody"></div></div></div><div id="planModal" class="modal-backdrop plan-layer" onclick="if(event.target===this)closePlanModal()"><div class="modal"><div class="modal-head"><div><div class="eyebrow">Course plan control</div><h3 id="planModalTitle">Add Plan</h3></div><button class="ghost" onclick="closePlanModal()">Close</button></div><div id="planModalBody"></div></div></div><div id="studentModal" class="modal-backdrop" onclick="if(event.target===this)closeStudent()"><div class="modal"><div class="modal-head"><div><div class="eyebrow">Customer control</div><h3 id="studentModalTitle">Student</h3></div><button class="ghost" onclick="closeStudent()">Close</button></div><div id="studentModalBody"></div></div></div><div id="paymentModal" class="modal-backdrop" onclick="if(event.target===this)closePayment()"><div class="modal"><div class="modal-head"><div><div class="eyebrow">Payment review</div><h3 id="paymentModalTitle">Payment</h3></div><button class="ghost" onclick="closePayment()">Close</button></div><div id="paymentModalBody"></div></div></div><div id="courseModal" class="modal-backdrop course-layer" onclick="if(event.target===this)closeCourse()"><div class="modal"><div class="modal-head"><div><div class="eyebrow">Course control</div><h3 id="courseModalTitle">New Course</h3></div><button class="ghost" onclick="closeCourse()">Close</button></div><div id="courseModalBody"></div></div></div>
<div class="footer-note">CourseFlow private dashboard · Existing Telegram automation remains the source of truth</div>
</main></div>
<script>
const esc=s=>String(s??'').replace(/[&<>'"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]));


/* ===== V32.5 BUTTON/WORKFLOW RESTORATION ===== */
async function generateStudentInvite(id){if(!confirm('Generate a new one-use Telegram invite for this subscription?'))return;try{const r=await fetch('/dashboard/api/students/'+encodeURIComponent(currentStudent.user.id)+'/invite',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({subscription_id:id})});const d=await r.json();if(!r.ok)throw new Error(d.detail||'Invite generation failed');await navigator.clipboard?.writeText(d.invite_link).catch(()=>{});alert('Invite generated:\n\n'+d.invite_link+'\n\nThe link was copied when browser permission allowed it.');await openStudent(currentStudent.user.id)}catch(e){alert(e.message)}}

async function extendStudent(id,days=7){if(!confirm('Extend this subscription by '+days+' days?'))return;try{const r=await fetch('/dashboard/api/students/'+encodeURIComponent(currentStudent.user.id)+'/extend',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({subscription_id:id,days})});const d=await r.json();if(!r.ok)throw new Error(d.detail||'Extension failed');await openStudent(currentStudent.user.id);await refreshAll()}catch(e){alert(e.message)}}

async function revokeStudent(id){if(!confirm('Revoke this access? Telegram invite links will be revoked and the member will be removed when permissions allow.'))return;try{const r=await fetch('/dashboard/api/students/'+encodeURIComponent(currentStudent.user.id)+'/revoke',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({subscription_id:id})});const d=await r.json();if(!r.ok)throw new Error(d.detail||'Revoke failed');await openStudent(currentStudent.user.id);await refreshAll();if(d.warnings?.length)alert('Access revoked, but Telegram warnings occurred:\n\n'+d.warnings.join('\n'))}catch(e){alert(e.message)}}

async function grantStudent(){const plan=document.getElementById('grantPlan')?.value;if(!plan)return alert('Select a plan first.');if(!confirm('Grant this plan to the customer?'))return;try{const r=await fetch('/dashboard/api/students/'+encodeURIComponent(currentStudent.user.id)+'/grant',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({plan_id:plan})});const d=await r.json();if(!r.ok)throw new Error(d.detail||'Grant failed');await openStudent(currentStudent.user.id);await refreshAll();if(d.warning)alert(d.warning);else alert('Access granted successfully.')}catch(e){alert(e.message)}}
let currentCourse=null;

async function testCourseTelegram(courseId){const box=document.getElementById('courseTelegramBox');if(box)box.innerHTML='<div class="notice">Testing Telegram connection and refreshing permissions…</div>';try{const r=await fetch('/dashboard/api/courses/'+encodeURIComponent(courseId)+'/telegram/test',{method:'POST'});const d=await r.json();if(!r.ok)throw new Error(d.detail||'Telegram test failed');await loadCourseTelegram(courseId);await refreshAll();alert('Telegram connection verified. Bot: '+d.bot_status+' · Invite: '+(d.can_invite_users?'YES':'NO')+' · Manage members: '+(d.can_manage_members?'YES':'NO'))}catch(e){if(box)box.innerHTML='<div class="notice danger">'+esc(e.message)+'</div>';alert(e.message)}}

function openPlanForm(courseId,plan={}){const editing=!!plan.id;const body=`<div class="modal-body"><div class="form-grid"><div class="form-field"><label>Plan name</label><input id="planName" class="search-input" value="${esc(plan.name||'')}" placeholder="e.g. 30 Days"></div><div class="form-field"><label>Price (INR)</label><input id="planPrice" class="search-input" type="number" min="1" step="0.01" value="${esc(plan.price??'')}" placeholder="499"></div></div><div class="form-grid" style="margin-top:14px"><div class="form-field"><label>Plan type</label><select id="planType" class="select-dark" onchange="syncPlanDuration()"><option value="fixed" ${plan.plan_type!=='lifetime'?'selected':''}>Fixed Duration</option><option value="lifetime" ${plan.plan_type==='lifetime'?'selected':''}>Lifetime</option></select></div><div class="form-field"><label>Duration (days)</label><input id="planDuration" class="search-input" type="number" min="1" value="${esc(plan.duration_days??'')}" placeholder="30" ${plan.plan_type==='lifetime'?'disabled':''}></div></div><div class="form-field" style="margin-top:14px"><label>Description</label><textarea id="planDescription" class="textarea-dark" rows="3" placeholder="Plan description">${esc(plan.description||'')}</textarea></div><div class="form-field" style="margin-top:14px"><label>Payment QR</label><div class="upload-row"><input id="planQr" class="file-input" type="file" accept="image/png,image/jpeg,image/webp"><span class="muted">Required for new plans. Existing QR is kept when editing.</span></div><div id="planQrStatus" class="muted" style="margin-top:6px">${plan.qr_code_path?'QR already stored':'No QR uploaded'}</div></div><div class="modal-actions"><button class="action-btn" onclick="closePlanModal()">Cancel</button><button class="action-btn primary" onclick="saveCoursePlan('${esc(courseId)}',${editing?`'${esc(plan.id)}'`:'null'})">${editing?'Save Plan':'Create Plan'}</button></div></div>`;document.getElementById('planModalTitle').textContent=editing?'Edit Plan':'Add Plan';document.getElementById('planModalBody').innerHTML=body;document.getElementById('planModal').classList.add('open');document.body.classList.add('modal-open');window.currentPlan=plan;}

function syncPlanDuration(){const lt=document.getElementById('planType')?.value==='lifetime',el=document.getElementById('planDuration');if(el){el.disabled=lt;if(lt)el.value='';}}

function closePlanModal(){document.getElementById('planModal').classList.remove('open');document.body.classList.remove('modal-open');window.currentPlan=null}

async function saveCoursePlan(courseId,planId){const name=(document.getElementById('planName')?.value||'').trim(),price=document.getElementById('planPrice')?.value,planType=document.getElementById('planType')?.value||'fixed',duration=document.getElementById('planDuration')?.value,description=(document.getElementById('planDescription')?.value||'').trim(),file=document.getElementById('planQr')?.files?.[0];if(!name)return alert('Plan name is required.');if(!price||Number(price)<=0)return alert('Enter a valid price.');if(planType==='fixed'&&(!duration||Number(duration)<=0))return alert('Enter duration in days.');try{let qr=window.currentPlan?.qr_code_path||'';if(file){const fd=new FormData();fd.append('file',file);const ur=await fetch('/dashboard/api/plans/qr-upload',{method:'POST',body:fd});const ud=await ur.json();if(!ur.ok)throw new Error(ud.detail||'QR upload failed');qr=ud.qr_code_path}if(!planId&&!qr)throw new Error('Payment QR is required for a new plan.');const url=planId?'/dashboard/api/plans/'+encodeURIComponent(planId):'/dashboard/api/courses/'+encodeURIComponent(courseId)+'/plans';const r=await fetch(url,{method:planId?'PATCH':'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name,price:Number(price),plan_type:planType,duration_days:planType==='lifetime'?null:Number(duration),description,qr_code_path:qr,currency:'INR'})});const d=await r.json();if(!r.ok)throw new Error(d.detail||'Plan save failed');closePlanModal();await refreshAll();await openCourse(courseId);alert(planId?'Plan updated successfully.':'Plan created successfully.')}catch(e){alert(e.message)}}

async function editCoursePlan(planId){const p=(currentCourse?.plans||[]).find(x=>String(x.id)===String(planId));if(!p)return alert('Plan not found.');openPlanForm(currentCourse.id,p)}

async function toggleCoursePlan(planId){if(!confirm('Change this plan status?'))return;try{const r=await fetch('/dashboard/api/plans/'+encodeURIComponent(planId)+'/toggle',{method:'POST'}),d=await r.json();if(!r.ok)throw new Error(d.detail||'Status update failed');await refreshAll();await openCourse(currentCourse.id)}catch(e){alert(e.message)}}

async function deleteCoursePlan(planId){if(!confirm('Delete this plan permanently? If it has subscriptions or payments, deletion will be blocked.'))return;try{const r=await fetch('/dashboard/api/plans/'+encodeURIComponent(planId),{method:'DELETE'}),d=await r.json();if(!r.ok)throw new Error(d.detail||'Delete failed');await refreshAll();await openCourse(currentCourse.id);alert('Plan deleted.')}catch(e){alert(e.message)}}

async function deleteCourse(id){if(!confirm('Delete this course permanently? If it has plans, payments, subscriptions or a Telegram group, deletion will be blocked.'))return;try{const r=await fetch('/dashboard/api/courses/'+encodeURIComponent(id),{method:'DELETE'});const d=await r.json();if(!r.ok)throw new Error(d.detail||'Delete failed');closeCourse();await refreshAll();alert('Course deleted.')}catch(e){alert(e.message)}}

async function openPayment(id){try{const p=await api('/dashboard/api/payments/'+encodeURIComponent(id));document.getElementById('paymentModalTitle').textContent='Payment #'+(p.payment_number||p.id);document.getElementById('paymentModalBody').innerHTML=paymentDetailHtml(p);document.getElementById('paymentModal').classList.add('open')}catch(e){alert('Could not load payment: '+e.message)}}

function closePayment(){document.getElementById('paymentModal').classList.remove('open')}

function paymentDetailHtml(p){
 const u=p.user||{},c=p.course||{},pl=p.plan||{},s=p.subscription;
 const name=[u.first_name,u.last_name].filter(Boolean).join(' ')||'Unknown';
 let html=`<div class="modal-body"><div class="detail-grid">
 <div class="detail-card"><div class="detail-label">Customer</div><div class="detail-value">${esc(name)}</div><div class="muted">${esc(u.username?'@'+u.username:(u.telegram_user_id||''))}</div></div>
 <div class="detail-card"><div class="detail-label">Amount</div><div class="detail-value">${esc((p.currency||'')+' '+(p.amount??''))}</div></div>
 <div class="detail-card"><div class="detail-label">Status</div><div class="detail-value"><span class="status ${statusClass(p.status)}">${esc(p.status)}</span></div></div></div>
 <div class="section-head" style="margin-top:24px"><div><h2 style="font-size:18px">Purchase</h2><p>${esc(c.name||'Unknown course')} · ${esc(pl.name||'Unknown plan')}</p></div></div>
 <div class="detail-grid"><div><div class="detail-label">Plan type</div><div class="detail-value">${esc(pl.plan_type||'—')}</div></div>
 <div><div class="detail-label">Duration</div><div class="detail-value">${esc(pl.plan_type==='lifetime'?'Lifetime':((pl.duration_days||'—')+' days'))}</div></div>
 <div><div class="detail-label">Submitted</div><div class="detail-value">${esc(p.submitted_at||'—')}</div></div></div>`;
 if(p.screenshot_url) html+=`<div style="margin-top:20px"><div class="detail-label">Payment screenshot</div><a href="${esc(p.screenshot_url)}" target="_blank" rel="noopener"><img src="${esc(p.screenshot_url)}" alt="Payment screenshot" style="max-width:100%;max-height:420px;margin-top:10px;border:1px solid var(--line2);border-radius:12px"></a></div>`;
 if(p.rejection_reason) html+=`<div class="notice danger" style="margin-top:16px">Rejection reason: ${esc(p.rejection_reason)}</div>`;
 if(s) html+=`<div class="notice" style="margin-top:16px">Access already provisioned · ${esc(s.status)} · ${esc(s.is_lifetime?'Lifetime':(s.expires_at||'No expiry'))}</div>`;
 if(p.status==='pending') html+=`<div class="modal-actions"><button class="action-btn good" onclick="approvePayment('${esc(p.id)}')">✓ Approve & Grant Access</button><button class="action-btn danger" onclick="rejectPayment('${esc(p.id)}')">✕ Reject</button></div>`;
 else if(p.status==='approved'&&!s) html+=`<div class="notice warn" style="margin-top:16px">Payment is approved but course access is not provisioned. Use the existing Telegram Admin Bot to grant access after fixing the underlying Telegram issue.</div>`;
 html+='</div>';return html;
}
/* ===== END V32.5 RESTORATION ===== */
async function api(path){const r=await fetch(path,{credentials:'same-origin'});if(!r.ok)throw new Error(await r.text());return r.json()}
function showPanel(id,btn){document.querySelectorAll('.panel').forEach(x=>x.classList.remove('active'));const target=document.getElementById(id);if(target)target.classList.add('active');const isOverview=id==='overview';document.getElementById('overviewView')?.classList.toggle('hidden',!isOverview);document.getElementById('moduleView')?.classList.toggle('hidden',isOverview);document.querySelectorAll('.nav button').forEach(x=>x.classList.remove('active'));if(btn)btn.classList.add('active');const titles={overview:'Overview',students:'Students',subscriptions:'Subscriptions',courses:'Courses',payments:'Payments',plans:'Plan Overview',broadcast:'Broadcast',analytics:'Analytics',activity:'Activity Log',settings:'Settings & Security'};document.getElementById('pageTitle').textContent=titles[id]||'Dashboard';window.scrollTo({top:0,behavior:'smooth'});if(id==='overview')loadNotifications();}
function table(headers,rows){if(!rows.length)return '<div class="empty">No records found.</div>';return '<table><thead><tr>'+headers.map(h=>'<th>'+h+'</th>').join('')+'</tr></thead><tbody>'+rows.join('')+'</tbody></table>'}
function statusClass(s){return s==='active'||s==='approved'?'active-status':s==='pending'?'pending-status':s==='expired'||s==='rejected'?'danger-status':''}
let studentsCache=[];let currentStudent=null;let subscriptionsCache=[];let coursesCache=[];let plansCache=[];let paymentsCache=[];let activityCache=[];
async function loadSubscriptions(){try{subscriptionsCache=await api('/dashboard/api/subscriptions');populateSubscriptionCourseFilter();renderSubscriptions();}catch(e){const el=document.getElementById('subscriptionsTable');if(el)el.innerHTML='<div class="notice danger">Subscriptions unavailable: '+esc(e.message)+'</div>';}}
function populateSubscriptionCourseFilter(){const sel=document.getElementById('subscriptionCourseFilter');if(!sel)return;const cur=sel.value;sel.innerHTML='<option value="">All courses</option>'+coursesCache.map(c=>'<option value="'+esc(c.id)+'">'+esc(c.name)+'</option>').join('');if([...sel.options].some(o=>o.value===cur))sel.value=cur;}
function renderSubscriptions(){const q=(document.getElementById('subscriptionSearch')?.value||'').trim().toLowerCase(),st=document.getElementById('subscriptionStatusFilter')?.value||'',course=document.getElementById('subscriptionCourseFilter')?.value||'';const list=subscriptionsCache.filter(x=>{const hay=[x.customer_name,x.username,x.telegram_user_id,x.course_name,x.plan_name].filter(Boolean).join(' ').toLowerCase();return(!q||hay.includes(q))&&(!st||x.management_status===st)&&(!course||String(x.course_id)===String(course));});document.getElementById('subscriptionsTable').innerHTML=table(['Student','Course / Plan','Status','Started','Expiry','Remaining','Actions'],list.map(x=>{const expiry=x.is_lifetime?'Lifetime':(x.expires_at||'—'),remain=x.is_lifetime?'Protected':(x.days_remaining==null?'—':x.days_remaining+' days');let actions=x.status==='active'?'<div class="row-actions"><button class="action-btn good" onclick="extendSubscriptionFromList(\''+esc(x.id)+'\',\''+esc(x.user_id)+'\')">+30 Days</button><button class="action-btn" onclick="changeSubscriptionPlan(\''+esc(x.id)+'\',\''+esc(x.course_id)+'\')">Change Plan</button><button class="action-btn danger" onclick="revokeSubscriptionFromList(\''+esc(x.id)+'\',\''+esc(x.user_id)+'\')">Revoke</button></div>':'<button class="action-btn" onclick="openStudent(\''+esc(x.user_id)+'\')">View Student</button>';return '<tr><td><b>'+esc(x.customer_name)+'</b><div class="muted">'+esc(x.username?'@'+x.username:(x.telegram_user_id||''))+'</div></td><td><b>'+esc(x.course_name)+'</b><div class="muted">'+esc(x.plan_name)+'</div></td><td><span class="status '+statusClass(x.management_status)+'">'+esc(x.management_status)+'</span></td><td>'+esc(x.started_at||'—')+'</td><td>'+esc(expiry)+'</td><td>'+esc(remain)+'</td><td>'+actions+'</td></tr>'; }));}
async function extendSubscriptionFromList(id,userId){if(!confirm('Extend this subscription by 30 days?'))return;try{const r=await fetch('/dashboard/api/students/'+encodeURIComponent(userId)+'/extend',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({subscription_id:id,days:30})}),d=await r.json();if(!r.ok)throw new Error(d.detail||'Extension failed');await loadSubscriptions();await refreshAll();alert('Subscription extended by 30 days.');}catch(e){alert(e.message)}}
async function revokeSubscriptionFromList(id,userId){if(!confirm('Revoke this subscription and its Telegram access?'))return;try{const r=await fetch('/dashboard/api/students/'+encodeURIComponent(userId)+'/revoke',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({subscription_id:id})}),d=await r.json();if(!r.ok)throw new Error(d.detail||'Revoke failed');await loadSubscriptions();await refreshAll();alert(d.warnings?.length?'Access revoked. Telegram warnings:\n\n'+d.warnings.join('\n'):'Subscription access revoked.');}catch(e){alert(e.message)}}
async function changeSubscriptionPlan(id,courseId){const choices=plansCache.filter(p=>String(p.course_id)===String(courseId)&&p.is_active);if(!choices.length){alert('No active plans are available for this course.');return;}const labels=choices.map((p,i)=>(i+1)+'. '+p.name+' — '+(p.currency||'')+' '+p.price+(p.plan_type==='lifetime'?' · Lifetime':'')).join('\n');const answer=prompt('Choose the new plan by number:\n\n'+labels);if(answer===null)return;const idx=Number(answer)-1;if(!Number.isInteger(idx)||!choices[idx]){alert('Invalid plan selection.');return;}if(!confirm('Change this subscription to '+choices[idx].name+'?'))return;try{const r=await fetch('/dashboard/api/subscriptions/'+encodeURIComponent(id)+'/change-plan',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({plan_id:choices[idx].id})}),d=await r.json();if(!r.ok)throw new Error(d.detail||'Plan change failed');await loadSubscriptions();await refreshAll();alert('Subscription plan changed.');}catch(e){alert(e.message)}}

function renderStudents(){const q=(document.getElementById('studentSearch')?.value||'').trim().toLowerCase(),st=document.getElementById('studentStatusFilter')?.value||'',course=document.getElementById('studentCourseFilter')?.value||'';const list=studentsCache.filter(x=>{const hay=[x.first_name,x.last_name,x.username,x.telegram_user_id,(x.course_names||[]).join(' '),(x.plan_names||[]).join(' ')].filter(Boolean).join(' ').toLowerCase();return(!q||hay.includes(q))&&(!st||x.subscription_status===st)&&(!course||(x.course_ids||[]).map(String).includes(String(course)));});document.getElementById('studentsTable').innerHTML=table(['Student','Telegram','Username','Active courses','Status','Action'],list.map(x=>'<tr><td><b>'+esc([x.first_name,x.last_name].filter(Boolean).join(' ')||'—')+'</b><div class="muted">'+esc((x.course_names||[]).join(', ')||'No active course')+'</div></td><td>'+esc(x.telegram_user_id)+'</td><td>'+esc(x.username?'@'+x.username:'—')+'</td><td>'+esc(x.active_courses)+'</td><td><span class="status '+statusClass(x.subscription_status)+'">'+esc(x.subscription_status)+'</span></td><td><button class="action-btn primary" onclick="openStudent(&quot;'+esc(x.id)+'&quot;)">Manage</button></td></tr>'))}
async function openStudent(id){try{const d=await api('/dashboard/api/students/'+encodeURIComponent(id));currentStudent=d;document.getElementById('studentModalTitle').textContent=[d.user.first_name,d.user.last_name].filter(Boolean).join(' ')||'Student';document.getElementById('studentModalBody').innerHTML=studentDetailHtml(d);document.getElementById('studentModal').classList.add('open')}catch(e){alert('Could not load student: '+e.message)}}
function closeStudent(){document.getElementById('studentModal').classList.remove('open');currentStudent=null}
function studentDetailHtml(d){
 const u=d.user||{}, subs=d.subscriptions||[], payments=d.payments||[], activity=d.activity||[];
 const name=[u.first_name,u.last_name].filter(Boolean).join(' ')||'Student';
 const active=subs.filter(s=>s.status==='active').length;
 const approved=payments.filter(p=>p.status==='approved').length;
 const pending=payments.filter(p=>p.status==='pending').length;
 let html='<div class="student-profile modal-body">';
 html+='<div class="profile-hero"><div class="profile-avatar">'+esc((name[0]||'S').toUpperCase())+'</div><div class="profile-main"><div class="eyebrow">Student profile</div><h2>'+esc(name)+'</h2><div class="profile-subline">'+esc(u.username?'@'+u.username:'No username')+' · Telegram ID '+esc(u.telegram_user_id||'—')+'</div></div><div class="profile-joined">Joined<br><b>'+esc(u.created_at||'—')+'</b></div></div>';
 html+='<div class="profile-stats"><div class="mini-stat"><div class="label">Active courses</div><div class="value">'+active+'</div></div><div class="mini-stat"><div class="label">Subscriptions</div><div class="value">'+subs.length+'</div></div><div class="mini-stat"><div class="label">Approved payments</div><div class="value">'+approved+'</div></div><div class="mini-stat"><div class="label">Pending payments</div><div class="value">'+pending+'</div></div></div>';
 html+='<div class="profile-tabs"><button class="profile-tab active" onclick="switchStudentTab(\'studentOverviewTab\',this)">Overview</button><button class="profile-tab" onclick="switchStudentTab(\'studentPaymentsTab\',this)">Payments</button><button class="profile-tab" onclick="switchStudentTab(\'studentActivityTab\',this)">Activity</button></div>';
 html+='<div id="studentOverviewTab" class="student-tab active">';
 html+='<div class="section-head"><div><h2 style="font-size:18px">Course Access</h2><p>Subscriptions, Telegram access and quick actions.</p></div></div>';
 if(!subs.length) html+='<div class="empty">No subscriptions found.</div>';
 else subs.forEach(s=>{
   const isActive=s.status==='active';
   html+='<div class="sub-card"><div class="sub-head"><div><b>'+esc(s.course_name)+'</b><div class="muted">Plan: '+esc(s.plan_name)+' · '+esc(s.status)+'</div></div><span class="status '+statusClass(s.status)+'">'+esc(s.status)+'</span></div><div class="detail-grid" style="margin-top:12px"><div><div class="detail-label">Started</div><div class="detail-value">'+esc(s.started_at||'—')+'</div></div><div><div class="detail-label">Expires</div><div class="detail-value">'+esc(s.is_lifetime?'Lifetime':(s.expires_at||'—'))+'</div></div><div><div class="detail-label">Telegram joined</div><div class="detail-value">'+(s.joined_channel_at?'Yes':'No')+'</div></div></div>'+(isActive?'<div class="modal-actions"><button class="action-btn good" onclick="extendStudent(\''+esc(s.id)+'\')">+7 Days</button><button class="action-btn good" onclick="extendStudent(\''+esc(s.id)+'\',30)">+30 Days</button><button class="action-btn good" onclick="extendStudent(\''+esc(s.id)+'\',90)">+90 Days</button><button class="action-btn primary" onclick="generateStudentInvite(\''+esc(s.id)+'\')">Generate Invite</button><button class="action-btn danger" onclick="revokeStudent(\''+esc(s.id)+'\')">Revoke Access</button></div>':'')+'</div>';
 });
 const activePlans=plansCache.filter(p=>p.is_active && coursesCache.some(c=>c.id===p.course_id && c.status==='active')); const grouped={}; activePlans.forEach(p=>(grouped[p.course_id]??=[]).push(p)); let options='<option value="">Select active course plan…</option>'; Object.entries(grouped).forEach(([cid,arr])=>{const cname=(coursesCache.find(c=>c.id===cid)||{}).name||'Course'; options+='<optgroup label="'+esc(cname)+'">'+arr.map(p=>'<option value="'+esc(p.id)+'">'+esc(p.name)+' — '+esc(p.currency||'')+' '+esc(p.price)+'</option>').join('')+'</optgroup>'});
 html+='<div class="section-head" style="margin-top:24px"><div><h2 style="font-size:18px">Manual Grant</h2><p>Select a plan from its course.</p></div></div><div class="modal-actions"><select id="grantPlan" class="select-dark">'+options+'</select><button class="action-btn primary" onclick="grantStudent()">Grant Access</button></div><div class="muted" style="margin-top:8px">The dashboard creates a one-use Telegram invite and uses the existing customer notification function.</div></div>';
 html+='</div>';
 html+='<div id="studentPaymentsTab" class="student-tab">';
 if(!payments.length) html+='<div class="empty">No payment history found.</div>'; else html+='<div class="history-list">'+payments.map(p=>'<div class="history-item"><div><b>'+esc(p.course_name||'Course')+'</b><div class="muted">'+esc(p.plan_name||'Plan')+' · #'+esc(p.payment_number||p.id||'—')+'</div></div><div class="history-right"><b>'+esc(p.currency||'INR')+' '+esc(p.amount||'0')+'</b><span class="status '+statusClass(p.status)+'">'+esc(p.status)+'</span><div class="muted">'+esc(p.submitted_at||'—')+'</div></div></div>').join('')+'</div>';
 html+='</div>';
 html+='<div id="studentActivityTab" class="student-tab">';
 if(!activity.length) html+='<div class="empty">No student-specific activity has been recorded yet.</div>'; else html+='<div class="history-list">'+activity.map(a=>'<div class="history-item"><div><b>'+esc((a.action||'activity').replaceAll('_',' '))+'</b><div class="muted">'+esc(a.description||'Dashboard action')+'</div></div><div class="history-right"><span class="activity-pill">'+esc(a.entity_type||'system')+'</span><div class="muted">'+esc(a.created_at||'—')+'</div></div></div>').join('')+'</div>';
 html+='</div></div>'; return html;
}
function switchStudentTab(id,btn){document.querySelectorAll('#studentModal .student-tab').forEach(x=>x.classList.remove('active'));const el=document.getElementById(id);if(el)el.classList.add('active');document.querySelectorAll('#studentModal .profile-tab').forEach(x=>x.classList.remove('active'));if(btn)btn.classList.add('active')}

function courseFormHtml(c={}){
 const editing=!!c.id, plans=Array.isArray(c.plans)?c.plans:[];
 const planRows=plans.length?plans.map(p=>`<div class="plan-card"><div class="plan-card-head"><div><b>${esc(p.name)}</b><div class="muted">${esc(p.currency||'INR')} ${esc(p.price??'')} · ${esc(p.plan_type==='lifetime'?'Lifetime':((p.duration_days||'—')+' days'))}</div></div><span class="status ${p.is_active?'active-status':'danger-status'}">${p.is_active?'Active':'Inactive'}</span></div><div class="row-actions" style="margin-top:10px"><button class="action-btn primary" onclick="editCoursePlan('${esc(p.id)}')">Edit</button><button class="action-btn ${p.is_active?'danger':'good'}" onclick="toggleCoursePlan('${esc(p.id)}')">${p.is_active?'Deactivate':'Activate'}</button><button class="action-btn danger" onclick="deleteCoursePlan('${esc(p.id)}')">Delete</button></div></div>`).join(''):'<div class="empty">No plans for this course yet.</div>';
 const telegramSection=editing?`<div class="section-head" style="margin-top:24px"><div><h2 style="font-size:18px">Telegram Access</h2><p>Connect a Telegram group, supergroup or channel and verify the bot administrator permissions.</p></div><button class="action-btn primary" onclick="testCourseTelegram('${esc(c.id)}')">Test Connection</button></div><div id="courseTelegramBox" class="telegram-box"><div class="muted">Loading Telegram destination…</div></div>`:'';
 const planSection=editing?`<div class="section-head" style="margin-top:24px"><div><h2 style="font-size:18px">Course Plans</h2><p>Every plan belongs only to this course.</p></div><button class="action-btn primary" onclick="openPlanForm('${esc(c.id)}')">+ Add Plan</button></div><div class="plan-list">${planRows}</div>`:'';
 const lessons=Array.isArray(c.lessons)?c.lessons:[];
 const lessonRows=lessons.length?lessons.map(l=>`<div class="plan-card"><div class="plan-card-head"><div><b>${esc(l.title)}</b><div class="muted">${esc((l.content_type||'video').toUpperCase())} · ${l.content_url?'<a href="'+esc(l.content_url)+'" target="_blank" rel="noopener">Open content</a>':(l.telegram_message_id?('Telegram message #'+esc(l.telegram_message_id)):'No content link yet')}</div></div><span class="status ${l.is_published?'active-status':'danger-status'}">${l.is_published?'Published':'Draft'}</span></div><div class="muted" style="margin-top:8px">Order: ${esc(l.sort_order??0)}${l.description?' · '+esc(l.description):''}</div><div class="row-actions" style="margin-top:10px"><button class="action-btn primary" onclick="editCourseLesson('${esc(l.id)}','${esc(c.id)}')">Edit</button><button class="action-btn ${l.is_published?'danger':'good'}" onclick="toggleCourseLesson('${esc(l.id)}','${esc(c.id)}',${l.is_published?'false':'true'})">${l.is_published?'Unpublish':'Publish'}</button><button class="action-btn danger" onclick="deleteCourseLesson('${esc(l.id)}','${esc(c.id)}')">Delete</button></div></div>`).join(''):'<div class="empty">No lessons yet. Add the first lesson for this course.</div>';
 const contentSection=editing?`<div class="section-head" style="margin-top:24px"><div><h2 style="font-size:18px">Course Content</h2><p>Manage lessons, ordering and Telegram/video mapping for this course.</p></div><button class="action-btn primary" onclick="openLessonForm('${esc(c.id)}')">+ Add Lesson</button></div><div class="plan-list">${lessonRows}</div>`:'';
 return '<div class="modal-body"><div class="form-grid"><div class="form-field"><label>Course name</label><input id="courseName" class="search-input" value="'+esc(c.name||'')+'" placeholder="e.g. Python Masterclass"></div><div class="form-field"><label>Status</label><select id="courseStatus" class="select-dark"><option value="active" '+(c.status==='active'||!c.status?'selected':'')+'>Active</option><option value="inactive" '+(c.status==='inactive'?'selected':'')+'>Inactive</option></select></div></div><div class="form-field" style="margin-top:14px"><label>Description</label><textarea id="courseDescription" class="textarea-dark" rows="5" placeholder="Course description">'+esc(c.description||'')+'</textarea></div>'+ (editing?'<div class="detail-grid" style="margin-top:18px"><div class="detail-card"><div class="detail-label">Slug</div><div class="detail-value">'+esc(c.slug||'—')+'</div></div><div class="detail-card"><div class="detail-label">Plans</div><div class="detail-value">'+esc(c.plan_count||0)+'</div></div><div class="detail-card"><div class="detail-label">Telegram group</div><div class="detail-value">'+esc(c.group_connected?(c.group_title||'Connected'):'Not connected')+'</div></div></div>':'')+telegramSection+planSection+contentSection+'<div class="modal-actions"><button class="action-btn primary" onclick="saveCourse('+ (editing?"'"+esc(c.id)+"'":"null") +')">'+(editing?'Save Changes':'Create Course')+'</button>'+(editing?'<button class="action-btn danger" onclick="deleteCourse(\''+esc(c.id)+'\')">Delete Course</button>':'')+'</div></div>';
}

/* ===== V32.3 RESTORED COURSE MANAGEMENT ===== */
async function openCourse(courseId){
  try{
    const c = await api('/dashboard/api/courses/'+encodeURIComponent(courseId));
    currentCourse = c;
    const modal = document.getElementById('courseModal');
    const title = document.getElementById('courseModalTitle');
    const body = document.getElementById('courseModalBody');
    if(!modal || !body){
      console.error('Course modal elements are missing');
      alert('Course management UI is unavailable. Please refresh the dashboard.');
      return;
    }
    if(title) title.textContent = c.name || 'Manage Course';

    const plans = Array.isArray(c.plans) ? c.plans : [];
    const lessons = Array.isArray(c.lessons) ? c.lessons : [];
    const tg = c.telegram || c.telegram_connection || c.telegram_access || null;

    body.innerHTML = `
      <div class="modal-body" style="display:flex;flex-direction:column;gap:18px;min-height:0;overflow-y:auto">
        <div class="detail-card" style="padding:18px">
          <div class="section-head">
            <div>
              <div class="kicker">Course workspace</div>
              <h2 style="margin:4px 0">${esc(c.name||'Untitled Course')}</h2>
              <p>${esc(c.description||'Manage plans, lessons and Telegram access from one place.')}</p>
            </div>
            <span class="status ${statusClass(c.status)}">${esc(c.status||'unknown')}</span>
          </div>
        </div>

        <div class="course-tabs" style="display:flex;gap:8px;flex-wrap:wrap">
          <button class="action-btn primary" onclick="showCourseTab('courseOverviewTab',this)">Overview</button>
          <button class="action-btn" onclick="showCourseTab('coursePlansTab',this)">Plans</button>
          <button class="action-btn" onclick="showCourseTab('courseLessonsTab',this)">Content</button>
          <button class="action-btn" onclick="showCourseTab('courseTelegramTab',this)">Telegram</button>
        </div>

        <div id="courseOverviewTab" class="course-tab">
          <div class="detail-card" style="padding:18px">
            <div class="section-head"><div><b>Course Overview</b><p>Basic course information.</p></div></div>
            <div class="settings-list">
              <div class="settings-row"><span>Name</span><b>${esc(c.name||'—')}</b></div>
              <div class="settings-row"><span>Slug</span><b>${esc(c.slug||'—')}</b></div>
              <div class="settings-row"><span>Status</span><b>${esc(c.status||'—')}</b></div>
              <div class="settings-row"><span>Plans</span><b>${plans.length}</b></div>
              <div class="settings-row"><span>Lessons</span><b>${lessons.length}</b></div>
            </div>
          </div>
        </div>

        <div id="coursePlansTab" class="course-tab" style="display:none">
          <div class="detail-card" style="padding:18px">
            <div class="section-head">
              <div><b>Course Plans</b><p>Create and manage pricing plans.</p></div>
              <button class="action-btn primary" onclick="openPlanForm('${esc(c.id)}')">+ Add Plan</button>
            </div>
            <div class="plan-list">
              ${plans.length ? plans.map(p=>`
                <div class="plan-card">
                  <div class="plan-card-head">
                    <div><b>${esc(p.name||'Unnamed Plan')}</b><div class="muted">${esc(p.currency||'')} ${esc(p.price??'')} · ${esc(p.plan_type||'')}</div></div>
                    <span class="status ${p.is_active?'active-status':''}">${p.is_active?'Active':'Inactive'}</span>
                  </div>
                  <div class="row-actions" style="margin-top:10px">
                    <button class="action-btn" onclick="editCoursePlan('${esc(p.id)}','${esc(c.id)}')">Edit</button>
                    <button class="action-btn ${p.is_active?'danger':'good'}" onclick="toggleCoursePlan('${esc(p.id)}','${esc(c.id)}',${p.is_active?'false':'true'})">${p.is_active?'Deactivate':'Activate'}</button>
                  </div>
                </div>`).join('') : '<div class="empty">No plans yet.</div>'}
            </div>
          </div>
        </div>

        <div id="courseLessonsTab" class="course-tab" style="display:none">
          <div class="detail-card" style="padding:18px">
            <div class="section-head">
              <div><b>Course Content</b><p>Manage lessons and protected course content.</p></div>
              <button class="action-btn primary" onclick="openLessonForm('${esc(c.id)}')">+ Add Lesson</button>
            </div>
            <div class="plan-list">
              ${lessons.length ? lessons.map((l,i)=>`
                <div class="plan-card">
                  <div class="plan-card-head">
                    <div><b>${i+1}. ${esc(l.title||'Untitled Lesson')}</b><div class="muted">${esc(l.content_type||'content')}</div></div>
                    <span class="status ${l.is_published!==false?'active-status':''}">${l.is_published!==false?'Published':'Draft'}</span>
                  </div>
                  <div class="row-actions" style="margin-top:10px">
                    <button class="action-btn" onclick="editCourseLesson('${esc(l.id)}','${esc(c.id)}')">Edit</button>
                    <button class="action-btn ${l.is_published!==false?'danger':'good'}" onclick="toggleCourseLesson('${esc(l.id)}','${esc(c.id)}',${l.is_published!==false?'false':'true'})">${l.is_published!==false?'Unpublish':'Publish'}</button>
                    <button class="action-btn danger" onclick="deleteCourseLesson('${esc(l.id)}','${esc(c.id)}')">Delete</button>
                  </div>
                </div>`).join('') : '<div class="empty">No lessons yet.</div>'}
            </div>
          </div>
        </div>

        <div id="courseTelegramTab" class="course-tab" style="display:none">
          <div id="courseTelegramBox" class="detail-card" style="padding:18px">
            <div class="section-head">
              <div><b>Telegram Access</b><p>Connect this course to a private group, supergroup or channel.</p></div>
            </div>
            <div id="courseTelegramContent"><div class="empty">Loading Telegram status…</div></div>
          </div>
        </div>

        <div class="modal-actions">
          <button class="action-btn" onclick="closeCourse()">Close</button>
        </div>
      </div>`;

    modal.classList.add('open');
    document.body.classList.add('modal-open');
    showCourseTab('courseOverviewTab', document.querySelector('#courseModal .course-tabs .action-btn'));
    await loadCourseTelegram(c.id);
  }catch(e){
    console.error(e);
    alert(e.message||'Could not open course.');
  }
}

function showCourseTab(id, btn){
  document.querySelectorAll('#courseModal .course-tab').forEach(x=>x.style.display='none');
  const el=document.getElementById(id);
  if(el) el.style.display='block';
  document.querySelectorAll('#courseModal .course-tabs .action-btn').forEach(x=>x.classList.remove('primary'));
  if(btn) btn.classList.add('primary');
}

function closeCourse(){
  document.getElementById('courseModal')?.classList.remove('open');
  if(!document.getElementById('planModal')?.classList.contains('open') &&
     !document.getElementById('lessonModal')?.classList.contains('open')){
    document.body.classList.remove('modal-open');
  }
}

function openCourseCreate(){
  const modal=document.getElementById('courseModal');
  const title=document.getElementById('courseModalTitle');
  const body=document.getElementById('courseModalBody');
  if(!modal||!body){alert('Course modal is unavailable.');return;}
  if(title) title.textContent='Create Course';
  body.innerHTML=`<div class="modal-body" style="overflow-y:auto">
    <div class="form-grid">
      <div class="form-field"><label>Course name</label><input id="newCourseName" class="search-input" placeholder="Course name"></div>
      <div class="form-field"><label>Status</label><select id="newCourseStatus" class="select-dark"><option value="active">Active</option><option value="inactive">Inactive</option></select></div>
    </div>
    <div class="form-field" style="margin-top:14px"><label>Description</label><textarea id="newCourseDescription" class="textarea-dark" rows="4" placeholder="Course description"></textarea></div>
    <div class="modal-actions">
      <button class="action-btn primary" onclick="saveCourse()">Create Course</button>
      <button class="action-btn" onclick="closeCourse()">Cancel</button>
    </div>
  </div>`;
  modal.classList.add('open');
  document.body.classList.add('modal-open');
}

async function saveCourse(courseId=null){
  const payload={
    name:(document.getElementById('newCourseName')?.value||'').trim(),
    description:(document.getElementById('newCourseDescription')?.value||'').trim(),
    status:document.getElementById('newCourseStatus')?.value||'active'
  };
  if(!payload.name)return alert('Course name is required.');
  try{
    const url=courseId?'/dashboard/api/courses/'+encodeURIComponent(courseId):'/dashboard/api/courses';
    const r=await fetch(url,{method:courseId?'PATCH':'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
    const d=await r.json(); if(!r.ok) throw new Error(d.detail||'Course save failed');
    closeCourse(); await refreshAll(); alert(courseId?'Course updated.':'Course created.');
  }catch(e){alert(e.message)}
}

async function toggleCourse(courseId){
  try{
    const c=await api('/dashboard/api/courses/'+encodeURIComponent(courseId));
    const next=c.status==='active'?'inactive':'active';
    const r=await fetch('/dashboard/api/courses/'+encodeURIComponent(courseId),{
      method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({status:next})
    });
    const d=await r.json();if(!r.ok)throw new Error(d.detail||'Course update failed');
    await refreshAll();
  }catch(e){alert(e.message)}
}
/* ===== END V32.3 RESTORED COURSE MANAGEMENT ===== */

function closeLessonModal(){document.getElementById('lessonModal')?.classList.remove('open');if(!document.getElementById('courseModal')?.classList.contains('open')&&!document.getElementById('planModal')?.classList.contains('open'))document.body.classList.remove('modal-open')}
function openLessonForm(courseId,lesson={}){const editing=!!lesson.id;document.getElementById('lessonModalTitle').textContent=editing?'Edit Lesson':'Add Lesson';document.getElementById('lessonModalBody').innerHTML=`<div class="modal-body"><div class="form-grid"><div class="form-field"><label>Lesson title</label><input id="lessonTitle" class="search-input" value="${esc(lesson.title||'')}" placeholder="e.g. Introduction"></div><div class="form-field"><label>Order</label><input id="lessonOrder" class="search-input" type="number" min="0" value="${esc(lesson.sort_order??0)}"></div></div><div class="form-grid" style="margin-top:14px"><div class="form-field"><label>Content type</label><select id="lessonType" class="select-dark"><option value="video" ${lesson.content_type==='video'||!lesson.content_type?'selected':''}>Video / URL</option><option value="telegram" ${lesson.content_type==='telegram'?'selected':''}>Telegram message</option><option value="link" ${lesson.content_type==='link'?'selected':''}>External link</option><option value="text" ${lesson.content_type==='text'?'selected':''}>Text / Notes</option></select></div><div class="form-field"><label>Content URL</label><input id="lessonUrl" class="search-input" value="${esc(lesson.content_url||'')}" placeholder="https://…"></div></div><div class="form-field" style="margin-top:14px"><label>Telegram message ID <span class="muted">(optional)</span></label><input id="lessonTelegramId" class="search-input" value="${esc(lesson.telegram_message_id||'')}" placeholder="12345"></div><div class="form-field" style="margin-top:14px"><label>Description / notes</label><textarea id="lessonDescription" class="textarea-dark" rows="4" placeholder="Short lesson description">${esc(lesson.description||'')}</textarea></div><label style="display:flex;gap:8px;align-items:center;margin-top:14px"><input id="lessonPublished" type="checkbox" ${lesson.is_published===false?'':'checked'}> Published</label><div class="modal-actions"><button class="action-btn primary" onclick="saveCourseLesson('${esc(courseId)}'${editing?`, '${esc(lesson.id)}'`:''})">${editing?'Save Changes':'Create Lesson'}</button><button class="action-btn" onclick="closeLessonModal()">Cancel</button></div></div>`;document.getElementById('lessonModal').classList.add('open');document.body.classList.add('modal-open')}
async function saveCourseLesson(courseId,lessonId=null){const payload={title:(document.getElementById('lessonTitle')?.value||'').trim(),sort_order:Number(document.getElementById('lessonOrder')?.value||0),content_type:document.getElementById('lessonType')?.value||'video',content_url:(document.getElementById('lessonUrl')?.value||'').trim(),telegram_message_id:(document.getElementById('lessonTelegramId')?.value||'').trim(),description:(document.getElementById('lessonDescription')?.value||'').trim(),is_published:!!document.getElementById('lessonPublished')?.checked};if(!payload.title)return alert('Lesson title is required.');try{const url='/dashboard/api/courses/'+encodeURIComponent(courseId)+'/lessons'+(lessonId?'/'+encodeURIComponent(lessonId):'');const r=await fetch(url,{method:lessonId?'PATCH':'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});const d=await r.json();if(!r.ok)throw new Error(d.detail||'Lesson save failed');closeLessonModal();await openCourse(courseId);await refreshAll();alert(lessonId?'Lesson updated.':'Lesson created.')}catch(e){alert(e.message)}}
async function editCourseLesson(lessonId,courseId){try{const c=await api('/dashboard/api/courses/'+encodeURIComponent(courseId));const l=(c.lessons||[]).find(x=>String(x.id)===String(lessonId));if(!l)return alert('Lesson not found.');openLessonForm(courseId,l)}catch(e){alert(e.message)}}
async function toggleCourseLesson(lessonId,courseId,published){try{const r=await fetch('/dashboard/api/courses/'+encodeURIComponent(courseId)+'/lessons/'+encodeURIComponent(lessonId),{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({is_published:published})});const d=await r.json();if(!r.ok)throw new Error(d.detail||'Lesson update failed');await openCourse(courseId);await refreshAll()}catch(e){alert(e.message)}}
async function deleteCourseLesson(lessonId,courseId){if(!confirm('Delete this lesson? This cannot be undone.'))return;try{const r=await fetch('/dashboard/api/courses/'+encodeURIComponent(courseId)+'/lessons/'+encodeURIComponent(lessonId),{method:'DELETE'});const d=await r.json();if(!r.ok)throw new Error(d.detail||'Lesson delete failed');await openCourse(courseId);await refreshAll();alert('Lesson deleted.')}catch(e){alert(e.message)}}

function telegramStatusClass(v){return v?'active-status':'danger-status'}
function telegramTypeLabel(t){return t==='channel'?'Channel':(t==='supergroup'?'Supergroup':(t==='group'?'Group':'Telegram'))}
function telegramBoxHtml(d){
  const ch=(d.channels||[])[0], pending=d.pending_request;
  if(!ch){
    if(pending){
      return '<div class="telegram-connect-panel"><div><div class="eyebrow">Waiting for Admin Bot</div><h3>Connection code: '+esc(pending.connection_code)+'</h3><div class="muted">Add the <b>Admin Bot</b> as administrator to your private group/channel, then send this command <b>inside that Telegram destination</b>:</div><div class="detail-card" style="margin-top:12px"><div class="detail-value" style="font-family:monospace">'+esc('/connect '+pending.connection_code)+'</div></div><div class="muted" style="margin-top:10px">Expires: '+esc(pending.expires_at||'15 minutes')+'. The Admin Bot will verify Group / Supergroup / Channel, administrator status and invite permission.</div></div><div class="row-actions" style="margin-top:14px"><button class="action-btn" onclick="loadCourseTelegram(currentCourse.id)">Refresh Status</button><button class="action-btn primary" onclick="requestCourseTelegramConnection(currentCourse.id)">Generate New Code</button></div></div>';
    }
    return '<div class="telegram-connect-panel"><div><b>Connect Telegram destination</b><div class="muted">Your existing Admin Bot will do the Telegram-side verification. No Chat ID is required here.</div></div><div class="telegram-connect-row"><button class="action-btn primary" onclick="requestCourseTelegramConnection(currentCourse.id)">Connect with Admin Bot</button></div></div>';
  }
  const inv=ch.latest_invite; const type=telegramTypeLabel(ch.chat_type);
  return '<div class="telegram-connect-panel">'+
  '<div class="telegram-destination-head"><div><div class="eyebrow">Connected destination</div><h3>'+esc(ch.channel_title||type)+'</h3><div class="muted">'+esc(ch.channel_username?'@'+ch.channel_username:'Private / no username')+'</div></div><span class="status active-status">Verified</span></div>'+
  '<div class="telegram-grid">'+
  '<div class="detail-card"><div class="detail-label">Type</div><div class="detail-value">'+esc(type)+'</div></div>'+
  '<div class="detail-card"><div class="detail-label">Telegram Chat ID</div><div class="detail-value">'+esc(ch.telegram_chat_id||'—')+'</div></div>'+
  '<div class="detail-card"><div class="detail-label">Bot status</div><div class="detail-value"><span class="status '+telegramStatusClass(ch.bot_is_admin)+'">'+esc(ch.bot_is_admin?'Administrator':'Not Admin')+'</span></div></div>'+
  '<div class="detail-card"><div class="detail-label">Invite permission</div><div class="detail-value"><span class="status '+telegramStatusClass(ch.can_invite_users)+'">'+esc(ch.can_invite_users?'Allowed':'Not allowed')+'</span></div></div>'+
  '<div class="detail-card"><div class="detail-label">Manage / ban permission</div><div class="detail-value"><span class="status '+telegramStatusClass(ch.can_manage_members)+'">'+esc(ch.can_manage_members?'Allowed':'Not detected')+'</span></div></div>'+
  '<div class="detail-card"><div class="detail-label">Course destination</div><div class="detail-value">'+(ch.telegram_link?'<a href="'+esc(ch.telegram_link)+'" target="_blank" rel="noopener">Open in Telegram</a>':'Private destination')+'</div></div>'+
  '</div>'+
  '<div class="telegram-connect-row" style="margin-top:12px"><button class="action-btn primary" onclick="requestCourseTelegramConnection(currentCourse.id)">Reconnect with Admin Bot</button><button class="action-btn" onclick="testCourseTelegram(currentCourse.id)">Refresh Verification</button></div>'+
  (inv?'<div class="notice" style="margin-top:14px">Latest student invite: <a href="'+esc(inv.telegram_invite_link||'#')+'" target="_blank" rel="noopener">Open invite link</a><div class="muted">Status: '+esc(inv.status||'unknown')+'</div></div>':'')+
  (d.course_invite?'<div class="notice" style="margin-top:14px"><div class="eyebrow">Course Invite Link</div><div class="detail-value" style="word-break:break-all;margin-top:6px"><a href="'+esc(d.course_invite.telegram_invite_link)+'" target="_blank" rel="noopener">'+esc(d.course_invite.telegram_invite_link)+'</a></div><div class="muted" style="margin-top:6px">🟢 One-time link — it becomes unusable after one successful join.</div><div class="row-actions" style="margin-top:12px"><button class="action-btn" onclick="revokeCourseInvite(currentCourse.id)">Revoke Link</button><button class="action-btn primary" onclick="generateCourseInvite(currentCourse.id)">Generate New Link</button></div></div>':'<div class="notice" style="margin-top:14px"><div class="eyebrow">Course Invite Link</div><div class="muted">No course invite link exists yet. Generate a secure one-time link for this connected Telegram destination.</div><div class="row-actions" style="margin-top:12px"><button class="action-btn primary" onclick="generateCourseInvite(currentCourse.id)">Generate One-Time Link</button></div></div>')+
  '</div>';
}
async function loadCourseTelegram(courseId){const box=document.getElementById('courseTelegramBox');if(!box)return;try{const d=await api('/dashboard/api/courses/'+encodeURIComponent(courseId)+'/telegram');box.innerHTML=telegramBoxHtml(d)}catch(e){box.innerHTML='<div class="notice danger">'+esc(e.message)+'</div>'}}
async function generateCourseInvite(courseId){if(!confirm('Generate a new one-time course invite? Any existing active course invite will be revoked.'))return;try{const r=await fetch('/dashboard/api/courses/'+encodeURIComponent(courseId)+'/telegram/invite',{method:'POST'});const d=await r.json();if(!r.ok)throw new Error(d.detail||'Could not generate invite');await loadCourseTelegram(courseId);if(navigator.clipboard){try{await navigator.clipboard.writeText(d.invite_link)}catch(e){}}alert('One-time invite created.\n\n'+d.invite_link+'\n\nThe link can be used once.');}catch(e){alert(e.message)}}
async function revokeCourseInvite(courseId){if(!confirm('Revoke the current course invite link? It will stop working immediately.'))return;try{const r=await fetch('/dashboard/api/courses/'+encodeURIComponent(courseId)+'/telegram/invite/revoke',{method:'POST'});const d=await r.json();if(!r.ok)throw new Error(d.detail||'Could not revoke invite');await loadCourseTelegram(courseId);alert('Course invite revoked.');}catch(e){alert(e.message)}}
async function requestCourseTelegramConnection(courseId){const box=document.getElementById('courseTelegramBox');if(box)box.innerHTML='<div class="notice">Creating a secure Admin Bot connection code…</div>';try{const r=await fetch('/dashboard/api/courses/'+encodeURIComponent(courseId)+'/telegram/request',{method:'POST'});const d=await r.json();if(!r.ok)throw new Error(d.detail||'Could not create connection request');await loadCourseTelegram(courseId);alert('Connection code: '+d.connection_code+'\n\nAdd the Admin Bot as administrator to your private Group / Supergroup / Channel, then send:\n/connect '+d.connection_code+'\n\ninside that Telegram destination.');}catch(e){if(box)box.innerHTML='<div class="notice danger">'+esc(e.message)+'</div>';alert(e.message)}}
async function approvePayment(id){if(!confirm('Approve this payment and provision course access?'))return;try{const r=await fetch('/dashboard/api/payments/'+encodeURIComponent(id)+'/approve',{method:'POST',headers:{'Content-Type':'application/json'}});const d=await r.json();if(!r.ok)throw new Error(d.detail||'Approval failed');closePayment();await refreshAll();alert('Payment approved and course access provisioned.')}catch(e){alert(e.message)}}
async function rejectPayment(id){const reason=prompt('Optional rejection reason:','');if(reason===null)return;if(!confirm('Reject this payment?'))return;try{const r=await fetch('/dashboard/api/payments/'+encodeURIComponent(id)+'/reject',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({reason})});const d=await r.json();if(!r.ok)throw new Error(d.detail||'Rejection failed');closePayment();await refreshAll();alert('Payment rejected.')}catch(e){alert(e.message)}}

let broadcastPreviewData=null;
function loadBroadcast(){
 const sel=document.getElementById('broadcastCourse'); if(!sel)return;
 sel.innerHTML='<option value="">Select course…</option>'+coursesCache.map(c=>'<option value="'+esc(c.id)+'">'+esc(c.name)+'</option>').join('');
 broadcastAudienceChanged();
}
function broadcastAudienceChanged(){const a=document.getElementById('broadcastAudience')?.value;const c=document.getElementById('broadcastCourse');if(c)c.style.display=a==='course'?'block':'none';broadcastPreviewData=null;}
async function previewBroadcast(){
 const audience=document.getElementById('broadcastAudience').value, course_id=document.getElementById('broadcastCourse').value||null, message=document.getElementById('broadcastMessage').value.trim();
 if(!message)return alert('Write a message first.'); if(audience==='course'&&!course_id)return alert('Select a course first.');
 try{const r=await fetch('/dashboard/api/broadcast/preview',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({audience,course_id,message})});const d=await r.json();if(!r.ok)throw new Error(d.detail||'Preview failed');broadcastPreviewData=d;const sample=(d.sample||[]).map(x=>'<div><b>'+esc(x.name)+'</b> <span class="muted">'+esc(x.username?'@'+x.username:x.telegram_user_id)+'</span></div>').join('');document.getElementById('broadcastPreview').innerHTML='<div class="muted">Recipients</div><div class="preview-count">'+esc(d.recipient_count)+'</div><div class="muted">'+esc(audience==='all'?'All students':audience==='active'?'Active students':audience==='expiring'?'Expiring in 7 days':'Selected course')+'</div><div class="broadcast-sample">'+(sample||'<div class="empty">No recipients.</div>')+'</div>'+(d.recipient_count?'<button class="action-btn primary broadcast-send" onclick="sendBroadcast()">Send to '+esc(d.recipient_count)+' students</button>':'');}catch(e){alert(e.message)}}
async function sendBroadcast(){if(!broadcastPreviewData)return;if(!confirm('Send this message to '+broadcastPreviewData.recipient_count+' recipients? This action cannot be undone.'))return;try{const p={...broadcastPreviewData,confirm:true};const r=await fetch('/dashboard/api/broadcast/send',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(p)});const d=await r.json();if(!r.ok)throw new Error(d.detail||'Broadcast failed');alert('Broadcast complete.\n\nSent: '+d.sent+'\nFailed: '+d.failed);broadcastPreviewData=null;}catch(e){alert(e.message)}}

async function loadSettings(){
 try{
   const d=await api('/dashboard/api/settings/health');
   const checks=[['Dashboard',d.dashboard?.status],['Supabase',d.supabase?.status],['Admin Bot',d.admin_bot?.status],['Customer Bot',d.customer_bot?.status]];
   const ok=v=>v==='online'||v==='connected';
   document.getElementById('settingsOverall').className='status '+(checks.every(x=>ok(x[1]))?'active-status':'pending-status');
   document.getElementById('settingsOverall').textContent=checks.every(x=>ok(x[1]))?'Healthy':'Needs attention';
   document.getElementById('healthList').innerHTML=checks.map(([name,status])=>'<div class="settings-row"><span>'+esc(name)+'</span><span class="status '+(ok(status)?'active-status':status==='missing'?'danger-status':'pending-status')+'">'+esc(status||'unknown')+'</span></div>').join('');
   const botRole=(label,bot,role,uses)=>{const status=bot?.status||'unknown';const cls=ok(status)?'active-status':status==='missing'?'danger-status':'pending-status';const identity=status==='connected'?((bot.name||'Unnamed bot')+' · '+(bot.username?'@'+bot.username:'username unavailable')):'Not available';return '<div class="bot-role"><div class="bot-role-head"><div><div class="bot-role-name">'+esc(label)+'</div><div class="bot-role-sub">'+esc(role)+'</div></div><span class="status '+cls+'">'+esc(status)+'</span></div><div class="bot-role-meta"><div class="bot-role-row"><span class="bot-role-label">Bot identity</span><span class="bot-role-value">'+esc(identity)+'</span></div><div class="bot-role-row"><span class="bot-role-label">Configured</span><span class="bot-role-value">'+(bot?.configured?'Yes':'No')+'</span></div></div><div class="bot-role-uses"><b>Used for</b><br>'+uses+'</div></div>';};
   document.getElementById('botRoles').innerHTML=botRole('Admin Bot',d.admin_bot,'Administration','Admin authentication · course management · group management · payment management')+botRole('Customer Bot',d.customer_bot,'Customer communication','Student registration · courses · plans · payments · customer notifications · dashboard broadcasts');
   const username=esc(d.dashboard_user||'admin');
   document.getElementById('securityInfo').innerHTML='<div class="settings-row"><span>Dashboard username</span><b>'+username+'</b></div><div class="settings-row"><span>Password configured</span><span class="status '+(d.password_configured?'active-status':'danger-status')+'">'+(d.password_configured?'Yes':'No')+'</span></div><div class="settings-row"><span>Runtime mode</span><b>'+esc(d.mode||'false')+'</b></div><div class="muted" style="margin-top:10px">For security, the dashboard does not display or edit bot tokens. Change the dashboard password through your deployment environment variable <b>ADMIN_DASHBOARD_PASSWORD</b>.</div>';
 }catch(e){document.getElementById('settingsOverall').className='status danger-status';document.getElementById('settingsOverall').textContent='Error';document.getElementById('healthList').innerHTML='<div class="empty">'+esc(e.message)+'</div>';}
}
function exportDashboardData(){window.location.href='/dashboard/api/settings/export';}

function populateFilterOptions(){const fill=(id,items)=>{const el=document.getElementById(id);if(!el)return;const cur=el.value;el.innerHTML='<option value="">All courses</option>'+items.map(x=>'<option value="'+esc(x.id)+'">'+esc(x.name||'Unknown')+'</option>').join('');if([...el.options].some(o=>o.value===cur))el.value=cur;};fill('studentCourseFilter',coursesCache);fill('paymentCourseFilter',coursesCache);fill('planCourseFilter',coursesCache);}
function renderCourses(){const q=(document.getElementById('courseSearch')?.value||'').trim().toLowerCase(),st=document.getElementById('courseStatusFilter')?.value||'';const list=coursesCache.filter(x=>(!q||[x.name,x.description,x.slug].filter(Boolean).join(' ').toLowerCase().includes(q))&&(!st||(st==='active'?x.status==='active':x.status!=='active')));document.getElementById('coursesTable').innerHTML=table(['Course','Status','Plans','Telegram group','Created','Actions'],list.map(x=>'<tr><td><b>'+esc(x.name)+'</b><div class="muted">'+esc(x.description||'')+'</div><div class="muted">slug: '+esc(x.slug||'—')+'</div></td><td><span class="status '+statusClass(x.status)+'">'+esc(x.status||'unknown')+'</span></td><td>'+esc(x.plan_count)+'</td><td><span class="status '+(x.group_connected?'active-status':'')+'">'+esc(x.group_connected?(x.group_title||'Connected'):'Not connected')+'</span></td><td>'+esc(x.created_at||'—')+'</td><td><div class="row-actions"><button class="action-btn primary" onclick="openCourse(&quot;'+esc(x.id)+'&quot;)">Manage</button><button class="action-btn '+(x.status==='active'?'danger':'good')+'" onclick="toggleCourse(&quot;'+esc(x.id)+'&quot;)">'+(x.status==='active'?'Deactivate':'Activate')+'</button></div></td></tr>'));}
function renderPayments(){const q=(document.getElementById('paymentSearch')?.value||'').trim().toLowerCase(),st=document.getElementById('paymentStatusFilter')?.value||'',course=document.getElementById('paymentCourseFilter')?.value||'',from=document.getElementById('paymentFrom')?.value||'',to=document.getElementById('paymentTo')?.value||'';const list=paymentsCache.filter(x=>{const hay=[x.payment_number,x.customer_name,x.username,x.telegram_user_id,x.course_name,x.plan_name].filter(Boolean).join(' ').toLowerCase(),d=(x.submitted_at||'').slice(0,10);return(!q||hay.includes(q))&&(!st||x.status===st)&&(!course||String(x.course_id)===String(course))&&(!from||d>=from)&&(!to||d<=to);});document.getElementById('paymentsTable').innerHTML=table(['Payment','Customer','Course / Plan','Amount','Status','Submitted','Action'],list.map(x=>`<tr><td><b>#${esc(x.payment_number||x.id)}</b></td><td><b>${esc(x.customer_name||'Unknown')}</b><div class="muted">${esc(x.username?'@'+x.username:(x.telegram_user_id||''))}</div></td><td>${esc(x.course_name||'Unknown')}<div class="muted">${esc(x.plan_name||'')}</div></td><td>${esc((x.currency||'')+' '+(x.amount??''))}</td><td><span class="status ${statusClass(x.status)}">${esc(x.status)}</span></td><td>${esc(x.submitted_at||'—')}</td><td><button class="action-btn primary" onclick="openPayment('${esc(x.id)}')">Review</button></td></tr>`));}
function renderPlans(){const q=(document.getElementById('planSearch')?.value||'').trim().toLowerCase(),course=document.getElementById('planCourseFilter')?.value||'',st=document.getElementById('planStatusFilter')?.value||'';const list=plansCache.filter(x=>{const c=coursesCache.find(c=>String(c.id)===String(x.course_id))||{};return(!q||[x.name,c.name].filter(Boolean).join(' ').toLowerCase().includes(q))&&(!course||String(x.course_id)===String(course))&&(!st||(st==='active'?x.is_active:!x.is_active));});document.getElementById('plansTable').innerHTML=table(['Plan','Course','Price','Type','Duration','Status','Course control'],list.map(x=>{const c=coursesCache.find(c=>String(c.id)===String(x.course_id))||{};return '<tr><td><b>'+esc(x.name)+'</b></td><td>'+esc(c.name||'—')+'</td><td>'+esc((x.currency||'')+' '+(x.price??''))+'</td><td>'+esc(x.plan_type)+'</td><td>'+esc(x.duration_days??'Lifetime')+'</td><td><span class="status '+(x.is_active?'active-status':'')+'">'+esc(x.is_active?'Active':'Inactive')+'</span></td><td>'+(c.id?'<button class="action-btn" onclick="openCourse(&quot;'+esc(c.id)+'&quot;)">View Course</button>':'—')+'</td></tr>'; }));}
function renderActivity(){const q=(document.getElementById('activitySearch')?.value||'').trim().toLowerCase(),a=document.getElementById('activityActionFilter')?.value||'',from=document.getElementById('activityFrom')?.value||'',to=document.getElementById('activityTo')?.value||'';const list=activityCache.filter(x=>{const hay=[x.action,x.description,x.entity_type].filter(Boolean).join(' ').toLowerCase(),d=(x.created_at||'').slice(0,10);return(!q||hay.includes(q))&&(!a||x.action===a)&&(!from||d>=from)&&(!to||d<=to);});const el=document.getElementById('activityList');if(!list.length){el.innerHTML='<div class="empty">No matching activity.</div>';return;}el.innerHTML=list.map(x=>'<div class="activity-item"><div><div class="activity-action">'+esc(x.action||'Action')+'</div><div class="activity-time">'+esc(x.created_at||'—')+'</div></div><div class="activity-desc">'+esc(x.description||'')+'</div><span class="activity-pill">'+esc(x.entity_type||'system')+'</span></div>').join('');}
async function loadAnalytics(){
 try{const d=await api('/dashboard/api/analytics');const money=n=>esc(d.currency+' '+Number(n||0).toLocaleString('en-IN'));document.getElementById('analyticsCards').innerHTML=[['Customers',d.customers],['Active subscriptions',d.active_subscriptions],['Pending payments',d.pending_payments],['Approved payments',d.payment_counts.all_time]].map(x=>'<div class="mini-stat"><div class="label">'+esc(x[0])+'</div><div class="value">'+esc(x[1])+'</div></div>').join('');document.getElementById('revenueCards').innerHTML=[['Today',d.revenue.today,d.payment_counts.today],['This week',d.revenue.week,d.payment_counts.week],['This month',d.revenue.month,d.payment_counts.month],['All time',d.revenue.all_time,d.payment_counts.all_time]].map(x=>'<div class="mini-stat"><div class="label">'+esc(x[0])+'</div><div class="value">'+money(x[1])+'</div><div class="muted">'+esc(x[2])+' approved payments</div></div>').join('');}catch(e){const el=document.getElementById('analyticsCards');if(el)el.innerHTML='<div class="notice danger">Analytics unavailable: '+esc(e.message)+'</div>';}}
async function loadActivity(){try{activityCache=await api('/dashboard/api/activity');const sel=document.getElementById('activityActionFilter');if(sel){const cur=sel.value,acts=[...new Set(activityCache.map(x=>x.action).filter(Boolean))].sort();sel.innerHTML='<option value="">All actions</option>'+acts.map(x=>'<option value="'+esc(x)+'">'+esc(x.replaceAll('_',' '))+'</option>').join('');if(acts.includes(cur))sel.value=cur;}renderActivity();}catch(e){document.getElementById('activityList').innerHTML='<div class="notice danger">Activity log unavailable: '+esc(e.message)+'</div>';}}

let notificationsOpen=false;
async function loadNotifications(){try{const d=await api('/dashboard/api/notifications');const badge=document.getElementById('notificationBadge');badge.textContent=d.count;badge.style.display=d.count?'inline-grid':'none';const list=document.getElementById('notificationList');if(!d.notifications.length){list.innerHTML='<div class="empty">No alerts right now.</div>';return;}list.innerHTML=d.notifications.map(n=>'<div class="notification-item" onclick="notificationTarget(\''+esc(n.target)+'\')"><span class="notification-dot '+esc(n.level||'')+'"></span><div><div class="notification-title">'+esc(n.title)+'</div><div class="notification-detail">'+esc(n.detail)+'</div></div><span class="notification-count">'+esc(n.count)+'</span></div>').join('');}catch(e){const list=document.getElementById('notificationList');if(list)list.innerHTML='<div class="notice danger">Notifications unavailable: '+esc(e.message)+'</div>';}}
function toggleNotifications(){const p=document.getElementById('notificationPanel');notificationsOpen=!notificationsOpen;p.classList.toggle('open',notificationsOpen);if(notificationsOpen)loadNotifications();}
function notificationTarget(target){const map={payments:3,students:1,courses:2};const buttons=document.querySelectorAll('.nav button');showPanel(target,buttons[map[target]]||buttons[0]);document.getElementById('notificationPanel').classList.remove('open');notificationsOpen=false;}
document.addEventListener('click',e=>{const w=document.querySelector('.notification-wrap');if(w&&!w.contains(e.target)){document.getElementById('notificationPanel')?.classList.remove('open');notificationsOpen=false;}});

async function refreshAll(){
 try{
  const s=await api('/dashboard/api/overview');
  const money=(n)=>s.currency+' '+Number(n||0).toLocaleString('en-IN');
  document.getElementById('stats').innerHTML=[
   ['Customers',s.customers,'Registered users',''],['Active',s.active_subscriptions,'Live subscriptions','accent'],['Pending',s.pending_payments,'Needs review',''],['Revenue',money(s.total_revenue),'All approved','accent'],
   ['Lifetime',s.lifetime_active,'Protected access',''],['Expiring',s.expiring_7_days,'Next 7 days',''],['Expired',s.expired_subscriptions,'Inactive access',''],['This month',money(s.month_revenue),'Current month','accent']
  ].map(x=>'<div class="stat '+x[3]+'"><div class="stat-label">'+x[0]+'</div><div class="stat-value">'+esc(x[1])+'</div><div class="stat-meta">'+x[2]+'</div></div>').join('');
  document.getElementById('avgPayment').textContent=money(s.average_payment);document.getElementById('approvedPayments').textContent=s.approved_payments;document.getElementById('attention').textContent=Number(s.pending_payments)+Number(s.expiring_7_days);document.getElementById('lastUpdated').textContent='Updated '+new Date().toLocaleTimeString();
  const [students,courses,payments,plans,subscriptions]=await Promise.all([api('/dashboard/api/students'),api('/dashboard/api/courses'),api('/dashboard/api/payments'),api('/dashboard/api/plans'),api('/dashboard/api/subscriptions')]);
  studentsCache=students;coursesCache=courses;paymentsCache=payments;plansCache=plans;subscriptionsCache=subscriptions;populateFilterOptions();populateSubscriptionCourseFilter();renderStudents();renderSubscriptions();renderCourses();renderPayments();renderPlans();
  loadAnalytics();loadActivity();
 }catch(e){alert('Dashboard error: '+e.message)}
}
showPanel('overview',document.querySelector('.nav button'));refreshAll();loadNotifications();
</script>
</body></html>
'''
