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
from io import BytesIO
import astrbot.api.message_components as Comp
from urllib.parse import quote
from datetime import datetime
from astrbot.api.all import *

# === 引入图像处理库 ===
try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    raise ImportError("请先安装 Pillow 库: pip install Pillow")

PLUGIN_DIR = os.path.join('data', 'plugins', 'astrbot_plugin_openweaponscase', 'data')
CASES_FILE = os.path.join(PLUGIN_DIR, 'cases.json')
IMAGES_MAP_FILE = os.path.join(PLUGIN_DIR, 'case_images.json')
HISTORY_FILE = os.path.join(PLUGIN_DIR, 'open_history.json')
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

DOPPLER_WEAR_LEVELS = [
    ("崭新出厂", 0.03, 0.00, 0.87),
    ("略有磨损", 0.24, 0.07, 0.12),
]

CASE_PROBABILITY = {
    "军规级": 0.7992, "受限": 0.1598, "保密": 0.032, "隐秘": 0.0064, "非凡": 0.0026
}

MAP_DROP_PROBABILITY = {
    "消费级": 0.80, "工业级": 0.16, "军规级": 0.032, "受限": 0.0064, "保密": 0.0016, "隐秘": 0.0004
}

NORMAL_DOPPLER_PROBS = {"p1": 0.2, "p2": 0.2, "p3": 0.2, "p4": 0.2, "蓝宝石": 0.1, "红宝石": 0.05, "黑珍珠": 0.05}
GAMMA_DOPPLER_PROBS = {"p1": 0.2, "p2": 0.2, "p3": 0.2, "p4": 0.2, "绿宝石": 0.2}

ALL_QUALITIES = set(list(CASE_PROBABILITY.keys()) + list(MAP_DROP_PROBABILITY.keys()))

# ================= 辅助类：网络请求管理 =================
class NetworkManager:
    def __init__(self, api_token):
        self.ssl_context = ssl._create_unverified_context()
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Referer': 'https://buff.163.com/',
            'Content-Type': 'application/json',
            'ApiToken': api_token
        }

    def request(self, url, method="GET", data=None):
        try:
            if data: data = data.encode('utf-8')
            if not url.startswith("http"): url = "https://" + url

            req = urllib.request.Request(url, data=data, headers=self.headers, method=method)
            
            with urllib.request.urlopen(req, context=self.ssl_context, timeout=15) as response:
                return json.loads(response.read().decode('utf-8'))
        except Exception as e:
            print(f"⚠️ 网络请求失败 [{url}]: {e}")
            raise e

# ================= 辅助类：图片管理 =================
class ImageManager:
    def __init__(self):
        os.makedirs(IMAGES_DIR, exist_ok=True)
        self.ssl_context = ssl._create_unverified_context()

    def _get_file_path(self, url):
        hash_name = hashlib.md5(url.encode()).hexdigest()
        return os.path.join(IMAGES_DIR, f"{hash_name}.png")

    async def get_image(self, url):
        if not url: return None
        file_path = self._get_file_path(url)
        
        if os.path.exists(file_path):
            try:
                if os.path.getsize(file_path) > 100:
                    return Image.open(file_path).convert("RGBA")
            except: pass

        try:
            return await asyncio.to_thread(self._download_sync, url, file_path)
        except Exception as e:
            return None

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

# ================= 辅助类：GIF 生成器 =================
class GifGenerator:
    def __init__(self, image_manager):
        self.img_mgr = image_manager
        self.ITEM_SIZE = 120      
        self.MARGIN = 10          
        self.VIEWPORT_W = 600     
        self.VIEWPORT_H = 160     
        self.TOTAL_ITEMS = 35     
        self.WINNER_INDEX = 28    
        
    async def generate(self, winner_item, case_items):
        filler_pool = [i for i in case_items if i.get("rln") != "非凡"]
        if not filler_pool: filler_pool = case_items

        scroll_items = []
        for _ in range(self.WINNER_INDEX):
            scroll_items.append(random.choice(filler_pool))
        scroll_items.append(winner_item) 
        for _ in range(self.TOTAL_ITEMS - self.WINNER_INDEX - 1):
            scroll_items.append(random.choice(filler_pool))

        tasks = [self.img_mgr.get_image(item.get("img")) for item in scroll_items]
        images = await asyncio.gather(*tasks)
        return await asyncio.to_thread(self._create_gif_frames, scroll_items, images)

    def _create_gif_frames(self, items_data, images):
        text_height = 20
        total_h = self.VIEWPORT_H + text_height
        unit_w = self.ITEM_SIZE + self.MARGIN
        strip_w = unit_w * len(items_data)
        
        strip_img = Image.new("RGBA", (strip_w, total_h), (30, 30, 35))
        draw_strip = ImageDraw.Draw(strip_img)
        
        try:
            font = ImageFont.truetype("msyh.ttc", 10) 
        except:
            font = ImageFont.load_default()

        for i, (item_data, img) in enumerate(zip(items_data, images)):
            x = i * unit_w
            q_color = QUALITY_COLORS.get(item_data.get("rln"), (100, 100, 100))
            draw_strip.rectangle([x, self.VIEWPORT_H - 4, x + self.ITEM_SIZE, self.VIEWPORT_H], fill=q_color)
            
            if img:
                img_copy = img.copy()
                img_copy.thumbnail((self.ITEM_SIZE, self.ITEM_SIZE), Image.Resampling.BILINEAR)
                w, h = img_copy.size
                x_offset = x + (self.ITEM_SIZE - w) // 2
                y_offset = (self.VIEWPORT_H - h) // 2 - 5
                strip_img.paste(img_copy, (x_offset, y_offset), img_copy)
            
            full_name = item_data.get("name", "???")
            short_name = full_name.split("|")[-1].strip()
            
            try:
                text_bbox = draw_strip.textbbox((0, 0), short_name, font=font)
                text_w = text_bbox[2] - text_bbox[0]
            except:
                text_w = self.ITEM_SIZE
                
            text_x = x + (self.ITEM_SIZE - text_w) // 2
            draw_strip.text((text_x, self.VIEWPORT_H + 2), short_name, fill=q_color, font=font)

        random_offset = random.uniform(-0.35, 0.35) * self.ITEM_SIZE
        target_x = (self.WINNER_INDEX * unit_w + self.ITEM_SIZE/2) - (self.VIEWPORT_W / 2) + random_offset
        
        frames = []
        fps = 25
        duration_sec = 5.5
        total_frames = int(fps * duration_sec)
        frame_h = total_h
        
        for f in range(total_frames):
            t = f / total_frames
            ease_t = 1 - pow(1 - t, 4)
            current_x = int(target_x * ease_t)
            crop_x = max(0, current_x)
            crop_w = min(strip_w - crop_x, self.VIEWPORT_W)
            
            frame = Image.new("RGB", (self.VIEWPORT_W, frame_h), (25, 25, 25))
            if crop_w > 0:
                segment = strip_img.crop((crop_x, 0, crop_x + crop_w, frame_h))
                frame.paste(segment, (0, 0))
            
            draw = ImageDraw.Draw(frame)
            mid = self.VIEWPORT_W // 2
            draw.line([(mid+1, 0), (mid+1, frame_h)], fill=(0, 0, 0), width=1)
            draw.line([(mid, 0), (mid, frame_h)], fill=(255, 215, 0), width=2)
            draw.rectangle([0, 0, 20, frame_h], fill=None, outline=None) 
            frames.append(frame)

        output = BytesIO()
        if frames:
            frames[-1].info['duration'] = 2500
            frames[0].save(output, format="GIF", save_all=True, append_images=frames[1:], duration=int(1000/fps), loop=0, optimize=False)
        return output.getvalue()

@register("CS武器箱开箱模拟", "luooka", "支持武器箱、纪念包、收藏品开箱模拟(带动画)", "1.2")
class CasePlugin(Star):
    def __init__(self, context: Context, config: dict):
        super().__init__(context)
        self.config = config
        
        self.api_host = self.config.get('api_host', 'api.csqaq.com').replace("https://", "").replace("http://", "").strip("/")
        self.api_token = self.config.get('api_token')
        
        self.net_mgr = NetworkManager(self.api_token) 
        self.img_mgr = ImageManager()
        self.gif_gen = GifGenerator(self.img_mgr)
        
        self.case_data = self._load_cases()
        self.case_images = self._load_case_images()
        self.open_history = self._load_history()
        
        # === 核心修改：解析管理员配置 (String -> List) ===
        raw_admins = self.config.get("admins")
        if isinstance(raw_admins, list):
            # 兼容旧配置
            self.admins = [str(x) for x in raw_admins]
        else:
            # 处理字符串：去除空格，替换中文逗号，分割
            self.admins = [x.strip() for x in str(raw_admins).replace("，", ",").split(",") if x.strip()]
            
        print(f"插件加载完成。Config: Number={self.config.get('number', 10)}, Admins={self.admins}")

    def _load_cases(self):
        try:
            os.makedirs(PLUGIN_DIR, exist_ok=True)
            if not os.path.exists(CASES_FILE):
                with open(CASES_FILE, 'w', encoding='utf-8') as f: json.dump({}, f)
            with open(CASES_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                self._recalculate_probabilities(data)
                return data
        except: return {}

    def _load_case_images(self):
        try:
            if not os.path.exists(IMAGES_MAP_FILE): return {}
            with open(IMAGES_MAP_FILE, 'r', encoding='utf-8') as f: return json.load(f)
        except: return {}

    def _identify_container_type(self, case_name):
        if "纪念包" in case_name: return "souvenir"
        elif "收藏品" in case_name: return "collection"
        elif any(k in case_name for k in ["胶囊", "涂鸦", "布章"]): return "capsule"
        return "case"

    def _get_prob_table(self, container_type):
        if container_type == "case": return CASE_PROBABILITY
        return MAP_DROP_PROBABILITY

    def _recalculate_probabilities(self, data):
        for case_name, items in data.items():
            ctype = self._identify_container_type(case_name)
            prob_table = self._get_prob_table(ctype)
            quality_counts = {}
            for item in items:
                q = item["rln"]
                if q in prob_table: quality_counts[q] = quality_counts.get(q, 0) + 1
            for item in items:
                q = item["rln"]
                if q in prob_table:
                    item["probability"] = prob_table[q] / quality_counts.get(q, 1)
                else: item["probability"] = 0

    def _load_history(self):
        if not os.path.exists(HISTORY_FILE): return {}
        with open(HISTORY_FILE, 'r', encoding='utf-8') as f: return json.load(f)

    def _save_history(self):
        with open(HISTORY_FILE, 'w', encoding='utf-8') as f:
            json.dump(self.open_history, f, indent=2, ensure_ascii=False)

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

    def _record_history(self, group_id, user_id, item):
        history_key = f"{group_id}-{user_id}"
        self.open_history.setdefault(history_key, {"total": 0, "red_count": 0, "gold_count": 0, "other_stats": {}, "items": [], "last_open": None})
        record = self.open_history[history_key]
        record["total"] += 1
        record["last_open"] = time.time()
        q = item["quality"]
        if item.get("is_special", False) or q in ["隐秘", "非凡"]:
            if q == "非凡": record["gold_count"] += 1
            elif q == "隐秘": record["red_count"] += 1
            record["items"].append({"name": item["name"], "wear_value": item["wear_value"], "time": datetime.now().isoformat()})
        else:
            record["other_stats"][q] = record["other_stats"].get(q, 0) + 1
        self._save_history()

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
        elif msg == "更新武器箱":
            sender_id = str(event.get_sender_id())
            # === 权限检查：比对字符串 ===
            if sender_id in self.admins:
                async for r in self._handle_update_cases(event): yield r
            else:
                yield event.plain_result(f"❌ 权限不足：仅管理员可更新数据。")
        elif msg == "开箱菜单":
            async for r in self._show_menu(event): yield r
        elif msg == "武器箱列表":
            async for r in self._handle_show_list(event): yield r
        elif msg == "库存":
            async for r in self._show_inventory(event): yield r
        elif msg.startswith("开箱"):
            async for r in self._handle_open(event): yield r
        elif msg.startswith("查询价格"):
            async for r in self._handle_price_query(event): yield r
        elif msg.startswith("挂刀排行"):
            async for r in self._handle_market_ratio(event): yield r

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
        new_data = {}
        new_images_map = {}
        success = 0
        
        try:
            for idx, c in enumerate(containers):
                if any(k in c['name'] for k in ["胶囊", "涂鸦", "布章"]): continue
                
                if c.get("img"): new_images_map[c['name']] = c['img']

                detail_url = f"https://{self.api_host}/api/v1/info/good/container_detail?id={c['id']}"
                detail = self.net_mgr.request(detail_url)

                if detail and detail.get("code") == 200:
                    raw = detail.get("data", [])
                    cleaned = []
                    seen = set()
                    for item in raw:
                        rln = item.get("rln")
                        name = item.get("short_name")
                        if rln not in ALL_QUALITIES: continue
                        if name in seen: continue
                        if "（★）" in name: rln = "非凡"
                        seen.add(name)
                        cleaned.append({"short_name": name, "rln": rln, "img": item.get("img")})
                    if cleaned:
                        new_data[c['name']] = cleaned
                        success += 1
                
                if idx % 10 == 0: print(f"同步: {idx}/{total}")
                await asyncio.sleep(1.5)
        except Exception as e:
            import traceback
            traceback.print_exc()
            yield event.plain_result(f"❌ 中断: {e}")
            return
        
        os.makedirs(os.path.dirname(CASES_FILE), exist_ok=True)
        with open(CASES_FILE, 'w', encoding='utf-8') as f: json.dump(new_data, f, ensure_ascii=False, indent=2)
        with open(IMAGES_MAP_FILE, 'w', encoding='utf-8') as f: json.dump(new_images_map, f, ensure_ascii=False, indent=2)
        
        self.case_data = self._load_cases()
        self.case_images = self._load_case_images()
        
        yield event.plain_result(f"✅ 更新完毕！收录 {success} 个容器。")

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
        
        items_res = []
        for _ in range(count):
            item = self._generate_item(target_case)
            items_res.append(item)
            self._record_history(group_id, user_id, item)

        chain = [Comp.At(qq=user_id)]
        
        if count == 1:
            winner = items_res[0]
            case_img_url = self.case_images.get(target_case)
            if case_img_url:
                chain.append(Comp.Image.fromURL(case_img_url))
            
            chain.append(Comp.Plain(f"\n🎲 正在开启【{target_case}】，请稍候..."))
            yield event.chain_result(chain)
            
            chain = [Comp.At(qq=user_id)]
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
        else:
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
                    if item.get("is_special"): rare.append(item)
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

        chain.append(Comp.Plain(f"\n📦 总库存: {self.open_history[f'{group_id}-{user_id}']['total']}"))
        yield event.chain_result(chain)

    async def _handle_purge(self, event):
        uid = f"{event.message_obj.group_id}-{event.get_sender_id()}"
        if uid in self.open_history:
            del self.open_history[uid]
            self._save_history()
            yield event.plain_result("✅ 库存已清空")
        else: yield event.plain_result("❌ 无库存")

    async def _show_inventory(self, event):
        uid = f"{event.message_obj.group_id}-{event.get_sender_id()}"
        inv = self.open_history.get(uid)
        if not inv: 
            yield event.plain_result("📭 空空如也")
            return
        msg = [f"📦 总数: {inv['total']}", "---"]
        for k,v in inv['other_stats'].items(): msg.append(f"{k}: {v}")
        if inv['items']:
            msg.append("\n💎 稀有物品:")
            for i in inv['items'][-10:]: msg.append(f"▫ {i['name']}")
        yield event.plain_result("\n".join(msg))

    async def _show_menu(self, event):
        yield event.plain_result("🔫 CS开箱模拟 2.8\n▬▬▬▬▬▬▬▬\n开箱 [数量] [名称]\n更新武器箱 | 武器箱列表\n库存 | 清除库存\n查询价格 [名] | 挂刀排行")

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

    async def _handle_market_ratio(self, event):
        try:
            payload = json.dumps({"page_index":1,"res":0,"platforms":"BUFF-YYYP","sort_by":1,"min_price":1,"max_price":5000,"turnover":10})
            data = self.net_mgr.request(f"https://{self.api_host}/api/v1/info/exchange_detail", method="POST", data=payload)
            if not data or data.get('code')!=200: 
                yield event.plain_result("❌ 失败")
                return
            res = "⚡ 挂刀排行:\n"
            for i, item in enumerate(data['data'][:10], 1):
                rate = item['yyyp_sell_price']/item['steam_buy_price'] if item['steam_buy_price'] else 0
                res += f"{i}. {item['market_hash_name']}\n   比率: {rate:.2f}\n"
            yield event.plain_result(res)
        except Exception as e:
            yield event.plain_result(f"❌ 错误: {e}")
