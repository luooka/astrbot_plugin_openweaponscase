import random
import json
import os
import json
import os
import time
import astrbot.api.message_components as Comp
from datetime import datetime
from astrbot.api.all import *

PLUGIN_DIR = os.path.join('data','plugins','astrbot_plugin_openweaponscase','data')
CASES_FILE = os.path.join(PLUGIN_DIR, 'cases.json')
HISTORY_FILE = os.path.join(PLUGIN_DIR, 'open_history.json')
# 修改后的磨损等级配置（名称, 概率, 最小磨损值, 最大磨损值）
WEAR_LEVELS = [
    ("崭新出厂", 0.03, 0.00, 0.07),    # 3% 概率
    ("略有磨损", 0.24, 0.07, 0.15),   # 24% 概率
    ("久经沙场", 0.33, 0.15, 0.45),   # 33% 概率
    ("破损不堪", 0.24, 0.30, 0.45),   # 24% 概率
    ("战痕累累", 0.16, 0.45, 1.00)    # 16% 概率
]
DOPPLER_WEAR_LEVELS = [
    ("崭新出厂", 0.03, 0.00, 0.87),    # 3% 概率
    ("略有磨损", 0.24, 0.07, 0.12),   # 24% 概率
]
QUALITY_PROBABILITY = {
    "军规级": 0.7992,  # 军规级
    "受限": 0.1598,   # 受限级
    "保密": 0.032,    # 保密级
    "隐秘": 0.0064,   # 隐秘级
    "非凡": 0.0026    # 金
}
@register("CS武器箱开箱模拟", "luooka", "支持当前游戏中绝大多数武器箱,详细使用输入开箱菜单进行查看", "1.1")
class CasePlugin(Star):
    def __init__(self, context: Context,config: dict):
        super().__init__(context)
        self.config= config
        self.case_data = self._load_cases()
        self.open_history = self._load_history()
        print(self.config)
        print(self.config.get('number', 10))
    def _load_cases(self):
        """加载并处理武器箱数据"""
        try:
            os.makedirs('data', exist_ok=True)
            if not os.path.exists(CASES_FILE):
                with open(CASES_FILE, 'w', encoding='utf-8') as f:
                    json.dump({}, f, ensure_ascii=False)
            
            with open(CASES_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                self._process_cases(data)
                return data
        except Exception as e:
            raise PluginLoadError(f"数据加载失败: {str(e)}")

    def _process_cases(self, data):
        """处理每个武器箱的概率分配"""
        for case_name, items in data.items():
            quality_counts = {}
            # 统计各品质物品数量
            for item in items:
                quality = item["rln"]
                quality_counts[quality] = quality_counts.get(quality, 0) + 1
            
            # 分配概率并添加probability字段
            for item in items:
                quality = item["rln"]
                total_prob = QUALITY_PROBABILITY.get(quality, 0)
                count = quality_counts.get(quality, 1)
                item["probability"] = total_prob / count

    def _load_history(self):
        """加载开箱历史记录"""
        if not os.path.exists(HISTORY_FILE):
            return {}
        with open(HISTORY_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)

    def _save_history(self):
        """保存开箱记录"""
        with open(HISTORY_FILE, 'w', encoding='utf-8') as f:
            json.dump(self.open_history, f, indent=2, ensure_ascii=False)

    def _generate_item(self, case_name):
        """生成带磨损值的物品（包含概率范围）"""
        items = self.case_data[case_name]
        rand = random.random()
        cumulative = 0.0
        
        # 先选择物品品质
        for item in items:
            cumulative += item["probability"]
            if rand <= cumulative:
                # ===== 新增StatTrak判断 =====
                is_stattrak = False
                item_name = item["short_name"]
                # 排除手套类物品的StatTrak判断
                if "手套" not in item_name:
                    # 10%概率生成StatTrak
                    is_stattrak = random.random() < 0.1
                    # 处理物品名称
                    if is_stattrak:
                        item_name = f"StatTrak™ | {item_name}"
                # 根据概率分布选择磨损等级
                is_doppler = "多普勒" in item_name
                wear_config = DOPPLER_WEAR_LEVELS if is_doppler else WEAR_LEVELS                           
                # 根据配置选择磨损等级
                chosen_level = random.choices(
                    wear_config,
                    weights=[wl[1] for wl in wear_config],
                    k=1
                )[0]                
                # 在选定等级范围内生成磨损值
                wear_min = chosen_level[2]
                wear_max = chosen_level[3]
                wear = round(random.uniform(wear_min, wear_max), 8)
                
                return {
                    "name": item_name,
                    "quality": item["rln"],
                    "wear_value": wear,
                    "wear_level": chosen_level[0],
                    "template_id": random.randint(0, 999),
                    "img": item.get("img", "")
                }
        
        # 兜底逻辑（理论上不会执行到这里）
        last_item = items[-1]
        wear = round(random.uniform(0, 1), 8)
        return {
            "name": last_item["short_name"],
            "quality": last_item["rln"],
            "wear_value": wear,
            "wear_level": "战痕累累",
            "img": last_item.get("img", "")
        }

    def _record_history(self, group_id, user_id, item):
        """优化后的记录逻辑 只详细记录红/金物品"""
        history_key = f"{group_id}-{user_id}"
        self.open_history.setdefault(history_key, {
            "total": 0,
            "red_count": 0,       # 隐秘物品总数
            "gold_count": 0,      # 非凡物品总数
            "other_stats": {      # 其他品质统计
                "军规级": 0,
                "受限": 0,
                "保密": 0
            },
            "items": [],          # 仅存储红/金物品详情
            "last_open": None
        })
        
        record = self.open_history[history_key]
        record["total"] += 1
        
        # 分类存储逻辑
        quality = item["quality"]
        if quality == "隐秘":
            record["red_count"] += 1
            record["items"].append({
            "name": item["name"],
            "wear_value": item["wear_value"],
            "template_id": item["template_id"],
            "time": datetime.now().isoformat()
        })
        elif quality == "非凡":
            record["gold_count"] += 1
            record["items"].append({
                "name": item["name"],
                "wear_value": item["wear_value"],
                "template_id": item["template_id"],
                "time": datetime.now().isoformat()
            })
        else:
            if quality in record["other_stats"]:
                record["other_stats"][quality] += 1
        
        record["last_open"] = time.time()
        self._save_history()

    def _parse_command(self, msg: str) -> tuple:
        """
        解析开箱指令格式：
        支持格式：
        1. 开箱[武器箱名称][次数] 示例：开箱狂牙武器箱10
        2. 开箱[次数][空格][武器箱名称] 示例：开箱10 狂牙武器箱
        3. 开箱[武器箱名称]（默认开1箱）示例：开箱变革武器箱
        """
        clean_msg = msg.replace("开箱", "", 1).strip()
        if not clean_msg:
            return None, 1
        
        if ' ' in clean_msg:
            parts = clean_msg.split(maxsplit=1)
            if parts[0].isdigit():
                return parts[1], min(int(parts[0]), 1000)
            elif len(parts) > 1 and parts[1].isdigit():
                return parts[0], min(int(parts[1]), 1000)
        
        count_str = ""
        index = len(clean_msg) - 1
        while index >= 0 and clean_msg[index].isdigit():
            count_str = clean_msg[index] + count_str
            index -= 1
        
        if count_str:
            return clean_msg[:index+1].strip(), min(int(count_str), 1000)
        else:
            return clean_msg, 1

    @event_message_type(EventMessageType.GROUP_MESSAGE)
    async def on_group_message(self, event: AstrMessageEvent):
        msg = event.message_str.strip()
        
        if msg == "清除库存":
            async for result in self._handle_purge(event):
                yield result
        elif msg == "开箱菜单":
            async for result in self._show_menu(event):
                yield result
        elif msg == "库存":
            async for result in self._show_inventory(event):
                yield result
        elif msg.startswith("开箱"):
            async for result in self._handle_open(event):
                yield result

    async def _handle_purge(self, event: AstrMessageEvent):
        """处理清除库存"""
        group_id = str(event.message_obj.group_id)
        user_id = str(event.get_sender_id())
        history_key = f"{group_id}-{user_id}"
        if history_key in self.open_history:
            del self.open_history[history_key]
            self._save_history()
            yield event.plain_result("✅ 库存已清空")
        else:
            yield event.plain_result("❌ 没有可清除的库存")

    async def _handle_open(self, event: AstrMessageEvent):
        """处理开箱请求"""
        msg = event.message_str.strip()
        case_name, count = self._parse_command(msg)
        
        if not case_name:
            yield event.plain_result("❌ 请输入武器箱名称")
            return
        
        if case_name not in self.case_data:
            yield event.plain_result(f"❌ 未找到【{case_name}】武器箱")
            return
        
        group_id = str(event.message_obj.group_id)
        user_id = str(event.get_sender_id())
        nickname = event.get_sender_name()
        
        items_generated = []
        quality_stats = {"军规级":0, "受限":0, "保密":0, "隐秘":0, "非凡":0}
        message_chain = [
            Comp.At(qq=event.get_sender_id()),
            Comp.Plain(f"⚡ {nickname} 开启【{case_name}】x{count}\n"),
            Comp.Plain("\n")
        ]

        for _ in range(count):
            item = self._generate_item(case_name)
            items_generated.append(item)
            self._record_history(group_id, user_id, item)
            quality = item["quality"]
            quality_stats[quality] += 1

        # 新增品质统计和分段显示逻辑
        rare_items = []
        for item in items_generated:
            if item["quality"] in ["隐秘", "非凡"]:
                rare_items.append(item)
        

        if count <= int(self.config.get('number', '10')):
            # 显示所有物品详情
            for item in items_generated:
                if item.get("img"):
                    message_chain.append(Comp.Image.fromURL(item["img"]))
                message_chain.extend([
                    Comp.Plain(f"🎁 获得物品：{item['name']}\n"),
                    Comp.Plain(f"✦ 品质：{item['quality']}\n"),
                    Comp.Plain(f"🔧 磨损：{item['wear_level']} ({item['wear_value']:.8f}) | 模板编号: {item['template_id']}\n")
                ])
        else:
            # 超过阈值时显示统计和稀有物品
            message_chain.append(Comp.Plain(f"✦ 普通物品统计：\n"))
            for q in ["军规级", "受限", "保密"]:
                if quality_stats[q] > 0:
                    message_chain.append(Comp.Plain(f"· {q}: {quality_stats[q]}件\n"))
            
            if rare_items:
                message_chain.append(Comp.Plain("\n💎 稀有物品清单：\n"))
                for item in rare_items[:20]:
                    components = []
                    if item.get("img"):
                        components.append(Comp.Image.fromURL(item["img"]))
                    components.append(Comp.Plain(
                        f"▫ {item['name']} | 磨损:{item['wear_value']:.8f} | 模板编号: {item['template_id']}\n"
                    ))
                    message_chain.extend(components)
        
        # 添加库存信息
        history_key = f"{group_id}-{user_id}"
        message_chain.append(Comp.Plain(
            f"\n📦 当前库存：{self.open_history[history_key]['total']}件"
        ))
        yield event.chain_result(message_chain)
    async def _show_inventory(self, event: AstrMessageEvent):
        group_id = str(event.message_obj.group_id)
        user_id = str(event.get_sender_id())
        history_key = f"{group_id}-{user_id}"
        inventory = self.open_history.get(history_key, {})

        if not inventory.get("total"):
            yield event.plain_result("📭 库存空空如也")
            return

        # 构建统计信息
        result = [
            f"📦 总库存：{inventory['total']}件",
            "▬▬▬▬▬▬▬▬▬▬▬▬▬",
            "✦ 普通物品统计：",
            *[f"· {k}: {v}件" for k, v in inventory['other_stats'].items()]
        ]

        # 隐秘物品展示
        if inventory["red_count"] > 0:
            red_items = inventory["items"][:50] if inventory["red_count"] > 5 else inventory["items"]
            result.extend([
                "",
                "🔴 隐秘级物品：",
                *[f"▫ {item['name']} | 磨损:{item['wear_value']:.8f} | 模板编号: {item['template_id']}" for item in red_items]
            ])
            if inventory["red_count"] > 50:
                result.append(f"...等{inventory['red_count']}件隐秘级物品")
        result.append(f"\n⏰ 最后开箱：{datetime.fromtimestamp(inventory['last_open']).strftime('%m-%d %H:%M')}")
        yield event.plain_result("\n".join(result))
    
    async def _show_menu(self, event: AstrMessageEvent):
        """显示帮助菜单"""
        menu = [
            "🔫 CSGO开箱系统菜单",
            "▬▬▬▬▬▬▬▬▬▬▬▬▬",
            "✦ 单次开箱：开箱[武器箱名称]",
            "  示例：开箱梦魇武器箱",
            "",
            "✦ 批量开箱：开箱[次数][空格][武器箱名称] 或 开箱[武器箱名称][次数]",
            "  示例：开箱10 狂牙武器箱 或 开箱狂牙武器箱10",
            "",
            "✦ 库存查询：库存",
            "✦ 清除数据：清除库存",
            "",
            "👜 可用武器箱列表：",
            *[f"▫ {name}" for name in self.case_data]
        ]
        yield event.plain_result("\n".join(menu))
