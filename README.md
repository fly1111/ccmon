# ccmon

一眼看清所有正在运行的 Claude Code 会话：**哪个在跑、哪个卡在等你确认、哪个已经停了。**

同时开好几个 Claude Code 终端时，某个会话弹出权限确认框后就会静默卡住，直到你偶然切过去才发现。ccmon 常驻后台，用系统托盘和桌面宠物两种方式把这件事推到你眼前。

## 它是怎么知道的

Claude Code 自己就在实时维护一份会话注册表 —— `~/.claude/sessions/<pid>.json`：

```json
{"pid":28860,"sessionId":"979b3ecf-...","cwd":"D:\\ComfyUI","name":"comfyui-e1",
 "status":"waiting","waitingFor":"approve Bash","updatedAt":1786594264809}
```

ccmon 只是读它。**不改你的 `settings.json`，不装 hook，不往 Claude 的执行路径里插任何代码。**

因此：装 ccmon 之前就开着的会话照样能监控；ccmon 崩了重启后状态自动恢复；对 Claude 本身零性能影响、零阻塞风险。

状态映射（取值固定于 claude-code 源码 `src/screens/REPL.tsx`）：

| 注册表 `status` | `waitingFor` | ccmon 状态 |
|---|---|---|
| `busy` | — | 运行中 |
| `waiting` | `approve <工具名>` | **等待授权** ← 核心 |
| `waiting` | `input needed` | 等待输入 |
| `waiting` | `dialog open` | 等待选择 |
| `waiting` | `worker request` / `sandbox request` | 等待授权 |
| `idle` | — | 空闲 |
| 文件消失 | — | 已退出 |
| 文件在但进程没了 | — | 异常退出 |

子 agent 不会出现在列表里 —— Claude 的 `registerSession()` 本来就不给它们建档，正好是我们想要的粒度。

## 安装

```bash
cd D:\vscodepro\ccmon
uv venv --python 3.11 .venv
uv pip install -e ".[dev,tray,pet]"
```

## 用法

```bash
ccmon ps              # 打印一次当前所有会话
ccmon ps -w           # 持续刷新（Ctrl-C 退出）
ccmon ps -w -n 0.5    # 自定义刷新间隔
ccmon tray           # 只起系统托盘
ccmon pet            # 只起桌面宠物
ccmon both           # 托盘 + 宠物一起
```

输出示例：

```
状态        项目       PID    更新  详情
----------  ---------  -----  ----  --------
! 等待授权  dingyue    31204  8s    需要授权: Bash
* 运行中    ComfyUI    28860  2s    Edit: nodes.py
- 空闲      michang    27510  12m   空闲
```

## 开机自启

```bash
python -m scripts.install     # 写入 HKCU\...\Run，开机自动 ccmon both
python -m scripts.uninstall   # 移除
```

使用 `pythonw.exe` 启动，不闪控制台。

## 桌面宠物

右下角默认位置。左键拖动、双击打开会话列表、右键菜单。状态直接读 Claude 注册表：

- **红色感叹号** ← 你最该看的：会话在等你点授权
- **橙** ← 进程异常退出
- **黄问号** ← 等你说话 / 弹窗打开
- **蓝** ← 在干活
- **灰** ← 空闲

宠物没有图也能跑 —— `ccmon/ui/pet/fallback_sprite.py` 用 Pillow 直接画一个显示器小人。若你想用 AI 生成专属形象，把 PNG 放到 `%LOCALAPPDATA%\ccmon\assets\pet\<state>.png`（如 `needs_approval.png`、`running.png`），宠物会自动优先用它们。

## 配置 webhooks

桌面提醒 + 提示音是免费的。手机推送通过一个通用 webhook 配置 —— Bark / Server酱 / Telegram / Discord / 企微机器人 都用同一份 schema。在 `ccmon/notify/webhook.py` 写一个 `WebhookConfig`，触发时机是 `NEEDS_APPROVAL`、`ERROR`、`NEEDS_INPUT` 之一或多个。

关键设计：**手机推送延迟 60 秒才发**。如果托盘/宠物已经提醒过你，60 秒后你还没响应（说明你不在电脑前），才升级到手机。不打扰是核心。

占位符：`{title}` `{message}` `{status}` `{project}` `{cwd}` `{session_id}` `{pid}` `{tool}` `{waiting_for}` `{timestamp}`，以及 `{env.XXX}` 读环境变量（让 token 不进配置文件）。

## 开发

```bash
uv venv --python 3.11 .venv
uv pip install -e ".[dev,tray,pet]"
.venv/Scripts/python.exe -m pytest -q
```

测试用合成的注册表快照驱动状态机，不需要真的开 Claude 会话就能验证全部状态转移，包括半截 JSON、pid 复用、非法文件名、webhook 重试等边界情况。**47 个测试覆盖 state classification、registry 容错、状态机、webhook 渲染与重试。**

## 架构原则

- **状态来自 `~/.claude/sessions/<pid>.json`** —— Claude 自己就在实时维护，ccmon 只读。无需任何 hook。
- **不写你的 `~/.claude/settings.json`** —— 你已配好的 `guard.sh`/`audit.sh` 不会被碰。
- **pid 是主键**（`session_id` 在 `/resume` 时会被原地改写）。
- **子 agent 天然不出现** —— Claude 的 `registerSession()` 本就不给它们建档。

## 非目标

- **不做远程批准/拒绝** —— 绝不向会话注入按键或输入。只展示状态 + 跳转到窗口。
- 不解析或展示对话内容。
- 不修改任何 Claude Code 自身状态。
- 不联网上报，webhook 是唯一出站通道且完全由你配置。
