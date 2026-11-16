# LiveCodeBench / Orchestra-o1 测评代码

本文件夹可独立复制使用，包含所需的框架源码、运行入口、配置示例和工具容器定义，不依赖另一个基准目录。

先进入本目录：`cd livecodebench`。不包含数据集、模型权重、密钥、轨迹或实验输出。

## 运行环境

Linux、Python 3.12、Docker Engine、可调用的两个 OpenAI-compatible 本地模型服务。模型服务与评测 Python 环境可分开安装。

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
docker build -f Dockerfile.tools -t o1-eval-tools:share .
cp .env.example .env
# 修改 .env 中自己的路径、端点，然后：
source .env
```

历史配置：LCB 主模型 40960 / 子模型 65536 上下文，主输出8192 / 子输出4096；HLE双方65536上下文，主输出16384 / 子输出8192。
主模型 HLE 64K 使用 Qwen3 YaRN factor=2；不要将它套用于其他架构。上下文预检和 KV admission budget 必须按新服务器实测。

## LiveCodeBench

官方下载仓库： https://huggingface.co/datasets/livecodebench/code_generation_lite

固定数据版本 `0fe84c3912ea0c4d4a78037083943e8f0c4dd505`（release_v6）。官方判题器版本 `28fef95ea8c9f7a547c8329f2cd3d32b92c1fa24`。
本包保留官方 `testing_util.py`，不包含题目和测试。
在 `$O1_EVAL_ROOT/lcb-service/full-v6-r2/` 准备：

- `data/public.json`：官方六个 JSONL 文件顺序拼接后去除 private_test_cases 的1055行；
- `data/private/<platform>-<question_id>.json`：各行原始字节（含行尾），只允许隔离判题器读取；
- `data/manifest.json`：public_sha256、files（各文件 name/count/bytes/sha256），全部通过校验后写入 `data/READY`；
- 将本文件夹下的 `run_lcb_o1.py`、`lcb_team_base.py`、`lcb_judge.py`、`testing_util.py` 四个运行文件复制到该运行目录，供指纹检查和 Docker 挂载；
- `$O1_EVAL_ROOT/lcb-service/services/services.json`：连接检查状态，phase=ready，services.sub.context=65536。它不是启动模型的命令，两个端点须真实可用。

确认上述条件后 `bash run.sh`。代码先执行合成输入、错误程序、隐藏测试隔离及真实主子模型调用预检，断言失败则不开始全量测评。
保留未通过题目在分母；接口/容器故障单列。历史184/249=73.9%是冻结时部分题成绩，不是1055题全量成绩；非每题都委派Omni。

## 注意

- Docker用于运行不可信模型代码，默认无网络、只读根目录、内存/CPU/PID限制；不要挂载宿主私有数据目录。
- 没有包含自动清理显卡、停止他人任务、下载权重或付费API启动逻辑。
- 不要把 `.env`、runtime、题目、答案、私有测试及模型权重提交到 Git。
- 本包仅做源码语法、文件清单和敏感信息检查；没有在新的硬件与模型组合上重跑基准。
- 上游源码和判题器权利归原作者；本包不擅自重新授予第三方代码许可证。建议私有仓库内进行团队复核。
