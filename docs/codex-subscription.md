# 使用 ChatGPT Plus / Codex 订阅

本仓库新增 `--backend codex`，通过官方 `codex exec` 复用 ChatGPT 登录。
不需要 OpenAI API Key，也不会在失败时回退到付费 API。原有 API 模式仍可使用。

## 准备

1. 在**实际运行 Python 的环境**安装当前版本的官方 Codex CLI：
   `npm install -g @openai/codex`。
2. 执行 `codex login`，在浏览器选择你的 ChatGPT 账号。
3. 执行 `codex login status`，确认显示 `Logged in using ChatGPT`。
4. 安装 README 中的 Python/实验依赖。完整实验仍需要 Linux、CUDA/PyTorch；写论文需要 LaTeX。

Windows PowerShell 如果阻止 `codex.ps1`，使用 `codex.cmd login` 和
`codex.cmd login status`。适配器会绕过 PowerShell 启动 npm 包，不需要更改执行策略。
WSL、容器和 Windows 的登录状态可能不同，需在运行实验的环境内确认。

仅测试适配器可使用轻量环境：

```bash
python -m venv .venv
# Linux: source .venv/bin/activate
# Windows PowerShell 可直接用 .venv\Scripts\python.exe 代替 python
python -m pip install openai anthropic backoff tiktoken jsonschema pillow funcy rich dataclasses-json psutil
python -m unittest discover -s tests -v
python -m scripts.smoke_codex
python -m scripts.smoke_codex --full
```

离线测试不调用模型。两个 smoke 命令会使用少量订阅额度；`--full` 还验证
实验管理器的结构化结果、历史消息、批量回复和图片输入。
轻量环境不足以启动完整研究流水线。

## 生成想法

从仓库根目录执行（以下使用 Bash 换行语法）：

```bash
python -m ai_scientist.perform_ideation_temp_free \
  --backend codex \
  --workshop-file ai_scientist/ideas/my_topic.md \
  --max-num-generations 1 \
  --num-reflections 3
```

先创建自己的主题 Markdown。可参考 `ai_scientist/ideas/i_cant_believe_its_not_better.md`。
生成结果写入同名 JSON。Semantic Scholar 是独立文献服务，仍可能限流；
`S2_API_KEY` 可选，Codex 登录不会替代文献服务凭据。

## 小规模实验

```bash
python launch_scientist_bfts.py \
  --backend codex \
  --config bfts_config_codex.yaml \
  --load_ideas ai_scientist/ideas/i_cant_believe_its_not_better.json \
  --idea_idx 0 \
  --skip_writeup --skip_review
```

`bfts_config_codex.yaml` 是单 worker、每阶段最多 2 次迭代的小规模配置，
用于排查环境与流程问题，不能据此认为实验充分或论文结论可靠。
不加两个 `--skip_*` 参数即可启用论文和评审；需要先安装 PDF/LaTeX 依赖。
结果仍位于 `experiments/`。跳过论文时仍会汇总图表并调用模型。

`--backend codex` 覆盖所有模型入口，包括代码中的默认 GPT/Claude 名称。
因此不用逐个设置 `--model_writeup`、`--model_citation` 等参数。
也可设置 `AI_SCIENTIST_BACKEND=codex`，供其他独立脚本复用同一后端。
单独使用 `--model codex` 只切换该入口；整条流水线请使用 `--backend codex`。

## 可选设置与限制

| 环境变量 | 用途 |
| --- | --- |
| `AI_SCIENTIST_CODEX_MODEL` | 指定订阅可用的 Codex 模型；不设置则使用 CLI 内置默认模型 |
| `AI_SCIENTIST_CODEX_TIMEOUT` | 每次调用超时秒数，默认 600 |
| `AI_SCIENTIST_CODEX_BIN` | 自定义 Codex 可执行文件路径 |
| `CODEX_HOME` | 官方 CLI 的凭据目录；由 CLI 自己管理 |

适配器在临时目录以只读沙箱运行，忽略用户 CLI 配置并禁用 shell/patch 功能，
让 AI Scientist 自己管理实验执行。原来的用户配置模型、MCP 和自定义 provider 不会自动加载。
不会读取或复制 `auth.json`；API Key 和自定义 OpenAI base URL 不会传给子进程。

- 模型输入支持文本、历史消息，以及仓库生成的 PNG/JPEG/WebP base64 图片；不下载远程图片 URL。
- 函数调用映射到 CLI JSON Schema 输出，并校验返回数据。
- 批量回复顺序执行多个独立 turn，可能比 API 模式慢。
- API 参数 `temperature`、`max_tokens`、`seed` 不映射为 CLI 参数；不能据此约束输出长度或保证可重复性。
- 超时会清理本次 CLI 子进程。额度耗尽、登录失效或输出校验失败会报错，不无限重试、不转用 API。
  上层原有阶段可能捕获错误并继续；本次改造没有增加全流程断点恢复或自动等额度功能。
- Plus 额度与其他 Codex 使用共享。订阅成本显示为 `null`，不能把 API 单价用于计算订阅费用。
- 完整实验的硬件需求、生成代码的执行风险和原有流程成功率仍然存在。

官方文档：[非交互模式](https://learn.chatgpt.com/docs/non-interactive-mode)、
[登录](https://learn.chatgpt.com/docs/auth)、[订阅额度](https://learn.chatgpt.com/docs/pricing)。
