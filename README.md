# Orchestra-O1：LiveCodeBench 与 HLE 测评

本仓库整理了基于 Orchestra-O1 多智能体框架的两套测评代码，供研究复核和迁移到其他服务器使用。

主智能体负责规划、委派与汇总；子智能体负责执行子任务和使用工具。最终提交由独立判题或评分流程处理，不向解题智能体提供隐藏测试与标准答案。

## 选择一个基准

| 文件夹 | 任务与评分方式 | 使用说明 |
| --- | --- | --- |
| [livecodebench/](livecodebench/) | 编程解题；提交程序后，在隔离容器中运行官方测试 | [LiveCodeBench README](livecodebench/README.md) |
| [hle/](hle/) | 综合知识与推理，包含图像题；解题后单独评分 | [HLE README](hle/README.md) |

两个文件夹各自包含框架源码、配置示例、依赖列表、Dockerfile 和运行入口，可单独复制使用。运行前仍须准备自己的模型服务和官方数据。

```text
.
├── livecodebench/           # LiveCodeBench 测评
│   ├── framework/          # Orchestra-O1 框架核心
│   ├── shared/             # 路径、端点及凭证读取
│   ├── run_lcb_o1.py        # 解题与隔离判题入口
│   ├── lcb_judge.py        # 判题包装器
│   ├── testing_util.py     # 官方判题器
│   └── README.md
├── hle/                    # HLE 测评
│   ├── framework/
│   ├── shared/
│   ├── run_hle_o1.py        # 解题入口
│   ├── stratified_sample.py# 分层抽样
│   ├── hle_judge.py         # 独立评分入口
│   └── README.md
└── SOURCE_MANIFEST.json     # 文件清单与校验哈希
```

## 模型与运行方式

历史实验使用 `Orchestra-o1-8B-all-ourdata` 作为主模型、`Qwen3-Omni-30B-A3B-Instruct` 作为子模型。配置示例面向两个已部署的 OpenAI-compatible 模型服务；仓库不负责下载权重或启动推理服务。

本版本没有将独立奖励模型接入解题流程。HLE 的最终答案裁判属于测评阶段，与训练奖励模型不同。评分脚本默认关闭，须显式配置并启动；解题工具中的联网搜索也需要自行提供服务凭证。

## 数据与凭证

**仓库不包含数据集、题目、标准答案、私有测试、模型权重、API 密钥、实验轨迹或结果文件。**

请从各基准官方来源获取数据。HLE 须按官方要求取得访问授权。将本地配置写入 `.env`，不要将凭证或生成的运行目录提交到 Git。`.gitignore` 已排除常见数据和权重文件。

## 复现范围

这是历史测评代码的整理版本，已将机器专属路径和凭证改为配置项。本次交付进行了 Python 语法、文件清单、哈希及凭证模式检查，并通过了并发准入逻辑的本地测试；没有在新服务器上重新运行模型测评。

LiveCodeBench 原远程实验目录已清理，本地副本无法与该运行目录逐字节复核。历史阶段成绩不能作为本仓库在任意新模型或新硬件上的效果保证。两个基准均应先完成环境与工具预检，再开始正式运行。

上游框架与判题代码的权利归原作者；保留原代码中的作者标识，本仓库不另行授予第三方源码许可证。
