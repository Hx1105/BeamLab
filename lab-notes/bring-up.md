# 实验台启用笔记

Linux x86_64、Python 3.13、Node.js 24；光场计算使用 CPU LightPipes。

## 本地两镜对准

```bash
export AGH_SOURCE_DIR=/opt/agnes-harness
bash scripts/create_env.sh
source .venv/bin/activate
export PYTHONPATH="$PWD/src"
python experiments/two_mirror_alignment.py
```
示例给两面反射镜设置偏移，通过功率测量反馈进行有限次寻优，输出对准前后的耦合率与测量次数。

## 接通智能实验服务

在仓库外安装兼容 AGH，然后在本项目已激活的 Python 环境连接：

```bash
export AGH_SOURCE_DIR=/opt/agnes-harness
(cd "$AGH_SOURCE_DIR" && pnpm install --frozen-lockfile && pnpm --filter @agnes/cli build:local && pnpm --filter @agnes/mhs build:plugin)
python scripts/attach_instruments.py
```

可先运行连接脚本并添加 `--check` 检查依赖。CLI、SDK 和 MHS 均来自这份外部安装；本项目生成的连接和运行输出由 `.gitignore` 排除。


```bash
set -a; source .env; set +a
python scripts/start.py start --base-port 44640 --instance lab
python scripts/run_instance.py --instance lab --prefix alignment_001 --case geometric --policy mimo
```
`MODEL_BASE_URL` 指向 Messages 模型服务；`DEEPSEEK_API_KEY` 为该启动器沿用的凭据变量名，模型配置请求 `mimo-v2.6-pro`。工作台端口为基准端口加 4。

## 光学与寻优检查

```bash
PYTHONPATH=src python -m pytest tests/test_physics.py tests/test_optimizer.py -q
```
