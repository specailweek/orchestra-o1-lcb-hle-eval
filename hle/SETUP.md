# HLE / Orchestra-O1

使用 O1 主智能体和 Omni 子智能体完成综合知识、推理及图像题。支持 Python/Bash、文件操作、联网搜索、网页/PDF 读取和图像分析；最终答案在独立阶段评分。

## 文件说明

| 文件 | 用途 |
| --- | --- |
| `stratified_sample.py` | 按官方学科、模态和题型抽样20% |
| `run_hle_o1.py` | 主子模型调度、解题工具与进度保存 |
| `hle_runtime.py` | 代码执行、tokenizer 与请求容量控制 |
| `hle_team_base.py` / `hle_schemas.py` | O1 适配与动作格式约束 |
| `hle_judge.py` | 独立答案评分，默认禁用 |
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

## 数据与抽样

官方数据：[cais/hle](https://huggingface.co/datasets/cais/hle)。请先取得访问授权，再自行下载；不要将原始数据或抽样结果上传到本仓库。

固定数据修订：`5a81a4c7271a2a2a312b9a690f0c2fde837e4c29`。该版本包含 2,500 题，按官方学科 × 文本/图像 × 题型联合分层，固定 seed=42 抽取 500 题。每个非空分层至少保留一题，小分层的抽样比例可能略高于20%。

```bash
mkdir -p "$O1_EVAL_ROOT/hle"
python stratified_sample.py /absolute/path/to/test.parquet \
  --output "$O1_EVAL_ROOT/hle/data"
```

输出的 `sample.public.json` 用于解题，`sample.gold.json` 只用于独立评分；`sampling-report.json` 记录题目ID、配额和统计权重。抽样规则不依据答案正确与否选题。

## 运行前配置

在 `$O1_EVAL_ROOT/hle/` 准备：

- `run-profile.json`：模型、上下文、预算及运行方式说明。
- `profile-validation.json`：至少包含实测得到的 `main_admission_budget`，即并发请求可预留的总 token 预算。
- `official/`：自行拉取 [HLE 官方代码](https://github.com/centerforaisafety/hle) 的修订 `22ed3074b1e7b134bcbc09028d0ba320839b0655`，评分脚本需读取其中 `hle_eval/run_judge_results.py`。

历史主、子模型服务窗口均为 65,536，输出上限分别为 16,384 / 8,192 tokens。主模型使用 Qwen3 YaRN factor=2 扩展窗口；这不是重新训练，不能直接套用于其他架构。主、子模型的实际容量应先验证，当前代码内的子模型准入预算也须随新服务容量核对。

搜索工具需要自己的 `SERPER_API_KEY`，并将 `SERPER_BASE_URL` 配置为对应服务地址。搜索可能产生费用；不要在仓库中保存真实凭证。

## 解题与评分

启动解题：

```bash
bash run.sh
```

代码先验证真实容器执行、Omni 图像分析、搜索及答案导出。预检通过后先运行固定的10题兼容性试测；只要产生有效最终答案，就继续剩余题目，不按准确率筛选是否继续。

运行目录中的 `status.json` 记录进度，`results/` 保存每题轨迹，`predictions.json` 汇总模型提交的最终答案。支持检查点恢复，正式评分不能把未完成或缺失评分的题静默当作已完成。

**评分默认关闭，不会随解题入口自动调用付费裁判。** 如使用本地裁判，配置其兼容接口与模型名称后，显式启动：

```bash
export O1_JUDGE_URL="http://127.0.0.1:18009/v1"
export O1_JUDGE_MODEL="your-local-judge-model"
export O1_ENABLE_JUDGE=1
bash run.sh judge
```

裁判必须支持代码使用的结构化输出，并先通过正误样例检查。历史实验使用在线 `o3-mini-2025-01-31`；换成本地模型后须披露裁判变化，不能声称与官方默认裁判完全一致。裁判不参与解题，也不等同于训练阶段的奖励模型。

## 结果口径与限制

报告应包含已尝试、已作答、已评分、答对和超时数量，并区分已评分准确率与完整样本成绩。500题分层样本不等于全量2,500题，也不能直接与无工具单模型榜单比较；总体估计还需考虑分层权重。

本版本整理了历史64K实验代码，完成源码检查，尚未在新硬件和模型服务上重跑。数据、权重、密钥、评分结果和轨迹均需留在自己的运行目录中。
