# 小智控制台 · 让小爱音箱用你的 AI 回答

> 把小米小爱音箱的回答大脑换成你自己的大模型，**不刷机、不破坏小米智能家居**。
> 附 macOS 桌面控制台 + 局域网浏览器版，手机也能用。

![Python](https://img.shields.io/badge/Python-3.12-blue) ![macOS](https://img.shields.io/badge/macOS-arm64%20%7C%20Intel-lightgrey) ![License](https://img.shields.io/badge/license-MIT-green) ![无刷机](https://img.shields.io/badge/%E5%88%B7%E6%9C%BA-%E2%9D%8C%20%E5%AE%89%E5%85%A8-brightgreen)

---

## 它是什么

小爱音箱的语音链路是 **ASR（小米）→ 大脑 → TTS（小米）**。
这个项目**只换掉中间那一环**：

```
你对着音箱说话
      ↓
[小米 ASR]      ← 原生，不动
      ↓
[大脑]  ──→  ★ 换成你自己的大模型  ★
      ↓
[小米 TTS]     ← 原生，不动（所以还是小爱那个声音，最大声、零延迟）
      ↓
音箱出声
```

### 为什么智能家居一点都不会丢

这是整个方案成立的前提。读了 xiaogpt 源码后确认：

```python
# xiaogpt/xiaogpt.py:183
def need_ask_gpt(self, record):
    query = record.get("query", "")
    return (
        self.in_conversation and not query.startswith("小爱同学")
        or query.lower().startswith(tuple(k.lower() for k in self.config.keyword))
    )
```

**只有含触发词的指令才会转给 AI。**
「打开客厅灯」不含触发词 → `need_ask_gpt()` 返回 False → 根本不进 AI 分支 →
小爱原生照常执行米家云端的设备控制。

`mute_xiaoai: true` 也**只在 AI 真要回答时**才暂停小爱播报。

> **结论：米家 App、全部设备、场景联动、定时任务，一条都不会少。**

---

## 效果

| 你说的话 | 谁来响应 |
|---|---|
| 「小爱同学，**帮我**介绍一下量子计算」 | **你的 AI**（智能体回答） |
| 「小爱同学，**请问**今天天气怎么样」 | **你的 AI** |
| 「小爱同学，开始持续对话」 | **你的 AI**（进入连续对话，说「结束持续对话」退出） |
| 「小爱同学，打开客厅灯」 | 小爱原生（智能家居照常） |
| 「小爱同学，播放音乐」 | 小爱原生 |

---

## 快速开始

### 前提

- 一台小米小爱音箱（本项目在 **Xiaomi Sound / L16A** 上实测，其他型号需自行确认）
- 一台常开着的 Mac 或 Linux 机器（跑引擎，音箱通过局域网访问它）
- Python 3.11+
- 一个 MiniMax API Key（[平台地址](https://platform.minimaxi.com/user-center/basic-information/interface-key)）
- 小米账号（**小米ID**，不是手机号 —— 米家 App → 我的 → 个人信息 → 小米ID）

### 三步

```bash
git clone <本仓库地址>
cd <仓库目录>

# ① 装依赖（必须用 locked 组，理由见「坑」一节）
uv venv --python 3.12 xiaogpt-env
VIRTUAL_ENV=$PWD/xiaogpt-env UV_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple \
  uv pip install "xiaogpt[locked]" miservice_fork

# ② 配置（交互式向导，引导填小米账号和 MiniMax key）
cp .env.example .env && chmod 600 .env
./xiaogpt-env/bin/python tools/setup_wizard.py

# ③ 跑起来
./run.sh
```

跑起来后对着音箱说「小爱同学，帮我介绍一下量子计算」，它就会用你的智能体回答。

---

## 控制台

两种形态，**同一套界面**：

| | 桌面版 | 浏览器版 |
|---|---|---|
| 启动 | `./console/build_app.sh` → `dist/小智控制台.app` | `./console/run_server.sh start` |
| 技术 | pywebview + WKWebView | 标准 HTTP 服务 |
| 访问 | 本机 | 局域网（手机也能开） |

七个页面：

- **控制台** —— 引擎状态、一键体检、最近对话
- **说话** —— 手动让音箱说一句话（可选 MiniMax 音色 / 小爱原生音色）
- **智能体** —— API key、模型、人设
- **音色** —— 音色来源、响度、试听；**克隆音色**（需账号开通克隆权限）
- **触发词** —— 哪些话转给 AI、哪些留给小爱，含实时判定器
- **历史** —— 音箱对话记录
- **设置** —— 路径、音箱音量、**MIoT 设备控制**（麦克风静音 / 勿扰 / 唤醒 / 云端播报）

浏览器版启动后终端会打印带令牌的地址：

```
  http://<你的局域网IP>:8848/?token=xxxxxxxxxxxxxxxxxxxx
```

同一个 Wi-Fi 下用手机打开即可。令牌存 `~/.xiaozhi/access_token`（600）。

---

## 目录结构

```
├── mybot/                  自有智能体（核心）
│   ├── minimax_agent.py    大模型对话
│   ├── minimax_tts.py      TTS 合成（含降级策略）
│   └── main.py             引擎入口 + 运行期补丁（触发词/排除词/唤醒词）
├── console/                控制台
│   ├── app.py              桌面版 API 桥（pywebview）
│   ├── server.py           浏览器版 HTTP 服务
│   ├── backend/            业务模块（miot / xiaomi / voices / engine …）
│   ├── web/                界面（两个版本共用同一套）
│   ├── build_app.sh        打包成 macOS .app
│   └── selftest.py         自检
├── tools/                  探测与运维脚本
├── docs/                   使用文档
├── config.yaml             运行时配置（注释完整）
├── setup.sh / run.sh       一键配置 / 启动
└── .env                    凭据（600，**不入库**）
```

### tools/ 里的脚本

| 脚本 | 干什么 |
|---|---|
| `setup_wizard.py` | 一站式配置向导 |
| `list_devices.py` | 列出账号下所有设备，查 miotDID |
| `check_minimax.py` | 模型侧自检 |
| `patch_l16a.py` | 给 venv 打硬件指令补丁 |
| `miot_login_probe.py` | 打印 MIoT 验证链接 / `--save` 写回 token |
| `miot_probe.py` | 扫 MIoT spec 全表 |
| `miot_rw.py` | 读/写/调 MIoT 属性与动作 |
| `miot_home_scan.py` | 全屋设备只读扫描 |
| `voice_clone.py` | 音色克隆全流程 |
| `record_voice.sh` | 录人声素材 |
| `tts_preview.py` | 逐个试听音色 |
| `speaker_test.py` | 拉对话记录、验证出声 |
| `bench_model.py` | 模型延迟对比 |

---

## 配置说明

全部配置在 `config.yaml`，带完整注释。常用的几项：

```yaml
# 设备。miotDID 留空会按型号自动匹配；也可以用环境变量 MI_DID 覆盖
hardware: "L16A"
mi_did: ""

# 后端。MiniMax 中国大陆区用 api.minimaxi.com，国际区用 api.minimax.io
# ⚠️ key 不能跨区域用
api_base: https://api.minimaxi.com/v1
openai_key: ""        # 建议放 .env 的 MINIMAX_API_KEY

# 模型
gpt_options:
  model: MiniMax-M3
  extra_body:
    thinking:
      type: disabled   # M3 能关思考；M3.1-Flash 不能，写了直接 400

# 语音来源：mi = 小爱原生（更响、零延迟）；minimax = MiniMax 合成（音色可选）
tts: mi
tts_options:
  model: speech-2.6-hd
  voice: female-shaonv
  vol: 4               # 响度增益，每翻一倍 +6dB，超过 5 会削波

# 智能家居音量（音箱自己的旋钮，改喇叭增益，不破音）
speaker_volume: 85
```

### 环境变量

| 变量 | 作用 |
|---|---|
| `MINIMAX_API_KEY` | 大模型 / TTS 密钥（放 `.env`） |
| `MI_USER` / `MI_PASS` | 小米账号 |
| `MI_DID` | 音箱 miotDID，覆盖 `config.yaml` |
| `MI_DEVICE_ID` | 固定 deviceId（小米风控按设备） |
| `SPEAKER_IP` | 音箱局域网 IP（健康检查用） |

---

## 踩过的坑

### 引擎侧

1. **必须装 `xiaogpt[locked]` 而不是 `xiaogpt`**。官方默认装 langchain 1.4.x，
   而 `langchain.memory` 在 langchain 0.3+ 已被移除，`import` 阶段就报
   `ModuleNotFoundError: No module named 'langchain.memory'`，根本起不来。
2. **L16A 不在 xiaogpt 的硬件指令表里**，会回落到错误的 `5-1` 指令。
   `tools/patch_l16a.py` 会补上 `L16A: ("7-3", "7-4")`（与 L17A 同代 MiOS）。
3. **`thinking` 必须走 `extra_body` 且被摊平到请求体顶层**，
   否则思考过程会混进正文被中文念出来。
4. **MiniMax-M3 能关思考，M3.1-Flash 不能** —— 后者写 `disabled` 直接 HTTP 400。
5. **账号异地 / 海外 IP 登录必然 `Login failed`** → 在本地登录一次，
   token 缓存到 `~/.mi.token`，之后复用。
6. **同一时间只能跑一个引擎**，两个会同时轮询小米云，音箱答两遍。

### MIoT 侧

7. **MIoT 需要单独过一次真人身份验证**（图形验证码 + 短信，自动化做不了）。
   `serviceLogin?sid=xiaomiio` 返回 `70016`；
   `serviceLoginAuth2` 返回成功但 `securityStatus=16` + `notificationUrl` ——
   密码是对的，只是新 sid 要单独验证一次。跑 `tools/miot_login_probe.py` 拿链接。
8. **prop/get 有会话内缓存** → 每次操作换新进程，或用 ubus 交叉验证。
9. **code=0 ≠ 生效**。实测扬声器静音、休眠模式、循环模式云端都收下但设备忽略。
   写操作只在确认状态变化后才敢说生效。
10. **写入约 30 秒后才在云端可见**（设备属性上报有周期）。

### 音色侧

11. **音色克隆不能直接传音频** → 必须先 `POST /v1/files/upload` 拿 file_id。
12. **克隆音色 7 天不用会被平台自动删** → 克隆完立刻合成一句激活。
13. **响度差不在系统音量，在源音频电平**。两条链路共用同一音量通道，
    小爱原生母带更响；MiniMax 侧靠 `vol` 补，实测 **vol=4 是甜点**（+11.9dB，0 削波），
    再往上服务端归一化会让收益饱和。

---

## 文档

| 文档 | 内容 |
|---|---|
| [docs/部署指南.md](docs/部署指南.md) | 设备识别、依赖、配置、故障排查 |
| [docs/浏览器版使用说明.md](docs/浏览器版使用说明.md) | 局域网版部署、访问保护、手机适配 |
| [docs/音色克隆操作指南.md](docs/音色克隆操作指南.md) | 音色克隆：在哪录、怎么提交、报错对照 |

---

## 当前状态

**已跑通**：

- 引擎常驻，音箱语音往返实测通过（唤醒词路由正确）
- MIoT 通道打通（可读写麦克风静音、勿扰、音量、唤醒、云端播报）
- 桌面 App 可打包（ad-hoc 签名），浏览器版实测可用
- 控制台自检全部通过

**未完成**：

- **音色克隆需要平台开通权限**（否则接口返回 `2038`）→ 需实名认证 + 申请
- **摄像头取帧未走通** → 需实现小米 P2P 协议
- **launchd 开机自启未实测**

---

## 安全约定

- **API key 只放 `.env`（600），绝不写进 `config.yaml`，绝不进 App 包**
- 小米 token 在 `~/.mi.token`（600），**只读不写** ——
  `miservice` 的 `login()` 失败时会**把 token 文件删掉**，那份 token 很贵
- 仓库里的文档中所有 token 都是 `<...>` 占位符

```bash
chmod 600 .env ~/.mi.token
```

---

## 致谢与参考

- [yihong0618/xiaogpt](https://github.com/yihong0618/xiaogpt) —— 本项目的基础
- [miservice_fork](https://pypi.org/project/miservice_fork/) —— 小米云 API 封装
- [miot-spec.org](https://miot-spec.org) —— MIoT 设备规格（L16A 有公开 spec）
- [Home Assistant xiaomi_miot](https://github.com/al-one/hass-xiaomi-miot) —— 想让 AI 也控家电时用

## License

MIT —— 见 [LICENSE](LICENSE)。