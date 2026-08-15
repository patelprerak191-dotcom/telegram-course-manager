# ============================================================
# MERGED BUILD
# Original backup preserved:
# admin_bot_main_ADVANCED_ANALYTICS_COMPLETE(1).py
#
# Updated Broadcast module merged in.
# Existing Admin Dashboard, Customer Management, Payments,
# Courses, Plans, Grant/Revoke Access, Analytics, etc. retained.
# ============================================================

import asyncio
import json
import os
import re
import secrets
import string
from io import BytesIO
from datetime import datetime, timedelta, timezone

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    BufferedInputFile,
)

from dotenv import load_dotenv

from app.database.supabase_client import supabase


# ============================================================
# CONFIG
# ============================================================

load_dotenv()

TOKEN = os.getenv("ADMIN_BOT_TOKEN")
ADMIN_TELEGRAM_ID = os.getenv("ADMIN_TELEGRAM_ID")
CUSTOMER_BOT_TOKEN = os.getenv("CUSTOMER_BOT_TOKEN")

dp = Dispatcher()

# Temporary in-memory setup data
pending_group_connections = {}


# ============================================================
# STATES
# ============================================================

class AddCourseStates(StatesGroup):
    waiting_for_name = State()
    waiting_for_description = State()


class AddPlanStates(StatesGroup):
    waiting_for_name = State()
    waiting_for_price = State()
    waiting_for_type = State()
    waiting_for_duration = State()
    waiting_for_qr = State()


class BroadcastStates(StatesGroup):
    choosing_audience = State()
    choosing_course = State()
    waiting_for_message = State()
    waiting_for_confirmation = State()


# ============================================================
# SECURITY
# ============================================================

def is_admin(user_id: int) -> bool:
    if not ADMIN_TELEGRAM_ID:
        return False

    return str(user_id) == str(ADMIN_TELEGRAM_ID)


async def deny_access(
    callback: CallbackQuery | None = None,
    message: Message | None = None,
):
    if callback:
        await callback.answer(
            "🔒 Access denied",
            show_alert=True,
        )

    if message:
        await message.answer(
            "🔒 ACCESS DENIED\n\n"
            "This bot is restricted to authorized administrators."
        )


# ============================================================
# KEYBOARDS
# ============================================================

def admin_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="💳 Pending Payments",
                    callback_data="pending_payments",
                )
            ],
            [
                InlineKeyboardButton(
                    text="✅ Approved Payments",
                    callback_data="approved_payments",
                )
            ],
            [
                InlineKeyboardButton(
                    text="📚 Manage Courses",
                    callback_data="manage_courses",
                )
            ],
            [
                InlineKeyboardButton(
                    text="👥 Manage Users",
                    callback_data="manage_users",
                ),
                InlineKeyboardButton(
                    text="📊 Statistics",
                    callback_data="statistics",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="📢 Broadcast",
                    callback_data="broadcast",
                ),
                InlineKeyboardButton(
                    text="⚙️ Settings",
                    callback_data="settings",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🛡️ Audit Log",
                    callback_data="audit_logs",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🔔 Notifications",
                    callback_data="notifications",
                ),
            ],
        ]
    )


def back_to_admin_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🔙 Admin Panel",
                    callback_data="admin_panel",
                )
            ]
        ]
    )


def course_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="➕ Add Course",
                    callback_data="add_course",
                )
            ],
            [
                InlineKeyboardButton(
                    text="🔄 Refresh",
                    callback_data="manage_courses",
                )
            ],
            [
                InlineKeyboardButton(
                    text="🔙 Admin Panel",
                    callback_data="admin_panel",
                )
            ],
        ]
    )


# ============================================================
# HELPERS
# ============================================================

def create_course_slug(name: str) -> str:
    slug = name.lower().strip()

    slug = re.sub(
        r"[^a-z0-9\s-]",
        "",
        slug,
    )

    slug = re.sub(
        r"[\s_-]+",
        "-",
        slug,
    )

    return slug.strip("-")


def generate_connection_code() -> str:
    chars = string.ascii_uppercase + string.digits

    random_part = "".join(
        secrets.choice(chars)
        for _ in range(8)
    )

    return f"CONNECT-{random_part}"


def sanitize_storage_name(name: str) -> str:
    name = re.sub(
        r"[^a-zA-Z0-9._-]",
        "_",
        name,
    )

    return name[:100]


# ============================================================
# ADMIN AUDIT LOG
# ============================================================

async def write_audit_log(
    admin_telegram_id: int,
    action: str,
    result: str = "success",
    target_user_id=None,
    target_telegram_user_id=None,
    course_id=None,
    plan_id=None,
    details: dict | None = None,
):
    """Best-effort audit logging. Never breaks the main business flow."""
    try:
        payload = {
            "admin_telegram_id": int(admin_telegram_id),
            "action": str(action)[:120],
            "result": str(result)[:40],
            "target_user_id": target_user_id,
            "target_telegram_user_id": target_telegram_user_id,
            "course_id": course_id,
            "plan_id": plan_id,
            "details": details or {},
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        supabase.table("admin_audit_logs").insert(payload).execute()
    except Exception as error:
        print("AUDIT LOG WRITE FAILED:", repr(error))


def audit_logs_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🔄 Refresh",
                    callback_data="audit_logs",
                )
            ],
            [
                InlineKeyboardButton(
                    text="🔙 Admin Panel",
                    callback_data="admin_panel",
                )
            ],
        ]
    )


@dp.callback_query(F.data == "audit_logs")
async def audit_logs_handler(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await deny_access(callback=callback)
        return

    await safe_callback_answer(callback)

    try:
        response = (
            supabase
            .table("admin_audit_logs")
            .select(
                "id,admin_telegram_id,action,result,target_telegram_user_id,"
                "course_id,plan_id,details,created_at"
            )
            .order("created_at", desc=True)
            .limit(20)
            .execute()
        )

        rows = response.data or []
        if not rows:
            text_out = (
                "🛡️ ADMIN AUDIT LOG\n"
                "━━━━━━━━━━━━━━━━━━━━\n\n"
                "No admin activity has been logged yet."
            )
        else:
            chunks = [
                "🛡️ ADMIN AUDIT LOG",
                "━━━━━━━━━━━━━━━━━━━━",
                "",
                f"Showing latest {len(rows)} actions:",
                "",
            ]
            for row in rows:
                ts = row.get("created_at") or "-"
                try:
                    dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                    ts = dt.astimezone(timezone.utc).strftime("%d %b %Y %H:%M UTC")
                except Exception:
                    pass

                result = row.get("result", "success")
                icon = "✅" if result == "success" else "❌"
                target = row.get("target_telegram_user_id")
                target_text = f" | User: {target}" if target else ""
                details = row.get("details") or {}
                summary = ""
                if isinstance(details, dict):
                    for key in ("course_name", "plan_name", "payment_number", "audience", "sent", "failed"):
                        if details.get(key) is not None:
                            summary += f" | {key}: {details[key]}"

                chunks.append(
                    f"{icon} {row.get('action', 'UNKNOWN')}\n"
                    f"👤 Admin: {row.get('admin_telegram_id')}"
                    f"{target_text}\n"
                    f"🕐 {ts}{summary}\n"
                )

            text_out = "\n".join(chunks)

        await callback.message.edit_text(
            text_out[:4000],
            reply_markup=audit_logs_keyboard(),
        )
    except Exception as error:
        print("AUDIT LOG READ ERROR:", repr(error))
        await callback.message.edit_text(
            "❌ Could not load audit logs.\n\n"
            "Make sure the admin_audit_logs SQL has been run.",
            reply_markup=back_to_admin_menu(),
        )


# ============================================================
# START
# ============================================================

@dp.message(CommandStart())
async def start_handler(message: Message):
    if not is_admin(message.from_user.id):
        await deny_access(message=message)
        return

    await message.answer(
        "👑 ADMIN CONTROL CENTER\n\n"
        "Welcome, Admin.\n\n"
        "Choose an option:",
        reply_markup=admin_menu(),
    )


# ============================================================
# ADMIN PANEL
# ============================================================

@dp.callback_query(F.data == "admin_panel")
async def admin_panel_handler(
    callback: CallbackQuery,
):
    if not is_admin(callback.from_user.id):
        await deny_access(callback=callback)
        return

    await safe_callback_answer(callback)

    await callback.message.edit_text(
        "👑 ADMIN CONTROL CENTER\n\n"
        "Welcome, Admin.\n\n"
        "Choose an option:",
        reply_markup=admin_menu(),
    )


# ============================================================
# MANAGE COURSES
# ============================================================

@dp.callback_query(F.data == "manage_courses")
async def manage_courses_handler(
    callback: CallbackQuery,
):
    if not is_admin(callback.from_user.id):
        await deny_access(callback=callback)
        return

    await safe_callback_answer(callback)

    try:
        response = (
            supabase
            .table("courses")
            .select(
                "id, name, slug, description, status, "
                "sort_order, created_at"
            )
            .order("sort_order")
            .order("created_at")
            .execute()
        )

        courses = response.data or []

        if not courses:
            await callback.message.edit_text(
                "📚 MANAGE COURSES\n\n"
                "No courses have been created yet.\n\n"
                "Create your first course:",
                reply_markup=course_menu(),
            )
            return

        buttons = []

        for course in courses:
            status = course.get(
                "status",
                "active",
            )

            icon = "🟢" if status == "active" else "🔴"

            buttons.append(
                [
                    InlineKeyboardButton(
                        text=f"{icon} {course['name']}",
                        callback_data=f"course_{course['id']}",
                    )
                ]
            )

        buttons.append(
            [
                InlineKeyboardButton(
                    text="➕ Add Course",
                    callback_data="add_course",
                )
            ]
        )

        buttons.append(
            [
                InlineKeyboardButton(
                    text="🔙 Admin Panel",
                    callback_data="admin_panel",
                )
            ]
        )

        await callback.message.edit_text(
            "📚 MANAGE COURSES\n\n"
            f"Total Courses: {len(courses)}\n\n"
            "Select a course or create a new one:",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=buttons
            ),
        )

    except Exception as error:
        print("Manage courses error:", repr(error))

        await callback.message.edit_text(
            "❌ ERROR\n\n"
            "Could not load courses from Supabase.",
            reply_markup=back_to_admin_menu(),
        )


# ============================================================
# ADD COURSE
# ============================================================

@dp.callback_query(F.data == "add_course")
async def add_course_handler(
    callback: CallbackQuery,
    state: FSMContext,
):
    if not is_admin(callback.from_user.id):
        await deny_access(callback=callback)
        return

    await safe_callback_answer(callback)
    await state.clear()

    await state.set_state(
        AddCourseStates.waiting_for_name
    )

    await callback.message.edit_text(
        "➕ CREATE NEW COURSE\n\n"
        "STEP 1 OF 3\n\n"
        "Send the course name.\n\n"
        "Example:\n"
        "AI & Automation",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="❌ Cancel",
                        callback_data="cancel_add_course",
                    )
                ]
            ]
        ),
    )


@dp.message(AddCourseStates.waiting_for_name)
async def course_name_handler(
    message: Message,
    state: FSMContext,
):
    if not is_admin(message.from_user.id):
        await deny_access(message=message)
        return

    if not message.text:
        await message.answer(
            "⚠️ Please send the course name as text."
        )
        return

    name = message.text.strip()

    if len(name) < 2:
        await message.answer(
            "⚠️ Course name is too short."
        )
        return

    if len(name) > 100:
        await message.answer(
            "⚠️ Course name must be under 100 characters."
        )
        return

    await state.update_data(
        course_name=name
    )

    await state.set_state(
        AddCourseStates.waiting_for_description
    )

    await message.answer(
        "📝 COURSE DESCRIPTION\n\n"
        "STEP 2 OF 3\n\n"
        f"Course:\n{name}\n\n"
        "Send the course description.",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="❌ Cancel",
                        callback_data="cancel_add_course",
                    )
                ]
            ]
        ),
    )


@dp.message(AddCourseStates.waiting_for_description)
async def course_description_handler(
    message: Message,
    state: FSMContext,
):
    if not is_admin(message.from_user.id):
        await deny_access(message=message)
        return

    if not message.text:
        await message.answer(
            "⚠️ Please send the description as text."
        )
        return

    description = message.text.strip()

    if len(description) > 2000:
        await message.answer(
            "⚠️ Description is too long."
        )
        return

    data = await state.get_data()

    course_name = data["course_name"]

    code = generate_connection_code()

    pending_group_connections[
        message.from_user.id
    ] = {
        "course_name": course_name,
        "description": description,
        "connection_code": code,
    }

    await state.clear()

    await message.answer(
        "🔐 CONNECT PRIVATE GROUP\n\n"
        "STEP 3 OF 3\n\n"
        f"Course:\n{course_name}\n\n"
        "Your unique connection code:\n\n"
        f"🔑 {code}\n\n"
        "Open your private content group and send:\n\n"
        f"/connect {code}\n\n"
        "⚠️ The command must be sent INSIDE "
        "the private group.\n\n"
        "Admin Bot must be administrator of the group.",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="❌ Cancel",
                        callback_data="cancel_add_course",
                    )
                ]
            ]
        ),
    )


# ============================================================
# CONNECT PRIVATE GROUP
# ============================================================

@dp.message(Command("connect"))
async def connect_group_handler(
    message: Message,
):
    if message.chat.type not in {
        "group",
        "supergroup",
    }:
        await message.reply(
            "⚠️ This command must be used "
            "inside the private content group."
        )
        return

    if not is_admin(message.from_user.id):
        await message.reply(
            "🔒 Only the authorized administrator "
            "can connect a course group."
        )
        return

    parts = (
        message.text or ""
    ).split(maxsplit=1)

    if len(parts) != 2:
        await message.reply(
            "⚠️ Missing connection code.\n\n"
            "Example:\n"
            "/connect CONNECT-ABCDEFGH"
        )
        return

    code = parts[1].strip().upper()

    pending = pending_group_connections.get(
        message.from_user.id
    )

    if not pending:
        await message.reply(
            "❌ No active course setup found.\n\n"
            "Go to Admin Bot → Manage Courses → "
            "Add Course first."
        )
        return

    if code != pending["connection_code"]:
        await message.reply(
            "❌ Invalid connection code."
        )
        return

    try:
        bot = message.bot

        bot_member = await bot.get_chat_member(
            chat_id=message.chat.id,
            user_id=bot.id,
        )

        status = bot_member.status

        if status not in {
            "administrator",
            "creator",
        }:
            await message.reply(
                "❌ Admin Bot is not an administrator "
                "of this group."
            )
            return

        can_invite = bool(
            getattr(
                bot_member,
                "can_invite_users",
                False,
            )
        )

        can_manage = bool(
            getattr(
                bot_member,
                "can_restrict_members",
                False,
            )
        )

        if status == "creator":
            can_invite = True
            can_manage = True

        if not can_invite or not can_manage:
            await message.reply(
                "⚠️ GROUP PERMISSIONS INCOMPLETE\n\n"
                f"Invite Users: "
                f"{'🟢 YES' if can_invite else '🔴 NO'}\n"
                f"Manage Members: "
                f"{'🟢 YES' if can_manage else '🔴 NO'}\n\n"
                "Update the bot permissions and run "
                "/connect again."
            )
            return

        pending.update(
            {
                "telegram_chat_id": message.chat.id,
                "channel_title": (
                    message.chat.title
                    or "Private Group"
                ),
                "channel_username": getattr(
                    message.chat,
                    "username",
                    None,
                ),
                "bot_is_admin": True,
                "can_invite_users": True,
                "can_manage_members": True,
            }
        )

        await message.reply(
            "✅ GROUP VERIFIED!\n\n"
            f"🎓 Course:\n{pending['course_name']}\n\n"
            f"🔐 Group:\n{message.chat.title}\n\n"
            f"🆔 Group ID:\n{message.chat.id}\n\n"
            "Bot Status:\n"
            "🟢 Administrator\n\n"
            "Invite Users:\n"
            "🟢 YES\n\n"
            "Manage Members:\n"
            "🟢 YES\n\n"
            "Return to Admin Bot and press "
            "CONNECT GROUP."
        )

        await bot.send_message(
            chat_id=message.from_user.id,
            text=(
                "✅ GROUP VERIFIED\n\n"
                f"🎓 Course:\n{pending['course_name']}\n\n"
                f"🔐 Private Group:\n{message.chat.title}\n\n"
                f"🆔 Group ID:\n{message.chat.id}\n\n"
                "Everything is ready."
            ),
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="✅ CONNECT GROUP",
                            callback_data="confirm_course",
                        )
                    ],
                    [
                        InlineKeyboardButton(
                            text="❌ Cancel",
                            callback_data="cancel_add_course",
                        )
                    ],
                ]
            ),
        )

    except Exception as error:
        print("Group connection error:", repr(error))

        await message.reply(
            "❌ GROUP VERIFICATION FAILED\n\n"
            "Check Admin Bot permissions."
        )


# ============================================================
# CONFIRM COURSE
# ============================================================

@dp.callback_query(F.data == "confirm_course")
async def confirm_course_handler(
    callback: CallbackQuery,
):
    if not is_admin(callback.from_user.id):
        await deny_access(callback=callback)
        return

    await safe_callback_answer(callback)

    pending = pending_group_connections.get(
        callback.from_user.id
    )

    if not pending:
        await callback.message.edit_text(
            "❌ COURSE SETUP EXPIRED.\n\n"
            "Please start Add Course again.",
            reply_markup=course_menu(),
        )
        return

    try:
        base_slug = create_course_slug(
            pending["course_name"]
        )

        if not base_slug:
            base_slug = "course"

        slug = base_slug
        counter = 2

        while True:
            existing = (
                supabase
                .table("courses")
                .select("id")
                .eq("slug", slug)
                .limit(1)
                .execute()
            )

            if not existing.data:
                break

            slug = f"{base_slug}-{counter}"
            counter += 1

        course_response = (
            supabase
            .table("courses")
            .insert(
                {
                    "name": pending["course_name"],
                    "slug": slug,
                    "description": pending["description"],
                    "status": "active",
                    "sort_order": 0,
                }
            )
            .execute()
        )

        course = course_response.data[0]
        course_id = course["id"]

        group_response = (
            supabase
            .table("channels")
            .insert(
                {
                    "course_id": course_id,
                    "telegram_chat_id": pending[
                        "telegram_chat_id"
                    ],
                    "channel_username": pending[
                        "channel_username"
                    ],
                    "channel_title": pending[
                        "channel_title"
                    ],
                    "is_active": True,
                    "bot_is_admin": True,
                    "can_invite_users": True,
                    "can_manage_members": True,
                }
            )
            .execute()
        )

        if not group_response.data:
            (
                supabase
                .table("courses")
                .delete()
                .eq("id", course_id)
                .execute()
            )

            raise RuntimeError(
                "Group could not be connected."
            )

        pending_group_connections.pop(
            callback.from_user.id,
            None,
        )

        await write_audit_log(
            callback.from_user.id,
            "CREATE_COURSE",
            course_id=course_id,
            details={
                "course_name": pending["course_name"],
                "group_title": pending.get("channel_title"),
            },
        )

        await callback.message.edit_text(
            "🎉 COURSE CREATED SUCCESSFULLY!\n\n"
            f"🎓 Course:\n{pending['course_name']}\n\n"
            f"🔐 Content Group:\n{pending['channel_title']}\n\n"
            "Status:\n"
            "🟢 ACTIVE\n\n"
            "Bot Permissions:\n"
            "🟢 Invite Users\n"
            "🟢 Manage Members\n\n"
            "Saved in Supabase.",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="💳 Manage Plans",
                            callback_data=f"plans_{course_id}",
                        )
                    ],
                    [
                        InlineKeyboardButton(
                            text="📚 Manage Courses",
                            callback_data="manage_courses",
                        )
                    ],
                    [
                        InlineKeyboardButton(
                            text="👑 Admin Panel",
                            callback_data="admin_panel",
                        )
                    ],
                ]
            ),
        )

    except Exception as error:
        print("Create course error:", repr(error))

        await callback.message.edit_text(
            "❌ COURSE CREATION FAILED\n\n"
            "Check the terminal for the error.",
            reply_markup=course_menu(),
        )


# ============================================================
# COURSE DETAILS
# ============================================================

@dp.callback_query(F.data.startswith("course_"))
async def course_details_handler(
    callback: CallbackQuery,
):
    if not is_admin(callback.from_user.id):
        await deny_access(callback=callback)
        return

    await safe_callback_answer(callback)

    course_id = callback.data.replace(
        "course_",
        "",
        1,
    )

    try:
        response = (
            supabase
            .table("courses")
            .select(
                "id, name, description, status, slug"
            )
            .eq("id", course_id)
            .limit(1)
            .execute()
        )

        if not response.data:
            await callback.message.edit_text(
                "❌ Course not found.",
                reply_markup=course_menu(),
            )
            return

        course = response.data[0]

        group_response = (
            supabase
            .table("channels")
            .select(
                "channel_title, telegram_chat_id, "
                "is_active"
            )
            .eq("course_id", course_id)
            .limit(1)
            .execute()
        )

        group = (
            group_response.data[0]
            if group_response.data
            else None
        )

        group_name = (
            group["channel_title"]
            if group
            else "Not connected"
        )

        await callback.message.edit_text(
            f"🎓 {course['name']}\n\n"
            f"Status: {course['status']}\n\n"
            f"📝 Description:\n"
            f"{course.get('description') or 'None'}\n\n"
            f"🔐 Content Group:\n"
            f"{group_name}\n\n"
            "Choose an action:",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="💳 Manage Plans",
                            callback_data=f"plans_{course_id}",
                        )
                    ],
                    [
                        InlineKeyboardButton(
                            text="👥 Subscribers",
                            callback_data=(
                                f"subscribers_{course_id}"
                            ),
                        )
                    ],
                    [
                        InlineKeyboardButton(
                            text="🔙 Manage Courses",
                            callback_data="manage_courses",
                        )
                    ],
                ]
            ),
        )

    except Exception as error:
        print("Course details error:", repr(error))

        await callback.message.edit_text(
            "❌ Could not load course.",
            reply_markup=course_menu(),
        )


# ============================================================
# MANAGE PLANS
# ============================================================

@dp.callback_query(F.data.startswith("plans_"))
async def plans_handler(
    callback: CallbackQuery,
):
    if not is_admin(callback.from_user.id):
        await deny_access(callback=callback)
        return

    await safe_callback_answer(callback)

    course_id = callback.data.replace(
        "plans_",
        "",
        1,
    )

    try:
        course_response = (
            supabase
            .table("courses")
            .select("id, name")
            .eq("id", course_id)
            .limit(1)
            .execute()
        )

        if not course_response.data:
            await callback.message.edit_text(
                "❌ Course not found.",
                reply_markup=course_menu(),
            )
            return

        course = course_response.data[0]

        plans_response = (
            supabase
            .table("plans")
            .select(
                "id, name, plan_type, price, "
                "currency, duration_days, is_active"
            )
            .eq("course_id", course_id)
            .order("sort_order")
            .order("created_at")
            .execute()
        )

        plans = plans_response.data or []

        buttons = []

        for plan in plans:
            status_icon = (
                "🟢"
                if plan["is_active"]
                else "🔴"
            )

            if plan["plan_type"] == "lifetime":
                duration = "Lifetime"
            else:
                duration = (
                    f"{plan['duration_days']} days"
                )

            buttons.append(
                [
                    InlineKeyboardButton(
                        text=(
                            f"{status_icon} "
                            f"{plan['name']} — "
                            f"₹{plan['price']} "
                            f"({duration})"
                        ),
                        callback_data=(
                            f"plan_{plan['id']}"
                        ),
                    )
                ]
            )

        buttons.append(
            [
                InlineKeyboardButton(
                    text="➕ Add Plan",
                    callback_data=f"add_plan_{course_id}",
                )
            ]
        )

        buttons.append(
            [
                InlineKeyboardButton(
                    text="🔙 Course",
                    callback_data=f"course_{course_id}",
                )
            ]
        )

        await callback.message.edit_text(
            "💳 MANAGE PLANS\n\n"
            f"🎓 Course:\n{course['name']}\n\n"
            f"Total Plans: {len(plans)}\n\n"
            "Select a plan or add a new one:",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=buttons
            ),
        )

    except Exception as error:
        print("Manage plans error:", repr(error))

        await callback.message.edit_text(
            "❌ Could not load plans.",
            reply_markup=back_to_admin_menu(),
        )


# ============================================================
# ADD PLAN
# ============================================================

@dp.callback_query(F.data.startswith("add_plan_"))
async def add_plan_handler(
    callback: CallbackQuery,
    state: FSMContext,
):
    if not is_admin(callback.from_user.id):
        await deny_access(callback=callback)
        return

    await safe_callback_answer(callback)

    course_id = callback.data.replace(
        "add_plan_",
        "",
        1,
    )

    await state.clear()

    await state.update_data(
        course_id=course_id
    )

    await state.set_state(
        AddPlanStates.waiting_for_name
    )

    await callback.message.edit_text(
        "➕ CREATE NEW PLAN\n\n"
        "STEP 1 OF 5\n\n"
        "Enter the plan name.\n\n"
        "Example:\n"
        "Lifetime",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="❌ Cancel",
                        callback_data="cancel_plan",
                    )
                ]
            ]
        ),
    )


# ============================================================
# PLAN NAME
# ============================================================

@dp.message(AddPlanStates.waiting_for_name)
async def plan_name_handler(
    message: Message,
    state: FSMContext,
):
    if not is_admin(message.from_user.id):
        await deny_access(message=message)
        return

    if not message.text:
        await message.answer(
            "⚠️ Please send the plan name."
        )
        return

    name = message.text.strip()

    if len(name) > 100:
        await message.answer(
            "⚠️ Plan name must be under 100 characters."
        )
        return

    await state.update_data(
        plan_name=name
    )

    await state.set_state(
        AddPlanStates.waiting_for_price
    )

    await message.answer(
        "💰 PLAN PRICE\n\n"
        "STEP 2 OF 5\n\n"
        "Enter price in INR.\n\n"
        "Example:\n"
        "99",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="❌ Cancel",
                        callback_data="cancel_plan",
                    )
                ]
            ]
        ),
    )


# ============================================================
# PLAN PRICE
# ============================================================

@dp.message(AddPlanStates.waiting_for_price)
async def plan_price_handler(
    message: Message,
    state: FSMContext,
):
    if not is_admin(message.from_user.id):
        await deny_access(message=message)
        return

    if not message.text:
        await message.answer(
            "⚠️ Enter a valid price."
        )
        return

    try:
        price = float(
            message.text.strip()
        )
    except ValueError:
        await message.answer(
            "⚠️ Invalid price.\n\n"
            "Example:\n"
            "99"
        )
        return

    if price <= 0:
        await message.answer(
            "⚠️ Price must be greater than ₹0."
        )
        return

    await state.update_data(
        price=price
    )

    await state.set_state(
        AddPlanStates.waiting_for_type
    )

    await message.answer(
        "📅 PLAN TYPE\n\n"
        "STEP 3 OF 5\n\n"
        "Choose subscription type:",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="♾️ Lifetime",
                        callback_data="plan_type_lifetime",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="📅 Fixed Duration",
                        callback_data="plan_type_fixed",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="❌ Cancel",
                        callback_data="cancel_plan",
                    )
                ],
            ]
        ),
    )


# ============================================================
# LIFETIME
# ============================================================

@dp.callback_query(F.data == "plan_type_lifetime")
async def plan_type_lifetime_handler(
    callback: CallbackQuery,
    state: FSMContext,
):
    if not is_admin(callback.from_user.id):
        await deny_access(callback=callback)
        return

    await safe_callback_answer(callback)

    await state.update_data(
        plan_type="lifetime",
        duration_days=None,
    )

    await state.set_state(
        AddPlanStates.waiting_for_qr
    )

    await callback.message.edit_text(
        "📸 PAYMENT QR CODE\n\n"
        "STEP 4 OF 5\n\n"
        "Plan Type:\n"
        "♾️ Lifetime\n\n"
        "Send the payment QR as a photo.",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="❌ Cancel",
                        callback_data="cancel_plan",
                    )
                ]
            ]
        ),
    )


# ============================================================
# FIXED PLAN
# ============================================================

@dp.callback_query(F.data == "plan_type_fixed")
async def plan_type_fixed_handler(
    callback: CallbackQuery,
    state: FSMContext,
):
    if not is_admin(callback.from_user.id):
        await deny_access(callback=callback)
        return

    await safe_callback_answer(callback)

    await state.update_data(
        plan_type="fixed"
    )

    await state.set_state(
        AddPlanStates.waiting_for_duration
    )

    await callback.message.edit_text(
        "📅 PLAN DURATION\n\n"
        "STEP 4 OF 5\n\n"
        "Enter duration in days.\n\n"
        "Example:\n"
        "30",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="❌ Cancel",
                        callback_data="cancel_plan",
                    )
                ]
            ]
        ),
    )


# ============================================================
# FIXED DURATION
# ============================================================

@dp.message(AddPlanStates.waiting_for_duration)
async def plan_duration_handler(
    message: Message,
    state: FSMContext,
):
    if not is_admin(message.from_user.id):
        await deny_access(message=message)
        return

    if not message.text:
        await message.answer(
            "⚠️ Enter duration in days."
        )
        return

    try:
        duration = int(
            message.text.strip()
        )
    except ValueError:
        await message.answer(
            "⚠️ Invalid duration.\n\n"
            "Example:\n"
            "30"
        )
        return

    if duration <= 0:
        await message.answer(
            "⚠️ Duration must be greater than 0."
        )
        return

    await state.update_data(
        duration_days=duration
    )

    await state.set_state(
        AddPlanStates.waiting_for_qr
    )

    await message.answer(
        "📸 PAYMENT QR CODE\n\n"
        "STEP 5 OF 5\n\n"
        f"Duration:\n{duration} days\n\n"
        "Send the payment QR as a photo.",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="❌ Cancel",
                        callback_data="cancel_plan",
                    )
                ]
            ]
        ),
    )


# ============================================================
# QR UPLOAD
# ============================================================

@dp.message(AddPlanStates.waiting_for_qr)
async def plan_qr_handler(
    message: Message,
    state: FSMContext,
):
    if not is_admin(message.from_user.id):
        await deny_access(message=message)
        return

    if not message.photo:
        await message.answer(
            "⚠️ Please send the QR code as a photo."
        )
        return

    try:
        data = await state.get_data()

        course_id = data.get("course_id")
        plan_name = data.get("plan_name")
        price = data.get("price")
        plan_type = data.get("plan_type")
        duration_days = data.get("duration_days")

        if not course_id:
            await message.answer(
                "❌ Course information is missing."
            )
            await state.clear()
            return

        photo = message.photo[-1]

        telegram_file = await message.bot.get_file(
            photo.file_id
        )

        buffer = BytesIO()

        await message.bot.download_file(
            telegram_file.file_path,
            destination=buffer,
        )

        file_bytes = buffer.getvalue()

        if not file_bytes:
            raise RuntimeError(
                "QR file is empty."
            )

        filename = (
            f"{secrets.token_hex(8)}_"
            f"{sanitize_storage_name(plan_name)}.jpg"
        )

        storage_path = (
            f"{course_id}/{filename}"
        )

        supabase.storage.from_(
            "payment-qr"
        ).upload(
            storage_path,
            file_bytes,
            {
                "content-type": "image/jpeg",
                "upsert": "false",
            },
        )

        await state.update_data(
            qr_code_path=storage_path
        )

        duration_text = (
            "Lifetime"
            if plan_type == "lifetime"
            else f"{duration_days} days"
        )

        await message.answer(
            "✅ QR UPLOADED\n\n"
            "PLAN PREVIEW\n\n"
            f"💎 Name:\n{plan_name}\n\n"
            f"💰 Price:\n₹{price:.2f}\n\n"
            f"📅 Duration:\n{duration_text}\n\n"
            "📲 QR Code:\n"
            "🟢 Stored securely\n\n"
            "Save this plan?",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="✅ SAVE PLAN",
                            callback_data="save_plan",
                        )
                    ],
                    [
                        InlineKeyboardButton(
                            text="❌ Cancel",
                            callback_data="cancel_plan",
                        )
                    ],
                ]
            ),
        )

    except Exception as error:
        print("QR upload error:", repr(error))

        await message.answer(
            "❌ QR UPLOAD FAILED\n\n"
            "Could not upload the QR to Supabase Storage.\n\n"
            "Check the terminal error."
        )


# ============================================================
# SAVE PLAN
# ============================================================

@dp.callback_query(F.data == "save_plan")
async def save_plan_handler(
    callback: CallbackQuery,
    state: FSMContext,
):
    if not is_admin(callback.from_user.id):
        await deny_access(callback=callback)
        return

    await safe_callback_answer(callback)

    data = await state.get_data()

    course_id = data.get("course_id")
    plan_name = data.get("plan_name")
    price = data.get("price")
    plan_type = data.get("plan_type")
    duration_days = data.get("duration_days")
    qr_code_path = data.get("qr_code_path")

    if not all(
        [
            course_id,
            plan_name,
            price,
            plan_type,
            qr_code_path,
        ]
    ):
        await state.clear()

        await callback.message.edit_text(
            "❌ PLAN DATA INCOMPLETE\n\n"
            "Please create the plan again.",
            reply_markup=back_to_admin_menu(),
        )
        return

    try:
        duplicate = (
            supabase
            .table("plans")
            .select("id")
            .eq("course_id", course_id)
            .eq("name", plan_name)
            .limit(1)
            .execute()
        )

        if duplicate.data:
            await state.clear()

            await callback.message.edit_text(
                "⚠️ PLAN ALREADY EXISTS\n\n"
                f"'{plan_name}' already exists.",
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            InlineKeyboardButton(
                                text="💳 Manage Plans",
                                callback_data=(
                                    f"plans_{course_id}"
                                ),
                            )
                        ]
                    ]
                ),
            )
            return

        existing = (
            supabase
            .table("plans")
            .select("id")
            .eq("course_id", course_id)
            .execute()
        )

        sort_order = len(
            existing.data or []
        )

        response = (
            supabase
            .table("plans")
            .insert(
                {
                    "course_id": course_id,
                    "name": plan_name,
                    "plan_type": plan_type,
                    "price": price,
                    "currency": "INR",
                    "duration_days": (
                        duration_days
                        if plan_type == "fixed"
                        else None
                    ),
                    "description": (
                        f"{plan_name} subscription"
                    ),
                    "qr_code_path": qr_code_path,
                    "is_active": True,
                    "sort_order": sort_order,
                }
            )
            .execute()
        )

        if not response.data:
            raise RuntimeError(
                "Plan was not saved."
            )

        await state.clear()

        duration_text = (
            "Lifetime"
            if plan_type == "lifetime"
            else f"{duration_days} days"
        )

        await callback.message.edit_text(
            "🎉 PLAN CREATED SUCCESSFULLY!\n\n"
            f"💎 Plan:\n{plan_name}\n\n"
            f"💰 Price:\n₹{price:.2f}\n\n"
            f"📅 Duration:\n{duration_text}\n\n"
            "📲 QR Code:\n"
            "🟢 Stored in Supabase Storage\n\n"
            "Status:\n"
            "🟢 ACTIVE",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="💳 Manage Plans",
                            callback_data=(
                                f"plans_{course_id}"
                            ),
                        )
                    ],
                    [
                        InlineKeyboardButton(
                            text="📚 Manage Courses",
                            callback_data="manage_courses",
                        )
                    ],
                ]
            ),
        )

    except Exception as error:
        print("Save plan error:", repr(error))

        await callback.message.edit_text(
            "❌ PLAN CREATION FAILED\n\n"
            "Check the terminal for the exact error.",
            reply_markup=back_to_admin_menu(),
        )


# ============================================================
# PLAN DETAILS
# ============================================================

@dp.callback_query(F.data.startswith("plan_"))
async def plan_details_handler(
    callback: CallbackQuery,
):
    if not is_admin(callback.from_user.id):
        await deny_access(callback=callback)
        return

    await safe_callback_answer(callback)

    plan_id = callback.data.replace(
        "plan_",
        "",
        1,
    )

    try:
        response = (
            supabase
            .table("plans")
            .select(
                "id, course_id, name, plan_type, "
                "price, currency, duration_days, "
                "description, qr_code_path, is_active"
            )
            .eq("id", plan_id)
            .limit(1)
            .execute()
        )

        if not response.data:
            await callback.message.edit_text(
                "❌ Plan not found.",
                reply_markup=back_to_admin_menu(),
            )
            return

        plan = response.data[0]

        duration = (
            "Lifetime"
            if plan["plan_type"] == "lifetime"
            else f"{plan['duration_days']} days"
        )

        status = (
            "🟢 ACTIVE"
            if plan["is_active"]
            else "🔴 INACTIVE"
        )

        qr_status = (
            "🟢 Uploaded"
            if plan.get("qr_code_path")
            else "🔴 Missing"
        )

        await callback.message.edit_text(
            "💳 PLAN DETAILS\n\n"
            f"💎 Name:\n{plan['name']}\n\n"
            f"💰 Price:\n₹{plan['price']}\n\n"
            f"📅 Duration:\n{duration}\n\n"
            f"📲 QR:\n{qr_status}\n\n"
            f"Status:\n{status}",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text=(
                                "🔴 Deactivate"
                                if plan["is_active"]
                                else "🟢 Activate"
                            ),
                            callback_data=(
                                f"toggle_plan_{plan_id}"
                            ),
                        )
                    ],
                    [
                        InlineKeyboardButton(
                            text="🗑 Delete Plan",
                            callback_data=(
                                f"delete_plan_{plan_id}"
                            ),
                        )
                    ],
                    [
                        InlineKeyboardButton(
                            text="🔙 Plans",
                            callback_data=(
                                f"plans_{plan['course_id']}"
                            ),
                        )
                    ],
                ]
            ),
        )

    except Exception as error:
        print("Plan details error:", repr(error))

        await callback.message.edit_text(
            "❌ Could not load plan.",
            reply_markup=back_to_admin_menu(),
        )


# ============================================================
# TOGGLE PLAN
# ============================================================

@dp.callback_query(F.data.startswith("toggle_plan_"))
async def toggle_plan_handler(
    callback: CallbackQuery,
):
    if not is_admin(callback.from_user.id):
        await deny_access(callback=callback)
        return

    await safe_callback_answer(callback)

    plan_id = callback.data.replace(
        "toggle_plan_",
        "",
        1,
    )

    try:
        response = (
            supabase
            .table("plans")
            .select(
                "id, course_id, is_active"
            )
            .eq("id", plan_id)
            .limit(1)
            .execute()
        )

        if not response.data:
            await callback.message.edit_text(
                "❌ Plan not found.",
                reply_markup=back_to_admin_menu(),
            )
            return

        plan = response.data[0]

        new_status = not plan["is_active"]

        (
            supabase
            .table("plans")
            .update(
                {
                    "is_active": new_status
                }
            )
            .eq("id", plan_id)
            .execute()
        )

        await callback.message.edit_text(
            (
                "🟢 PLAN ACTIVATED"
                if new_status
                else "🔴 PLAN DEACTIVATED"
            ),
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="💳 Back to Plans",
                            callback_data=(
                                f"plans_{plan['course_id']}"
                            ),
                        )
                    ]
                ]
            ),
        )

    except Exception as error:
        print("Toggle plan error:", repr(error))

        await callback.message.edit_text(
            "❌ Could not update plan.",
            reply_markup=back_to_admin_menu(),
        )


# ============================================================
# DELETE PLAN
# ============================================================

@dp.callback_query(F.data.startswith("delete_plan_"))
async def delete_plan_handler(
    callback: CallbackQuery,
):
    if not is_admin(callback.from_user.id):
        await deny_access(callback=callback)
        return

    await safe_callback_answer(callback)

    plan_id = callback.data.replace(
        "delete_plan_",
        "",
        1,
    )

    try:
        response = (
            supabase
            .table("plans")
            .select(
                "id, course_id, name, qr_code_path"
            )
            .eq("id", plan_id)
            .limit(1)
            .execute()
        )

        if not response.data:
            await callback.message.edit_text(
                "❌ Plan not found.",
                reply_markup=back_to_admin_menu(),
            )
            return

        plan = response.data[0]

        await callback.message.edit_text(
            "⚠️ DELETE PLAN?\n\n"
            f"Plan:\n{plan['name']}\n\n"
            "This cannot be undone.",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="⚠️ YES, DELETE",
                            callback_data=(
                                f"confirm_delete_plan_{plan_id}"
                            ),
                        )
                    ],
                    [
                        InlineKeyboardButton(
                            text="❌ Cancel",
                            callback_data=(
                                f"plan_{plan_id}"
                            ),
                        )
                    ],
                ]
            ),
        )

    except Exception as error:
        print(
            "Delete confirmation error:",
            repr(error),
        )

        await callback.message.edit_text(
            "❌ Could not process request.",
            reply_markup=back_to_admin_menu(),
        )


@dp.callback_query(
    F.data.startswith("confirm_delete_plan_")
)
async def confirm_delete_plan_handler(
    callback: CallbackQuery,
):
    if not is_admin(callback.from_user.id):
        await deny_access(callback=callback)
        return

    await safe_callback_answer(callback)

    plan_id = callback.data.replace(
        "confirm_delete_plan_",
        "",
        1,
    )

    try:
        response = (
            supabase
            .table("plans")
            .select(
                "id, course_id, qr_code_path"
            )
            .eq("id", plan_id)
            .limit(1)
            .execute()
        )

        if not response.data:
            await callback.message.edit_text(
                "❌ Plan not found.",
                reply_markup=back_to_admin_menu(),
            )
            return

        plan = response.data[0]
        course_id = plan["course_id"]

        (
            supabase
            .table("plans")
            .delete()
            .eq("id", plan_id)
            .execute()
        )

        qr_path = plan.get(
            "qr_code_path"
        )

        if qr_path:
            try:
                (
                    supabase
                    .storage
                    .from_("payment-qr")
                    .remove([qr_path])
                )
            except Exception as storage_error:
                print(
                    "QR deletion warning:",
                    repr(storage_error),
                )

        await callback.message.edit_text(
            "🗑 PLAN DELETED SUCCESSFULLY.",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="💳 Manage Plans",
                            callback_data=(
                                f"plans_{course_id}"
                            ),
                        )
                    ]
                ]
            ),
        )

    except Exception as error:
        print("Delete plan error:", repr(error))

        await callback.message.edit_text(
            "❌ Could not delete plan.",
            reply_markup=back_to_admin_menu(),
        )


# ============================================================
# CANCEL PLAN
# ============================================================

@dp.callback_query(F.data == "cancel_plan")
async def cancel_plan_handler(
    callback: CallbackQuery,
    state: FSMContext,
):
    if not is_admin(callback.from_user.id):
        await deny_access(callback=callback)
        return

    await safe_callback_answer(callback)

    data = await state.get_data()

    course_id = data.get(
        "course_id"
    )

    await state.clear()

    if course_id:
        await callback.message.edit_text(
            "💳 MANAGE PLANS\n\n"
            "Plan creation cancelled.",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="💳 Manage Plans",
                            callback_data=(
                                f"plans_{course_id}"
                            ),
                        )
                    ],
                    [
                        InlineKeyboardButton(
                            text="👑 Admin Panel",
                            callback_data="admin_panel",
                        )
                    ],
                ]
            ),
        )
    else:
        await callback.message.edit_text(
            "👑 ADMIN CONTROL CENTER",
            reply_markup=admin_menu(),
        )


# ============================================================
# CANCEL COURSE
# ============================================================

@dp.callback_query(F.data == "cancel_add_course")
async def cancel_add_course_handler(
    callback: CallbackQuery,
    state: FSMContext,
):
    if not is_admin(callback.from_user.id):
        await deny_access(callback=callback)
        return

    await safe_callback_answer(callback)

    await state.clear()

    pending_group_connections.pop(
        callback.from_user.id,
        None,
    )

    await callback.message.edit_text(
        "📚 MANAGE COURSES\n\n"
        "Course creation cancelled.",
        reply_markup=course_menu(),
    )


# ============================================================
# PENDING PAYMENTS
# ============================================================

def pending_payments_keyboard(payments) -> InlineKeyboardMarkup:
    buttons = []

    for payment in payments:
        payment_number = payment.get("payment_number")
        amount = payment.get("amount")
        buttons.append(
            [
                InlineKeyboardButton(
                    text=f"#{payment_number} • ₹{amount}",
                    callback_data=f"payment_{payment['id']}",
                )
            ]
        )

    buttons.append(
        [
            InlineKeyboardButton(
                text="🔄 Refresh",
                callback_data="pending_payments",
            )
        ]
    )

    buttons.append(
        [
            InlineKeyboardButton(
                text="🔙 Admin Panel",
                callback_data="admin_panel",
            )
        ]
    )

    return InlineKeyboardMarkup(inline_keyboard=buttons)


def payment_action_keyboard(
    payment_id: str,
    status: str = "pending",
    has_access: bool = False,
) -> InlineKeyboardMarkup:

    buttons = []

    if status == "pending":
        buttons.append([
            InlineKeyboardButton(
                text="✅ APPROVE",
                callback_data=f"approve_payment_{payment_id}",
            ),
            InlineKeyboardButton(
                text="❌ REJECT",
                callback_data=f"reject_payment_{payment_id}",
            ),
        ])
    elif status == "approved" and not has_access:
        buttons.append([
            InlineKeyboardButton(
                text="🔐 GRANT COURSE ACCESS",
                callback_data=f"grant_access_{payment_id}",
            )
        ])

    back_callback = (
        "approved_payments"
        if status == "approved"
        else "pending_payments"
    )
    back_text = (
        "🔙 Approved Payments"
        if status == "approved"
        else "🔙 Pending Payments"
    )

    buttons.append([
        InlineKeyboardButton(
            text=back_text,
            callback_data=back_callback,
        )
    ])

    return InlineKeyboardMarkup(inline_keyboard=buttons)


@dp.callback_query(F.data == "pending_payments")
async def pending_payments_handler(
    callback: CallbackQuery,
):
    if not is_admin(callback.from_user.id):
        await deny_access(callback=callback)
        return

    await safe_callback_answer(callback)

    try:
        response = (
            supabase
            .table("payment_requests")
            .select(
                "id, payment_number, amount, currency, "
                "status, submitted_at"
            )
            .eq("status", "pending")
            .order(
                "submitted_at",
                desc=True,
            )
            .limit(20)
            .execute()
        )

        payments = response.data or []

        if not payments:
            await callback.message.edit_text(
                "💳 PENDING PAYMENTS\n\n"
                "🟢 No pending payment requests.",
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            InlineKeyboardButton(
                                text="🔄 Refresh",
                                callback_data="pending_payments",
                            )
                        ],
                        [
                            InlineKeyboardButton(
                                text="🔙 Admin Panel",
                                callback_data="admin_panel",
                            )
                        ],
                    ]
                ),
            )
            return

        lines = [
            "💳 PENDING PAYMENTS",
            "",
            f"🔴 Pending: {len(payments)}",
            "",
            "Select a payment to review:",
        ]

        await callback.message.edit_text(
            "\n".join(lines),
            reply_markup=pending_payments_keyboard(payments),
        )

    except Exception as error:
        print(
            "Pending payments error:",
            repr(error),
        )

        await callback.message.edit_text(
            "❌ Could not load pending payments.",
            reply_markup=back_to_admin_menu(),
        )


# ============================================================
# APPROVED PAYMENTS
# ============================================================

def approved_payments_keyboard(payments) -> InlineKeyboardMarkup:
    buttons = []

    for payment in payments:
        payment_number = payment.get("payment_number")
        amount = payment.get("amount")
        access_state = payment.get("access_state", "pending")

        if access_state == "granted":
            prefix = "🟢"
        else:
            prefix = "🔐"

        buttons.append([
            InlineKeyboardButton(
                text=f"{prefix} #{payment_number} • ₹{amount}",
                callback_data=f"payment_{payment['id']}",
            )
        ])

    buttons.append([
        InlineKeyboardButton(
            text="🔄 Refresh",
            callback_data="approved_payments",
        )
    ])

    buttons.append([
        InlineKeyboardButton(
            text="🔙 Admin Panel",
            callback_data="admin_panel",
        )
    ])

    return InlineKeyboardMarkup(inline_keyboard=buttons)


@dp.callback_query(F.data == "approved_payments")
async def approved_payments_handler(
    callback: CallbackQuery,
):
    if not is_admin(callback.from_user.id):
        await deny_access(callback=callback)
        return

    await safe_callback_answer(callback)

    try:
        response = (
            supabase
            .table("payment_requests")
            .select(
                "id, payment_number, user_id, course_id, plan_id, "
                "amount, currency, status, submitted_at, reviewed_at"
            )
            .eq("status", "approved")
            .order("reviewed_at", desc=True)
            .limit(30)
            .execute()
        )

        payments = response.data or []

        if not payments:
            await callback.message.edit_text(
                "✅ APPROVED PAYMENTS\n\n"
                "No approved payments found.",
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            InlineKeyboardButton(
                                text="🔄 Refresh",
                                callback_data="approved_payments",
                            )
                        ],
                        [
                            InlineKeyboardButton(
                                text="🔙 Admin Panel",
                                callback_data="admin_panel",
                            )
                        ],
                    ]
                ),
            )
            return

        enriched = []
        granted_count = 0
        pending_access_count = 0

        for payment in payments:
            sub_response = (
                supabase
                .table("subscriptions")
                .select("id, status")
                .eq("payment_request_id", payment["id"])
                .limit(1)
                .execute()
            )

            if sub_response.data:
                payment["access_state"] = "granted"
                granted_count += 1
            else:
                payment["access_state"] = "pending"
                pending_access_count += 1

            enriched.append(payment)

        lines = [
            "✅ APPROVED PAYMENTS",
            "",
            f"Total: {len(enriched)}",
            f"🟢 Access granted: {granted_count}",
            f"🔐 Access pending: {pending_access_count}",
            "",
            "Select a payment:",
        ]

        await callback.message.edit_text(
            "\n".join(lines),
            reply_markup=approved_payments_keyboard(enriched),
        )

    except Exception as error:
        print(
            "Approved payments error:",
            repr(error),
        )

        await callback.message.edit_text(
            "❌ Could not load approved payments.",
            reply_markup=back_to_admin_menu(),
        )


# ============================================================
# PAYMENT DETAILS
# ============================================================

@dp.callback_query(F.data.startswith("payment_"))
async def payment_details_handler(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await deny_access(callback=callback)
        return

    await safe_callback_answer(callback)
    payment_id = callback.data.replace("payment_", "", 1)

    try:
        response = (
            supabase.table("payment_requests")
            .select(
                "id, payment_number, user_id, course_id, plan_id, "
                "amount, currency, status, screenshot_path, "
                "screenshot_file_id, submitted_at, reviewed_at, "
                "rejection_reason, admin_note"
            )
            .eq("id", payment_id).limit(1).execute()
        )
        if not response.data:
            await callback.message.edit_text(
                "❌ Payment request not found.",
                reply_markup=back_to_admin_menu(),
            )
            return

        payment = response.data[0]
        user_response = (supabase.table("users")
            .select("telegram_user_id, username, first_name, last_name")
            .eq("id", payment["user_id"]).limit(1).execute())
        course_response = (supabase.table("courses")
            .select("name").eq("id", payment["course_id"]).limit(1).execute())
        plan_response = (supabase.table("plans")
            .select("name, plan_type, price, duration_days")
            .eq("id", payment["plan_id"]).limit(1).execute())

        user = user_response.data[0] if user_response.data else {}
        course = course_response.data[0] if course_response.data else {}
        plan = plan_response.data[0] if plan_response.data else {}

        sub_response = (supabase.table("subscriptions")
            .select("id, status, is_lifetime, expires_at")
            .eq("payment_request_id", payment_id).limit(1).execute())
        has_access = bool(sub_response.data)
        subscription = sub_response.data[0] if sub_response.data else None

        username = user.get("username")
        username_text = f"@{username}" if username else "Not set"
        user_name = " ".join(
            p for p in [user.get("first_name"), user.get("last_name")] if p
        ) or "Unknown"
        duration = "Lifetime" if plan.get("plan_type") == "lifetime" else f"{plan.get('duration_days')} days"

        text = (
            "💳 PAYMENT REVIEW\n\n"
            f"Payment #: {payment.get('payment_number')}\n"
            f"Amount: ₹{payment.get('amount')} {payment.get('currency') or 'INR'}\n"
            f"Status: {payment.get('status')}\n\n"
            "👤 CUSTOMER\n"
            f"Name: {user_name}\n"
            f"Username: {username_text}\n"
            f"Telegram ID: {user.get('telegram_user_id', 'Unknown')}\n\n"
            "📚 COURSE\n"
            f"{course.get('name', 'Unknown')}\n\n"
            "💳 PLAN\n"
            f"{plan.get('name', 'Unknown')}\n"
            f"Duration: {duration}\n\n"
            f"Submitted: {payment.get('submitted_at')}\n"
        )
        if subscription:
            text += (
                "\n🔐 ACCESS\n"
                f"Subscription: {subscription.get('status')}\n"
                f"Lifetime: {subscription.get('is_lifetime')}\n"
                f"Expires: {subscription.get('expires_at') or 'Never'}\n"
            )

        screenshot_path = payment.get("screenshot_path")
        if screenshot_path:
            try:
                print("Downloading payment screenshot:", screenshot_path)
                screenshot_bytes = supabase.storage.from_("payment-qr").download(screenshot_path)
                if screenshot_bytes:
                    await callback.message.answer_photo(
                        photo=BufferedInputFile(screenshot_bytes, filename="payment_screenshot.jpg"),
                        caption=f"📸 Payment #{payment.get('payment_number')} screenshot",
                    )
            except Exception as screenshot_error:
                print("Payment screenshot download error:", repr(screenshot_error))
                text += "\n⚠️ Screenshot could not be loaded."

        if payment.get("status") == "pending":
            text += "\n\nChoose an action:"
        elif payment.get("status") == "approved" and not has_access:
            text += "\n\n⚠️ Payment approved, but course access is not provisioned."
        elif has_access:
            text += "\n\n✅ Course access is already provisioned."

        await callback.message.edit_text(
            text,
            reply_markup=payment_action_keyboard(
                payment_id, payment.get("status", "pending"), has_access
            ),
        )
    except Exception as error:
        print("Payment details error:", repr(error))
        await callback.message.edit_text(
            "❌ Could not load payment details.",
            reply_markup=back_to_admin_menu(),
        )


# ============================================================
# APPROVE PAYMENT
# ============================================================

def get_active_subscription_for_user_course(user_id: str, course_id: str):
    response = (
        supabase
        .table("subscriptions")
        .select(
            "id, status, plan_id, started_at, expires_at, is_lifetime"
        )
        .eq("user_id", user_id)
        .eq("course_id", course_id)
        .eq("status", "active")
        .order("created_at", desc=True)
        .limit(1)
        .execute()
    )
    return response.data[0] if response.data else None


async def send_customer_access_message(
    user: dict, course: dict, plan: dict, invite_link: str
):
    customer_token = os.getenv("CUSTOMER_BOT_TOKEN")
    if not customer_token:
        raise RuntimeError("CUSTOMER_BOT_TOKEN is missing in .env")

    customer_bot = Bot(token=customer_token)
    try:
        duration = (
            "Lifetime" if plan.get("plan_type") == "lifetime"
            else f"{plan.get('duration_days')} days"
        )
        await customer_bot.send_message(
            chat_id=int(user["telegram_user_id"]),
            text=(
                "🎉 PAYMENT APPROVED!\n\n"
                f"🎓 Course:\n{course['name']}\n\n"
                f"💳 Plan:\n{plan['name']}\n\n"
                f"📅 Access:\n{duration}\n\n"
                "🔐 Your course access is ready.\n\n"
                "Tap below to join the private course group."
            ),
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="🚀 JOIN COURSE", url=invite_link)
            ]]),
        )
    finally:
        await customer_bot.session.close()


async def send_customer_renewal_message(
    user: dict,
    course: dict,
    plan: dict,
    invite_link: str | None,
    new_expires_at,
):
    customer_token = os.getenv("CUSTOMER_BOT_TOKEN")
    if not customer_token:
        raise RuntimeError("CUSTOMER_BOT_TOKEN is missing in .env")

    customer_bot = Bot(token=customer_token)
    try:
        duration = (
            "Lifetime"
            if plan.get("plan_type") == "lifetime"
            else f"{plan.get('duration_days')} days"
        )

        text = (
            "🎉 COURSE RENEWAL APPROVED!\n\n"
            f"🎓 Course:\n{course['name']}\n\n"
            f"💳 Plan:\n{plan['name']}\n\n"
            f"📅 Renewal period:\n{duration}\n\n"
            f"⏳ New expiry:\n{new_expires_at or 'Never'}\n\n"
            "✅ Your course access has been renewed."
        )

        if invite_link:
            text += "\n\nUse the button below if you need to join the private course group."

            await customer_bot.send_message(
                chat_id=int(user["telegram_user_id"]),
                text=text,
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                    InlineKeyboardButton(
                        text="🚀 JOIN COURSE",
                        url=invite_link,
                    )
                ]]),
            )
        else:
            await customer_bot.send_message(
                chat_id=int(user["telegram_user_id"]),
                text=text,
            )
    finally:
        await customer_bot.session.close()


async def provision_course_access(admin_bot: Bot, payment: dict) -> dict:
    """
    Provision an approved payment.

    Renewal behavior:
    - Active fixed subscription for the same course -> extend the SAME row.
    - Expired/revoked/cancelled subscription -> create a new active row.
    - Active lifetime subscription -> block the payment.
    - Existing payment_request_id -> idempotently reuse the existing result.
    """

    payment_id = payment["id"]

    user_response = (
        supabase.table("users")
        .select("id, telegram_user_id, username, first_name, last_name")
        .eq("id", payment["user_id"])
        .limit(1)
        .execute()
    )
    course_response = (
        supabase.table("courses")
        .select("id, name")
        .eq("id", payment["course_id"])
        .limit(1)
        .execute()
    )
    plan_response = (
        supabase.table("plans")
        .select("id, name, plan_type, duration_days")
        .eq("id", payment["plan_id"])
        .limit(1)
        .execute()
    )

    if not user_response.data:
        raise RuntimeError("User record not found.")
    if not course_response.data:
        raise RuntimeError("Course record not found.")
    if not plan_response.data:
        raise RuntimeError("Plan record not found.")

    user = user_response.data[0]
    course = course_response.data[0]
    plan = plan_response.data[0]

    # Idempotency: if this exact payment has already been provisioned,
    # do not extend access a second time.
    existing_payment = (
        supabase.table("subscriptions")
        .select("*")
        .eq("payment_request_id", payment_id)
        .limit(1)
        .execute()
    )

    if existing_payment.data:
        subscription = existing_payment.data[0]
        return {
            "subscription": subscription,
            "invite": None,
            "course": course,
            "plan": plan,
            "renewed": False,
            "already_provisioned": True,
        }

    active_subscription = get_active_subscription_for_user_course(
        payment["user_id"],
        payment["course_id"],
    )

    if active_subscription and active_subscription.get("is_lifetime"):
        raise RuntimeError(
            "Customer already has an active lifetime subscription for this course. "
            "Do not provision another subscription."
        )

    from datetime import datetime, timezone, timedelta

    now = datetime.now(timezone.utc)
    is_lifetime = plan.get("plan_type") == "lifetime"

    if is_lifetime:
        # Lifetime purchase replaces fixed active access with permanent access
        # on the same subscription row.
        if active_subscription:
            updated = (
                supabase.table("subscriptions")
                .update({
                    "plan_id": payment["plan_id"],
                    "payment_request_id": payment_id,
                    "status": "active",
                    "started_at": active_subscription.get("started_at") or now.isoformat(),
                    "expires_at": None,
                    "is_lifetime": True,
                    "revoked_at": None,
                })
                .eq("id", active_subscription["id"])
                .eq("status", "active")
                .execute()
            )
            if not updated.data:
                raise RuntimeError("Could not convert active access to lifetime.")
            subscription = updated.data[0]
            return {
                "subscription": subscription,
                "invite": None,
                "course": course,
                "plan": plan,
                "renewed": True,
                "already_provisioned": False,
            }

        expires_at = None
    else:
        duration_days = plan.get("duration_days")
        if not duration_days:
            raise RuntimeError(
                "duration_days is missing for this non-lifetime plan."
            )

        if active_subscription:
            old_expires_raw = active_subscription.get("expires_at")

            if not old_expires_raw:
                base = now
            else:
                old_expires = datetime.fromisoformat(
                    str(old_expires_raw).replace("Z", "+00:00")
                )
                if old_expires.tzinfo is None:
                    old_expires = old_expires.replace(tzinfo=timezone.utc)
                base = max(old_expires, now)

            expires_at = (
                base + timedelta(days=int(duration_days))
            ).isoformat()

            updated = (
                supabase.table("subscriptions")
                .update({
                    "plan_id": payment["plan_id"],
                    "payment_request_id": payment_id,
                    "status": "active",
                    "expires_at": expires_at,
                    "is_lifetime": False,
                    "revoked_at": None,
                })
                .eq("id", active_subscription["id"])
                .eq("status", "active")
                .execute()
            )

            if not updated.data:
                raise RuntimeError(
                    "Renewal could not extend the existing subscription."
                )

            subscription = updated.data[0]

            # Active renewal keeps the same subscription row but receives a
            # fresh one-time invite, so a customer who has not joined can join.
            channel_response = (
                supabase.table("channels")
                .select(
                    "id, telegram_chat_id, channel_title, is_active, "
                    "bot_is_admin, can_invite_users"
                )
                .eq("course_id", payment["course_id"])
                .eq("is_active", True)
                .limit(1)
                .execute()
            )

            invite = None
            if channel_response.data:
                channel = channel_response.data[0]

                if channel.get("bot_is_admin") and channel.get("can_invite_users"):
                    invite_kwargs = {
                        "chat_id": channel["telegram_chat_id"],
                        "name": f"Renewal {payment.get('payment_number')}",
                        "member_limit": 1,
                        "expire_date": int(
                            (
                                now + timedelta(days=int(duration_days))
                            ).timestamp()
                        ),
                    }

                    telegram_invite = await admin_bot.create_chat_invite_link(
                        **invite_kwargs
                    )

                    invite_response = (
                        supabase.table("invite_links")
                        .insert({
                            "subscription_id": subscription["id"],
                            "channel_id": channel["id"],
                            "telegram_invite_link": telegram_invite.invite_link,
                            "status": "created",
                            "expires_at": expires_at,
                        })
                        .execute()
                    )

                    if invite_response.data:
                        invite = invite_response.data[0]

                        supabase.table("invite_links").update({
                            "status": "sent",
                            "sent_at": now.isoformat(),
                        }).eq("id", invite["id"]).execute()

            await send_customer_renewal_message(
                user,
                course,
                plan,
                invite["telegram_invite_link"] if invite else None,
                expires_at,
            )

            return {
                "subscription": subscription,
                "invite": invite,
                "course": course,
                "plan": plan,
                "renewed": True,
                "already_provisioned": False,
            }

        # No active subscription: this is a new purchase or a renewal after
        # an old subscription has already expired/revoked.
        started_at = now
        expires_at = (
            started_at + timedelta(days=int(duration_days))
        ).isoformat()

    # Create a new subscription only when there is no active subscription.
    sub_response = (
        supabase.table("subscriptions")
        .insert({
            "user_id": payment["user_id"],
            "course_id": payment["course_id"],
            "plan_id": payment["plan_id"],
            "payment_request_id": payment_id,
            "status": "active",
            "started_at": now.isoformat(),
            "expires_at": expires_at,
            "is_lifetime": is_lifetime,
        })
        .execute()
    )

    if not sub_response.data:
        raise RuntimeError("Subscription was not created.")

    subscription = sub_response.data[0]

    channel_response = (
        supabase.table("channels")
        .select(
            "id, telegram_chat_id, channel_title, is_active, "
            "bot_is_admin, can_invite_users"
        )
        .eq("course_id", payment["course_id"])
        .eq("is_active", True)
        .limit(1)
        .execute()
    )

    if not channel_response.data:
        raise RuntimeError(
            "No active Telegram channel is configured for this course."
        )

    channel = channel_response.data[0]

    if not channel.get("bot_is_admin"):
        raise RuntimeError(
            "Admin Bot is not marked as administrator for this channel."
        )

    if not channel.get("can_invite_users"):
        raise RuntimeError(
            "Admin Bot does not have invite permission for this channel."
        )

    invite_kwargs = {
        "chat_id": channel["telegram_chat_id"],
        "name": f"Payment {payment.get('payment_number')}",
        "member_limit": 1,
    }

    if expires_at:
        invite_kwargs["expire_date"] = int(
            (
                now + timedelta(days=int(plan["duration_days"]))
            ).timestamp()
        )

    telegram_invite = await admin_bot.create_chat_invite_link(
        **invite_kwargs
    )

    invite_response = (
        supabase.table("invite_links")
        .insert({
            "subscription_id": subscription["id"],
            "channel_id": channel["id"],
            "telegram_invite_link": telegram_invite.invite_link,
            "status": "created",
            "expires_at": expires_at,
        })
        .execute()
    )

    if not invite_response.data:
        raise RuntimeError("Invite link record was not created.")

    invite = invite_response.data[0]

    await send_customer_access_message(
        user,
        course,
        plan,
        telegram_invite.invite_link,
    )

    supabase.table("invite_links").update({
        "status": "sent",
        "sent_at": datetime.now(timezone.utc).isoformat(),
    }).eq("id", invite["id"]).execute()

    return {
        "subscription": subscription,
        "invite": invite,
        "course": course,
        "plan": plan,
        "renewed": False,
        "already_provisioned": False,
    }


@dp.callback_query(F.data.startswith("approve_payment_"))
async def approve_payment_handler(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await deny_access(callback=callback)
        return
    await safe_callback_answer(callback)
    payment_id = callback.data.replace("approve_payment_", "", 1)

    try:
        response = (supabase.table("payment_requests")
            .select("id, payment_number, user_id, course_id, plan_id, amount, currency, status")
            .eq("id", payment_id).limit(1).execute())
        if not response.data:
            raise RuntimeError("Payment request not found.")
        payment = response.data[0]
        if payment.get("status") != "pending":
            raise RuntimeError(f"Payment is not pending. Current status: {payment.get('status')}")

        # Approval guard: do not even change payment status when the customer
        # already owns lifetime access for this course.
        active_subscription = get_active_subscription_for_user_course(
            payment["user_id"],
            payment["course_id"],
        )
        if active_subscription and active_subscription.get("is_lifetime"):
            await callback.message.edit_text(
                "♾️ LIFETIME ACCESS ALREADY EXISTS\n\n"
                f"Payment #: {payment.get('payment_number')}\n"
                f"Amount: ₹{payment.get('amount')}\n\n"
                "This customer already has permanent access to this course.\n\n"
                "The payment was NOT approved and no new subscription was created.\n\n"
                "If money was actually received, handle the refund according to your payment policy and then reject this payment.",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="❌ REJECT PAYMENT", callback_data=f"reject_payment_{payment_id}")],
                    [InlineKeyboardButton(text="🔙 Pending Payments", callback_data="pending_payments")],
                ]),
            )
            return

        supabase.table("payment_requests").update({
            "status": "approved",
            "reviewed_at": datetime.now(timezone.utc).isoformat(),
        }).eq("id", payment_id).execute()

        result = await provision_course_access(callback.bot, payment)

        await write_audit_log(
            callback.from_user.id,
            "GRANT_COURSE_ACCESS",
            target_user_id=payment.get("user_id"),
            course_id=payment.get("course_id"),
            plan_id=payment.get("plan_id"),
            details={"payment_number": payment.get("payment_number")},
        )

        result_label = (
            "🔄 SUBSCRIPTION RENEWED"
            if result.get("renewed")
            else "🆕 SUBSCRIPTION CREATED"
        )

        await write_audit_log(
            callback.from_user.id,
            "APPROVE_PAYMENT",
            target_user_id=payment.get("user_id"),
            course_id=payment.get("course_id"),
            plan_id=payment.get("plan_id"),
            details={
                "payment_number": payment.get("payment_number"),
                "amount": payment.get("amount"),
                "renewed": bool(result.get("renewed")),
            },
        )

        await create_admin_notification(
            "PAYMENT_APPROVED",
            "Payment Approved",
            f"Payment #{payment.get('payment_number')} approved. Amount: ₹{payment.get('amount')}",
            severity="success",
            metadata={"payment_id": payment.get("id"), "user_id": payment.get("user_id")},
        )

        await callback.message.edit_text(
            "✅ PAYMENT APPROVED\n\n"
            f"Payment #: {payment.get('payment_number')}\n"
            f"Amount: ₹{payment.get('amount')}\n\n"
            f"🎓 Course:\n{result['course']['name']}\n\n"
            f"💳 Plan:\n{result['plan']['name']}\n\n"
            f"📝 {result_label}\n"
            "🔐 Customer has been notified.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="💳 Pending Payments", callback_data="pending_payments")],
                [InlineKeyboardButton(text="🔙 Admin Panel", callback_data="admin_panel")],
            ]),
        )
    except Exception as error:
        print("Approve/provision payment error:", repr(error))
        await callback.message.edit_text(
            "⚠️ PAYMENT APPROVED, BUT ACCESS SETUP FAILED\n\n"
            f"{error}\n\n"
            "Open this payment again and use 🔐 GRANT COURSE ACCESS after fixing the issue.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔐 GRANT COURSE ACCESS", callback_data=f"grant_access_{payment_id}")],
                [InlineKeyboardButton(text="🔙 Pending Payments", callback_data="pending_payments")],
            ]),
        )


@dp.callback_query(F.data.startswith("grant_access_"))
async def grant_access_handler(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await deny_access(callback=callback)
        return
    await safe_callback_answer(callback)
    payment_id = callback.data.replace("grant_access_", "", 1)

    try:
        response = (supabase.table("payment_requests")
            .select("id, payment_number, user_id, course_id, plan_id, amount, currency, status")
            .eq("id", payment_id).limit(1).execute())
        if not response.data:
            raise RuntimeError("Payment request not found.")
        payment = response.data[0]
        if payment.get("status") != "approved":
            raise RuntimeError(f"Payment must be approved first. Current status: {payment.get('status')}")

        active_subscription = get_active_subscription_for_user_course(
            payment["user_id"],
            payment["course_id"],
        )
        if active_subscription and active_subscription.get("is_lifetime"):
            await callback.message.edit_text(
                "♾️ LIFETIME ACCESS ALREADY EXISTS\n\n"
                f"Payment #: {payment.get('payment_number')}\n\n"
                "No additional subscription will be created.\n"
                "If this payment was received, handle the refund and reject the payment.",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="❌ REJECT PAYMENT", callback_data=f"reject_payment_{payment_id}")],
                    [InlineKeyboardButton(text="🔙 Pending Payments", callback_data="pending_payments")],
                ]),
            )
            return

        result = await provision_course_access(callback.bot, payment)
        await callback.message.edit_text(
            "🔐 COURSE ACCESS GRANTED\n\n"
            f"Payment #: {payment.get('payment_number')}\n\n"
            f"🎓 Course:\n{result['course']['name']}\n\n"
            "📝 Subscription: ACTIVE\n"
            "📩 Customer access message: SENT",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔙 Pending Payments", callback_data="pending_payments")],
                [InlineKeyboardButton(text="🔙 Admin Panel", callback_data="admin_panel")],
            ]),
        )
    except Exception as error:
        print("Grant access error:", repr(error))
        await callback.message.edit_text(
            "❌ COURSE ACCESS COULD NOT BE GRANTED\n\n"
            f"{error}",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔄 Try Again", callback_data=f"grant_access_{payment_id}")],
                [InlineKeyboardButton(text="🔙 Pending Payments", callback_data="pending_payments")],
            ]),
        )


# ============================================================
# REJECT PAYMENT
# ============================================================

@dp.callback_query(F.data.startswith("reject_payment_"))
async def reject_payment_handler(
    callback: CallbackQuery,
):
    if not is_admin(callback.from_user.id):
        await deny_access(callback=callback)
        return

    await safe_callback_answer(callback)

    payment_id = callback.data.replace(
        "reject_payment_",
        "",
        1,
    )

    try:
        response = (
            supabase
            .table("payment_requests")
            .select(
                "id, payment_number, amount, status"
            )
            .eq("id", payment_id)
            .limit(1)
            .execute()
        )

        if not response.data:
            await callback.message.edit_text(
                "❌ Payment request not found.",
                reply_markup=back_to_admin_menu(),
            )
            return

        payment = response.data[0]

        if payment.get("status") != "pending":
            await callback.message.edit_text(
                "⚠️ This payment has already been reviewed.",
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            InlineKeyboardButton(
                                text="🔙 Pending Payments",
                                callback_data="pending_payments",
                            )
                        ]
                    ]
                ),
            )
            return

        supabase.table("payment_requests").update(
            {
                "status": "rejected",
                "reviewed_at": "now()",
                "reviewed_by": None,
            }
        ).eq(
            "id",
            payment_id,
        ).execute()

        await create_admin_notification(
            "PAYMENT_REJECTED",
            "Payment Rejected",
            f"Payment #{payment.get('payment_number')} rejected. Amount: ₹{payment.get('amount')}",
            severity="warning",
            metadata={"payment_id": payment.get("id"), "user_id": payment.get("user_id")},
        )

        await callback.message.edit_text(
            "❌ PAYMENT REJECTED\n\n"
            f"Payment #: {payment.get('payment_number')}\n"
            f"Amount: ₹{payment.get('amount')}\n\n"
            "Payment status has been updated to rejected.",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="💳 Pending Payments",
                            callback_data="pending_payments",
                        )
                    ],
                    [
                        InlineKeyboardButton(
                            text="🔙 Admin Panel",
                            callback_data="admin_panel",
                        )
                    ],
                ]
            ),
        )

    except Exception as error:
        print(
            "Reject payment error:",
            repr(error),
        )

        await callback.message.edit_text(
            "❌ Could not reject payment.\n\n"
            "Check the terminal for the exact error.",
            reply_markup=back_to_admin_menu(),
        )


# ============================================================
# USERS
# ============================================================

def customer_management_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🟢 Active Customers", callback_data="customers_active")],
        [InlineKeyboardButton(text="⏰ Expiring Soon", callback_data="customers_expiring")],
        [InlineKeyboardButton(text="🔴 Expired Customers", callback_data="customers_expired")],
        [InlineKeyboardButton(text="♾️ Lifetime Customers", callback_data="customers_lifetime")],
        [InlineKeyboardButton(text="🔄 Refresh", callback_data="manage_users")],
        [InlineKeyboardButton(text="🔙 Admin Panel", callback_data="admin_panel")],
    ])


def customer_list_keyboard(rows) -> InlineKeyboardMarkup:
    buttons=[]
    for row in rows:
        name=" ".join(p for p in [row.get("first_name"), row.get("last_name")] if p) or "Unknown"
        buttons.append([InlineKeyboardButton(text=f"👤 {name} • {row.get('telegram_user_id')}", callback_data=f"customer_{row['user_id']}")])
    buttons.append([InlineKeyboardButton(text="🔙 Customer Management", callback_data="manage_users")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_customer_rows(mode: str):
    from datetime import datetime, timezone, timedelta
    q=(supabase.table("subscriptions").select("id,user_id,course_id,plan_id,status,started_at,expires_at,is_lifetime,joined_channel_at,revoked_at").order("started_at",desc=True).limit(100))
    if mode=="active": q=q.eq("status","active").eq("is_lifetime",False)
    elif mode=="expired": q=q.eq("status","expired")
    elif mode=="lifetime": q=q.eq("status","active").eq("is_lifetime",True)
    elif mode=="expiring": q=q.eq("status","active").eq("is_lifetime",False)
    subs=q.execute().data or []
    now=datetime.now(timezone.utc); out=[]
    for s in subs:
        if mode=="expiring":
            raw=s.get("expires_at")
            if not raw: continue
            try:
                ex=datetime.fromisoformat(str(raw).replace("Z","+00:00"))
                if ex.tzinfo is None: ex=ex.replace(tzinfo=timezone.utc)
            except ValueError: continue
            if not now < ex <= now+timedelta(days=7): continue
        u=(supabase.table("users").select("id,telegram_user_id,username,first_name,last_name").eq("id",s["user_id"]).limit(1).execute()).data
        c=(supabase.table("courses").select("name").eq("id",s["course_id"]).limit(1).execute()).data
        pl=(supabase.table("plans").select("name,plan_type").eq("id",s["plan_id"]).limit(1).execute()).data
        user=u[0] if u else {}; course=c[0] if c else {}; plan=pl[0] if pl else {}
        out.append({**s,"telegram_user_id":user.get("telegram_user_id"),"username":user.get("username"),"first_name":user.get("first_name"),"last_name":user.get("last_name"),"course_name":course.get("name"),"plan_name":plan.get("name"),"plan_type":plan.get("plan_type")})
    return out


@dp.callback_query(F.data == "manage_users")
async def manage_users_handler(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await deny_access(callback=callback); return
    await safe_callback_answer(callback)
    try:
        await callback.message.edit_text("👥 CUSTOMER MANAGEMENT\n\nChoose a customer category:", reply_markup=customer_management_keyboard())
    except Exception as error:
        print("CUSTOMER MANAGEMENT ERROR:", repr(error))


async def show_customer_list(callback: CallbackQuery, mode: str, title: str):
    if not is_admin(callback.from_user.id):
        await deny_access(callback=callback); return
    await safe_callback_answer(callback)
    try:
        rows=get_customer_rows(mode)
        if not rows:
            await callback.message.edit_text(f"{title}\n\nNo customers found.", reply_markup=customer_management_keyboard()); return
        await callback.message.edit_text(f"{title}\n\nFound: {len(rows)}\n\nSelect a customer:", reply_markup=customer_list_keyboard(rows))
    except Exception as error:
        print(f"CUSTOMER LIST ERROR ({mode}):", repr(error))
        await callback.message.edit_text("❌ Could not load customers.", reply_markup=customer_management_keyboard())


@dp.callback_query(F.data == "customers_active")
async def customers_active_handler(callback: CallbackQuery):
    await show_customer_list(callback,"active","🟢 ACTIVE CUSTOMERS")

@dp.callback_query(F.data == "customers_expiring")
async def customers_expiring_handler(callback: CallbackQuery):
    await show_customer_list(callback,"expiring","⏰ EXPIRING WITHIN 7 DAYS")

@dp.callback_query(F.data == "customers_expired")
async def customers_expired_handler(callback: CallbackQuery):
    await show_customer_list(callback,"expired","🔴 EXPIRED CUSTOMERS")

@dp.callback_query(F.data == "customers_lifetime")
async def customers_lifetime_handler(callback: CallbackQuery):
    await show_customer_list(callback,"lifetime","♾️ LIFETIME CUSTOMERS")


@dp.callback_query(F.data.startswith("customer_"))
async def customer_details_handler(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await deny_access(callback=callback); return
    await safe_callback_answer(callback)
    user_id=callback.data.replace("customer_","",1)
    try:
        u=(supabase.table("users").select("id,telegram_user_id,username,first_name,last_name").eq("id",user_id).limit(1).execute()).data
        if not u: raise RuntimeError("Customer not found.")
        user=u[0]
        subs=(supabase.table("subscriptions").select("id,course_id,plan_id,status,started_at,expires_at,is_lifetime,joined_channel_at,revoked_at").eq("user_id",user_id).order("started_at",desc=True).limit(20).execute()).data or []
        name=" ".join(p for p in [user.get("first_name"),user.get("last_name")] if p) or "Unknown"
        lines=["👤 CUSTOMER DETAILS","",f"Name: {name}",f"Username: @{user.get('username')}" if user.get("username") else "Username: Not set",f"Telegram ID: {user.get('telegram_user_id')}","","🎓 SUBSCRIPTIONS"]
        for s in subs:
            c=(supabase.table("courses").select("name").eq("id",s["course_id"]).limit(1).execute()).data
            pl=(supabase.table("plans").select("name").eq("id",s["plan_id"]).limit(1).execute()).data
            cn=c[0]["name"] if c else "Unknown course"; pn=pl[0]["name"] if pl else "Unknown plan"
            icon={"active":"🟢","expired":"🔴","cancelled":"⚪","revoked":"🚫","pending":"🟡"}.get(s.get("status"),"⚪")
            lines += ["",f"{icon} {cn}",f"Plan: {pn}",f"Status: {s.get('status')}",f"Lifetime: {s.get('is_lifetime')}",f"Started: {s.get('started_at')}",f"Expires: {s.get('expires_at') or 'Never'}",f"Telegram Joined: {'✅' if s.get('joined_channel_at') else '❌'}"]
        await callback.message.edit_text(
            "\n".join(lines),
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(
                        text="🔐 Grant Access",
                        callback_data=f"grant_customer_{user_id}",
                    )],
                    [InlineKeyboardButton(
                        text="🚫 Revoke Access",
                        callback_data=f"revoke_customer_{user_id}",
                    )],
                    [InlineKeyboardButton(
                        text="🔄 Extend Access",
                        callback_data=f"extend_customer_{user_id}",
                    )],
                    [InlineKeyboardButton(
                        text="💳 Payment History",
                        callback_data=f"customer_payments_{user_id}",
                    )],
                    [InlineKeyboardButton(
                        text="🔙 Customer Management",
                        callback_data="manage_users",
                    )],
                ]
            ),
        )
    except Exception as error:
        print("CUSTOMER DETAILS ERROR:",repr(error))
        await callback.message.edit_text("❌ Could not load customer details.",reply_markup=customer_management_keyboard())


# ============================================================
# EXTEND CUSTOMER ACCESS
# ============================================================

def extend_duration_keyboard(user_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="+7 Days", callback_data=f"extend_days_{user_id}_7"),
                InlineKeyboardButton(text="+30 Days", callback_data=f"extend_days_{user_id}_30"),
            ],
            [
                InlineKeyboardButton(text="+90 Days", callback_data=f"extend_days_{user_id}_90"),
            ],
            [
                InlineKeyboardButton(text="🔙 Customer", callback_data=f"customer_{user_id}"),
            ],
        ]
    )


@dp.callback_query(F.data.startswith("extend_customer_"))
async def extend_customer_handler(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await deny_access(callback=callback)
        return

    await safe_callback_answer(callback)
    user_id = callback.data.replace("extend_customer_", "", 1)

    try:
        response = (
            supabase.table("subscriptions")
            .select("id,status,expires_at,is_lifetime")
            .eq("user_id", user_id)
            .eq("status", "active")
            .order("started_at", desc=True)
            .limit(20)
            .execute()
        )
        subscriptions = response.data or []

        if not subscriptions:
            await callback.message.edit_text(
                "❌ No active subscription found.",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                    InlineKeyboardButton(text="🔙 Customer", callback_data=f"customer_{user_id}")
                ]]),
            )
            return

        if any(s.get("is_lifetime") for s in subscriptions):
            await callback.message.edit_text(
                "♾️ LIFETIME ACCESS\n\n"
                "This customer already has lifetime access.\n"
                "Extend Access is not applicable.",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                    InlineKeyboardButton(text="🔙 Customer", callback_data=f"customer_{user_id}")
                ]]),
            )
            return

        await callback.message.edit_text(
            "🔄 EXTEND CUSTOMER ACCESS\n\n"
            "Select additional access duration:",
            reply_markup=extend_duration_keyboard(user_id),
        )

    except Exception as error:
        print("EXTEND ACCESS MENU ERROR:", repr(error))
        await callback.message.edit_text(
            "❌ Could not load customer access.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="🔙 Customer", callback_data=f"customer_{user_id}")
            ]]),
        )


@dp.callback_query(F.data.startswith("extend_days_"))
async def extend_days_handler(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await deny_access(callback=callback)
        return

    await safe_callback_answer(callback)
    payload = callback.data.replace("extend_days_", "", 1)
    user_id, days_text = payload.rsplit("_", 1)

    try:
        days = int(days_text)
        if days not in (7, 30, 90):
            raise ValueError("Unsupported extension duration.")

        response = (
            supabase.table("subscriptions")
            .select("id,status,expires_at,is_lifetime")
            .eq("user_id", user_id)
            .eq("status", "active")
            .order("started_at", desc=True)
            .limit(20)
            .execute()
        )
        subscriptions = response.data or []

        subscription = next(
            (s for s in subscriptions if not s.get("is_lifetime")),
            None,
        )
        if not subscription:
            raise RuntimeError("No active fixed subscription found.")

        from datetime import datetime, timezone, timedelta

        expires_raw = subscription.get("expires_at")
        if not expires_raw:
            raise RuntimeError("Fixed subscription has no expiry date.")

        old_expiry = datetime.fromisoformat(
            str(expires_raw).replace("Z", "+00:00")
        )
        if old_expiry.tzinfo is None:
            old_expiry = old_expiry.replace(tzinfo=timezone.utc)

        now = datetime.now(timezone.utc)
        new_expiry = max(old_expiry, now) + timedelta(days=days)

        updated = (
            supabase.table("subscriptions")
            .update({
                "expires_at": new_expiry.isoformat(),
                "status": "active",
                "revoked_at": None,
            })
            .eq("id", subscription["id"])
            .eq("status", "active")
            .execute()
        )

        if not updated.data:
            raise RuntimeError("Subscription was not updated.")

        await callback.message.edit_text(
            "✅ ACCESS EXTENDED\n\n"
            f"Added: +{days} days\n"
            f"Previous expiry: {old_expiry.isoformat()}\n"
            f"New expiry: {new_expiry.isoformat()}",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="👤 Customer", callback_data=f"customer_{user_id}")],
                    [InlineKeyboardButton(text="👥 Customer Management", callback_data="manage_users")],
                ]
            ),
        )

    except Exception as error:
        print("EXTEND ACCESS ERROR:", repr(error))
        await callback.message.edit_text(
            f"❌ Could not extend customer access.\n\nReason: {error}",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="🔙 Customer", callback_data=f"customer_{user_id}")
            ]]),
        )



# ============================================================
# SAFE CALLBACK ANSWER
# Telegram can reject stale callback queries after a restart or
# when an old inline button is clicked. This must never break the
# actual handler.
# ============================================================

async def safe_callback_answer(callback: CallbackQuery, *args, **kwargs):
    try:
        await callback.answer(*args, **kwargs)
    except Exception as error:
        # A stale/expired callback query is harmless. The handler can
        # continue and edit/send the requested screen.
        if "query is too old" in str(error).lower() or "query id is invalid" in str(error).lower():
            print("ℹ️ Ignored stale Telegram callback query.")
            return
        raise

# ============================================================
# CUSTOMER PAYMENT HISTORY
# ============================================================

@dp.callback_query(F.data.startswith("customer_payments_"))
async def customer_payment_history_handler(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await deny_access(callback=callback)
        return

    await safe_callback_answer(callback)

    user_id = callback.data.replace("customer_payments_", "", 1)

    try:
        user_response = (
            supabase
            .table("users")
            .select(
                "id, telegram_user_id, username, first_name, last_name"
            )
            .eq("id", user_id)
            .limit(1)
            .execute()
        )

        if not user_response.data:
            raise RuntimeError("Customer not found.")

        user = user_response.data[0]

        payment_response = (
            supabase
            .table("payment_requests")
            .select(
                "id, payment_number, course_id, plan_id, amount, "
                "currency, status, submitted_at, approved_at, "
                "rejection_reason"
            )
            .eq("user_id", user_id)
            .order("submitted_at", desc=True)
            .limit(50)
            .execute()
        )

        payments = payment_response.data or []

        name = " ".join(
            p for p in [user.get("first_name"), user.get("last_name")]
            if p
        ) or "Unknown Customer"

        lines = [
            "💳 PAYMENT HISTORY",
            "",
            f"Customer: {name}",
            f"Telegram ID: {user.get('telegram_user_id')}",
            "",
        ]

        if not payments:
            lines.append("No payment requests found.")
        else:
            for payment in payments:
                course_response = (
                    supabase.table("courses")
                    .select("name")
                    .eq("id", payment["course_id"])
                    .limit(1)
                    .execute()
                )

                plan_response = (
                    supabase.table("plans")
                    .select("name, plan_type, duration_days")
                    .eq("id", payment["plan_id"])
                    .limit(1)
                    .execute()
                )

                course_name = (
                    course_response.data[0]["name"]
                    if course_response.data
                    else "Unknown course"
                )

                plan = plan_response.data[0] if plan_response.data else {}
                plan_name = plan.get("name") or "Unknown plan"

                status = payment.get("status") or "unknown"
                status_icon = {
                    "approved": "✅",
                    "pending": "🟡",
                    "rejected": "❌",
                    "cancelled": "⚪",
                }.get(status, "⚪")

                amount = payment.get("amount")
                currency = payment.get("currency") or "INR"
                submitted = payment.get("submitted_at")
                approved = payment.get("approved_at")
                reason = payment.get("rejection_reason")

                lines.extend([
                    f"{status_icon} Payment #{payment.get('payment_number')}",
                    f"Amount: {amount} {currency}",
                    f"Plan: {plan_name}",
                    f"Course: {course_name}",
                    f"Status: {status}",
                    f"Submitted: {submitted or 'N/A'}",
                ])

                if approved:
                    lines.append(f"Approved: {approved}")

                if reason:
                    lines.append(f"Reason: {reason}")

                lines.append("")

        await callback.message.edit_text(
            "\n".join(lines).strip(),
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="👤 Customer",
                            callback_data=f"customer_{user_id}",
                        )
                    ],
                    [
                        InlineKeyboardButton(
                            text="👥 Customer Management",
                            callback_data="manage_users",
                        )
                    ],
                ]
            ),
        )

    except Exception as error:
        print("CUSTOMER PAYMENT HISTORY ERROR:", repr(error))

        await callback.message.edit_text(
            "❌ Could not load payment history.",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="👤 Customer",
                            callback_data=f"customer_{user_id}",
                        )
                    ],
                    [
                        InlineKeyboardButton(
                            text="👥 Customer Management",
                            callback_data="manage_users",
                        )
                    ],
                ]
            ),
        )



# ============================================================
# COMPACT CALLBACK UUID HELPERS
# Telegram callback_data is limited to 64 bytes.
# UUID strings are 36 bytes each, so pack them before putting
# multiple IDs into callback_data.
# ============================================================

import base64
import uuid as _uuid


def pack_uuid_pair(first_id: str, second_id: str) -> str:
    raw = _uuid.UUID(str(first_id)).bytes + _uuid.UUID(str(second_id)).bytes
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def unpack_uuid_pair(token: str):
    padded = token + "=" * (-len(token) % 4)
    raw = base64.urlsafe_b64decode(padded.encode())

    if len(raw) != 32:
        raise ValueError("Invalid compact UUID token.")

    return (
        str(_uuid.UUID(bytes=raw[:16])),
        str(_uuid.UUID(bytes=raw[16:])),
    )


def pack_uuid_triple(first_id: str, second_id: str, third_id: str) -> str:
    raw = (
        _uuid.UUID(str(first_id)).bytes
        + _uuid.UUID(str(second_id)).bytes
        + _uuid.UUID(str(third_id)).bytes
    )
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def unpack_uuid_triple(token: str):
    padded = token + "=" * (-len(token) % 4)
    raw = base64.urlsafe_b64decode(padded.encode())

    if len(raw) != 48:
        raise ValueError("Invalid compact UUID triple token.")

    return (
        str(_uuid.UUID(bytes=raw[:16])),
        str(_uuid.UUID(bytes=raw[16:32])),
        str(_uuid.UUID(bytes=raw[32:])),
    )


# ============================================================
# GRANT CUSTOMER ACCESS
# ============================================================

def grant_course_keyboard(user_id: str, courses) -> InlineKeyboardMarkup:
    buttons = []
    for course in courses:
        buttons.append([
            InlineKeyboardButton(
                text=f"📚 {course.get('name', 'Course')[:48]}",
                callback_data=f"grant_course_{pack_uuid_pair(user_id, course['id'])}",
            )
        ])

    buttons.append([
        InlineKeyboardButton(
            text="🔙 Customer",
            callback_data=f"customer_{user_id}",
        )
    ])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def grant_plan_keyboard(user_id: str, course_id: str, plans) -> InlineKeyboardMarkup:
    buttons = []

    for plan in plans:
        plan_type = plan.get("plan_type")
        price = plan.get("price")
        currency = plan.get("currency") or "INR"

        if plan_type == "lifetime":
            label = f"♾️ {plan.get('name', 'Lifetime')}"
        else:
            duration = plan.get("duration_days") or "?"
            label = f"📅 {plan.get('name', 'Plan')} • {duration}d"

        if price is not None:
            label += f" • {price} {currency}"

        buttons.append([
            InlineKeyboardButton(
                text=label[:64],
                callback_data=f"grant_plan_{pack_uuid_pair(user_id, plan['id'])}",
            )
        ])

    buttons.append([
        InlineKeyboardButton(
            text="🔙 Courses",
            callback_data=f"grant_customer_{user_id}",
        )
    ])

    return InlineKeyboardMarkup(inline_keyboard=buttons)


@dp.callback_query(F.data.startswith("grant_customer_"))
async def grant_customer_handler(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await deny_access(callback=callback)
        return

    await safe_callback_answer(callback)
    user_id = callback.data.replace("grant_customer_", "", 1)

    try:
        # Use the same known-working courses query already used by
        # the Admin Bot's Manage Courses screen.
        # Keep Grant Access independent of optional course metadata columns.
        # The customer bot already proves these columns exist.
        courses_response = (
            supabase
            .table("courses")
            .select("id, name, description, status")
            .eq("status", "active")
            .execute()
        )

        courses = courses_response.data or []

        if not courses:
            await callback.message.edit_text(
                "❌ No courses found.",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                    InlineKeyboardButton(
                        text="🔙 Customer",
                        callback_data=f"customer_{user_id}",
                    )
                ]]),
            )
            return

        await callback.message.edit_text(
            "🔐 GRANT CUSTOMER ACCESS\n\n"
            "Select the course:",
            reply_markup=grant_course_keyboard(user_id, courses),
        )

    except Exception as error:
        import traceback
        print("GRANT ACCESS COURSE ERROR:", repr(error))
        traceback.print_exc()
        await callback.message.edit_text(
            "❌ Could not load courses.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(
                    text="🔙 Customer",
                    callback_data=f"customer_{user_id}",
                )
            ]]),
        )


@dp.callback_query(F.data.startswith("grant_course_"))
async def grant_course_handler(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await deny_access(callback=callback)
        return

    await safe_callback_answer(callback)

    payload = callback.data.replace("grant_course_", "", 1)
    user_id, course_id = unpack_uuid_pair(payload)

    try:
        plans_response = (
            supabase
            .table("plans")
            .select(
                "id, name, plan_type, price, currency, "
                "duration_days, is_active"
            )
            .eq("course_id", course_id)
            .eq("is_active", True)
            .order("sort_order")
            .order("created_at")
            .execute()
        )
        plans = plans_response.data or []

        if not plans:
            await callback.message.edit_text(
                "❌ No active plans found for this course.",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                    InlineKeyboardButton(
                        text="🔙 Courses",
                        callback_data=f"grant_customer_{user_id}",
                    )
                ]]),
            )
            return

        await callback.message.edit_text(
            "🔐 GRANT CUSTOMER ACCESS\n\n"
            "Select the plan:",
            reply_markup=grant_plan_keyboard(user_id, course_id, plans),
        )

    except Exception as error:
        print("GRANT ACCESS PLAN ERROR:", repr(error))
        await callback.message.edit_text(
            "❌ Could not load plans.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(
                    text="🔙 Customer",
                    callback_data=f"grant_customer_{user_id}",
                )
            ]]),
        )


@dp.callback_query(F.data.startswith("grant_plan_"))
async def grant_plan_handler(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await deny_access(callback=callback)
        return

    await safe_callback_answer(callback)

    payload = callback.data.replace("grant_plan_", "", 1)
    user_id, plan_id = unpack_uuid_pair(payload)

    try:
        plan_lookup = (
            supabase
            .table("plans")
            .select("id,course_id")
            .eq("id", plan_id)
            .limit(1)
            .execute()
        )

        if not plan_lookup.data:
            raise RuntimeError("Plan not found.")

        course_id = plan_lookup.data[0]["course_id"]

        user_response = (
            supabase.table("users")
            .select("id,telegram_user_id,username,first_name,last_name")
            .eq("id", user_id)
            .limit(1)
            .execute()
        )
        course_response = (
            supabase.table("courses")
            .select("id,name")
            .eq("id", course_id)
            .limit(1)
            .execute()
        )
        plan_response = (
            supabase.table("plans")
            .select(
                "id,name,plan_type,price,currency,duration_days,"
                "description"
            )
            .eq("id", plan_id)
            .limit(1)
            .execute()
        )

        if not user_response.data or not course_response.data or not plan_response.data:
            raise RuntimeError("Customer, course, or plan not found.")

        user = user_response.data[0]
        course = course_response.data[0]
        plan = plan_response.data[0]

        active = get_active_subscription_for_user_course(user_id, course_id)

        if active:
            status = "lifetime" if active.get("is_lifetime") else "active"
            await callback.message.edit_text(
                "⚠️ ACCESS ALREADY EXISTS\n\n"
                f"Course: {course['name']}\n"
                f"Current status: {status}\n\n"
                "Grant Access will not create a duplicate active subscription.",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(
                        text="👤 Customer",
                        callback_data=f"customer_{user_id}",
                    )],
                    [InlineKeyboardButton(
                        text="👥 Customer Management",
                        callback_data="manage_users",
                    )],
                ]),
            )
            return

        duration = plan.get("duration_days")
        plan_type = plan.get("plan_type")
        duration_text = "Lifetime" if plan_type == "lifetime" else f"{duration} days"

        await callback.message.edit_text(
            "🔐 CONFIRM GRANT ACCESS\n\n"
            f"Customer: {user.get('first_name') or ''} {user.get('last_name') or ''}\n"
            f"Course: {course['name']}\n"
            f"Plan: {plan['name']}\n"
            f"Duration: {duration_text}\n\n"
            "No payment request will be created.\n"
            "This is a manual admin grant.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(
                    text="✅ Confirm Grant",
                    callback_data=f"grant_confirm_{pack_uuid_pair(user_id, plan_id)}",
                )],
                [InlineKeyboardButton(
                    text="🔙 Plans",
                    callback_data=f"grant_course_{user_id}_{course_id}",
                )],
            ]),
        )

    except Exception as error:
        print("GRANT ACCESS PREVIEW ERROR:", repr(error))
        await callback.message.edit_text(
            f"❌ Could not prepare grant.\n\nReason: {error}",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(
                    text="🔙 Customer",
                    callback_data=f"customer_{user_id}",
                )
            ]]),
        )


@dp.callback_query(F.data.startswith("grant_confirm_"))
async def grant_confirm_handler(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await deny_access(callback=callback)
        return

    await safe_callback_answer(callback)

    payload = callback.data.replace("grant_confirm_", "", 1)
    user_id, plan_id = unpack_uuid_pair(payload)

    try:
        from datetime import datetime, timezone, timedelta

        plan_lookup = (
            supabase
            .table("plans")
            .select("id,course_id")
            .eq("id", plan_id)
            .limit(1)
            .execute()
        )

        if not plan_lookup.data:
            raise RuntimeError("Plan not found.")

        course_id = plan_lookup.data[0]["course_id"]

        # Re-check immediately before writing to prevent duplicate active access.
        active = get_active_subscription_for_user_course(user_id, course_id)
        if active:
            raise RuntimeError(
                "Customer already has an active subscription for this course."
            )

        user_response = (
            supabase.table("users")
            .select("id,telegram_user_id,username,first_name,last_name")
            .eq("id", user_id)
            .limit(1)
            .execute()
        )
        course_response = (
            supabase.table("courses")
            .select("id,name")
            .eq("id", course_id)
            .limit(1)
            .execute()
        )
        plan_response = (
            supabase.table("plans")
            .select(
                "id,name,plan_type,price,currency,duration_days,"
                "description"
            )
            .eq("id", plan_id)
            .limit(1)
            .execute()
        )

        if not user_response.data or not course_response.data or not plan_response.data:
            raise RuntimeError("Customer, course, or plan not found.")

        user = user_response.data[0]
        course = course_response.data[0]
        plan = plan_response.data[0]

        started_at = datetime.now(timezone.utc)
        is_lifetime = plan.get("plan_type") == "lifetime"
        expires_at = None

        if not is_lifetime:
            duration = plan.get("duration_days")
            if not duration:
                raise RuntimeError("duration_days is missing for this plan.")
            expires_at = (
                started_at + timedelta(days=int(duration))
            ).isoformat()

        subscription_response = (
            supabase.table("subscriptions")
            .insert({
                "user_id": user_id,
                "course_id": course_id,
                "plan_id": plan_id,
                "status": "active",
                "started_at": started_at.isoformat(),
                "expires_at": expires_at,
                "is_lifetime": is_lifetime,
            })
            .execute()
        )

        if not subscription_response.data:
            raise RuntimeError("Subscription was not created.")

        subscription = subscription_response.data[0]

        channel_response = (
            supabase.table("channels")
            .select(
                "id,telegram_chat_id,channel_title,is_active,"
                "bot_is_admin,can_invite_users"
            )
            .eq("course_id", course_id)
            .eq("is_active", True)
            .limit(1)
            .execute()
        )

        if not channel_response.data:
            # Roll back the manual grant if Telegram access cannot be provisioned.
            supabase.table("subscriptions").delete().eq(
                "id", subscription["id"]
            ).execute()
            raise RuntimeError(
                "No active Telegram channel is configured for this course."
            )

        channel = channel_response.data[0]

        if not channel.get("bot_is_admin") or not channel.get("can_invite_users"):
            supabase.table("subscriptions").delete().eq(
                "id", subscription["id"]
            ).execute()
            raise RuntimeError(
                "Admin Bot does not have the required Telegram invite permissions."
            )

        invite_kwargs = {
            "chat_id": channel["telegram_chat_id"],
            "name": f"Manual Grant {user.get('telegram_user_id')}",
            "member_limit": 1,
        }

        if expires_at:
            invite_kwargs["expire_date"] = int(
                (
                    started_at + timedelta(days=int(plan["duration_days"]))
                ).timestamp()
            )

        telegram_invite = await callback.bot.create_chat_invite_link(
            **invite_kwargs
        )

        invite_response = (
            supabase.table("invite_links")
            .insert({
                "subscription_id": subscription["id"],
                "channel_id": channel["id"],
                "telegram_invite_link": telegram_invite.invite_link,
                "status": "created",
                "expires_at": expires_at,
            })
            .execute()
        )

        if not invite_response.data:
            # Revoke the Telegram invite and roll back the subscription.
            try:
                await callback.bot.revoke_chat_invite_link(
                    chat_id=channel["telegram_chat_id"],
                    invite_link=telegram_invite.invite_link,
                )
            except Exception as revoke_error:
                print("GRANT ROLLBACK INVITE ERROR:", repr(revoke_error))

            supabase.table("subscriptions").delete().eq(
                "id", subscription["id"]
            ).execute()
            raise RuntimeError("Invite link record was not created.")

        await send_customer_access_message(
            user,
            course,
            plan,
            telegram_invite.invite_link,
        )

        supabase.table("invite_links").update({
            "status": "sent",
            "sent_at": datetime.now(timezone.utc).isoformat(),
        }).eq("id", invite_response.data[0]["id"]).execute()

        duration_text = (
            "Lifetime"
            if is_lifetime
            else f"{plan.get('duration_days')} days"
        )

        await write_audit_log(
            callback.from_user.id,
            "GRANT_MANUAL_ACCESS",
            target_user_id=user_id,
            course_id=course_id,
            plan_id=plan_id,
            details={"source": "admin_customer_flow"},
        )

        await callback.message.edit_text(
            "✅ ACCESS GRANTED\n\n"
            f"Customer: {user.get('first_name') or ''} {user.get('last_name') or ''}\n"
            f"Course: {course['name']}\n"
            f"Plan: {plan['name']}\n"
            f"Duration: {duration_text}\n"
            f"Expires: {expires_at or 'Never'}\n\n"
            "🔗 One-time invite sent to the customer.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(
                    text="👤 Customer",
                    callback_data=f"customer_{user_id}",
                )],
                [InlineKeyboardButton(
                    text="👥 Customer Management",
                    callback_data="manage_users",
                )],
            ]),
        )

    except Exception as error:
        print("GRANT ACCESS ERROR:", repr(error))
        await callback.message.edit_text(
            f"❌ Grant Access failed.\n\nReason: {error}",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(
                    text="👤 Customer",
                    callback_data=f"customer_{user_id}",
                )],
                [InlineKeyboardButton(
                    text="👥 Customer Management",
                    callback_data="manage_users",
                )],
            ]),
        )


# ============================================================
# REVOKE CUSTOMER ACCESS
# ============================================================

@dp.callback_query(F.data.startswith("revoke_customer_"))
async def revoke_customer_handler(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await deny_access(callback=callback)
        return

    await safe_callback_answer(callback)
    user_id = callback.data.replace("revoke_customer_", "", 1)

    try:
        user_response = (
            supabase
            .table("users")
            .select("id,telegram_user_id,username,first_name,last_name")
            .eq("id", user_id)
            .limit(1)
            .execute()
        )

        if not user_response.data:
            raise RuntimeError("Customer not found.")

        subscriptions_response = (
            supabase
            .table("subscriptions")
            .select(
                "id,course_id,plan_id,status,started_at,expires_at,"
                "is_lifetime,joined_channel_at,revoked_at"
            )
            .eq("user_id", user_id)
            .eq("status", "active")
            .order("started_at", desc=True)
            .limit(50)
            .execute()
        )

        subscriptions = subscriptions_response.data or []

        if not subscriptions:
            await callback.message.edit_text(
                "🚫 REVOKE ACCESS\n\n"
                "No active subscriptions were found for this customer.",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(
                        text="👤 Customer",
                        callback_data=f"customer_{user_id}",
                    )],
                    [InlineKeyboardButton(
                        text="👥 Customer Management",
                        callback_data="manage_users",
                    )],
                ]),
            )
            return

        # Show all active subscriptions so the admin can revoke exactly one.
        buttons = []

        for sub in subscriptions:
            course_response = (
                supabase
                .table("courses")
                .select("id,name")
                .eq("id", sub["course_id"])
                .limit(1)
                .execute()
            )
            course = course_response.data[0] if course_response.data else {}
            course_name = course.get("name") or "Unknown Course"

            icon = "♾️" if sub.get("is_lifetime") else "🟢"
            buttons.append([
                InlineKeyboardButton(
                    text=f"{icon} {course_name[:45]}",
                    callback_data=f"revoke_sub_{sub['id']}",
                )
            ])

        buttons.extend([
            [InlineKeyboardButton(
                text="🔙 Customer",
                callback_data=f"customer_{user_id}",
            )],
        ])

        await callback.message.edit_text(
            "🚫 REVOKE ACCESS\n\n"
            "Select the active course access you want to revoke.\n\n"
            "The subscription will be marked as revoked; payment history "
            "will not be deleted.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
        )

    except Exception as error:
        import traceback
        print("REVOKE ACCESS MENU ERROR:", repr(error))
        traceback.print_exc()

        await callback.message.edit_text(
            "❌ Could not load access to revoke.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(
                    text="👤 Customer",
                    callback_data=f"customer_{user_id}",
                )],
                [InlineKeyboardButton(
                    text="👥 Customer Management",
                    callback_data="manage_users",
                )],
            ]),
        )


@dp.callback_query(F.data.startswith("revoke_sub_"))
async def revoke_subscription_preview_handler(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await deny_access(callback=callback)
        return

    await safe_callback_answer(callback)

    subscription_id = callback.data.replace("revoke_sub_", "", 1)

    try:
        response = (
            supabase
            .table("subscriptions")
            .select(
                "id,user_id,course_id,plan_id,status,started_at,"
                "expires_at,is_lifetime,joined_channel_at"
            )
            .eq("id", subscription_id)
            .limit(1)
            .execute()
        )

        if not response.data:
            raise RuntimeError("Subscription not found.")

        subscription = response.data[0]

        if subscription.get("status") != "active":
            raise RuntimeError("This subscription is no longer active.")

        course_response = (
            supabase
            .table("courses")
            .select("id,name")
            .eq("id", subscription["course_id"])
            .limit(1)
            .execute()
        )
        plan_response = (
            supabase
            .table("plans")
            .select("id,name,plan_type")
            .eq("id", subscription["plan_id"])
            .limit(1)
            .execute()
        )
        user_response = (
            supabase
            .table("users")
            .select("id,first_name,last_name,telegram_user_id")
            .eq("id", subscription["user_id"])
            .limit(1)
            .execute()
        )

        course = course_response.data[0] if course_response.data else {}
        plan = plan_response.data[0] if plan_response.data else {}
        user = user_response.data[0] if user_response.data else {}

        customer_name = " ".join(
            p for p in [user.get("first_name"), user.get("last_name")]
            if p
        ) or "Unknown"

        await callback.message.edit_text(
            "⚠️ CONFIRM REVOKE ACCESS\n\n"
            f"Customer: {customer_name}\n"
            f"Telegram ID: {user.get('telegram_user_id')}\n"
            f"Course: {course.get('name') or 'Unknown Course'}\n"
            f"Plan: {plan.get('name') or 'Unknown Plan'}\n"
            f"Lifetime: {'Yes' if subscription.get('is_lifetime') else 'No'}\n"
            f"Expires: {subscription.get('expires_at') or 'Never'}\n\n"
            "⚠️ This will:\n"
            "• Mark the subscription as revoked\n"
            "• Revoke unused Telegram invite links\n"
            "• Remove the customer from the connected Telegram group "
            "if they have already joined\n"
            "• Keep payment and subscription history intact\n\n"
            "This action cannot be undone automatically.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(
                    text="🚫 YES, REVOKE ACCESS",
                    callback_data=f"revoke_confirm_{subscription_id}",
                )],
                [InlineKeyboardButton(
                    text="🔙 Cancel",
                    callback_data=f"customer_{subscription['user_id']}",
                )],
            ]),
        )

    except Exception as error:
        import traceback
        print("REVOKE PREVIEW ERROR:", repr(error))
        traceback.print_exc()

        await callback.message.edit_text(
            "❌ Could not prepare revoke action.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(
                    text="🔙 Customer",
                    callback_data="manage_users",
                )],
            ]),
        )


@dp.callback_query(F.data.startswith("revoke_confirm_"))
async def revoke_subscription_confirm_handler(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await deny_access(callback=callback)
        return

    await safe_callback_answer(callback)

    subscription_id = callback.data.replace("revoke_confirm_", "", 1)

    try:
        subscription_response = (
            supabase
            .table("subscriptions")
            .select(
                "id,user_id,course_id,plan_id,status,is_lifetime,"
                "joined_channel_at"
            )
            .eq("id", subscription_id)
            .limit(1)
            .execute()
        )

        if not subscription_response.data:
            raise RuntimeError("Subscription not found.")

        subscription = subscription_response.data[0]

        if subscription.get("status") != "active":
            await callback.message.edit_text(
                "ℹ️ This subscription is already no longer active.",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(
                        text="👤 Customer",
                        callback_data=f"customer_{subscription['user_id']}",
                    )],
                    [InlineKeyboardButton(
                        text="👥 Customer Management",
                        callback_data="manage_users",
                    )],
                ]),
            )
            return

        from datetime import datetime, timezone

        now = datetime.now(timezone.utc).isoformat()

        # 1. Revoke the database subscription first.
        updated = (
            supabase
            .table("subscriptions")
            .update({
                "status": "revoked",
                "revoked_at": now,
            })
            .eq("id", subscription_id)
            .eq("status", "active")
            .execute()
        )

        if not updated.data:
            raise RuntimeError(
                "Subscription was not revoked. It may have changed meanwhile."
            )

        # 2. Revoke every stored invite for this subscription.
        invite_response = (
            supabase
            .table("invite_links")
            .select(
                "id,channel_id,telegram_invite_link,status,revoked_at"
            )
            .eq("subscription_id", subscription_id)
            .is_("revoked_at", "null")
            .limit(100)
            .execute()
        )

        invites = invite_response.data or []
        invite_errors = []

        for invite in invites:
            channel_response = (
                supabase
                .table("channels")
                .select("telegram_chat_id")
                .eq("id", invite["channel_id"])
                .limit(1)
                .execute()
            )

            if not channel_response.data:
                invite_errors.append(
                    f"Channel not found for invite {invite['id']}"
                )
                continue

            chat_id = channel_response.data[0]["telegram_chat_id"]

            try:
                await callback.bot.revoke_chat_invite_link(
                    chat_id=chat_id,
                    invite_link=invite["telegram_invite_link"],
                )
            except Exception as telegram_error:
                # Already-revoked/expired links should not stop the database
                # cleanup. Record the error for the admin.
                invite_errors.append(
                    f"Invite revoke failed: {telegram_error}"
                )

            supabase.table("invite_links").update({
                "status": "revoked",
                "revoked_at": now,
            }).eq("id", invite["id"]).execute()

        # 3. If the customer has joined, remove them from the connected group.
        member_removal_error = None

        try:
            channel_response = (
                supabase
                .table("channels")
                .select(
                    "id,telegram_chat_id,is_active,bot_is_admin,"
                    "can_manage_members"
                )
                .eq("course_id", subscription["course_id"])
                .eq("is_active", True)
                .limit(1)
                .execute()
            )

            if channel_response.data:
                channel = channel_response.data[0]

                if (
                    channel.get("bot_is_admin")
                    and channel.get("can_manage_members")
                ):
                    user_response = (
                        supabase
                        .table("users")
                        .select("telegram_user_id")
                        .eq("id", subscription["user_id"])
                        .limit(1)
                        .execute()
                    )

                    if user_response.data:
                        telegram_user_id = user_response.data[0]["telegram_user_id"]

                        # Ban removes the member. Immediately unban so the user
                        # is not permanently blocked from a future legitimate
                        # purchase/re-grant.
                        await callback.bot.ban_chat_member(
                            chat_id=channel["telegram_chat_id"],
                            user_id=telegram_user_id,
                        )
                        await callback.bot.unban_chat_member(
                            chat_id=channel["telegram_chat_id"],
                            user_id=telegram_user_id,
                            only_if_banned=True,
                        )

                        supabase.table("subscriptions").update({
                            "joined_channel_at": None,
                        }).eq("id", subscription_id).execute()

        except Exception as member_error:
            member_removal_error = str(member_error)

        message = (
            "✅ ACCESS REVOKED\n\n"
            f"Subscription: {subscription_id}\n"
            "Status: revoked\n"
            "Payment history: preserved\n"
            "Subscription history: preserved\n"
            f"Invite links processed: {len(invites)}"
        )

        if invite_errors:
            message += (
                "\n\n⚠️ Some invite links could not be revoked:\n"
                + "\n".join(f"• {e}" for e in invite_errors[:3])
            )

        if member_removal_error:
            message += (
                "\n\n⚠️ Telegram group removal could not be completed:\n"
                f"{member_removal_error}"
            )

        await callback.message.edit_text(
            message,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(
                    text="👤 Customer",
                    callback_data=f"customer_{subscription['user_id']}",
                )],
                [InlineKeyboardButton(
                    text="👥 Customer Management",
                    callback_data="manage_users",
                )],
            ]),
        )

    except Exception as error:
        import traceback
        print("REVOKE CONFIRM ERROR:", repr(error))
        traceback.print_exc()

        await callback.message.edit_text(
            f"❌ Revoke Access failed.\n\nReason: {error}",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(
                    text="👥 Customer Management",
                    callback_data="manage_users",
                )],
            ]),
        )



# ============================================================
# STATISTICS
# ============================================================

async def build_admin_dashboard_text():
    """
    Build the live Admin Dashboard + Advanced Analytics from Supabase.

    Analytics:
    - Customers / subscription status
    - Pending / approved payments
    - Total revenue
    - Course-wise sales and revenue
    - New purchases vs renewals
    - Monthly revenue
    - Average approved payment
    - Best-selling course
    """

    from collections import defaultdict
    from datetime import datetime, timezone, timedelta

    now = datetime.now(timezone.utc)
    seven_days = now + timedelta(days=7)
    month_start = now.replace(
        day=1, hour=0, minute=0, second=0, microsecond=0
    )

    # -----------------------------
    # Core counts
    # -----------------------------
    users_response = (
        supabase.table("users")
        .select("id")
        .limit(10000)
        .execute()
    )

    active_response = (
        supabase.table("subscriptions")
        .select("id")
        .eq("status", "active")
        .limit(10000)
        .execute()
    )

    expired_response = (
        supabase.table("subscriptions")
        .select("id")
        .eq("status", "expired")
        .limit(10000)
        .execute()
    )

    lifetime_response = (
        supabase.table("subscriptions")
        .select("id")
        .eq("is_lifetime", True)
        .eq("status", "active")
        .limit(10000)
        .execute()
    )

    expiring_response = (
        supabase.table("subscriptions")
        .select("id,expires_at")
        .eq("status", "active")
        .eq("is_lifetime", False)
        .gte("expires_at", now.isoformat())
        .lte("expires_at", seven_days.isoformat())
        .limit(10000)
        .execute()
    )

    pending_response = (
        supabase.table("payment_requests")
        .select("id")
        .eq("status", "pending")
        .limit(10000)
        .execute()
    )

    approved_response = (
        supabase.table("payment_requests")
        .select(
            "id,amount,currency,course_id,plan_id,submitted_at,"
            "status,payment_number"
        )
        .eq("status", "approved")
        .limit(10000)
        .execute()
    )

    approved_payments = approved_response.data or []

    # -----------------------------
    # Lookup course + plan names
    # -----------------------------
    courses_response = (
        supabase.table("courses")
        .select("id,name")
        .limit(10000)
        .execute()
    )

    plans_response = (
        supabase.table("plans")
        .select("id,name,plan_type,duration_days")
        .limit(10000)
        .execute()
    )

    course_name_by_id = {
        row["id"]: row.get("name") or "Unknown Course"
        for row in (courses_response.data or [])
    }

    plan_by_id = {
        row["id"]: row
        for row in (plans_response.data or [])
    }

    # -----------------------------
    # Revenue + course analytics
    # -----------------------------
    total_revenue = 0.0
    month_revenue = 0.0
    revenue_currency = "INR"

    course_sales = defaultdict(int)
    course_revenue = defaultdict(float)
    plan_sales = defaultdict(int)

    new_purchase_count = 0
    renewal_count = 0

    daily_revenue = defaultdict(float)

    for payment in approved_payments:
        try:
            amount = float(payment.get("amount") or 0)
        except (TypeError, ValueError):
            amount = 0.0

        total_revenue += amount

        if payment.get("currency"):
            revenue_currency = payment["currency"]

        course_id = payment.get("course_id")
        plan_id = payment.get("plan_id")

        if course_id:
            course_sales[course_id] += 1
            course_revenue[course_id] += amount

        if plan_id:
            plan_sales[plan_id] += 1

        submitted_at = payment.get("submitted_at")

        if submitted_at:
            try:
                submitted_dt = datetime.fromisoformat(
                    str(submitted_at).replace("Z", "+00:00")
                )

                if submitted_dt.tzinfo is None:
                    submitted_dt = submitted_dt.replace(
                        tzinfo=timezone.utc
                    )

                if submitted_dt >= month_start:
                    month_revenue += amount

                day_key = submitted_dt.strftime("%Y-%m-%d")
                daily_revenue[day_key] += amount

            except (TypeError, ValueError):
                pass

        # Renewal detection is based on the selected plan name/type.
        # Existing plans named "renewal" are counted as renewals.
        plan = plan_by_id.get(plan_id, {})
        plan_name = str(plan.get("name") or "").lower()

        if "renew" in plan_name:
            renewal_count += 1
        else:
            new_purchase_count += 1

    approved_count = len(approved_payments)

    average_payment = (
        total_revenue / approved_count
        if approved_count
        else 0.0
    )

    # -----------------------------
    # Best-selling course
    # -----------------------------
    best_course_id = None
    best_course_sales = 0

    if course_sales:
        best_course_id, best_course_sales = max(
            course_sales.items(),
            key=lambda item: item[1],
        )

    best_course_name = (
        course_name_by_id.get(best_course_id, "N/A")
        if best_course_id
        else "N/A"
    )

    # -----------------------------
    # Top courses by revenue
    # -----------------------------
    top_courses = sorted(
        course_revenue.items(),
        key=lambda item: item[1],
        reverse=True,
    )[:5]

    top_course_lines = []

    for course_id, revenue in top_courses:
        name = course_name_by_id.get(
            course_id,
            "Unknown Course",
        )
        sales = course_sales.get(course_id, 0)

        top_course_lines.append(
            f"• {name[:38]} — {sales} sales — "
            f"{revenue_currency} {revenue:,.2f}"
        )

    if not top_course_lines:
        top_course_lines.append("• No approved course sales yet.")

    # -----------------------------
    # Top plans
    # -----------------------------
    top_plans = sorted(
        plan_sales.items(),
        key=lambda item: item[1],
        reverse=True,
    )[:5]

    top_plan_lines = []

    for plan_id, sales in top_plans:
        plan = plan_by_id.get(plan_id, {})
        name = plan.get("name") or "Unknown Plan"
        top_plan_lines.append(
            f"• {name[:42]} — {sales} sales"
        )

    if not top_plan_lines:
        top_plan_lines.append("• No approved plan sales yet.")

    # -----------------------------
    # Last 7 calendar days revenue
    # -----------------------------
    last_7_days_lines = []

    for offset in range(6, -1, -1):
        day = (now - timedelta(days=offset)).strftime("%Y-%m-%d")
        amount = daily_revenue.get(day, 0.0)
        last_7_days_lines.append(
            f"• {day}: {revenue_currency} {amount:,.2f}"
        )

    total_customers = len(users_response.data or [])
    active_subscriptions = len(active_response.data or [])
    expiring_soon = len(expiring_response.data or [])
    expired_subscriptions = len(expired_response.data or [])
    lifetime_subscriptions = len(lifetime_response.data or [])
    pending_payments = len(pending_response.data or [])

    return (
        "📊 ADMIN DASHBOARD + ANALYTICS\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "👥 CUSTOMER / ACCESS\n"
        f"👥 Total Customers: {total_customers}\n"
        f"🟢 Active Subscriptions: {active_subscriptions}\n"
        f"♾️ Lifetime Active: {lifetime_subscriptions}\n"
        f"⏰ Expiring Within 7 Days: {expiring_soon}\n"
        f"🔴 Expired Subscriptions: {expired_subscriptions}\n\n"

        "💳 PAYMENT / REVENUE\n"
        f"💳 Pending Payments: {pending_payments}\n"
        f"✅ Approved Payments: {approved_count}\n"
        f"💰 Total Revenue: {revenue_currency} {total_revenue:,.2f}\n"
        f"📅 This Month: {revenue_currency} {month_revenue:,.2f}\n"
        f"📊 Average Payment: {revenue_currency} {average_payment:,.2f}\n\n"

        "🔄 PURCHASE MIX\n"
        f"🆕 New Purchases: {new_purchase_count}\n"
        f"🔄 Renewals: {renewal_count}\n\n"

        "🏆 BEST SELLER\n"
        f"📚 {best_course_name[:45]}\n"
        f"🛒 Sales: {best_course_sales}\n\n"

        "📚 TOP COURSES BY REVENUE\n"
        + "\n".join(top_course_lines)
        + "\n\n"

        "💳 TOP PLANS\n"
        + "\n".join(top_plan_lines)
        + "\n\n"

        "📅 LAST 7 DAYS REVENUE\n"
        + "\n".join(last_7_days_lines)
        + "\n\n"

        f"🕐 Updated: {now.strftime('%Y-%m-%d %H:%M:%S UTC')}"
    )


@dp.callback_query(F.data == "statistics")
async def statistics_handler(
    callback: CallbackQuery,
):
    if not is_admin(callback.from_user.id):
        await deny_access(callback=callback)
        return

    await safe_callback_answer(callback)

    try:
        dashboard_text = await build_admin_dashboard_text()

        await callback.message.edit_text(
            dashboard_text,
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="🔄 Refresh Analytics",
                            callback_data="statistics",
                        )
                    ],
                    [
                        InlineKeyboardButton(
                            text="💳 Pending Payments",
                            callback_data="pending_payments",
                        ),
                        InlineKeyboardButton(
                            text="👥 Manage Users",
                            callback_data="manage_users",
                        ),
                    ],
                    [
                        InlineKeyboardButton(
                            text="📚 Manage Courses",
                            callback_data="manage_courses",
                        ),
                        InlineKeyboardButton(
                            text="🔙 Admin Panel",
                            callback_data="admin_panel",
                        ),
                    ],
                ]
            ),
        )

    except Exception as error:
        import traceback
        print("ADMIN DASHBOARD ERROR:", repr(error))
        traceback.print_exc()

        await callback.message.edit_text(
            "❌ Could not load Admin Dashboard.\n\n"
            f"Reason: {error}",
            reply_markup=back_to_admin_menu(),
        )


# ============================================================
# BROADCAST
# ============================================================

def broadcast_audience_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="👥 All Customers",
                    callback_data="broadcast_audience_all",
                )
            ],
            [
                InlineKeyboardButton(
                    text="🟢 Active Subscribers",
                    callback_data="broadcast_audience_active",
                )
            ],
            [
                InlineKeyboardButton(
                    text="⏰ Expiring Within 7 Days",
                    callback_data="broadcast_audience_expiring",
                )
            ],
            [
                InlineKeyboardButton(
                    text="🔴 Expired Customers",
                    callback_data="broadcast_audience_expired",
                )
            ],
            [
                InlineKeyboardButton(
                    text="📚 Course Customers",
                    callback_data="broadcast_audience_course",
                )
            ],
            [
                InlineKeyboardButton(
                    text="❌ Cancel",
                    callback_data="broadcast_cancel",
                )
            ],
        ]
    )


def broadcast_cancel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="❌ Cancel Broadcast",
                    callback_data="broadcast_cancel",
                )
            ]
        ]
    )


def broadcast_confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Confirm & Send",
                    callback_data="broadcast_confirm",
                ),
                InlineKeyboardButton(
                    text="❌ Cancel",
                    callback_data="broadcast_cancel",
                ),
            ]
        ]
    )


def get_broadcast_audience_ids(
    audience: str,
    course_id: str | None = None,
):
    """
    Return unique Telegram user IDs for a broadcast audience.
    Blocked users are excluded.
    """
    now = datetime.now(timezone.utc)
    seven_days = now + timedelta(days=7)

    if audience == "all":
        response = (
            supabase
            .table("users")
            .select("id,telegram_user_id")
                        .limit(10000)
            .execute()
        )
        return list({
            int(row["telegram_user_id"])
            for row in (response.data or [])
            if row.get("telegram_user_id") is not None
        })

    if audience == "active":
        subscriptions = (
            supabase
            .table("subscriptions")
            .select("user_id")
            .eq("status", "active")
            .limit(10000)
            .execute()
        )
    elif audience == "expired":
        subscriptions = (
            supabase
            .table("subscriptions")
            .select("user_id")
            .eq("status", "expired")
            .limit(10000)
            .execute()
        )
    elif audience == "expiring":
        subscriptions = (
            supabase
            .table("subscriptions")
            .select("user_id")
            .eq("status", "active")
            .eq("is_lifetime", False)
            .gte("expires_at", now.isoformat())
            .lte("expires_at", seven_days.isoformat())
            .limit(10000)
            .execute()
        )
    elif audience == "course":
        if not course_id:
            return []
        subscriptions = (
            supabase
            .table("subscriptions")
            .select("user_id")
            .eq("course_id", course_id)
            .limit(10000)
            .execute()
        )
    else:
        return []

    user_ids = list({
        row["user_id"]
        for row in (subscriptions.data or [])
        if row.get("user_id")
    })

    if not user_ids:
        return []

    # Supabase Python client supports .in_ for UUID filters.
    users_response = (
        supabase
        .table("users")
        .select("telegram_user_id")
        .in_("id", user_ids)
                .limit(10000)
        .execute()
    )

    return list({
        int(row["telegram_user_id"])
        for row in (users_response.data or [])
        if row.get("telegram_user_id") is not None
    })


def broadcast_target_label(audience: str, course_name: str | None = None):
    labels = {
        "all": "👥 All Customers",
        "active": "🟢 Active Subscribers",
        "expiring": "⏰ Expiring Within 7 Days",
        "expired": "🔴 Expired Customers",
        "course": f"📚 {course_name or 'Selected Course'}",
    }
    return labels.get(audience, "Unknown")


@dp.callback_query(F.data == "broadcast")
async def broadcast_handler(
    callback: CallbackQuery,
    state: FSMContext,
):
    if not is_admin(callback.from_user.id):
        await deny_access(callback=callback)
        return

    await callback.answer()
    await state.clear()
    await state.set_state(BroadcastStates.choosing_audience)

    await callback.message.edit_text(
        "📢 BROADCAST CENTER\n\n"
        "Choose who should receive the message:\n\n"
        "⚠️ A preview and confirmation step is required before sending.",
        reply_markup=broadcast_audience_keyboard(),
    )


@dp.callback_query(
    F.data.startswith("broadcast_audience_"),
    BroadcastStates.choosing_audience,
)
async def broadcast_audience_handler(
    callback: CallbackQuery,
    state: FSMContext,
):
    if not is_admin(callback.from_user.id):
        await deny_access(callback=callback)
        return

    await callback.answer()

    audience = callback.data.replace(
        "broadcast_audience_",
        "",
        1,
    )

    if audience == "course":
        courses_response = (
            supabase
            .table("courses")
            .select("id,name")
            .eq("status", "active")
            .order("created_at")
            .limit(100)
            .execute()
        )

        courses = courses_response.data or []

        if not courses:
            await callback.message.edit_text(
                "❌ No active courses found.",
                reply_markup=back_to_admin_menu(),
            )
            await state.clear()
            return

        buttons = [
            [
                InlineKeyboardButton(
                    text=f"📚 {course['name'][:45]}",
                    callback_data=f"broadcast_course_{course['id']}",
                )
            ]
            for course in courses
        ]

        buttons.append([
            InlineKeyboardButton(
                text="❌ Cancel",
                callback_data="broadcast_cancel",
            )
        ])

        await state.set_state(BroadcastStates.choosing_course)

        await callback.message.edit_text(
            "📚 COURSE BROADCAST\n\n"
            "Select the course whose customers should receive the message:",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=buttons
            ),
        )
        return

    await state.update_data(
        audience=audience,
        course_id=None,
        course_name=None,
    )
    await state.set_state(BroadcastStates.waiting_for_message)

    await callback.message.edit_text(
        f"📢 TARGET\n\n"
        f"{broadcast_target_label(audience)}\n\n"
        "Now send the message you want to broadcast.\n\n"
        "Text, photo, video, document and other Telegram message types "
        "are supported.\n\n"
        "You will get a preview before anything is sent.",
        reply_markup=broadcast_cancel_keyboard(),
    )


@dp.callback_query(
    F.data.startswith("broadcast_course_"),
    BroadcastStates.choosing_course,
)
async def broadcast_course_handler(
    callback: CallbackQuery,
    state: FSMContext,
):
    if not is_admin(callback.from_user.id):
        await deny_access(callback=callback)
        return

    await callback.answer()

    course_id = callback.data.replace(
        "broadcast_course_",
        "",
        1,
    )

    course_response = (
        supabase
        .table("courses")
        .select("id,name")
        .eq("id", course_id)
        .limit(1)
        .execute()
    )

    if not course_response.data:
        await callback.message.edit_text(
            "❌ Course not found.",
            reply_markup=back_to_admin_menu(),
        )
        await state.clear()
        return

    course_name = course_response.data[0]["name"]

    await state.update_data(
        audience="course",
        course_id=course_id,
        course_name=course_name,
    )
    await state.set_state(BroadcastStates.waiting_for_message)

    await callback.message.edit_text(
        "📢 TARGET\n\n"
        f"📚 {course_name}\n\n"
        "Now send the message you want to broadcast.\n\n"
        "You will get a preview before anything is sent.",
        reply_markup=broadcast_cancel_keyboard(),
    )


@dp.message(BroadcastStates.waiting_for_message)
async def broadcast_message_handler(
    message: Message,
    state: FSMContext,
):
    if not is_admin(message.from_user.id):
        await deny_access(message=message)
        return

    # We keep only the source chat/message IDs in FSM.
    # This allows text, photo, video, document, etc. without storing
    # Telegram file bytes in memory.
    data = await state.get_data()

    audience = data.get("audience")
    course_name = data.get("course_name")

    target_ids = get_broadcast_audience_ids(
        audience=audience,
        course_id=data.get("course_id"),
    )

    if not target_ids:
        await message.answer(
            "❌ No eligible customers found for this audience.",
            reply_markup=back_to_admin_menu(),
        )
        await state.clear()
        return

    payload = {
        "content_type": message.content_type,
        "text": message.text,
        "caption": message.caption,
        "file_id": None,
        "file_name": None,
    }

    if message.photo:
        payload["file_id"] = message.photo[-1].file_id
        payload["file_name"] = "broadcast.jpg"
    elif message.video:
        payload["file_id"] = message.video.file_id
        payload["file_name"] = "broadcast.mp4"
    elif message.document:
        payload["file_id"] = message.document.file_id
        payload["file_name"] = message.document.file_name or "broadcast.bin"
    elif message.audio:
        payload["file_id"] = message.audio.file_id
        payload["file_name"] = message.audio.file_name or "broadcast.mp3"
    elif message.voice:
        payload["file_id"] = message.voice.file_id
        payload["file_name"] = "broadcast.ogg"
    elif message.animation:
        payload["file_id"] = message.animation.file_id
        payload["file_name"] = "broadcast.gif"

    await state.update_data(
        source_chat_id=message.chat.id,
        source_message_id=message.message_id,
        broadcast_payload=payload,
        recipient_count=len(target_ids),
    )
    await state.set_state(BroadcastStates.waiting_for_confirmation)

    # Preview is rendered as a copy in the admin chat. This works for
    # arbitrary Telegram message types without downloading/re-uploading files.
    try:
        await message.bot.copy_message(
            chat_id=message.chat.id,
            from_chat_id=message.chat.id,
            message_id=message.message_id,
        )
    except Exception:
        # If Telegram cannot copy the preview, continue with a textual notice.
        pass

    target_label = broadcast_target_label(
        audience,
        course_name,
    )

    await message.answer(
        "👀 BROADCAST PREVIEW\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        f"🎯 Audience: {target_label}\n"
        f"👥 Recipients: {len(target_ids)}\n\n"
        "⚠️ Nothing has been sent yet.\n"
        "Press Confirm & Send to start the broadcast.",
        reply_markup=broadcast_confirm_keyboard(),
    )


@dp.callback_query(
    F.data == "broadcast_confirm",
    BroadcastStates.waiting_for_confirmation,
)
async def broadcast_confirm_handler(
    callback: CallbackQuery,
    state: FSMContext,
):
    if not is_admin(callback.from_user.id):
        await deny_access(callback=callback)
        return

    if not system_setting_enabled("broadcast_enabled"):
        await callback.answer(
            "Broadcast is disabled in Settings.",
            show_alert=True,
        )
        return

    await callback.answer("Broadcast started.")

    data = await state.get_data()

    audience = data.get("audience")
    course_id = data.get("course_id")
    course_name = data.get("course_name")
    source_chat_id = data.get("source_chat_id")
    source_message_id = data.get("source_message_id")
    broadcast_payload = data.get("broadcast_payload")

    if not source_chat_id or not source_message_id or not broadcast_payload:
        await callback.message.edit_text(
            "❌ Broadcast message is missing.",
            reply_markup=back_to_admin_menu(),
        )
        await state.clear()
        return

    target_ids = get_broadcast_audience_ids(
        audience=audience,
        course_id=course_id,
    )

    total = len(target_ids)
    sent = 0
    failed = 0
    blocked = 0

    await callback.message.edit_text(
        "📤 BROADCAST IN PROGRESS...\n\n"
        f"🎯 {broadcast_target_label(audience, course_name)}\n"
        f"👥 Recipients: {total}\n\n"
        "⏳ Please wait...",
    )

    # Broadcast log is optional; failures here must not stop delivery.
    broadcast_log_id = None
    try:
        log_response = (
            supabase
            .table("broadcast_logs")
            .insert({
                "admin_telegram_id": int(callback.from_user.id),
                "audience": audience,
                "course_id": course_id,
                "source_chat_id": int(source_chat_id),
                "source_message_id": int(source_message_id),
                "recipient_count": total,
                "status": "sending",
            })
            .execute()
        )
        if log_response.data:
            broadcast_log_id = log_response.data[0]["id"]
    except Exception as error:
        print("BROADCAST LOG CREATE ERROR:", repr(error))

    if not CUSTOMER_BOT_TOKEN:
        raise RuntimeError("CUSTOMER_BOT_TOKEN is missing in .env")

    customer_bot = Bot(token=CUSTOMER_BOT_TOKEN)

    try:
        for telegram_user_id in target_ids:
            try:
                content_type = broadcast_payload.get("content_type")
                text_body = broadcast_payload.get("text")
                caption = broadcast_payload.get("caption")
                file_id = broadcast_payload.get("file_id")
                file_name = broadcast_payload.get("file_name") or "broadcast.bin"

                if content_type == "text":
                    await customer_bot.send_message(
                        chat_id=telegram_user_id,
                        text=text_body or "",
                    )
                else:
                    if not file_id:
                        raise RuntimeError(
                            f"Unsupported broadcast type: {content_type}"
                        )

                    telegram_file = await callback.bot.get_file(file_id)
                    buffer = BytesIO()
                    await callback.bot.download_file(
                        telegram_file.file_path,
                        buffer,
                    )
                    upload = BufferedInputFile(
                        buffer.getvalue(),
                        filename=file_name,
                    )

                    if content_type == "photo":
                        await customer_bot.send_photo(
                            chat_id=telegram_user_id,
                            photo=upload,
                            caption=caption,
                        )
                    elif content_type == "video":
                        await customer_bot.send_video(
                            chat_id=telegram_user_id,
                            video=upload,
                            caption=caption,
                        )
                    elif content_type == "document":
                        await customer_bot.send_document(
                            chat_id=telegram_user_id,
                            document=upload,
                            caption=caption,
                        )
                    elif content_type == "audio":
                        await customer_bot.send_audio(
                            chat_id=telegram_user_id,
                            audio=upload,
                            caption=caption,
                        )
                    elif content_type == "voice":
                        await customer_bot.send_voice(
                            chat_id=telegram_user_id,
                            voice=upload,
                        )
                    elif content_type == "animation":
                        await customer_bot.send_animation(
                            chat_id=telegram_user_id,
                            animation=upload,
                            caption=caption,
                        )
                    else:
                        raise RuntimeError(
                            f"Unsupported broadcast type: {content_type}"
                        )

                sent += 1
                await asyncio.sleep(0.10)

            except Exception as error:
                error_name = type(error).__name__
                error_text = str(error).lower()

                if (
                    error_name == "TelegramForbiddenError"
                    or "bot was blocked by the user" in error_text
                    or "user is deactivated" in error_text
                    or "chat not found" in error_text
                ):
                    blocked += 1
                    failed += 1

                    try:
                        (
                            supabase
                            .table("users")
                            .update({"is_blocked": True})
                            .eq("telegram_user_id", telegram_user_id)
                            .execute()
                        )
                    except Exception:
                        pass

                    print(
                        "BROADCAST USER BLOCKED:",
                        telegram_user_id,
                    )
                    continue

                retry_after = getattr(error, "retry_after", None)
                if retry_after is not None:
                    try:
                        await asyncio.sleep(float(retry_after))
                    except Exception:
                        await asyncio.sleep(2)

                    # Retry the same delivery once.
                    try:
                        if content_type == "text":
                            await customer_bot.send_message(
                                chat_id=telegram_user_id,
                                text=text_body or "",
                            )
                        else:
                            telegram_file = await callback.bot.get_file(file_id)
                            buffer = BytesIO()
                            await callback.bot.download_file(
                                telegram_file.file_path,
                                buffer,
                            )
                            upload = BufferedInputFile(
                                buffer.getvalue(),
                                filename=file_name,
                            )

                            if content_type == "photo":
                                await customer_bot.send_photo(
                                    chat_id=telegram_user_id,
                                    photo=upload,
                                    caption=caption,
                                )
                            elif content_type == "video":
                                await customer_bot.send_video(
                                    chat_id=telegram_user_id,
                                    video=upload,
                                    caption=caption,
                                )
                            elif content_type == "document":
                                await customer_bot.send_document(
                                    chat_id=telegram_user_id,
                                    document=upload,
                                    caption=caption,
                                )
                            elif content_type == "audio":
                                await customer_bot.send_audio(
                                    chat_id=telegram_user_id,
                                    audio=upload,
                                    caption=caption,
                                )
                            elif content_type == "voice":
                                await customer_bot.send_voice(
                                    chat_id=telegram_user_id,
                                    voice=upload,
                                )
                            elif content_type == "animation":
                                await customer_bot.send_animation(
                                    chat_id=telegram_user_id,
                                    animation=upload,
                                    caption=caption,
                                )

                        sent += 1
                        continue

                    except Exception as retry_error:
                        print(
                            "BROADCAST RETRY FAILED:",
                            telegram_user_id,
                            repr(retry_error),
                        )

                failed += 1
                print(
                    "BROADCAST SEND FAILED:",
                    telegram_user_id,
                    repr(error),
                )
    finally:
        await customer_bot.session.close()

    status = "completed"

    try:
        if broadcast_log_id:
            (
                supabase
                .table("broadcast_logs")
                .update({
                    "sent_count": sent,
                    "failed_count": failed,
                    "blocked_count": blocked,
                    "status": status,
                    "completed_at": datetime.now(
                        timezone.utc
                    ).isoformat(),
                })
                .eq("id", broadcast_log_id)
                .execute()
            )
    except Exception as error:
        print("BROADCAST LOG UPDATE ERROR:", repr(error))

    await write_audit_log(
        callback.from_user.id,
        "BROADCAST_COMPLETED",
        result="success" if failed == 0 else "partial",
        details={
            "audience": broadcast_target_label(audience, course_name),
            "total": total,
            "sent": sent,
            "failed": failed,
            "blocked": blocked,
        },
    )

    await create_admin_notification(
        "BROADCAST_COMPLETED",
        "Broadcast Completed",
        f"{sent}/{total} delivered; {failed} failed; {blocked} blocked.",
        severity="success" if failed == 0 else "warning",
        metadata={
            "total": total,
            "sent": sent,
            "failed": failed,
            "blocked": blocked,
        },
    )

    await state.clear()

    await callback.message.edit_text(
        "✅ BROADCAST COMPLETED\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        f"🎯 Audience: "
        f"{broadcast_target_label(audience, course_name)}\n"
        f"👥 Total: {total}\n"
        f"✅ Sent: {sent}\n"
        f"❌ Failed: {failed}\n"
        f"🚫 Blocked/Unavailable: {blocked}\n\n"
        "The broadcast has finished.",
        reply_markup=back_to_admin_menu(),
    )


@dp.callback_query(
    F.data == "broadcast_cancel",
)
async def broadcast_cancel_handler(
    callback: CallbackQuery,
    state: FSMContext,
):
    if not is_admin(callback.from_user.id):
        await deny_access(callback=callback)
        return

    await callback.answer("Broadcast cancelled.")
    await state.clear()

    await callback.message.edit_text(
        "❌ Broadcast cancelled.",
        reply_markup=back_to_admin_menu(),
    )


# ============================================================

# ============================================================
# ADMIN NOTIFICATION CENTER
# ============================================================

async def create_admin_notification(
    notification_type: str,
    title: str,
    message: str,
    severity: str = "info",
    admin_telegram_id: int | None = None,
    metadata: dict | None = None,
):
    target = admin_telegram_id
    if target is None and ADMIN_TELEGRAM_ID:
        target = int(ADMIN_TELEGRAM_ID)
    if not target:
        return None
    try:
        response = (
            supabase.table("admin_notifications")
            .insert({
                "admin_telegram_id": int(target),
                "notification_type": notification_type,
                "title": title,
                "message": message,
                "severity": severity,
                "metadata": metadata or {},
            })
            .execute()
        )
        return response.data[0] if response.data else None
    except Exception as error:
        print("ADMIN NOTIFICATION CREATE ERROR:", repr(error))
        return None


def notification_icon(severity: str) -> str:
    return {
        "info": "ℹ️",
        "success": "✅",
        "warning": "⚠️",
        "error": "🚨",
    }.get(severity, "ℹ️")


def notification_center_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Mark All Read",
                    callback_data="notifications_mark_all_read",
                ),
                InlineKeyboardButton(
                    text="🗑️ Clear Read",
                    callback_data="notifications_clear_read",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🔄 Refresh",
                    callback_data="notifications",
                ),
                InlineKeyboardButton(
                    text="🔙 Admin Panel",
                    callback_data="admin_panel",
                ),
            ],
        ]
    )


async def notification_center_text() -> str:
    try:
        response = (
            supabase.table("admin_notifications")
            .select(
                "id,notification_type,title,message,severity,"
                "is_read,created_at"
            )
            .eq("admin_telegram_id", int(ADMIN_TELEGRAM_ID))
            .order("created_at", desc=True)
            .limit(20)
            .execute()
        )
        rows = response.data or []
        unread = sum(1 for row in rows if not row.get("is_read"))

        lines = [
            "🔔 NOTIFICATION CENTER",
            "━━━━━━━━━━━━━━━━━━━━",
            "",
            f"🔴 Unread: {unread}",
            f"📋 Latest: {len(rows)}",
            "",
        ]

        if not rows:
            lines.append("🎉 No notifications yet.")
            return "\n".join(lines)

        for row in rows:
            mark = "🔴" if not row.get("is_read") else "✓"
            icon = notification_icon(row.get("severity") or "info")
            created = str(row.get("created_at") or "")
            created = created.replace("T", " ")[:16]
            lines.extend([
                f"{mark} {icon} {row.get('title') or 'Notification'}",
                f"   {row.get('message') or ''}",
                f"   🕐 {created} UTC",
                "",
            ])
        return "\n".join(lines)

    except Exception as error:
        print("NOTIFICATION CENTER READ ERROR:", repr(error))
        return "❌ NOTIFICATION CENTER\n\nCould not load notifications."


@dp.callback_query(F.data == "notifications")
async def notifications_handler(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await deny_access(callback=callback)
        return

    try:
        await safe_callback_answer(callback)

        text = await notification_center_text()
        keyboard = notification_center_keyboard()

        try:
            await callback.message.edit_text(
                text,
                reply_markup=keyboard,
            )
        except Exception as edit_error:
            # Telegram raises "message is not modified" when the user
            # opens/refreshes an already identical Notification Center.
            # This is harmless and must NOT show an error screen.
            if "message is not modified" in str(edit_error).lower():
                return
            raise

    except Exception as error:
        print("NOTIFICATION CENTER ERROR:", repr(error))

        # Only show the error screen for a real loading/editing failure.
        try:
            await callback.message.edit_text(
                "❌ Could not load Notification Center.\n\n"
                "Please press 🔄 Refresh and try again.",
                reply_markup=notification_center_keyboard(),
            )
        except Exception as fallback_error:
            if "message is not modified" not in str(fallback_error).lower():
                print(
                    "NOTIFICATION CENTER FALLBACK ERROR:",
                    repr(fallback_error),
                )


@dp.callback_query(F.data == "notifications_mark_all_read")
async def notifications_mark_all_read_handler(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await deny_access(callback=callback)
        return
    try:
        (
            supabase.table("admin_notifications")
            .update({
                "is_read": True,
                "read_at": datetime.now(timezone.utc).isoformat(),
            })
            .eq("admin_telegram_id", int(callback.from_user.id))
            .eq("is_read", False)
            .execute()
        )
        await safe_callback_answer(callback, "All notifications marked read.")
        await callback.message.edit_text(
            await notification_center_text(),
            reply_markup=notification_center_keyboard(),
        )
    except Exception as error:
        print("NOTIFICATIONS MARK READ ERROR:", repr(error))
        await safe_callback_answer(
            callback,
            "Could not update notifications.",
            show_alert=True,
        )


@dp.callback_query(F.data == "notifications_clear_read")
async def notifications_clear_read_handler(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await deny_access(callback=callback)
        return
    try:
        (
            supabase.table("admin_notifications")
            .delete()
            .eq("admin_telegram_id", int(callback.from_user.id))
            .eq("is_read", True)
            .execute()
        )
        await safe_callback_answer(callback, "Read notifications cleared.")
        await callback.message.edit_text(
            await notification_center_text(),
            reply_markup=notification_center_keyboard(),
        )
    except Exception as error:
        print("NOTIFICATIONS CLEAR ERROR:", repr(error))
        await safe_callback_answer(
            callback,
            "Could not clear notifications.",
            show_alert=True,
        )


# SETTINGS & SYSTEM CONTROLS
# ============================================================

DEFAULT_SYSTEM_SETTINGS = {
    "broadcast_enabled": "true",
    "expiry_alerts_enabled": "true",
    "payment_alerts_enabled": "true",
    "maintenance_mode": "false",
    "expiry_warning_days": "7,3,1",
}


def get_system_setting(key: str, default=None):
    try:
        response = (
            supabase
            .table("system_settings")
            .select("value")
            .eq("key", key)
            .limit(1)
            .execute()
        )
        if response.data:
            return response.data[0].get("value", default)
    except Exception as error:
        print("SYSTEM SETTING READ ERROR:", key, repr(error))

    if default is not None:
        return default
    return DEFAULT_SYSTEM_SETTINGS.get(key)


def system_setting_enabled(key: str) -> bool:
    return str(
        get_system_setting(key, "false")
    ).strip().lower() == "true"


def set_system_setting(
    key: str,
    value: str,
    admin_id: int,
):
    (
        supabase
        .table("system_settings")
        .upsert({
            "key": key,
            "value": str(value),
            "updated_at": datetime.now(
                timezone.utc
            ).isoformat(),
            "updated_by": int(admin_id),
        })
        .execute()
    )


def settings_keyboard() -> InlineKeyboardMarkup:
    broadcast = (
        "🟢 ON"
        if system_setting_enabled("broadcast_enabled")
        else "🔴 OFF"
    )
    expiry = (
        "🟢 ON"
        if system_setting_enabled("expiry_alerts_enabled")
        else "🔴 OFF"
    )
    payment = (
        "🟢 ON"
        if system_setting_enabled("payment_alerts_enabled")
        else "🔴 OFF"
    )
    maintenance = (
        "🔴 ON"
        if system_setting_enabled("maintenance_mode")
        else "🟢 OFF"
    )

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"📢 Broadcast {broadcast}",
                    callback_data="setting_toggle_broadcast",
                )
            ],
            [
                InlineKeyboardButton(
                    text=f"⏰ Expiry Alerts {expiry}",
                    callback_data="setting_toggle_expiry",
                )
            ],
            [
                InlineKeyboardButton(
                    text=f"💳 Payment Alerts {payment}",
                    callback_data="setting_toggle_payment",
                )
            ],
            [
                InlineKeyboardButton(
                    text=f"🛠️ Maintenance {maintenance}",
                    callback_data="setting_toggle_maintenance",
                )
            ],
            [
                InlineKeyboardButton(
                    text="📊 System Status",
                    callback_data="settings_status",
                )
            ],
            [
                InlineKeyboardButton(
                    text="🔙 Admin Panel",
                    callback_data="admin_panel",
                )
            ],
        ]
    )


def settings_text() -> str:
    broadcast = (
        "🟢 ON"
        if system_setting_enabled("broadcast_enabled")
        else "🔴 OFF"
    )
    expiry = (
        "🟢 ON"
        if system_setting_enabled("expiry_alerts_enabled")
        else "🔴 OFF"
    )
    payment = (
        "🟢 ON"
        if system_setting_enabled("payment_alerts_enabled")
        else "🔴 OFF"
    )
    maintenance = (
        "🔴 ON"
        if system_setting_enabled("maintenance_mode")
        else "🟢 OFF"
    )
    days = get_system_setting(
        "expiry_warning_days",
        "7,3,1",
    )

    return (
        "⚙️ SYSTEM SETTINGS\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📢 Broadcast: {broadcast}\n"
        f"⏰ Expiry Alerts: {expiry}\n"
        f"💳 Payment Alerts: {payment}\n"
        f"🛠️ Maintenance Mode: {maintenance}\n"
        f"📅 Expiry Warnings: {days} days\n\n"
        "Settings are stored in Supabase."
    )


async def refresh_settings(callback: CallbackQuery):
    await callback.message.edit_text(
        settings_text(),
        reply_markup=settings_keyboard(),
    )


@dp.callback_query(F.data == "settings")
async def settings_handler(
    callback: CallbackQuery,
):
    if not is_admin(callback.from_user.id):
        await deny_access(callback=callback)
        return

    await safe_callback_answer(callback)

    try:
        await callback.message.edit_text(
            settings_text(),
            reply_markup=settings_keyboard(),
        )
    except Exception as error:
        print("SETTINGS OPEN ERROR:", repr(error))
        await callback.message.edit_text(
            "❌ Could not load settings.",
            reply_markup=back_to_admin_menu(),
        )


@dp.callback_query(F.data == "setting_toggle_broadcast")
async def setting_toggle_broadcast_handler(
    callback: CallbackQuery,
):
    if not is_admin(callback.from_user.id):
        await deny_access(callback=callback)
        return

    current = system_setting_enabled(
        "broadcast_enabled"
    )

    try:
        set_system_setting(
            "broadcast_enabled",
            "false" if current else "true",
            callback.from_user.id,
        )
        await safe_callback_answer(
            callback,
            "Broadcast setting updated.",
        )
        await refresh_settings(callback)
    except Exception as error:
        print("BROADCAST SETTING ERROR:", repr(error))
        await safe_callback_answer(
            callback,
            "Could not update setting.",
            show_alert=True,
        )


@dp.callback_query(F.data == "setting_toggle_expiry")
async def setting_toggle_expiry_handler(
    callback: CallbackQuery,
):
    if not is_admin(callback.from_user.id):
        await deny_access(callback=callback)
        return

    current = system_setting_enabled(
        "expiry_alerts_enabled"
    )

    try:
        set_system_setting(
            "expiry_alerts_enabled",
            "false" if current else "true",
            callback.from_user.id,
        )
        await safe_callback_answer(
            callback,
            "Expiry alert setting updated.",
        )
        await refresh_settings(callback)
    except Exception as error:
        print("EXPIRY SETTING ERROR:", repr(error))
        await safe_callback_answer(
            callback,
            "Could not update setting.",
            show_alert=True,
        )


@dp.callback_query(F.data == "setting_toggle_payment")
async def setting_toggle_payment_handler(
    callback: CallbackQuery,
):
    if not is_admin(callback.from_user.id):
        await deny_access(callback=callback)
        return

    current = system_setting_enabled(
        "payment_alerts_enabled"
    )

    try:
        set_system_setting(
            "payment_alerts_enabled",
            "false" if current else "true",
            callback.from_user.id,
        )
        await safe_callback_answer(
            callback,
            "Payment alert setting updated.",
        )
        await refresh_settings(callback)
    except Exception as error:
        print("PAYMENT SETTING ERROR:", repr(error))
        await safe_callback_answer(
            callback,
            "Could not update setting.",
            show_alert=True,
        )


@dp.callback_query(F.data == "setting_toggle_maintenance")
async def setting_toggle_maintenance_handler(
    callback: CallbackQuery,
):
    if not is_admin(callback.from_user.id):
        await deny_access(callback=callback)
        return

    current = system_setting_enabled(
        "maintenance_mode"
    )

    try:
        set_system_setting(
            "maintenance_mode",
            "false" if current else "true",
            callback.from_user.id,
        )
        await safe_callback_answer(
            callback,
            "Maintenance mode updated.",
        )
        await refresh_settings(callback)
    except Exception as error:
        print("MAINTENANCE SETTING ERROR:", repr(error))
        await safe_callback_answer(
            callback,
            "Could not update setting.",
            show_alert=True,
        )


@dp.callback_query(F.data == "settings_status")
async def settings_status_handler(
    callback: CallbackQuery,
):
    if not is_admin(callback.from_user.id):
        await deny_access(callback=callback)
        return

    await safe_callback_answer(callback)

    try:
        supabase_ok = False

        try:
            (
                supabase
                .table("system_settings")
                .select("key")
                .limit(1)
                .execute()
            )
            supabase_ok = True
        except Exception:
            supabase_ok = False

        admin_bot_status = (
            "🟢 Configured"
            if TOKEN
            else "🔴 Missing"
        )

        customer_bot_status = (
            "🟢 Configured"
            if CUSTOMER_BOT_TOKEN
            else "🔴 Missing"
        )

        db_status = (
            "🟢 Connected"
            if supabase_ok
            else "🔴 Error"
        )

        maintenance = (
            "🔴 ON"
            if system_setting_enabled(
                "maintenance_mode"
            )
            else "🟢 OFF"
        )

        await callback.message.edit_text(
            "📊 SYSTEM STATUS\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            f"👑 Admin Bot: {admin_bot_status}\n"
            f"🤖 Customer Bot: {customer_bot_status}\n"
            f"🗄️ Supabase: {db_status}\n"
            f"🛠️ Maintenance: {maintenance}\n\n"
            "⏰ Expiry Worker runs separately.",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="⚙️ Settings",
                            callback_data="settings",
                        )
                    ],
                    [
                        InlineKeyboardButton(
                            text="🔙 Admin Panel",
                            callback_data="admin_panel",
                        )
                    ],
                ]
            ),
        )

    except Exception as error:
        print("SYSTEM STATUS ERROR:", repr(error))

        await callback.message.edit_text(
            "❌ Could not load system status.",
            reply_markup=back_to_admin_menu(),
        )


# ============================================================
# SUBSCRIBERS PLACEHOLDER
# ============================================================

@dp.callback_query(F.data.startswith("subscribers_"))
async def subscribers_handler(
    callback: CallbackQuery,
):
    if not is_admin(callback.from_user.id):
        await deny_access(callback=callback)
        return

    await safe_callback_answer(callback)

    await callback.message.edit_text(
        "👥 SUBSCRIBERS\n\n"
        "Subscriber management will be added "
        "after the customer subscription flow.",
        reply_markup=back_to_admin_menu(),
    )


# ============================================================
# MAIN
# ============================================================

async def main():

    if not TOKEN:
        raise RuntimeError(
            "ADMIN_BOT_TOKEN is missing in .env"
        )

    if not ADMIN_TELEGRAM_ID:
        raise RuntimeError(
            "ADMIN_TELEGRAM_ID is missing in .env"
        )

    bot = Bot(
        token=TOKEN
    )

    print("=" * 50)
    print("✅ ADMIN BOT IS RUNNING")
    print("=" * 50)
    print("🔐 Admin authentication enabled.")
    print("📚 Course management enabled.")
    print("🔐 Group connection enabled.")
    print("💳 Plan management enabled.")
    print("📲 QR upload enabled.")
    print("🗄️ Supabase connected.")
    print("=" * 50)

    try:
        await dp.start_polling(bot)

    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
