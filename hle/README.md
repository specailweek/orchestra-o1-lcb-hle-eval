# HLE / Orchestra-o1 测评代码

本文件夹可独立复制使用，包含所需的框架源码、运行入口、配置示例和工具容器定义，不依赖另一个基准目录。

先进入本目录：`cd hle`。不包含数据集、模型权重、密钥、轨迹或实验输出。

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

## HLE

官方数据： https://huggingface.co/datasets/cais/hle （须自行获得授权）；版本 `5a81a4c7271a2a2a312b9a690f0c2fde837e4c29`。
官方评分代码： https://github.com/centerforaisafety/hle ，版本 `22ed3074b1e7b134bcbc09028d0ba320839b0655`。

```bash
mkdir -p "$O1_EVAL_ROOT/hle"
python stratified_sample.py /your/authorized/test.parquet --output "$O1_EVAL_ROOT/hle/data"
```

按官方学科×模态×题型抽样20%，2500题抽500题；抽样不读取答案来选题，gold与agent输入分开保存。
在 `$O1_EVAL_ROOT/hle/` 另准备：

- `run-profile.json`：本轮模型、上下文、预算说明；
- `profile-validation.json`：`main_admission_budget`，须由本机 KV 容量预检得出（不是盲用旧机器数值）；
- `official/`：自行拉取上述固定版本评分代码。

设置自己的 Serper key 后 `bash run.sh`。工具包含 Python/Bash、文件操作、搜索、网页/PDF读取和真实图像分析，工具可用性预检不通过会阻止测评。

**评分与解题独立。默认不启动任何付费评分 API。**
如需评分，先配置兼容结构化输出的本地裁判，设置 O1_JUDGE_URL / O1_JUDGE_MODEL / O1_ENABLE_JUDGE，再 `bash run.sh judge`。
旧实验使用 o3-mini-2025-01-31 在线裁判；换成本地裁判后必须披露模型变化，不能声称完全复现官方评分口径。

## 注意

- Docker用于运行不可信模型代码，默认无网络、只读根目录、内存/CPU/PID限制；不要挂载宿主私有数据目录。
- 没有包含自动清理显卡、停止他人任务、下载权重或付费API启动逻辑。
- 不要把 `.env`、runtime、题目、答案、私有测试及模型权重提交到 Git。
- 本包仅做源码语法、文件清单和敏感信息检查；没有在新的硬件与模型组合上重跑基准。
- 上游源码和判题器权利归原作者；本包不擅自重新授予第三方代码许可证。建议私有仓库内进行团队复核。
