/* 小智控制台 前端 */

const $ = (s) => document.querySelector(s);
const $$ = (s) => Array.from(document.querySelectorAll(s));

const ICONS = {
  grid: "M3 3h8v8H3zM13 3h8v8h-8zM3 13h8v8H3zM13 13h8v8h-8z",
  mic: "M9 2h6a3 3 0 0 1 3 3v7a6 6 0 0 1-12 0V5a3 3 0 0 1 3-3zM3 11h2a7 7 0 0 0 14 0h2a9 9 0 0 1-8 8.94V23h-2v-3.06A9 9 0 0 1 3 11z",
  brain: "M12 4a4 4 0 0 0-4 4 3 3 0 0 0-2 5.2A3 3 0 0 0 8 18a4 4 0 0 0 4 2 4 4 0 0 0 4-2 3 3 0 0 0 2-4.8A3 3 0 0 0 16 8a4 4 0 0 0-4-4z",
  wave: "M2 10h2.5v4H2zM7 6h2.5v12H7zM12 2h2.5v20H12zM17 6h2.5v12H17zM22 10h2.5v4H22z",
  target: "M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20zm0 3a7 7 0 1 1 0 14 7 7 0 0 1 0-14z",
  clock: "M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20zm0 2a8 8 0 1 1 0 16 8 8 0 0 1 0-16zM11 6h2v6.4l4 2.4-1 1.7-5-3z",
  gear: "M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8zm0 6a2 2 0 1 1 0-4 2 2 0 0 1 0 4zM12 2l1.2 2.4 2.7-.4.6 2.7 2.4 1.2-1.3 2.4 1.3 2.4-2.4 1.2-.6 2.7-2.7-.4L12 22l-1.2-2.4-2.7.4-.6-2.7L5.1 16l1.3-2.4L5.1 11l2.4-1.2.6-2.7 2.7.4z"
};

function svgUrl(path) {
  const svg = "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='black'><path d='" + path + "'/></svg>";
  return "url(\"data:image/svg+xml," + encodeURIComponent(svg) + "\")";
}

/* ---------------- 后端调用 ---------------- */

function pollTask(taskId) {
  return new Promise((resolve, reject) => {
    let tries = 0;
    const iv = setInterval(async () => {
      tries += 1;
      if (tries > 1500) { clearInterval(iv); reject(new Error("超时")); return; }
      try {
        const st = await window.pywebview.api.task_status(taskId);
        if (st && st.done) {
          clearInterval(iv);
          if (st.error) reject(new Error(st.error));
          else resolve(st.result);
        }
      } catch (e) { clearInterval(iv); reject(e); }
    }, 200);
  });
}

async function api(name, payload) {
  const r = await window.pywebview.api.call(name, payload || {});
  if (r && r.immediate) return r.data;
  return pollTask(r.task_id);
}

/* ---------------- 提示 ---------------- */

let toastTimer = null;
function toast(msg, isErr) {
  let el = $(".toast");
  if (!el) {
    el = document.createElement("div");
    el.className = "toast";
    document.body.appendChild(el);
  }
  el.textContent = msg;
  el.className = "toast show" + (isErr ? " err" : "");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.className = "toast" + (isErr ? " err" : ""); }, 2600);
}

/* ---------------- 全局状态 ---------------- */

const state = {
  voices: [], ttsModels: [], settings: {}, rules: {},
  currentVoice: "", ttsSource: "minimax",
  logOffset: 0, logTimer: null, engineTimer: null
};

/* ---------------- 页面切换 ---------------- */

function showPage(name) {
  $$(".nav-item").forEach(b => b.classList.toggle("is-active", b.dataset.page === name));
  $$(".page").forEach(p => p.classList.toggle("is-active", p.id === "page-" + name));
  if (name === "settings") { loadDevices(); startLogPolling(); }
  else stopLogPolling();
  /* 克隆音色要问平台要，进音色页才拉 —— 别一进程序就多一次网络往返 */
  if (name === "voices") loadClonedVoices();
  scrollNavIntoView();
}

/* 窄屏 / 折叠屏上标签栏是横向滚动的，当前标签可能在视野外 */
function scrollNavIntoView() {
  const el = document.querySelector(".nav-item.is-active");
  if (!el || !el.scrollIntoView) return;
  try {
    el.scrollIntoView({ block: "nearest", inline: "center", behavior: "smooth" });
  } catch (e) {
    el.scrollIntoView();
  }
}

/* ---------------- 显示环境（折叠屏会换视口） ---------------- */

/* 触控模式：真机 pointer:coarse 自动开；桌面上加 ?touch=1 也能模拟，
   用来在开发机上复现折叠屏的触控热区与 16px 输入。 */
function syncTouchMode() {
  const forced = /[?&]touch=1(&|$)/.test(location.search);
  const coarse = !!(window.matchMedia && window.matchMedia("(pointer: coarse)").matches);
  document.documentElement.classList.toggle("touch", !!(forced || coarse));
}

function layoutTier(w) {
  if (w <= 460) return { name: "窄屏（合盖外屏）", desc: "顶部标签栏 + 卡片单列" };
  if (w <= 660) return { name: "中屏（折叠屏展开竖屏）", desc: "顶部标签栏 + 卡片两列" };
  if (w <= 900) return { name: "宽屏（折叠屏展开横屏）", desc: "顶部标签栏 + 卡片两列" };
  return { name: "桌面", desc: "左侧边栏 + 多列" };
}

function updateViewportInfo() {
  const box = $("#viewportInfo");
  if (!box) return;
  const w = window.innerWidth;
  const h = window.innerHeight;
  const dpr = window.devicePixelRatio || 1;
  const vv = window.visualViewport;
  const tier = layoutTier(w);
  const touch = document.documentElement.classList.contains("touch");
  const rows = [
    ["视口（CSS 像素）", w + " × " + h],
    ["可视高度", vv ? Math.round(vv.height) + "px" : "—"],
    ["设备像素比", dpr + " → 物理 " + Math.round(w * dpr) + " × " + Math.round(h * dpr)],
    ["屏幕", (window.screen.width || "?") + " × " + (window.screen.height || "?")],
    ["触屏模式", touch ? "开（热区与 16px 输入）" : "关（鼠标 / 桌面）"],
    ["布局档位", tier.name + " · " + tier.desc],
    ["方向", w > h ? "横向" : "纵向"],
  ];
  box.className = "";
  box.innerHTML = rows
    .map(([k, v]) => '<div class="kv"><span>' + esc(k) + "</span><code>" + esc(v) + "</code></div>")
    .join("");
}

/* ---------------- 概览 ---------------- */

async function loadOverview() {
  try {
    const ov = await api("get_overview");
    const s = ov.settings || {};
    $("#statModel").textContent = s.model || "—";
    $("#statVoice").textContent = s.voice || "—";
    $("#statKey").textContent = s.api_key_set ? (s.api_key_masked || "已配置") : "未配置";
    $("#statKeyword").textContent = (ov.keyword_count || 0) + " 个";
    $("#sbModel").textContent = "模型 " + (s.model || "—");
    $("#sbVoice").textContent = "音色 " + (s.voice || "—");
    $("#sbSpeaker").textContent = "音箱 " + (s.mi_did ? "已连接" : "—");
    updateEnginePill(ov.engine);
  } catch (e) { console.error(e); }
}

function updateEnginePill(eng) {
  const on = eng && eng.running;
  const ext = (eng && eng.external && eng.external.length) ? eng.external : null;
  $("#enginePill .dot").className = "dot " + (on ? "on" : "off");
  $("#engineState").textContent = on ? "引擎运行中" : "引擎未运行";
  $("#btnEngineToggle").textContent = on ? "停止" : "启动";
  $("#engineDesc").textContent = on
    ? (ext
        ? "xiaogpt 正在轮询（pid " + ext.join(", ") + "，不是这个窗口启动的）"
        : "xiaogpt 正在轮询，识别到触发词就会转给智能体")
    : "xiaogpt 进程未运行 —— 启动后才能听懂你说的话";
}

/* ---------------- 引擎 ---------------- */

async function engineToggle() {
  const st = await api("engine_status");
  const running = !!st.running;
  const res = running ? await api("engine_stop") : await api("engine_start");
  if (res && res.ok === false) {
    toast(res.error || "操作失败", true);
    await loadOverview();
    return;
  }
  toast((res && res.detail) || (running ? "引擎已停止" : "引擎已启动"));
  await loadOverview();
}

async function engineRestart() {
  toast("正在重启引擎…");
  const res = await api("engine_restart");
  if (res && res.ok === false) toast(res.error || "重启失败", true);
  await loadOverview();
}

/* ---------------- 健康检查 ---------------- */

async function runHealth() {
  const box = $("#healthList");
  box.innerHTML = '<div class="empty">体检中…</div>';
  try {
    const r = await api("health");
    box.innerHTML = "";
    const ico = { ok: "✓", warn: "!", bad: "✕" };
    const cls = { ok: "hi-ok", warn: "hi-warn", bad: "hi-bad" };
    r.items.forEach(it => {
      const d = document.createElement("div");
      d.className = "health-item";
      d.innerHTML =
        '<div class="hi-ico ' + cls[it.level] + '">' + ico[it.level] + "</div>" +
        '<div class="hi-name">' + esc(it.name) + "</div>" +
        '<div><div class="hi-detail">' + esc(it.detail) + "</div>" +
        (it.hint ? '<div class="hi-hint">' + esc(it.hint) + "</div>" : "") + "</div>";
      box.appendChild(d);
    });
    const bal = r.items.find(i => i.name === "账户余额");
    if (bal) $("#sbBalance").textContent = "余额 " + bal.detail;
    toast(r.overall === "ok" ? "体检通过，一切正常" :
          r.overall === "warn" ? "体检完成，有需要留意的地方" : "体检发现问题");
  } catch (e) {
    box.innerHTML = '<div class="empty">体检失败：' + esc(String(e.message || e)) + "</div>";
  }
}

/* ---------------- 说话 ---------------- */

function speakParams() {
  return {
    voice: $("#speakVoice").value || null,
    speed: parseFloat($("#speakSpeed").value),
    vol: parseFloat($("#speakVol").value),
    pitch: parseInt($("#speakPitch").value, 10),
    use_minimax: !$("#speakNative").checked
  };
}

async function doSpeak() {
  const text = $("#speakText").value.trim();
  if (!text) { toast("先写点什么", true); return; }
  $("#speakHint").textContent = "正在合成并推送…";
  try {
    await api("speak", Object.assign({ text: text }, speakParams()));
    $("#speakHint").textContent = "已推送，音箱正在说";
  } catch (e) {
    $("#speakHint").textContent = "";
    toast("播放失败：" + (e.message || e), true);
  }
}

async function doPreview() {
  const text = $("#speakText").value.trim();
  if (!text) { toast("先写点什么", true); return; }
  $("#speakHint").textContent = "正在本地合成…";
  try {
    const r = await api("preview", Object.assign({ text: text }, speakParams()));
    const a = $("#previewAudio");
    a.src = "data:audio/mpeg;base64," + r.audio;
    a.style.display = "block";
    a.play();
    $("#speakHint").textContent = "在本机播放（不打扰音箱），时长 " + r.duration.toFixed(1) + "s";
  } catch (e) {
    $("#speakHint").textContent = "";
    toast("试听失败：" + (e.message || e), true);
  }
}

async function doAskThenSpeak() {
  const q = $("#speakText").value.trim();
  if (!q) { toast("先写个问题", true); return; }
  $("#speakHint").textContent = "正在问 AI…";
  try {
    const r = await api("ask", { question: q });
    $("#speakHint").textContent = "AI 答了，正在播报…";
    await api("speak", Object.assign({ text: r.answer }, speakParams()));
    $("#speakHint").textContent = "完成";
  } catch (e) {
    $("#speakHint").textContent = "";
    toast("失败：" + (e.message || e), true);
  }
}

/* ---------------- 智能体 ---------------- */

async function loadSettings() {
  const s = await api("get_settings");
  state.settings = s;
  $("#setApiBase").value = s.api_base || "";
  $("#setAgentName").value = s.agent_name || "";
  $("#setPrompt").value = s.system_prompt || "";
  $("#setMaxTokens").value = s.max_tokens || 500;
  $("#vMaxTok").textContent = s.max_tokens || 500;
  $("#setThinking").checked = !!s.thinking_disabled;
  $("#keyHint").textContent = s.api_key_set ? ("当前：" + s.api_key_masked) : "还没配密钥";
  $("#setApiKey").placeholder = s.api_key_set ? "已配置，留空表示不改" : "粘贴 MiniMax 密钥";
  const tts = s.tts_options || {};
  state.currentVoice = tts.voice || "";
  $("#ttsModel").value = tts.model || "";
  syncVoiceSelect();
  loadModels();
}

async function loadModels() {
  const sel = $("#setModel");
  try {
    const r = await api("list_models");
    sel.innerHTML = "";
    r.models.forEach(m => {
      const o = document.createElement("option");
      o.value = m.id;
      o.textContent = m.id + (m.hint ? "  —  " + m.hint : "");
      sel.appendChild(o);
    });
    if (state.settings.model) sel.value = state.settings.model;
    const cur = r.models.find(m => m.id === state.settings.model);
    $("#modelHint").textContent = cur && cur.hint ? cur.hint : "";
  } catch (e) {
    $("#modelHint").textContent = "拉取失败：" + (e.message || e);
  }
}

async function saveAgent() {
  const patch = {
    api_base: $("#setApiBase").value.trim(),
    model: $("#setModel").value,
    agent_name: $("#setAgentName").value.trim(),
    max_tokens: parseInt($("#setMaxTokens").value, 10),
    thinking_disabled: $("#setThinking").checked
  };
  const key = $("#setApiKey").value.trim();
  if (key) patch.api_key = key;
  try {
    await api("update_settings", { patch: patch });
    $("#setApiKey").value = "";
    toast("已保存");
    await loadSettings();
    await loadOverview();
  } catch (e) { toast("保存失败：" + (e.message || e), true); }
}

async function savePrompt() {
  try {
    await api("update_settings", { patch: { system_prompt: $("#setPrompt").value } });
    toast("人设已更新（下次回答即刻生效）");
  } catch (e) { toast("保存失败：" + (e.message || e), true); }
}

async function doAsk() {
  const q = $("#askInput").value.trim();
  if (!q) return;
  $("#askOutput").className = "answer";
  $("#askOutput").textContent = "思考中…";
  try {
    const r = await api("ask", { question: q });
    $("#askOutput").textContent = r.answer || "（空回答）";
  } catch (e) {
    $("#askOutput").textContent = "失败：" + (e.message || e);
  }
}

/* ---------------- 音色 ---------------- */

function syncVoiceSelect() {
  const sel = $("#speakVoice");
  sel.innerHTML = "";
  state.voices.forEach(v => {
    const o = document.createElement("option");
    o.value = v.id;
    o.textContent = v.label;
    sel.appendChild(o);
  });
  if (state.currentVoice) sel.value = state.currentVoice;
}

async function loadVoices() {
  const r = await api("list_voices");
  state.voices = r.voices;
  state.ttsModels = r.models;
  const sel = $("#ttsModel");
  sel.innerHTML = "";
  r.models.forEach(m => {
    const o = document.createElement("option");
    o.value = m.id; o.textContent = m.label;
    sel.appendChild(o);
  });
  const srcSel = $("#ttsSource");
  if (srcSel) {
    srcSel.innerHTML = "";
    (r.sources || []).forEach(s => {
      const o = document.createElement("option");
      o.value = s.id; o.textContent = s.label;
      o.dataset.hint = s.hint || "";
      srcSel.appendChild(o);
    });
  }
  syncVoiceSelect();
  renderVoices();
  await fillTtsControls(state.settings);
}

/* 把配置里的音色来源 / 响度等回填到界面 */
async function fillTtsControls(settings) {
  let s = settings;
  if (!s || !s.tts) {
    try { s = await api("get_settings"); } catch (e) { return; }
  }
  state.settings = s;
  const tts = s.tts_options || {};
  const src = s.tts || "minimax";
  state.ttsSource = src;
  const srcSel = $("#ttsSource");
  if (srcSel) {
    srcSel.value = src;
    if (srcSel.selectedIndex < 0 && srcSel.options.length) srcSel.selectedIndex = 0;
  }
  const modelSel = $("#ttsModel");
  if (modelSel && tts.model) modelSel.value = tts.model;
  const sp = $("#audSpeed"), sv = $("#audVol"), pt = $("#audPitch");
  if (sp && tts.speed != null) {
    sp.value = tts.speed;
    $("#vSpeed2").textContent = parseFloat(tts.speed).toFixed(2);
  }
  if (sv && tts.vol != null) {
    sv.value = tts.vol;
    $("#vVol2").textContent = parseFloat(tts.vol).toFixed(2);
  }
  if (pt && tts.pitch != null) {
    pt.value = tts.pitch;
    $("#vPitch2").textContent = tts.pitch;
  }
  syncTtsSourceUI();
}

/* 音色来源切换：MiniMax 系控件在小爱原生下没意义，置灰 */
function syncTtsSourceUI() {
  const srcSel = $("#ttsSource");
  const src = srcSel ? srcSel.value : (state.ttsSource || "minimax");
  state.ttsSource = src;
  const isMi = src === "mi";
  const hint = $("#ttsSourceHint");
  if (hint) {
    const opt = (srcSel && srcSel.selectedIndex >= 0) ? srcSel.options[srcSel.selectedIndex] : null;
    const extra = opt ? (opt.dataset.hint || "") : "";
    const head = isMi
      ? "现在是 <b>小爱原生音色</b>：声音是音箱自己发的，比 MiniMax 合成的声音大、延迟几乎为零；代价是分不清哪句是小智答的。"
      : "现在是 <b>MiniMax 音色</b>：14 个音色可选、能和小爱原生区分开。<b>响度</b>是合成波形的放大倍数，每翻一倍 +6dB；超过 5 波形会削顶、听着是破音而不是更响。";
    hint.innerHTML = head + (extra ? "<br>" + esc(extra) : "") + "<br>改完会自动重启引擎生效。";
  }
  ["#ttsModel", "#audSpeed", "#audVol", "#audPitch", "#speakVoice"].forEach(function (sel) {
    const el = $(sel);
    if (el) el.disabled = isMi;
  });
  const grid = $("#voiceGrid");
  if (grid) grid.style.opacity = isMi ? "0.5" : "1";
}

async function saveTtsSource() {
  const srcSel = $("#ttsSource");
  if (!srcSel) return;
  const src = srcSel.value;
  try {
    await api("update_settings", {
      patch: {
        tts: src,
        tts_options: {
          model: $("#ttsModel") ? $("#ttsModel").value : undefined,
          speed: parseFloat($("#audSpeed").value),
          vol: parseFloat($("#audVol").value),
          pitch: parseInt($("#audPitch").value, 10)
        }
      }
    });
    state.ttsSource = src;
    syncTtsSourceUI();
    toast("已保存，正在重启引擎让新音色生效…");
    const r = await api("engine_restart");
    toast(src === "mi" ? "已切到小爱原生音色（引擎已重启）" : "已切到 MiniMax 音色（引擎已重启）", !(r && r.ok));
  } catch (e) {
    toast("保存失败：" + ((e && e.message) || e), true);
  }
}

function renderVoices() {
  const grid = $("#voiceGrid");
  grid.innerHTML = "";
  $("#voiceCount").textContent = state.voices.length + " 个";
  state.voices.forEach(v => {
    const isCur = v.id === state.currentVoice;
    const card = document.createElement("div");
    card.className = "voice-card" + (isCur ? " is-current" : "");
    card.innerHTML =
      '<div class="vc-name">' + esc(v.label) + (isCur ? ' <span class="vc-badge">默认</span>' : "") + "</div>" +
      '<div class="vc-id">' + esc(v.id) + "</div>" +
      '<div class="vc-actions">' +
        '<button class="btn btn-sm" data-act="aud">试听</button>' +
        '<button class="btn btn-sm" data-act="use">设为默认</button>' +
      "</div>";
    card.querySelector('[data-act=aud]').onclick = () => audition(v.id);
    card.querySelector('[data-act=use]').onclick = () => useVoice(v.id);
    grid.appendChild(card);
  });
}

/* ---------------- 克隆音色 ---------------- */

/* 克隆音色不在本地白名单里，只有平台知道有哪些 —— 要问 MiniMax 接口。
   账号没开通克隆权限时返回空列表，这里如实显示「还没有」，不假装有。 */
async function loadClonedVoices() {
  const grid = $("#clonedGrid");
  const hint = $("#clonedHint");
  if (!grid) return;
  grid.innerHTML = "";
  hint.textContent = "正在拉取…";
  try {
    const r = await api("list_cloned_voices");
    if (!r || !r.ok) {
      hint.textContent = "拉取失败：" + ((r && r.error) || "未知错误");
      return;
    }
    const vs = r.voices || [];
    if (!vs.length) {
      hint.innerHTML =
        "这个账号下还没有克隆音色。<br>" +
        "克隆需要 <b>手机录一段 30~60 秒</b> 的干净人声（安静房间、正常语速），" +
        "然后跑 <code>tools/voice_clone.py clone &lt;文件&gt; --voice-id 你的音色id</code>。<br>" +
        "操作细节见项目里的《音色克隆操作指南.md》。";
      return;
    }
    hint.textContent = "共 " + vs.length + " 个克隆音色";
    vs.forEach(v => {
      const isCur = v.id === state.currentVoice;
      const card = document.createElement("div");
      card.className = "voice-card" + (isCur ? " is-current" : "");
      card.innerHTML =
        '<div class="vc-name">' + esc(v.label) + (isCur ? ' <span class="vc-badge">默认</span>' : "") + "</div>" +
        '<div class="vc-id">' + esc(v.id) + "</div>" +
        '<div class="vc-actions">' +
          '<button class="btn btn-sm" data-act="aud">试听</button>' +
          '<button class="btn btn-sm" data-act="use">设为默认</button>' +
        "</div>";
      card.querySelector('[data-act=aud]').onclick = () => audition(v.id);
      card.querySelector('[data-act=use]').onclick = () => useVoice(v.id);
      grid.appendChild(card);
    });
  } catch (e) {
    hint.textContent = "拉取失败：" + (e.message || e);
  }
}

async function audition(voice) {
  const text = $("#auditionText").value.trim() || "你好，我是小智。";
  const useMm = state.ttsSource !== "mi";
  toast("正在让音箱念「" + text + "」…");
  try {
    await api("speak", {
      text: text, voice: voice,
      speed: parseFloat($("#audSpeed").value),
      vol: parseFloat($("#audVol").value),
      pitch: parseInt($("#audPitch").value, 10),
      use_minimax: useMm
    });
  } catch (e) { toast("试听失败：" + (e.message || e), true); }
}

async function useVoice(voice) {
  try {
    await api("update_settings", {
      patch: { tts_options: { voice: voice, model: $("#ttsModel").value,
        speed: parseFloat($("#audSpeed").value),
        vol: parseFloat($("#audVol").value),
        pitch: parseInt($("#audPitch").value, 10) } }
    });
    state.currentVoice = voice;
    renderVoices();
    syncVoiceSelect();
    toast("已设为默认音色");
    loadOverview();
  } catch (e) { toast("设置失败：" + (e.message || e), true); }
}

/* ---------------- 音箱系统音量 ---------------- */

/* 音箱自己的音量旋钮（0-100），改喇叭输出增益。
   和音色页的 vol（改音频波形）是两回事 —— 要更响优先调这里。 */
async function readSpeakerVolume() {
  try {
    const r = await api("get_volume");
    const v = Math.max(0, Math.min(100, Number(r.volume) || 0));
    $("#speakerVol").value = v;
    $("#speakerVolLabel").textContent = v;
    toast("音箱当前音量 " + v);
  } catch (e) {
    toast("读音量失败：" + (e.message || e), true);
  }
}

async function applySpeakerVolume() {
  const v = Math.max(0, Math.min(100, Number($("#speakerVol").value) || 0));
  try {
    const r = await api("set_volume", { volume: v });
    const actual = Number(r.volume);
    $("#speakerVolLabel").textContent = actual;
    if (actual !== v) $("#speakerVol").value = actual;
    toast("音箱音量已设为 " + actual + (actual !== v ? "（设备钳制后）" : ""));
  } catch (e) {
    toast("设音量失败：" + (e.message || e), true);
  }
}

/* ---------------- MIoT 设备控制 ---------------- */

/* 走的是 MIoT 云端通道（api.io.mi.com），和小米云对话不是一套登录态。
   实测两个坑：写入生效要 10-20 秒；云端 code=0 不代表设备照做了。
   所以界面如实显示「已生效 / 已下发」，不假装秒回。 */

let miotState = null;

function esc(s) {
  return String(s === null || s === undefined ? "" : s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function miotVal(v) {
  if (v === true) return "开";
  if (v === false) return "关";
  if (v === null || v === undefined) return "读不到";
  return String(v);
}

function renderMiotState(r) {
  miotState = r;
  const box = $("#miotState");
  if (!box) return;
  if (!r || r.error) {
    box.className = "empty";
    box.textContent = "读取失败：" + ((r && r.error) || "未知错误");
    return;
  }
  const rows = [
    ["音量", r.volume],
    ["麦克风静音", r.mic_mute],
    ["扬声器静音", r.speaker_mute],
    ["勿扰", r.no_disturb],
    ["勿扰时段", r.period],
    ["环境分贝", r.decibel],
    ["播放状态", r.playing_state],
    ["型号", r.model],
    ["序列号", r.serial],
    ["固件版本", r.firmware]
  ];
  box.className = "";
  box.innerHTML = rows.map(function (row) {
    return '<div class="kv"><span>' + esc(row[0]) + '</span><code>' +
      esc(miotVal(row[1]) || "读不到") + '</code></div>';
  }).join("");
  const mic = $("#btnMicMute");
  if (mic) mic.textContent = r.mic_mute ? "取消麦克风静音" : "麦克风静音";
  const nd = $("#btnNoDisturb");
  if (nd) nd.textContent = r.no_disturb ? "关掉勿扰" : "打开勿扰";
}

async function loadMiotState(silent) {
  const box = $("#miotState");
  try {
    const r = await api("miot_state");
    renderMiotState(r);
    if (!silent) toast("MIoT 状态已读取");
  } catch (e) {
    const msg = (e && e.message) || e;
    if (box) { box.className = "empty"; box.textContent = "读取失败：" + msg; }
    if (!silent) toast("MIoT 状态读取失败：" + msg, true);
  }
}

async function toggleMiot(key, name) {
  const cur = miotState ? miotState[key] : false;
  const on = !(cur === true);
  try {
    const r = await api(key === "mic_mute" ? "set_mic_mute" : "set_no_disturb", { on: on });
    toast(name + (on ? "已打开" : "已关闭") + " · " +
      (r && r.applied ? "已生效" : "已下发，设备约 30 秒后上报生效"));
    loadMiotState(true);
    setTimeout(function () { loadMiotState(true); }, 35000);
  } catch (e) {
    toast(name + "失败：" + ((e && e.message) || e), true);
  }
}

async function miotSpeak() {
  const el = $("#miotText");
  const text = (el && el.value || "").trim();
  if (!text) { toast("先写要播报的内容", true); return; }
  try {
    const r = await api("miot_play_text", { text: text });
    toast(r && r.ok ? "已下发云端播报" : "播报失败（code " + (r && r.code) + "）", !(r && r.ok));
  } catch (e) {
    toast("云端播报失败：" + ((e && e.message) || e), true);
  }
}

async function miotWake() {
  try {
    const r = await api("miot_wake");
    toast(r && r.ok ? "已唤醒音箱" : "唤醒失败（code " + (r && r.code) + "）", !(r && r.ok));
  } catch (e) {
    toast("唤醒失败：" + ((e && e.message) || e), true);
  }
}

/* ---------------- 触发词 ---------------- */

async function loadRules() {
  const r = await api("get_rules");
  state.rules = r;
  renderChips();
}

function renderChips() {
  const kw = $("#kwChips"), ex = $("#exChips"), wu = $("#wakeupChips");
  kw.innerHTML = ""; ex.innerHTML = "";
  if (wu) wu.innerHTML = "";
  (state.rules.agent_wakeup || []).forEach(w => wu && wu.appendChild(mkChip(w, false, "agent_wakeup")));
  (state.rules.keyword || []).forEach(w => kw.appendChild(mkChip(w, false, "keyword")));
  (state.rules.exclude_keyword || []).forEach(w => ex.appendChild(mkChip(w, true, "exclude_keyword")));
}

function mkChip(word, isEx, kind) {
  const c = document.createElement("span");
  c.className = "chip" + (isEx ? " ex" : "");
  c.textContent = word;
  const b = document.createElement("button");
  b.textContent = "×";
  b.title = "删除";
  b.onclick = async () => {
    const list = (state.rules[kind] || []).filter(x => x !== word);
    await api("set_rules", { patch: { [kind]: list } });
    await loadRules();
  };
  c.appendChild(b);
  return c;
}

async function addChip(kind, inputEl) {
  const v = inputEl.value.trim();
  if (!v) return;
  const list = (state.rules[kind] || []).slice();
  if (!list.includes(v)) list.push(v);
  await api("set_rules", { patch: { [kind]: list } });
  inputEl.value = "";
  await loadRules();
}

async function doJudge() {
  const q = $("#judgeInput").value.trim();
  if (!q) return;
  const r = await api("judge", { query: q });
  const box = $("#judgeOutput");
  box.className = "judge r-" + r.route;
  const title = r.route === "ai" ? "会转给智能体"
              : r.route === "control" ? "对话控制词"
              : "交给小爱原生";
  box.innerHTML =
    '<div class="j-title">' + title + "</div>" +
    '<div class="j-detail">' + esc(r.detail) + "</div>" +
    (r.stripped && r.stripped !== r.query
      ? '<div class="j-stripped">剥离后问的是：' + esc(r.stripped) + "</div>" : "");
}

/* ---------------- 历史 / 设备 ---------------- */

async function loadHistory() {
  const box = $("#historyList");
  box.innerHTML = '<div class="empty">拉取中…</div>';
  try {
    const recs = await api("history", { limit: 30 });
    if (!recs.length) { box.innerHTML = '<div class="empty">没有记录</div>'; return; }
    box.innerHTML = "";
    recs.forEach(r => {
      const d = document.createElement("div");
      d.className = "rec";
      const t = r.time ? new Date(r.time).toLocaleString("zh-CN") : "";
      d.innerHTML =
        '<span class="rec-time">' + esc(t) + "</span>" +
        '<div class="rec-q">' + esc(r.query || "") + "</div>" +
        (r.xiaoai ? '<div class="rec-a">小爱：' + esc(r.xiaoai) + "</div>" : "");
      box.appendChild(d);
    });
    const recent = $("#recentList");
    recent.innerHTML = "";
    recs.slice(0, 5).forEach(r => {
      const d = document.createElement("div");
      d.className = "rec";
      d.innerHTML = '<div class="rec-q">' + esc(r.query || "") + "</div>";
      recent.appendChild(d);
    });
  } catch (e) {
    box.innerHTML = '<div class="empty">拉取失败：' + esc(String(e.message || e)) + "</div>";
  }
}

async function loadDevices() {
  const box = $("#deviceList");
  box.innerHTML = '<div class="empty">拉取中…</div>';
  try {
    const devs = await api("devices");
    box.innerHTML = "";
    devs.forEach(d => {
      const row = document.createElement("div");
      row.className = "dev-row";
      row.innerHTML =
        "<span>" + esc(d.name || "") + "</span>" +
        '<span class="dev-badge">' + esc(d.model || "") + "</span>" +
        '<span style="color:var(--text-3);font-size:11px">' + esc(d.mac || "") + "</span>" +
        (d.is_speaker ? '<span class="dev-badge">音箱</span>' : "");
      box.appendChild(row);
    });
  } catch (e) {
    box.innerHTML = '<div class="empty">拉取失败：' + esc(String(e.message || e)) + "</div>";
  }
}

/* ---------------- 日志 ---------------- */

function startLogPolling() {
  if (state.logTimer) return;
  state.logOffset = 0;
  $("#logBox").textContent = "";
  state.logTimer = setInterval(pullLogs, 1500);
  pullLogs();
}

function stopLogPolling() {
  if (state.logTimer) { clearInterval(state.logTimer); state.logTimer = null; }
}

async function pullLogs() {
  try {
    const r = await api("engine_logs", { offset: state.logOffset });
    if (r.lines && r.lines.length) {
      state.logOffset = r.total;
      const box = $("#logBox");
      box.textContent += r.lines.join("\n") + "\n";
      if ($("#logAuto").checked) box.scrollTop = box.scrollHeight;
    }
  } catch (e) {}
}

/* ---------------- 工具 ---------------- */

function esc(s) {
  return String(s === undefined || s === null ? "" : s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

/* ---------------- 绑定 ---------------- */

function bind() {
  $$(".nav-item").forEach(b => b.onclick = () => showPage(b.dataset.page));

  $("#btnEngineToggle").onclick = engineToggle;
  $("#btnEngineRestart").onclick = engineRestart;
  $("#enginePill").onclick = engineToggle;
  $("#btnHealth").onclick = runHealth;
  $("#btnRefreshRecent").onclick = loadHistory;

  const bc = $("#btnLoadCloned");
  if (bc) bc.onclick = () => loadClonedVoices();

  $("#btnSpeak").onclick = doSpeak;
  $("#btnPreview").onclick = doPreview;
  $("#btnAskThenSpeak").onclick = doAskThenSpeak;
  $("#speakSpeed").oninput = e => $("#vSpeed").textContent = parseFloat(e.target.value).toFixed(2);
  $("#speakVol").oninput = e => {
    const v = parseFloat(e.target.value);
    $("#vVol").textContent = v.toFixed(2);
    $("#vVol").style.color = v > 5 ? "var(--red)" : "";
  };
  $("#speakPitch").oninput = e => $("#vPitch").textContent = e.target.value;

  $("#audSpeed").oninput = e => $("#vSpeed2").textContent = parseFloat(e.target.value).toFixed(2);
  $("#audVol").oninput = e => {
    const v = parseFloat(e.target.value);
    $("#vVol2").textContent = v.toFixed(2);
    $("#vVol2").style.color = v > 5 ? "var(--red)" : "";
  };
  $("#audPitch").oninput = e => $("#vPitch2").textContent = e.target.value;
  const srcSel = $("#ttsSource");
  if (srcSel) srcSel.onchange = function () { syncTtsSourceUI(); saveTtsSource(); };

  $("#btnSaveAgent").onclick = saveAgent;
  $("#btnSavePrompt").onclick = savePrompt;
  $("#btnRefreshModels").onclick = loadModels;
  $("#btnAsk").onclick = doAsk;
  $("#setMaxTokens").oninput = e => $("#vMaxTok").textContent = e.target.value;
  $("#btnToggleKey").onclick = () => {
    const f = $("#setApiKey");
    const show = f.type === "password";
    f.type = show ? "text" : "password";
    $("#btnToggleKey").textContent = show ? "隐藏" : "显示";
  };

  $("#btnAddWakeup").onclick = () => addChip("agent_wakeup", $("#wakeupInput"));
  $("#wakeupInput").onkeydown = e => { if (e.key === "Enter") { e.preventDefault(); addChip("agent_wakeup", $("#wakeupInput")); } };
  $("#btnAddKw").onclick = () => addChip("keyword", $("#kwInput"));
  $("#kwInput").onkeydown = e => { if (e.key === "Enter") { e.preventDefault(); addChip("keyword", $("#kwInput")); } };
  $("#btnAddEx").onclick = () => addChip("exclude_keyword", $("#exInput"));
  $("#exInput").onkeydown = e => { if (e.key === "Enter") { e.preventDefault(); addChip("exclude_keyword", $("#exInput")); } };
  $("#btnJudge").onclick = doJudge;
  $("#judgeInput").onkeydown = e => { if (e.key === "Enter") { e.preventDefault(); doJudge(); } };

  $("#btnRefreshHistory").onclick = loadHistory;
  $("#btnRefreshDevices").onclick = loadDevices;
  const volReadBtn = $("#btnReadVolume");
  if (volReadBtn) volReadBtn.onclick = readSpeakerVolume;
  const volApplyBtn = $("#btnApplyVolume");
  if (volApplyBtn) volApplyBtn.onclick = applySpeakerVolume;
  const volSlider = $("#speakerVol");
  if (volSlider) volSlider.oninput = e => $("#speakerVolLabel").textContent = e.target.value;
  const vpBtn = $("#btnRefreshViewport");
  if (vpBtn) vpBtn.onclick = updateViewportInfo;
  const miotReadBtn = $("#btnRefreshMiot");
  if (miotReadBtn) miotReadBtn.onclick = function () { loadMiotState(); };
  const micMuteBtn = $("#btnMicMute");
  if (micMuteBtn) micMuteBtn.onclick = function () { toggleMiot("mic_mute", "麦克风静音"); };
  const noDisturbBtn = $("#btnNoDisturb");
  if (noDisturbBtn) noDisturbBtn.onclick = function () { toggleMiot("no_disturb", "勿扰"); };
  const miotWakeBtn = $("#btnMiotWake");
  if (miotWakeBtn) miotWakeBtn.onclick = miotWake;
  const miotSpeakBtn = $("#btnMiotSpeak");
  if (miotSpeakBtn) miotSpeakBtn.onclick = miotSpeak;
  const miotTextEl = $("#miotText");
  if (miotTextEl) miotTextEl.onkeydown = function (e) {
    if (e.key === "Enter") { e.preventDefault(); miotSpeak(); }
  };
  $("#btnClearLog").onclick = () => { $("#logBox").textContent = ""; state.logOffset = 0; };
}

async function loadPaths() {
  try {
    const p = await api("get_paths");
    $("#pathConfig").textContent = p.config || "";
    $("#pathEnv").textContent = p.env || "";
    $("#pathToken").textContent = p.token || "";
    $("#pathRoot").textContent = p.user_home || "";
  } catch (e) {}
}

async function init() {
  $$("i[data-ico]").forEach(el => { el.style.webkitMaskImage = svgUrl(ICONS[el.dataset.ico]); el.style.maskImage = svgUrl(ICONS[el.dataset.ico]); });
  bind();

  await Promise.all([loadPaths(), loadOverview(), loadVoices(), loadSettings(), loadRules()]);
  state.engineTimer = setInterval(loadOverview, 4000);
  syncTouchMode();
  updateViewportInfo();
  scrollNavIntoView();
  toast("就绪");
  reportDiagnostics();

  /* 折叠屏展开/收起、横竖屏切换、安卓地址栏收放 —— 视口变了要跟着变 */
  let vpTimer = null;
  const onViewportChange = () => {
    clearTimeout(vpTimer);
    vpTimer = setTimeout(() => {
      syncTouchMode();
      updateViewportInfo();
      scrollNavIntoView();
      loadOverview();
    }, 160);
  };
  window.addEventListener("resize", onViewportChange);
  window.addEventListener("orientationchange", onViewportChange);
  if (window.visualViewport) {
    window.visualViewport.addEventListener("resize", onViewportChange);
  }
}

window.__errors = [];
window.addEventListener("error", e => {
  window.__errors.push(String(e.message) + " @ " + e.filename + ":" + e.lineno);
});
window.addEventListener("unhandledrejection", e => {
  window.__errors.push("unhandled: " + String(e.reason && e.reason.message || e.reason));
});

if (window.pywebview && window.pywebview.api) init();
else window.addEventListener("pywebviewready", init, { once: true });

/* ---------------- 前端自检 ---------------- */

async function reportDiagnostics() {
  const cs = (sel, prop) => {
    const el = document.querySelector(sel);
    return el ? getComputedStyle(el)[prop] : "";
  };
  const d = {
    title: document.title,
    pages: document.querySelectorAll(".page").length,
    navItems: document.querySelectorAll(".nav-item").length,
    activePage: (document.querySelector(".page.is-active") || {}).id,
    voiceCards: document.querySelectorAll(".voice-card").length,
    chips: document.querySelectorAll(".chip").length,
    statModel: (document.querySelector("#statModel") || {}).textContent,
    statVoice: (document.querySelector("#statVoice") || {}).textContent,
    apiReady: !!(window.pywebview && window.pywebview.api),
    errors: window.__errors || [],
    // 版面取证：CSS 生效了没、布局塌了没
    navWidth: (document.querySelector(".nav-item") || {}).offsetWidth || 0,
    pageWidth: (document.querySelector(".page.is-active") || {}).offsetWidth || 0,
    bodyBg: cs("body", "backgroundColor"),
    bodyFont: cs("body", "fontFamily").split(",")[0],
    cardCount: document.querySelectorAll(".card").length,
  };
  try {
    await api("diagnostics", { payload: d });
    state.diag = d;
  } catch (e) {}
}
