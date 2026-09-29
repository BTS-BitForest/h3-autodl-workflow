# H3 工作流：Agent 部署与维护规程

## 目标和边界

部署当前发布版本的完整工作流，保留 LoRA、PE、超分、工作流模板及面板功能。不要替换为官方最小工作流，不要擅自升级 torch/transformers，不要将本工程称为 AutoDL 镜像二进制。不要把机器的任何账号配置、API key、SSH 私钥、浏览器 profile、任务数据库或用户素材提交 Git。

## 预检

1. 确认用户提供的是目标机器的 SSH 地址，核对主机指纹；密码交互输入，禁止写入命令、日志或文档。
2. 只读检查 `uname -m`、`cat /etc/os-release`、`nvidia-smi`、`df -h / /root/autodl-tmp`、现有 `/opt/h3-suite`、端口 8188/8190。
3. 要求 Ubuntu 22.04/x86_64、root、独立数据盘，系统盘空闲20GiB、数据盘空闲100GiB以上。实际已验证4090 24GB及驱动595.71.05，运行库CUDA13。基础镜像CUDA标签不能证明驱动兼容。
4. 若已存在服务/队列，先确认运行状态。不得 kill 全局 Python、清空队列、删除模型或重启正在生成的服务。已有安装直接走启动入口。

## 标准安装

从可信 GitHub Release 获取 `deploy-linux.sh` 并审阅后执行 `bash deploy-linux.sh`。版本固定为 v2026.09.30。仓库已公开，脚本通过 HTTPS 匿名下载，不需要 GitHub 凭证。不要从开发机器拷贝 gh、Codex 或 DSH 登录态。

Windows 用户完整解压 `H3-OneClick-Windows.zip`，运行 BAT，输入 SSH 命令。脚本调用同一 Linux 入口，转发本机8190/8188并使用一次性浏览器票据打开面板，无需手工 token.txt。不要把票据记录到部署报告中。

需要手工部署时：

```bash
# 在仓库根目录，确保已安装 curl；附件目录放数据盘
python3 install/download_assets.py --repo BTS-BitForest/h3-autodl-workflow --tag v2026.09.30 --output /root/autodl-tmp/h3-deploy/assets
python3 install/install.py --assets /root/autodl-tmp/h3-deploy/assets --check-only
python3 install/install.py --assets /root/autodl-tmp/h3-deploy/assets
/opt/h3-suite/venv/bin/python /opt/h3-suite/scripts/launch_session.py
python3 /opt/h3-suite/scripts/watch_download.py
```

安装器以 `install/assets.json` 校验所有运行环境附件大小和 SHA256；缺件、损坏、额外 Python 分卷均失败，禁止绕过。环境安装过程不具备事务回滚。中断后保留目录便于诊断，不自动清理。确认为本次空白实例安装产生的半成品后，再由操作者决定重建实例或移走半成品重新安装，不能误删已有生产环境。

## 目录

| 路径 | 内容 |
|---|---|
| `/opt/h3-python` | 固定 Python/基础运行库 |
| `/opt/h3-suite` | ComfyUI、节点、网关、工作流、PE、下载器、venv、agent程序 |
| `/root/autodl-tmp/h3` | 模型、素材、成片、网关状态、缓存及日志 |
| `/root/autodl-tmp/h3-deploy` | 安装下载缓存和源代码 |
| `/工具包/H3控制端.zip` | 日常 Windows EXE 启动器和辅助文件 |

AutoDL 系统镜像不包含数据盘内容，所以镜像恢复后需要重新下载模型。不要因为数据盘模型未进入镜像而声称用户获得了免下载模型包。

## 下载与服务验收

模型清单 `suite/bootstrap/models.json` 共33文件，87466198780字节。下载源固定提交版本与SHA，优先魔搭对应版本，HF镜像/官方回退。下载器清除通用代理，4并发，分块续传，监督器有限重试。

- `bootstrap/status.json` 应最终 `state=complete`；核对文件总数、大小、最终SHA校验。进度速度是近期聚合网络流量，校验阶段速度可能为0。
- 服务只监听回环地址。通过 SSH 访问面板，不把8188直接映射公网。
- `launch_session.py` 幂等启动并生成一次性票据，不重启已有服务。下载未完成阻止生成是预期行为。
- 在用户允许的测试范围内验证素材上传、PE开/关、PE自动续接、统计、复用、取消、耗时、超分和原音轨。测试会产生任务/素材/日志，不应进入公开镜像。
- 使用日志明确定位GPU驱动错误、模型下载错误或网关错误，不将全部失败归因于代理。

## 网络与凭证

DSH/Codex只有程序和通用模板。各用户自行登录/输入自己的API。外网访问需要真实可用的网络线路，不能承诺任意 VPN 开关状态都可连通。日常EXE可按用户本机代理建立桥接；部署脚本仅负责SSH端口转发，不代替该代理桥接。勿全局把所有流量永久指向用户私有代理。

GitHub CLI主账号凭证不得放到镜像。EXE保存密码仅限用户主动勾选后本机DPAPI，不分发 `login.dpapi` 或 `connection.json`。

## 发布或镜像保存前

只使用程序目录白名单。排除数据盘、token.txt、auth.json、.env、.ssh、.config/gh、API配置、浏览器状态、生成日志和历史。先扫描候选文件再发布；不能只依赖gitignore。保留第三方许可证；没有许可的集成文件不能擅自宣布MIT。用户已明确授权当前仓库公开，所有上传内容必须适合公开分发。

随包原工作流通过新实例真实生成和超分测试；部署脚本语法/逻辑测试不等同于空白实例安装验收。报告必须区分两者。
