# H3 实例操作约定

程序目录 /opt/h3-suite，运行数据 /root/autodl-tmp/h3。
通过 /opt/h3-suite/venv/bin/python /opt/h3-suite/scripts/launch_session.py 启动面板与 ComfyUI。
面板127.0.0.1:8190；ComfyUI127.0.0.1:8188。禁止未经用户要求清空队列或中断生成。
PE和视频共用显存锁；使用网关提交，勿并行另起模型进程。
用户素材、视频、任务数据库、凭据和日志都不是公开镜像内容。
凭据由每个用户自行登录或配置，不写入项目文件、示例、日志或聊天输出。
镜像包含通用程序，不包含账号登录态。
