"""
TOOL AI SEW PRO — Bot Telegram Dự Đoán Tài Xỉu / Sicbo / Baccarat
Sử dụng: Python + aiogram 3.x
"""

import asyncio
import logging
import json
import os
import random
import re
import string
import unicodedata
from datetime import datetime, timedelta, timezone
from typing import Optional

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart, Command
from aiogram.client.default import DefaultBotProperties
from aiogram.types import (
    Message, CallbackQuery,
    InlineKeyboardMarkup, InlineKeyboardButton,
    FSInputFile
)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from fastapi import FastAPI, Request
import uvicorn

import requests

# ─────────────────────────────────────────────
#  CẤU HÌNH
# ─────────────────────────────────────────────
BOT_TOKEN  = "8293331183:AAFbaUlOIms2ioHPgUpEF78q8zkPWTXnBvA"
ADMIN_ID   = 7219600109
ADMIN_LINK = "https://t.me/sewdangcap"
BANK_STK   = "0886027767"

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger(__name__)

# ─────────────────────────────────────────────
#  LƯU TRỮ KEY BẰNG JSON
# ─────────────────────────────────────────────
DATA_FILE = "bot_data.json"

valid_keys: dict[int, dict] = {}
all_users: set[int] = set()
payment_history: dict[int, list] = {}
user_balances: dict[int, int] = {}

# ─────────────────────────────────────────────
#  AUTO-PREDICT: subscriptions & session cache
# ─────────────────────────────────────────────
auto_subs: dict[int, set] = {}          # uid → set of "game_id" hoặc "bcr_table_N"
last_session_cache: dict[str, str] = {} # game_key → last seen session

# ─────────────────────────────────────────────
#  AUTO-DELETE: theo dõi tin nhắn để tự xóa
# ─────────────────────────────────────────────
# msg_tracker: uid → list of {"chat_id": int, "msg_id": int, "sent_at": datetime}
msg_tracker: dict[int, list] = {}
# pending_qr: uid → {"chat_id": int, "msg_id": int, "task": asyncio.Task | None}
pending_qr: dict[int, dict] = {}

MSG_TTL_HOURS = 3          # Xóa tin nhắn cũ hơn N giờ
QR_EXPIRE_MINUTES = 10     # QR hết hạn sau N phút

key_store: dict[str, dict] = {
    "VIP-TEST-2024": {"duration_days": 1,   "used_by": None},
    "VIP-WEEK-001" : {"duration_days": 7,   "used_by": None},
    "VIP-MONTH-001": {"duration_days": 30,  "used_by": None},
    "VIP-FOREVER-1": {"duration_days": -1,  "used_by": None},
}

def save_data():
    vk_json = {}
    for uid, info in valid_keys.items():
        exp = info.get("expires")
        vk_json[str(uid)] = {
            "key": info["key"],
            "expires": exp.isoformat() if isinstance(exp, datetime) else None
        }
    subs_json = {str(k): list(v) for k, v in auto_subs.items()}
    data = {
        "valid_keys": vk_json,
        "key_store": key_store,
        "all_users": list(all_users),
        "payment_history": payment_history,
        "user_balances": user_balances,
        "auto_subs": subs_json,
    }
    try:
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4, ensure_ascii=False)
    except Exception as e:
        log.error(f"Lỗi lưu file: {e}")

def load_data():
    global valid_keys, key_store, all_users, payment_history, user_balances
    if not os.path.exists(DATA_FILE):
        save_data()
        return
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if "key_store" in data:
            key_store.clear()
            key_store.update(data["key_store"])
        if "valid_keys" in data:
            valid_keys.clear()
            for uid_str, info in data["valid_keys"].items():
                exp_str = info.get("expires")
                expires = datetime.fromisoformat(exp_str) if exp_str else None
                valid_keys[int(uid_str)] = {"key": info["key"], "expires": expires}
        if "all_users" in data:
            all_users.clear()
            all_users.update(data["all_users"])
        if "payment_history" in data:
            payment_history.clear()
            payment_history.update({int(k): v for k, v in data["payment_history"].items()})
        if "user_balances" in data:
            user_balances.clear()
            user_balances.update({int(k): int(v) for k, v in data["user_balances"].items()})
        if "auto_subs" in data:
            auto_subs.clear()
            auto_subs.update({int(k): set(v) for k, v in data["auto_subs"].items()})
    except Exception as e:
        log.error(f"Lỗi đọc file: {e}")

load_data()

# ─────────────────────────────────────────────
#  DANH SÁCH GAME
# ─────────────────────────────────────────────
GAMES = [
    {"id": "sunwin_tx",    "name": "🎯 Tài Xỉu Sunwin",      "type": "taixiu"},
    {"id": "hitclub_hu",   "name": "🎰 TX Hitclub Hũ",        "type": "taixiu"},
    {"id": "hitclub_md5",  "name": "🔐 TX Hitclub MD5",       "type": "taixiu"},
    {"id": "baccarat_sexy","name": "👠 Baccarat Sexy",        "type": "baccarat"},
    {"id": "789club_tx",   "name": "🎲 TX 789Club",           "type": "taixiu"},
    {"id": "lc79_md5",     "name": "🔑 TX lc79 MD5",          "type": "taixiu"},
    {"id": "lc79_hu",      "name": "🏺 TX lc79 Hũ",           "type": "taixiu"},
    {"id": "sunwin_sicbo", "name": "🎱 Sicbo Sunwin",         "type": "sicbo"},
    {"id": "789_sicbo",    "name": "🎳 Sicbo 789Club",        "type": "sicbo"},
    {"id": "hit_sicbo",    "name": "🎯 Sicbo Hitclub",        "type": "sicbo"},
    {"id": "68gb_xanh",    "name": "🟢 68gb Bàn Xanh",       "type": "taixiu"},
    {"id": "68gb_md5",     "name": "🔒 68gb Bàn MD5",        "type": "taixiu"},
    {"id": "luck8",        "name": "🍀 Luck8",                "type": "taixiu"},
    {"id": "b52_tx",       "name": "✈️ TX B52",               "type": "taixiu"},
    {"id": "bcr_sunwin",   "name": "🏆 Baccarat Sunwin",       "type": "baccarat_sunwin"},
]

GAME_MAP = {g["id"]: g for g in GAMES}

# ─────────────────────────────────────────────
#  FSM STATES
# ─────────────────────────────────────────────
class UserState(StatesGroup):
    waiting_key = State()

class AdminState(StatesGroup):
    addkey_key = State()
    addkey_days = State()
    genkeys_amount = State()
    genkeys_days = State()
    delkey_key = State()
    broadcast_text = State()

# ─────────────────────────────────────────────
#  HELPER
# ─────────────────────────────────────────────
def is_authorized(user_id: int) -> bool:
    if user_id == ADMIN_ID:
        return True
    info = valid_keys.get(user_id)
    if not info:
        return False
    exp = info.get("expires")
    if exp is None:
        return True
    return datetime.now() < exp

def key_expire_str(user_id: int) -> str:
    if user_id == ADMIN_ID:
        return "Vĩnh viễn (Admin)"
    info = valid_keys.get(user_id)
    if not info:
        return "Chưa kích hoạt"
    exp = info.get("expires")
    if exp is None:
        return "Vĩnh viễn ♾️"
    remaining = exp - datetime.now()
    days = remaining.days
    hours = remaining.seconds // 3600
    return f"{days} ngày {hours} giờ"

def clean_memo(name: str) -> str:
    n = unicodedata.normalize('NFKD', name).encode('ASCII', 'ignore').decode('utf-8')
    clean = ''.join(e for e in n if e.isalnum()).upper()
    return clean if clean else "USER"

VN_TZ = timezone(timedelta(hours=7))

def now_vn() -> datetime:
    """Trả về datetime hiện tại theo giờ Việt Nam (UTC+7)."""
    return datetime.now(VN_TZ)

# ─────────────────────────────────────────────
#  HELPER: Track & Auto-delete tin nhắn
# ─────────────────────────────────────────────
def track_message(uid: int, chat_id: int, msg_id: int):
    """Lưu tin nhắn vào tracker để auto-xóa sau MSG_TTL_HOURS giờ."""
    if uid not in msg_tracker:
        msg_tracker[uid] = []
    msg_tracker[uid].append({
        "chat_id": chat_id,
        "msg_id" : msg_id,
        "sent_at": now_vn(),
    })
    # Giới hạn tối đa 200 tin nhắn/user để tránh tràn bộ nhớ
    if len(msg_tracker[uid]) > 200:
        msg_tracker[uid] = msg_tracker[uid][-200:]

async def auto_delete_old_messages():
    """Background task: mỗi 5 phút scan và xóa tin nhắn cũ > MSG_TTL_HOURS giờ."""
    log.info("🗑 Bắt đầu auto-delete background task...")
    while True:
        await asyncio.sleep(300)  # 5 phút
        cutoff = now_vn() - timedelta(hours=MSG_TTL_HOURS)
        deleted = 0
        for uid in list(msg_tracker.keys()):
            remaining = []
            for entry in msg_tracker[uid]:
                if entry["sent_at"] < cutoff:
                    try:
                        await bot.delete_message(entry["chat_id"], entry["msg_id"])
                        deleted += 1
                        await asyncio.sleep(0.05)
                    except Exception:
                        pass  # Tin nhắn đã xóa rồi hoặc không có quyền — bỏ qua
                else:
                    remaining.append(entry)
            if remaining:
                msg_tracker[uid] = remaining
            else:
                msg_tracker.pop(uid, None)
        if deleted:
            log.info(f"🗑 Auto-delete: đã xóa {deleted} tin nhắn cũ > {MSG_TTL_HOURS}h")

async def delete_qr_for_user(uid: int, reason: str = "expired"):
    """Xóa QR đang pending của user và gửi thông báo tương ứng."""
    entry = pending_qr.pop(uid, None)
    if not entry:
        return
    # Huỷ expire task nếu còn đang chạy
    task = entry.get("task")
    if task and not task.done():
        task.cancel()
    # Xóa tin nhắn QR
    try:
        await bot.delete_message(entry["chat_id"], entry["msg_id"])
    except Exception:
        pass
    # Gửi thông báo phù hợp
    if reason == "paid":
        # Khi CK thành công: chỉ xóa QR, thông báo đầy đủ do webhook gửi riêng
        pass
    else:  # expired
        try:
            sent_msg = await bot.send_message(
                uid,
                "⏰ <b>Mã QR đã hết hạn!</b>\n\n"
                "🗑 Mã QR đã được tự động xóa.\n"
                "Vui lòng tạo mã mới để tiếp tục nạp tiền.",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="💰 Tạo QR mới",    callback_data="deposit")],
                    [InlineKeyboardButton(text="🏠 Về Menu Chính",  callback_data="home")],
                ])
            )
            track_message(uid, sent_msg.chat.id, sent_msg.message_id)
        except Exception:
            pass

# ─────────────────────────────────────────────
#  HELPER: Gọi API dự đoán
# ─────────────────────────────────────────────
def fetch_prediction(game_id: str) -> dict:
    if game_id == "sunwin_tx":
        try:
            resp = requests.get("https://sunwinsaygex-vd0m.onrender.com/api/sun", timeout=5)
            if resp.status_code == 200:
                data = resp.json()
                return {
                    "game_id"        : game_id,
                    "current_session": data.get("phien", 0),
                    "dice"           : [data.get("xuc_xac_1", 0), data.get("xuc_xac_2", 0), data.get("xuc_xac_3", 0)],
                    "total"          : data.get("tong", 0),
                    "result"         : str(data.get("ket_qua", "")).upper(),
                    "next_session"   : data.get("phien_hien_tai", 0),
                    "prediction"     : str(data.get("du_doan", "")).upper(),
                    "confidence"     : random.randint(75, 95),
                    "bridge_type"    : "Cầu Hệ Thống",
                    "pattern"        : "N/A",
                }
        except Exception as e:
            log.warning(f"API error for {game_id}: {e}")

    elif game_id == "sunwin_sicbo":
        try:
            resp = requests.get("https://sicbosunwin.onrender.com/api/sicbo/sunwin", timeout=5)
            if resp.status_code == 200:
                data = resp.json()
                conf_str = data.get("do_tin_cay", "80%")
                conf = int(float(conf_str.replace("%", ""))) if "%" in conf_str else 80
                return {
                    "game_id"        : game_id,
                    "current_session": str(data.get("Phien", "")).replace("#", ""),
                    "dice"           : [data.get("Xuc_xac_1", 0), data.get("Xuc_xac_2", 0), data.get("Xuc_xac_3", 0)],
                    "total"          : data.get("Tong", 0),
                    "result"         : str(data.get("Ket_qua", "")).upper(),
                    "next_session"   : data.get("phien_hien_tai", 0),
                    "prediction"     : str(data.get("du_doan", "")).upper(),
                    "confidence"     : conf,
                    "bridge_type"    : "Cầu Thông Minh",
                    "pattern"        : "N/A",
                    "position"       : str(data.get("dudoan_vi", "")),
                }
        except Exception as e:
            log.warning(f"API error for {game_id}: {e}")

    elif game_id == "lc79_hu":
        try:
            resp = requests.get("http://160.250.137.196:5000/lc79-hu", timeout=8)
            if resp.status_code == 200:
                data = resp.json()
                conf_str = str(data.get("ti_le", "70%"))
                conf = int(float(conf_str.replace("%", "").strip())) if conf_str.replace("%","").strip().replace(".","").isdigit() else 70
                pred = str(data.get("du_doan", "")).strip().upper()
                pred = "TÀI" if pred in ["TAI","TÀI","T"] else ("XỈU" if pred in ["XIU","XỈU","X"] else pred)
                return {
                    "game_id"        : game_id,
                    "current_session": str(data.get("phien_hien_tai", "N/A")),
                    "dice"           : [0, 0, 0],
                    "total"          : 0,
                    "result"         : "N/A",
                    "next_session"   : str(int(str(data.get("phien_hien_tai","0")).replace("#","") or 0) + 1),
                    "prediction"     : pred,
                    "confidence"     : conf,
                    "bridge_type"    : "Cầu lc79 Hũ",
                    "pattern"        : "N/A",
                }
        except Exception as e:
            log.warning(f"API error for {game_id}: {e}")

    elif game_id == "lc79_md5":
        try:
            resp = requests.get("http://160.250.137.196:5000/lc79-md5", timeout=8)
            if resp.status_code == 200:
                data = resp.json()
                conf_str = str(data.get("ti_le", "70%"))
                conf = int(float(conf_str.replace("%", "").strip())) if conf_str.replace("%","").strip().replace(".","").isdigit() else 70
                pred = str(data.get("du_doan", "")).strip().upper()
                pred = "TÀI" if pred in ["TAI","TÀI","T"] else ("XỈU" if pred in ["XIU","XỈU","X"] else pred)
                return {
                    "game_id"        : game_id,
                    "current_session": str(data.get("phien_hien_tai", "N/A")),
                    "dice"           : [0, 0, 0],
                    "total"          : 0,
                    "result"         : "N/A",
                    "next_session"   : str(int(str(data.get("phien_hien_tai","0")).replace("#","") or 0) + 1),
                    "prediction"     : pred,
                    "confidence"     : conf,
                    "bridge_type"    : "Cầu lc79 MD5",
                    "pattern"        : "N/A",
                }
        except Exception as e:
            log.warning(f"API error for {game_id}: {e}")

    elif game_id == "luck8":
        try:
            resp = requests.get("https://luck8md5vip-4vph.onrender.com/api/taixiu", timeout=8)
            if resp.status_code == 200:
                data = resp.json()
                conf_str = str(data.get("doTinCay", "70%"))
                try:
                    conf = int(float(conf_str.replace("%", "").strip()))
                except Exception:
                    conf = 70
                pred_raw = str(data.get("duDoan", "")).strip()
                pred_up  = pred_raw.upper()
                pred = "TÀI" if pred_up in ["TAI","TÀI","T"] else ("XỈU" if pred_up in ["XIU","XỈU","X"] else pred_raw)
                ket_raw = str(data.get("ketQua", "")).strip().upper()
                result  = "TÀI" if "TÀI" in ket_raw or "TAI" in ket_raw else ("XỈU" if "XỈU" in ket_raw or "XIU" in ket_raw else ket_raw if ket_raw else "N/A")
                xuc     = data.get("xucXac", [0, 0, 0])
                if isinstance(xuc, list) and len(xuc) >= 3:
                    xuc = [int(xuc[0]), int(xuc[1]), int(xuc[2])]
                else:
                    xuc = [0, 0, 0]
                pattern_raw  = str(data.get("pattern", ""))
                pattern_disp = pattern_raw[-20:] if len(pattern_raw) > 20 else (pattern_raw if pattern_raw else "N/A")
                return {
                    "game_id"        : game_id,
                    "current_session": str(data.get("phien", "N/A")),
                    "dice"           : xuc,
                    "total"          : sum(xuc),
                    "result"         : result,
                    "next_session"   : str(data.get("phienHienTai", "N/A")),
                    "prediction"     : pred,
                    "confidence"     : conf,
                    "bridge_type"    : "Cầu Luck8",
                    "pattern"        : pattern_disp,
                }
        except Exception as e:
            log.warning(f"API error for {game_id}: {e}")

    elif game_id == "b52_tx":
        try:
            resp = requests.get("https://b52-taixiu-l69b.onrender.com/api/taixiu", timeout=8)
            if resp.status_code == 200:
                data = resp.json()
                conf_str = str(data.get("Do_tin_cay", "70%"))
                try:
                    conf = int(float(conf_str.replace("%","").strip()))
                except Exception:
                    conf = 70
                pred = str(data.get("Du_doan", "")).strip().upper()
                pred = "TÀI" if pred in ["TAI","TÀI","T","TÀI"] else ("XỈU" if pred in ["XIU","XỈU","X"] else pred)
                result = str(data.get("Ket_qua","")).strip().upper()
                result = "TÀI" if "TÀI" in result or "TAI" in result else ("XỈU" if "XỈU" in result or "XIU" in result else result)
                pattern_raw = str(data.get("Pattern",""))
                pattern_disp = pattern_raw[-20:] if len(pattern_raw) > 20 else pattern_raw
                return {
                    "game_id"        : game_id,
                    "current_session": str(data.get("Phien", "N/A")),
                    "dice"           : [data.get("Xuc_xac_1",0), data.get("Xuc_xac_2",0), data.get("Xuc_xac_3",0)],
                    "total"          : data.get("Tong", 0),
                    "result"         : result,
                    "next_session"   : str(data.get("phien_hien_tai", "N/A")),
                    "prediction"     : pred,
                    "confidence"     : conf,
                    "bridge_type"    : "Cầu B52",
                    "pattern"        : pattern_disp if pattern_disp else "N/A",
                }
        except Exception as e:
            log.warning(f"API error for {game_id}: {e}")

    elif game_id in ("68gb_xanh", "68gb_md5"):
        # Key map: 68gb_xanh -> bàn xanh, 68gb_md5 -> bàn đỏ/md5
        # API mới trả về "key":"banxanh" và "key":"md5" trong mảng data[]
        key_map = {
            "68gb_xanh": ["banxanh","ban_xanh","ban-xanh","xanh","68gb_xanh","BanXanh","ban xanh"],
            "68gb_md5" : ["md5","ban_do","ban-do","do","68gb_do","BanDo","ban do","ban_md5"],
        }
        bridge_map = {"68gb_xanh": "Cầu 68gb Bàn Xanh", "68gb_md5": "Cầu 68gb Bàn Đỏ"}
        try:
            raw = fetch_68gb_all()
            if raw.get("ok"):
                for key_try in key_map[game_id]:
                    parsed = _parse_68gb_item(raw["data"], key_try, game_id, bridge_map[game_id])
                    if parsed.get("ok"):
                        return parsed
        except Exception as e:
            log.warning(f"API error for {game_id}: {e}")

    dice = [random.randint(1, 6) for _ in range(3)]
    total = sum(dice)
    result = "TÀI" if total >= 11 else "XỈU"
    patterns = ["Cầu bệt", "Cầu đảo", "Cầu 1-1", "Cầu 2-2", "Cầu 3-3", "Cầu zigzag"]
    pattern_seq = "T-T-X-T" if result == "TÀI" else "X-X-T-X"
    current_session = random.randint(100000, 999999)
    return {
        "game_id"        : game_id,
        "current_session": current_session,
        "dice"           : dice,
        "total"          : total,
        "result"         : result,
        "next_session"   : current_session + 1,
        "prediction"     : "TÀI" if random.random() > 0.45 else "XỈU",
        "confidence"     : random.randint(72, 95),
        "bridge_type"    : random.choice(patterns),
        "pattern"        : pattern_seq,
        "position"       : random.choice(["Cửa Tài", "Cửa Xỉu", "Cửa Chẵn", "Cửa Lẻ"]),
        "baccarat_result": random.choice(["Player", "Banker", "Tie"]),
    }

def fetch_68gb_all() -> dict:
    """Gọi API 68gb tổng hợp — trả về bàn xanh, bàn đỏ, bàn md5."""
    try:
        resp = requests.get("https://six8gbsew.onrender.com/all", timeout=10)
        if resp.status_code == 200:
            return {"ok": True, "data": resp.json()}
    except Exception as e:
        log.warning(f"68gb all API error: {e}")
    return {"ok": False}

def _parse_68gb_item(data: dict, key: str, game_id: str, bridge_name: str) -> dict:
    """Parse item từ response 68gb /all theo key bàn.

    Hỗ trợ cả 2 format:
      - Mới: {"status":..., "data":[{"key":"banxanh", ...}, ...]}
      - Cũ : {"ban_xanh": {...}, ...}
    """
    key_norm = key.lower().replace("-","").replace("_","").replace(" ","")
    item = {}

    # ── Format mới: data["data"] là list, mỗi phần tử có field "key" ──
    if isinstance(data, dict):
        arr = data.get("data", [])
        if isinstance(arr, list):
            for entry in arr:
                if not isinstance(entry, dict):
                    continue
                entry_key = str(entry.get("key","")).lower().replace("-","").replace("_","").replace(" ","")
                if entry_key == key_norm or key_norm in entry_key or entry_key in key_norm:
                    item = entry
                    break

    # ── Fallback format cũ: dict có key trực tiếp ──
    if not item and isinstance(data, dict):
        item = data.get(key, {})
    if not item and isinstance(data, dict):
        for k, v in data.items():
            if key_norm in k.lower().replace("-","").replace("_","").replace(" ",""):
                item = v
                break

    if not item:
        return {"ok": False}

    # ── Độ tin cậy ──
    raw_conf = item.get("do_tin_cay", item.get("ti_le", item.get("Do_tin_cay", None)))
    if raw_conf is None:
        conf = 70
    else:
        try:
            conf = int(float(str(raw_conf).replace("%","").strip()))
        except Exception:
            conf = 70

    # ── Dự đoán ──
    pred_raw = str(item.get("du_doan", item.get("Du_doan",""))).strip()
    pred_up  = pred_raw.upper()
    if pred_up in ["TAI","TÀI","T","TÀI","TAIXIU_TAI"]:
        pred = "TÀI"
    elif pred_up in ["XIU","XỈU","X","TAIXIU_XIU"]:
        pred = "XỈU"
    else:
        pred = pred_raw if pred_raw else "N/A"

    # ── Kết quả ──
    ket_qua_raw = str(item.get("ket_qua", item.get("Ket_qua",""))).strip()
    kq_up = ket_qua_raw.upper()
    if "TÀI" in kq_up or "TAI" in kq_up:
        result = "TÀI"
    elif "XỈU" in kq_up or "XIU" in kq_up:
        result = "XỈU"
    else:
        result = ket_qua_raw if ket_qua_raw and ket_qua_raw.lower() != "none" else "N/A"

    # ── Phiên ──
    phien = str(item.get("phien", item.get("Phien", item.get("phien_hien_tai","N/A")))).replace("#","")
    next_phien = str(item.get("phien_hien_tai", item.get("phien_tiep_theo",
        str(int(phien) + 1) if phien.isdigit() else "N/A")))

    # ── Xúc xắc — hỗ trợ cả dạng list mới [d1,d2,d3] và dạng cũ xuc_xac_1/2/3 ──
    xuc_list = item.get("xuc_xac", [])
    if isinstance(xuc_list, list) and len(xuc_list) >= 3:
        xuc = [int(xuc_list[0]), int(xuc_list[1]), int(xuc_list[2])]
    else:
        xuc = [item.get("xuc_xac_1", item.get("Xuc_xac_1", 0)),
               item.get("xuc_xac_2", item.get("Xuc_xac_2", 0)),
               item.get("xuc_xac_3", item.get("Xuc_xac_3", 0))]
    tong = item.get("tong", item.get("Tong", sum(xuc)))

    # ── Vị trí (vi) — hỗ trợ list mới [v1,v2,...] ──
    vi_raw = item.get("vi", item.get("dudoan_vi", ""))
    if isinstance(vi_raw, list):
        position = ",".join(str(v) for v in vi_raw)
    else:
        position = str(vi_raw)

    pattern_raw = str(item.get("Pattern", item.get("pattern","")))
    pattern_disp = pattern_raw[-20:] if len(pattern_raw) > 20 else (pattern_raw if pattern_raw else "N/A")

    return {
        "ok"             : True,
        "game_id"        : game_id,
        "current_session": phien,
        "dice"           : xuc,
        "total"          : tong,
        "result"         : result,
        "next_session"   : next_phien.replace("#",""),
        "prediction"     : pred,
        "confidence"     : conf,
        "bridge_type"    : bridge_name,
        "pattern"        : pattern_disp,
        "position"       : position,
    }

def fetch_bcr_sunwin() -> dict:
    """Gọi API BCR Sunwin từ endpoint /all của six8gbsew."""
    try:
        resp = requests.get("https://six8gbsew.onrender.com/all", timeout=10)
        if resp.status_code == 200:
            raw = resp.json()
            bcr_item = None

            # ── Format mới: raw["data"] là list, tìm item có key "baccarat" ──
            if isinstance(raw, dict):
                arr = raw.get("data", [])
                if isinstance(arr, list):
                    for entry in arr:
                        if not isinstance(entry, dict):
                            continue
                        k = str(entry.get("key","")).lower()
                        if k in ("baccarat","bcr","bcr_sunwin","baccarat_sunwin"):
                            bcr_item = entry
                            break

            # ── Fallback format cũ: dict key trực tiếp ──
            if not bcr_item and isinstance(raw, dict):
                for k in ["bcr","baccarat","bcr_sunwin","BCR","Baccarat"]:
                    if k in raw:
                        bcr_item = raw[k]
                        break

            if not bcr_item:
                return {"ok": False}

            # Parse từ item tìm được
            raw_conf = bcr_item.get("do_tin_cay", bcr_item.get("Độ tin cậy", None))
            try:
                conf = int(float(str(raw_conf).replace("%","").strip())) if raw_conf is not None else 75
            except Exception:
                conf = 75

            du_doan = bcr_item.get("du_doan", bcr_item.get("Dự đoán", "N/A"))
            phien   = bcr_item.get("phien", bcr_item.get("Số phiên", 0))
            return {
                "ok"        : True,
                "ban"       : str(bcr_item.get("ban", bcr_item.get("Bàn", "1"))),
                "so_phien"  : phien,
                "du_doan"   : du_doan,
                "loai_cau"  : bcr_item.get("loai_cau", bcr_item.get("Loại cầu", [])),
                "confidence": conf,
                "do_manh"   : bcr_item.get("do_manh", bcr_item.get("Độ mạnh", "N/A")),
                "trang_thai": bcr_item.get("trang_thai", bcr_item.get("Trạng thái", "N/A")),
                "dev"       : bcr_item.get("dev", bcr_item.get("Dev", "")),
            }
    except Exception as e:
        log.warning(f"BCR Sunwin API error: {e}")
    return {"ok": False}

def _parse_bcr_item(data: dict, table_num: int = 0) -> dict:
    """Parse một object bàn từ API BCR."""
    ds = data.get("Danh sách", [])
    item = ds[0] if ds else {}
    conf_str = item.get("Độ tin cậy", "75%")
    try:
        conf = int(float(conf_str.replace("%", "").strip()))
    except Exception:
        conf = 75
    return {
        "ok"        : True,
        "ban"       : data.get("Bàn", str(table_num)),
        "so_phien"  : data.get("Số phiên", 0),
        "du_doan"   : item.get("Dự đoán", "N/A"),
        "loai_cau"  : item.get("Loại cầu", []),
        "confidence": conf,
        "do_manh"   : item.get("Độ mạnh", "N/A"),
        "trang_thai": item.get("Trạng thái", "N/A"),
        "dev"       : item.get("Dev", ""),
    }

def fetch_bcr_sexy(table_num: int) -> dict:
    """Gọi API Baccarat Sexy theo số bàn cụ thể /apibcr/{n}."""
    try:
        resp = requests.get(
            f"https://bcrsexysewpro.onrender.com/apibcr/{table_num}",
            timeout=10
        )
        if resp.status_code == 200:
            return _parse_bcr_item(resp.json(), table_num)
    except Exception as e:
        log.warning(f"BCR Sexy API error table {table_num}: {e}")
    return {"ok": False}

def fetch_bcr_all() -> list:
    """Gọi API tổng hợp /apibcr — trả về danh sách tất cả bàn."""
    try:
        resp = requests.get(
            "https://bcrsexysewpro.onrender.com/apibcr",
            timeout=15
        )
        if resp.status_code == 200:
            raw = resp.json()
            # API trả về list các bàn
            if isinstance(raw, list):
                return [_parse_bcr_item(item, i + 1) for i, item in enumerate(raw)]
            # API trả về dict có key "Danh sách" chứa list bàn
            if isinstance(raw, dict):
                tables = raw.get("Danh sách", raw.get("data", [raw]))
                if isinstance(tables, list):
                    return [_parse_bcr_item(t, i + 1) for i, t in enumerate(tables)]
    except Exception as e:
        log.warning(f"BCR all tables API error: {e}")
    return []

def fetch_hit_sicbo() -> dict:
    """Gọi API Sicbo Hitclub."""
    try:
        resp = requests.get("https://sichit.onrender.com/sicbo", timeout=8)
        if resp.status_code == 200:
            data = resp.json()
            conf_str = data.get("do_tin_cay", "75%")
            try:
                conf = int(float(conf_str.replace("%", "").strip()))
            except Exception:
                conf = 75
            return {
                "ok"          : True,
                "game_id"     : "hit_sicbo",
                "current_session": str(data.get("phien", "")).replace("#", ""),
                "dice"        : [data.get("xuc_xac_1", 0), data.get("xuc_xac_2", 0), data.get("xuc_xac_3", 0)],
                "total"       : data.get("tong", 0),
                "result"      : str(data.get("ket_qua", "")),
                "next_session": str(data.get("phien_hien_tai", "")).replace("#", ""),
                "prediction"  : str(data.get("du_doan", "")),
                "position"    : str(data.get("dudoan_vi", "")),
                "confidence"  : conf,
                "bridge_type" : str(data.get("ly_do", "Cầu Hệ Thống")),
                "pattern"     : "N/A",
            }
    except Exception as e:
        log.warning(f"Hit Sicbo API error: {e}")
    return {"ok": False}

# ─────────────────────────────────────────────
#  KEYBOARD BUILDERS
# ─────────────────────────────────────────────
def kb_start(authorized: bool) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text="🔑 Nhập Key", callback_data="enter_key")],
        [InlineKeyboardButton(text="💰 Nạp Tiền", callback_data="deposit")],
        [InlineKeyboardButton(text="ℹ️ Trợ Giúp (Help)", callback_data="help")],
    ]
    if authorized:
        rows.append([InlineKeyboardButton(text="🎮 Danh sách Game", callback_data="game_list")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def kb_help() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💰 Nạp Tiền", callback_data="deposit"),
         InlineKeyboardButton(text="👤 Tài Khoản", callback_data="account")],
        [InlineKeyboardButton(text="🛒 Mua VIP", callback_data="buy_vip_menu"),
         InlineKeyboardButton(text="👨‍💻 Liên Hệ Admin", url=ADMIN_LINK)],
        [InlineKeyboardButton(text="📜 Lịch sử giao dịch", callback_data="history")],
        [InlineKeyboardButton(text="🏠 Về Menu Chính", callback_data="home")],
    ])

def kb_games() -> InlineKeyboardMarkup:
    rows = []
    for i in range(0, len(GAMES), 2):
        row = [InlineKeyboardButton(text=GAMES[i]["name"], callback_data=f"game_{GAMES[i]['id']}")]
        if i + 1 < len(GAMES):
            row.append(InlineKeyboardButton(text=GAMES[i+1]["name"], callback_data=f"game_{GAMES[i+1]['id']}"))
        rows.append(row)
    rows.append([InlineKeyboardButton(text="🏠 Về Menu Chính", callback_data="home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def kb_game_result(game_id: str, uid: int = 0) -> InlineKeyboardMarkup:
    subscribed = game_id in auto_subs.get(uid, set())
    sub_btn = (
        InlineKeyboardButton(text="🔕 Dừng tự động", callback_data=f"unsub_{game_id}")
        if subscribed else
        InlineKeyboardButton(text="🔔 Nhận tự động", callback_data=f"sub_{game_id}")
    )
    return InlineKeyboardMarkup(inline_keyboard=[
        [sub_btn],
        [InlineKeyboardButton(text="◀️ Quay lại", callback_data="game_list"),
         InlineKeyboardButton(text="🏠 Menu Chính", callback_data="home")],
    ])

def kb_admin_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Thống Kê", callback_data="admin_stats"),
         InlineKeyboardButton(text="📋 Danh Sách Key", callback_data="admin_listkeys")],
        [InlineKeyboardButton(text="➕ Tạo 1 Key", callback_data="admin_addkey"),
         InlineKeyboardButton(text="🎲 Tạo Nhiều Key", callback_data="admin_genkeys")],
        [InlineKeyboardButton(text="❌ Xóa 1 Key", callback_data="admin_delkey"),
         InlineKeyboardButton(text="🗑 Xóa Toàn Bộ", callback_data="admin_clear_keys")],
        [InlineKeyboardButton(text="📢 Gửi Thông Báo", callback_data="admin_broadcast")],
        [InlineKeyboardButton(text="Đóng Menu", callback_data="admin_close")]
    ])

def kb_cancel_admin() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Hủy", callback_data="admin_cancel")]
    ])

def kb_admin_confirm_clear() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⚠️ CÓ, XÓA TẤT CẢ!", callback_data="admin_confirm_clear_yes")],
        [InlineKeyboardButton(text="Hủy", callback_data="admin_confirm_clear_no")]
    ])

def kb_bcr_tables(current: int = 0) -> InlineKeyboardMarkup:
    """Keyboard chọn bàn Baccarat Sexy (10 bàn, 5 cột x 2 hàng)."""
    rows = []
    row = []
    for i in range(1, 11):
        label = f"{'✅ ' if i == current else ''}Bàn {i}"
        row.append(InlineKeyboardButton(text=label, callback_data=f"bcr_table_{i}"))
        if len(row) == 5:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([
        InlineKeyboardButton(text="📋 Tất cả bàn", callback_data="bcr_all"),
    ])
    rows.append([
        InlineKeyboardButton(text="◀️ Quay lại", callback_data="game_list"),
        InlineKeyboardButton(text="🏠 Menu Chính", callback_data="home"),
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def kb_bcr_table_result(table_num: int, uid: int = 0) -> InlineKeyboardMarkup:
    """Keyboard kết quả 1 bàn BCR — có nút subscribe."""
    key = f"bcr_table_{table_num}"
    subscribed = key in auto_subs.get(uid, set())
    sub_btn = (
        InlineKeyboardButton(text="🔕 Dừng tự động", callback_data=f"unsub_{key}")
        if subscribed else
        InlineKeyboardButton(text="🔔 Nhận tự động", callback_data=f"sub_{key}")
    )
    return InlineKeyboardMarkup(inline_keyboard=[
        [sub_btn],
        [InlineKeyboardButton(text="🎰 Chọn bàn khác", callback_data="game_baccarat_sexy"),
         InlineKeyboardButton(text="🏠 Menu Chính", callback_data="home")],
    ])

def kb_bcr_all() -> InlineKeyboardMarkup:
    """Keyboard cho màn hình tổng hợp tất cả bàn."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎰 Chọn bàn cụ thể", callback_data="game_baccarat_sexy")],
        [InlineKeyboardButton(text="◀️ Quay lại", callback_data="game_list"),
         InlineKeyboardButton(text="🏠 Menu Chính", callback_data="home")],
    ])

# ─────────────────────────────────────────────
#  FORMAT TEXT KẾT QUẢ
# ─────────────────────────────────────────────
def format_taixiu(game: dict, data: dict) -> str:
    dice = data["dice"]
    pred = data["prediction"]
    pred_emoji = "📈 TÀI" if pred == "TÀI" else ("📉 XỈU" if pred == "XỈU" else f"🎯 {pred}")
    conf = data["confidence"]
    conf_bar = "█" * (conf // 10) + "░" * (10 - conf // 10)
    has_dice = any(d > 0 for d in dice)
    has_result = data["result"] not in ("N/A", "", "0")
    lines = [
        f"🎲 <b>GAME: {game['name']}</b>",
        f"{'─'*28}",
        f"🔹 Phiên hiện tại: <code>{data['current_session']}</code>",
    ]
    if has_result:
        lines.append(f"🔹 Kết quả: <b>{data['result']}</b> — Tổng: <b>{data['total']}</b>")
    if has_dice:
        lines.append(f"🎲 Xúc xắc: <b>{dice[0]}</b> ─ <b>{dice[1]}</b> ─ <b>{dice[2]}</b>")
    lines += [
        f"{'─'*28}",
        f"🔮 Phiên dự đoán: <code>{data['next_session']}</code>",
        f"🎯 Dự đoán: <b>{pred_emoji}</b>",
        f"📊 Độ tin cậy: <b>{conf}%</b>",
        f"   <code>[{conf_bar}]</code>",
        f"🔄 Loại cầu: <i>{data['bridge_type']}</i>",
        f"📉 Pattern: <code>{data['pattern']}</code>",
        f"{'─'*28}",
        f"⏱ Cập nhật: {now_vn().strftime('%H:%M:%S')} (VN)",
    ]
    return "\n".join(lines)

def format_sicbo(game: dict, data: dict) -> str:
    dice = data["dice"]
    total = data["total"]
    conf = data["confidence"]
    conf_bar = "█" * (conf // 10) + "░" * (10 - conf // 10)
    return (
        f"🎱 <b>GAME: {game['name']}</b>\n"
        f"{'─'*28}\n"
        f"🔹 Phiên hiện tại: <code>{data['current_session']}</code>\n"
        f"🔹 Kết quả: <b>{data['result']}</b> — Tổng: <b>{total}</b>\n"
        f"🎲 Xúc xắc: <b>{dice[0]}</b> ─ <b>{dice[1]}</b> ─ <b>{dice[2]}</b>\n"
        f"{'─'*28}\n"
        f"🔮 Phiên dự đoán: <code>{data['next_session']}</code>\n"
        f"🎯 Dự đoán: <b>{data['prediction']}</b>\n"
        f"📍 Vị: <b>{data['position']}</b>\n"
        f"📊 Độ tin cậy: <b>{conf}%</b>\n"
        f"   <code>[{conf_bar}]</code>\n"
        f"{'─'*28}\n"
        f"⏱ Cập nhật: {now_vn().strftime('%H:%M:%S')} (VN)"
    )

def format_baccarat(game: dict, data: dict) -> str:
    conf = data["confidence"]
    conf_bar = "█" * (conf // 10) + "░" * (10 - conf // 10)
    return (
        f"🃏 <b>GAME: {game['name']}</b>\n"
        f"{'─'*28}\n"
        f"🔹 Phiên hiện tại: <code>{data['current_session']}</code>\n"
        f"🔹 Kết quả: <b>{data['baccarat_result']}</b>\n"
        f"{'─'*28}\n"
        f"🔮 Phiên dự đoán: <code>{data['next_session']}</code>\n"
        f"🎯 Dự đoán: <b>{data['baccarat_result']}</b>\n"
        f"📊 Độ tin cậy: <b>{conf}%</b>\n"
        f"   <code>[{conf_bar}]</code>\n"
        f"🔄 Loại cầu: <i>{data['bridge_type']}</i>\n"
        f"📉 Pattern: <code>{data['pattern']}</code>\n"
        f"{'─'*28}\n"
        f"⏱ Cập nhật: {now_vn().strftime('%H:%M:%S')} (VN)"
    )

def pred_icon(du_doan: str) -> str:
    du = du_doan.lower()
    if any(x in du for x in ["banker", "cái", "cai"]):
        return "🔵"
    if any(x in du for x in ["player", "nhà", "nha"]):
        return "🔴"
    if "tie" in du or "hòa" in du or "hoa" in du:
        return "🟡"
    return "🎯"

def format_bcr_all(tables: list) -> str:
    """Hiển thị tổng hợp tất cả bàn BCR Sexy dạng compact."""
    if not tables:
        return "⚠️ Không lấy được dữ liệu từ API. Vui lòng thử lại!"
    lines = [
        "👠 <b>BACCARAT SEXY — TẤT CẢ BÀN</b>",
        f"{'─'*30}",
    ]
    for d in tables:
        if not d.get("ok"):
            continue
        ban = d.get("ban", "?")
        phien = d.get("so_phien", "?")
        du_doan = d.get("du_doan", "N/A")
        conf = d.get("confidence", 0)
        do_manh = d.get("do_manh", "")
        trang_thai = d.get("trang_thai", "")
        icon = pred_icon(du_doan)
        lines.append(
            f"{icon} <b>Bàn {ban}</b> — Phiên <code>{phien}</code>\n"
            f"   🎯 <b>{du_doan}</b> | {conf}% | {do_manh}\n"
            f"   {trang_thai}"
        )
        lines.append("─" * 30)
    lines.append(f"⏱ Cập nhật: {now_vn().strftime('%H:%M:%S')} (VN)")
    return "\n".join(lines)

def format_bcr_sexy(table_num: int, d: dict) -> str:
    conf = d.get("confidence", 75)
    conf_bar = "█" * (conf // 10) + "░" * (10 - conf // 10)
    cau_list = d.get("loai_cau", [])
    cau_text = "\n".join([f"   • {c}" for c in cau_list]) if cau_list else "   • N/A"
    du_doan = d.get("du_doan", "N/A")
    icon = pred_icon(du_doan)
    return (
        f"👠 <b>BACCARAT SEXY — BÀN {table_num}</b>\n"
        f"{'─'*28}\n"
        f"🔹 Số phiên: <code>{d.get('so_phien', 'N/A')}</code>\n"
        f"{'─'*28}\n"
        f"🎯 Dự đoán: {icon} <b>{du_doan}</b>\n"
        f"📊 Độ tin cậy: <b>{conf}%</b>\n"
        f"   <code>[{conf_bar}]</code>\n"
        f"💪 Độ mạnh: <b>{d.get('do_manh', 'N/A')}</b>\n"
        f"🏆 Trạng thái: <b>{d.get('trang_thai', 'N/A')}</b>\n"
        f"{'─'*28}\n"
        f"🔄 Loại cầu phát hiện:\n{cau_text}\n"
        f"{'─'*28}\n"
        f"⏱ Cập nhật: {now_vn().strftime('%H:%M:%S')} (VN)"
    )

def format_result(game: dict, data: dict) -> str:
    t = game["type"]
    if t == "sicbo":
        return format_sicbo(game, data)
    if t == "baccarat":
        return format_baccarat(game, data)
    if t == "baccarat_sunwin":
        return format_bcr_sexy(1, data)   # dùng lại format BCR
    return format_taixiu(game, data)

# ─────────────────────────────────────────────
#  WELCOME TEXT
# ─────────────────────────────────────────────
def welcome_text(user) -> str:
    user_id = user.id
    name = user.full_name
    username = f" (@{user.username})" if user.username else ""
    status = "✅ <b>Đã kích hoạt</b>" if is_authorized(user_id) else "❌ <b>Chưa kích hoạt</b>"
    expire = key_expire_str(user_id) if is_authorized(user_id) else "—"
    balance = user_balances.get(user_id, 0)
    return (
        "╔══════════════════════════╗\n"
        "║  🤖  <b>TOOL AI SEW PRO</b>  🤖  ║\n"
        "╚══════════════════════════╝\n\n"
        "👤 <b>THÔNG TIN CÁ NHÂN</b>\n"
        f"┣ Tên: <b>{name}</b>{username}\n"
        f"┣ ID: <code>{user_id}</code>\n"
        f"┣ Số dư: <b>{balance:,.0f}đ</b>\n"
        f"┣ Trạng thái: {status}\n"
        f"┗ Hạn dùng: <i>{expire}</i>\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        " <b>GIỚI THIỆU CHỨC NĂNG TOOL</b>\n"
        "🤖 Hệ thống phân tích AI thông minh hỗ trợ:\n"
        "✔️ <b>Tài Xỉu:</b> Bắt cầu MD5, Hũ, Sunwin, 88...\n"
        "✔️ <b>Sicbo:</b> Soi vị, soi tổng chuẩn xác.\n"
        "✔️ <b>Baccarat:</b> Phân tích cầu Player/Banker.\n"
        "⚡️ Cập nhật kết quả & dự đoán thời gian thực!\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "💎 <b>BẢNG GIÁ KEY VIP</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "📅  1 Ngày      ──────  <b>30.000đ</b>\n"
        "📆  1 Tuần      ──────  <b>120.000đ</b>\n"
        "🗓️  1 Tháng     ──────  <b>220.000đ</b>\n"
        "♾️  Vĩnh viễn  ──────  <b>380.000đ</b>\n"
    )

# ─────────────────────────────────────────────
#  KHỞI TẠO BOT
# ─────────────────────────────────────────────
bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
dp  = Dispatcher(storage=MemoryStorage())
app = FastAPI(redirect_slashes=False)

# ─────────────────────────────────────────────
#  HANDLERS
# ─────────────────────────────────────────────
@dp.message(CommandStart())
async def cmd_start(msg: Message, state: FSMContext):
    await state.clear()
    uid = msg.from_user.id
    if uid not in all_users:
        all_users.add(uid)
        save_data()
    await msg.answer(
        welcome_text(msg.from_user),
        reply_markup=kb_start(is_authorized(uid))
    )

@dp.callback_query(F.data == "home")
async def cb_home(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    uid = cb.from_user.id
    text = welcome_text(cb.from_user)
    markup = kb_start(is_authorized(uid))
    if cb.message.photo:
        await cb.message.delete()
        await cb.message.answer(text, reply_markup=markup)
    else:
        await cb.message.edit_text(text, reply_markup=markup)
    await cb.answer()

@dp.message(Command("help"))
async def cmd_help(msg: Message, state: FSMContext):
    await state.clear()
    await msg.answer(
        "ℹ️ <b>TRỢ GIÚP & CHỨC NĂNG KHÁC</b>\n\nVui lòng chọn các chức năng bên dưới:",
        reply_markup=kb_help()
    )

@dp.callback_query(F.data == "help")
async def cb_help(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await cb.message.edit_text(
        "ℹ️ <b>TRỢ GIÚP & CHỨC NĂNG KHÁC</b>\n\nVui lòng chọn các chức năng bên dưới:",
        reply_markup=kb_help()
    )
    await cb.answer()

# ─────────────────────────────────────────────
#  NẠP TIỀN — ✅ ĐÃ FIX
#  Dùng "NAP {uid}" làm nội dung CK
#  → Không cần pending_payments → không mất data khi Render restart
# ─────────────────────────────────────────────
@dp.callback_query(F.data == "deposit")
async def cb_deposit(cb: CallbackQuery):
    uid = cb.from_user.id
    text = (
        f"💰 <b>NẠP TIỀN VÀO SỐ DƯ</b>\n\n"
        f"📝 Nội dung chuyển khoản của bạn: <code>NAP {uid}</code>\n\n"
        "Vui lòng chọn số tiền bạn muốn nạp:"
    )
    await cb.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="50.000đ",  callback_data="pay_50000"),
         InlineKeyboardButton(text="100.000đ", callback_data="pay_100000")],
        [InlineKeyboardButton(text="200.000đ", callback_data="pay_200000"),
         InlineKeyboardButton(text="500.000đ", callback_data="pay_500000")],
        [InlineKeyboardButton(text="◀️ Quay lại Trợ Giúp", callback_data="help")]
    ]))
    await cb.answer()

@dp.callback_query(F.data.startswith("pay_"))
async def cb_pay(cb: CallbackQuery):
    amount = int(cb.data.split("_")[1])
    uid = cb.from_user.id

    # Nếu user đang có QR cũ chưa dùng → xóa đi trước
    if uid in pending_qr:
        await delete_qr_for_user(uid, reason="expired")

    # ✅ Thêm 4 ký tự random để tránh trùng nội dung CK
    rand_suffix = ''.join(random.choices(string.ascii_uppercase + string.digits, k=4))
    memo = f"NAP {uid} {rand_suffix}"

    expire_time = now_vn() + timedelta(minutes=QR_EXPIRE_MINUTES)
    expire_str  = expire_time.strftime('%H:%M:%S')

    qr_url = (
        f"https://img.vietqr.io/image/MB-{BANK_STK}-compact2.png"
        f"?amount={amount}&addInfo={memo}"
    )

    await cb.message.delete()
    sent = await cb.message.answer_photo(
        photo=qr_url,
        caption=(
            f"💰 <b>HÓA ĐƠN THANH TOÁN TỰ ĐỘNG</b>\n\n"
            f"🏦 Ngân hàng: <b>MB Bank</b>\n"
            f"💳 Số tài khoản: <code>{BANK_STK}</code>\n"
            f"💵 Số tiền: <b>{amount:,}đ</b>\n"
            f"📝 Nội dung CK: <code>{memo}</code>\n\n"
            f"⏳ <b>Mã QR hết hạn lúc: {expire_str} ({QR_EXPIRE_MINUTES} phút)</b>\n\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📖 <b>HƯỚNG DẪN NẠP TIỀN</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"1️⃣ Mở app ngân hàng → <b>Chuyển khoản QR</b>\n"
            f"2️⃣ Quét mã QR ở trên\n"
            f"3️⃣ Kiểm tra <b>số tiền</b> và <b>nội dung CK</b> đúng chưa\n"
            f"4️⃣ Xác nhận chuyển khoản\n"
            f"5️⃣ Hệ thống sẽ <b>tự động cộng số dư</b> và xóa mã QR\n\n"
            f"<i>⚠️ Ghi ĐÚNG nội dung CK bên trên — hệ thống tự động cộng số dư!</i>"
        ),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="🏠 Về Menu Chính", callback_data="home")
        ]])
    )
    await cb.answer()

    # Lưu QR vào pending_qr và lên lịch expire
    async def expire_qr():
        await asyncio.sleep(QR_EXPIRE_MINUTES * 60)
        await delete_qr_for_user(uid, reason="expired")

    task = asyncio.create_task(expire_qr())
    pending_qr[uid] = {
        "chat_id": sent.chat.id,
        "msg_id" : sent.message_id,
        "task"   : task,
    }
    # Track tin nhắn QR vào auto-delete 3h
    track_message(uid, sent.chat.id, sent.message_id)

@dp.callback_query(F.data == "account")
async def cb_account(cb: CallbackQuery):
    await cb.message.edit_text(
        welcome_text(cb.from_user),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Quay lại", callback_data="help")]
        ])
    )
    await cb.answer()

@dp.callback_query(F.data == "history")
async def cb_history(cb: CallbackQuery):
    uid = cb.from_user.id
    history = payment_history.get(uid, [])
    if not history:
        text = "📜 <b>LỊCH SỬ GIAO DỊCH</b>\n\nBạn chưa có giao dịch nào."
    else:
        lines = ["📜 <b>LỊCH SỬ GIAO DỊCH</b>\n"]
        for item in history[:10]:
            date_obj = datetime.fromisoformat(item['date'])
            date_str = date_obj.strftime('%d/%m/%Y %H:%M')
            lines.append(f"<b>- {item['description']}</b>")
            lines.append(f"  <i>({date_str})</i>: {item['details']}")
        text = "\n".join(lines)
    await cb.message.edit_text(
        text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Quay lại", callback_data="help")]
        ])
    )
    await cb.answer()

# ── MUA VIP ──
@dp.callback_query(F.data == "buy_vip_menu")
async def cb_buy_vip_menu(cb: CallbackQuery):
    uid = cb.from_user.id
    bal = user_balances.get(uid, 0)
    text = (
        f"🛒 <b>MUA GÓI VIP BẰNG SỐ DƯ</b>\n\n"
        f"💰 Số dư hiện tại: <b>{bal:,.0f}đ</b>\n\n"
        "Vui lòng chọn gói VIP bạn muốn mua.\nNếu không đủ tiền, hãy vào mục Nạp Tiền nhé!"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="1 Ngày - 30.000đ",      callback_data="buy_vip_1")],
        [InlineKeyboardButton(text="7 Ngày - 120.000đ",     callback_data="buy_vip_7")],
        [InlineKeyboardButton(text="1 Tháng - 220.000đ",    callback_data="buy_vip_30")],
        [InlineKeyboardButton(text="Vĩnh viễn - 380.000đ",  callback_data="buy_vip_999")],
        [InlineKeyboardButton(text="🏠 Về Menu Chính",       callback_data="home")]
    ])
    if cb.message.photo:
        await cb.message.delete()
        await cb.message.answer(text, reply_markup=kb)
    else:
        await cb.message.edit_text(text, reply_markup=kb)
    await cb.answer()

@dp.callback_query(F.data.startswith("buy_vip_") & ~F.data.contains("menu"))
async def cb_process_buy_vip(cb: CallbackQuery):
    days = int(cb.data.split("_")[2])
    price_map = {1: 30000, 7: 120000, 30: 220000, 999: 380000}
    price = price_map.get(days, 30000)
    uid = cb.from_user.id
    bal = user_balances.get(uid, 0)

    if bal < price:
        await cb.answer(f"❌ Số dư không đủ! Bạn cần thêm {price - bal:,.0f}đ.", show_alert=True)
        return

    user_balances[uid] -= price
    info = valid_keys.get(uid)
    current_exp = info.get("expires") if info else None

    if days == 999:
        expires = None
    else:
        if current_exp and current_exp > datetime.now():
            expires = current_exp + timedelta(days=days)
        else:
            expires = datetime.now() + timedelta(days=days)

    valid_keys[uid] = {"key": "BOUGHT_FROM_BALANCE", "expires": expires}

    if uid not in payment_history:
        payment_history[uid] = []
    days_text = "Vĩnh viễn" if days == 999 else f"+{days} ngày"
    payment_history[uid].insert(0, {
        "date": datetime.now().isoformat(),
        "description": f"Mua gói VIP {days_text}",
        "details": f"-{price:,.0f}đ"
    })
    save_data()

    exp_str = "Vĩnh viễn ♾️" if expires is None else expires.strftime("%d/%m/%Y %H:%M")
    await cb.message.edit_text(
        f"✅ <b>MUA VIP THÀNH CÔNG!</b>\n\n"
        f"Gói mua: <b>{days_text}</b>\n"
        f"Số dư còn lại: <b>{user_balances[uid]:,.0f}đ</b>\n"
        f"⏳ Hạn sử dụng mới: <b>{exp_str}</b>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="🏠 Về Menu Chính", callback_data="home")
        ]])
    )
    await cb.answer()

# ── NHẬP KEY ──
@dp.callback_query(F.data == "enter_key")
async def cb_enter_key(cb: CallbackQuery, state: FSMContext):
    await state.set_state(UserState.waiting_key)
    await cb.message.edit_text(
        "🔑 <b>Nhập Key Kích Hoạt</b>\n\n"
        "Vui lòng gửi Key của bạn vào đây.\n"
        "<i>Liên hệ Admin để mua Key nếu chưa có.</i>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ Hủy", callback_data="home")]
        ])
    )
    await cb.answer()

@dp.message(UserState.waiting_key)
async def process_key(msg: Message, state: FSMContext):
    await state.clear()
    uid = msg.from_user.id
    key = msg.text.strip()

    if key not in key_store:
        await msg.answer(
            "❌ <b>Key không hợp lệ!</b>\n\nVui lòng kiểm tra lại hoặc liên hệ Admin mua Key mới.",
            reply_markup=kb_start(False)
        )
        return

    info = key_store[key]
    if info["used_by"] is not None and info["used_by"] != uid:
        await msg.answer(
            "⚠️ <b>Key này đã được sử dụng bởi tài khoản khác!</b>",
            reply_markup=kb_start(False)
        )
        return

    days = info["duration_days"]
    expires = None if days == -1 else datetime.now() + timedelta(days=days)
    valid_keys[uid] = {"key": key, "expires": expires}
    key_store[key]["used_by"] = uid

    if uid not in payment_history:
        payment_history[uid] = []
    days_text = "Vĩnh viễn" if days == -1 else f"+{days} ngày"
    payment_history[uid].insert(0, {
        "date": datetime.now().isoformat(),
        "description": f"Kích hoạt key <code>{key}</code>",
        "details": days_text
    })
    save_data()

    exp_str = "Vĩnh viễn ♾️" if expires is None else expires.strftime("%d/%m/%Y %H:%M")
    await msg.answer(
        f"✅ <b>Kích hoạt Key thành công!</b>\n\n"
        f"🔑 Key: <code>{key}</code>\n"
        f"⏳ Hạn sử dụng: <b>{exp_str}</b>\n\n"
        f"Chào mừng bạn đến với TOOL AI SEW PRO! 🎉",
        reply_markup=kb_start(True)
    )
    try:
        user_name = msg.from_user.full_name
        username = f" (@{msg.from_user.username})" if msg.from_user.username else ""
        await bot.send_message(
            ADMIN_ID,
            f"🔔 <b>THÔNG BÁO NẠP KEY THÀNH CÔNG</b>\n\n"
            f"👤 Người dùng: <b>{user_name}</b>{username}\n"
            f"🆔 ID: <code>{uid}</code>\n"
            f"🔑 Key sử dụng: <code>{key}</code>\n"
            f"⏳ Hạn sử dụng: <b>{exp_str}</b>"
        )
    except Exception as e:
        log.error(f"Lỗi gửi thông báo cho admin: {e}")

# ── DANH SÁCH GAME ──
@dp.callback_query(F.data == "game_list")
async def cb_game_list(cb: CallbackQuery):
    uid = cb.from_user.id
    if not is_authorized(uid):
        await cb.answer("❌ Bạn chưa có Key hợp lệ!", show_alert=True)
        return
    await cb.message.edit_text(
        "🎮 <b>DANH SÁCH GAME HỖ TRỢ</b>\n\nChọn game bạn muốn xem dự đoán:",
        reply_markup=kb_games()
    )
    await cb.answer()

@dp.callback_query(F.data.startswith("game_"))
async def cb_game(cb: CallbackQuery):
    uid     = cb.from_user.id
    game_id = cb.data[5:]
    if not is_authorized(uid):
        await cb.answer("❌ Key hết hạn hoặc chưa kích hoạt!", show_alert=True)
        return
    game = GAME_MAP.get(game_id)
    if not game:
        await cb.answer("Game không tồn tại.", show_alert=True)
        return

    # ── Baccarat Sunwin: gọi API riêng ──
    if game_id == "bcr_sunwin":
        await cb.answer("⏳ Đang tải Baccarat Sunwin...")
        raw = await asyncio.to_thread(fetch_bcr_sunwin)
        if raw.get("ok"):
            text = format_bcr_sexy(1, raw)
            text = text.replace("BÀN 1", "SUNWIN")
        else:
            text = "🏆 <b>BACCARAT SUNWIN</b>\n\n⚠️ Không lấy được dữ liệu từ API. Thử lại sau!"
        await cb.message.edit_text(text, reply_markup=kb_game_result(game_id))
        return

    # ── Baccarat Sexy: hiện menu chọn bàn ──
    if game_id == "baccarat_sexy":
        await cb.message.edit_text(
            "👠 <b>BACCARAT SEXY</b>\n\n"
            "🎰 Chọn bàn bạn muốn xem dự đoán:\n"
            "<i>(Mỗi bàn là 1 bàn chơi riêng biệt trên hệ thống)</i>",
            reply_markup=kb_bcr_tables()
        )
        await cb.answer()
        return

    # ── Sicbo Hitclub: dùng API riêng ──
    if game_id == "hit_sicbo":
        await cb.answer()
        raw = await asyncio.to_thread(fetch_hit_sicbo)
        if raw.get("ok"):
            text = format_sicbo(game, raw)
        else:
            text = f"🎯 <b>{game['name']}</b>\n\n⚠️ Không lấy được dữ liệu. Thử lại sau!"
        await cb.message.edit_text(text, reply_markup=kb_game_result(game_id, uid))
        return

    await cb.answer()
    data = await asyncio.to_thread(fetch_prediction, game_id)
    text = format_result(game, data)
    await cb.message.edit_text(text, reply_markup=kb_game_result(game_id, uid))

@dp.callback_query(F.data.startswith("bcr_table_"))
async def cb_bcr_table(cb: CallbackQuery):
    uid = cb.from_user.id
    if not is_authorized(uid):
        await cb.answer("❌ Key hết hạn hoặc chưa kích hoạt!", show_alert=True)
        return
    try:
        table_num = int(cb.data.split("_")[2])
    except Exception:
        await cb.answer("Bàn không hợp lệ.", show_alert=True)
        return
    await cb.answer(f"⏳ Đang tải Bàn {table_num}...")
    raw = await asyncio.to_thread(fetch_bcr_sexy, table_num)
    if raw.get("ok"):
        text = format_bcr_sexy(table_num, raw)
    else:
        text = (
            f"👠 <b>BACCARAT SEXY — BÀN {table_num}</b>\n\n"
            f"⚠️ Không lấy được dữ liệu từ API.\n"
            f"Vui lòng thử lại sau!"
        )
    await cb.message.edit_text(text, reply_markup=kb_bcr_table_result(table_num, uid))

@dp.callback_query(F.data == "bcr_all")
async def cb_bcr_all(cb: CallbackQuery):
    uid = cb.from_user.id
    if not is_authorized(uid):
        await cb.answer("❌ Key hết hạn hoặc chưa kích hoạt!", show_alert=True)
        return
    await cb.answer("⏳ Đang tải tất cả bàn...")
    tables = await asyncio.to_thread(fetch_bcr_all)
    if tables:
        text = format_bcr_all(tables)
    else:
        text = (
            "👠 <b>BACCARAT SEXY — TẤT CẢ BÀN</b>\n\n"
            "⚠️ Không lấy được dữ liệu từ API.\n"
            "Thử xem từng bàn riêng lẻ hoặc thử lại sau!"
        )
    await cb.message.edit_text(text, reply_markup=kb_bcr_all())

# ── ADMIN MENU ──
@dp.message(Command("menu"))
async def cmd_menu(msg: Message):
    if msg.from_user.id != ADMIN_ID:
        await msg.answer(
            f"⛔ <b>Lỗi:</b> Bạn không phải là Admin!\n\n"
            f"<i>ID của bạn là:</i> <code>{msg.from_user.id}</code>\n\n"
            f"👉 Mở file <b>bot.py</b>, sửa dòng <code>ADMIN_ID</code> thành:\n"
            f"<code>ADMIN_ID = {msg.from_user.id}</code>"
        )
        return
    await msg.answer("🛠 <b>MENU QUẢN TRỊ (ADMIN)</b> 🛠\n\nChọn một chức năng:", reply_markup=kb_admin_menu())

@dp.callback_query(F.data == "admin_close")
async def cb_admin_close(cb: CallbackQuery):
    await cb.message.delete()

@dp.callback_query(F.data == "admin_cancel")
async def cb_admin_cancel(cb: CallbackQuery, state: FSMContext):
    current_state = await state.get_state()
    if current_state is None:
        return
    await state.clear()
    await cb.message.edit_text(
        "🛠 <b>MENU QUẢN TRỊ (ADMIN)</b> 🛠\n\nĐã hủy thao tác.",
        reply_markup=kb_admin_menu()
    )
    await cb.answer("Đã hủy.")

@dp.callback_query(F.data == "admin_stats")
async def cb_admin_stats(cb: CallbackQuery):
    if cb.from_user.id != ADMIN_ID: return
    total_users = len(all_users)
    active_vip = sum(1 for uid in valid_keys if is_authorized(uid))
    total_balance = sum(user_balances.values())
    text = (
        "📊 <b>THỐNG KÊ HỆ THỐNG</b>\n\n"
        f"👥 Tổng số người dùng: <b>{total_users}</b>\n"
        f"👑 Người dùng VIP (Active): <b>{active_vip}</b>\n"
        f"🔑 Tổng số Key đã tạo: <b>{len(key_store)}</b>\n"
        f"💰 Tổng số dư trong hệ thống: <b>{total_balance:,.0f}đ</b>"
    )
    await cb.message.edit_text(text, reply_markup=kb_admin_menu())
    await cb.answer("Đã cập nhật thống kê!")

@dp.callback_query(F.data == "admin_listkeys")
async def cb_admin_listkeys(cb: CallbackQuery):
    if cb.from_user.id != ADMIN_ID: return
    if not key_store:
        await cb.answer("Chưa có key nào.", show_alert=True)
        return
    lines = ["📋 <b>Danh sách Key</b>\n"]
    for k, v in key_store.items():
        used = f"User {v['used_by']}" if v["used_by"] else "Chưa dùng"
        d    = "♾️" if v["duration_days"] == -1 else f"{v['duration_days']}d"
        lines.append(f"• <code>{k}</code> [{d}] — {used}")
    text = "\n".join(lines)
    try:
        await cb.message.edit_text(text, reply_markup=kb_admin_menu())
        await cb.answer("Đã tải danh sách key!")
    except Exception:
        await cb.message.answer(text)
        await cb.answer("Danh sách key quá dài, đã gửi trong tin nhắn mới.")

@dp.callback_query(F.data == "admin_clear_keys")
async def cb_admin_clear_keys(cb: CallbackQuery):
    if cb.from_user.id != ADMIN_ID: return
    await cb.message.edit_text(
        "⚠️ <b>BẠN CÓ CHẮC CHẮN MUỐN XÓA TOÀN BỘ KEY KHÔNG?</b>\n\n"
        "Hành động này sẽ xóa tất cả key và thu hồi VIP của tất cả người dùng. Không thể hoàn tác!",
        reply_markup=kb_admin_confirm_clear()
    )
    await cb.answer()

@dp.callback_query(F.data == "admin_confirm_clear_yes")
async def cb_admin_confirm_clear_yes(cb: CallbackQuery):
    if cb.from_user.id != ADMIN_ID: return
    key_store.clear()
    valid_keys.clear()
    save_data()
    await cb.message.edit_text("✅ <b>Đã xóa toàn bộ Key trong hệ thống!</b>", reply_markup=kb_admin_menu())
    await cb.answer("Đã dọn dẹp toàn bộ key!", show_alert=True)

@dp.callback_query(F.data == "admin_confirm_clear_no")
async def cb_admin_confirm_clear_no(cb: CallbackQuery):
    if cb.from_user.id != ADMIN_ID: return
    await cb.message.edit_text(
        "🛠 <b>MENU QUẢN TRỊ (ADMIN)</b> 🛠\n\nĐã hủy thao tác xóa.",
        reply_markup=kb_admin_menu()
    )
    await cb.answer("Đã hủy.")

@dp.callback_query(F.data == "admin_addkey")
async def cb_admin_addkey_start(cb: CallbackQuery, state: FSMContext):
    if cb.from_user.id != ADMIN_ID: return
    await state.set_state(AdminState.addkey_key)
    await cb.message.edit_text("<b>Bước 1/2:</b> Nhập tên Key muốn tạo:", reply_markup=kb_cancel_admin())
    await cb.answer()

@dp.message(AdminState.addkey_key)
async def process_admin_addkey_key(msg: Message, state: FSMContext):
    if msg.from_user.id != ADMIN_ID: return
    key = msg.text.strip()
    if key in key_store:
        await msg.answer(f"⚠️ Key <code>{key}</code> đã tồn tại! Nhập key khác:", reply_markup=kb_cancel_admin())
        return
    await state.update_data(key=key)
    await state.set_state(AdminState.addkey_days)
    await msg.answer("<b>Bước 2/2:</b> Nhập số ngày sử dụng (nhập -1 cho key vĩnh viễn):", reply_markup=kb_cancel_admin())

@dp.message(AdminState.addkey_days)
async def process_admin_addkey_days(msg: Message, state: FSMContext):
    if msg.from_user.id != ADMIN_ID: return
    try:
        days = int(msg.text.strip())
    except ValueError:
        await msg.answer("❗ Số ngày không hợp lệ. Nhập số nguyên (VD: 30 hoặc -1):", reply_markup=kb_cancel_admin())
        return
    data = await state.get_data()
    key = data['key']
    key_store[key] = {"duration_days": days, "used_by": None}
    save_data()
    await state.clear()
    await msg.answer(f"✅ Đã tạo Key thành công!\n\nKey: <code>{key}</code>\nThời hạn: {days if days != -1 else '♾️ Vĩnh viễn'} ngày")
    await msg.answer("🛠 <b>MENU QUẢN TRỊ (ADMIN)</b> 🛠", reply_markup=kb_admin_menu())

@dp.callback_query(F.data == "admin_genkeys")
async def cb_admin_genkeys_start(cb: CallbackQuery, state: FSMContext):
    if cb.from_user.id != ADMIN_ID: return
    await state.set_state(AdminState.genkeys_amount)
    await cb.message.edit_text("<b>Bước 1/2:</b> Nhập số lượng key muốn tạo (tối đa 50):", reply_markup=kb_cancel_admin())
    await cb.answer()

@dp.message(AdminState.genkeys_amount)
async def process_admin_genkeys_amount(msg: Message, state: FSMContext):
    if msg.from_user.id != ADMIN_ID: return
    try:
        amount = int(msg.text.strip())
        if not (0 < amount <= 50): raise ValueError
    except ValueError:
        await msg.answer("❗ Số lượng không hợp lệ (1-50):", reply_markup=kb_cancel_admin())
        return
    await state.update_data(amount=amount)
    await state.set_state(AdminState.genkeys_days)
    await msg.answer("<b>Bước 2/2:</b> Nhập số ngày sử dụng (nhập -1 cho key vĩnh viễn):", reply_markup=kb_cancel_admin())

@dp.message(AdminState.genkeys_days)
async def process_admin_genkeys_days(msg: Message, state: FSMContext):
    if msg.from_user.id != ADMIN_ID: return
    try:
        days = int(msg.text.strip())
    except ValueError:
        await msg.answer("❗ Số ngày không hợp lệ:", reply_markup=kb_cancel_admin())
        return
    data = await state.get_data()
    amount = data['amount']
    generated_keys = []
    for _ in range(amount):
        while True:
            rand_str = ''.join(random.choices(string.ascii_uppercase + string.digits, k=8))
            new_key = f"VIP-{rand_str[:4]}-{rand_str[4:]}"
            if new_key not in key_store: break
        key_store[new_key] = {"duration_days": days, "used_by": None}
        generated_keys.append(new_key)
    save_data()
    await state.clear()
    keys_text = "\n".join([f"<code>{k}</code>" for k in generated_keys])
    duration_text = f"{days} ngày" if days != -1 else "Vĩnh viễn ♾️"
    await msg.answer(f"✅ <b>Đã tạo {amount} key ({duration_text}):</b>\n\n{keys_text}\n\n<i>(Chạm vào key để copy)</i>")
    await msg.answer("🛠 <b>MENU QUẢN TRỊ (ADMIN)</b> 🛠", reply_markup=kb_admin_menu())

@dp.callback_query(F.data == "admin_delkey")
async def cb_admin_delkey_start(cb: CallbackQuery, state: FSMContext):
    if cb.from_user.id != ADMIN_ID: return
    await state.set_state(AdminState.delkey_key)
    await cb.message.edit_text("Nhập tên Key muốn xóa:", reply_markup=kb_cancel_admin())
    await cb.answer()

@dp.message(AdminState.delkey_key)
async def process_admin_delkey_key(msg: Message, state: FSMContext):
    if msg.from_user.id != ADMIN_ID: return
    key = msg.text.strip()
    if key not in key_store:
        await msg.answer(f"⚠️ Key <code>{key}</code> không tồn tại! Nhập lại:", reply_markup=kb_cancel_admin())
        return
    used_by = key_store[key].get("used_by")
    if used_by is not None and used_by in valid_keys:
        del valid_keys[used_by]
    del key_store[key]
    save_data()
    await state.clear()
    await msg.answer(f"✅ Đã xóa Key: <code>{key}</code> và thu hồi quyền (nếu có)!")
    await msg.answer("🛠 <b>MENU QUẢN TRỊ (ADMIN)</b> 🛠", reply_markup=kb_admin_menu())

@dp.callback_query(F.data == "admin_broadcast")
async def cb_admin_broadcast_start(cb: CallbackQuery, state: FSMContext):
    if cb.from_user.id != ADMIN_ID: return
    await state.set_state(AdminState.broadcast_text)
    await cb.message.edit_text("Nhập nội dung tin nhắn gửi tới tất cả người dùng VIP:", reply_markup=kb_cancel_admin())
    await cb.answer()

@dp.message(AdminState.broadcast_text)
async def process_admin_broadcast_text(msg: Message, state: FSMContext):
    if msg.from_user.id != ADMIN_ID: return
    text = msg.text.strip()
    await state.clear()
    sent = 0
    for uid in valid_keys:
        if not is_authorized(uid): continue
        try:
            await bot.send_message(uid, f"📢 <b>THÔNG BÁO TỪ ADMIN</b>\n\n{text}")
            sent += 1
            await asyncio.sleep(0.1)
        except Exception:
            pass
    await msg.answer(f"✅ Đã gửi thông báo tới {sent} người dùng VIP đang hoạt động.")
    await msg.answer("🛠 <b>MENU QUẢN TRỊ (ADMIN)</b> 🛠", reply_markup=kb_admin_menu())


# ─────────────────────────────────────────────
#  WEBHOOK SEPAY — ✅ ĐÃ FIX HOÀN TOÀN
#  - Tìm "NAP <uid>" trong nội dung CK
#  - Không cần pending_payments
#  - Không mất dữ liệu khi Render restart
#  - Log đầy đủ để debug
# ─────────────────────────────────────────────
@app.post("/sepay-webhook")
async def sepay_webhook(request: Request):
    try:
        raw_body = await request.body()
        log.info(f"📥 WEBHOOK RAW: {raw_body.decode('utf-8', errors='ignore')}")

        data = await request.json()
        amount_in = float(data.get('transferAmount', 0))
        content = str(data.get('content', '')).upper()

        log.info(f"💰 SePay: +{amount_in}đ | Nội dung: {content}")

        # Tìm "NAP <UID>" trong nội dung chuyển khoản
        match = re.search(r'NAP\s*(\d+)', content)
        if not match:
            log.info(f"❌ Không tìm thấy mã NAP. Content: {content}")
            return {"status": "ignored", "reason": "no NAP code"}

        uid = int(match.group(1))
        log.info(f"✅ Tìm thấy UID: {uid}, Số tiền: {amount_in}")

        # Cộng tiền vào số dư
        if uid not in user_balances:
            user_balances[uid] = 0
        user_balances[uid] += int(amount_in)

        # Lưu lịch sử giao dịch
        if uid not in payment_history:
            payment_history[uid] = []
        payment_history[uid].insert(0, {
            "date": now_vn().isoformat(),
            "description": "Nạp tiền tự động (SePay)",
            "details": f"+{amount_in:,.0f}đ"
        })
        save_data()

        bal = user_balances[uid]

        # ✅ Xóa QR đang pending của user (nếu có)
        await delete_qr_for_user(uid, reason="paid")

        # Gửi thông báo NẠP THÀNH CÔNG kèm số dư
        try:
            nap_msg = await bot.send_message(
                uid,
                f"✅ <b>NẠP TIỀN THÀNH CÔNG!</b>\n\n"
                f"💰 Đã nhận: <b>+{amount_in:,.0f}đ</b>\n"
                f"💳 Số dư hiện tại: <b>{bal:,.0f}đ</b>\n\n"
                f"<i>👉 Vào Menu Chính → <b>MUA VIP</b> để đổi số dư thành ngày sử dụng!</i>",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                    InlineKeyboardButton(text="🛒 Mua VIP ngay", callback_data="buy_vip_menu"),
                    InlineKeyboardButton(text="🏠 Menu Chính",   callback_data="home"),
                ]])
            )
            track_message(uid, nap_msg.chat.id, nap_msg.message_id)
            log.info(f"✅ Đã gửi thông báo nạp tiền cho user {uid}")
        except Exception as e:
            log.warning(f"⚠️ Không gửi được tin nhắn cho user {uid}: {e}")

        # Lấy thông tin Telegram của user
        tele_name = "Không rõ"
        tele_username = ""
        try:
            chat = await bot.get_chat(uid)
            tele_name = chat.full_name or "Không rõ"
            tele_username = f"@{chat.username}" if chat.username else "Không có username"
        except Exception:
            pass

        now_str = now_vn().strftime('%d/%m/%Y %H:%M:%S')

        # Gửi thông báo cho Admin
        try:
            await bot.send_message(
                ADMIN_ID,
                f"🔔 <b>ĐƠN NẠP TIỀN MỚI</b>\n"
                f"{'━'*28}\n"
                f"👤 <b>Tên Telegram:</b> {tele_name}\n"
                f"🔗 <b>Username:</b> {tele_username}\n"
                f"🆔 <b>ID Telegram:</b> <code>{uid}</code>\n"
                f"{'━'*28}\n"
                f"⏰ <b>Thời gian:</b> {now_str}\n"
                f"📝 <b>Nội dung CK:</b> <code>{content}</code>\n"
                f"💵 <b>Số tiền:</b> <b>+{amount_in:,.0f}đ</b>\n"
                f"{'━'*28}\n"
                f"💳 <b>Số dư mới của khách:</b> {bal:,.0f}đ"
            )
        except Exception as e:
            log.warning(f"⚠️ Không gửi được tin nhắn cho admin: {e}")

        return {"status": "success"}

    except Exception as e:
        log.error(f"❌ Webhook error: {e}")
        return {"status": "error", "detail": str(e)}

# ─────────────────────────────────────────────
#  AUTO-PREDICT: Subscribe / Unsubscribe
# ─────────────────────────────────────────────
POLL_GAMES = ["sunwin_tx", "sunwin_sicbo", "lc79_hu", "lc79_md5", "b52_tx", "hit_sicbo"]
BCR_POLL_TABLES = list(range(1, 11))
POLL_INTERVAL = 12  # giây

GAME_LABELS = {
    "sunwin_tx"    : "🎯 Tài Xỉu Sunwin",
    "sunwin_sicbo" : "🎱 Sicbo Sunwin",
    "lc79_hu"      : "🏺 lc79 Hũ",
    "lc79_md5"     : "🔑 lc79 MD5",
    "b52_tx"       : "✈️ TX B52",
    "hit_sicbo"    : "🎯 Sicbo Hitclub",
}

def sub_label(key: str) -> str:
    if key.startswith("bcr_table_"):
        n = key.split("_")[2]
        return f"👠 Baccarat Sexy Bàn {n}"
    return GAME_LABELS.get(key, key)

@dp.callback_query(F.data.startswith("sub_"))
async def cb_subscribe(cb: CallbackQuery):
    uid = cb.from_user.id
    if not is_authorized(uid):
        await cb.answer("❌ Key hết hạn!", show_alert=True)
        return
    key = cb.data[4:]
    if uid not in auto_subs:
        auto_subs[uid] = set()
    auto_subs[uid].add(key)
    save_data()
    label = sub_label(key)
    await cb.answer(f"✅ Đã bật tự động: {label}", show_alert=False)
    # Cập nhật inline keyboard
    try:
        if key.startswith("bcr_table_"):
            table_num = int(key.split("_")[2])
            await cb.message.edit_reply_markup(reply_markup=kb_bcr_table_result(table_num, uid))
        else:
            await cb.message.edit_reply_markup(reply_markup=kb_game_result(key, uid))
    except Exception:
        pass

@dp.callback_query(F.data.startswith("unsub_"))
async def cb_unsubscribe(cb: CallbackQuery):
    uid = cb.from_user.id
    key = cb.data[6:]
    if uid in auto_subs:
        auto_subs[uid].discard(key)
        if not auto_subs[uid]:
            del auto_subs[uid]
    save_data()
    label = sub_label(key)
    await cb.answer(f"🔕 Đã tắt tự động: {label}", show_alert=False)
    try:
        if key.startswith("bcr_table_"):
            table_num = int(key.split("_")[2])
            await cb.message.edit_reply_markup(reply_markup=kb_bcr_table_result(table_num, uid))
        else:
            await cb.message.edit_reply_markup(reply_markup=kb_game_result(key, uid))
    except Exception:
        pass

# ─────────────────────────────────────────────
#  AUTO-PREDICT: Background polling tasks
# ─────────────────────────────────────────────
def get_session_key(game_id: str, data: dict) -> str:
    """Lấy session key để so sánh phiên mới/cũ."""
    return str(data.get("next_session") or data.get("current_session") or "")

async def broadcast_game(game_id: str, data: dict):
    """Gửi dự đoán mới tới tất cả subscriber của game này."""
    game = GAME_MAP.get(game_id)
    if not game:
        return
    if game_id == "hit_sicbo":
        text = format_sicbo(game, data)
    else:
        text = format_result(game, data)
    text += "\n\n🤖 <i>Dự đoán tự động</i>"

    kb = kb_game_result(game_id)  # keyboard không có uid — dùng chung
    dead_uids = []
    for uid, keys in list(auto_subs.items()):
        if game_id not in keys:
            continue
        if not is_authorized(uid):
            continue
        try:
            kb_uid = kb_game_result(game_id, uid)
            sent_msg = await bot.send_message(uid, text, reply_markup=kb_uid)
            track_message(uid, sent_msg.chat.id, sent_msg.message_id)
            await asyncio.sleep(0.05)
        except Exception as e:
            if "bot was blocked" in str(e).lower() or "chat not found" in str(e).lower():
                dead_uids.append((uid, game_id))
    for uid, gid in dead_uids:
        if uid in auto_subs:
            auto_subs[uid].discard(gid)

async def broadcast_bcr_table(table_num: int, raw: dict):
    """Gửi dự đoán BCR Sexy tới subscriber bàn đó."""
    key = f"bcr_table_{table_num}"
    text = format_bcr_sexy(table_num, raw)
    text += "\n\n🤖 <i>Dự đoán tự động</i>"
    dead_uids = []
    for uid, keys in list(auto_subs.items()):
        if key not in keys:
            continue
        if not is_authorized(uid):
            continue
        try:
            sent_msg = await bot.send_message(uid, text, reply_markup=kb_bcr_table_result(table_num, uid))
            track_message(uid, sent_msg.chat.id, sent_msg.message_id)
            await asyncio.sleep(0.05)
        except Exception as e:
            if "bot was blocked" in str(e).lower() or "chat not found" in str(e).lower():
                dead_uids.append((uid, key))
    for uid, k in dead_uids:
        if uid in auto_subs:
            auto_subs[uid].discard(k)

async def poll_game_loop(game_id: str):
    """Background task: poll 1 game liên tục, broadcast khi phiên mới."""
    log.info(f"🔄 Bắt đầu poll: {game_id}")
    while True:
        try:
            if game_id == "hit_sicbo":
                data = await asyncio.to_thread(fetch_hit_sicbo)
                if not data.get("ok"):
                    await asyncio.sleep(POLL_INTERVAL)
                    continue
            else:
                data = await asyncio.to_thread(fetch_prediction, game_id)

            sess = get_session_key(game_id, data)
            if sess and sess != last_session_cache.get(game_id):
                last_session_cache[game_id] = sess
                # Chỉ broadcast nếu có subscriber
                has_subs = any(game_id in v for v in auto_subs.values())
                if has_subs:
                    await broadcast_game(game_id, data)
        except Exception as e:
            log.warning(f"Poll error {game_id}: {e}")
        await asyncio.sleep(POLL_INTERVAL)

async def poll_bcr_table_loop(table_num: int):
    """Background task: poll 1 bàn BCR liên tục."""
    key = f"bcr_table_{table_num}"
    while True:
        try:
            raw = await asyncio.to_thread(fetch_bcr_sexy, table_num)
            if raw.get("ok"):
                sess = str(raw.get("so_phien", ""))
                if sess and sess != last_session_cache.get(key):
                    last_session_cache[key] = sess
                    has_subs = any(key in v for v in auto_subs.values())
                    if has_subs:
                        await broadcast_bcr_table(table_num, raw)
        except Exception as e:
            log.warning(f"Poll BCR table {table_num} error: {e}")
        await asyncio.sleep(POLL_INTERVAL)

async def start_all_poll_tasks():
    """Khởi động tất cả background polling tasks."""
    log.info("🚀 Khởi động auto-predict polling tasks...")
    for gid in POLL_GAMES:
        asyncio.create_task(poll_game_loop(gid))
    for t in BCR_POLL_TABLES:
        asyncio.create_task(poll_bcr_table_loop(t))
    asyncio.create_task(auto_delete_old_messages())
    log.info(f"✅ Đã khởi động {len(POLL_GAMES)} game + {len(BCR_POLL_TABLES)} bàn BCR + auto-delete task")

# Route alias
@app.post("/api/sepay-webhook")
async def sepay_webhook_api(request: Request):
    return await sepay_webhook(request)

@app.get("/")
async def health_check():
    return {"status": "alive"}

# ─────────────────────────────────────────────
#  MAIN
# ─────────────────────────────────────────────
async def main():
    log.info("🚀 Bot đang khởi động...")
    config = uvicorn.Config(app, host="0.0.0.0", port=8000, log_level="warning")
    server = uvicorn.Server(config)
    try:
        await asyncio.gather(
            server.serve(),
            dp.start_polling(bot, handle_signals=False),
            start_all_poll_tasks(),
        )
    except OSError as e:
        log.error(f"Lỗi Port: {e}")

if __name__ == "__main__":
    asyncio.run(main())
