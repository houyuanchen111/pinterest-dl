# 单图天空/海面/光影/写实风格筛选器

输入一张 PNG、JPG 或 JPEG 图片，调用 VLM 独立判断：

1. 图片是否同时清楚出现天空和海面；
2. 图片是否具有**极其明显**的戏剧化光影效果；
3. 图片是否属于真实摄影/高度写实风格。

只有三项都通过时，输出中的 `all_pass` 才会是 `true`。

## 使用方法

```bash
cd /mnt/aigc/houyuanchen/pinterest-dl/vlm
./sh/classify_image.sh /path/to/image.jpg
```

保存结果到 JSON 文件：

```bash
./sh/classify_image.sh /path/to/image.png --output output/result.json
```

也可以直接运行 Python：

```bash
python3 src/classify_image.py /path/to/image.jpg
```

## 输出示例

```json
{
  "image": "/absolute/path/to/image.jpg",
  "model": "qwen3.5-plus",
  "has_sky_and_sea": true,
  "extreme_light_shadow": false,
  "photorealistic": true,
  "all_pass": false,
  "reasons": {
    "sky_and_sea": "画面上方可见天空，下方可见连续海面。",
    "light_shadow": "整体为普通自然光，没有极强明暗反差或戏剧化投影。",
    "photorealistic": "画面具有自然的摄影纹理、透视和材质细节。"
  }
}
```

## 配置

- 默认 API 配置：`api/qwen_3_5_plus.json`
- 默认提示词：`prompt/image_scene_classifier_zh.md`
- 可通过环境变量 `VLM_API_KEY` 覆盖配置文件中的 API Key。
- 可用 `--api-config`、`--system-prompt`、`--timeout` 和 `--retries` 覆盖默认参数。

光影判定采用默认否决策略：普通日照、日落色彩、柔光、轻微阴影或一般水面反光都会判为 `false`；只有强烈且主导画面的明暗、方向光、投影、高光或光束才可能通过。
