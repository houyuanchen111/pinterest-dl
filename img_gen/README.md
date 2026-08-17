# GPT Image 原子调用脚本

`src/main.py` 每次执行只发送一次 OpenAI Image API 请求。没有输入图片时执行生成；提供一个或多个 `--image` 时执行编辑。返回的所有图片和本次请求的 JSON 元数据都会写入 `output/`。

## 目录约定

```text
img_gen/
├── prompt/
│   ├── sys/       # 全局策略、风格控制、变化策略
│   └── user/      # 单个具体图像任务
├── src/main.py
└── output/        # 运行时生成，Git 忽略
```

Prompt 使用 UTF-8 Markdown。Image API 没有独立的 system role，因此脚本会把两个文件合并为带有 `SYSTEM GUIDANCE` 和 `USER REQUEST` 标记的单个 prompt。这样仍可分别维护全局策略和具体任务。

## 安装

```bash
python -m pip install -r img_gen/requirements.txt
```

脚本默认读取 `img_gen/api/api.json`：

```json
{
  "_type": "newapi_channel_conn",
  "key": "YOUR_API_KEY",
  "url": "https://your-api-host.example.com"
}
```

`url` 如果没有以 `/v1` 结尾，脚本会自动补全。可通过 `--api-config path/to/another.json` 使用其他配置文件；也兼容 `api_key` 和 `base_url` 字段名。API key 不会出现在 dry-run 输出或请求元数据中。

可用 `OPENAI_IMAGE_MODEL` 覆盖默认模型；未设置时使用 `gpt-image-2`。

## 生成

先检查最终请求，不调用 API：

```bash
python img_gen/src/main.py \
  --user-prompt sunlight_surface \
  --sys-prompt diversity \
  --n 4 \
  --size 1536x1024 \
  --quality high \
  --dry-run
```

执行一次请求并返回四张图：

```bash
python img_gen/src/main.py \
  --user-prompt sunlight_surface \
  --n 4 \
  --size 1536x1024 \
  --quality high \
  --prefix sunlight_surface
```

`--user-prompt sunlight_surface` 会解析为 `prompt/user/sunlight_surface.md`；`--sys-prompt diversity` 同理。也可以直接传任意 Markdown 文件路径。默认自动使用 `prompt/sys/diversity.md`，如不需要可传 `--no-system-prompt`。

## 编辑

单图编辑：

```bash
python img_gen/src/main.py \
  --user-prompt path/to/edit_prompt.md \
  --image path/to/input.png \
  --input-fidelity high \
  --quality high
```

多参考图编辑：

```bash
python img_gen/src/main.py \
  --user-prompt path/to/edit_prompt.md \
  --image path/to/reference_1.png \
  --image path/to/reference_2.png \
  --input-fidelity high
```

带 mask 编辑：

```bash
python img_gen/src/main.py \
  --user-prompt path/to/edit_prompt.md \
  --image path/to/input.png \
  --mask path/to/mask.png
```

## 原子行为

- 一次进程只调用一次 `images.generate` 或 `images.edit`。
- `--n` 控制该请求返回的图片数量，不会在脚本内循环发请求。
- 成功时 stdout 只输出 JSON 结果，便于被 shell、队列或上层程序调用。
- 失败时错误写入 stderr，并以非零状态退出。
- 每次请求都会保存一个同名前缀的 `.json` 元数据文件，记录模型、参数、完整合并 prompt、输入路径和输出路径。
