/* 浏览器版的桥接垫片
 * ---------------------------------------------------------------------------
 * 桌面版（小智控制台.app）用 pywebview：页面以 file:// 加载，pywebview 自己
 * 注入 window.pywebview.api。
 *
 * 浏览器版（局域网 HTTP 服务）里没有 pywebview，这里补一个等价物：
 * 把 api.call / api.task_status 换成 fetch 请求打到 /api/*。
 *
 * 判据是 location.protocol：file:// 一律不接管（留给 pywebview），
 * 其余（http/https）一律接管。这样**同一份 index.html / app.js 两边都能用**，
 * 不会出现「改了浏览器版却把桌面版弄坏」这种事。
 */
(function () {
  if (location.protocol === "file:") return;   // 桌面版：pywebview 自己管

  const TOKEN_KEY = "xiaozhi.token";

  function readToken() {
    let q = "";
    try { q = new URLSearchParams(location.search).get("token") || ""; } catch (e) {}
    if (q) {
      try { localStorage.setItem(TOKEN_KEY, q); } catch (e) {}
      // 把 token 从地址栏抹掉，免得被复制走或者截进截图
      try {
        const u = new URL(location.href);
        u.searchParams.delete("token");
        history.replaceState(null, "", u.toString());
      } catch (e) {}
      return q;
    }
    try { return localStorage.getItem(TOKEN_KEY) || ""; } catch (e) { return ""; }
  }

  const TOKEN = readToken();

  async function post(path, body) {
    let resp;
    try {
      resp = await fetch(path, {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-Token": TOKEN },
        body: JSON.stringify(body || {}),
        credentials: "same-origin",
      });
    } catch (e) {
      throw new Error("连不上小智控制台服务：" + ((e && e.message) || e));
    }
    if (resp.status === 401) {
      try { localStorage.removeItem(TOKEN_KEY); } catch (e) {}
      throw new Error("访问令牌无效，请用带 token 的链接重新打开");
    }
    if (!resp.ok) {
      let msg = "HTTP " + resp.status;
      try {
        const j = await resp.json();
        if (j && j.error) msg = j.error;
      } catch (e) {}
      throw new Error(msg);
    }
    return await resp.json();
  }

  const httpApi = {
    async call(name, payload) {
      return await post("/api/call", { name: name, payload: payload || {} });
    },
    async task_status(taskId) {
      return await post("/api/task_status", { task_id: taskId });
    },
  };

  window.pywebview = window.pywebview || {};
  window.pywebview.api = httpApi;
  window.pywebview.isBrowser = true;
})();
