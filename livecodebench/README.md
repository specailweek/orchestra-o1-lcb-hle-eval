# LiveCodeBench 测评

使用 O1 多智能体团队解编程题，生成 Python 程序，再通过隔离容器运行官方测试。

## 主要文件

| 文件 | 用途 |
| --- | --- |
| `run_lcb_o1.py` | 测评主入口 |
| `lcb_team_base.py` | 主、子智能体调用与工具适配 |
| `lcb_judge.py`、`testing_util.py` | 判题包装器与官方判题代码 |
| `framework/` | O1 框架源码 |
| `.env.example`、`run.sh` | 配置示例与启动脚本 |

## 如何运行

1. 准备 Linux、Python 3.12、Docker 和主、子模型服务。
2. 安装 `requirements.txt` 中的依赖，按 `.env.example` 填写本机路径和接口。
3. 自行获取官方 `release_v6` 数据，按要求整理运行目录。
4. 加载配置，执行 `bash run.sh`。程序会先做工具与判题预检。

完整命令和数据目录格式见 **[部署说明](SETUP.md)**；数据来源为 **[官方仓库](https://huggingface.co/datasets/livecodebench/code_generation_lite)**。

## 实验记录

历史实验完成 **249题，通过184题，阶段通过率约73.9%**。原计划为1,055题，此数字不是全量成绩，也不能单独证明多智能体优于单智能体。

代码不附带题目、私有测试、权重或密钥。原远程实验目录已清理，本目录为本地保留代码的整理版，尚未在迁移后的环境中重跑。
