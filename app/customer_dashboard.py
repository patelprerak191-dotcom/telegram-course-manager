import base64
import hashlib
import hmac
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from urllib.request import Request as UrlRequest, urlopen

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request, UploadFile, File, Form
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from supabase import create_client

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_SECRET_KEY = os.getenv("SUPABASE_SECRET_KEY")
CUSTOMER_BOT_TOKEN = os.getenv("CUSTOMER_BOT_TOKEN")
CUSTOMER_BOT_USERNAME = os.getenv("CUSTOMER_BOT_USERNAME", "")
_RESOLVED_BOT_USERNAME = CUSTOMER_BOT_USERNAME.strip().lstrip("@")
SESSION_SECRET = os.getenv("CUSTOMER_DASHBOARD_SECRET") or SUPABASE_SECRET_KEY
QR_BUCKET = "payment-qr"

if not SUPABASE_URL or not SUPABASE_SECRET_KEY:
    raise RuntimeError("SUPABASE_URL and SUPABASE_SECRET_KEY are required")
if not CUSTOMER_BOT_TOKEN:
    raise RuntimeError("CUSTOMER_BOT_TOKEN is required")
if not SESSION_SECRET:
    raise RuntimeError("CUSTOMER_DASHBOARD_SECRET is required")

supabase = create_client(SUPABASE_URL, SUPABASE_SECRET_KEY)
app = FastAPI(title="Telegram Course Manager - Customer Dashboard")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])


def sign(value: str) -> str:
    return hmac.new(SESSION_SECRET.encode(), value.encode(), hashlib.sha256).hexdigest()


def make_session(user_id: str, telegram_user_id: int) -> str:
    payload = {"uid": user_id, "tid": telegram_user_id, "exp": int(time.time()) + 60 * 60 * 24 * 30}
    raw = base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode()).decode().rstrip("=")
    return raw + "." + sign(raw)


def read_session(request: Request) -> Optional[dict]:
    token = request.cookies.get("customer_session")
    if not token or "." not in token:
        return None
    raw, sig = token.rsplit(".", 1)
    if not hmac.compare_digest(sign(raw), sig):
        return None
    try:
        pad = "=" * (-len(raw) % 4)
        data = json.loads(base64.urlsafe_b64decode(raw + pad))
        if int(data.get("exp", 0)) < int(time.time()):
            return None
        return data
    except Exception:
        return None


def require_user(request: Request) -> dict:
    session = read_session(request)
    if not session:
        raise HTTPException(status_code=401, detail="Login with Telegram is required.")
    resp = supabase.table("users").select("id,telegram_user_id,username,first_name,last_name,is_blocked").eq("id", session["uid"]).limit(1).execute()
    if not resp.data:
        raise HTTPException(status_code=401, detail="Customer account not found.")
    user = resp.data[0]
    if user.get("is_blocked"):
        raise HTTPException(status_code=403, detail="Your account is blocked.")
    return user


def verify_telegram_login(data: dict) -> bool:
    received_hash = str(data.get("hash", ""))
    if not received_hash:
        return False
    check = {k: str(v) for k, v in data.items() if k != "hash" and v is not None}
    data_check_string = "\n".join(f"{k}={check[k]}" for k in sorted(check))
    secret_key = hashlib.sha256(CUSTOMER_BOT_TOKEN.encode()).digest()
    expected = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, received_hash):
        return False
    try:
        if int(time.time()) - int(data.get("auth_date", 0)) > 86400:
            return False
    except Exception:
        return False
    return True


def money(value, currency="INR"):
    try:
        return f"₹{float(value):,.2f}" if currency == "INR" else f"{currency} {float(value):,.2f}"
    except Exception:
        return str(value)


def duration(plan):
    return "Lifetime" if plan.get("plan_type") == "lifetime" else f"{plan.get('duration_days') or '—'} days"


@app.get("/health")
async def health():
    return {"status": "ok", "service": "customer-dashboard"}


@app.post("/api/auth/telegram")
async def telegram_auth(payload: dict, request: Request):
    if not verify_telegram_login(payload):
        raise HTTPException(status_code=401, detail="Telegram login verification failed.")
    tid = int(payload["id"])
    existing = supabase.table("users").select("id,is_blocked").eq("telegram_user_id", tid).limit(1).execute()
    fields = {
        "telegram_user_id": tid,
        "username": payload.get("username"),
        "first_name": payload.get("first_name"),
        "last_name": payload.get("last_name"),
        "language_code": payload.get("language_code"),
        "last_seen_at": datetime.now(timezone.utc).isoformat(),
    }
    if existing.data:
        user = supabase.table("users").update(fields).eq("id", existing.data[0]["id"]).execute().data[0]
    else:
        fields["is_blocked"] = False
        user = supabase.table("users").insert(fields).execute().data[0]
    if user.get("is_blocked"):
        raise HTTPException(status_code=403, detail="Your account is blocked.")
    response = {"ok": True, "user": user}
    from fastapi.responses import JSONResponse
    out = JSONResponse(response)
    out.set_cookie("customer_session", make_session(user["id"], tid), httponly=True, secure=request.url.scheme == "https", samesite="lax", max_age=60*60*24*30)
    return out


@app.post("/api/logout")
async def logout():
    from fastapi.responses import JSONResponse
    out = JSONResponse({"ok": True})
    out.delete_cookie("customer_session")
    return out


@app.get("/api/me")
async def me(request: Request):
    try:
        return {"logged_in": True, "user": require_user(request)}
    except HTTPException:
        return {"logged_in": False}


@app.get("/api/courses")
async def courses():
    response = supabase.table("courses").select("id,name,description,status,slug,sort_order,created_at").eq("status", "active").order("sort_order").order("created_at", desc=True).execute()
    items = []
    for c in response.data or []:
        plans = supabase.table("plans").select("id,name,price,currency,plan_type,duration_days,description,is_active").eq("course_id", c["id"]).eq("is_active", True).order("price").execute().data or []
        items.append({"course": c, "plans": plans})
    return items


@app.get("/api/courses/{course_id}")
async def course_detail(course_id: str):
    c = supabase.table("courses").select("id,name,description,status,slug,sort_order,created_at").eq("id", course_id).eq("status", "active").limit(1).execute()
    if not c.data:
        raise HTTPException(status_code=404, detail="Course not found.")
    plans = supabase.table("plans").select("id,course_id,name,price,currency,plan_type,duration_days,description,is_active,qr_code_path").eq("course_id", course_id).eq("is_active", True).order("price").execute().data or []
    for p in plans:
        p.pop("qr_code_path", None)
    return {"course": c.data[0], "plans": plans}


@app.get("/api/plans/{plan_id}/qr")
async def plan_qr(plan_id: str):
    p = supabase.table("plans").select("id,qr_code_path,is_active").eq("id", plan_id).limit(1).execute()
    if not p.data or not p.data[0].get("is_active") or not p.data[0].get("qr_code_path"):
        raise HTTPException(status_code=404, detail="Payment QR not available.")
    result = supabase.storage.from_(QR_BUCKET).create_signed_url(p.data[0]["qr_code_path"], 900)
    url = result.get("signedURL") or result.get("signedUrl") or result.get("signed_url")
    if not url:
        raise HTTPException(status_code=500, detail="Could not create payment QR URL.")
    return {"url": url}


@app.get("/api/my-courses")
async def my_courses(request: Request):
    user = require_user(request)
    subs = supabase.table("subscriptions").select("id,course_id,plan_id,status,started_at,expires_at,is_lifetime,joined_channel_at,revoked_at,payment_request_id").eq("user_id", user["id"]).order("created_at", desc=True).execute().data or []
    out=[]
    seen=set()
    for s in subs:
        if s.get("course_id") in seen: continue
        seen.add(s.get("course_id"))
        c=supabase.table("courses").select("id,name,description,status,slug").eq("id",s["course_id"]).limit(1).execute().data
        p=supabase.table("plans").select("id,name,price,currency,plan_type,duration_days").eq("id",s["plan_id"]).limit(1).execute().data
        if c:
            out.append({"subscription":s,"course":c[0],"plan":p[0] if p else None})
    return out


@app.get("/api/my-courses/{course_id}")
async def my_course_detail(course_id: str, request: Request):
    user = require_user(request)
    subq=supabase.table("subscriptions").select("id,course_id,plan_id,status,started_at,expires_at,is_lifetime,joined_channel_at,revoked_at").eq("user_id",user["id"]).eq("course_id",course_id).eq("status","active").order("created_at",desc=True).limit(1).execute()
    if not subq.data: raise HTTPException(status_code=403, detail="You do not have active access to this course.")
    sub=subq.data[0]
    c=supabase.table("courses").select("id,name,description,status,slug").eq("id",course_id).limit(1).execute().data
    lessons=supabase.table("course_lessons").select("id,course_id,title,description,content_type,content_url,telegram_message_id,sort_order,is_published").eq("course_id",course_id).eq("is_published",True).order("sort_order").order("created_at").execute().data or []
    invite=supabase.table("invite_links").select("id,telegram_invite_link,status,expires_at,created_at").eq("subscription_id",sub["id"]).order("created_at",desc=True).limit(1).execute().data
    return {"subscription":sub,"course":c[0] if c else None,"lessons":lessons,"invite":invite[0] if invite else None}


@app.get("/api/my-payments")
async def my_payments(request: Request):
    user=require_user(request)
    rows=supabase.table("payment_requests").select("id,payment_number,course_id,plan_id,amount,currency,status,submitted_at,reviewed_at,rejection_reason,admin_note").eq("user_id",user["id"]).order("submitted_at",desc=True).limit(100).execute().data or []
    for x in rows:
        c=supabase.table("courses").select("name").eq("id",x["course_id"]).limit(1).execute().data
        p=supabase.table("plans").select("name,plan_type,duration_days").eq("id",x["plan_id"]).limit(1).execute().data
        x["course_name"]=c[0]["name"] if c else "Unknown"
        x["plan_name"]=p[0]["name"] if p else "Unknown"
    return rows


@app.post("/api/payments")
async def create_payment(request: Request, plan_id: str = Form(...), screenshot: UploadFile = File(...)):
    user=require_user(request)
    if not screenshot.content_type or not screenshot.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="Payment proof must be an image.")
    content=await screenshot.read()
    if not content or len(content)>10*1024*1024:
        raise HTTPException(status_code=400, detail="Payment proof must be between 1 byte and 10 MB.")
    p=supabase.table("plans").select("id,course_id,name,price,currency,plan_type,duration_days,is_active").eq("id",plan_id).limit(1).execute()
    if not p.data or not p.data[0].get("is_active"):
        raise HTTPException(status_code=404, detail="Plan is unavailable.")
    plan=p.data[0]
    c=supabase.table("courses").select("id,name,status").eq("id",plan["course_id"]).eq("status","active").limit(1).execute()
    if not c.data: raise HTTPException(status_code=404, detail="Course is unavailable.")
    active=supabase.table("subscriptions").select("id,is_lifetime,status").eq("user_id",user["id"]).eq("course_id",plan["course_id"]).eq("status","active").limit(1).execute().data
    if active and active[0].get("is_lifetime"):
        raise HTTPException(status_code=409, detail="You already have lifetime access to this course.")
    ext=screenshot.filename or "proof.jpg"
    ext=Path(ext).suffix.lower() if Path(ext).suffix else ".jpg"
    path=f"payments/{user['id']}_{int(time.time())}_{hashlib.sha1(content).hexdigest()[:10]}{ext}"
    try:
        supabase.storage.from_(QR_BUCKET).upload(path,content,{"content-type":screenshot.content_type,"upsert":"false"})
        payment=supabase.table("payment_requests").insert({"user_id":user["id"],"course_id":plan["course_id"],"plan_id":plan_id,"amount":plan["price"],"currency":plan.get("currency") or "INR","status":"pending","screenshot_path":path}).execute().data
        if not payment: raise RuntimeError("Payment request was not created.")
        return {"ok":True,"payment":payment[0]}
    except Exception:
        try: supabase.storage.from_(QR_BUCKET).remove([path])
        except Exception: pass
        raise


def resolve_customer_bot_username() -> str:
    """Use configured username, otherwise safely resolve it from CUSTOMER_BOT_TOKEN."""
    global _RESOLVED_BOT_USERNAME
    if _RESOLVED_BOT_USERNAME:
        return _RESOLVED_BOT_USERNAME
    try:
        req = UrlRequest(
            f"https://api.telegram.org/bot{CUSTOMER_BOT_TOKEN}/getMe",
            headers={"User-Agent": "Telegram-Course-Manager/1.0"},
            method="GET",
        )
        with urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        username = ((data.get("result") or {}).get("username") or "").strip().lstrip("@")
        if username:
            _RESOLVED_BOT_USERNAME = username
    except Exception:
        pass
    return _RESOLVED_BOT_USERNAME


@app.get("/", response_class=HTMLResponse)
async def index():
    username = resolve_customer_bot_username()
    return HTML.replace("__BOT_USERNAME__", username)


HTML = r'''<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>CourseFlow — Learn</title>
<script src="https://telegram.org/js/telegram-widget.js?22" data-telegram-login="__BOT_USERNAME__" data-size="large" data-userpic="false" data-request-access="write" data-onauth="onTelegramAuth(user)" async></script>
<style>
*{box-sizing:border-box}body{margin:0;font-family:Inter,Segoe UI,system-ui,sans-serif;color:#eef2ff;background:#060811}button,input{font:inherit}.shell{min-height:100vh;background:radial-gradient(900px 500px at 10% -10%,#7c3aed28,transparent),radial-gradient(800px 500px at 90% 0,#06b6d422,transparent),#060811}.nav{height:70px;display:flex;align-items:center;justify-content:space-between;padding:0 5%;border-bottom:1px solid #ffffff12;background:#080b14cc;backdrop-filter:blur(18px);position:sticky;top:0;z-index:20}.logo{display:flex;align-items:center;gap:10px;font-weight:800}.mark{width:36px;height:36px;border-radius:12px;background:linear-gradient(135deg,#8b5cf6,#22d3ee);display:grid;place-items:center}.mark svg{width:22px}.navlinks{display:flex;gap:8px}.nav button,.btn{border:1px solid #ffffff14;background:#ffffff08;color:#dbe4f0;padding:10px 14px;border-radius:11px;cursor:pointer}.btn.primary{background:linear-gradient(135deg,#8b5cf6,#6366f1);border-color:#a78bfa66;color:#fff}.nav button:hover,.btn:hover{transform:translateY(-1px);border-color:#a78bfa55}.container{width:min(1180px,92%);margin:auto}.hero{padding:70px 0 36px}.eyebrow{color:#a78bfa;font-size:12px;font-weight:800;letter-spacing:.15em;text-transform:uppercase}.hero h1{font-size:clamp(36px,6vw,68px);line-height:1.02;letter-spacing:-.05em;margin:12px 0}.hero p{color:#94a3b8;max-width:680px;font-size:17px;line-height:1.7}.grid{display:grid;grid-template-columns:repeat(3,1fr);gap:16px;padding-bottom:60px}.card{border:1px solid #ffffff12;border-radius:20px;background:linear-gradient(145deg,#101726ee,#0b1019ee);padding:22px;box-shadow:0 20px 55px #0005}.card h3{margin:8px 0}.muted{color:#94a3b8;line-height:1.6}.price{font-size:26px;font-weight:800;margin:16px 0 5px}.badge{display:inline-flex;border:1px solid #34d39933;background:#34d39912;color:#6ee7b7;border-radius:999px;padding:5px 9px;font-size:11px}.modal{position:fixed;inset:0;background:#02040bd9;backdrop-filter:blur(10px);display:none;align-items:center;justify-content:center;padding:18px;z-index:50}.modal.open{display:flex}.box{width:min(760px,100%);max-height:90vh;overflow:auto;border:1px solid #ffffff1c;border-radius:22px;background:#0d1420;box-shadow:0 40px 120px #000b;padding:26px}.row{display:flex;justify-content:space-between;gap:14px;align-items:center}.plans{display:grid;gap:12px;margin-top:18px}.plan{border:1px solid #ffffff12;border-radius:15px;padding:16px;background:#ffffff05;display:flex;justify-content:space-between;align-items:center;gap:12px}.mygrid{display:grid;grid-template-columns:repeat(2,1fr);gap:15px}.lesson{padding:15px;border:1px solid #ffffff10;border-radius:14px;background:#ffffff04}.progress{height:7px;background:#ffffff0c;border-radius:99px;overflow:hidden}.progress i{display:block;height:100%;background:linear-gradient(90deg,#8b5cf6,#22d3ee)}.hidden{display:none}.login{display:flex;align-items:center;gap:10px}.telegram-login{display:flex;align-items:center;gap:8px;min-height:40px}.tg-fallback{display:none}.empty{padding:40px;text-align:center;color:#94a3b8;border:1px dashed #ffffff18;border-radius:16px}.qr{max-width:260px;width:100%;border-radius:16px;background:white;padding:8px}.file{border:1px dashed #ffffff20;padding:15px;border-radius:13px;width:100%;color:#cbd5e1}.status{font-size:12px;padding:5px 9px;border-radius:999px;background:#fbbf2415;color:#fcd34d}.status.approved{background:#34d39915;color:#6ee7b7}.status.rejected{background:#fb718515;color:#fda4af}.toast{position:fixed;right:18px;bottom:18px;padding:13px 16px;border:1px solid #ffffff18;background:#111827f2;border-radius:13px;display:none;z-index:100}.toast.show{display:block}@media(max-width:850px){.grid,.mygrid{grid-template-columns:1fr}.navlinks{display:none}.hero{padding-top:45px}.plan{align-items:flex-start;flex-direction:column}.box{padding:20px}}
</style></head><body><div class="shell"><nav class="nav"><div class="logo"><div class="mark"><svg viewBox="0 0 48 48"><path d="M11 19 37 10 29 36l-7-9-8 5 3-8-6-5Z" fill="white"/></svg></div><span>CourseFlow</span></div><div class="navlinks"><button type="button" onclick="show('catalog')">Courses</button><button type="button" onclick="show('learning')">My Learning</button><button type="button" onclick="show('payments')">Payments</button></div><div class="login" id="loginBox">
<div id="tgLoginWidget" class="telegram-login">
<span class="muted" style="font-size:13px;margin-right:4px">Login with Telegram</span>
<script async src="https://telegram.org/js/telegram-widget.js?22" data-telegram-login="__BOT_USERNAME__" data-size="small" data-userpic="false" data-request-access="write" data-onauth="onTelegramAuth(user)"></script><button id="tgOpenBot" type="button" class="btn primary tg-fallback" onclick="openTelegramBot()">Open Telegram</button>
</div></div></nav>
<main class="container"><section id="catalog" class="view"><div class="hero"><div class="eyebrow">Learn • Purchase • Access</div><h1>Your courses,<br><span style="color:#a78bfa">all in one place.</span></h1><p>Browse available courses, choose a plan, pay securely with the existing payment workflow, and manage your learning access from one dashboard.</p></div><div class="row" style="margin:18px 0;justify-content:flex-end"><button type="button" class="btn" onclick="loadCatalog()">Refresh Courses</button></div><div id="catalogGrid" class="grid"></div></section>
<section id="learning" class="view hidden"><div class="hero"><div class="eyebrow">My Learning</div><h1>Continue learning.</h1><p>Your active subscriptions and Telegram course access appear here.</p></div><div id="myCourses" class="mygrid"></div></section>
<section id="payments" class="view hidden"><div class="hero"><div class="eyebrow">Payments</div><h1>Payment history.</h1><p>Track submitted payments and verification status.</p></div><div id="myPayments"></div></section></main></div>
<div id="modal" class="modal"><div class="box" id="modalBox"></div></div><div id="toast" class="toast"></div>
<script>
const BOT='__BOT_USERNAME__';let me=null,courses=[];
const esc=s=>String(s??'').replace(/[&<>'"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]));
function toast(t){const x=document.getElementById('toast');x.textContent=t;x.classList.add('show');setTimeout(()=>x.classList.remove('show'),3200)}
async function api(u,o){
  const r=await fetch(u,o);
  const text=await r.text();
  let d={};
  try{d=JSON.parse(text)}catch(_){d={detail:text||('HTTP '+r.status)}}
  if(!r.ok)throw Error(d.detail||'Request failed');
  return d;
}
function show(id){document.querySelectorAll('.view').forEach(x=>x.classList.add('hidden'));document.getElementById(id).classList.remove('hidden');if(id==='learning')loadMyCourses();if(id==='payments')loadPayments()}
function openTelegramBot(){window.open('https://t.me/'+BOT,'_blank','noopener,noreferrer')}
setTimeout(()=>{const w=document.getElementById('tgLoginWidget');const iframe=w&&w.querySelector('iframe');const f=document.getElementById('tgOpenBot');if(f&&!iframe)f.style.display='inline-flex'},2500);
function onTelegramAuth(user){api('/customer/api/auth/telegram',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(user)}).then(d=>{me=d.user;renderLogin();loadMyCourses();toast('Logged in successfully')}).catch(e=>toast(e.message))}
function renderLogin(){
  const b=document.getElementById('loginBox');
  if(me){
    b.innerHTML=`<span class="muted">${esc(me.first_name||me.username||'Customer')}</span><button type="button" class="btn" onclick="logout()">Logout</button>`;
  }
}
async function logout(){await api('/customer/api/logout',{method:'POST'});location.reload()}
async function loadCatalog(){try{courses=await api('/customer/api/courses');document.getElementById('catalogGrid').innerHTML=courses.length?courses.map(x=>{const c=x.course;const p=x.plans[0];return `<article class="card"><span class="badge">Available</span><h3>${esc(c.name)}</h3><p class="muted">${esc(c.description||'Explore this course and choose a plan.')}</p><div class="price">${p?esc(p.currency||'INR')+' '+esc(p.price):'Plans available'}</div><div class="muted">${p?esc(p.plan_type==='lifetime'?'Lifetime':(p.duration_days||'')+' days'):'Multiple plans'}</div><button class="btn primary" style="margin-top:18px;width:100%" onclick="openCourse('${c.id}')">View Course</button></article>`}).join(''):'<div class="empty" style="grid-column:1/-1">No courses available yet.</div>'}catch(e){toast(e.message)}}
async function openCourse(id){try{const d=await api('/customer/api/courses/'+id);document.getElementById('modalBox').innerHTML=`<div class="row"><div><div class="eyebrow">Course</div><h2>${esc(d.course.name)}</h2></div><button class="btn" onclick="closeModal()">✕</button></div><p class="muted">${esc(d.course.description||'')}</p><div class="plans">${d.plans.map(p=>`<div class="plan"><div><b>${esc(p.name)}</b><div class="muted">${esc(p.description||'')}<br>${esc(p.plan_type==='lifetime'?'Lifetime':(p.duration_days||'')+' days')}</div><div class="price">${esc(p.currency||'INR')} ${esc(p.price)}</div></div><button class="btn primary" onclick="buy('${p.id}','${d.course.id}')">Buy Now</button></div>`).join('')}</div>`;openModal()}catch(e){toast(e.message)}}
async function buy(planId,courseId){if(!me){toast('Please login with Telegram first');return}try{const d=await api('/customer/api/plans/'+planId+'/qr');document.getElementById('modalBox').innerHTML=`<div class="row"><h2>Complete Payment</h2><button class="btn" onclick="closeModal()">✕</button></div><p class="muted">Scan the QR and pay the exact plan amount. Then upload your payment screenshot.</p><img class="qr" src="${d.url}" alt="Payment QR"><form onsubmit="submitPayment(event,'${planId}')" style="margin-top:18px"><input class="file" type="file" name="screenshot" accept="image/*" required><button class="btn primary" style="width:100%;margin-top:12px">Submit Payment Proof</button></form>`}catch(e){toast(e.message)}}
async function submitPayment(e,planId){e.preventDefault();const fd=new FormData(e.target);fd.append('plan_id',planId);try{const d=await api('/customer/api/payments',{method:'POST',body:fd});closeModal();toast('Payment submitted — waiting for verification');show('payments');loadPayments()}catch(x){toast(x.message)}}
async function loadMyCourses(){if(!me){document.getElementById('myCourses').innerHTML='<div class="empty" style="grid-column:1/-1">Login with Telegram to see your courses.</div>';return}try{const rows=await api('/customer/api/my-courses');document.getElementById('myCourses').innerHTML=rows.length?rows.map(x=>`<article class="card"><span class="badge">${esc(x.subscription.is_lifetime?'Lifetime':'Active')}</span><h3>${esc(x.course.name)}</h3><p class="muted">${esc(x.plan?.name||'Course access')}</p><div class="muted">${x.subscription.is_lifetime?'Never expires':('Expires: '+esc(x.subscription.expires_at||'—'))}</div><button class="btn primary" style="margin-top:16px" onclick="openLearning('${x.course.id}')">Open Course</button></article>`).join(''):'<div class="empty" style="grid-column:1/-1">You have no active courses yet.</div>'}catch(e){toast(e.message)}}
async function openLearning(id){try{const d=await api('/customer/api/my-courses/'+id);document.getElementById('modalBox').innerHTML=`<div class="row"><div><div class="eyebrow">My Course</div><h2>${esc(d.course.name)}</h2></div><button class="btn" onclick="closeModal()">✕</button></div><p class="muted">${esc(d.course.description||'')}</p>${d.invite?`<div class="card" style="margin:18px 0"><b>Telegram Access</b><p class="muted">Your course invite link is ready.</p><a class="btn primary" href="${esc(d.invite.telegram_invite_link)}" target="_blank">Join Telegram Course</a></div>`:''}<h3>Lessons</h3><div class="plans">${d.lessons.length?d.lessons.map((l,i)=>`<div class="lesson"><b>${i+1}. ${esc(l.title)}</b><div class="muted">${esc(l.description||'')}</div>${l.content_url?`<a class="btn" style="margin-top:9px" href="${esc(l.content_url)}" target="_blank">Open Lesson</a>`:''}</div>`).join(''):'<div class="empty">No published lessons yet.</div>'}</div>`;openModal()}catch(e){toast(e.message)}}
async function loadPayments(){if(!me){document.getElementById('myPayments').innerHTML='<div class="empty">Login with Telegram to see payment history.</div>';return}try{const rows=await api('/customer/api/my-payments');document.getElementById('myPayments').innerHTML=rows.length?rows.map(x=>`<div class="card" style="margin-bottom:12px"><div class="row"><div><b>#${esc(x.payment_number||x.id)}</b><div class="muted">${esc(x.course_name)} · ${esc(x.plan_name)}</div></div><span class="status ${x.status}">${esc(x.status)}</span></div><div class="price">${esc(x.currency||'INR')} ${esc(x.amount)}</div><div class="muted">${esc(x.submitted_at||'')}</div>${x.rejection_reason?`<div class="muted">Reason: ${esc(x.rejection_reason)}</div>`:''}</div>`).join(''):'<div class="empty">No payments yet.</div>'}catch(e){toast(e.message)}}
function openModal(){document.getElementById('modal').classList.add('open')}function closeModal(){document.getElementById('modal').classList.remove('open')}document.getElementById('modal').addEventListener('click',e=>{if(e.target.id==='modal')closeModal()});
(async function init(){
  try{
    const d=await api('/customer/api/me');
    if(d.logged_in) me=d.user;
  }catch(e){
    console.warn('Customer session check failed:',e);
  }
  renderLogin();
  await loadCatalog();
  if(me) await loadMyCourses();
})()
</script></body></html>'''
