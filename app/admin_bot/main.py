import asyncio
import os

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart
from aiogram.types import (
    Message,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("ADMIN_BOT_TOKEN")

dp = Dispatcher()


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


def is_admin(user_id: int) -> bool:
    admin_id = os.getenv("ADMIN_TELEGRAM_ID")

    if not admin_id:
        return False

    return str(user_id) == str(admin_id)


async def deny_access(callback=None, message=None):
    text = (
        "🔒 ACCESS DENIED\n\n"
        "This bot is restricted to authorized administrators."
    )

    if callback:
        await callback.answer(
            "🔒 Access denied",
            show_alert=True,
        )

    if message:
        await message.answer(text)


@dp.message(CommandStart())
async def start_handler(message: Message):
    user_id = message.from_user.id

    if not is_admin(user_id):
        await deny_access(message=message)
        return

    await message.answer(
        "👑 ADMIN CONTROL CENTER\n\n"
        "Welcome, Admin.\n\n"
        "Choose an option:",
        reply_markup=admin_menu(),
    )


@dp.callback_query(F.data == "pending_payments")
async def pending_payments_handler(callback):
    if not is_admin(callback.from_user.id):
        await deny_access(callback=callback)
        return

    await callback.answer()

    await callback.message.edit_text(
        "💳 PENDING PAYMENTS\n\n"
        "There are currently no payment "
        "requests because Supabase has not "
        "been connected yet.",
        reply_markup=back_to_admin_menu(),
    )


@dp.callback_query(F.data == "manage_courses")
async def manage_courses_handler(callback):
    if not is_admin(callback.from_user.id):
        await deny_access(callback=callback)
        return

    await callback.answer()

    await callback.message.edit_text(
        "📚 MANAGE COURSES\n\n"
        "Course management will be connected "
        "to Supabase next.",
        reply_markup=back_to_admin_menu(),
    )


@dp.callback_query(F.data == "manage_users")
async def manage_users_handler(callback):
    if not is_admin(callback.from_user.id):
        await deny_access(callback=callback)
        return

    await callback.answer()

    await callback.message.edit_text(
        "👥 MANAGE USERS\n\n"
        "User management will be connected "
        "to Supabase next.",
        reply_markup=back_to_admin_menu(),
    )


@dp.callback_query(F.data == "statistics")
async def statistics_handler(callback):
    if not is_admin(callback.from_user.id):
        await deny_access(callback=callback)
        return

    await callback.answer()

    await callback.message.edit_text(
        "📊 STATISTICS\n\n"
        "Statistics will appear after "
        "Supabase is connected.",
        reply_markup=back_to_admin_menu(),
    )


@dp.callback_query(F.data == "broadcast")
async def broadcast_handler(callback):
    if not is_admin(callback.from_user.id):
        await deny_access(callback=callback)
        return

    await callback.answer()

    await callback.message.edit_text(
        "📢 BROADCAST\n\n"
        "Broadcast system will be added later.",
        reply_markup=back_to_admin_menu(),
    )


@dp.callback_query(F.data == "settings")
async def settings_handler(callback):
    if not is_admin(callback.from_user.id):
        await deny_access(callback=callback)
        return

    await callback.answer()

    await callback.message.edit_text(
        "⚙️ SETTINGS\n\n"
        "System settings will be added later.",
        reply_markup=back_to_admin_menu(),
    )


@dp.callback_query(F.data == "admin_panel")
async def admin_panel_handler(callback):
    if not is_admin(callback.from_user.id):
        await deny_access(callback=callback)
        return

    await callback.answer()

    await callback.message.edit_text(
        "👑 ADMIN CONTROL CENTER\n\n"
        "Welcome, Admin.\n\n"
        "Choose an option:",
        reply_markup=admin_menu(),
    )


async def main():
    if not TOKEN:
        raise RuntimeError(
            "ADMIN_BOT_TOKEN is missing in .env"
        )

    if not os.getenv("ADMIN_TELEGRAM_ID"):
        raise RuntimeError(
            "ADMIN_TELEGRAM_ID is missing in .env"
        )

    bot = Bot(token=TOKEN)

    print("✅ Admin Bot is running...")
    print("🔐 Admin authentication enabled.")

    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())