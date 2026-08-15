# Telegram Course Manager — Complete Setup Guide (Zero to 24/7 Test Deployment)

This guide is written for a person who has **almost no technical knowledge**.

By following the steps in order, you can create your own independent Telegram Course Manager using:

- GitHub — source code
- Telegram BotFather — your own Admin and Customer bots
- Supabase — your own database
- Render — hosting
- UptimeRobot — periodic health checks for the Render Free Web Service

> **Important:** This guide creates a separate instance for you. You use your own Telegram bot tokens and your own Supabase project. Never use another person's credentials.

---

# 1. What you are going to build

After setup, your system will look like this:

```text
                    TELEGRAM
                       │
          ┌────────────┴────────────┐
          │                         │
      ADMIN BOT               CUSTOMER BOT
          │                         │
          └────────────┬────────────┘
                       │
                    SUPABASE
                       │
                 EXPIRY WORKER
                       ▲
                       │
                Render Web Service
                       ▲
                       │
                UptimeRobot
                 /health check
```

Your Render Free Web Service uses `web_runner.py` to start:

```text
1. Admin Bot
2. Customer Bot
3. Expiry Worker
4. FastAPI /health endpoint
```

The `/health` endpoint lets Render/UptimeRobot check whether the service is responding.

## Important limitation

The Free Render + UptimeRobot method is a **free test/hobby setup**, not a guaranteed enterprise-grade 24/7 service.

Render Free Web Services can restart or cold-start. UptimeRobot periodically sends HTTP requests and can help prevent the normal idle sleep condition, but it does not guarantee zero downtime.

For serious production use, use an always-on paid hosting plan/worker.

---

# 2. Before you start

You need:

- A Windows PC
- Internet connection
- A GitHub account
- A Telegram account
- A Supabase account
- A Render account
- An UptimeRobot account

You do **not** need to understand Python programming to follow this guide.

---

# 3. Install Python on Windows

## Step 3.1 — Download Python

Open:

https://www.python.org/downloads/

Install Python 3.11.

During installation:

**IMPORTANT:** tick:

```text
Add python.exe to PATH
```

Then click:

```text
Install Now
```

## Step 3.2 — Verify Python

Open PowerShell.

Press:

```text
Windows key
```

type:

```text
PowerShell
```

open it.

Run:

```powershell
python --version
```

You should see something similar to:

```text
Python 3.11.x
```

If `python` does not work, try:

```powershell
py --version
```

---

# 4. Install Git

Download Git:

https://git-scm.com/download/win

Install using the normal/default options.

Verify:

```powershell
git --version
```

You should see something similar to:

```text
git version 2.x.x
```

---

# 5. Get the project from GitHub

Open PowerShell.

Go to the folder where you want the project.

Example:

```powershell
cd "D:\"
```

Clone the repository:

```powershell
git clone https://github.com/patelprerak191-dotcom/telegram-course-manager.git
```

Enter the project:

```powershell
cd "D:\telegram-course-manager"
```

If you cloned it somewhere else, use that folder instead.

Check files:

```powershell
Get-ChildItem
```

You should see files such as:

```text
app
migrations
tests
requirements.txt
.env.example
render.yaml
README.md
LICENSE
web_runner.py
```

---

# 6. Create a Python virtual environment

Inside the project folder:

```powershell
python -m venv .venv
```

Activate it:

```powershell
.\.venv\Scripts\Activate.ps1
```

If activation works, PowerShell should show:

```text
(.venv)
```

at the beginning of the command line.

If PowerShell blocks activation, run:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
```

Then:

```powershell
.\.venv\Scripts\Activate.ps1
```

---

# 7. Install project dependencies

Run:

```powershell
python -m pip install --upgrade pip
```

Then:

```powershell
pip install -r requirements.txt
```

Wait until installation finishes.

---

# 8. Create your Telegram Admin Bot

Open Telegram.

Search:

```text
@BotFather
```

Open the official BotFather.

Send:

```text
/newbot
```

Follow the instructions.

Give it a name, for example:

```text
My Course Admin
```

Then give it a username ending in:

```text
bot
```

Example:

```text
my_course_admin_bot
```

BotFather will give you a token similar to:

```text
123456789:AAxxxxxxxxxxxxxxxxxxxxxxxx
```

## IMPORTANT

Save this token privately.

Call it:

```text
ADMIN_BOT_TOKEN
```

**Never post it on GitHub, Discord, Telegram groups, screenshots, or public chat.**

---

# 9. Create your Telegram Customer Bot

Again open:

```text
@BotFather
```

Send:

```text
/newbot
```

Create another bot.

Example:

```text
My Course Customer
```

Username:

```text
my_course_customer_bot
```

BotFather will give another token.

Save it privately as:

```text
CUSTOMER_BOT_TOKEN
```

You now have:

```text
ADMIN_BOT_TOKEN
CUSTOMER_BOT_TOKEN
```

---

# 10. Get your Telegram Admin ID

You need your numeric Telegram user ID.

Use a trusted Telegram ID bot/service or your existing method to obtain your numeric Telegram ID.

It will look like:

```text
123456789
```

Save it as:

```text
ADMIN_TELEGRAM_ID
```

Do not confuse this with your bot token.

---

# 11. Create a Supabase project

Open:

https://supabase.com/

Create an account or log in.

Create a new project.

Example project name:

```text
my-telegram-course-manager
```

Choose a strong database password.

Wait until the Supabase project is ready.

---

# 12. Get Supabase URL

In Supabase:

```text
Project
→ Settings
→ API
```

Find your project URL.

It looks similar to:

```text
https://xxxxxxxxxxxxxxxx.supabase.co
```

Save it as:

```text
SUPABASE_URL
```

---

# 13. Get the Supabase secret key

In the Supabase API settings, find the server-side secret/service credential supported by your project.

This project expects the environment variable:

```text
SUPABASE_SECRET_KEY
```

Copy the secret value.

## SECURITY WARNING

This is a powerful server-side credential.

Never:

- put it in GitHub
- put it in README.md
- send it in a public chat
- put it in screenshots
- commit `.env`

Only put it into your local `.env` or Render's private environment variables.

---

# 14. Prepare the database

The repository contains database migrations:

```text
migrations/
```

Open your Supabase project.

Go to:

```text
SQL Editor
```

Review the migration SQL files from the repository.

Run the required SQL migrations in their intended order.

## IMPORTANT

If you are installing a fresh instance:

```text
new Supabase project
+
migrations
```

is the safest approach.

Do not run destructive SQL against an existing production database without a backup.

---

# 15. Create your local `.env`

Inside the project folder, copy:

```text
.env.example
```

to:

```text
.env
```

PowerShell command:

```powershell
Copy-Item .env.example .env
```

Open it:

```powershell
notepad .env
```

Fill it like this:

```env
CUSTOMER_BOT_TOKEN=YOUR_CUSTOMER_BOT_TOKEN
ADMIN_BOT_TOKEN=YOUR_ADMIN_BOT_TOKEN

SUPABASE_URL=https://YOUR_PROJECT.supabase.co
SUPABASE_SECRET_KEY=YOUR_SUPABASE_SECRET_KEY

ADMIN_TELEGRAM_ID=YOUR_TELEGRAM_ID
```

Replace the placeholders with your real values.

Save the file.

## NEVER run this:

```powershell
git add .env
```

`.env` must remain private.

---

# 16. Test the Admin Bot locally

Activate your virtual environment:

```powershell
.\.venv\Scripts\Activate.ps1
```

Run:

```powershell
python -m app.admin_bot.main
```

You should see the Admin Bot startup logs.

Keep this terminal open while testing.

Test your Admin Bot in Telegram.

When finished, press:

```text
Ctrl + C
```

---

# 17. Test the Customer Bot locally

Open a second PowerShell window.

Go to your project:

```powershell
cd "D:\telegram-course-manager"
```

Activate:

```powershell
.\.venv\Scripts\Activate.ps1
```

Run:

```powershell
python -m app.customer_bot.main
```

Test the Customer Bot in Telegram.

When finished:

```text
Ctrl + C
```

---

# 18. Test the expiry worker locally

Run:

```powershell
python -m app.expiry_worker
```

Verify that it starts without an error.

When finished:

```text
Ctrl + C
```

---

# 19. Free Render test setup

This repository includes:

```text
web_runner.py
```

The runner starts the existing modules without replacing their internal logic:

```text
Admin Bot
Customer Bot
Expiry Worker
```

and also exposes:

```text
/health
```

## IMPORTANT

Do not run the same Telegram bot locally and on Render at the same time.

Telegram polling can produce:

```text
TelegramConflictError:
terminated by other getUpdates request
```

if two instances use the same bot token.

---

# 20. Test `web_runner.py` locally

First stop any individual bot process.

Then:

```powershell
python web_runner.py
```

You should see:

```text
[RUNNER] Starting admin_bot
[RUNNER] Starting customer_bot
[RUNNER] Starting expiry_worker
[RUNNER] All child processes started.
```

Open another browser tab:

```text
http://127.0.0.1:10000/health
```

Expected result:

```json
{
  "status": "healthy",
  "services": {
    "admin_bot": {
      "running": true
    },
    "customer_bot": {
      "running": true
    },
    "expiry_worker": {
      "running": true
    }
  }
}
```

Stop the local runner with:

```text
Ctrl + C
```

before deploying the same bot tokens to Render.

---

# 21. Create Render account

Open:

https://render.com/

Create an account.

Using GitHub login is easiest.

---

# 22. Connect GitHub to Render

In Render:

```text
Dashboard
→ New +
→ Web Service
```

Select:

```text
patelprerak191-dotcom/telegram-course-manager
```

Select:

```text
main
```

branch.

---

# 23. Configure Render Web Service

Use:

```text
Name:
telegram-course-manager
```

Runtime:

```text
Python 3
```

Build Command:

```text
pip install -r requirements.txt
```

Start Command:

```text
python web_runner.py
```

Plan:

```text
Free
```

If Render shows a Health Check Path field, enter:

```text
/health
```

If it does not show the field, the service can still run because the application itself exposes `/health`.

---

# 24. Add Render environment variables

In Render's Environment Variables section add:

```text
CUSTOMER_BOT_TOKEN
```

Value:

```text
YOUR_CUSTOMER_BOT_TOKEN
```

Add:

```text
ADMIN_BOT_TOKEN
```

Value:

```text
YOUR_ADMIN_BOT_TOKEN
```

Add:

```text
SUPABASE_URL
```

Value:

```text
https://YOUR_PROJECT.supabase.co
```

Add:

```text
SUPABASE_SECRET_KEY
```

Value:

```text
YOUR_SUPABASE_SECRET_KEY
```

Add:

```text
ADMIN_TELEGRAM_ID
```

Value:

```text
YOUR_TELEGRAM_ID
```

Never put these values into GitHub.

---

# 25. Deploy Render

Click:

```text
Create Web Service
```

Wait for the build.

A successful build should show:

```text
Build successful
```

Then:

```text
Running 'python web_runner.py'
```

and:

```text
[RUNNER] Starting admin_bot
[RUNNER] Starting customer_bot
[RUNNER] Starting expiry_worker
[RUNNER] All child processes started.
```

You should also see Uvicorn running on the Render port.

---

# 26. Test Render `/health`

Render gives you a URL similar to:

```text
https://telegram-course-manager-xxxx.onrender.com
```

Open:

```text
https://telegram-course-manager-xxxx.onrender.com/health
```

Expected:

```json
{
  "status": "healthy",
  "services": {
    "admin_bot": {
      "running": true
    },
    "customer_bot": {
      "running": true
    },
    "expiry_worker": {
      "running": true
    }
  }
}
```

If status is:

```text
healthy
```

your three child processes are running.

---

# 27. Test Telegram after Render deployment

Do not run the local bots at the same time.

Test:

## Customer Bot

Send:

```text
/start
```

Check that the customer receives the expected menu.

## Admin Bot

Send:

```text
/start
```

Check the admin menu.

## Database

Open Supabase and verify the expected data is available.

---

# 28. Test the complete course flow

Recommended test:

```text
Customer
   ↓
/start
   ↓
View All Courses
   ↓
Select Course
   ↓
Select Plan
   ↓
Submit Payment
   ↓
Admin receives payment
   ↓
Admin approves payment
   ↓
Customer receives access
   ↓
Customer starts another session
   ↓
Full menu is available
```

Also test:

```text
Broadcast
Notifications
Course access
Renewal
Expiry
Admin settings
```

Do not test destructive operations with real customers until the new instance is verified.

---

# 29. Configure UptimeRobot

Open:

https://uptimerobot.com/

Create an account.

Create a new monitor.

Choose:

```text
Monitor Type:
HTTP(s)
```

URL:

```text
https://YOUR-RENDER-SERVICE.onrender.com/health
```

Monitoring interval:

```text
5 minutes
```

Friendly name:

```text
Telegram Course Manager
```

Create the monitor.

The monitor should eventually show:

```text
UP
```

## Why `/health`?

Your application exposes:

```text
/health
```

and returns a healthy status when the child processes are running.

---

# 30. Understand the Free Render limitation

UptimeRobot can periodically send an HTTP request to your Render service.

This can help with the normal idle-sleep behavior of the Free Web Service.

However:

```text
UptimeRobot ≠ guaranteed 24/7 uptime
```

Render Free can still restart or cold-start.

For a serious paid production service, use an always-on paid service/worker.

---

# 31. Telegram polling conflict — very important

Never run:

```text
Local Admin Bot
+
Render Admin Bot
```

with the same token at the same time.

Never run:

```text
Local Customer Bot
+
Render Customer Bot
```

with the same token at the same time.

Otherwise Telegram may show:

```text
TelegramConflictError:
Conflict: terminated by other getUpdates request
```

If you see this:

1. Stop local Python processes.
2. Wait a few seconds.
3. Check Render logs.
4. Test Telegram again.

PowerShell command to see Python processes:

```powershell
Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" | Select-Object ProcessId,CommandLine
```

If nothing is printed, there are no running Python processes on that Windows machine.

---

# 32. Updating the project later

When a new version is available:

First update your local repository:

```powershell
git pull origin main
```

Install dependencies if required:

```powershell
pip install -r requirements.txt
```

Run tests/syntax checks:

```powershell
python -m py_compile app/admin_bot/main.py
python -m py_compile app/customer_bot/main.py
python -m py_compile app/expiry_worker.py
python -m py_compile web_runner.py
```

Then commit your changes:

```powershell
git add .
git commit -m "Describe your change"
git push origin main
```

Render can automatically redeploy when the connected GitHub branch changes, depending on the service's auto-deploy setting.

---

# 33. Do not commit secrets

Never run:

```powershell
git add .env
```

Never upload:

```text
.env
database exports
customer payment screenshots
production credentials
Telegram bot tokens
Supabase secret keys
```

The repository's `.gitignore` is intended to protect `.env`, virtual environments, Python cache files, and other local files.

---

# 34. Emergency: bot token accidentally exposed

If a Telegram bot token is accidentally published:

1. Open BotFather.
2. Revoke/replace the compromised token.
3. Update Render's environment variable.
4. Redeploy/restart the Render service.

If the Supabase secret key is exposed:

1. Rotate/revoke the compromised credential in Supabase.
2. Replace it in Render.
3. Check Git history if it was committed.
4. Review the database/security policies.

---

# 35. Recommended final checklist

## Accounts

```text
[ ] GitHub
[ ] Telegram
[ ] Supabase
[ ] Render
[ ] UptimeRobot
```

## Telegram

```text
[ ] Admin Bot created
[ ] Customer Bot created
[ ] Admin Bot token saved privately
[ ] Customer Bot token saved privately
[ ] Admin Telegram ID obtained
```

## Supabase

```text
[ ] Project created
[ ] Migrations reviewed
[ ] Required SQL applied
[ ] Supabase URL copied
[ ] Secret key copied privately
```

## Local

```text
[ ] Python installed
[ ] Git installed
[ ] Repository cloned
[ ] .venv created
[ ] Dependencies installed
[ ] .env configured
[ ] Admin Bot tested
[ ] Customer Bot tested
[ ] Expiry Worker tested
[ ] web_runner.py tested
[ ] /health returns healthy
```

## Render

```text
[ ] GitHub repository connected
[ ] main branch selected
[ ] Build command configured
[ ] Start command configured
[ ] Free plan selected
[ ] Five environment variables configured
[ ] Deployment successful
[ ] /health returns healthy
```

## UptimeRobot

```text
[ ] HTTP(s) monitor created
[ ] /health URL configured
[ ] 5-minute interval selected
[ ] Monitor shows UP
```

## Final

```text
[ ] Admin Bot works
[ ] Customer Bot works
[ ] Supabase works
[ ] Payment flow works
[ ] Admin approval works
[ ] Course access works
[ ] Broadcast works
[ ] Expiry worker works
[ ] No TelegramConflictError
```

---

# 36. One-command quick reference

After the first setup, these are the most useful PowerShell commands:

```powershell
# Enter project
cd "D:\telegram-course-manager"

# Activate environment
.\.venv\Scripts\Activate.ps1

# Install/update dependencies
pip install -r requirements.txt

# Run Admin Bot
python -m app.admin_bot.main

# Run Customer Bot
python -m app.customer_bot.main

# Run Expiry Worker
python -m app.expiry_worker

# Run combined Render-style local runner
python web_runner.py

# Check Python processes
Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" | Select-Object ProcessId,CommandLine

# Check Git
git status

# Update from GitHub
git pull origin main
```

---

# 37. Final architecture for your own instance

```text
                  YOUR GITHUB
                       │
                       ▼
              YOUR RENDER SERVICE
                       │
                  web_runner.py
                       │
        ┌──────────────┼──────────────┐
        ▼              ▼              ▼
     ADMIN          CUSTOMER        EXPIRY
      BOT             BOT           WORKER
        │              │              │
        └──────────────┼──────────────┘
                       ▼
                   SUPABASE
                       ▲
                       │
                  /health
                       ▲
                       │
                  UptimeRobot
```

Every person using this project should create:

```text
their own Telegram bots
their own Supabase project
their own Render service
their own secrets
```

Do not share your production credentials.

---

# 38. If something fails

Do not randomly delete files or databases.

Collect:

1. The exact PowerShell command used.
2. The exact error.
3. The Render log around the error.
4. Whether the Admin Bot, Customer Bot, or Expiry Worker is affected.

Then troubleshoot that specific component.

