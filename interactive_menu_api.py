"""Build and resolve CS2 interactive menu pages without executing commands."""

import re
from collections.abc import Callable, Mapping

INTERACTIVE_MENU_API_ID = "cs2_openweaponscase"
INTERACTIVE_MENU_API_VERSION = 1
INTERACTIVE_MENU_CONSUMER_ID = "astrbot_plugin_react_menu"
INTERACTIVE_MENU_CONSUMER_API_VERSION = 1
INTERACTIVE_MENU_PAGE_SIZE = 10
INTERACTIVE_MENU_CONTAINER_TYPES = (
    ("case", "武器箱"),
    ("souvenir", "纪念包"),
    ("collection", "收藏品"),
    ("capsule", "胶囊/涂鸦/布章"),
)
INTERACTIVE_MENU_HOME_ITEMS = (
    ("self_open", "self_open_label", None),
    ("inventory", "inventory_label", "库存"),
    ("price_help", "price_help_label", "开箱菜单"),
    ("case_list", "case_list_label", "武器箱列表"),
    ("clear_inventory", "clear_inventory_label", "清除库存"),
    ("update_cases", "update_cases_label", "更新武器箱"),
    ("clear_cache", "clear_cache_label", "清除缓存"),
)
INTERACTIVE_MENU_DEFAULT_OPTIONS = {
    "self_open_label": "快速开箱",
    "inventory_label": "查看库存",
    "price_help_label": "开箱帮助",
    "case_list_label": "武器箱一览",
    "clear_inventory_label": "清空库存记录（个人）",
    "update_cases_label": "更新武器箱数据（管理员）",
    "clear_cache_label": "清理本地图片缓存（管理员）",
    "type_page_title": "选择开箱种类",
    "case_label": "武器箱",
    "souvenir_label": "纪念包",
    "collection_label": "收藏品",
    "capsule_label": "胶囊/涂鸦/布章",
    "category_option_template": "{label}（{count}）",
    "container_page_title_template": "选择{type}",
    "quantity_page_title_template": "选择开箱数量：{item}",
    "quantity_option_template": "开 {count} 个",
    "page_indicator_template": "（第 {current}/{total} 页）",
    "previous_page_label": "上一页",
    "next_page_label": "下一页",
    "back_to_type_label": "返回种类选择",
    "back_to_container_label": "返回箱子选择",
    "cancel_label": "取消选择",
    "cancel_reply_text": "✅ 已取消 CS2 开箱。",
    "quantity_options": "1,5,10,20,50",
}


def has_compatible_consumer(context):
    """Check whether the loaded menu plugin supports the expected consumer API."""
    get_all_stars = getattr(context, "get_all_stars", None)
    if not callable(get_all_stars):
        return False
    try:
        stars = get_all_stars()
    except Exception:
        return False
    for metadata in stars or []:
        if not getattr(metadata, "activated", False):
            continue
        plugin_id = str(getattr(metadata, "plugin_id", "") or "").casefold()
        if plugin_id and plugin_id != "dearcrazyleaf/astrbot_plugin_react_menu":
            continue
        plugin = getattr(metadata, "star_cls", None)
        info_method = getattr(plugin, "get_interactive_menu_consumer_api_info", None)
        if not callable(info_method):
            continue
        try:
            info = info_method()
        except Exception:
            continue
        if (
            isinstance(info, dict)
            and info.get("consumer_id") == INTERACTIVE_MENU_CONSUMER_ID
            and info.get("api_version") == INTERACTIVE_MENU_CONSUMER_API_VERSION
        ):
            return True
    return False


def get_api_info(enabled: bool):
    """Return the public provider identity and availability for react_menu."""
    return {
        "provider_id": INTERACTIVE_MENU_API_ID,
        "api_version": INTERACTIVE_MENU_API_VERSION,
        "enabled": enabled,
        "supported_platforms": ["aiocqhttp"],
        "page_size": INTERACTIVE_MENU_PAGE_SIZE,
    }


class InteractiveMenuAPI:
    """Use the current case data and configuration for one API request."""

    def __init__(
        self,
        *,
        case_data: Mapping[str, object],
        identify_container_type: Callable[[str], str],
        max_open_per_request: int,
        enabled: bool,
    ):
        self._case_data = case_data
        self._identify_container_type = identify_container_type
        self._max_open_per_request = max_open_per_request
        self._enabled = enabled

    def _container_names(self, category):
        return sorted(
            name for name in self._case_data if self._identify_container_type(name) == category
        )

    @staticmethod
    def _error(message):
        return {"kind": "error", "message": f"❌ {message}"}

    @staticmethod
    def _text(display_options, key, **values):
        options = display_options if isinstance(display_options, dict) else {}
        template = str(options.get(key) or INTERACTIVE_MENU_DEFAULT_OPTIONS[key])
        legacy_defaults = {
            "self_open_label": ("自助开箱",),
            "inventory_label": ("库存",),
            "price_help_label": ("查询价格说明",),
            "case_list_label": ("武器箱列表",),
            "clear_inventory_label": ("清除库存", "清空库存记录"),
            "update_cases_label": ("更新武器箱", "更新武器箱数据"),
            "clear_cache_label": ("清除缓存", "清理本地图片缓存"),
            "type_page_title": ("选择容器类型",),
            "quantity_page_title_template": ("选择数量：{container}",),
            "back_to_type_label": ("返回类型",),
            "back_to_container_label": ("返回容器选择",),
            "cancel_label": ("取消",),
            "cancel_reply_text": ("已取消 CS2 开箱。",),
        }
        if template in legacy_defaults.get(key, ()):
            template = INTERACTIVE_MENU_DEFAULT_OPTIONS[key]
        for name, value in values.items():
            template = template.replace("{" + name + "}", str(value))
        return template

    @staticmethod
    def _category_labels(display_options):
        return {
            "case": str(
                (display_options or {}).get("case_label")
                or INTERACTIVE_MENU_DEFAULT_OPTIONS["case_label"]
            ),
            "souvenir": str(
                (display_options or {}).get("souvenir_label")
                or INTERACTIVE_MENU_DEFAULT_OPTIONS["souvenir_label"]
            ),
            "collection": str(
                (display_options or {}).get("collection_label")
                or INTERACTIVE_MENU_DEFAULT_OPTIONS["collection_label"]
            ),
            "capsule": str(
                (display_options or {}).get("capsule_label")
                or INTERACTIVE_MENU_DEFAULT_OPTIONS["capsule_label"]
            ),
        }

    @staticmethod
    def _page_result(title, state, items, page_info=None):
        page = {"title": title, "state": state, "items": items}
        if page_info is not None:
            page["page_info"] = page_info
        return {"kind": "page", "page": page}

    def _quantity_choices(self, display_options=None):
        maximum = self._max_open_per_request
        raw_choices = (display_options or {}).get(
            "quantity_options", INTERACTIVE_MENU_DEFAULT_OPTIONS["quantity_options"]
        )
        if isinstance(raw_choices, list):
            values = raw_choices
        else:
            values = re.split(r"[,，;；\s]+", str(raw_choices or ""))

        choices = set()
        for value in values:
            try:
                count = int(value)
            except (TypeError, ValueError):
                continue
            if 0 < count <= maximum:
                choices.add(count)
        if not choices:
            choices = {1}
        return sorted(choices)

    def get_page(self, state=None, display_options=None):
        """Build a menu page from cached data without changing open-box state."""
        if not self._enabled:
            return self._error("CS2 互动菜单 API 未启用")
        if state is None:
            state = {"level": "home"}
        if not isinstance(state, dict):
            return self._error("菜单状态无效，请重新发送“开箱”")
        if not isinstance(display_options, dict):
            display_options = {}

        level = state.get("level", "home")
        if level == "home":
            items = []
            for action_key, option_key, _ in INTERACTIVE_MENU_HOME_ITEMS:
                items.append(
                    {
                        "id": f"home:{action_key}",
                        "label": self._text(display_options, option_key),
                        "action_id": f"home:{action_key}",
                    }
                )
            return self._page_result("", {"level": "home"}, items)

        if level in ("type", "container", "quantity") and not self._case_data:
            return self._error("当前没有可开箱数据，请先更新武器箱")

        if level == "type":
            items = []
            labels = self._category_labels(display_options)
            for category, _ in INTERACTIVE_MENU_CONTAINER_TYPES:
                count = len(self._container_names(category))
                if count:
                    label = self._text(
                        display_options,
                        "category_option_template",
                        label=labels[category],
                        count=count,
                    )
                    items.append(
                        {
                            "id": f"category:{category}",
                            "label": label,
                            "action_id": f"category:{category}",
                        }
                    )
            if not items:
                return self._error("当前没有可用的开箱种类")
            items.append(
                {
                    "id": "navigation:cancel",
                    "label": self._text(display_options, "cancel_label"),
                    "action_id": "nav:cancel",
                }
            )
            title = self._text(display_options, "type_page_title")
            return self._page_result(title, {"level": "type"}, items)

        if level == "container":
            category = state.get("category")
            valid_categories = {key for key, _ in INTERACTIVE_MENU_CONTAINER_TYPES}
            if not isinstance(category, str) or category not in valid_categories:
                return self._error("开箱种类无效，请重新发送“开箱”")
            names = self._container_names(category)
            if not names:
                return self._error("该种类当前没有可开箱的物品")
            try:
                page_index = int(state.get("page", 0))
            except (TypeError, ValueError):
                return self._error("页码无效，请重新发送“开箱”")
            page_count = (len(names) + INTERACTIVE_MENU_PAGE_SIZE - 1) // INTERACTIVE_MENU_PAGE_SIZE
            if page_index < 0 or page_index >= page_count:
                return self._error("菜单页已失效，请重新发送“开箱”")

            start = page_index * INTERACTIVE_MENU_PAGE_SIZE
            page_names = names[start : start + INTERACTIVE_MENU_PAGE_SIZE]
            items = [
                {"id": f"container:{name}", "label": name, "action_id": f"container:{name}"}
                for name in page_names
            ]
            if page_index > 0:
                items.append(
                    {
                        "id": "navigation:prev",
                        "label": self._text(display_options, "previous_page_label"),
                        "action_id": "nav:prev",
                    }
                )
            if page_index + 1 < page_count:
                items.append(
                    {
                        "id": "navigation:next",
                        "label": self._text(display_options, "next_page_label"),
                        "action_id": "nav:next",
                    }
                )
            items.extend(
                (
                    {
                        "id": "navigation:back",
                        "label": self._text(display_options, "back_to_type_label"),
                        "action_id": "nav:back",
                    },
                    {
                        "id": "navigation:cancel",
                        "label": self._text(display_options, "cancel_label"),
                        "action_id": "nav:cancel",
                    },
                )
            )
            label = self._category_labels(display_options)[category]
            return self._page_result(
                self._text(display_options, "container_page_title_template", type=label),
                {"level": "container", "category": category, "page": page_index},
                items,
                {"current": page_index + 1, "total": page_count},
            )

        if level == "quantity":
            container_name = state.get("container_name")
            if not isinstance(container_name, str) or container_name not in self._case_data:
                return self._error("该开箱物品已不可用，请重新选择")
            try:
                container_page = max(0, int(state.get("container_page", 0)))
            except (TypeError, ValueError):
                container_page = 0
            items = [
                {
                    "id": f"quantity:{count}",
                    "label": self._text(display_options, "quantity_option_template", count=count),
                    "action_id": f"quantity:{count}",
                }
                for count in self._quantity_choices(display_options)
            ]
            items.extend(
                (
                    {
                        "id": "navigation:back",
                        "label": self._text(display_options, "back_to_container_label"),
                        "action_id": "nav:back",
                    },
                    {
                        "id": "navigation:cancel",
                        "label": self._text(display_options, "cancel_label"),
                        "action_id": "nav:cancel",
                    },
                )
            )
            return self._page_result(
                self._text(
                    display_options,
                    "quantity_page_title_template",
                    item=container_name,
                    container=container_name,
                ),
                {
                    "level": "quantity",
                    "category": self._identify_container_type(container_name),
                    "container_name": container_name,
                    "container_page": container_page,
                },
                items,
            )

        return self._error("菜单步骤无效，请重新发送“开箱”")

    def handle_action(self, state, action_id, display_options=None):
        """Resolve one provider action; final selections return the legacy command."""
        if not self._enabled:
            return self._error("CS2 互动菜单 API 未启用")
        if not isinstance(state, dict) or not isinstance(action_id, str):
            return self._error("菜单操作无效，请重新发送“开箱”")
        if not isinstance(display_options, dict):
            display_options = {}
        if action_id == "nav:cancel":
            return {
                "kind": "cancel",
                "message": self._text(display_options, "cancel_reply_text"),
            }

        level = state.get("level", "home")
        if level == "home":
            action = next(
                (item for item in INTERACTIVE_MENU_HOME_ITEMS if action_id == f"home:{item[0]}"),
                None,
            )
            if action is None:
                return self._error("请选择有效的菜单项目")
            action_key, _, command = action
            if action_key == "self_open":
                return self.get_page({"level": "type"}, display_options)
            return {"kind": "command", "command": command, "keep_menu": True}

        if level == "type":
            prefix = "category:"
            if not action_id.startswith(prefix):
                return self._error("请选择有效的开箱种类")
            category = action_id[len(prefix) :]
            if not self._container_names(category):
                return self._error("该开箱种类当前不可用")
            return self.get_page(
                {"level": "container", "category": category, "page": 0}, display_options
            )

        if level == "container":
            current_page = self.get_page(state, display_options)
            if current_page.get("kind") != "page":
                return current_page
            current_state = current_page["page"]["state"]
            category = current_state["category"]
            page_index = current_state["page"]
            names = self._container_names(category)
            page_count = (len(names) + INTERACTIVE_MENU_PAGE_SIZE - 1) // INTERACTIVE_MENU_PAGE_SIZE

            if action_id == "nav:back":
                return self.get_page({"level": "type"}, display_options)
            if action_id == "nav:prev":
                if page_index == 0:
                    return self._error("已经是第一页")
                return self.get_page(
                    {"level": "container", "category": category, "page": page_index - 1},
                    display_options,
                )
            if action_id == "nav:next":
                if page_index + 1 >= page_count:
                    return self._error("已经是最后一页")
                return self.get_page(
                    {"level": "container", "category": category, "page": page_index + 1},
                    display_options,
                )

            prefix = "container:"
            if not action_id.startswith(prefix):
                return self._error("请选择当前页中的开箱物品")
            container_name = action_id[len(prefix) :]
            start = page_index * INTERACTIVE_MENU_PAGE_SIZE
            if container_name not in names[start : start + INTERACTIVE_MENU_PAGE_SIZE]:
                return self._error("该物品不在当前列表页，请重新选择")
            return self.get_page(
                {
                    "level": "quantity",
                    "category": category,
                    "container_name": container_name,
                    "container_page": page_index,
                },
                display_options,
            )

        if level == "quantity":
            container_name = state.get("container_name")
            if not isinstance(container_name, str) or container_name not in self._case_data:
                return self._error("该开箱物品已不可用，请重新选择")
            if action_id == "nav:back":
                return self.get_page(
                    {
                        "level": "container",
                        "category": self._identify_container_type(container_name),
                        "page": state.get("container_page", 0),
                    },
                    display_options,
                )

            prefix = "quantity:"
            if not action_id.startswith(prefix):
                return self._error("请选择有效的开箱数量")
            try:
                count = int(action_id[len(prefix) :])
            except ValueError:
                return self._error("开箱数量无效")
            if count not in self._quantity_choices(display_options):
                return self._error("该数量当前不可用")
            return {
                "kind": "command",
                "command": f"开箱 {count} {container_name}",
                "keep_menu": True,
            }

        return self._error("菜单步骤已失效，请重新发送“开箱”")
