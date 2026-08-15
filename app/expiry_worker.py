import asyncio
import os
import sys
from datetime import datetime, timezone, timedelta
import uuid

from aiogram import Bot
from dotenv import load_dotenv

from app.database.supabase_client import supabase


# ============================================================
# CONFIG
# ============================================================

load_dotenv()

ADMIN_BOT_TOKEN = os.getenv("ADMIN_BOT_TOKEN")
ADMIN_TELEGRAM_ID = os.getenv("ADMIN_TELEGRAM_ID")
CUSTOMER_BOT_TOKEN = os.getenv("CUSTOMER_BOT_TOKEN")

CHECK_INTERVAL_SECONDS = int(
    os.getenv(
        "EXPIRY_CHECK_INTERVAL_SECONDS",
        "3600",
    )
)


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

    return response.data or []


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

        await bot.ban_chat_member(
            chat_id=chat_id,
            user_id=telegram_user_id,
        )

        log(
            f"User {telegram_user_id} "
            f"removed from Telegram chat {chat_id}"
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
# ADMIN ALERTS
# ============================================================

async def create_admin_alert(
    alert_type: str,
    severity: str,
    title: str,
    message: str,
    reference_id: str,
):
    """
    Create a deduplicated admin alert.

    The admin_alerts table has a unique constraint on
    (alert_type, reference_id), so the same lifecycle alert
    cannot be created twice.
    """
    try:
        existing = (
            supabase
            .table("admin_alerts")
            .select("id")
            .eq("alert_type", alert_type)
            .eq("reference_id", reference_id)
            .limit(1)
            .execute()
        )

        if existing.data:
            return False

        response = (
            supabase
            .table("admin_alerts")
            .insert({
                "alert_type": alert_type,
                "severity": severity,
                "title": title,
                "message": message,
                "reference_id": reference_id,
            })
            .execute()
        )

        return bool(response.data)

    except Exception as error:
        log(
            "Admin alert creation failed: "
            f"{repr(error)}"
        )
        return False


async def send_admin_alert(
    telegram_bot: Bot | None,
    title: str,
    message: str,
    severity: str,
):
    if not telegram_bot or not ADMIN_TELEGRAM_ID:
        return False

    icon = {
        "critical": "🚨",
        "warning": "⚠️",
        "info": "ℹ️",
    }.get(severity, "🔔")

    try:
        await telegram_bot.send_message(
            chat_id=int(ADMIN_TELEGRAM_ID),
            text=(
                f"{icon} {title}\n\n"
                f"{message}\n\n"
                "🔔 Open Admin Bot → Alerts for details."
            ),
        )
        return True

    except Exception as error:
        log(
            "Admin Telegram alert failed: "
            f"{repr(error)}"
        )
        return False


async def check_admin_alerts(
    telegram_bot: Bot | None,
):
    """
    Generate admin alerts for:
      - subscriptions expiring within 7/3/1 days
      - payments pending for more than 24 hours

    This is deliberately separate from actual expiry processing.
    Lifetime subscriptions are excluded.
    """

    log("🔔 Admin alert check started")

    now = datetime.now(timezone.utc)
    seven_days = now + timedelta(days=7)
    pending_cutoff = now - timedelta(hours=24)

    new_alerts = 0

    # --------------------------------------------------------
    # SUBSCRIPTION EXPIRY ALERTS
    # --------------------------------------------------------
    response = (
        supabase
        .table("subscriptions")
        .select(
            "id,user_id,course_id,expires_at,is_lifetime,status"
        )
        .eq("status", "active")
        .eq("is_lifetime", False)
        .gte("expires_at", now.isoformat())
        .lte("expires_at", seven_days.isoformat())
        .limit(1000)
        .execute()
    )

    for subscription in response.data or []:
        try:
            expires_at = datetime.fromisoformat(
                str(subscription["expires_at"]).replace(
                    "Z",
                    "+00:00",
                )
            )

            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(
                    tzinfo=timezone.utc
                )

            days_left = (
                expires_at - now
            ).total_seconds() / 86400

            if days_left <= 1:
                alert_type = "subscription_expiring_1_day"
                severity = "critical"
                title = "Subscription expires within 1 day"
            elif days_left <= 3:
                alert_type = "subscription_expiring_3_days"
                severity = "warning"
                title = "Subscription expires within 3 days"
            else:
                alert_type = "subscription_expiring_7_days"
                severity = "info"
                title = "Subscription expires within 7 days"

            reference_id = str(
                uuid.uuid5(
                    uuid.NAMESPACE_URL,
                    f"{subscription['id']}:{alert_type}",
                )
            )

            user = get_user(
                subscription["user_id"]
            )
            course = get_course(
                subscription["course_id"]
            )

            customer = " ".join(
                part
                for part in [
                    (user or {}).get("first_name"),
                    (user or {}).get("last_name"),
                ]
                if part
            ) or str(
                (user or {}).get(
                    "telegram_user_id",
                    "Unknown",
                )
            )

            message = (
                f"👤 Customer: {customer}\n"
                f"🆔 Telegram ID: "
                f"{(user or {}).get('telegram_user_id', 'Unknown')}\n"
                f"🎓 Course: "
                f"{(course or {}).get('name', 'Unknown')}\n"
                f"📅 Expires: "
                f"{subscription.get('expires_at')}"
            )

            created = await create_admin_alert(
                alert_type=alert_type,
                severity=severity,
                title=title,
                message=message,
                reference_id=reference_id,
            )

            if created:
                new_alerts += 1
                await send_admin_alert(
                    telegram_bot,
                    title,
                    message,
                    severity,
                )

        except Exception as error:
            log(
                "Subscription admin alert error: "
                f"{subscription.get('id')}: "
                f"{repr(error)}"
            )

    # --------------------------------------------------------
    # PENDING PAYMENTS > 24 HOURS
    # --------------------------------------------------------
    pending_response = (
        supabase
        .table("payment_requests")
        .select(
            "id,user_id,course_id,amount,currency,"
            "submitted_at,payment_number,status"
        )
        .eq("status", "pending")
        .lte(
            "submitted_at",
            pending_cutoff.isoformat(),
        )
        .limit(1000)
        .execute()
    )

    for payment in pending_response.data or []:
        try:
            user = get_user(
                payment["user_id"]
            )
            course = get_course(
                payment["course_id"]
            )

            customer = " ".join(
                part
                for part in [
                    (user or {}).get("first_name"),
                    (user or {}).get("last_name"),
                ]
                if part
            ) or str(
                (user or {}).get(
                    "telegram_user_id",
                    "Unknown",
                )
            )

            title = (
                "Payment pending for more than 24 hours"
            )

            message = (
                f"💳 Payment #: "
                f"{payment.get('payment_number')}\n"
                f"👤 Customer: {customer}\n"
                f"🎓 Course: "
                f"{(course or {}).get('name', 'Unknown')}\n"
                f"💰 Amount: "
                f"{payment.get('currency') or 'INR'} "
                f"{payment.get('amount')}\n"
                f"🕐 Submitted: "
                f"{payment.get('submitted_at')}"
            )

            created = await create_admin_alert(
                alert_type="payment_pending_24h",
                severity="warning",
                title=title,
                message=message,
                reference_id=payment["id"],
            )

            if created:
                new_alerts += 1
                await send_admin_alert(
                    telegram_bot,
                    title,
                    message,
                    "warning",
                )

        except Exception as error:
            log(
                "Payment admin alert error: "
                f"{payment.get('id')}: "
                f"{repr(error)}"
            )

    log(
        f"🔔 Admin alert check completed. "
        f"New alerts: {new_alerts}"
    )

    return new_alerts


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

            # Generate admin alerts from the same hourly worker.
            # Alert failures are isolated and must never stop expiry processing.
            alert_bot = None
            try:
                if ADMIN_BOT_TOKEN:
                    alert_bot = Bot(token=ADMIN_BOT_TOKEN)

                await check_admin_alerts(
                    telegram_bot=alert_bot,
                )
            except Exception as alert_error:
                log(
                    "Admin alert worker error: "
                    f"{repr(alert_error)}"
                )
            finally:
                if alert_bot:
                    await alert_bot.session.close()

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

        alert_bot = None

        try:
            if ADMIN_BOT_TOKEN:
                alert_bot = Bot(token=ADMIN_BOT_TOKEN)

            await check_admin_alerts(
                telegram_bot=alert_bot,
            )
        finally:
            if alert_bot:
                await alert_bot.session.close()

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