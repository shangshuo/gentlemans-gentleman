# Gentleman's Gentleman

**English** · [简体中文](#简体中文)

> Inspired by the British butler — **you should not have to write that automation which wakes you up.**

You say what you want in one plain sentence; the system turns it into a runnable policy.
At runtime a fast discrimination model reads your sensors and external facts, decides whether the
house is *in the state you named*, and deterministic code then decides whether to act.

```
config time : write an idea → get a draft → you review → trial run → save
runtime     : trigger → sensor snapshot → context → model verdict → threshold → action → full trace
```

## What this repository is

An **internal-beta release repo**: it contains only the part that lands on your Home Assistant —
the folded integration directory `custom_components/gentlemans_gentleman/` (the decision kernel
`butler` is already inside it) plus `hacs.json`. Architectural decisions, domain-model derivations
and the development process live in the private development repository, not here.

## Install (HACS custom repository)

1. HACS → three dots → **Custom repositories** → add this repository, category **Integration**.
2. Find *Gentleman's Gentleman* → Download. **Restart Home Assistant** afterwards.
3. Settings → Devices & Services → Add Integration → search "Gentleman's Gentleman".
4. Configuration takes two steps: first the **discriminator** (Jev by default; endpoint, model and
   key are all editable, and each is probed with a real call while you type it), then the
   **compiler** (any OpenAI-compatible endpoint, including Ollama/vLLM on your own box; you may
   also skip it for now).
5. The policy list is **empty** after install — deliberately. Open this integration's *Configure*
   page, choose "➕ Add a smart control from a sentence", tick which devices it may touch, write
   one plain sentence, and follow the wizard.

> Copying this repository's `custom_components/` directory straight into
> `<config>/custom_components/` works just as well (no zip assets during the beta, see ADR-0016).

## What works today

| Item | Status |
|---|---|
| Decision kernel (`butler`, four ports) | ✅ done, 103 offline unit tests green |
| Discriminator (Jev) + external facts (Open-Meteo) | ✅ end-to-end against the real APIs, both directions |
| OpenAI-compatible compiler (incl. local models) | ✅ done; **compile quality not yet measured**, see boundaries |
| HA shell: config flow, sensor, redacted diagnostics | ✅ 37 end-to-end tests green on HA 2024.12.5 and 2026.9.3 |
| Policy CRUD + trial-run / threshold wizard | ✅ done |
| Real-hardware check (an actual light turning on) | ✅ proven back in v0.1; to be re-run on v0.2 |

## Known boundaries (stated up front)

- **Two API keys**: one for the discriminator (Jev by default, `api.typesafe.ai`), one for the
  compiler (may be left empty for a local endpoint). Home Assistant stores integration config in
  a local plaintext file (`.storage/`, mode 0644) — that is a platform mechanism an integration
  cannot encrypt around. What we can promise: the key never appears in logs, never in traces, and
  is always redacted in an exported diagnostics bundle. **Do not put a long-lived token on a shared
  machine or one whose backups others can read.**
- **The compiler can be wrong.** It only produces a **draft**: a draft has no standing to act, and
  nothing takes effect until you review, trial-run and save it. It can also only pick from the
  devices you ticked — anything outside that set voids the whole draft. There is no
  "compiled, therefore installed" in this version.
- **One action per policy** (the v0.2 shape boundary). Write "turn on the light and close the
  curtain" as two policies — they run in the same decision round, and when both point at the same
  device the later verdict wins *with a recorded override*, never silently.
- **Models misjudge.** Before anything is executed there is a deterministic action gate: the
  candidate-entity set, mutex groups, debounce cooldowns, and a safe fallback when the model is
  unreachable. **The AI never drives a device directly.**
- **Judgement quality depends on your sensors.** A missing sensor is not a defect, it is the
  precondition for this product to exist at all — but fewer sensors means less confidence.
  Measured: Chinese phrasing scores about 0.1 lower than English, so calibrate the threshold on
  your own site.
- **What we are measuring during the beta.** Every "add a smart control from a sentence" writes one
  line into `traces/` on your own HA box (the **first 30 characters** of your sentence, which model
  answered, whether the draft validated, whether you finally saved it). That is how we learn whether
  the compiler actually works, instead of guessing. The data stays on your machine; exporting a
  diagnostics bundle is something you click, and keys are redacted.
- This is a **beta**: no promise of backwards compatibility for the config format (a v0.1
  configuration migrates automatically to the v0.2 field names).

## If something misbehaves

Settings → Devices & Services → Gentleman's Gentleman → **Diagnostics** (three dots → Download
diagnostics data). You will see what each policy last decided, with what confidence, on which
readings, plus latency and model version; keys are redacted. The trace file lives at
`<config>/traces/gentlemans_gentleman.jsonl` — one event per line.

## Language

Partly, and here is exactly which part. The setup wizard — both configuration steps, every wizard
step, field names, descriptions and error messages — follows your Home Assistant language; English
and 简体中文 ship in the box (`translations/en.json`, `translations/zh-Hans.json`), and any other
language falls back to English.
**Not translated yet:** the five buttons of the policy menu, the three trial-run choices, the
delete confirmation, and the `run_cycle` service description are hard-coded Chinese in this build.
They are being moved into the same translation mechanism; until then an English-only installation
will show those few strings in Chinese.

---

# 简体中文

> 灵感来自英国管家——**你不用再写那条会照醒你的自动化**。

你说一段想要什么，系统把它变成一条能跑的策略；运行期由快速判别模型读传感器与外部事实，
判断屋子当前**是不是**你说的那个状态，再由确定性的代码决定要不要动手。

```
配置期：写一段想法 → 出草稿 → 你核对 → 试跑 → 保存
运行期：触发 → 读数快照 → 上下文 → 模型判断 → 阈值 → 执行动作 → 全程留痕
```

## 这个仓库是什么

**内测发布仓**：只放"装到你 Home Assistant 上的那一份"——折叠好的集成目录
`custom_components/gentlemans_gentleman/`（决策内核 `butler` 已经在里面）与 `hacs.json`。
架构决策、领域模型推导与开发过程在本地开发仓，不在这个仓里。

## 怎么装（HACS 自定义仓库）

1. HACS → 三个点 → **自定义仓库** → 添加本仓库，类别选 **Integration**。
2. 找到 Gentleman's Gentleman → 下载。装完**重启 Home Assistant**。
3. 设置 → 设备与服务 → 添加集成 → 搜 "Gentleman's Gentleman"。
4. 配置分两步：先接**判别器**（默认 Jev，端点/模型/Key 都可改，当场真发一次探活），
   再接**编译器**（任何 OpenAI 兼容端点，包括你本机跑的 Ollama/vLLM；也可以先跳过）。
5. 装完列表是**空的**——这是有意的。到集成的"配置"页里，选"➕ 用一段想法新增一条智能控制"，
   勾上这条策略可以碰哪些设备，写一句人话，剩下的走向导。

> 直接把这个仓库的 `custom_components/` 目录整体拷进 HA 的 `config/custom_components/`
> 也一样能装（内测阶段不发 zip 资产，见 ADR-0016）。

## 现在能做什么

| 项 | 状态 |
|---|---|
| 决策内核（`butler`，四个端口） | ✅ 离线单测 103 例全绿 |
| 判别器（Jev）＋外部事实（Open-Meteo） | ✅ 真实 API 端到端双向跑通 |
| OpenAI 兼容编译器（含本机部署） | ✅ 已落地；**编译质量尚未测量**，见下方边界 |
| HA 薄壳：配置流程、状态实体、诊断脱敏 | ✅ 37 例端到端测试在 HA 2024.12.5 与 2026.9.3 上全绿 |
| 策略增删改查＋试跑与调阈值向导 | ✅ 已落地 |
| 真机验证（在你的 HA 上真的开灯） | ✅ v0.1 时期跑通，v0.2 待复跑 |

## 已知边界（现在就说清楚）

- **要两把 Key**：判别器一把（默认 Jev，`api.typesafe.ai`），编译器一把（本机端点可以留空）。
  Home Assistant 把集成配置存在本地明文文件里（`.storage/`，权限 0644），这是平台既有机制、
  集成无法加密。我们能承诺的是：它不进日志、不进留痕、导出的诊断包必然脱敏，我们也不经手。
  **别在共享机器或别人能读到备份的机器上放长期令牌。**
- **编译器可能编错**。它产出的只是**草稿**：草稿没有资格执行任何动作，必须你核对、试跑、
  保存之后才生效；而且它只能在你勾选的设备里挑，越界整份作废。这一版没有"编好自动装上"。
- **一条策略一个动作**（v0.2 的形状边界）。"开灯并把窗帘关上"请写成两条策略——它们会在
  同一轮判断里各自跑，指向同一设备时按后判定的生效，并且一定留下覆盖记录，不会静默。
- **模型会判错**。动手前有一道确定性的动作门：候选实体集、互斥组、防抖冷却，以及模型不可达
  时的安全回退——**AI 从不直接控制设备**。
- **判断能力依赖你手上的传感器**。没有某个传感器不是缺陷，是这个产品存在的前提；
  但传感器越少，判断的把握度越低，中文问法实测比英文低约 0.1，阈值要按你自己的现场调。
- **内测期我们在量一件事**：每次"用一段想法新增策略"都会在 HA 本机 `traces/` 里留下一行
  （意图的**前 30 个字**、用的哪个模型、过没过校验、你最后保存了没有）。这是为了知道编译器
  到底行不行，而不是靠猜。数据只在你本机，导出诊断包是你主动点的，密钥必然脱敏。
- 本版本是**内测**，不承诺配置格式向后兼容（v0.1 的配置会自动迁移到 v0.2 的字段名）。

## 出问题先看哪里

设置 → 设备与服务 → Gentleman's Gentleman → **诊断**（三个点 → 下载诊断数据）。
里面能看到每条策略最后一次判成了什么、把握度多少、依据哪些读数、耗时与模型版本；
密钥已脱敏。留痕文件在 HA 配置目录的 `traces/gentlemans_gentleman.jsonl`，一行一个事件。

## 界面语言

**只做了一半，说清楚是哪一半。** 配置向导——接入判别器与编译器的两步、流程向导的每一步、
字段名、说明与错误提示——跟随你的 Home Assistant 语言，英文与简体中文都在包里
（`translations/en.json`、`translations/zh-Hans.json`），其他语言回退到英文。
**还没收进翻译机制的**：策略主菜单那五个选项、试跑的三个选择、删除确认，以及 `run_cycle`
服务的说明文字，这一版仍是写死的中文。它们正在往同一套机制里搬；搬完之前，纯英文安装也会
在这几处看到中文。
