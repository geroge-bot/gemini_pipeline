import os
import glob
from collections import defaultdict

dir_ori = r"D:\gemini_测试结果\原图"
dir_tgt = r"D:\gemini_测试结果\标注_2方案"

# 获取原图前缀
ori_files = glob.glob(os.path.join(dir_ori, "*.*"))
ori_prefixes = [os.path.splitext(os.path.basename(f))[0] for f in ori_files]
sorted_ori_prefixes = sorted(ori_prefixes, key=len, reverse=True)

# 获取目标目录中的所有文件
tgt_files = glob.glob(os.path.join(dir_tgt, "**", "*.*"), recursive=True)

tgt_jpgs = defaultdict(list)
tgt_jsons = defaultdict(list)
unmatched_files = []

for filepath in tgt_files:
    if not os.path.isfile(filepath):
        continue
    
    filename = os.path.basename(filepath)
    name, ext = os.path.splitext(filename)
    
    matched_prefix = None
    for prefix in sorted_ori_prefixes:
        if name.startswith(prefix):
            matched_prefix = prefix
            break
            
    if matched_prefix:
        if ext.lower() in ['.jpg', '.jpeg', '.png']:
            tgt_jpgs[matched_prefix].append(filename)
        elif ext.lower() == '.json':
            tgt_jsons[matched_prefix].append(filename)
        else:
            unmatched_files.append(filename) # 异常后缀也放到多余清单
    else:
        unmatched_files.append(filename)

extra_files_report = []
for prefix in ori_prefixes:
    jpg_count = len(tgt_jpgs[prefix])
    json_count = len(tgt_jsons[prefix])
    
    # 查找超过 2 个 JPG 或者超过 2 个 JSON 的前缀
    if jpg_count > 2 or json_count > 2:
        extra_files_report.append({
            'prefix': prefix,
            'jpg_count': jpg_count,
            'json_count': json_count,
            'jpgs': tgt_jpgs[prefix],
            'jsons': tgt_jsons[prefix]
        })

report_path = r"D:\gemini_测试结果\多余情况汇总.md"
with open(report_path, "w", encoding="utf-8") as f:
    f.write("# 标注方案2 多余文件检查报告\n\n")
    f.write(f"**原图总数:** {len(ori_prefixes)}\n")
    
    if unmatched_files:
        f.write(f"**未匹配到原图前缀的多余文件数:** {len(unmatched_files)}\n\n")
        f.write("### 未匹配的文件列表:\n")
        for uf in unmatched_files[:50]:
            f.write(f"- `{uf}`\n")
        if len(unmatched_files) > 50:
            f.write(f"- ... 等共 {len(unmatched_files)} 个未匹配文件\n")
        f.write("\n")
    else:
        f.write("**未匹配到原图前缀的多余文件数:** 0\n\n")
        
    f.write(f"**数量超出的前缀数 (超过 2 JPG 或 2 JSON):** {len(extra_files_report)}\n\n")
    
    if extra_files_report:
        f.write("## 1. 数量超出的前缀列表\n")
        for item in extra_files_report:
            f.write(f"### {item['prefix']}\n")
            if item['jpg_count'] > 2:
                f.write(f"- ⚠️ 找到 {item['jpg_count']} 个 JPG (多于2个): `{item['jpgs']}`\n")
            elif item['jpg_count'] <= 2:
                f.write(f"- JPG 数量正常: {item['jpg_count']} 个\n")
                
            if item['json_count'] > 2:
                f.write(f"- ⚠️ 找到 {item['json_count']} 个 JSON (多于2个): `{item['jsons']}`\n")
            elif item['json_count'] <= 2:
                f.write(f"- JSON 数量正常: {item['json_count']} 个\n")
            f.write("\n")

print(f"分析完成，报告已生成至：{report_path}")
