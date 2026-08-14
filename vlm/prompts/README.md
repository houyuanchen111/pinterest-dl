# Prompt profiles

每一种图片筛选任务由一组同名文件组成：

```text
prompts/
├── task_name.json
└── task_name.zh.md
```

- `task_name.zh.md`：给 VLM 的系统提示词，定义筛选标准和 JSON 输出格式。
- `task_name.json`：profile 配置，关联提示词并声明接口需要校验的输出字段。

## Profile 格式

```json
{
  "id": "task_name",
  "description": "任务说明",
  "system_prompt_file": "task_name.zh.md",
  "user_prompt": "检查图片并只返回 JSON。",
  "validation": {
    "required_boolean_fields": ["pass"],
    "required_nonempty_string_fields": ["reason"],
    "pass_field": "pass"
  }
}
```

字段路径支持点号，例如 `reasons.style`。

如果最终通过结果由多个条件共同决定，可增加：

```json
{
  "pass_field": "all_pass",
  "pass_from": ["condition_a", "condition_b"]
}
```

接口会重新计算 `all_pass`，避免模型返回的汇总值与子条件不一致。

新增任务时可复制：

- `templates/single_condition.json`
- `templates/single_condition.zh.md`
