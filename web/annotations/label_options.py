from __future__ import annotations

from typing import Any


ANGLE_OPTIONS = [0, 5, 10, 15, 20, 30, 45, 60, 75, 90]

LABEL_OPTION_GROUPS: list[dict[str, Any]] = [
    {
        "name": "输入图",
        "dimensions": [
            {"name": "拍摄角度", "options": ANGLE_OPTIONS},
            {"name": "菜品种类", "options": ["中餐", "日式料理", "西餐", "火锅", "下午茶", "家庭烹饪", "烧烤烤肉", "韩式料理", "东南亚菜", "野餐露营"]},
            {"name": "拍摄场景", "options": ["窗边", "居家", "餐厅", "室外", "室内"]},
            {"name": "光线", "options": ["顶灯", "自然光", "其他"]},
            {"name": "色温", "options": ["低色温", "中色温", "高色温", "混合色温"]},
            {"name": "菜品数量", "options": [1, 2, 3, 4, 5, "6+"]},
        ],
    },
    {
        "name": "输出图",
        "dimensions": [
            {"name": "拍摄角度", "options": ANGLE_OPTIONS},
            {"name": "景别", "options": ["特写", "中近景", "近景", "中景", "远景"]},
            {"name": "拍摄角度方法", "options": ["平拍", "斜拍", "俯拍", "其他"]},
            {"name": "构图&摆盘", "options": ["中心构图", "偏中心构图", "对角线构图", "三角形构图", "S形构图", "框架构图", "留白构图", "其他"]},
            {"name": "互动", "options": ["无互动", "切", "夹", "叉", "倒", "举杯", "其他"]},
            {"name": "新增餐具", "options": ["筷子", "刀", "叉", "瓷勺", "不锈钢勺", "其他"]},
            {"name": "美学评分", "options": [1, 2, 3, 4, 5]},
        ],
    },
]
