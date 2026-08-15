# Telegram Course Manager

A Telegram-based course management system with separate Admin and Customer bots, Supabase persistence, payment approval, course access management, renewals, broadcasts, notifications, audit logs, and automated subscription expiry handling.

> **Status:** Working production-oriented project. Review the configuration and database migrations before deploying a new instance.

## Features

### Admin Bot
- Admin authentication
- Course management
- Plan management
- Group/channel connection
- Payment review and approval
- Grant/revoke course access
- Customer management
- Broadcast messaging
- Settings
- Audit logs
- Notification center

### Customer Bot
- Course browsing
- Plan selection
- Payment submission with payment screenshot
- My Courses
- My Payments
- My Account
- Course renewal
- Access to approved courses

### Customer menu access rule
New or unapproved customers see only:

`📚 View All Courses`

After a payment is approved by an administrator, the customer receives the full customer menu on future sessions.

### Expiry Worker
The project includes a continuous expiry worker. It checks subscriptions periodically, handles expired access, revokes relevant Telegram invites/membership where configured, updates subscription state, sends customer notifications, and creates/sends admin alerts.

## Architecture

```text
                    Telegram
                       │
          ┌────────────┴────────────┐
          │                         │
     Admin Bot                Customer Bot
          │                         │
          └────────────┬────────────┘
                       │
                    Supabase
                       │
              ┌────────┴────────┐
              │                 │
       Course/Plan data   Payments/Access
              │
        Expiry Worker
```

## Project structure

```text
telegram-course-manager/
├── app/
│   ├── admin_bot/
│   │   └── main.py
│   ├── customer_bot/
│   │   └── main.py
│   ├── database/
│   │   └── supabase_client.py
│   ├── workers/
│   └── expiry_worker.py
├── cron/
├── migrations/
├── tests/
├── requirements.txt
├── .env.example
├── .gitignore
├── .python-version
└── render.yaml
```

## Requirements

- Python 3.11
- Telegram Bot accounts/tokens
- Supabase project
- A database configured using the project's migrations
- For 24/7 hosted operation: a suitable Render worker setup

## Environment variables

Create a local `.env` file from `.env.example`.

```env
CUSTOMER_BOT_TOKEN=
ADMIN_BOT_TOKEN=

SUPABASE_URL=
SUPABASE_SECRET_KEY=

ADMIN_TELEGRAM_ID=
```

Never commit `.env` or real credentials.

## Local setup

### 1. Clone

```bash
git clone https://github.com/patelprerak191-dotcom/telegram-course-manager.git
cd telegram-course-manager
```

### 2. Create virtual environment

Windows:

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
```

Linux/macOS:

```bash
python3 -m venv .venv
source .venv/bin/activate
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

### 4. Configure environment

Copy `.env.example` to `.env` and fill in your own credentials.

### 5. Run Admin Bot

```bash
python -m app.admin_bot.main
```

### 6. Run Customer Bot

Open another terminal with the same virtual environment:

```bash
python -m app.customer_bot.main
```

### 7. Run Expiry Worker

```bash
python -m app.expiry_worker
```

The expiry worker runs continuously. For a one-time/manual run, the project also supports:

```bash
python -m app.expiry_worker --once
```

## Render deployment

The repository contains `render.yaml` defining three Background Worker services:

```text
telegram-course-admin
telegram-course-customer
telegram-course-expiry
```

Their commands are:

```bash
python -m app.admin_bot.main
python -m app.customer_bot.main
python -m app.expiry_worker
```

### Render environment variables

Configure these variables with your own values in Render:

```text
CUSTOMER_BOT_TOKEN
ADMIN_BOT_TOKEN
SUPABASE_URL
SUPABASE_SECRET_KEY
ADMIN_TELEGRAM_ID
```

Do not put real secret values inside `render.yaml` or the Git repository.

## Database

The `migrations/` directory contains the database changes used by the project.

For a new installation:

1. Create a new Supabase project.
2. Review the migration SQL files.
3. Apply the required migrations in the correct order.
4. Create your own Telegram bots.
5. Configure the environment variables.
6. Start the Admin Bot, Customer Bot, and Expiry Worker.

**Important:** Do not run destructive SQL against an existing production database without a backup and review.

## Production safety

Before deploying a new version:

1. Back up the current working code.
2. Review database migrations.
3. Verify environment variables.
4. Run a Python syntax check.
5. Test Admin Bot and Customer Bot locally.
6. Test payment approval and access.
7. Test renewal.
8. Test expiry behavior.
9. Check Render logs after deployment.

Example syntax checks:

```bash
python -m py_compile app/admin_bot/main.py
python -m py_compile app/customer_bot/main.py
python -m py_compile app/expiry_worker.py
```

## Security

Never publish:
- Telegram bot tokens
- Supabase secret/service credentials
- `.env`
- Customer payment screenshots
- Production database exports
- Private customer information

If a credential is accidentally exposed, revoke/rotate it immediately.

See `SECURITY.md` for reporting security issues.

## Contributing

See `CONTRIBUTING.md`.

## License

This project is released under the MIT License. See `LICENSE`.

## Disclaimer

This software is provided as an open-source project. You are responsible for configuring Telegram permissions, Supabase security policies, payment handling, privacy requirements, and production infrastructure appropriately for your deployment and jurisdiction.
