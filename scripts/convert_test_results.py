import json
import os

with open(r"D:\美食测试集\美食 - 测试数据\caffee_260331_short_测试集.json", 'r', encoding='utf-8') as f:
    data = json.load(f)

result = []
for item in data.values():
    outputs = json.loads(item["conversations"][1]["a"])
    result.append({
        "image": os.path.basename(item["image"]),
        "predict_plan": outputs["content"],
    })

with open(r"D:\美食测试集\美食 - 测试数据\caffee_260331_short_测试集_convert.json", 'w', encoding='utf-8') as f:
    json.dump(result, f, ensure_ascii=False, indent=4)