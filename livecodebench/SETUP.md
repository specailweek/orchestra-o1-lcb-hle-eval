# LiveCodeBench / Orchestra-O1

使用 O1 主、子智能体解决编程题，将最终 Python 程序提交给独立容器，使用官方判题器测试。解题工具只能接触公开题目与样例，私有测试仅交给判题器。

## 文件说明

| 文件 | 用途 |
| --- | --- |
| `run_lcb_o1.py` | 主子模型调度、代码工具、检查点恢复与判题控制 |
| `lcb_team_base.py` | 对原 O1 框架的工具反馈与模型调用适配 |
| `lcb_judge.py` | 隔离判题包装器 |
| `testing_util.py` | 保留的官方判题器源码 |
| `token_gate_selftest.py` | 不调用模型的并发准入测试 |
| `framework/` / `shared/` | 框架核心与环境配置 |

## 环境准备

需要 Linux、Python 3.12、Docker Engine，以及两个可访问的本地模型服务。先进入本文件夹，再执行：

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
docker info
docker build -f Dockerfile.tools -t o1-eval-tools:share .
cp .env.example .env
# 编辑 .env，填写自己的绝对路径与模型接口，然后加载：
source .env
```

主要配置项：

| 变量 | 含义 |
| --- | --- |
| `O1_EVAL_ROOT` | 本地运行目录，建议使用绝对路径 |
| `O1_MAIN_MODEL_PATH` | 主模型本地权重或 tokenizer 路径 |
| `O1_SUB_MODEL_PATH` | 子模型本地权重或 tokenizer 路径 |
| `O1_MAIN_URL` / `O1_SUB_URL` | 两个 OpenAI-compatible 服务的 `/v1` 地址 |
| `O1_LOCAL_API_KEY` | 本地模型服务使用的访问密钥 |
| `O1_TOOL_IMAGE` | 运行代码工具的 Docker 镜像名称 |

本版本请求中的模型名称固定为 `Orchestra-o1-8B-all-ourdata` 和 `Qwen3-Omni-30B-A3B-Instruct`，服务端须提供对应别名。更换模型还须核对 tokenizer、上下文、采样配置和动作协议，不能只改 URL。

## 准备官方数据

- 数据来源：[LiveCodeBench code_generation_lite](https://huggingface.co/datasets/livecodebench/code_generation_lite)。
- 本版本面向 `release_v6`，共 1,055 题。
- 固定数据修订：`0fe84c3912ea0c4d4a78037083943e8f0c4dd505`。
- 官方判题器修订：`28fef95ea8c9f7a547c8329f2cd3d32b92c1fa24`。

仓库不附数据下载结果或现成数据清单。自行获取官方六个 JSONL 文件后，在 `$O1_EVAL_ROOT/lcb-service/full-v6-r2/` 准备以下结构：

```text
full-v6-r2/
├── run_lcb_o1.py
├── lcb_team_base.py
├── lcb_judge.py
├── testing_util.py
└── data/
    ├── public.json
    ├── private/<platform>-<question_id>.json
    ├── manifest.json
    └── READY
```

将本文件夹对应的四个 Python 文件复制到上面的运行目录，供指纹检查和容器挂载使用。

`public.json` 按官方六个文件顺序拼接全部题目，并去掉 `private_test_cases`；`private/` 保存每题原始行字节（含行尾）。`manifest.json` 必须包含 `public_sha256` 和 `files`，每个源文件记录 `name`、`count`、`bytes`、`sha256`；完成完整性校验后才创建 `READY`。详细校验逻辑见 `run_lcb_o1.py`。

模型服务就绪后，在 `$O1_EVAL_ROOT/lcb-service/services/services.json` 记录 `phase: ready` 和 `services.sub.context: 65536`。此文件只是状态记录，不能替代真实的模型服务。不要用虚假就绪状态跳过部署检查。

## 启动与查看结果

```bash
bash run.sh
```

运行前会检查标准输入、文件持久化、隐藏测试隔离、正确与错误程序，并实际调用原 O1 主子智能体。预检失败不会启动全量任务。

主要输出位于运行目录：`status.json` 保存进度和结果；`results/` 保存每题事件、轨迹、最终程序与判题记录。基础设施错误与正常解题失败分别记录，0分题保留在分母。

历史配置为主模型 40,960、子模型 65,536 上下文，单次输出上限分别为 8,192 / 4,096 tokens。并发数与 KV 预算须按实际服务容量设置，不能照搬原服务器的显存预留值。

## 结果口径与限制

这是单次提交的 agentic pass@1，不等同于官方原生模型榜单的生成协议。主模型可以直接提交，并非每题都调用 Omni。

历史冻结时完成 249/1,055 题，其中 184 题通过，已完成部分通过率约 73.9%；这是阶段结果，不代表全量成绩。原远程实验目录已删除，此处为本地保留源码，尚未在迁移后的环境中重新验证全流程。

工具和判题容器默认无网络、只读根目录并限制 CPU、内存及 PID。不要将私有数据目录额外挂载给解题容器，也不要将运行数据或凭证提交到仓库。
