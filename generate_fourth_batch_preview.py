#!/usr/bin/env python3
"""Generate a static, local preview for the extracted fourth-batch dataset."""

from __future__ import annotations

import argparse
import html
import json
import os
import random
from pathlib import Path
from typing import Any


WORKFLOW_ORDER = {
    "累积编辑": 0,
    "累积指代": 1,
    "短指令": 2,
    "隐式回滚": 3,
    "显式回滚": 4,
    "规则约束": 5,
}


def visible_dirs(path: Path) -> list[Path]:
    try:
        return sorted(
            (Path(entry.path) for entry in os.scandir(path) if entry.is_dir() and not entry.name.startswith(".")),
            key=lambda item: (WORKFLOW_ORDER.get(item.name, 99), item.name),
        )
    except OSError:
        return []


def task_files(task: Path) -> list[Path]:
    files: list[Path] = []
    for current, dirs, names in os.walk(task):
        relative_depth = len(Path(current).relative_to(task).parts)
        if relative_depth >= 2:
            dirs[:] = []
        dirs[:] = [name for name in dirs if not name.startswith("._")]
        files.extend(
            Path(current) / name
            for name in names
            if not name.startswith("._")
        )
    return files


def select_image(files: list[Path], names: tuple[str, ...]) -> Path | None:
    by_name: dict[str, list[Path]] = {}
    for path in files:
        by_name.setdefault(path.name.lower(), []).append(path)
    for name in names:
        matches = by_name.get(name.lower())
        if matches:
            return sorted(matches)[-1]
    return None


def choose_images(files: list[Path]) -> tuple[Path | None, Path | None]:
    seed = select_image(files, ("seed.jpg", "seed.jpeg", "seed.png", "reference_source_1_4.jpg"))
    result = select_image(
        files,
        (
            "grid_5_8.jpg",
            "grid_1_4.jpg",
            "reference_grid_1_4.jpg",
            "round_08.jpg",
            "round_04.jpg",
            "round_01.jpg",
        ),
    )
    if result is None:
        images = sorted(
            (path for path in files if path.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}),
            key=lambda path: path.as_posix(),
        )
        result = images[-1] if images else None
    return seed, result


def read_prompt_metadata(task: Path) -> dict[str, Any]:
    prompt_file = task / "final_prompts.json"
    if not prompt_file.is_file():
        return {}
    try:
        payload = json.loads(prompt_file.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {"prompt_json": "无法解析"}
    prompt = payload.get("combined_prompt_zh") or payload.get("combined_prompt_en") or ""
    if isinstance(prompt, str) and len(prompt) > 260:
        prompt = prompt[:257] + "…"
    return {
        "status": payload.get("status", ""),
        "task_type": payload.get("task_type_zh") or payload.get("task_type") or "",
        "round_count": payload.get("round_count", ""),
        "prompt": prompt if isinstance(prompt, str) else "",
    }


def load_batch_records(batch: Path) -> dict[tuple[str, str, str], dict[str, Any]]:
    summary = batch / "batch_result_summary.json"
    if not summary.is_file():
        return {}
    try:
        records = json.loads(summary.read_text(encoding="utf-8")).get("records", [])
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, AttributeError):
        return {}
    index: dict[tuple[str, str, str], dict[str, Any]] = {}
    for record in records:
        if not isinstance(record, dict):
            continue
        workflow = str(record.get("workflow", ""))
        status_group = str(record.get("status_group", ""))
        directory = record.get("moved_dir") or record.get("run_dir") or ""
        task_name = Path(str(directory)).name
        if workflow and task_name:
            index[(workflow, task_name, status_group)] = record
    return index


def relative_url(path: Path | None, root: Path) -> str:
    return path.relative_to(root).as_posix() if path else ""


def first_existing(paths: list[Path]) -> Path | None:
    for path in paths:
        if path.is_file():
            return path
    return None


def make_item(task: Path, root: Path, workflow: str, state: str, record: dict[str, Any]) -> dict[str, Any]:
    result_name = Path(str(record.get("result_image", ""))).name
    seed_candidates = [task / "seed.jpg", task / "reference_source_1_4.jpg", task / "seed.jpeg", task / "seed.png"]
    result_candidates = [
        *([task / result_name] if result_name else []),
        task / "grid_5_8.jpg",
        task / "grid_1_4.jpg",
        task / "reference_grid_1_4.jpg",
        task / "split_5_8" / "round_08.jpg",
        task / "split_1_4" / "round_04.jpg",
        task / "split_1_4" / "round_01.jpg",
    ]
    seed_urls = list(dict.fromkeys(relative_url(path, root) for path in seed_candidates))
    result_urls = list(dict.fromkeys(relative_url(path, root) for path in result_candidates))
    round_count = 8 if result_name == "grid_5_8.jpg" else (4 if result_name else "")
    error = str(record.get("error", ""))
    if len(error) > 420:
        error = error[:417] + "…"
    return {
        "task": task.name,
        "path": task.relative_to(root).as_posix(),
        "seed": seed_urls[0],
        "result": result_urls[0],
        "seed_candidates": seed_urls,
        "result_candidates": result_urls,
        "file_count": "",
        "status": record.get("status") or state,
        "task_type": workflow,
        "round_count": round_count,
        "prompt": "",
        "error": error,
    }


def build_groups(root: Path, limit: int, seed: int) -> list[dict[str, Any]]:
    groups: list[dict[str, Any]] = []
    batches = sorted(path for path in visible_dirs(root) if path.name.startswith("batch_"))
    for batch in batches:
        indexed: dict[tuple[str, str], list[dict[str, Any]]] = {}
        success_file = batch / "success_manifest.json"
        summary_file = batch / "batch_result_summary.json"
        resume_file = batch / "batch_resume_plan.json"
        if success_file.is_file():
            payload = json.loads(success_file.read_text(encoding="utf-8"))
            for record in payload.get("records", []):
                indexed.setdefault(("成功", str(record.get("workflow", "未知"))), []).append(record)
            if summary_file.is_file():
                payload = json.loads(summary_file.read_text(encoding="utf-8"))
                for record in payload.get("records", []):
                    if record.get("status_group") == "failed":
                        indexed.setdefault(("失败", str(record.get("workflow", "未知"))), []).append(record)
        elif resume_file.is_file():
            payload = json.loads(resume_file.read_text(encoding="utf-8"))
            for record in payload.get("jobs", []):
                indexed.setdefault(("成功", str(record.get("workflow", "未知"))), []).append(record)

        categories = sorted(indexed, key=lambda value: (0 if value[0] == "成功" else 1, WORKFLOW_ORDER.get(value[1], 99), value[1]))
        for state, workflow in categories:
            records = indexed[(state, workflow)]
            if not records:
                continue
            rng = random.Random(f"{seed}:{batch.name}:{state}:{workflow}")
            candidates = records[:]
            rng.shuffle(candidates)
            items: list[dict[str, Any]] = []
            for record in candidates:
                directory = record.get("moved_dir") or record.get("run_dir") or ""
                task_name = Path(str(directory)).name
                if not task_name:
                    continue
                task = batch / ("失败任务" if state == "失败" else "") / workflow / task_name
                items.append(make_item(task, root, workflow, state, record))
                if len(items) >= limit:
                    break
            if not items:
                continue
            groups.append(
                {
                    "id": f"{batch.name}|{state}|{workflow}",
                    "batch": batch.name,
                    "state": state,
                    "workflow": workflow,
                    "available": len(records),
                    "sampled": len(items),
                    "items": sorted(items, key=lambda item: item["task"]),
                }
            )
    return groups


def render_html(groups: list[dict[str, Any]], root: Path, limit: int, seed: int) -> str:
    data = json.dumps(groups, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    total_sampled = sum(group["sampled"] for group in groups)
    batches = len({group["batch"] for group in groups})
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>第四批 · 随机样本预览</title>
<style>
:root{{--bg:#0b0d12;--panel:#141822;--panel2:#1b2130;--text:#eef2f8;--muted:#98a3b7;--line:#2a3345;--accent:#7c9cff;--ok:#39d98a;--bad:#ff6b79;--shadow:0 16px 42px rgba(0,0,0,.28)}}
*{{box-sizing:border-box}} body{{margin:0;background:var(--bg);color:var(--text);font-family:Inter,"PingFang SC","Microsoft YaHei",system-ui,sans-serif}}
header{{position:sticky;top:0;z-index:10;padding:20px 28px 16px;background:rgba(11,13,18,.92);backdrop-filter:blur(16px);border-bottom:1px solid var(--line)}}
.top{{display:flex;align-items:end;justify-content:space-between;gap:20px;flex-wrap:wrap}} h1{{font-size:25px;margin:0 0 5px}} .sub{{color:var(--muted);font-size:13px}}
.controls{{display:flex;gap:10px;flex-wrap:wrap}} select,input{{min-height:40px;border:1px solid var(--line);border-radius:10px;background:var(--panel);color:var(--text);padding:0 12px;font-size:14px}}
select{{min-width:290px}} input{{width:220px}} main{{padding:24px 28px 60px;max-width:1800px;margin:auto}}
.summary{{display:flex;align-items:center;gap:12px;flex-wrap:wrap;margin-bottom:20px}} .pill{{padding:7px 11px;border-radius:999px;background:var(--panel2);color:var(--muted);font-size:13px;border:1px solid var(--line)}}
.pill.ok{{color:var(--ok)}} .pill.bad{{color:var(--bad)}} .grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(360px,1fr));gap:18px}}
.card{{background:var(--panel);border:1px solid var(--line);border-radius:14px;overflow:hidden;box-shadow:var(--shadow)}} .images{{display:grid;grid-template-columns:1fr 1fr;background:#07080b;min-height:180px}}
.imagebox{{position:relative;min-width:0;border-right:1px solid var(--line)}} .imagebox:last-child{{border:0}} .imagebox img{{width:100%;height:230px;display:block;object-fit:contain;cursor:zoom-in;background:#090b10}}
.tag{{position:absolute;top:8px;left:8px;background:rgba(0,0,0,.68);padding:4px 7px;border-radius:6px;font-size:11px}} .empty{{height:230px;display:grid;place-items:center;color:#657087;font-size:12px}}
.body{{padding:13px 14px 15px}} .title{{display:flex;align-items:center;justify-content:space-between;gap:10px;font-weight:700}} .state{{font-size:11px;padding:3px 7px;border-radius:99px;background:rgba(57,217,138,.12);color:var(--ok)}} .state.bad{{background:rgba(255,107,121,.12);color:var(--bad)}}
.path{{margin-top:7px;color:var(--muted);font:11px/1.45 ui-monospace,SFMono-Regular,monospace;overflow-wrap:anywhere}} .meta{{display:flex;gap:8px;flex-wrap:wrap;margin-top:9px;color:#b8c2d4;font-size:12px}}
.prompt,.error{{margin-top:10px;padding:9px 10px;border-radius:8px;background:#10141d;color:#cbd3e1;font-size:12px;line-height:1.5;max-height:92px;overflow:auto}} .error{{color:#ffc0c6;border-left:3px solid var(--bad)}}
.actions{{display:flex;gap:8px;margin-top:11px}} a,button{{border:1px solid var(--line);background:var(--panel2);color:#dce4f3;border-radius:8px;padding:6px 9px;text-decoration:none;font-size:12px;cursor:pointer}} a:hover,button:hover{{border-color:var(--accent)}}
#modal{{display:none;position:fixed;inset:0;z-index:99;background:rgba(0,0,0,.91);padding:30px}} #modal.open{{display:grid;place-items:center}} #modal img{{max-width:96vw;max-height:91vh;object-fit:contain}} #modal span{{position:absolute;right:24px;top:16px;font-size:32px;cursor:pointer}}
.none{{padding:70px;text-align:center;color:var(--muted)}} @media(max-width:700px){{header,main{{padding-left:14px;padding-right:14px}}.grid{{grid-template-columns:1fr}}select,input{{width:100%;min-width:0}}.images img,.empty{{height:190px}}}}
</style>
</head>
<body>
<header><div class="top"><div><h1>第四批 · 随机样本预览</h1><div class="sub">{batches} 个批次 · 每个批次/类别最多 {limit} 条 · 固定随机种子 {seed} · 共 {total_sampled:,} 个样本</div></div><div class="controls"><select id="group"></select><input id="search" placeholder="筛选任务名…"></div></div></header>
<main><div id="summary" class="summary"></div><div id="grid" class="grid"></div></main>
<div id="modal"><span>×</span><img alt="大图预览"></div>
<script id="preview-data" type="application/json">{data}</script>
<script>
const groups=JSON.parse(document.getElementById('preview-data').textContent), select=document.getElementById('group'), search=document.getElementById('search'), grid=document.getElementById('grid'), summary=document.getElementById('summary'), modal=document.getElementById('modal'), modalImg=modal.querySelector('img');
for(const g of groups){{const o=document.createElement('option');o.value=g.id;o.textContent=`${{g.batch.replace('batch_','')}} · ${{g.state}} · ${{g.workflow}} (${{g.sampled}}/${{g.available}})`;select.appendChild(o)}}
function esc(s){{return String(s??'').replace(/[&<>'"]/g,c=>({{'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}}[c]))}}
function imageBox(urls,label){{return urls?.length?`<div class="imagebox"><span class="tag">${{label}}</span><img loading="lazy" decoding="async" src="${{esc(urls[0])}}" data-index="0" data-candidates="${{esc(JSON.stringify(urls))}}" alt="${{label}}"></div>`:`<div class="imagebox"><span class="tag">${{label}}</span><div class="empty">无可用图片</div></div>`}}
function render(){{const g=groups.find(x=>x.id===select.value)||groups[0], q=search.value.trim().toLowerCase(), items=g.items.filter(x=>!q||x.task.toLowerCase().includes(q));location.hash=encodeURIComponent(g.id);summary.innerHTML=`<span class="pill">批次 ${{esc(g.batch.replace('batch_',''))}}</span><span class="pill ${{g.state==='失败'?'bad':'ok'}}">${{esc(g.state)}} · ${{esc(g.workflow)}}</span><span class="pill">随机抽取 ${{g.sampled}} / 清单共 ${{g.available}}</span><span class="pill">当前显示 ${{items.length}}</span>`;grid.innerHTML=items.map(x=>`<article class="card"><div class="images">${{imageBox(x.seed_candidates,'Seed')}}${{imageBox(x.result_candidates,'Result / Grid')}}</div><div class="body"><div class="title"><span>${{esc(x.task)}}</span><span class="state ${{g.state==='失败'?'bad':''}}">${{esc(x.status||g.state)}}</span></div><div class="path">${{esc(x.path)}}</div><div class="meta"><span>${{esc(x.task_type)}}</span><span>${{x.round_count!==''?'轮次 '+esc(x.round_count):''}}</span></div>${{x.prompt?`<div class="prompt">${{esc(x.prompt)}}</div>`:''}}${{x.error?`<div class="error">${{esc(x.error)}}</div>`:''}}<div class="actions">${{x.result?`<a href="${{esc(x.result)}}" target="_blank">打开首选结果</a>`:''}}${{x.seed?`<a href="${{esc(x.seed)}}" target="_blank">打开 Seed</a>`:''}}<button data-copy="${{esc(x.path)}}">复制路径</button></div></div></article>`).join('')||'<div class="none">没有匹配的任务</div>';bind()}}
function bind(){{grid.querySelectorAll('img[data-candidates]').forEach(img=>{{img.onerror=()=>{{const candidates=JSON.parse(img.dataset.candidates),next=Number(img.dataset.index)+1;if(next<candidates.length){{img.dataset.index=String(next);img.src=candidates[next]}}else{{img.replaceWith(Object.assign(document.createElement('div'),{{className:'empty',textContent:'无可用图片'}}))}}}};img.onclick=()=>{{modalImg.src=img.currentSrc||img.src;modal.classList.add('open')}}}});grid.querySelectorAll('[data-copy]').forEach(btn=>btn.onclick=async()=>{{await navigator.clipboard.writeText(btn.dataset.copy);btn.textContent='已复制'}})}}
modal.onclick=()=>{{modal.classList.remove('open');modalImg.src=''}};document.addEventListener('keydown',e=>{{if(e.key==='Escape')modal.click()}});select.onchange=render;search.oninput=render;
const requested=decodeURIComponent(location.hash.slice(1));if(groups.some(g=>g.id===requested))select.value=requested;render();
</script>
</body></html>"""


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--seed", type=int, default=20260717)
    args = parser.parse_args()
    root = args.root.resolve()
    groups = build_groups(root, args.limit, args.seed)
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(render_html(groups, root, args.limit, args.seed), encoding="utf-8")
    print(json.dumps({"output": str(output), "groups": len(groups), "samples": sum(g["sampled"] for g in groups)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
