import asyncio
import os
import secrets
from io import BytesIO

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart
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

TOKEN = os.getenv("CUSTOMER_BOT_TOKEN")
ADMIN_TELEGRAM_ID = os.getenv("ADMIN_TELEGRAM_ID")

QR_BUCKET = "payment-qr"

dp = Dispatcher()


# ============================================================
# PAYMENT STATE
# ============================================================

class PaymentStates(StatesGroup):
    waiting_for_screenshot = State()


# ============================================================
# HELPERS
# ============================================================

def money(value) -> str:
    try:
        return f"₹{float(value):.2f}"
    except Exception:
        return f"₹{value}"


def duration_text(plan: dict) -> str:

    if plan.get("plan_type") == "lifetime":
        return "Lifetime"

    days = plan.get("duration_days")

    if days:
        return f"{days} days"

    return "Not specified"


# ============================================================
# USER REGISTER / UPDATE
# ============================================================

def ensure_user(user) -> dict:

    telegram_user_id = user.id

    try:

        existing = (
            supabase
            .table("users")
            .select(
                "id, telegram_user_id, username, "
                "first_name, last_name, "
                "language_code, is_blocked"
            )
            .eq(
                "telegram_user_id",
                telegram_user_id,
            )
            .limit(1)
            .execute()
        )

        username = user.username
        first_name = user.first_name
        last_name = user.last_name
        language_code = user.language_code

        if existing.data:

            user_row = existing.data[0]

            updated = (
                supabase
                .table("users")
                .update(
                    {
                        "username": username,
                        "first_name": first_name,
                        "last_name": last_name,
                        "language_code": language_code,
                        "last_seen_at": "now()",
                    }
                )
                .eq(
                    "id",
                    user_row["id"],
                )
                .execute()
            )

            if updated.data:
                return updated.data[0]

            return user_row

        created = (
            supabase
            .table("users")
            .insert(
                {
                    "telegram_user_id": telegram_user_id,
                    "username": username,
                    "first_name": first_name,
                    "last_name": last_name,
                    "language_code": language_code,
                    "is_blocked": False,
                }
            )
            .execute()
        )

        if not created.data:
            raise RuntimeError(
                "Could not create user."
            )

        return created.data[0]

    except Exception as error:

        print(
            "USER REGISTER ERROR:",
            repr(error),
        )

        raise


# ============================================================
# CUSTOMER ACCESS / MENU LOCK
# ============================================================

def customer_has_unlocked_menu(user_uuid: str) -> bool:
    """Return True only after an approved payment exists.

    Existing active/lifetime subscriptions are also treated as unlocked
    so previously approved customers keep their full menu.
    """
    try:
        approved = (
            supabase
            .table("payment_requests")
            .select("id")
            .eq("user_id", user_uuid)
            .eq("status", "approved")
            .limit(1)
            .execute()
        )
        if approved.data:
            return True

        active = (
            supabase
            .table("subscriptions")
            .select("id")
            .eq("user_id", user_uuid)
            .eq("status", "active")
            .limit(1)
            .execute()
        )
        return bool(active.data)
    except Exception as error:
        print("MENU ACCESS CHECK ERROR:", repr(error))
        # Fail closed: an unverified customer gets only View All Courses.
        return False


def locked_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📚 View All Courses",
                    callback_data="all_courses",
                )
            ]
        ]
    )


def main_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📚 All Courses",
                    callback_data="all_courses",
                )
            ],
            [
                InlineKeyboardButton(
                    text="🎓 My Courses",
                    callback_data="my_courses",
                )
            ],
            [
                InlineKeyboardButton(
                    text="💳 My Payments",
                    callback_data="my_payments",
                ),
                InlineKeyboardButton(
                    text="👤 My Account",
                    callback_data="my_account",
                ),
            ],
        ]
    )


# ============================================================
# BACK TO MAIN MENU
# ============================================================

def back_main_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🔙 Main Menu",
                    callback_data="main_menu",
                )
            ]
        ]
    )


# ============================================================
# COURSE KEYBOARD
# ============================================================

def courses_keyboard(
    courses: list,
) -> InlineKeyboardMarkup:

    buttons = []

    for course in courses:

        buttons.append(
            [
                InlineKeyboardButton(
                    text=f"🎓 {course['name']}",
                    callback_data=(
                        f"course_{course['id']}"
                    ),
                )
            ]
        )

    buttons.append(
        [
            InlineKeyboardButton(
                text="🔙 Main Menu",
                callback_data="main_menu",
            )
        ]
    )

    return InlineKeyboardMarkup(
        inline_keyboard=buttons
    )


# ============================================================
# PLAN KEYBOARD
# ============================================================

def plans_keyboard(
    plans: list,
) -> InlineKeyboardMarkup:

    buttons = []

    for plan in plans:

        buttons.append(
            [
                InlineKeyboardButton(
                    text=(
                        f"💳 {plan['name']} — "
                        f"{money(plan['price'])} "
                        f"({duration_text(plan)})"
                    ),
                    callback_data=(
                        f"buy_{plan['id']}"
                    ),
                )
            ]
        )

    buttons.append(
        [
            InlineKeyboardButton(
                text="📚 All Courses",
                callback_data="all_courses",
            )
        ]
    )

    buttons.append(
        [
            InlineKeyboardButton(
                text="🔙 Main Menu",
                callback_data="main_menu",
            )
        ]
    )

    return InlineKeyboardMarkup(
        inline_keyboard=buttons
    )


# ============================================================
# RENEWAL PLAN KEYBOARD
# ============================================================

def renewal_plans_keyboard(
    plans: list,
    course_id: str,
) -> InlineKeyboardMarkup:

    buttons = []

    for plan in plans:

        buttons.append(
            [
                InlineKeyboardButton(
                    text=(
                        f"🔄 {plan['name']} — "
                        f"{money(plan['price'])} "
                        f"({duration_text(plan)})"
                    ),
                    callback_data=(
                        f"renew_buy_{plan['id']}"
                    ),
                )
            ]
        )

    buttons.append(
        [
            InlineKeyboardButton(
                text="🔙 My Courses",
                callback_data="my_courses",
            )
        ]
    )

    buttons.append(
        [
            InlineKeyboardButton(
                text="🔙 Main Menu",
                callback_data="main_menu",
            )
        ]
    )

    return InlineKeyboardMarkup(
        inline_keyboard=buttons
    )


# ============================================================
# START
# ============================================================

@dp.message(CommandStart())
async def start_handler(
    message: Message,
    state: FSMContext,
):

    await state.clear()

    try:

        user_row = ensure_user(
            message.from_user
        )

        if user_row.get("is_blocked"):

            await message.answer(
                "🚫 Your account is blocked.\n\n"
                "Please contact the administrator."
            )

            return

    except Exception as error:

        print(
            "Start user error:",
            repr(error),
        )

        await message.answer(
            "⚠️ Could not connect to the account "
            "system.\n\n"
            "Please try again."
        )

        return

    unlocked = customer_has_unlocked_menu(user_row["id"])

    await message.answer(
        "👋 Welcome!\n\n"
        "🎓 PREMIUM COURSES\n\n"
        "Choose what you want to do:" if unlocked else
        "👋 Welcome!\n\n"
        "🎓 PREMIUM COURSES\n\n"
        "Please view our available courses:",
        reply_markup=main_menu() if unlocked else locked_menu(),
    )


# ============================================================
# MAIN MENU
# ============================================================

@dp.callback_query(
    F.data == "main_menu"
)
async def main_menu_handler(
    callback: CallbackQuery,
    state: FSMContext,
):

    await callback.answer()

    await state.clear()

    try:

        user_row = ensure_user(
            callback.from_user
        )

        if user_row.get("is_blocked"):

            await callback.message.answer(
                "🚫 Your account is blocked."
            )

            return

    except Exception as error:

        print(
            "Main menu user error:",
            repr(error),
        )

    unlocked = customer_has_unlocked_menu(user_row["id"])

    try:
        await callback.message.edit_text(
            "👋 Welcome!\n\n"
            "🎓 PREMIUM COURSES\n\n"
            "Choose what you want to do:" if unlocked else
            "👋 Welcome!\n\n"
            "🎓 PREMIUM COURSES\n\n"
            "Please view our available courses:",
            reply_markup=main_menu() if unlocked else locked_menu(),
        )
    except Exception as error:
        if "message is not modified" not in str(error).lower():
            print("MAIN MENU ERROR:", repr(error))


# ============================================================
# ALL COURSES
# ============================================================

@dp.callback_query(
    F.data == "all_courses"
)
async def all_courses_handler(
    callback: CallbackQuery,
):

    await callback.answer()

    try:

        response = (
            supabase
            .table("courses")
            .select(
                "id, name, description, status"
            )
            .eq(
                "status",
                "active",
            )
            .order(
                "sort_order"
            )
            .order(
                "created_at"
            )
            .execute()
        )

        courses = response.data or []

        if not courses:

            await callback.message.edit_text(
                "📚 ALL COURSES\n\n"
                "No courses are available right now.",
                reply_markup=back_main_menu(),
            )

            return

        await callback.message.edit_text(
            "📚 ALL COURSES\n\n"
            "Select the course you want:",
            reply_markup=courses_keyboard(
                courses
            ),
        )

    except Exception as error:

        print(
            "ALL COURSES ERROR:",
            repr(error),
        )

        await callback.message.edit_text(
            "❌ Could not load courses.\n\n"
            "Please try again.",
            reply_markup=back_main_menu(),
        )


# ============================================================
# COURSE -> PLANS
# ============================================================

@dp.callback_query(
    F.data.startswith("course_")
)
async def course_handler(
    callback: CallbackQuery,
):

    await callback.answer()

    course_id = callback.data.replace(
        "course_",
        "",
        1,
    )

    try:

        course_response = (
            supabase
            .table("courses")
            .select(
                "id, name, description, status"
            )
            .eq(
                "id",
                course_id,
            )
            .limit(1)
            .execute()
        )

        if not course_response.data:

            await callback.message.edit_text(
                "❌ Course not found.",
                reply_markup=back_main_menu(),
            )

            return

        course = course_response.data[0]

        plans_response = (
            supabase
            .table("plans")
            .select(
                "id, course_id, name, plan_type, "
                "price, currency, duration_days, "
                "description, qr_code_path, is_active"
            )
            .eq(
                "course_id",
                course_id,
            )
            .eq(
                "is_active",
                True,
            )
            .order(
                "sort_order"
            )
            .order(
                "created_at"
            )
            .execute()
        )

        plans = plans_response.data or []

        if not plans:

            await callback.message.edit_text(
                f"🎓 {course['name']}\n\n"
                f"📝 "
                f"{course.get('description') or 'Premium course'}"
                "\n\n"
                "⚠️ No plans are available "
                "for this course right now.",
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            InlineKeyboardButton(
                                text="📚 All Courses",
                                callback_data="all_courses",
                            )
                        ],
                        [
                            InlineKeyboardButton(
                                text="🔙 Main Menu",
                                callback_data="main_menu",
                            )
                        ],
                    ]
                ),
            )

            return

        await callback.message.edit_text(
            f"🎓 {course['name']}\n\n"
            f"📝 "
            f"{course.get('description') or 'Premium course'}"
            "\n\n"
            "💳 SELECT YOUR PLAN\n\n"
            "Choose a subscription plan:",
            reply_markup=plans_keyboard(
                plans
            ),
        )

    except Exception as error:

        print(
            "COURSE ERROR:",
            repr(error),
        )

        await callback.message.edit_text(
            "❌ Could not load this course.",
            reply_markup=back_main_menu(),
        )


# ============================================================
# BUY PLAN / QR
# ============================================================

@dp.callback_query(
    F.data.startswith("buy_")
)
async def buy_plan_handler(
    callback: CallbackQuery,
    state: FSMContext,
    plan_id_override: str | None = None,
):

    await callback.answer()

    plan_id = (
        plan_id_override
        if plan_id_override
        else callback.data.replace(
            "buy_",
            "",
            1,
        )
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
            .eq(
                "id",
                plan_id,
            )
            .limit(1)
            .execute()
        )

        if not response.data:

            await callback.message.edit_text(
                "❌ Plan not found.",
                reply_markup=back_main_menu(),
            )

            return

        plan = response.data[0]

        if not plan.get("is_active"):

            await callback.message.edit_text(
                "⚠️ This plan is currently unavailable.",
                reply_markup=back_main_menu(),
            )

            return

        # Final customer-side protection: never start a new payment
        # for a course that already has an active lifetime subscription.
        user_row = ensure_user(callback.from_user)
        active_subscription = get_active_subscription_for_course(
            user_row["id"],
            plan["course_id"],
        )

        if active_subscription and active_subscription.get("is_lifetime"):
            await callback.message.edit_text(
                "♾️ LIFETIME ACCESS ALREADY ACTIVE\n\n"
                "You already own permanent access to this course.\n\n"
                "No additional payment is required.",
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            InlineKeyboardButton(
                                text="🎓 My Courses",
                                callback_data="my_courses",
                            )
                        ],
                        [
                            InlineKeyboardButton(
                                text="🔙 Main Menu",
                                callback_data="main_menu",
                            )
                        ],
                    ]
                ),
            )
            return

        qr_path = plan.get(
            "qr_code_path"
        )

        if not qr_path:

            await callback.message.edit_text(
                "❌ Payment QR is not configured "
                "for this plan.",
                reply_markup=back_main_menu(),
            )

            return

        course_response = (
            supabase
            .table("courses")
            .select(
                "id, name, description"
            )
            .eq(
                "id",
                plan["course_id"],
            )
            .limit(1)
            .execute()
        )

        if not course_response.data:

            await callback.message.edit_text(
                "❌ Course not found.",
                reply_markup=back_main_menu(),
            )

            return

        course = course_response.data[0]

        # Save payment session
        await state.update_data(
            plan_id=plan["id"],
            course_id=plan["course_id"],
            plan_name=plan["name"],
            course_name=course["name"],
            amount=plan["price"],
            plan_type=plan["plan_type"],
            duration_days=plan["duration_days"],
        )

        print(
            "Downloading QR:",
            qr_path,
        )

        qr_bytes = (
            supabase
            .storage
            .from_(QR_BUCKET)
            .download(qr_path)
        )

        if not qr_bytes:

            raise RuntimeError(
                "QR download returned empty data."
            )

        print(
            "QR downloaded:",
            len(qr_bytes),
            "bytes",
        )

        qr_file = BufferedInputFile(
            qr_bytes,
            filename="payment_qr.jpg",
        )

        await callback.message.answer_photo(
            photo=qr_file,
            caption=(
                "💳 PAYMENT DETAILS\n\n"
                f"🎓 Course:\n"
                f"{course['name']}\n\n"
                f"💎 Plan:\n"
                f"{plan['name']}\n\n"
                f"💰 EXACT AMOUNT:\n"
                f"{money(plan['price'])}\n\n"
                f"📅 VALIDITY:\n"
                f"{duration_text(plan)}\n\n"
                "📲 Scan the QR code above.\n\n"
                f"⚠️ Pay exactly "
                f"{money(plan['price'])}.\n"
                "Do not change the amount.\n\n"
                "After payment, press:\n"
                "📸 SEND PAYMENT SCREENSHOT"
            ),
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="📸 SEND PAYMENT SCREENSHOT",
                            callback_data=(
                                "send_payment_screenshot"
                            ),
                        )
                    ],
                    [
                        InlineKeyboardButton(
                            text="🔙 Back to Plans",
                            callback_data=(
                                f"course_{plan['course_id']}"
                            ),
                        )
                    ],
                    [
                        InlineKeyboardButton(
                            text="❌ Cancel",
                            callback_data="main_menu",
                        )
                    ],
                ]
            ),
        )

        await callback.message.delete()

    except Exception as error:

        print(
            "BUY PLAN / QR ERROR:",
            repr(error),
        )

        await callback.message.edit_text(
            "❌ Payment QR could not be loaded.\n\n"
            "Please contact the administrator.",
            reply_markup=back_main_menu(),
        )


# ============================================================
# SCREENSHOT BUTTON
# ============================================================

@dp.callback_query(
    F.data == "send_payment_screenshot"
)
async def send_payment_screenshot_handler(
    callback: CallbackQuery,
    state: FSMContext,
):

    await callback.answer()

    data = await state.get_data()

    print(
        "Payment screenshot button clicked."
    )

    print(
        "Payment session:",
        data,
    )

    if not data.get("plan_id"):

        await state.clear()

        await callback.message.answer(
            "⚠️ Payment session expired.\n\n"
            "Please select the course and plan again.",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="📚 All Courses",
                            callback_data="all_courses",
                        )
                    ]
                ]
            ),
        )

        return

    await state.set_state(
        PaymentStates.waiting_for_screenshot
    )

    # IMPORTANT:
    # QR is a PHOTO message.
    # Therefore answer() is used instead of edit_text().

    await callback.message.answer(
        "📸 SEND PAYMENT SCREENSHOT\n\n"
        f"🎓 Course:\n"
        f"{data.get('course_name')}\n\n"
        f"💎 Plan:\n"
        f"{data.get('plan_name')}\n\n"
        f"💰 Amount:\n"
        f"{money(data.get('amount'))}\n\n"
        "Please send your payment screenshot "
        "as a PHOTO.\n\n"
        "⚠️ Make sure the payment details "
        "are clearly visible.",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="❌ Cancel",
                        callback_data="cancel_payment",
                    )
                ]
            ]
        ),
    )


# ============================================================
# RECEIVE SCREENSHOT
# ============================================================

@dp.message(
    PaymentStates.waiting_for_screenshot
)
async def payment_screenshot_handler(
    message: Message,
    state: FSMContext,
):

    print(
        "Payment screenshot received from:",
        message.from_user.id,
    )

    data = await state.get_data()

    if not data.get("plan_id"):

        await state.clear()

        await message.answer(
            "⚠️ Payment session expired.\n\n"
            "Please select the course again.",
            reply_markup=main_menu(),
        )

        return

    if not message.photo:

        await message.answer(
            "⚠️ Please send the payment screenshot "
            "as a PHOTO.\n\n"
            "Do not send it as text or document."
        )

        return

    try:

        user = message.from_user

        # ----------------------------------------------------
        # 1. GET / CREATE USER
        # ----------------------------------------------------

        user_row = ensure_user(
            user
        )

        if user_row.get("is_blocked"):

            await state.clear()

            await message.answer(
                "🚫 Your account is blocked."
            )

            return

        user_uuid = user_row["id"]

        print(
            "User UUID:",
            user_uuid,
        )

        # ----------------------------------------------------
        # 2. DOWNLOAD TELEGRAM SCREENSHOT
        # ----------------------------------------------------

        photo = message.photo[-1]

        telegram_file = (
            await message.bot.get_file(
                photo.file_id
            )
        )

        screenshot_buffer = BytesIO()

        await message.bot.download_file(
            telegram_file.file_path,
            destination=screenshot_buffer,
        )

        screenshot_bytes = (
            screenshot_buffer.getvalue()
        )

        if not screenshot_bytes:

            raise RuntimeError(
                "Screenshot download returned empty data."
            )

        print(
            "Screenshot downloaded:",
            len(screenshot_bytes),
            "bytes",
        )

        # ----------------------------------------------------
        # 3. GENERATE STORAGE PATH
        # ----------------------------------------------------

        payment_temp_id = (
            secrets.token_hex(6).upper()
        )

        screenshot_path = (
            f"payments/"
            f"{user.id}_"
            f"{payment_temp_id}.jpg"
        )

        # ----------------------------------------------------
        # 4. UPLOAD SCREENSHOT
        # ----------------------------------------------------

        try:

            (
                supabase
                .storage
                .from_(QR_BUCKET)
                .upload(
                    screenshot_path,
                    screenshot_bytes,
                    {
                        "content-type": "image/jpeg",
                        "upsert": "false",
                    },
                )
            )

            print(
                "Screenshot uploaded:",
                screenshot_path,
            )

        except Exception as storage_error:

            print(
                "SCREENSHOT STORAGE ERROR:",
                repr(storage_error),
            )

            raise RuntimeError(
                "Could not save payment screenshot."
            )

        # ----------------------------------------------------
        # 5. CREATE PAYMENT REQUEST
        # ----------------------------------------------------
        #
        # IMPORTANT:
        # payment_number is NOT inserted.
        # PostgreSQL generates it automatically.
        #
        # Exact columns from your schema:
        #
        # user_id
        # course_id
        # plan_id
        # amount
        # currency
        # status
        # screenshot_path
        # screenshot_file_id
        #

        payment_data = {
            "user_id": user_uuid,
            "course_id": data["course_id"],
            "plan_id": data["plan_id"],
            "amount": data["amount"],
            "currency": "INR",
            "status": "pending",
            "screenshot_path": screenshot_path,
            "screenshot_file_id": photo.file_id,
        }

        print(
            "Creating payment request..."
        )

        payment_response = (
            supabase
            .table("payment_requests")
            .insert(
                payment_data
            )
            .execute()
        )

        if not payment_response.data:

            raise RuntimeError(
                "Payment request was not created."
            )

        payment = payment_response.data[0]

        payment_number = payment.get(
            "payment_number"
        )

        payment_id = payment.get(
            "id"
        )

        print(
            "Payment created:",
            payment_number,
        )

        print(
            "Payment UUID:",
            payment_id,
        )

        # ----------------------------------------------------
        # 6. CLEAR PAYMENT STATE
        # ----------------------------------------------------

        await state.clear()

        # ----------------------------------------------------
        # 7. USER CONFIRMATION
        # ----------------------------------------------------

        await message.answer(
            "⏳ PAYMENT SUBMITTED\n\n"
            "Your payment screenshot has been "
            "submitted successfully.\n\n"
            f"🧾 Payment No:\n"
            f"{payment_number}\n\n"
            f"🎓 Course:\n"
            f"{data['course_name']}\n\n"
            f"💎 Plan:\n"
            f"{data['plan_name']}\n\n"
            f"💰 Amount:\n"
            f"{money(data['amount'])}\n\n"
            "⏳ Status:\n"
            "Pending Admin Verification\n\n"
            "You will receive a message after "
            "admin verification.",
            reply_markup=main_menu(),
        )

        # ----------------------------------------------------
        # 8. ADMIN NOTIFICATION
        # ----------------------------------------------------

        if ADMIN_TELEGRAM_ID:

            try:

                username_text = (
                    f"@{user.username}"
                    if user.username
                    else "Not set"
                )

                admin_text = (
                    "🔔 NEW PAYMENT REQUEST\n\n"
                    f"🧾 Payment No:\n"
                    f"{payment_number}\n\n"
                    f"🆔 Payment ID:\n"
                    f"{payment_id}\n\n"
                    f"👤 User:\n"
                    f"{user.full_name}\n\n"
                    f"🆔 Telegram ID:\n"
                    f"{user.id}\n\n"
                    f"👤 Username:\n"
                    f"{username_text}\n\n"
                    f"🎓 Course:\n"
                    f"{data['course_name']}\n\n"
                    f"💎 Plan:\n"
                    f"{data['plan_name']}\n\n"
                    f"💰 Amount:\n"
                    f"{money(data['amount'])}\n\n"
                    "⏳ STATUS: PENDING"
                )

                await message.bot.send_message(
                    chat_id=int(
                        ADMIN_TELEGRAM_ID
                    ),
                    text=admin_text,
                )

                screenshot_file = (
                    BufferedInputFile(
                        screenshot_bytes,
                        filename=(
                            f"payment_"
                            f"{payment_number}.jpg"
                        ),
                    )
                )

                await message.bot.send_photo(
                    chat_id=int(
                        ADMIN_TELEGRAM_ID
                    ),
                    photo=screenshot_file,
                    caption=(
                        "📸 PAYMENT SCREENSHOT\n\n"
                        f"Payment No:\n"
                        f"{payment_number}\n\n"
                        "Please verify this payment "
                        "from your payment app."
                    ),
                    reply_markup=InlineKeyboardMarkup(
                        inline_keyboard=[
                            [
                                InlineKeyboardButton(
                                    text="💳 Pending Payments",
                                    callback_data=(
                                        "admin_pending"
                                    ),
                                )
                            ]
                        ]
                    ),
                )

                print(
                    "Admin notification sent."
                )

            except Exception as admin_error:

                print(
                    "ADMIN NOTIFICATION ERROR:",
                    repr(admin_error),
                )

        else:

            print(
                "ADMIN_TELEGRAM_ID is not configured."
            )

    except Exception as error:

        print(
            "PAYMENT SCREENSHOT ERROR:",
            repr(error),
        )

        await message.answer(
            "❌ PAYMENT SUBMISSION FAILED\n\n"
            "Your screenshot could not be submitted.\n\n"
            "Please try again.",
            reply_markup=main_menu(),
        )


# ============================================================
# CANCEL PAYMENT
# ============================================================

@dp.callback_query(
    F.data == "cancel_payment"
)
async def cancel_payment_handler(
    callback: CallbackQuery,
    state: FSMContext,
):

    await callback.answer()

    await state.clear()

    await callback.message.answer(
        "❌ Payment process cancelled.",
        reply_markup=main_menu(),
    )


# ============================================================
# SUBSCRIPTION SAFETY
# ============================================================

def get_active_subscription_for_course(user_uuid: str, course_id: str):
    response = (
        supabase
        .table("subscriptions")
        .select(
            "id, status, plan_id, started_at, expires_at, "
            "is_lifetime"
        )
        .eq("user_id", user_uuid)
        .eq("course_id", course_id)
        .eq("status", "active")
        .order("created_at", desc=True)
        .limit(1)
        .execute()
    )
    return response.data[0] if response.data else None


# ============================================================
# RENEW COURSE
# ============================================================

@dp.callback_query(
    F.data.startswith("renew_course_")
)
async def renew_course_handler(
    callback: CallbackQuery,
):

    await callback.answer()

    course_id = callback.data.replace(
        "renew_course_",
        "",
        1,
    )

    try:
        user_row = ensure_user(callback.from_user)
        active_subscription = get_active_subscription_for_course(
            user_row["id"],
            course_id,
        )

        if active_subscription and active_subscription.get("is_lifetime"):
            await callback.message.edit_text(
                "♾️ LIFETIME ACCESS ALREADY ACTIVE\n\n"
                "You already have permanent access to this course.\n\n"
                "A renewal payment is not required.",
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            InlineKeyboardButton(
                                text="🎓 My Courses",
                                callback_data="my_courses",
                            )
                        ],
                        [
                            InlineKeyboardButton(
                                text="🔙 Main Menu",
                                callback_data="main_menu",
                            )
                        ],
                    ]
                ),
            )
            return

        course_response = (
            supabase
            .table("courses")
            .select("id, name, description, status")
            .eq("id", course_id)
            .limit(1)
            .execute()
        )

        if not course_response.data:

            await callback.message.edit_text(
                "❌ Course not found.",
                reply_markup=back_main_menu(),
            )
            return

        course = course_response.data[0]

        plans_response = (
            supabase
            .table("plans")
            .select(
                "id, course_id, name, plan_type, "
                "price, currency, duration_days, "
                "description, qr_code_path, is_active"
            )
            .eq("course_id", course_id)
            .eq("is_active", True)
            .eq("plan_type", "fixed")
            .order("sort_order")
            .order("created_at")
            .execute()
        )

        plans = plans_response.data or []

        if not plans:

            await callback.message.edit_text(
                f"🔄 RENEW COURSE\n\n"
                f"🎓 {course['name']}\n\n"
                "⚠️ No renewal plans are available "
                "for this course right now.",
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            InlineKeyboardButton(
                                text="🎓 My Courses",
                                callback_data="my_courses",
                            )
                        ],
                        [
                            InlineKeyboardButton(
                                text="🔙 Main Menu",
                                callback_data="main_menu",
                            )
                        ],
                    ]
                ),
            )
            return

        await callback.message.edit_text(
            f"🔄 RENEW COURSE\n\n"
            f"🎓 {course['name']}\n\n"
            "Choose a renewal plan:",
            reply_markup=renewal_plans_keyboard(
                plans,
                course_id,
            ),
        )

    except Exception as error:

        print(
            "RENEW COURSE ERROR:",
            repr(error),
        )

        await callback.message.edit_text(
            "❌ Could not load renewal plans.\n\n"
            "Please try again.",
            reply_markup=back_main_menu(),
        )


# ============================================================
# RENEWAL PLAN -> EXISTING PAYMENT FLOW
# ============================================================

@dp.callback_query(
    F.data.startswith("renew_buy_")
)
async def renew_buy_plan_handler(
    callback: CallbackQuery,
    state: FSMContext,
):

    await callback.answer()

    plan_id = callback.data.replace(
        "renew_buy_",
        "",
        1,
    )

    try:
        user_row = ensure_user(callback.from_user)
        plan_response = (
            supabase
            .table("plans")
            .select("id, course_id, plan_type, name")
            .eq("id", plan_id)
            .limit(1)
            .execute()
        )

        if not plan_response.data:
            await callback.message.edit_text(
                "❌ Renewal plan not found.",
                reply_markup=back_main_menu(),
            )
            return

        plan = plan_response.data[0]
        active_subscription = get_active_subscription_for_course(
            user_row["id"],
            plan["course_id"],
        )

        if active_subscription and active_subscription.get("is_lifetime"):
            await callback.message.edit_text(
                "♾️ LIFETIME ACCESS ALREADY ACTIVE\n\n"
                "You already have permanent access to this course.\n\n"
                "This renewal payment cannot be started.",
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            InlineKeyboardButton(
                                text="🎓 My Courses",
                                callback_data="my_courses",
                            )
                        ],
                        [
                            InlineKeyboardButton(
                                text="🔙 Main Menu",
                                callback_data="main_menu",
                            )
                        ],
                    ]
                ),
            )
            return

    except Exception as error:
        print("RENEWAL SAFETY CHECK ERROR:", repr(error))
        await callback.message.edit_text(
            "⚠️ Could not verify your existing course access.\n\n"
            "Please try again.",
            reply_markup=back_main_menu(),
        )
        return

    # Reuse the existing QR/payment flow without mutating
    # CallbackQuery. aiogram CallbackQuery is frozen.
    return await buy_plan_handler(
        callback,
        state,
        plan_id_override=plan_id,
    )


# ============================================================
# MY COURSES
# ============================================================

@dp.callback_query(
    F.data == "my_courses"
)
async def my_courses_handler(
    callback: CallbackQuery,
):
    await callback.answer()

    try:
        user_row = ensure_user(
            callback.from_user
        )

        user_uuid = user_row["id"]

        response = (
            supabase
            .table("subscriptions")
            .select(
                "id, course_id, plan_id, status, "
                "started_at, expires_at, is_lifetime, "
                "joined_channel_at, revoked_at, created_at"
            )
            .eq(
                "user_id",
                user_uuid,
            )
            .order(
                "created_at",
                desc=True,
            )
            .execute()
        )

        subscriptions = response.data or []

        if not subscriptions:
            await callback.message.edit_text(
                "🎓 MY COURSES\\n\\n"
                "You don't have any courses yet.",
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            InlineKeyboardButton(
                                text="📚 View All Courses",
                                callback_data="all_courses",
                            )
                        ],
                        [
                            InlineKeyboardButton(
                                text="🔙 Main Menu",
                                callback_data="main_menu",
                            )
                        ],
                    ]
                ),
            )
            return

        lines = ["🎓 MY COURSES", ""]

        for subscription in subscriptions:
            course_id = subscription.get("course_id")
            plan_id = subscription.get("plan_id")

            course_name = "Unknown Course"
            plan_name = "Unknown Plan"

            if course_id:
                course_response = (
                    supabase
                    .table("courses")
                    .select("name")
                    .eq("id", course_id)
                    .limit(1)
                    .execute()
                )

                if course_response.data:
                    course_name = course_response.data[0]["name"]

            if plan_id:
                plan_response = (
                    supabase
                    .table("plans")
                    .select("name, plan_type, duration_days")
                    .eq("id", plan_id)
                    .limit(1)
                    .execute()
                )

                if plan_response.data:
                    plan_name = (
                        plan_response.data[0].get("name")
                        or "Unknown Plan"
                    )

            status = subscription.get("status", "unknown")

            status_text = {
                "active": "🟢 Active",
                "expired": "⏰ Expired",
                "cancelled": "❌ Cancelled",
                "revoked": "🚫 Revoked",
                "pending": "🟡 Pending",
            }.get(status, str(status))

            if subscription.get("is_lifetime"):
                expiry_text = "♾️ Lifetime"
            elif subscription.get("expires_at"):
                expiry_text = str(subscription["expires_at"])
            else:
                expiry_text = "Not specified"

            started_text = (
                str(subscription["started_at"])
                if subscription.get("started_at")
                else "Not started"
            )

            lines.extend(
                [
                    f"🎓 {course_name}",
                    f"Status: {status_text}",
                    f"💎 Plan: {plan_name}",
                    f"📅 Started: {started_text}",
                    f"⏳ Expires: {expiry_text}",
                    "",
                ]
            )

        # Renewal is available directly from My Courses for every
        # non-lifetime subscription. Fetch the course name here so this
        # block is completely self-contained.
        keyboard_buttons = [
            [
                InlineKeyboardButton(
                    text="📚 View All Courses",
                    callback_data="all_courses",
                )
            ]
        ]

        added_renewal_buttons = set()

        for subscription in subscriptions:
            course_id = subscription.get("course_id")
            status = subscription.get("status")
            is_lifetime = bool(subscription.get("is_lifetime"))

            if (
                not course_id
                or is_lifetime
                or status not in {"active", "expired", "revoked", "cancelled"}
                or course_id in added_renewal_buttons
            ):
                continue

            course_name = "Course"

            course_response = (
                supabase
                .table("courses")
                .select("name")
                .eq("id", course_id)
                .limit(1)
                .execute()
            )

            if course_response.data:
                course_name = (
                    course_response.data[0].get("name")
                    or "Course"
                )

            keyboard_buttons.append(
                [
                    InlineKeyboardButton(
                        text=f"🔄 Renew {course_name[:40]}",
                        callback_data=f"renew_course_{course_id}",
                    )
                ]
            )

            added_renewal_buttons.add(course_id)

        keyboard_buttons.append(
            [
                InlineKeyboardButton(
                    text="🔙 Main Menu",
                    callback_data="main_menu",
                )
            ]
        )

        await callback.message.edit_text(
            "\n".join(lines),
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=keyboard_buttons
            ),
        )

    except Exception as error:
        print(
            "MY COURSES ERROR:",
            repr(error),
        )

        await callback.message.edit_text(
            "❌ Could not load your courses.\\n\\n"
            "Please try again.",
            reply_markup=back_main_menu(),
        )


# ============================================================
# MY PAYMENTS
# ============================================================

@dp.callback_query(
    F.data == "my_payments"
)
async def my_payments_handler(
    callback: CallbackQuery,
):
    await callback.answer()

    try:
        user_row = ensure_user(
            callback.from_user
        )

        user_uuid = user_row["id"]

        response = (
            supabase
            .table("payment_requests")
            .select(
                "id, payment_number, course_id, plan_id, "
                "amount, currency, status, submitted_at, "
                "reviewed_at, rejection_reason"
            )
            .eq(
                "user_id",
                user_uuid,
            )
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
                "💳 MY PAYMENTS\\n\\n"
                "No payment history found.",
                reply_markup=back_main_menu(),
            )
            return

        lines = ["💳 MY PAYMENTS", ""]

        for payment in payments:
            course_name = "Unknown Course"
            plan_name = "Unknown Plan"

            course_id = payment.get("course_id")
            plan_id = payment.get("plan_id")

            if course_id:
                course_response = (
                    supabase
                    .table("courses")
                    .select("name")
                    .eq("id", course_id)
                    .limit(1)
                    .execute()
                )

                if course_response.data:
                    course_name = course_response.data[0]["name"]

            if plan_id:
                plan_response = (
                    supabase
                    .table("plans")
                    .select("name")
                    .eq("id", plan_id)
                    .limit(1)
                    .execute()
                )

                if plan_response.data:
                    plan_name = (
                        plan_response.data[0].get("name")
                        or "Unknown Plan"
                    )

            status = payment.get("status", "unknown")

            status_text = {
                "pending": "🟡 Pending Admin Verification",
                "approved": "✅ Approved",
                "rejected": "❌ Rejected",
            }.get(status, str(status))

            lines.extend(
                [
                    f"🧾 Payment No: {payment.get('payment_number')}",
                    f"🎓 Course: {course_name}",
                    f"💎 Plan: {plan_name}",
                    f"💰 Amount: {money(payment.get('amount'))}",
                    f"📌 Status: {status_text}",
                    f"📅 Submitted: {payment.get('submitted_at') or 'Not available'}",
                ]
            )

            if status == "rejected":
                reason = payment.get("rejection_reason")
                if reason:
                    lines.append(
                        f"📝 Rejection reason: {reason}"
                    )

            lines.append("")

        await callback.message.edit_text(
            "\\n".join(lines),
            reply_markup=back_main_menu(),
        )

    except Exception as error:
        print(
            "MY PAYMENTS ERROR:",
            repr(error),
        )

        await callback.message.edit_text(
            "❌ Could not load payment history.\\n\\n"
            "Please try again.",
            reply_markup=back_main_menu(),
        )


# ============================================================
# MY ACCOUNT
# ============================================================

@dp.callback_query(
    F.data == "my_account"
)
async def my_account_handler(
    callback: CallbackQuery,
):

    await callback.answer()

    user = callback.from_user

    try:

        user_row = ensure_user(
            user
        )

        blocked = user_row.get(
            "is_blocked",
            False,
        )

    except Exception:

        blocked = False

    await callback.message.edit_text(
        "👤 MY ACCOUNT\n\n"
        f"Name:\n"
        f"{user.full_name}\n\n"
        f"Username:\n"
        f"@{user.username if user.username else 'Not set'}\n\n"
        f"Telegram ID:\n"
        f"{user.id}\n\n"
        f"Account Status:\n"
        f"{'🚫 Blocked' if blocked else '✅ Active'}",
        reply_markup=back_main_menu(),
    )


# ============================================================
# ADMIN PENDING SHORTCUT
# ============================================================

@dp.callback_query(
    F.data == "admin_pending"
)
async def admin_pending_handler(
    callback: CallbackQuery,
):

    await callback.answer()

    await callback.message.answer(
        "ℹ️ Open the Admin Bot and select:\n\n"
        "💳 Pending Payments"
    )


# ============================================================
# MAIN
# ============================================================

async def main():

    if not TOKEN:

        raise RuntimeError(
            "CUSTOMER_BOT_TOKEN is missing in .env"
        )

    bot = Bot(
        token=TOKEN
    )

    print(
        "========================================"
    )
    print(
        "✅ CUSTOMER BOT IS RUNNING"
    )
    print(
        "========================================"
    )
    print(
        "👤 User registration enabled."
    )
    print(
        "📚 Courses enabled."
    )
    print(
        "💳 Plans enabled."
    )
    print(
        "📲 Private QR enabled."
    )
    print(
        "📸 Screenshot upload enabled."
    )
    print(
        "💰 Payment requests enabled."
    )
    print(
        "👑 Admin notification enabled."
    )
    print(
        "🗄️ Supabase connected."
    )
    print(
        "========================================"
    )

    try:

        await dp.start_polling(bot)

    finally:

        await bot.session.close()


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":

    asyncio.run(main())