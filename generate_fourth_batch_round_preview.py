#!/usr/bin/env python3
"""Create a static Seed -> rounds -> prompts viewer for the fourth batch."""

from __future__ import annotations

import argparse
import concurrent.futures
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


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}


def relative(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def record_task(batch: Path, record: dict[str, Any]) -> Path | None:
    workflow = str(record.get("workflow", ""))
    original = record.get("moved_dir") or record.get("run_dir") or ""
    task_name = Path(str(original)).name
    return batch / workflow / task_name if workflow and task_name else None


def load_task(candidate: tuple[Path, Path, dict[str, Any]]) -> dict[str, Any] | None:
    root, batch, record = candidate
    task = record_task(batch, record)
    if task is None:
        return None
    prompt_path = task / "final_prompts.json"
    payload = read_json(prompt_path)
    rounds = payload.get("rounds")
    if not isinstance(rounds, list) or not rounds:
        return None
    files = payload.get("files") if isinstance(payload.get("files"), dict) else {}
    seed_name = files.get("seed_original") or "seed.jpg"
    result_rounds: list[dict[str, Any]] = []
    for position, item in enumerate(rounds, 1):
        if not isinstance(item, dict):
            continue
        number = item.get("round") if isinstance(item.get("round"), int) else position
        output = item.get("output_image")
        if not isinstance(output, str) or not output:
            output = f"split_1_4/round_{number:02d}.jpg" if number <= 4 else f"split_5_8/round_{number:02d}.jpg"
        result_rounds.append(
            {
                "round": number,
                "image": relative(task / output, root),
                "input_image": str(item.get("input_image", "")),
                "input_index": str(item.get("input_index", "")),
                "output_index": str(item.get("output_index", "")),
                "source_rounds": item.get("source_rounds", []),
                "edit_type": item.get("edit_type_zh") or item.get("edit_type_key") or "",
                "visible_change": str(item.get("actual_visible_change_zh", "")),
                "prompt_zh_long": str(item.get("prompt_zh_long", "")),
                "prompt_zh_short": str(item.get("prompt_zh_short", "")),
                "prompt_en_long": str(item.get("prompt_en_long", "")),
                "prompt_en_short": str(item.get("prompt_en_short", "")),
                "status": str(item.get("status", "")),
            }
        )
    if not result_rounds:
        return None
    return {
        "task": task.name,
        "path": relative(task, root),
        "seed": relative(task / str(seed_name), root),
        "workflow": record.get("workflow", ""),
        "status": payload.get("status") or record.get("status") or "",
        "round_count": payload.get("round_count") or len(result_rounds),
        "rounds": result_rounds,
    }


def build_groups(root: Path, limit: int, seed: int, workers: int) -> list[dict[str, Any]]:
    batch_paths = sorted(path for path in root.iterdir() if path.is_dir() and path.name.startswith("batch_"))
    raw_groups: list[tuple[Path, str, list[dict[str, Any]]]] = []
    for batch in batch_paths:
        manifest = batch / "success_manifest.json"
        resume = batch / "batch_resume_plan.json"
        source = read_json(manifest if manifest.is_file() else resume)
        records = source.get("records") if manifest.is_file() else source.get("jobs")
        if not isinstance(records, list):
            continue
        by_workflow: dict[str, list[dict[str, Any]]] = {}
        for record in records:
            if isinstance(record, dict) and record.get("workflow"):
                by_workflow.setdefault(str(record["workflow"]), []).append(record)
        for workflow in sorted(by_workflow, key=lambda value: (WORKFLOW_ORDER.get(value, 99), value)):
            candidates = by_workflow[workflow][:]
            random.Random(f"{seed}:{batch.name}:{workflow}").shuffle(candidates)
            raw_groups.append((batch, workflow, candidates))

    jobs: list[tuple[int, tuple[Path, Path, dict[str, Any]]]] = []
    for group_index, (batch, _workflow, records) in enumerate(raw_groups):
        jobs.extend((group_index, (root, batch, record)) for record in records[:limit])
    loaded: list[list[dict[str, Any]]] = [[] for _ in raw_groups]
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        future_map = {pool.submit(load_task, candidate): group_index for group_index, candidate in jobs}
        for future in concurrent.futures.as_completed(future_map):
            item = future.result()
            if item is not None:
                loaded[future_map[future]].append(item)

    groups: list[dict[str, Any]] = []
    for index, (batch, workflow, records) in enumerate(raw_groups):
        order = {Path(str(record.get("moved_dir") or record.get("run_dir") or "")).name: i for i, record in enumerate(records)}
        items = sorted(loaded[index], key=lambda item: order.get(item["task"], 10**9))
        for record in records[limit:]:
            if len(items) >= limit:
                break
            item = load_task((root, batch, record))
            if item is not None:
                items.append(item)
        items = sorted(items, key=lambda item: order.get(item["task"], 10**9))[:limit]
        if items:
            groups.append(
                {
                    "id": f"{batch.name}|{workflow}",
                    "batch": batch.name,
                    "workflow": workflow,
                    "available": len(records) if len(records) < limit + 25 else None,
                    "sampled": len(items),
                    "items": items,
                }
            )
    return groups


def render(groups: list[dict[str, Any]], limit: int, seed: int) -> str:
    payload = json.dumps(groups, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    sample_count = sum(group["sampled"] for group in groups)
    batch_count = len({group["batch"] for group in groups})
    return f'''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>第四批 · Seed / Rounds / Prompts</title>
<style>
:root{{--bg:#090b10;--panel:#121722;--panel2:#181f2d;--line:#293246;--text:#edf2fb;--muted:#98a5ba;--accent:#88a5ff;--green:#45d997;--shadow:0 18px 48px rgba(0,0,0,.3)}}*{{box-sizing:border-box}}
body{{margin:0;background:var(--bg);color:var(--text);font-family:Inter,"PingFang SC","Microsoft YaHei",system-ui,sans-serif}} header{{position:sticky;top:0;z-index:20;display:flex;align-items:center;justify-content:space-between;gap:18px;flex-wrap:wrap;padding:18px 24px;background:rgba(9,11,16,.94);backdrop-filter:blur(15px);border-bottom:1px solid var(--line)}}
h1{{font-size:22px;margin:0 0 4px}}.subtitle{{font-size:12px;color:var(--muted)}}select,input,button{{border:1px solid var(--line);background:var(--panel);color:var(--text);border-radius:9px;min-height:39px;padding:0 11px}}select{{min-width:310px}}.controls{{display:flex;gap:9px;flex-wrap:wrap}}
.layout{{display:grid;grid-template-columns:270px minmax(0,1fr);min-height:calc(100vh - 77px)}}aside{{position:sticky;top:77px;height:calc(100vh - 77px);overflow:auto;border-right:1px solid var(--line);padding:14px;background:#0d1017}}#taskSearch{{width:100%;margin-bottom:10px}}
.task{{display:block;width:100%;text-align:left;margin-bottom:6px;padding:9px 10px;height:auto;cursor:pointer;color:#b9c3d5;font:12px/1.4 ui-monospace,SFMono-Regular,monospace;overflow-wrap:anywhere}}.task.active{{border-color:var(--accent);background:#202a3f;color:#fff}}main{{padding:22px 24px 60px;max-width:1700px;width:100%;margin:auto}}
.hero{{display:flex;align-items:end;justify-content:space-between;gap:16px;flex-wrap:wrap;margin-bottom:18px}}.hero h2{{margin:0 0 6px;font-size:20px}}.path{{font:11px/1.4 ui-monospace,SFMono-Regular,monospace;color:var(--muted);overflow-wrap:anywhere}}.nav{{display:flex;gap:8px}}.nav button{{cursor:pointer}}
.timeline{{display:grid;grid-template-columns:repeat(auto-fill,minmax(330px,1fr));gap:18px}}.card{{background:var(--panel);border:1px solid var(--line);border-radius:14px;overflow:hidden;box-shadow:var(--shadow)}}.card.seed{{border-color:#486a5d}}.image{{position:relative;background:#06070a}}.image img{{display:block;width:100%;height:330px;object-fit:contain;cursor:zoom-in}}.badge{{position:absolute;left:10px;top:10px;border-radius:99px;padding:5px 9px;background:rgba(0,0,0,.72);font-size:12px;font-weight:700}}.body{{padding:14px}}.roundhead{{display:flex;justify-content:space-between;gap:10px;align-items:center;margin-bottom:10px}}.roundtitle{{font-size:16px;font-weight:750}}.edit{{font-size:11px;color:var(--green);border:1px solid #315c4d;border-radius:99px;padding:3px 7px}}
.flow{{font-size:11px;color:var(--muted);margin-bottom:10px}}.prompt{{border-left:3px solid var(--accent);padding:9px 10px;background:#0e121b;border-radius:7px;margin-top:9px;font-size:13px;line-height:1.55;white-space:pre-wrap}}.prompt.short{{border-color:#526078;color:#bac4d5;font-size:12px}}.label{{display:block;color:var(--muted);font-size:10px;text-transform:uppercase;letter-spacing:.08em;margin-bottom:3px}}.change{{font-size:12px;color:#d8dfec;line-height:1.5;margin-top:9px}}.missing{{height:330px;display:grid;place-items:center;color:#68758c}}
#modal{{display:none;position:fixed;inset:0;z-index:100;background:rgba(0,0,0,.93);place-items:center;padding:24px}}#modal.open{{display:grid}}#modal img{{max-width:97vw;max-height:94vh;object-fit:contain}}#modal span{{position:absolute;right:23px;top:12px;font-size:34px;cursor:pointer}}.empty{{padding:80px;text-align:center;color:var(--muted)}}
@media(max-width:800px){{header{{padding:13px}}select{{min-width:0;width:100%}}.controls{{width:100%}}.layout{{display:block}}aside{{position:static;height:auto;max-height:230px;border-right:0;border-bottom:1px solid var(--line)}}main{{padding:16px 13px 50px}}.timeline{{grid-template-columns:1fr}}.image img,.missing{{height:290px}}}}
</style></head><body>
<header><div><h1>Seed · 每轮图片 · 对应 Prompt</h1><div class="subtitle">{batch_count} 个批次 · 每批次/类别最多 {limit} 条 · 固定种子 {seed} · 共 {sample_count:,} 条完整任务</div></div><div class="controls"><select id="group"></select></div></header>
<div class="layout"><aside><input id="taskSearch" placeholder="筛选任务名…"><div id="tasks"></div></aside><main id="main"></main></div>
<div id="modal"><span>×</span><img alt="原图"></div><script id="data" type="application/json">{payload}</script>
<script>
const groups=JSON.parse(document.getElementById('data').textContent),groupSelect=document.getElementById('group'),taskSearch=document.getElementById('taskSearch'),tasks=document.getElementById('tasks'),main=document.getElementById('main'),modal=document.getElementById('modal'),modalImg=modal.querySelector('img');let taskIndex=0;
const esc=s=>String(s??'').replace(/[&<>'"]/g,c=>({{'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}}[c]));
for(const g of groups){{const o=document.createElement('option');o.value=g.id;o.textContent=`${{g.batch.replace('batch_','')}} · ${{g.workflow}} (${{g.sampled}})`;groupSelect.appendChild(o)}}
function group(){{return groups.find(g=>g.id===groupSelect.value)||groups[0]}}function filtered(){{const q=taskSearch.value.trim().toLowerCase();return group().items.filter(x=>!q||x.task.toLowerCase().includes(q))}}
function cardImage(url,label){{return `<div class="image"><span class="badge">${{esc(label)}}</span><img loading="lazy" decoding="async" src="${{esc(url)}}" alt="${{esc(label)}}" onerror="this.replaceWith(Object.assign(document.createElement('div'),{{className:'missing',textContent:'图片不存在或无法加载'}}))"></div>`}}
function renderTasks(){{const list=filtered();if(taskIndex>=list.length)taskIndex=0;tasks.innerHTML=list.map((x,i)=>`<button class="task ${{i===taskIndex?'active':''}}" data-i="${{i}}">${{esc(x.task)}} · ${{x.round_count}}轮</button>`).join('')||'<div class="empty">没有匹配任务</div>';tasks.querySelectorAll('[data-i]').forEach(b=>b.onclick=()=>{{taskIndex=Number(b.dataset.i);renderTasks();renderMain()}});renderMain()}}
function renderMain(){{const list=filtered(),x=list[taskIndex];if(!x){{main.innerHTML='<div class="empty">没有匹配任务</div>';return}}location.hash=encodeURIComponent(group().id+'~'+x.task);const seed=`<article class="card seed">${{cardImage(x.seed,'SEED')}}<div class="body"><div class="roundtitle">Seed Image</div><div class="flow">多轮编辑的原始输入图片</div></div></article>`;const rounds=x.rounds.map(r=>`<article class="card">${{cardImage(r.image,'ROUND '+String(r.round).padStart(2,'0'))}}<div class="body"><div class="roundhead"><span class="roundtitle">Round ${{String(r.round).padStart(2,'0')}}</span>${{r.edit_type?`<span class="edit">${{esc(r.edit_type)}}</span>`:''}}</div><div class="flow">${{esc(r.input_index)}} → ${{esc(r.output_index)}}${{r.source_rounds?.length?' · 来源轮次 '+esc(r.source_rounds.join(', ')):''}}</div>${{r.visible_change?`<div class="change"><span class="label">可见变化</span>${{esc(r.visible_change)}}</div>`:''}}${{r.prompt_zh_long?`<div class="prompt"><span class="label">中文 Prompt</span>${{esc(r.prompt_zh_long)}}</div>`:''}}${{r.prompt_zh_short?`<div class="prompt short"><span class="label">中文短 Prompt</span>${{esc(r.prompt_zh_short)}}</div>`:''}}${{r.prompt_en_long?`<div class="prompt"><span class="label">English Prompt</span>${{esc(r.prompt_en_long)}}</div>`:''}}${{r.prompt_en_short?`<div class="prompt short"><span class="label">English Short Prompt</span>${{esc(r.prompt_en_short)}}</div>`:''}}</div></article>`).join('');main.innerHTML=`<div class="hero"><div><h2>${{esc(x.task)}} · ${{esc(x.workflow)}} · ${{x.round_count}} 轮</h2><div class="path">${{esc(x.path)}}</div></div><div class="nav"><button id="prev">← 上一个</button><button id="next">下一个 →</button></div></div><section class="timeline">${{seed}}${{rounds}}</section>`;main.querySelectorAll('img').forEach(img=>img.onclick=()=>{{modalImg.src=img.currentSrc||img.src;modal.classList.add('open')}});main.querySelector('#prev').onclick=()=>{{taskIndex=(taskIndex-1+list.length)%list.length;renderTasks();window.scrollTo(0,0)}};main.querySelector('#next').onclick=()=>{{taskIndex=(taskIndex+1)%list.length;renderTasks();window.scrollTo(0,0)}}}}
groupSelect.onchange=()=>{{taskIndex=0;taskSearch.value='';renderTasks()}};taskSearch.oninput=()=>{{taskIndex=0;renderTasks()}};modal.onclick=()=>{{modal.classList.remove('open');modalImg.src=''}};document.addEventListener('keydown',e=>{{if(e.key==='Escape')modal.click();if(e.key==='ArrowLeft')document.getElementById('prev')?.click();if(e.key==='ArrowRight')document.getElementById('next')?.click()}});
const hash=decodeURIComponent(location.hash.slice(1)),[gid,wanted]=hash.split('~');if(groups.some(g=>g.id===gid))groupSelect.value=gid;const initial=group().items.findIndex(x=>x.task===wanted);if(initial>=0)taskIndex=initial;renderTasks();
</script></body></html>'''


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--seed", type=int, default=20260717)
    parser.add_argument("--workers", type=int, default=24)
    args = parser.parse_args()
    root = args.root.resolve()
    groups = build_groups(root, args.limit, args.seed, args.workers)
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(render(groups, args.limit, args.seed), encoding="utf-8")
    print(json.dumps({"output": str(output), "groups": len(groups), "samples": sum(g["sampled"] for g in groups)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
