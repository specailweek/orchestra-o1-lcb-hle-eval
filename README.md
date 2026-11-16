# Orchestra-o1: LiveCodeBench / HLE evaluation code

这是两次实验所用的 O1 测评适配代码交付包，供在其他服务器复核与继续适配。
包含 MainAgent → DelegateTaskTool → ReActAgent、记忆与轨迹处理、代码工具、判题包装器和 HLE 分层抽样代码。
**不含任何数据集、题目、标准答案、私有测试、模型权重、密钥、轨迹或实验输出。**

## 代码来源和复现边界

- `framework/`：本地保留的 Orchestra-o1 运行核心源码，仅选取 Python 源码；不含训练数据与原始账号配置。
- `livecodebench/`：本地保留的最后版本；原服务器 LCB 测评目录已按要求删除，无法逐字节核验最终运行副本。默认全量 release_v6 / 1055 题。
- `hle/`：HLE 第二轮 64K 本地代码副本，主模型为 Orchestra-o1-8B-all-ourdata，子模型为 Qwen3-Omni-30B-A3B-Instruct。
- 交付改动只涉及路径、端点和凭证配置；HLE 自动外部评分被禁用。代码清单与哈希见 `SOURCE_MANIFEST.json`。
- 这不是新服务器一键部署承诺：必须先准备模型服务、官方数据、运行目录和 Docker，并完成代码内 preflight。

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
- 将本包 `livecodebench/` 下的四个运行文件复制到该运行目录，供指纹检查和 Docker 挂载；
- `$O1_EVAL_ROOT/lcb-service/services/services.json`：连接检查状态，phase=ready，services.sub.context=65536。它不是启动模型的命令，两个端点须真实可用。

确认上述条件后 `bash run.sh lcb`。代码先执行合成输入、错误程序、隐藏测试隔离及真实主子模型调用预检，断言失败则不开始全量测评。
保留未通过题目在分母；接口/容器故障单列。历史184/249=73.9%是冻结时部分题成绩，不是1055题全量成绩；非每题都委派Omni。

## HLE

官方数据： https://huggingface.co/datasets/cais/hle （须自行获得授权）；版本 `5a81a4c7271a2a2a312b9a690f0c2fde837e4c29`。
官方评分代码： https://github.com/centerforaisafety/hle ，版本 `22ed3074b1e7b134bcbc09028d0ba320839b0655`。

```bash
mkdir -p "$O1_EVAL_ROOT/hle"
python hle/stratified_sample.py /your/authorized/test.parquet --output "$O1_EVAL_ROOT/hle/data"
```

按官方学科×模态×题型抽样20%，2500题抽500题；抽样不读取答案来选题，gold与agent输入分开保存。
在 `$O1_EVAL_ROOT/hle/` 另准备：

- `run-profile.json`：本轮模型、上下文、预算说明；
- `profile-validation.json`：`main_admission_budget`，须由本机 KV 容量预检得出（不是盲用旧机器数值）；
- `official/`：自行拉取上述固定版本评分代码。

设置自己的 Serper key 后 `bash run.sh hle`。工具包含 Python/Bash、文件操作、搜索、网页/PDF读取和真实图像分析，工具可用性预检不通过会阻止测评。

**评分与解题独立。默认不启动任何付费评分 API。**
如需评分，先配置兼容结构化输出的本地裁判，设置 O1_JUDGE_URL / O1_JUDGE_MODEL / O1_ENABLE_JUDGE，再 `bash run.sh judge`。
旧实验使用 o3-mini-2025-01-31 在线裁判；换成本地裁判后必须披露模型变化，不能声称完全复现官方评分口径。

## 注意

- Docker用于运行不可信模型代码，默认无网络、只读根目录、内存/CPU/PID限制；不要挂载宿主私有数据目录。
- 没有包含自动清理显卡、停止他人任务、下载权重或付费API启动逻辑。
- 不要把 `.env`、runtime、题目、答案、私有测试及模型权重提交到 Git。
- 本包仅做源码语法、文件清单和敏感信息检查；没有在新的硬件与模型组合上重跑基准。
- 上游源码和判题器权利归原作者；本包不擅自重新授予第三方代码许可证。建议私有仓库内进行团队复核。
