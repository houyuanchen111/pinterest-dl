# 统一单图 VLM 筛选接口

`src/classify_image.py` 是统一的单图分类入口。业务筛选规则不再写死在 Python 中，而是通过 `prompts/` 下不同的 prompt profile 控制。

## 查看可用任务

```bash
cd /mnt/aigc/houyuanchen/pinterest-dl/vlm
python3 src/classify_image.py --list-prompts
```

## 使用指定 prompt

```bash
python3 src/classify_image.py \
  /path/to/image.jpg \
  --prompt sky_sea
```

Shell 入口的参数完全相同：

```bash
./sh/classify_image.sh \
  /path/to/image.png \
  --prompt sky_sea \
  --output output/result.json
```

## 批量筛选目录

`src/filter_directory.py` 可以并发筛选整个目录、断点续跑、生成按相似度排序的
`summary.jsonl`，并把通过图片复制到单独目录。

筛选暖色逆光花草女性人像：

```bash
python3 src/filter_directory.py \
  /path/to/images \
  --prompt backlit_floral_portrait \
  --output-dir output/backlit_floral_portrait \
  --workers 16 \
  --min-score 75 \
  --copy-passed
```

- 单图结果：`output/backlit_floral_portrait/results/`
- 排序汇总：`output/backlit_floral_portrait/summary.jsonl`
- 通过图片：`output/backlit_floral_portrait/passed/`
- 失败记录：`output/backlit_floral_portrait/errors.jsonl`
- 修改 prompt 后缓存会自动失效；使用 `--refresh` 可强制全部重跑。

`--prompt` 支持三种形式：

1. `prompts/` 下的 profile 名称；
2. profile JSON 文件路径；
3. 原始 Markdown prompt 路径。原始 Markdown 模式只校验模型返回的是 JSON 对象。

## 输出

接口统一添加以下元数据：

```json
{
  "image": "/absolute/path/to/image.jpg",
  "model": "qwen3.5-plus",
  "prompt": "sky_sea"
}
```

随后合并所选 prompt 任务定义的结果字段。现有天空/海面任务仍保持原有的 `has_sky_and_sea`、`extreme_light_shadow`、`photorealistic`、`all_pass` 和 `reasons` 字段。

## 新增筛选任务

每个任务使用两个文件：

```text
prompts/
├── my_task.json
└── my_task.zh.md
```

1. 在 Markdown 中定义视觉筛选标准和 JSON 输出格式。
2. 在 JSON profile 中声明提示词文件、用户指令和结果校验规则。
3. 运行时传入 `--prompt my_task`。

完整格式参见 `prompts/README.md` 和 `prompts/templates/single_condition.*`。

## 其他参数

- `--user-prompt`：临时覆盖 profile 中的用户指令。
- `--system-prompt`：兼容旧调用，直接加载原始 Markdown prompt。
- `--api-config`：指定 API 配置文件。
- `--timeout`：请求超时秒数。
- `--retries`：模型返回无效结果时的最大尝试次数。
- `--output`：额外保存 JSON 结果。
- 环境变量 `VLM_API_KEY`：覆盖 API 配置中的 Key。
