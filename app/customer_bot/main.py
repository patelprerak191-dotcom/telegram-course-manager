import asyncio
import os

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart
from aiogram.types import Message
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("CUSTOMER_BOT_TOKEN")

dp = Dispatcher()


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


@dp.message(CommandStart())
async def start_handler(message: Message):
    await message.answer(
        "👋 Welcome!\n\n"
        "🎓 PREMIUM COURSES\n\n"
        "Choose what you want to do:",
        reply_markup=main_menu(),
    )


@dp.callback_query(F.data == "all_courses")
async def all_courses_handler(callback):
    await callback.answer()

    await callback.message.edit_text(
        "📚 ALL COURSES\n\n"
        "Courses will appear here after we connect "
        "the Supabase database.",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="🔙 Main Menu",
                        callback_data="main_menu",
                    )
                ]
            ]
        ),
    )


@dp.callback_query(F.data == "my_courses")
async def my_courses_handler(callback):
    await callback.answer()

    await callback.message.edit_text(
        "🎓 MY COURSES\n\n"
        "Your active subscriptions will appear here.",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="🔙 Main Menu",
                        callback_data="main_menu",
                    )
                ]
            ]
        ),
    )


@dp.callback_query(F.data == "my_payments")
async def my_payments_handler(callback):
    await callback.answer()

    await callback.message.edit_text(
        "💳 MY PAYMENTS\n\n"
        "Your payment history will appear here.",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="🔙 Main Menu",
                        callback_data="main_menu",
                    )
                ]
            ]
        ),
    )


@dp.callback_query(F.data == "my_account")
async def my_account_handler(callback):
    await callback.answer()

    user = callback.from_user

    await callback.message.edit_text(
        "👤 MY ACCOUNT\n\n"
        f"Name: {user.full_name}\n"
        f"Username: @{user.username if user.username else 'Not set'}\n"
        f"Telegram ID: {user.id}",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="🔙 Main Menu",
                        callback_data="main_menu",
                    )
                ]
            ],
        ),
    )


@dp.callback_query(F.data == "main_menu")
async def main_menu_handler(callback):
    await callback.answer()

    await callback.message.edit_text(
        "👋 Welcome!\n\n"
        "🎓 PREMIUM COURSES\n\n"
        "Choose what you want to do:",
        reply_markup=main_menu(),
    )


async def main():
    if not TOKEN:
        raise RuntimeError(
            "CUSTOMER_BOT_TOKEN is missing in .env"
        )

    bot = Bot(token=TOKEN)

    print("✅ Customer Bot is running...")

    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())