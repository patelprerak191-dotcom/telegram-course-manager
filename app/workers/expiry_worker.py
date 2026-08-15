import asyncio
import os
import sys
from datetime import datetime, timezone

from aiogram import Bot
from dotenv import load_dotenv

from app.database.supabase_client import supabase


# ============================================================
# CONFIG
# ============================================================

load_dotenv()

ADMIN_BOT_TOKEN = os.getenv("ADMIN_BOT_TOKEN")
CUSTOMER_BOT_TOKEN = os.getenv("CUSTOMER_BOT_TOKEN")

CHECK_INTERVAL_SECONDS = int(
    os.getenv(
        "EXPIRY_CHECK_INTERVAL_SECONDS",
        "3600",
    )
)

# Expiry-warning windows.
EXPIRY_WARNING_DAYS = (3, 1)


# ============================================================
# LOGGING
# ============================================================

def log(message: str):
    now = datetime.now(
        timezone.utc
    ).strftime("%Y-%m-%d %H:%M:%S UTC")

    print(
        f"[{now}] {message}"
    )


# ============================================================
# GET EXPIRED SUBSCRIPTIONS
# ============================================================

def get_expired_subscriptions():

    now_iso = datetime.now(
        timezone.utc
    ).isoformat()

    response = (
        supabase
        .table("subscriptions")
        .select(
            "id, user_id, course_id, plan_id, "
            "payment_request_id, status, "
            "started_at, expires_at, "
            "is_lifetime, "
            "joined_channel_at, revoked_at"
        )
        .eq(
            "status",
            "active",
        )
        .eq(
            "is_lifetime",
            False,
        )
        .not_.is_(
            "expires_at",
            "null",
        )
        .lte(
            "expires_at",
            now_iso,
        )
        .execute()
    )

    subscriptions = response.data or []

    # Defensive filtering: only fixed subscriptions with a real expiry
    # should ever reach the expiry processor.
    return [
        subscription
        for subscription in subscriptions
        if (
            not subscription.get("is_lifetime")
            and subscription.get("expires_at")
        )
    ]


# ============================================================
# EXPIRY WARNING TRACKING
# ============================================================

def warning_already_sent(subscription_id, warning_type):
    """
    Durable idempotency check.

    Step 2 requires the SQL table `subscription_notifications`
    to exist. The unique key prevents duplicate warnings even if
    the worker runs many times.
    """
    response = (
        supabase
        .table("subscription_notifications")
        .select("id")
        .eq("subscription_id", subscription_id)
        .eq("notification_type", warning_type)
        .limit(1)
        .execute()
    )

    return bool(response.data)


def mark_warning_sent(subscription_id, warning_type):
    response = (
        supabase
        .table("subscription_notifications")
        .insert(
            {
                "subscription_id": subscription_id,
                "notification_type": warning_type,
            }
        )
        .execute()
    )

    return bool(response.data)


async def send_expiry_warning(
    bot: Bot,
    telegram_user_id: int,
    course_name: str,
    expires_at,
    days_left: int,
):
    if days_left == 3:
        title = "⚠️ COURSE ACCESS EXPIRING SOON"
        body = (
            "Your course access will expire in 3 days.\n\n"
            "🔄 Please renew before your access expires."
        )
    else:
        title = "⚠️ COURSE ACCESS EXPIRES TOMORROW"
        body = (
            "Your course access expires tomorrow.\n\n"
            "🔄 Please renew your course access to continue learning."
        )

    text = (
        f"{title}\n\n"
        f"🎓 Course:\n{course_name}\n\n"
        f"📅 Current expiry:\n{expires_at}\n\n"
        f"{body}\n\n"
        "You can renew your course access from the Customer Bot."
    )

    try:
        await bot.send_message(
            chat_id=telegram_user_id,
            text=text,
        )

        log(
            f"{days_left}-day expiry warning sent to "
            f"{telegram_user_id} for subscription "
            f"{course_name}"
        )
        return True

    except Exception as error:
        log(
            f"Expiry warning notification failed for "
            f"{telegram_user_id}: {repr(error)}"
        )
        return False


def get_warning_candidates():
    """
    Return active fixed subscriptions whose expiry is within
    the configured warning windows.

    The comparison is done using UTC dates so the warning is
    deterministic across hourly worker runs.
    """
    response = (
        supabase
        .table("subscriptions")
        .select(
            "id, user_id, course_id, plan_id, "
            "payment_request_id, status, "
            "started_at, expires_at, "
            "is_lifetime, joined_channel_at, revoked_at"
        )
        .eq("status", "active")
        .eq("is_lifetime", False)
        .not_.is_("expires_at", "null")
        .execute()
    )

    now = datetime.now(timezone.utc)
    candidates = []

    for subscription in response.data or []:
        expires_raw = subscription.get("expires_at")
        if not expires_raw:
            continue

        try:
            expires = datetime.fromisoformat(
                str(expires_raw).replace("Z", "+00:00")
            )

            if expires.tzinfo is None:
                expires = expires.replace(tzinfo=timezone.utc)

        except ValueError:
            log(
                "Invalid expires_at for subscription: "
                f"{subscription.get('id')}: {expires_raw}"
            )
            continue

        days_left = (expires.date() - now.date()).days

        if days_left in EXPIRY_WARNING_DAYS and expires > now:
            candidates.append((subscription, days_left))

    return candidates


async def run_expiry_warnings():
    log("🔔 Expiry warning check started")

    candidates = get_warning_candidates()

    if not candidates:
        log("✅ No expiry warnings due.")
        return

    log(
        f"Found {len(candidates)} subscription(s) "
        "requiring expiry warning."
    )

    if not CUSTOMER_BOT_TOKEN:
        log("⚠️ CUSTOMER_BOT_TOKEN missing. Cannot send warnings.")
        return

    customer_bot = Bot(token=CUSTOMER_BOT_TOKEN)

    try:
        for subscription, days_left in candidates:
            subscription_id = subscription["id"]

            warning_type = f"expires_{days_left}_days"

            if warning_already_sent(
                subscription_id,
                warning_type,
            ):
                log(
                    f"Skipping duplicate {days_left}-day warning: "
                    f"{subscription_id}"
                )
                continue

            user = get_user(subscription["user_id"])
            if not user:
                log(
                    f"User not found for warning: "
                    f"{subscription_id}"
                )
                continue

            course = get_course(subscription["course_id"])
            if not course:
                log(
                    f"Course not found for warning: "
                    f"{subscription_id}"
                )
                continue

            sent = await send_expiry_warning(
                customer_bot,
                int(user["telegram_user_id"]),
                course["name"],
                subscription["expires_at"],
                days_left,
            )

            if sent:
                try:
                    mark_warning_sent(
                        subscription_id,
                        warning_type,
                    )
                except Exception as error:
                    log(
                        "Failed to record expiry warning: "
                        f"{subscription_id}: {repr(error)}"
                    )

    finally:
        await customer_bot.session.close()

    log("🔔 Expiry warning check completed.")


# ============================================================
# GET USER
# ============================================================

def get_user(
    user_id,
):

    response = (
        supabase
        .table("users")
        .select(
            "id, telegram_user_id, "
            "username, first_name, last_name"
        )
        .eq(
            "id",
            user_id,
        )
        .limit(1)
        .execute()
    )

    if not response.data:
        return None

    return response.data[0]


# ============================================================
# GET COURSE
# ============================================================

def get_course(
    course_id,
):

    response = (
        supabase
        .table("courses")
        .select(
            "id, name, slug"
        )
        .eq(
            "id",
            course_id,
        )
        .limit(1)
        .execute()
    )

    if not response.data:
        return None

    return response.data[0]


# ============================================================
# GET CHANNEL
# ============================================================

def get_channel(
    course_id,
):

    response = (
        supabase
        .table("channels")
        .select(
            "id, course_id, "
            "telegram_chat_id, "
            "channel_username, "
            "channel_title, "
            "is_active, "
            "bot_is_admin, "
            "can_invite_users, "
            "can_manage_members"
        )
        .eq(
            "course_id",
            course_id,
        )
        .eq(
            "is_active",
            True,
        )
        .limit(1)
        .execute()
    )

    if not response.data:
        return None

    return response.data[0]


# ============================================================
# GET INVITE LINKS
# ============================================================

def get_subscription_invites(
    subscription_id,
):

    response = (
        supabase
        .table("invite_links")
        .select(
            "id, subscription_id, "
            "channel_id, "
            "telegram_invite_link, "
            "status, "
            "created_at, "
            "sent_at, "
            "joined_at, "
            "revoked_at, "
            "expires_at"
        )
        .eq(
            "subscription_id",
            subscription_id,
        )
        .execute()
    )

    return response.data or []


# ============================================================
# REVOKE TELEGRAM INVITE
# ============================================================

async def revoke_telegram_invite(
    bot: Bot,
    chat_id: int,
    invite_link: str,
):

    if not invite_link:
        return False

    try:

        await bot.revoke_chat_invite_link(
            chat_id=chat_id,
            invite_link=invite_link,
        )

        log(
            f"Invite revoked: {invite_link}"
        )

        return True

    except Exception as error:

        log(
            "Telegram invite revoke failed: "
            f"{repr(error)}"
        )

        return False


# ============================================================
# REMOVE USER FROM TELEGRAM GROUP
# ============================================================

async def remove_user_from_course(
    bot: Bot,
    chat_id: int,
    telegram_user_id: int,
):

    try:

        # Ban removes the member, but an immediate unban is required.
        # Otherwise the user stays permanently banned and cannot rejoin
        # after a legitimate renewal.
        await bot.ban_chat_member(
            chat_id=chat_id,
            user_id=telegram_user_id,
        )

        await bot.unban_chat_member(
            chat_id=chat_id,
            user_id=telegram_user_id,
            only_if_banned=True,
        )

        log(
            f"User {telegram_user_id} "
            f"removed from Telegram chat {chat_id} "
            f"and unbanned for future legitimate access"
        )

        return True

    except Exception as error:

        log(
            "Telegram member removal failed: "
            f"{repr(error)}"
        )

        return False


# ============================================================
# UPDATE INVITE STATUS
# ============================================================

def mark_invite_revoked(
    invite_id,
):

    (
        supabase
        .table("invite_links")
        .update(
            {
                "status": "revoked",
                "revoked_at": "now()",
            }
        )
        .eq(
            "id",
            invite_id,
        )
        .execute()
    )


# ============================================================
# UPDATE SUBSCRIPTION
# ============================================================

def mark_subscription_expired(
    subscription_id,
):

    response = (
        supabase
        .table("subscriptions")
        .update(
            {
                "status": "expired",
                "revoked_at": "now()",
            }
        )
        .eq(
            "id",
            subscription_id,
        )
        .eq(
            "status",
            "active",
        )
        .execute()
    )

    return bool(response.data)


# ============================================================
# CUSTOMER NOTIFICATION
# ============================================================

async def notify_customer(
    bot: Bot,
    telegram_user_id: int,
    course_name: str,
    expires_at,
):

    expiry_text = str(
        expires_at
    )

    text = (
        "⏰ COURSE ACCESS EXPIRED\n\n"
        f"🎓 Course:\n"
        f"{course_name}\n\n"
        f"📅 Expired:\n"
        f"{expiry_text}\n\n"
        "🔒 Your access to this course "
        "has expired.\n\n"
        "You can renew your course access "
        "from the Customer Bot."
    )

    try:

        await bot.send_message(
            chat_id=telegram_user_id,
            text=text,
        )

        log(
            f"Expiry notification sent to "
            f"{telegram_user_id}"
        )

        return True

    except Exception as error:

        log(
            "Customer notification failed: "
            f"{repr(error)}"
        )

        return False


# ============================================================
# PROCESS ONE SUBSCRIPTION
# ============================================================

async def process_subscription(
    subscription,
    telegram_bot: Bot,
    customer_bot: Bot | None,
):

    subscription_id = subscription["id"]
    user_id = subscription["user_id"]
    course_id = subscription["course_id"]

    log(
        "Processing subscription: "
        f"{subscription_id}"
    )

    # --------------------------------------------------------
    # SAFETY: LIFETIME SUBSCRIPTION
    # --------------------------------------------------------

    if subscription.get(
        "is_lifetime"
    ):

        log(
            "SKIP lifetime subscription: "
            f"{subscription_id}"
        )

        return

    # --------------------------------------------------------
    # SAFETY: MUST BE ACTIVE
    # --------------------------------------------------------

    if subscription.get(
        "status"
    ) != "active":

        log(
            "SKIP non-active subscription: "
            f"{subscription_id}"
        )

        return

    # --------------------------------------------------------
    # USER
    # --------------------------------------------------------

    user = get_user(
        user_id
    )

    if not user:

        log(
            "User not found: "
            f"{user_id}"
        )

        return

    telegram_user_id = int(
        user["telegram_user_id"]
    )

    # --------------------------------------------------------
    # COURSE
    # --------------------------------------------------------

    course = get_course(
        course_id
    )

    if not course:

        log(
            "Course not found: "
            f"{course_id}"
        )

        return

    course_name = course[
        "name"
    ]

    # --------------------------------------------------------
    # CHANNEL
    # --------------------------------------------------------

    channel = get_channel(
        course_id
    )

    if not channel:

        log(
            "Active Telegram channel not found "
            f"for course {course_id}"
        )

        # We can still expire the database
        # subscription even if the Telegram
        # channel record is missing.

    # --------------------------------------------------------
    # REVOKE INVITE LINKS
    # --------------------------------------------------------

    invites = get_subscription_invites(
        subscription_id
    )

    for invite in invites:

        invite_link = invite.get(
            "telegram_invite_link"
        )

        channel_id = invite.get(
            "channel_id"
        )

        # Find the channel associated with
        # this invite.
        invite_channel = None

        if channel_id:

            channel_response = (
                supabase
                .table("channels")
                .select(
                    "telegram_chat_id"
                )
                .eq(
                    "id",
                    channel_id,
                )
                .limit(1)
                .execute()
            )

            if channel_response.data:

                invite_channel = (
                    channel_response
                    .data[0]
                )

        if invite_channel:

            chat_id = int(
                invite_channel[
                    "telegram_chat_id"
                ]
            )

            await revoke_telegram_invite(
                telegram_bot,
                chat_id,
                invite_link,
            )

        elif channel:

            chat_id = int(
                channel[
                    "telegram_chat_id"
                ]
            )

            await revoke_telegram_invite(
                telegram_bot,
                chat_id,
                invite_link,
            )

        mark_invite_revoked(
            invite["id"]
        )

        log(
            "invite_links updated to revoked: "
            f"{invite['id']}"
        )

    # --------------------------------------------------------
    # REMOVE USER FROM TELEGRAM COURSE
    # --------------------------------------------------------

    if channel:

        chat_id = int(
            channel[
                "telegram_chat_id"
            ]
        )

        can_manage_members = bool(
            channel.get(
                "can_manage_members"
            )
        )

        if can_manage_members:

            await remove_user_from_course(
                telegram_bot,
                chat_id,
                telegram_user_id,
            )

        else:

            log(
                "Cannot remove user because "
                "can_manage_members=false"
            )

    # --------------------------------------------------------
    # EXPIRE SUBSCRIPTION
    # --------------------------------------------------------

    changed = (
        mark_subscription_expired(
            subscription_id
        )
    )

    if not changed:

        log(
            "Subscription was not changed. "
            "It may already have been processed: "
            f"{subscription_id}"
        )

        return

    log(
        "Subscription marked expired: "
        f"{subscription_id}"
    )

    # --------------------------------------------------------
    # CUSTOMER NOTIFICATION
    # --------------------------------------------------------

    if customer_bot:

        await notify_customer(
            customer_bot,
            telegram_user_id,
            course_name,
            subscription.get(
                "expires_at"
            ),
        )


# ============================================================
# PROCESS ALL EXPIRED SUBSCRIPTIONS
# ============================================================

async def run_expiry_check():

    log(
        "========================================"
    )

    log(
        "⏰ Subscription expiry check started"
    )

    # Step 2: send durable 3-day / 1-day warnings first.
    try:
        await run_expiry_warnings()
    except Exception as error:
        log(
            "Expiry warning worker error: "
            f"{repr(error)}"
        )

    subscriptions = (
        get_expired_subscriptions()
    )

    if not subscriptions:

        log(
            "✅ No expired subscriptions found."
        )

        return

    log(
        f"Found {len(subscriptions)} "
        "expired subscription(s)."
    )

    telegram_bot = None
    customer_bot = None

    if ADMIN_BOT_TOKEN:

        telegram_bot = Bot(
            token=ADMIN_BOT_TOKEN
        )

    else:

        log(
            "⚠️ ADMIN_BOT_TOKEN missing."
        )

    if CUSTOMER_BOT_TOKEN:

        customer_bot = Bot(
            token=CUSTOMER_BOT_TOKEN
        )

    else:

        log(
            "⚠️ CUSTOMER_BOT_TOKEN missing."
        )

    if not telegram_bot:

        log(
            "❌ Telegram Bot unavailable. "
            "Cannot process expiry."
        )

        return

    try:

        for subscription in subscriptions:

            try:

                await process_subscription(
                    subscription,
                    telegram_bot,
                    customer_bot,
                )

            except Exception as error:

                log(
                    "Subscription processing error: "
                    f"{subscription.get('id')}: "
                    f"{repr(error)}"
                )

    finally:

        await telegram_bot.session.close()

        if customer_bot:

            await customer_bot.session.close()

    log(
        "⏰ Subscription expiry check completed."
    )

    log(
        "========================================"
    )


# ============================================================
# CONTINUOUS WORKER
# ============================================================

async def worker_loop():

    log(
        "========================================"
    )

    log(
        "⏰ EXPIRY WORKER STARTED"
    )

    log(
        f"Check interval: "
        f"{CHECK_INTERVAL_SECONDS} seconds"
    )

    log(
        "Lifetime subscriptions are protected."
    )

    log(
        "========================================"
    )

    while True:

        try:

            await run_expiry_check()

        except Exception as error:

            log(
                "Expiry worker error: "
                f"{repr(error)}"
            )

        log(
            f"Next check in "
            f"{CHECK_INTERVAL_SECONDS} seconds."
        )

        await asyncio.sleep(
            CHECK_INTERVAL_SECONDS
        )


# ============================================================
# MAIN
# ============================================================

async def main():

    # --------------------------------------------------------
    # --once support
    # --------------------------------------------------------

    if (
        len(sys.argv) > 1
        and sys.argv[1].lower() == "--once"
    ):

        log(
            "Running expiry worker once..."
        )

        await run_expiry_check()

        return

    # --------------------------------------------------------
    # CONTINUOUS MODE
    # --------------------------------------------------------

    await worker_loop()


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":

    try:

        asyncio.run(
            main()
        )

    except KeyboardInterrupt:

        print()

        log(
            "⛔ Expiry worker stopped."
        )