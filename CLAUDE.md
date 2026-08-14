# ccmon — Claude Code 会话状态监视器

一眼看清所有正在运行的 Claude Code 会话：**哪个在跑、哪个卡在等你确认、哪个已停**。托盘图标 + 桌面宠物两种视图共享同一个引擎。

## 技术栈

- **Python 3.11**（`pyproject.toml` 要求 `>=3.11`）
- **PySide6** — 桌面宠物窗口（无边框半透明置顶）
- **pystray** — 系统托盘
- **winotify** — Windows 通知中心 toast
- **Pillow** — 图像处理（fallback sprite + AI 资源抠图）
- **mmx**（mmx CLI）— AI 立绘生成，需联网
- **pytest** — 测试，74 个 case 全过

## 项目结构

```
D:\vscodepro\ccmon\
├── pyproject.toml
├── README.md
├── CLAUDE.md                   ← 你正在读的这个
├── ccmon\
│   ├── models.py               # State 枚举 + Session dataclass
│   ├── registry.py             # 读 ~/.claude/sessions/<pid>.json
│   ├── transcript.py           # 反向读 transcript 拿"当前在干什么"
│   ├── state_machine.py        # 状态机 (pure)
│   ├── engine.py               # 1.5s 轮询 scanner + Tick 事件
│   ├── paths.py                # %LOCALAPPDATA%\ccmon + ~/.claude 路径
│   ├── ide_lock.py             # 读 ~/.claude/ide/*.lock 找 VS Code 窗口
│   ├── win\
│   │   ├── activate.py         # pid → 窗口 → 前台（核心跳转逻辑）
│   │   └── autostart.py        # HKCU\...\Run 注册
│   ├── notify\
│   │   ├── toast.py            # winotify 通知 + "跳转到窗口" 按钮
│   │   ├── sound.py            # winsound 系统声音
│   │   ├── webhook.py          # 通用 webhook (Bark/Telegram/Discord/企微)
│   │   └── dedupe.py           # Notifier 编排 + 60s 升级 + 冷却
│   ├── ui\
│   │   ├── app.py              # pystray 托盘入口
│   │   ├── tray.py             # Pillow 渲染托盘图标
│   │   └── pet\
│   │       ├── window.py       # PySide6 宠物窗口
│   │       ├── fallback_sprite.py  # Pillow 实时绘制 dalmatian
│   │       ├── sprite_loader.py    # 多风格 loader (项目 + 用户)
│   │       ├── run.py              # 宠物 entry (含 signal handler)
│   │       └── assets\             # 空 — 真正的 pet 风格在 D:\vscodepro\ccmon\assets\pet\
│   └── __main__.py             # ccmon / ccmon ps|tray|pet|both
├── assets\pet\                 # AI 生成的宠物立绘（项目随版本控制）
│   ├── .gitkeep
│   ├── peter\                  # male dalmatian (default)
│   └── peter2\                 # cute chibi (备选)
├── scripts\
│   ├── gen_pet_sprites.sh      # mmx 生成新风格 + 抠图
│   ├── remove_white_bg.py      # 通用抠图（chroma key + outline flood 兜底）
│   ├── install.py / uninstall.py  # autostart 注册/注销
│   └── install.py
├── tests\                      # 74 个 case
└── docs\                       # 暂空
```

## 核心架构原则

### 1. 状态来自 `~/.claude/sessions/<pid>.json`，**零 hook**

Claude Code 自己就在实时维护会话注册表，ccmon 只读不写。装 ccmon 之前就开着的会话照样能监控，ccmon 崩了重启后状态自动恢复。

### 2. `pid` 是稳定主键

`session_id` 在 `/resume` 时会被原地改写（见 `concurrentSessions.ts:101-103`）。DB 用 `(pid, started_at)` 联合主键。

### 3. 子 agent 天然不出现

`registerSession()` 开头 `if (getAgentId() != null) return false`，所以注册表里只有顶层会话。无需过滤。

### 4. 状态机：raw → enum

| Claude Code 写 | `State` | 含义 |
|---|---|---|
| `status="busy"` | `RUNNING` | 正在干活 |
| `status="waiting"` + `waitingFor="approve <Tool>"` | `NEEDS_APPROVAL` | ★ 卡在权限确认 |
| `status="waiting"` + `waitingFor="input needed"` | `NEEDS_INPUT` | 等你说话 |
| `status="waiting"` + `waitingFor="dialog open"` | `DIALOG` | 弹窗打开 |
| `status="idle"` | `IDLE` | 空闲 |
| 文件在，进程没了 | `CRASHED` | 崩溃/被杀 |
| 文件消失 | `EXITED` | 正常退出 |

`status` 缺失时（`BG_SESSIONS` 特性关）= `UNKNOWN`，**绝不能当 IDLE**。

## 宠物资源管理

### 查找顺序

`sprite_loader._asset_dirs_for(style)` 返回 `[project_dir, user_dir]`：
1. `<project>/assets/pet/<style>/` — git 跟踪，clone 下来就有
2. `<%LOCALAPPDATA%>/ccmon/assets/pet/<style>/` — 用户本地覆盖

用户可以丢同名文件到 user 目录来覆盖项目的版本。

### 风格命名规范

每个风格一个子目录，里面 5 张 `<state>_alpha.png`（状态名：`happy`/`anxious`/`sad`/`sleepy`/`alert`）。

```
assets/pet/peter/
├── happy.png        # 原图 (mmx 输出)
├── happy_alpha.png  # 抠图后 (实际加载)
├── anxious.png / anxious_alpha.png
├── sad.png     / sad_alpha.png
├── sleepy.png  / sleepy_alpha.png
└── alert.png   / alert_alpha.png
```

加新风格：`bash scripts/gen_pet_sprites.sh <name> "<描述>"` 然后在宠物右键菜单选中。

### 当前风格

- **peter** — male dalmatian，stocky build、thick neck、broad chest
- **peter2** — cute chibi，原版（用户保留旧版作为对照）
- `_builtin` — Pillow 实时绘制的 dalmatian，有呼吸/眨眼/摇尾动画，CPU 接近 0

## AI 立绘生成流程

1. `bash scripts/gen_pet_sprites.sh <style> "[描述]"` 调用 mmx
2. prompt 含 "plain solid bright neon green background (#00FF00 chroma key green screen)" —— **绿幕比白幕好抠**
3. 5 个状态各生成 1 张 512×512 参考图，subject-ref 锁角色
4. `scripts/remove_white_bg.py` 抠图：自动检测边框颜色，**绿幕→chroma key，白底→outline flood**
5. mmx 右下角有 logo 戳 —— 抠图前先裁掉 60×60 像素

```bash
bash scripts/gen_pet_sprites.sh peter "Strong male dalmatian, stocky muscular build, thick neck"
```

## 用户偏好

- **UI 文字用中文**，代码注释用英文（参考 `dingyue` 风格）
- **托盘/宠物仅 Windows**（Linux/macOS 暂不支持）；**CLI 跨平台**（`ccmon ps` + webhook 在 Ubuntu/Debian 上可跑，零 GUI 依赖）
- **宠物 peter 命名**：用户自己起的名字，不要改
- **不在 toast 按钮上下太多功夫**（winotify pipe 回调在 Windows 上不稳定），优先 hover 气泡 + 宠物本体交互
- 资源放工程目录（git 跟踪），不放 `%LOCALAPPDATA%`

## 重要 bug 教训

### winotify pipe 冲突

Notifier 的 Listener 绑到固定命名 pipe，旧进程残留会冲突。**用 per-pid AUMID**：

```python
aumid = f"ccmon.callbacks.{os.getpid()}"
registry = Registry(app_id=aumid, force_override=True)
```

### pystray Windows 后端

`pystray.Menu(callable)` 在 Windows 上 `__bool__` 检测失败，菜单会被认为空：

```python
# ✗ 错误
tray_icon = pystray.Icon(..., menu=pystray.Menu(lambda i: _menu(i, ...)))

# ✓ 正确
tray_icon = pystray.Icon(..., menu=pystray.Menu(*_menu(...)))
```

### Ctrl+C 退出

Qt 在 Windows 下拦截 SIGINT，Python signal handler 不会触发。**用 Win32 SetConsoleCtrlHandler + os._exit**：

```python
@ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_ulong)
def _win_console_handler(ctrl_type):
    os._exit(0)
    return True
ctypes.windll.kernel32.SetConsoleCtrlHandler(_win_console_handler, True)
```

`os._exit` 是 C-level 立即终止，绕过所有 daemon 线程。

### `_run_pet` 漏调 `engine.start()`

只 `engine = Engine()` 不 start scanner，pet 收不到 Tick。**显式调**：

```python
engine = Engine(interval=1.5)
engine.start()  # ← 不能漏
return run_pet(engine)
```

## 宠物互动 (Phase 1 + Phase 2)

| 互动 | 触发 | 内部逻辑 | 文件位置 |
|---|---|---|---|
| 摸头 (heart) | mousePress 500ms+ | `_long_press_timer` + `_start_petting` | `window.py` `mousePressEvent` |
| 睡眠 (Zzz) | 鼠标停 ≥5min | `_check_hover` 检测 `_last_active_at` 差值 | `window.py` `_check_hover` |
| 激光笔 (红点) | 鼠标速度 > 800 px/s | `_check_hover` 算 dt 距离/时间 | `window.py` `_check_hover` |
| 自动避让 | 前景窗口覆盖 >50% | Win32 GetForegroundWindow + 1s 轮询 | `window.py` `_check_avoid` |
| 跟 active window | foreground hwnd 变化 | 30% 漂移到新窗口中心 | `window.py` `_drift_toward` |
| Walk-around | 鼠标停 5s | 已有；走 + 停 3s + 走回 | `window.py` `_update_walk_state` |
| 数字键跳 | 1-9 键 | 跳到 priority 第 N 个 session | `window.py` `keyPressEvent` |
| 单击跳 | mouseRelease（无 drag） | `_jump_to_attention` | `window.py` `mouseReleaseEvent` |
| 双击菜单 | mouseDoubleClick | 取消单击 + 打开菜单 | `window.py` `mouseDoubleClickEvent` |

所有 painter 绘制在 `paintEvent` 内 `try/finally` 包好，确保 `painter.end()` 一定调用（修了之前的"called with active painter"警告）。新增 Qt 类（QColor、QFont、QEasingCurve）必须加到 PySide6.QtGui / QtCore import。

## 常用命令

```bash
# 安装
uv venv --python 3.11 .venv
uv pip install -e ".[dev,tray,pet]"

# 跑
./.venv/Scripts/python.exe -m ccmon ps              # 命令行
./.venv/Scripts/python.exe -m ccmon both           # 托盘 + 宠物

# 测试
./.venv/Scripts/python.exe -m pytest -q             # 74 个 case

# 自启
./.venv/Scripts/python.exe -m scripts.install      # 写入 HKCU\...\Run
./.venv/Scripts/python.exe -m scripts.uninstall

# AI 立绘
bash scripts/gen_pet_sprites.sh <style> "[描述]"

# 调试：测跳转
./.venv/Scripts/python.exe -c "
from ccmon.win.activate import jump_to_session
jump_to_session(28860, r'D:\ComfyUI')
"
```

## Git

推送到 gitee：`git@gitee.com:fan-linya/ccmon.git`

```bash
git add -A && git commit -m "..." && git push
```

用户配置：fly65 / fly1111。
