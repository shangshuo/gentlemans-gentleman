"""配置流程：判别器 → 编译器 → 建 entry；以及策略的增删改查与试跑向导。

四条写在这里就不要再问的约束：

**验通与探活是同一个动作的两遍用法。** A2 要求"首次启用必须通过验证测试才可用"，
而实测冷连接的首个判别器请求要 2.1–2.6s。这里在流程里真打一次：既证明 Key 有效，
也让用户把 TLS 握手的钱付在填表时。编译器的探活问的是另一件事——"这个端点会不会只
回一个词"，因为爱写散文的端点第一次编译就会失败，那个错误该在填表时暴露。

**报错分得清，才不用把异常文本甩给用户。** 401、超时、DNS 不通落到三个不同的错误键
上（A2 P7）。

**所有增删改都写进 `entry.data["policies"]`，真值只有一处。** 一台 HA 一个 entry——
要加一条智能控制是往列表里加一项，**不是**再装一次集成（ADR-0013）。

**向导每一步只接受 `user_input`。** HA 是按 `async_step_<id>(user_input)` 调下一步的，
任何"顺手多传一个参数"的写法都会在用户提交表单的那一刻被拆穿——所以挑哪条策略、
正在核对哪份草稿，全都放在 self 上。
"""
from __future__ import annotations

import logging
import urllib.error
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.helpers.selector import (EntitySelector, EntitySelectorConfig,
                                            NumberSelector,
                                            NumberSelectorConfig,
                                            NumberSelectorMode, SelectSelector,
                                            SelectSelectorConfig,
                                            SelectSelectorMode, TextSelector,
                                            TextSelectorConfig, TextSelectorType)

from .butler.adapters import JevProvider, OpenAICompatCompiler
from .butler.draft import validate_draft
from .butler.models import Policy
from .butler.ports import CompileFailed, ProviderUnavailable
from .butler.samples import SAMPLE_POLICIES
from .const import (CONF_COMPILER_BASE_URL, CONF_COMPILER_KEY, CONF_COMPILER_MODEL,
                    CONF_DECISION_BASE_URL, CONF_DECISION_KEY, CONF_DECISION_MODEL,
                    CONF_POLICIES, CONF_TIMEOUT, DEFAULT_COMPILER_MODEL,
                    DEFAULT_DECISION_BASE_URL, DEFAULT_DECISION_MODEL, DEFAULT_TIMEOUT,
                    DOMAIN, LEGACY_KEYS, TRIAL_SAMPLES)
from .host import verbs_for
from .policies import (apply_edits, draft_summary, dump, entity_catalog,
                       next_policy_id, load)
from .runtime import PROBE_REQUEST, ButlerRuntime
from .runtime import ButlerCompileFailed  # noqa: F401  异常类型由界面层转成用户文案

_LOGGER = logging.getLogger(__name__)

#: 候选实体允许哪些域。读数与动作都只能落在这些域里（`host.VERB_SERVICES` 的覆盖面）
CATALOG_DOMAINS = ["light", "switch", "cover", "fan", "humidifier", "binary_sensor",
                   "sensor", "input_boolean"]


def classify(exc: ProviderUnavailable) -> str:
    """把一次失败归到 UI 上看得懂的那条消息。认不出来就落 `unreachable`，不抛新异常。"""
    cause = exc.__cause__
    if isinstance(cause, urllib.error.HTTPError):
        return "auth_failed" if cause.code in (401, 403) else "http_error"
    # socket.timeout 自 3.10 起就是内置 TimeoutError 的别名，一个写法就够
    if isinstance(cause, TimeoutError):
        return "timeout"
    return "unreachable"


# ============================================================ 首次配置

class ButlerConfigFlow(ConfigFlow, domain=DOMAIN):
    """单实例：一台 HA 一个 entry。多个 entry 会让"哪个阈值在生效"重新变成两个真值。"""

    VERSION = 2

    def __init__(self) -> None:
        self._setup: dict = {}

    async def async_step_user(self, user_input: dict | None = None) -> ConfigFlowResult:
        """第一步：判别器——运行期判断屋子处于什么状态的那个模型，三项都可换。"""
        errors: dict[str, str] = {}
        if self._async_current_entries():
            return self.async_abort(reason="already_configured")

        if user_input is not None:
            try:
                provider = _decision_provider(user_input)
            except ValueError:
                errors[CONF_DECISION_BASE_URL] = "bad_url"
                return self.async_show_form(step_id="user",
                                            data_schema=_decision_schema(user_input),
                                            errors=errors)
            try:
                await self.hass.async_add_executor_job(provider.evaluate, PROBE_REQUEST)
            except ProviderUnavailable as exc:
                _LOGGER.info("配置阶段的判别器校验没通过：%s", exc)
                errors["base"] = classify(exc)
            except Exception as exc:
                _LOGGER.info("配置阶段的判别器校验没通过：%s", exc)
                errors["base"] = "http_error"
            else:
                self._setup = {
                    CONF_DECISION_BASE_URL: user_input[CONF_DECISION_BASE_URL].strip(),
                    CONF_DECISION_MODEL: user_input[CONF_DECISION_MODEL].strip(),
                    CONF_DECISION_KEY: user_input[CONF_DECISION_KEY].strip(),
                    CONF_TIMEOUT: DEFAULT_TIMEOUT}
                return await self.async_step_compiler()
            return self.async_show_form(step_id="user", data_schema=_decision_schema(
                user_input), errors=errors)

        return self.async_show_form(step_id="user", data_schema=_decision_schema())

    async def async_step_compiler(self, user_input: dict | None = None) -> ConfigFlowResult:
        """第二步：编译器——把"我想要…"那段话编译成策略草稿。可以不配，代价写在文案里。"""
        errors: dict[str, str] = {}
        if user_input is not None:
            base_url = (user_input.get(CONF_COMPILER_BASE_URL) or "").strip()
            model = (user_input.get(CONF_COMPILER_MODEL) or "").strip()
            if not base_url and not model:
                return self._create()                    # 明说"暂不配置"：那就空着进列表页
            merged = {**self._setup, CONF_COMPILER_BASE_URL: base_url,
                      CONF_COMPILER_MODEL: model,
                      CONF_COMPILER_KEY: (user_input.get(CONF_COMPILER_KEY) or "").strip()}
            try:
                compiler = _compiler_provider(merged)
                reply = await self.hass.async_add_executor_job(compiler.probe)
            except (CompileFailed, ValueError) as exc:
                _LOGGER.info("配置阶段的编译器校验没通过：%s", exc)
                errors["base"] = "compiler_bad"
            else:
                self._setup = merged
                _LOGGER.info("编译器探活回答：%r", reply)
                return self._create()
        return self.async_show_form(step_id="compiler",
                                    data_schema=_compiler_schema(user_input or {}),
                                    errors=errors)

    def _create(self) -> ConfigFlowResult:
        """建 entry：**不带任何策略**。样例是文档，不是用户机器上的一条数据（ADR-0017）。"""
        return self.async_create_entry(title="Gentleman's Gentleman",
                                       data={**self._setup, CONF_POLICIES: []})

    @staticmethod
    def async_get_options_flow(config_entry) -> OptionsFlow:
        return ButlerOptionsFlow()


def _decision_provider(form: dict):
    return JevProvider(form.get(CONF_DECISION_KEY, ""),
                       model=(form.get(CONF_DECISION_MODEL) or DEFAULT_DECISION_MODEL).strip(),
                       base_url=form.get(CONF_DECISION_BASE_URL) or DEFAULT_DECISION_BASE_URL,
                       timeout_seconds=float(form.get(CONF_TIMEOUT) or DEFAULT_TIMEOUT))


def _compiler_provider(form: dict):
    return OpenAICompatCompiler(form[CONF_COMPILER_BASE_URL], form[CONF_COMPILER_MODEL],
                                form.get(CONF_COMPILER_KEY, ""))


def _decision_schema(form: dict | None = None) -> vol.Schema:
    form = form or {}
    return vol.Schema({
        vol.Required(CONF_DECISION_BASE_URL,
                     default=form.get(CONF_DECISION_BASE_URL,
                                      DEFAULT_DECISION_BASE_URL)): TextSelector(
            TextSelectorConfig(autocomplete="off")),
        vol.Required(CONF_DECISION_MODEL,
                     default=form.get(CONF_DECISION_MODEL,
                                      DEFAULT_DECISION_MODEL)): TextSelector(
            TextSelectorConfig(autocomplete="off")),
        vol.Required(CONF_DECISION_KEY,
                     default=form.get(CONF_DECISION_KEY, "")): TextSelector(
            TextSelectorConfig(type=TextSelectorType.PASSWORD, autocomplete="off")),
    })


def _compiler_schema(form: dict) -> vol.Schema:
    return vol.Schema({
        vol.Optional(CONF_COMPILER_BASE_URL,
                     default=form.get(CONF_COMPILER_BASE_URL, "")): TextSelector(
            TextSelectorConfig(autocomplete="off")),
        vol.Optional(CONF_COMPILER_MODEL,
                     default=form.get(CONF_COMPILER_MODEL,
                                      DEFAULT_COMPILER_MODEL)): TextSelector(
            TextSelectorConfig(autocomplete="off")),
        vol.Optional(CONF_COMPILER_KEY,
                     default=form.get(CONF_COMPILER_KEY, "")): TextSelector(
            TextSelectorConfig(type=TextSelectorType.PASSWORD, autocomplete="off")),
    })


# ============================================================ 策略管理面

ADD, EDIT, TOGGLE, DELETE, SETTINGS = "add", "edit", "toggle", "delete", "settings"
_LABELS = {ADD: "➕ 用一段想法新增一条智能控制", EDIT: "✏️ 改一条（含试跑与调阈值）",
           TOGGLE: "⏸ 启用／停用一条", DELETE: "🗑 删除一条",
           SETTINGS: "⚙️ 全局设置（两个模型端点与超时）"}


class ButlerOptionsFlow(OptionsFlow):
    """策略列表与向导。

    草稿试跑走的是与日常**完全相同**的一条代码路径（ADR-0009 不许为试跑特判），
    区别只是这一次喂给引擎的策略清单由界面给出——只喂这一条，别的策略不会被它顺手动到，
    这件事写在页面上，不藏着。
    """

    def __init__(self) -> None:
        self._draft: dict | None = None
        self._compile_trace: str = ""      # 这份草稿是哪条编译链产出的（留痕配对用）
        self._catalog: list[dict] = []
        self._action: str = ""
        self._target: str = ""

    # ------------------------------------------------------------------ 工具

    @property
    def _policies(self) -> list[Policy]:
        return load(self.config_entry)

    @property
    def _butler(self) -> ButlerRuntime | None:
        return self.hass.data.get(DOMAIN, {}).get(self.config_entry.entry_id)

    def _write(self, policies: list[Policy]) -> ConfigFlowResult:
        """写回真值。`async_update_entry` 会触发重载，运行时随之重建（ADR-0013）。"""
        self.hass.config_entries.async_update_entry(
            self.config_entry,
            data={**self.config_entry.data, CONF_POLICIES: dump(policies)})
        return self.async_create_entry(title="", data={})

    def _name_of(self, policy_id: str) -> str:
        return next((p.name for p in self._policies if p.id == policy_id), policy_id)

    def _markdown_list(self) -> str:
        policies = self._policies
        if not policies:
            return "还没有一条智能控制。选上面第一项，用一段话说明你想要什么。"
        runtime = self._butler
        rows = []
        for p in policies:
            status = runtime.status.get(p.id) if runtime else None
            last = f"{status.status}／{status.outcome}" if status else "没跑过"
            rows.append(f"- **{p.name}**｜{'启用中' if p.enabled else '已停用'}"
                        f"｜阈值 {p.judgment.threshold:.2f}｜上次：{last}")
        return "\n".join(rows)

    # ------------------------------------------------------------------ 主菜单

    async def async_step_init(self, user_input: dict | None = None) -> ConfigFlowResult:
        if user_input is not None:
            self._action = user_input["action"]
            if self._action == ADD:
                return await self.async_step_add()
            if self._action == SETTINGS:
                return await self.async_step_settings()
            if not self._policies:
                return await self.async_step_init()
            return await self.async_step_pick()
        schema = vol.Schema({vol.Required("action"): SelectSelector(SelectSelectorConfig(
            options=[{"value": key, "label": label} for key, label in _LABELS.items()],
            mode=SelectSelectorMode.LIST))})
        return self.async_show_form(step_id="init", data_schema=schema,
                                    description_placeholders={"策略清单": self._markdown_list()})

    async def async_step_pick(self, user_input: dict | None = None) -> ConfigFlowResult:
        """改／停／删都是先挑一条。"""
        ids = [p.id for p in self._policies]
        if user_input is not None:
            self._target = user_input["policy"]
            if self._action == EDIT:
                self._draft = next(p.to_dict() for p in self._policies
                                   if p.id == self._target)
                self._catalog = []                       # 改已有策略：候选集从草稿白名单来
                return await self.async_step_review()
            if self._action == TOGGLE:
                policies = self._policies
                for p in policies:
                    if p.id == self._target:
                        p.enabled = not p.enabled
                return self._write(policies)
            return await self.async_step_delete_confirm()
        schema = vol.Schema({vol.Required("policy"): SelectSelector(SelectSelectorConfig(
            options=[{"value": i, "label": self._name_of(i)} for i in ids]))})
        return self.async_show_form(step_id="pick", data_schema=schema)

    async def async_step_delete_confirm(self, user_input: dict | None = None
                                        ) -> ConfigFlowResult:
        """删除是这条流程里唯一不可逆的动作，所以它单独一步，而不是菜单里一键完成。"""
        if user_input is not None:
            if user_input["confirm"] == "yes":
                return self._write([p for p in self._policies if p.id != self._target])
            return await self.async_step_init()
        schema = vol.Schema({vol.Required("confirm", default="no"): SelectSelector(
            SelectSelectorConfig(options=[{"value": "no", "label": "先不删"},
                                          {"value": "yes", "label": "确认删除"}]))})
        return self.async_show_form(step_id="delete_confirm", data_schema=schema,
                                    description_placeholders={"策略": self._name_of(self._target)})

    # ----------------------------------------------------------------── 新增：写想法 → 编译

    async def async_step_add(self, user_input: dict | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        reason = ""
        if user_input is not None:
            runtime = self._butler
            if runtime is None:
                return self.async_abort(reason="not_ready")
            intent = user_input["intent"].strip()
            entity_ids = list(user_input["entities"])
            self._catalog = entity_catalog(self.hass, entity_ids)
            policy_id = next_policy_id(self.config_entry, intent)
            try:
                self._draft, self._compile_trace = await runtime.async_compile(
                    intent, entity_ids, policy_id)
            except ButlerCompileFailed as exc:
                errors["base"] = "compile_failed"
                reason = str(exc)
            else:
                return await self.async_step_review()
        schema = vol.Schema({
            vol.Required("intent"): TextSelector(TextSelectorConfig(multiline=True)),
            vol.Required("entities"): EntitySelector(EntitySelectorConfig(
                multiple=True, domain=CATALOG_DOMAINS)),
        })
        return self.async_show_form(step_id="add", data_schema=schema, errors=errors,
                                    description_placeholders={"示例": _sample_markdown(),
                                                              "原因": reason})

    # ------------------------------------------------------------------ 核对与微调

    def _entity_choices(self, draft: dict) -> list[str]:
        ids = [e["entity_id"] for e in self._catalog] or \
            [a["entity_id"] for b in draft["branches"] for a in b["actions"]]
        return list(dict.fromkeys(ids))

    def _review_schema(self, draft: dict) -> vol.Schema:
        """界面上改得动的六个字段。其余（读数清单、判据）要改就重编译——
        把一棵 20 个字段的树硬塞进 HA 的扁平表单，只会得到一个没人敢碰的表单。"""
        action = next((a for b in draft["branches"] for a in b["actions"]), {})
        ids = self._entity_choices(draft)
        verbs = sorted({v for eid in ids for v in verbs_for(eid.split(".", 1)[0])})
        return vol.Schema({
            vol.Required("name", default=draft["name"]): TextSelector(),
            vol.Required("question", default=draft["judgment"]["question"]): TextSelector(
                TextSelectorConfig(multiline=True)),
            vol.Required("threshold", default=float(draft["judgment"]["threshold"])):
                NumberSelector(NumberSelectorConfig(mode=NumberSelectorMode.SLIDER,
                                                    min=0.0, max=1.0, step=0.01)),
            vol.Optional("action_entity", default=action.get("entity_id")): SelectSelector(
                SelectSelectorConfig(options=[{"value": e, "label": e} for e in ids])),
            vol.Optional("action_verb", default=action.get("verb")): SelectSelector(
                SelectSelectorConfig(options=[{"value": v, "label": v}
                                              for v in verbs or ["turn_on"]])),
            vol.Optional("cooldown", default=int(draft.get("safety", {})
                                                 .get("cooldown_seconds", 0))): NumberSelector(
                NumberSelectorConfig(mode=NumberSelectorMode.BOX, min=0, max=86400,
                                     step=60)),
        })

    async def async_step_review(self, user_input: dict | None = None) -> ConfigFlowResult:
        draft = self._draft
        catalog = self._catalog or _catalog_from_draft(draft)
        self._catalog = catalog
        if user_input is not None:
            merged = apply_edits(draft, user_input)
            try:
                self._draft = validate_draft(merged, catalog, merged["id"])
            except CompileFailed as exc:
                return self.async_show_form(step_id="review",
                                            data_schema=self._review_schema(merged),
                                            errors={"base": "draft_invalid"},
                                            description_placeholders={
                                                "摘要": draft_summary(merged), "原因": str(exc)})
            return await self.async_step_trial()
        return self.async_show_form(step_id="review", data_schema=self._review_schema(draft),
                                    description_placeholders={"摘要": draft_summary(draft),
                                                              "原因": ""})

    # ------------------------------------------------------------------ 试跑与调阈值

    async def async_step_trial(self, user_input: dict | None = None) -> ConfigFlowResult:
        """试跑＝真判断＋真执行（ADR-0005/0009）。调阈值就是为了在这一页当场再看一次。"""
        if user_input is not None:
            choice = user_input["next"]
            if choice == "save":
                return self._save(Policy.from_dict(self._draft))
            if choice == "review":
                return await self.async_step_review()
            draft = {**self._draft,
                     "judgment": {**self._draft["judgment"],
                                  "threshold": float(user_input["threshold"])}}
            self._draft = draft
        runtime = self._butler
        if runtime is None:
            return self.async_abort(reason="not_ready")
        policy = Policy.from_dict(self._draft)
        report: dict[str, Any]
        try:
            statuses = await runtime.async_trial(policy.id, policies=[policy])
            status = statuses[policy.id]
            report = status.attributes()
            report = {"状态": status.status, **report}
            report["采样"] = "、".join(f"{s:.2f}" for s in status.samples) or "（没有采样）"
        except Exception as exc:                       # 链炸了留在这一页，别把人踢回主菜单
            report = {"状态": "这一轮没跑完", "原因": str(exc)}
        schema = vol.Schema({
            vol.Required("threshold", default=policy.judgment.threshold): NumberSelector(
                NumberSelectorConfig(mode=NumberSelectorMode.SLIDER, min=0.0, max=1.0,
                                     step=0.01)),
            vol.Required("next", default="again"): SelectSelector(SelectSelectorConfig(
                options=[{"value": "again", "label": "按这个阈值再试一次"},
                         {"value": "review", "label": "回到核对页改判断"},
                         {"value": "save", "label": "满意，保存这条策略"}],
                mode=SelectSelectorMode.LIST)),
        })
        return self.async_show_form(
            step_id="trial", data_schema=schema,
            description_placeholders={
                "结果": "\n".join(f"- {k}：{v}" for k, v in report.items()),
                "次数": str(TRIAL_SAMPLES), "策略": policy.name})

    def _save(self, policy: Policy) -> ConfigFlowResult:
        """保存＝用户在核对与试跑之后确认过了。

        这一步要落进留痕：`compile_saved` 与那次编译按 trace_id 配对，才凑出
        "编出来的草稿你到底要不要"这个比率（ADR-0017 待办①）。写在这里而不是
        `_write` 里，是因为停用／删除也走 `_write`，它们不是"保存了一份草稿"。
        """
        butler = self._butler
        if self._compile_trace and butler is not None:
            butler.record_compile_saved(self._compile_trace)
            self._compile_trace = ""
        policies = self._policies
        for index, existing in enumerate(policies):
            if existing.id == policy.id:
                policies[index] = policy
                break
        else:
            policies.append(policy)
        return self._write(policies)

    # ------------------------------------------------------------------ 全局设置

    async def async_step_settings(self, user_input: dict | None = None) -> ConfigFlowResult:
        """改端点同样要当场验通（A2 的 P2/P7）：填错地址却保存成功，代价是深夜那次判断
        静默走安全回退——那是用户最看不见错误的时刻。"""
        data = self.config_entry.data
        errors: dict[str, str] = {}
        if user_input is not None:
            merged = {**data}
            for key, value in user_input.items():
                if isinstance(value, str) and key.endswith("_key") and not value.strip():
                    continue                         # 空串＝"不改这把 Key"，不是"删掉它"
                merged[key] = value
            try:
                provider = _decision_provider(merged)
            except ValueError:
                provider = None
                errors[CONF_DECISION_BASE_URL] = "bad_url"
            if provider is not None:
                try:
                    await self.hass.async_add_executor_job(provider.evaluate, PROBE_REQUEST)
                except ProviderUnavailable as exc:
                    errors["base"] = classify(exc)
                except Exception as exc:
                    errors["base"] = "http_error"
                    _LOGGER.info("设置里的判别器校验没通过：%s", exc)
            if not errors and (merged.get(CONF_COMPILER_BASE_URL) or "").strip() and \
                    (merged.get(CONF_COMPILER_MODEL) or "").strip():
                try:
                    compiler = _compiler_provider(merged)
                    await self.hass.async_add_executor_job(compiler.probe)
                except (CompileFailed, ValueError) as exc:
                    errors["base"] = "compiler_bad"
                    _LOGGER.info("设置里的编译器校验没通过：%s", exc)
            if not errors:
                self.hass.config_entries.async_update_entry(self.config_entry, data=merged)
                return self.async_create_entry(title="", data={})
        schema = vol.Schema({
            vol.Required(CONF_DECISION_BASE_URL,
                         default=data.get(CONF_DECISION_BASE_URL,
                                          DEFAULT_DECISION_BASE_URL)): TextSelector(),
            vol.Required(CONF_DECISION_MODEL,
                         default=data.get(CONF_DECISION_MODEL,
                                          DEFAULT_DECISION_MODEL)): TextSelector(),
            vol.Optional(CONF_DECISION_KEY, default=""): TextSelector(
                TextSelectorConfig(type=TextSelectorType.PASSWORD, autocomplete="off")),
            vol.Optional(CONF_COMPILER_BASE_URL,
                         default=data.get(CONF_COMPILER_BASE_URL, "")): TextSelector(),
            vol.Optional(CONF_COMPILER_MODEL,
                         default=data.get(CONF_COMPILER_MODEL, "")): TextSelector(),
            vol.Optional(CONF_COMPILER_KEY, default=""): TextSelector(
                TextSelectorConfig(type=TextSelectorType.PASSWORD, autocomplete="off")),
            vol.Optional(CONF_TIMEOUT, default=float(data.get(CONF_TIMEOUT,
                                                              DEFAULT_TIMEOUT))): NumberSelector(
                NumberSelectorConfig(mode=NumberSelectorMode.BOX, min=1.0, max=30.0,
                                     step=0.5)),
        })
        return self.async_show_form(step_id="settings", data_schema=schema, errors=errors)


# ------------------------------------------------------------------ 表单辅助

def _catalog_from_draft(draft: dict) -> list[dict]:
    """改已有策略时没有"当场勾选"那一步：候选实体集就从它的白名单里来。"""
    allowed = draft.get("safety", {}).get("allowed_entities") or []
    return [{"entity_id": e, "name": e, "state": "",
             "verbs": verbs_for(e.split(".", 1)[0])} for e in allowed]


def _sample_markdown() -> str:
    """内置样例在这里继续干它的本职：**文档**。它不再是用户机器上的一条数据。"""
    raw = SAMPLE_POLICIES["sunset_light"]
    judgment = raw["judgment"]
    return (f"**{raw['name']}**\n\n"
            f"- 想法：{judgment['question']}\n"
            f"- 看的数：{'、'.join(r['key'] for r in raw['readings'])}\n"
            f"- 什么时候看：{raw['trigger']['type']}"
            f"（日落后再等 {raw['trigger']['offset_minutes']} 分钟）\n"
            f"- 判成「{judgment['status_of']['True']}」就不动手，"
            f"判成「{judgment['status_of']['False']}」才开灯\n\n"
            f"照这个形状写你自己的那段话就行，不必照抄内容。")
