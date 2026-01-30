import random
import json
import re
import os
import time
import asyncio
import hashlib
import math
import urllib.request
import ssl
import shutil
import sqlite3
from io import BytesIO
from functools import lru_cache
import astrbot.api.message_components as Comp
from urllib.parse import quote
from datetime import datetime
from astrbot.api.all import *

# === 引入图像处理库 ===
try:
    from PIL import Image, ImageDraw, ImageFont, ImageFilter
except ImportError:
    raise ImportError("请先安装 Pillow 库: pip install Pillow")

# === 路径配置 ===
PLUGIN_DIR = os.path.join('data', 'plugins', 'astrbot_plugin_openweaponscase', 'data')
IMAGES_MAP_FILE = os.path.join(PLUGIN_DIR, 'case_images.json')
HISTORY_FILE = os.path.join(PLUGIN_DIR, 'open_history.json')
CASES_FILE = os.path.join(PLUGIN_DIR, 'cases.json')
DB_FILE = os.path.join(PLUGIN_DIR, 'data.db')
IMAGES_DIR = os.path.join(PLUGIN_DIR, 'images')

# ================= 配置区域 =================

QUALITY_COLORS = {
    "消费级": (176, 195, 217),
    "工业级": (94, 152, 217),
    "军规级": (75, 105, 255),
    "受限": (136, 71, 255),
    "保密": (211, 44, 230),
    "隐秘": (235, 75, 75),
    "非凡": (255, 215, 0),
    "Contraband": (255, 165, 0)
}

WEAR_LEVELS = [
    ("崭新出厂", 0.03, 0.00, 0.07),
    ("略有磨损", 0.24, 0.07, 0.15),
    ("久经沙场", 0.33, 0.15, 0.45),
    ("破损不堪", 0.24, 0.30, 0.45),
    ("战痕累累", 0.16, 0.45, 1.00)
]

DOPPLER_WEAR_LEVELS = [("崭新出厂", 0.03, 0.00, 0.87), ("略有磨损", 0.24, 0.07, 0.12)]

PROB_CATEGORY_1 = {"军规级": 0.79923, "受限": 0.15985, "保密": 0.03197, "隐秘": 0.00639, "非凡": 0.00256}
PROB_CATEGORY_2 = {"消费级": 0.80537, "工业级": 0.16107, "军规级": 0.03356}
PROB_CATEGORY_3 = {"工业级": 0.80000, "军规级": 0.16667, "受限": 0.03333}
PROB_CATEGORY_4 = {"消费级": 0.80000, "工业级": 0.16000, "军规级": 0.03333, "受限": 0.00667}
PROB_CATEGORY_5 = {"消费级": 0.79893, "工业级": 0.15979, "军规级": 0.03329, "受限": 0.00666, "保密": 0.00133}
PROB_CATEGORY_6 = {"消费级": 0.79872, "工业级": 0.15974, "军规级": 0.03328, "受限": 0.00666, "保密": 0.00133, "隐秘": 0.00027}
PROB_CATEGORY_15 = {"军规级": 0.80128, "受限": 0.16026, "保密": 0.03205, "隐秘": 0.00641}

NORMAL_DOPPLER_PROBS = {"p1": 0.2, "p2": 0.2, "p3": 0.2, "p4": 0.2, "蓝宝石": 0.1, "红宝石": 0.05, "黑珍珠": 0.05}
GAMMA_DOPPLER_PROBS = {"p1": 0.2, "p2": 0.2, "p3": 0.2, "p4": 0.2, "绿宝石": 0.2}

ALL_QUALITIES = set().union(*[p.keys() for p in [PROB_CATEGORY_1, PROB_CATEGORY_2, PROB_CATEGORY_3, PROB_CATEGORY_4, PROB_CATEGORY_5, PROB_CATEGORY_6, PROB_CATEGORY_15]])

def get_wear_name(wear_value):
    if wear_value < 0.07: return "崭新出厂"
    if wear_value < 0.15: return "略有磨损"
    if wear_value < 0.38: return "久经沙场"
    if wear_value < 0.45: return "破损不堪"
    return "战痕累累"

# ================= 辅助类：网络请求 =================
class NetworkManager:
    def __init__(self, api_token):
        self.ssl_context = ssl._create_unverified_context()
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Referer': 'https://buff.163.com/',
            'Content-Type': 'application/json',
            'ApiToken': api_token
        }

    def request(self, url, method="GET", data=None, max_retries=3):
        if data: data = data.encode('utf-8')
        if not url.startswith("http"): url = "https://" + url
        for attempt in range(max_retries):
            try:
                req = urllib.request.Request(url, data=data, headers=self.headers, method=method)
                with urllib.request.urlopen(req, context=self.ssl_context, timeout=20) as response:
                    return json.loads(response.read().decode('utf-8'))
            except Exception as e:
                if attempt == max_retries - 1: raise e
                time.sleep(2)

# ================= 辅助类：图片管理 =================
class ImageManager:
    def __init__(self):
        os.makedirs(IMAGES_DIR, exist_ok=True)
        self.ssl_context = ssl._create_unverified_context()

    def _get_file_path(self, url):
        hash_name = hashlib.md5(url.encode()).hexdigest()
        return os.path.join(IMAGES_DIR, f"{hash_name}.png")

    @lru_cache(maxsize=128)
    def get_cached_image(self, file_path):
        try:
            if os.path.exists(file_path) and os.path.getsize(file_path) > 100:
                img = Image.open(file_path).convert("RGBA")
                return img
        except: return None
        return None

    async def get_image(self, url):
        if not url: return None
        file_path = self._get_file_path(url)
        if os.path.exists(file_path):
            return self.get_cached_image(file_path)
        try:
            return await asyncio.to_thread(self._download_sync, url, file_path)
        except: return None

    def _download_sync(self, url, file_path):
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Referer': 'https://buff.163.com/',
            'Accept': 'image/webp,image/apng,image/*,*/*;q=0.8'
        }
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=15, context=self.ssl_context) as response:
                data = response.read()
                if len(data) < 100: return None
                with open(file_path, "wb") as f: f.write(data)
                return Image.open(BytesIO(data)).convert("RGBA")
        except: return None

# ================= 辅助类：数据库管理 =================
class DatabaseManager:
    def __init__(self):
        os.makedirs(PLUGIN_DIR, exist_ok=True)
        self.db_path = DB_FILE
        self._init_db()

    def _get_conn(self):
        return sqlite3.connect(self.db_path)

    def _init_db(self):
        conn = self._get_conn()
        c = conn.cursor()
        
        c.execute('''CREATE TABLE IF NOT EXISTS history (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        user_key TEXT NOT NULL,
                        name TEXT,
                        quality TEXT,
                        wear_value REAL,
                        is_special INTEGER,
                        img_url TEXT, 
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )''')
        try:
            c.execute("PRAGMA table_info(history)")
            columns = [col[1] for col in c.fetchall()]
            if 'img_url' not in columns: c.execute("ALTER TABLE history ADD COLUMN img_url TEXT")
        except: pass
        c.execute('''CREATE INDEX IF NOT EXISTS idx_user_key ON history (user_key)''')
        c.execute('''CREATE TABLE IF NOT EXISTS user_stats (
                        user_key TEXT NOT NULL,
                        quality TEXT NOT NULL,
                        count INTEGER DEFAULT 0,
                        PRIMARY KEY (user_key, quality)
                    )''')
        c.execute('''CREATE TABLE IF NOT EXISTS containers (
                        name TEXT PRIMARY KEY,
                        img_url TEXT,
                        type TEXT
                    )''')
        c.execute('''CREATE TABLE IF NOT EXISTS items (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        container_name TEXT,
                        short_name TEXT,
                        quality TEXT,
                        img_url TEXT,
                        FOREIGN KEY(container_name) REFERENCES containers(name)
                    )''')
        c.execute('''CREATE INDEX IF NOT EXISTS idx_container ON items (container_name)''')
        conn.commit()
        conn.close()

    def migrate_json_history(self, item_img_map):
        if not os.path.exists(HISTORY_FILE): return
        conn = self._get_conn()
        c = conn.cursor()
        c.execute("SELECT count(*) FROM user_stats")
        has_stats = c.fetchone()[0] > 0
        if has_stats:
            conn.close()
            return

        print("检测到旧版历史记录，正在迁移至数据库...")
        try:
            with open(HISTORY_FILE, 'r', encoding='utf-8') as f: old_data = json.load(f)
            history_rows = []
            stats_rows = []
            
            for uid, data in old_data.items():
                for item in data.get("items", []):
                    name = item.get("name", "未知")
                    clean_name = name.replace("StatTrak™ | ", "").replace("纪念品 | ", "").strip()
                    if "多普勒" in clean_name and "(" in clean_name: clean_name = clean_name.split("(")[0].strip()
                    
                    img_url = item_img_map.get(clean_name)
                    if not img_url and "|" in clean_name:
                        img_url = item_img_map.get(clean_name.split("|")[-1].strip())
                    
                    final_img = img_url if img_url else "" 
                    history_rows.append((uid, name, "未知", item.get("wear_value", 0), 1, final_img))
                
                for quality, count in data.get("other_stats", {}).items():
                    if count > 0:
                        stats_rows.append((uid, quality, count))
            
            if history_rows:
                c.executemany("INSERT INTO history (user_key, name, quality, wear_value, is_special, img_url) VALUES (?, ?, ?, ?, ?, ?)", history_rows)
            if stats_rows:
                c.executemany("INSERT OR REPLACE INTO user_stats (user_key, quality, count) VALUES (?, ?, ?)", stats_rows)
            
            conn.commit()
            print("历史记录迁移完成。")
            os.rename(HISTORY_FILE, HISTORY_FILE + ".bak")
        except Exception as e:
            print(f"历史迁移警告: {e}")
        finally: conn.close()

    def migrate_cases(self):
        if not os.path.exists(CASES_FILE): return
        conn = self._get_conn()
        c = conn.cursor()
        c.execute("SELECT count(*) FROM containers")
        if c.fetchone()[0] > 0:
            conn.close()
            return
        try:
            with open(CASES_FILE, 'r', encoding='utf-8') as f: cases = json.load(f)
            case_imgs = {}
            if os.path.exists(IMAGES_MAP_FILE):
                with open(IMAGES_MAP_FILE, 'r', encoding='utf-8') as f: case_imgs = json.load(f)
            for name, items in cases.items():
                img = case_imgs.get(name, "")
                c.execute("INSERT OR REPLACE INTO containers (name, img_url) VALUES (?, ?)", (name, img))
                rows = []
                for item in items:
                    rows.append((name, item.get("short_name"), item.get("rln"), item.get("img")))
                c.executemany("INSERT INTO items (container_name, short_name, quality, img_url) VALUES (?, ?, ?, ?)", rows)
            conn.commit()
            os.rename(CASES_FILE, CASES_FILE + ".bak")
            if os.path.exists(IMAGES_MAP_FILE): os.rename(IMAGES_MAP_FILE, IMAGES_MAP_FILE + ".bak")
        except Exception as e: pass
        finally: conn.close()

    def save_all_data(self, new_cases, new_imgs):
        conn = self._get_conn()
        c = conn.cursor()
        try:
            c.execute("DELETE FROM items")
            c.execute("DELETE FROM containers")
            for name, items in new_cases.items():
                img = new_imgs.get(name, "")
                c.execute("INSERT INTO containers (name, img_url) VALUES (?, ?)", (name, img))
                rows = []
                for item in items:
                    rows.append((name, item.get("short_name"), item.get("rln"), item.get("img")))
                c.executemany("INSERT INTO items (container_name, short_name, quality, img_url) VALUES (?, ?, ?, ?)", rows)
            conn.commit()
            return True
        except Exception as e:
            print(f"保存失败: {e}")
            return False
        finally: conn.close()

    def load_all_data(self):
        conn = self._get_conn()
        c = conn.cursor()
        c.execute("SELECT name, img_url FROM containers")
        images_map = {row[0]: row[1] for row in c.fetchall()}
        case_data = {}
        c.execute("SELECT container_name, short_name, quality, img_url FROM items")
        for row in c.fetchall():
            c_name, s_name, q, img = row
            if c_name not in case_data: case_data[c_name] = []
            case_data[c_name].append({"short_name": s_name, "rln": q, "img": img})
        item_img_map = {}
        c.execute("SELECT short_name, img_url FROM items WHERE img_url IS NOT NULL")
        for row in c.fetchall(): item_img_map[row[0]] = row[1]
        conn.close()
        return case_data, images_map, item_img_map

    def add_item(self, user_key, item):
        conn = self._get_conn()
        c = conn.cursor()
        quality = item['quality']
        is_rare = quality in ["隐秘", "非凡", "Contraband"] or item.get('is_special', False)
        if is_rare:
            c.execute("INSERT INTO history (user_key, name, quality, wear_value, is_special, img_url) VALUES (?, ?, ?, ?, ?, ?)",
                      (user_key, item['name'], quality, item['wear_value'], 1, item.get('img', '')))
        else:
            c.execute("""
                INSERT INTO user_stats (user_key, quality, count) VALUES (?, ?, 1)
                ON CONFLICT(user_key, quality) DO UPDATE SET count = count + 1
            """, (user_key, quality))
        conn.commit()
        conn.close()

    def get_user_stats(self, user_key):
        conn = self._get_conn()
        c = conn.cursor()
        c.execute("SELECT quality, count FROM user_stats WHERE user_key=?", (user_key,))
        stats = dict(c.fetchall())
        c.execute("SELECT quality, count(*) FROM history WHERE user_key=? GROUP BY quality", (user_key,))
        rare_stats = dict(c.fetchall())
        for q, count in rare_stats.items():
            stats[q] = stats.get(q, 0) + count
        total = sum(stats.values())
        c.execute("""
            SELECT name, quality, wear_value, img_url
            FROM history 
            WHERE user_key=? 
            AND (quality IN ('隐秘', '非凡', 'Contraband') OR is_special=1) 
            ORDER BY id DESC LIMIT 10
        """, (user_key,))
        rare_items = []
        for row in c.fetchall():
            rare_items.append({"name": row[0], "quality": row[1], "wear_value": row[2], "img_url": row[3]})
        conn.close()
        return {"total": total, "other_stats": stats, "items": rare_items}

    def clear_user_history(self, user_key):
        conn = self._get_conn()
        c = conn.cursor()
        c.execute("DELETE FROM history WHERE user_key=?", (user_key,))
        c.execute("DELETE FROM user_stats WHERE user_key=?", (user_key,))
        conn.commit()
        conn.close()

# ================= 辅助类：GIF/图片 生成器 =================
class GifGenerator:
    def __init__(self, image_manager):
        self.img_mgr = image_manager
        self.BASE_ITEM_SIZE = 200   
        self.MAX_ITEM_SIZE = 260    
        self.MARGIN = 20            
        self.VIEWPORT_W = 800       
        self.VIEWPORT_H = 350       
        self.TOTAL_ITEMS = 35       
        self.WINNER_INDEX = 28      
        self.HEAD_BUFFER = 8        
        self.FPS = 20               
        self.SCROLL_DURATION = 3.5  
        
        try:
            self.font = ImageFont.truetype("msyh.ttc", 16) 
            self.font_bold = ImageFont.truetype("msyhbd.ttc", 20)
            self.font_title = ImageFont.truetype("msyhbd.ttc", 24)
        except:
            self.font = ImageFont.load_default()
            self.font_bold = self.font
            self.font_title = self.font

    async def generate(self, winner_item, case_items, case_img_url=None):
        filler_pool = [i for i in case_items if i.get("rln") != "非凡"]
        if not filler_pool: filler_pool = case_items

        scroll_items = []
        for _ in range(self.HEAD_BUFFER):
            scroll_items.append(random.choice(filler_pool))
        for _ in range(self.WINNER_INDEX):
            scroll_items.append(random.choice(filler_pool))
        scroll_items.append(winner_item) 
        for _ in range(self.TOTAL_ITEMS - self.WINNER_INDEX - 1):
            scroll_items.append(random.choice(filler_pool))

        img_tasks = [self.img_mgr.get_image(item.get("img")) for item in scroll_items]
        item_images = await asyncio.gather(*img_tasks)

        return await asyncio.to_thread(self._create_optimized_gif, scroll_items, item_images)

    def _create_optimized_gif(self, items_data, images):
        unit_w = self.BASE_ITEM_SIZE + self.MARGIN
        total_width = len(items_data) * unit_w
        
        strip_img = Image.new("RGBA", (total_width, self.VIEWPORT_H), (0,0,0,0))
        strip_draw = ImageDraw.Draw(strip_img)
        
        for idx, (item_data, img) in enumerate(zip(items_data, images)):
            x = idx * unit_w
            q_color = QUALITY_COLORS.get(item_data.get("rln"), (100, 100, 100))
            draw_y = (self.VIEWPORT_H - self.BASE_ITEM_SIZE) // 2 - 20
            bar_h = 6
            strip_draw.rectangle([x, draw_y + self.BASE_ITEM_SIZE, x + self.BASE_ITEM_SIZE, draw_y + self.BASE_ITEM_SIZE + bar_h], fill=q_color)
            if img:
                i_copy = img.copy()
                i_copy.thumbnail((self.BASE_ITEM_SIZE, self.BASE_ITEM_SIZE), Image.Resampling.BICUBIC)
                strip_img.paste(i_copy, (x, draw_y), i_copy)

        frames = []
        scroll_frames = int(self.FPS * self.SCROLL_DURATION)
        outro_frames = 20 
        total_frames = scroll_frames + outro_frames
        
        REAL_WINNER_INDEX = self.WINNER_INDEX + self.HEAD_BUFFER
        winner_center_x = REAL_WINNER_INDEX * unit_w + unit_w / 2
        viewport_center_x = self.VIEWPORT_W / 2
        target_scroll_x = winner_center_x - viewport_center_x
        random_offset = random.uniform(-0.4, 0.4) * self.BASE_ITEM_SIZE
        target_scroll_x += random_offset
        start_scroll_x = (self.HEAD_BUFFER * unit_w) - viewport_center_x

        def ease_out_cubic(t): return 1 - pow(1 - t, 3)

        bg_color = (30, 30, 35, 255)
        
        for f in range(total_frames):
            frame = Image.new("RGBA", (self.VIEWPORT_W, self.VIEWPORT_H), bg_color)
            draw = ImageDraw.Draw(frame)
            
            is_outro = f >= scroll_frames

            if not is_outro:
                t = f / scroll_frames
                current_scroll_x = start_scroll_x + (target_scroll_x - start_scroll_x) * ease_out_cubic(t)
                crop_x = int(current_scroll_x)
                crop_x = max(0, min(crop_x, strip_img.width - self.VIEWPORT_W))
                viewport_slice = strip_img.crop((crop_x, 0, crop_x + self.VIEWPORT_W, self.VIEWPORT_H))
                frame.paste(viewport_slice, (0, 0), viewport_slice)
                
                draw.rectangle([0, 0, 50, self.VIEWPORT_H], fill=(20, 20, 20, 100))
                draw.rectangle([self.VIEWPORT_W-50, 0, self.VIEWPORT_W, self.VIEWPORT_H], fill=(20, 20, 20, 100))
                mid = self.VIEWPORT_W // 2
                draw.line([(mid, 15), (mid, self.VIEWPORT_H-15)], fill=(255, 215, 0, 200), width=3)
                draw.polygon([(mid-8, 15), (mid+8, 15), (mid, 30)], fill=(255, 215, 0, 255))
                draw.polygon([(mid-8, self.VIEWPORT_H-15), (mid+8, self.VIEWPORT_H-15), (mid, self.VIEWPORT_H-30)], fill=(255, 215, 0, 255))

            else:
                outro_progress = (f - scroll_frames) / outro_frames
                scale = 1.0 + 0.3 * outro_progress # 1.0 -> 1.3
                
                item_data = items_data[REAL_WINNER_INDEX]
                img = images[REAL_WINNER_INDEX]
                q_color = QUALITY_COLORS.get(item_data.get("rln"), (100, 100, 100))
                
                draw_w = int(self.BASE_ITEM_SIZE * scale)
                draw_h = int(self.BASE_ITEM_SIZE * scale)
                draw_x = (self.VIEWPORT_W - draw_w) // 2 
                draw_y = (self.VIEWPORT_H - draw_h) // 2 - 20
                
                bar_h = 6 * scale
                draw.rectangle([draw_x, draw_y + draw_h, draw_x + draw_w, draw_y + draw_h + bar_h], fill=q_color)
                
                if img:
                    i_zoom = img.copy()
                    i_zoom.thumbnail((draw_w, draw_h), Image.Resampling.BICUBIC)
                    frame.paste(i_zoom, (int(draw_x), int(draw_y)), i_zoom)
                
                full_name = item_data.get("name", "???")
                short_name = full_name.split("|")[-1].strip()
                try:
                    text_bbox = draw.textbbox((0, 0), short_name, font=self.font_bold)
                    text_w = text_bbox[2] - text_bbox[0]
                except: text_w = 50
                
                text_draw_x = (self.VIEWPORT_W - text_w) // 2
                text_draw_y = draw_y + draw_h + bar_h + 10
                draw.text((text_draw_x, text_draw_y), short_name, fill=q_color, font=self.font_bold)

            frames.append(frame.convert("RGB"))

        if frames:
            last_frame = frames[-1]
            for _ in range(20): frames.append(last_frame)

        output = BytesIO()
        if frames:
            frames[0].save(output, format="GIF", save_all=True, append_images=frames[1:], duration=int(1000/self.FPS), loop=0, optimize=True)
        return output.getvalue()

    async def generate_inventory_card(self, stats_data, item_img_map):
        return await asyncio.to_thread(self._create_inv_card_sync, stats_data, item_img_map)

    def _create_inv_card_sync(self, stats_data, item_img_map):
        width = 650
        header_h = 80
        stats_h = 100
        item_h = 80
        padding = 15
        rare_items = stats_data['items']
        total_items = len(rare_items)
        height = header_h + stats_h + (total_items * (item_h + 5)) + padding * 2
        
        img = Image.new("RGB", (width, height), (30, 30, 35))
        draw = ImageDraw.Draw(img)
        
        draw.text((padding, 20), f"📦 个人库存总览", fill=(255, 215, 0), font=self.font_title)
        draw.text((padding, 55), f"总物品数: {stats_data['total']}", fill=(200, 200, 200), font=self.font)
        
        s_y = header_h
        x_offset = padding
        order = ["非凡", "隐秘", "保密", "受限", "军规级"]
        for q in order:
            count = stats_data['other_stats'].get(q, 0)
            if count > 0:
                color = QUALITY_COLORS.get(q, (200, 200, 200))
                txt = f"{q}: {count}"
                draw.text((x_offset, s_y), txt, fill=color, font=self.font)
                x_offset += 110
        
        list_y = header_h + stats_h
        draw.line([(padding, list_y-10), (width-padding, list_y-10)], fill=(60,60,60), width=1)
        draw.text((padding, list_y-35), "💎 最近稀有掉落", fill=(255, 255, 255), font=self.font)
        
        for item in rare_items:
            bg_rect = [padding, list_y, width-padding, list_y+item_h]
            draw.rectangle(bg_rect, fill=(40, 40, 45), outline=(60, 60, 60))
            q_color = QUALITY_COLORS.get(item['quality'], (150, 150, 150))
            draw.rectangle([padding, list_y, padding+5, list_y+item_h], fill=q_color)
            
            img_url = item.get('img_url')
            if img_url:
                local_path = self.img_mgr._get_file_path(img_url)
                item_img_obj = None
                if os.path.exists(local_path):
                    try: item_img_obj = Image.open(local_path).convert("RGBA")
                    except: pass
                else:
                    item_img_obj = self.img_mgr._download_sync(img_url, local_path)
                
                if item_img_obj:
                    item_img_obj.thumbnail((70, 70), Image.Resampling.LANCZOS)
                    paste_x = padding + 15
                    paste_y = list_y + (item_h - item_img_obj.height) // 2
                    img.paste(item_img_obj, (paste_x, paste_y), item_img_obj)
            else:
                draw.rectangle([padding+15, list_y+5, padding+15+70, list_y+75], outline=(100,100,100))
                draw.text((padding+35, list_y+25), "?", fill=(100,100,100), font=self.font_bold)

            text_x = padding + 100 
            draw.text((text_x, list_y + 15), item['name'], fill=q_color, font=self.font)
            wear_val = item['wear_value']
            wear_str = get_wear_name(wear_val)
            draw.text((text_x, list_y + 45), f"磨损: {wear_str} ({wear_val:.5f})", fill=(150, 150, 150), font=self.font)
            list_y += item_h + 5

        output = BytesIO()
        img.save(output, format="PNG")
        return output.getvalue()

    #  生成菜单图片
    def generate_help_card(self):
        width = 600
        height = 480
        img = Image.new("RGB", (width, height), (30, 30, 35))
        draw = ImageDraw.Draw(img)
        
        # 标题
        draw.text((20, 20), "🔫 CS2 开箱模拟", fill=(255, 215, 0), font=self.font_title)
        draw.text((20, 60), "v1.3", fill=(150, 150, 150), font=self.font)
        
        # 分割线
        draw.line([(20, 90), (width-20, 90)], fill=(60, 60, 60), width=2)
        
        # 指令列表
        commands = [
            ("📦 开箱 [数量] [名称]", "开启指定数量的武器箱/纪念包 (如: 开箱 10 命悬)"),
            ("🎒 库存", "查看当前的饰品库存统计 (生成图片)"),
            ("💰 查询价格 [名称]", "查询饰品BUFF/Steam参考价格"),
            ("📜 武器箱列表", "查看所有可开启的容器名称"),
            ("🗑️ 清除库存", "清空自己的所有开箱记录 (不可恢复)"),
            ("🔄 更新武器箱", "(管理员) 从服务器同步最新数据"),
            ("🧹 清除缓存", "(管理员) 清理本地临时图片文件")
        ]
        
        y = 110
        for cmd, desc in commands:
            # 指令名 (高亮)
            draw.text((30, y), cmd, fill=(255, 255, 255), font=self.font_bold)
            # 描述 (灰色)
            draw.text((30, y+30), desc, fill=(180, 180, 180), font=self.font)
            y += 70 # 行间距

        output = BytesIO()
        img.save(output, format="PNG")
        return output.getvalue()

@register("CS武器箱开箱模拟", "luooka", "支持武器箱、纪念包、收藏品开箱模拟(带动画)", "1.3")
class CasePlugin(Star):
    def __init__(self, context: Context, config: dict):
        super().__init__(context)
        self.config = config
        
        self.api_host = self.config.get('api_host', 'api.csqaq.com').replace("https://", "").replace("http://", "").strip("/")
        self.api_token = self.config.get('api_token', 'GWBR21M7K474Z3R5Y5H8K9J6')
        
        self.net_mgr = NetworkManager(self.api_token) 
        self.img_mgr = ImageManager()
        self.gif_gen = GifGenerator(self.img_mgr)
        self.db = DatabaseManager() 
        
        self.db.migrate_cases() 
        self.case_data, self.case_images, self.item_img_map = self.db.load_all_data()
        
        if os.path.exists(HISTORY_FILE):
            self.db.migrate_json_history(self.item_img_map)
            
        self._recalculate_probabilities(self.case_data)
        
        raw_admins = self.config.get("admins", "510591108")
        if isinstance(raw_admins, list):
            self.admins = [str(x) for x in raw_admins]
        else:
            self.admins = [x.strip() for x in str(raw_admins).replace("，", ",").split(",") if x.strip()]
            
        print(f"插件加载完成 (v4.4 Release)。Config: Number={self.config.get('number', 10)}, Admins={self.admins}")

    def _identify_container_type(self, case_name):
        if "纪念包" in case_name: return "souvenir"
        elif "收藏品" in case_name: return "collection"
        elif any(k in case_name for k in ["胶囊", "涂鸦", "布章"]): return "capsule"
        return "case"

    def _get_probability_map(self, items, case_name=""):
        if case_name.endswith("终端机"): return PROB_CATEGORY_15
        qualities = set(i["rln"] for i in items if i.get("rln"))
        if "军规级" in qualities and "消费级" not in qualities and "工业级" not in qualities:
             return PROB_CATEGORY_1
        if "消费级" in qualities:
            if "隐秘" in qualities: return PROB_CATEGORY_6
            if "保密" in qualities: return PROB_CATEGORY_5
            if "受限" in qualities: return PROB_CATEGORY_4
            return PROB_CATEGORY_2
        if "工业级" in qualities and "消费级" not in qualities:
            return PROB_CATEGORY_3
        return PROB_CATEGORY_1

    def _recalculate_probabilities(self, data):
        for case_name, items in data.items():
            prob_table = self._get_probability_map(items, case_name)
            quality_counts = {}
            for item in items:
                q = item["rln"]
                if q in prob_table: quality_counts[q] = quality_counts.get(q, 0) + 1
            for item in items:
                q = item["rln"]
                if q in prob_table and quality_counts.get(q, 0) > 0:
                    item["probability"] = prob_table[q] / quality_counts[q]
                else: item["probability"] = 0

    def _generate_item(self, case_name):
        items = self.case_data.get(case_name, [])
        valid_items = [i for i in items if i.get("probability", 0) > 0]
        if not valid_items: return {"name": "错误", "quality": "军规级", "wear_value": 0, "wear_level": "无", "img": "", "rln": "军规级", "short_name": "错误"}

        ctype = self._identify_container_type(case_name)
        rand = random.random()
        cumulative = 0.0
        selected_item = valid_items[-1]
        
        for item in valid_items:
            cumulative += item["probability"]
            if rand <= cumulative:
                selected_item = item
                break
        
        raw_name = selected_item["short_name"]
        item_name = raw_name
        quality = selected_item["rln"]
        img = selected_item.get("img", "")
        
        if ctype == "souvenir": item_name = f"纪念品 | {item_name}"
        elif ctype == "case":
            if "手套" not in item_name and random.random() < 0.1:
                item_name = f"StatTrak™ | {item_name}"

        is_doppler = "多普勒" in item_name
        if is_doppler:
            is_gamma = "伽玛" in item_name
            type_pool = GAMMA_DOPPLER_PROBS if is_gamma else NORMAL_DOPPLER_PROBS
            chosen_type = random.choices(list(type_pool.keys()), weights=list(type_pool.values()), k=1)[0]
            item_name = item_name.replace("多普勒", f"多普勒 ({chosen_type})")

        wear_config = DOPPLER_WEAR_LEVELS if is_doppler else WEAR_LEVELS
        chosen_level = random.choices(wear_config, weights=[wl[1] for wl in wear_config], k=1)[0]
        wear_val = round(random.uniform(chosen_level[2], chosen_level[3]), 8)

        is_rare = quality in ["隐秘", "非凡", "Contraband"]

        return {
            "name": item_name,
            "raw_name": raw_name,
            "quality": quality,
            "wear_value": wear_val,
            "wear_level": chosen_level[0],
            "template_id": random.randint(0, 999),
            "img": img,
            "is_special": is_rare,
            "rln": quality
        }

    def _parse_command(self, msg: str) -> tuple:
        clean_msg = msg.replace("开箱", "", 1).strip()
        if not clean_msg: return None, 1
        parts = clean_msg.split(maxsplit=1)
        if len(parts) > 1 and parts[0].isdigit(): return parts[1], min(int(parts[0]), 200)
        if len(parts) > 1 and parts[1].isdigit(): return parts[0], min(int(parts[1]), 200)
        match = re.search(r'(\d+)$', clean_msg)
        if match:
            num_str = match.group(1)
            return clean_msg[:-len(num_str)].strip(), min(int(num_str), 200)
        return clean_msg, 1

    @event_message_type(EventMessageType.GROUP_MESSAGE)
    async def on_group_message(self, event: AstrMessageEvent):
        msg = event.message_str.strip()
        if msg == "清除库存":
            async for r in self._handle_purge(event): yield r
        elif msg == "清除缓存":
            sender_id = str(event.get_sender_id())
            if sender_id in self.admins:
                async for r in self._handle_clear_cache(event): yield r
            else:
                yield event.plain_result("❌ 权限不足")
        elif msg == "更新武器箱":
            sender_id = str(event.get_sender_id())
            if sender_id in self.admins:
                async for r in self._handle_update_cases(event): yield r
            else:
                yield event.plain_result(f"❌ 权限不足：仅管理员可更新数据。")
        elif msg == "开箱菜单":
            # [v4.4] 发送菜单图片
            img_bytes = self.gif_gen.generate_help_card()
            yield event.chain_result([Comp.Image.fromBytes(img_bytes)])
        elif msg == "武器箱列表":
            async for r in self._handle_show_list(event): yield r
        elif msg == "库存":
            async for r in self._show_inventory(event): yield r
        elif msg.startswith("开箱"):
            async for r in self._handle_open(event): yield r
        elif msg.startswith("查询价格"):
            async for r in self._handle_price_query(event): yield r

    async def _handle_clear_cache(self, event):
        try:
            count = 0
            if os.path.exists(IMAGES_DIR):
                count = len(os.listdir(IMAGES_DIR))
                shutil.rmtree(IMAGES_DIR) 
            os.makedirs(IMAGES_DIR, exist_ok=True) 
            yield event.plain_result(f"✅ 缓存已清除！释放了 {count} 个文件。\n下次开箱将会重新下载图片。")
        except Exception as e:
            yield event.plain_result(f"❌ 清除失败: {e}")

    async def _handle_update_cases(self, event: AstrMessageEvent):
        yield event.plain_result("⏳ 开始同步数据 (限制频率 1.5s/次)...")
        url = f"https://{self.api_host}/api/v1/info/container_data_info"
        try:
            list_resp = self.net_mgr.request(url, method="POST")
        except Exception as e:
            yield event.plain_result(f"❌ 列表请求异常: {e}")
            return

        if not list_resp or list_resp.get("code") != 200:
            yield event.plain_result(f"❌ 获取列表失败: {list_resp}")
            return
        
        containers = list_resp.get("data", [])
        total = len(containers)
        new_cases = {}
        new_imgs = {}
        success = 0
        
        try:
            for idx, c in enumerate(containers):
                name = c['name']
                if any(k in name for k in ["胶囊", "涂鸦", "布章"]): continue
                if name.endswith("挂件") or name.endswith("印花"): continue
                
                if c.get("img"): new_imgs[name] = c['img']
                detail_url = f"https://{self.api_host}/api/v1/info/good/container_detail?id={c['id']}"
                detail = self.net_mgr.request(detail_url)

                if detail and detail.get("code") == 200:
                    raw = detail.get("data", [])
                    cleaned = []
                    seen = set()
                    for item in raw:
                        rln = item.get("rln")
                        s_name = item.get("short_name")
                        if rln not in ALL_QUALITIES: continue
                        if s_name in seen: continue
                        if "（★）" in s_name: rln = "非凡"
                        seen.add(s_name)
                        cleaned.append({"short_name": s_name, "rln": rln, "img": item.get("img")})
                    if cleaned:
                        new_cases[name] = cleaned
                        success += 1
                
                if idx % 10 == 0: print(f"同步: {idx}/{total}")
                await asyncio.sleep(1.5)
                
            if self.db.save_all_data(new_cases, new_imgs):
                self.case_data, self.case_images, self.item_img_map = self.db.load_all_data()
                self._recalculate_probabilities(self.case_data)
                yield event.plain_result(f"✅ 更新完毕！收录 {success} 个容器。")
            else:
                yield event.plain_result("❌ 数据库写入失败")
        except Exception as e:
            import traceback
            traceback.print_exc()
            yield event.plain_result(f"❌ 中断: {e}")

    async def _handle_show_list(self, event):
        if not self.case_data:
            yield event.plain_result("❌ 无数据，请先更新")
            return
        cases, souvenirs, collections = [], [], []
        for n in sorted(self.case_data.keys()):
            t = self._identify_container_type(n)
            if t == "souvenir": souvenirs.append(n)
            elif t == "collection": collections.append(n)
            else: cases.append(n)
        def fmt(items):
            lines = []
            for i in range(0, len(items), 2): lines.append(" | ".join(items[i:i+2]))
            return "\n".join(lines) if items else "(无)"
        yield event.plain_result(f"📦 武器箱 ({len(cases)}):\n{fmt(cases)}\n\n🎁 纪念包 ({len(souvenirs)}):\n{fmt(souvenirs)}\n\n🖼️ 收藏品 ({len(collections)}):\n{fmt(collections)}")

    async def _handle_open(self, event: AstrMessageEvent):
        msg = event.message_str.strip()
        case_name, count = self._parse_command(msg)
        if not case_name:
            yield event.plain_result("❌ 请输入名称")
            return
        
        target_case = None
        if case_name in self.case_data: target_case = case_name
        else:
            for name in self.case_data.keys():
                if case_name in name:
                    target_case = name
                    break
        if not target_case:
            yield event.plain_result(f"❌ 未找到【{case_name}】")
            return

        user_id = str(event.get_sender_id())
        group_id = str(event.message_obj.group_id)
        user_key = f"{group_id}-{user_id}"
        
        items_res = []
        for _ in range(count):
            item = self._generate_item(target_case)
            items_res.append(item)
            self.db.add_item(user_key, item)

        user_stats = self.db.get_user_stats(user_key)
        total_count = user_stats['total']

        if count == 1:
            winner = items_res[0]
            chain = [Comp.At(qq=user_id)]
            chain.append(Comp.Plain(f" 【{target_case}】开启结果\n"))
            
            case_img_url = self.case_images.get(target_case)
            if case_img_url:
                try:
                    img_obj = await self.img_mgr.get_image(case_img_url)
                    if img_obj:
                        base_width = 180 
                        w_percent = (base_width / float(img_obj.size[0]))
                        h_size = int((float(img_obj.size[1]) * float(w_percent)))
                        img_small = img_obj.resize((base_width, h_size), Image.Resampling.LANCZOS)
                        
                        temp_cover_path = os.path.join(IMAGES_DIR, f"cover_{user_id}.png")
                        img_small.save(temp_cover_path)
                        chain.append(Comp.Image.fromFileSystem(temp_cover_path))
                except Exception as e:
                    print(f"封面图处理失败: {e}")

            try:
                all_possible_items = self.case_data[target_case]
                gif_bytes = await self.gif_gen.generate(winner, all_possible_items)
                
                temp_gif_path = os.path.join(IMAGES_DIR, f"temp_{user_id}.gif")
                with open(temp_gif_path, "wb") as f: f.write(gif_bytes)
                chain.append(Comp.Image.fromFileSystem(temp_gif_path))
            except Exception as e:
                print(f"GIF生成失败: {e}")
                if winner.get("img"): chain.append(Comp.Image.fromURL(winner["img"]))

            ctype = self._identify_container_type(target_case)
            info = f"\n🎁 {winner['name']} ({winner['quality']})\n"
            if ctype != "capsule": info += f"🔧 {winner['wear_level']} ({winner['wear_value']:.5f})"
            chain.append(Comp.Plain(info))
            
            chain.append(Comp.Plain(f"\n📦 总库存: {total_count}"))
            yield event.chain_result(chain)
        else:
            chain = [Comp.At(qq=user_id)]
            
            best_item = None
            best_score = -1
            score_map = {"非凡": 10, "Contraband": 9, "隐秘": 8} 
            
            for item in items_res:
                score = score_map.get(item['quality'], 0)
                if score > best_score:
                    best_score = score
                    best_item = item
            
            if best_item and best_score > 0:
                chain.append(Comp.Plain(f" ✨ 欧气爆发！开出了稀有物品！\n"))
                try:
                    all_possible_items = self.case_data[target_case]
                    gif_bytes = await self.gif_gen.generate(best_item, all_possible_items)
                    temp_gif_path = os.path.join(IMAGES_DIR, f"temp_rare_{user_id}.gif")
                    with open(temp_gif_path, "wb") as f: f.write(gif_bytes)
                    chain.append(Comp.Image.fromFileSystem(temp_gif_path))
                except: pass

            chain.append(Comp.Plain(f" ⚡ 开启【{target_case}】x{count}\n"))
            if count <= 10:
                for item in items_res:
                    if item.get("img"): chain.append(Comp.Image.fromURL(item["img"]))
                    info = f"🎁 {item['name']} ({item['quality']})\n"
                    ctype = self._identify_container_type(target_case)
                    if ctype != "capsule": info += f"🔧 {item['wear_level']} ({item['wear_value']:.5f})\n"
                    chain.append(Comp.Plain(info))
            else:
                stats = {}
                rare = []
                for item in items_res:
                    stats[item['quality']] = stats.get(item['quality'], 0) + 1
                    if item.get("is_special") or item['quality'] in ["隐秘", "非凡", "Contraband"]: 
                        rare.append(item)
                
                chain.append(Comp.Plain("\n📊 统计结果：\n"))
                for q, c in stats.items(): chain.append(Comp.Plain(f"· {q}: {c}个\n"))
                
                if rare:
                    chain.append(Comp.Plain("\n💎 稀有掉落：\n"))
                    for item in rare:
                        if item.get("img"): chain.append(Comp.Image.fromURL(item["img"]))
                        chain.append(Comp.Plain(f"▫ {item['name']}\n"))
                        ctype = self._identify_container_type(target_case)
                        if ctype != "capsule":
                            chain.append(Comp.Plain(f"   🔧 {item['wear_level']} ({item['wear_value']:.5f})\n"))
            chain.append(Comp.Plain(f"\n📦 总库存: {total_count}"))
            yield event.chain_result(chain)

    async def _handle_purge(self, event):
        uid = f"{event.message_obj.group_id}-{event.get_sender_id()}"
        self.db.clear_user_history(uid)
        yield event.plain_result("✅ 库存已清空")

    async def _show_inventory(self, event):
        uid = f"{event.message_obj.group_id}-{event.get_sender_id()}"
        inv = self.db.get_user_stats(uid)
        
        if inv['total'] == 0: 
            yield event.plain_result("📭 空空如也")
            return
            
        try:
            img_bytes = await self.gif_gen.generate_inventory_card(inv, self.item_img_map)
            temp_path = os.path.join(IMAGES_DIR, f"inv_{uid}.png")
            with open(temp_path, "wb") as f: f.write(img_bytes)
            yield event.chain_result([Comp.At(qq=event.get_sender_id()), Comp.Image.fromFileSystem(temp_path)])
        except Exception as e:
            print(f"库存图片生成失败: {e}")
            import traceback
            traceback.print_exc()
            msg = [f"📦 总数: {inv['total']}", "---"]
            for k,v in inv['other_stats'].items(): msg.append(f"{k}: {v}")
            if inv['items']:
                msg.append("\n💎 最近稀有:")
                for item in inv['items']: msg.append(f"* {item['name']}")
            yield event.plain_result("\n".join(msg))

    async def _show_menu(self, event):
        # 菜单图片
        img_bytes = self.gif_gen.generate_help_card()
        yield event.chain_result([Comp.Image.fromBytes(img_bytes)])

    def _http_request(self, path, method="GET"):
        return self.net_mgr.request(f"https://{self.api_host}{path}", method=method)
    
    def search_items(self, keyword):
        d = self._http_request(f"/api/v1/search/suggest?text={quote(keyword)}")
        return d.get('data', []) if d and d.get('code')==200 else None

    def get_goods_info(self, gid):
        time.sleep(1.5)
        d = self._http_request(f"/api/v1/info/good?id={gid}")
        if not d or d.get('code')!=200: return None
        g = d['data']['goods_info']
        return {
            "名称": g['name'], 
            "BUFF": g['buff_sell_price'], 
            "YYYP": g.get('yyyp_sell_price', '无'),
            "Steam": g['steam_sell_price'], 
            "img": g['img'], 
            "更新": g['updated_at']
        }

    def get_price(self, name):
        items = self.search_items(name)
        if not items: return "❌ 未找到"
        info = self.get_goods_info(items[0]['id'])
        if not info: return "❌ 详情获取失败"
        return f"{info['img']}\n{info['名称']}\nBUFF: {info['BUFF']} | YYYP: {info['YYYP']}\nSteam: {info['Steam']}"

    async def _handle_price_query(self, event):
        name = event.message_str.replace("查询价格","").strip()
        res = self.get_price(name)
        if "http" in res:
            p = res.split('\n',1)
            yield event.chain_result([Comp.At(qq=event.get_sender_id()), Comp.Image.fromURL(p[0]), Comp.Plain("\n"+p[1])])
        else: yield event.plain_result(res)