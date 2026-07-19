const TOKEN_KEY = "paperpilot_token";

/** @type {"login" | "register"} */
let authMode = "login";
let codeCooldownTimer = null;

function apiUrl(path) {
  return path.startsWith("/") ? path : `/${path}`;
}

async function api(path, options = {}) {
  const headers = { ...(options.headers || {}) };
  if (options.json !== undefined) headers["Content-Type"] = "application/json";
  const resp = await fetch(apiUrl(path), {
    method: options.method || "GET",
    headers,
    body: options.json !== undefined ? JSON.stringify(options.json) : options.body
  });
  const text = await resp.text();
  let data = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = { detail: text };
  }
  if (!resp.ok) {
    const detail = data?.detail;
    const msg = typeof detail === "string" ? detail : detail ? JSON.stringify(detail) : `HTTP ${resp.status}`;
    throw new Error(msg);
  }
  return data;
}

function setAuthStatus(text, isError = false) {
  const el = document.getElementById("authStatus");
  if (!el) return;
  el.textContent = text;
  el.classList.toggle("is-error", Boolean(isError));
}

function setMode(mode) {
  authMode = mode === "register" ? "register" : "login";
  const loginForm = document.getElementById("loginForm");
  const registerForm = document.getElementById("registerForm");
  const card = document.querySelector(".auth-card");

  document.querySelectorAll(".auth-tab").forEach((tab) => {
    const active = tab.getAttribute("data-mode") === authMode;
    tab.classList.toggle("is-active", active);
    tab.setAttribute("aria-selected", active ? "true" : "false");
  });

  if (loginForm) loginForm.hidden = authMode !== "login";
  if (registerForm) registerForm.hidden = authMode !== "register";
  if (card) card.setAttribute("data-mode", authMode);
  setAuthStatus("");
  const hint = document.getElementById("codeHint");
  if (hint) hint.textContent = "";
}

function startCooldown(seconds) {
  const btn = document.getElementById("sendCodeBtn");
  if (!btn) return;
  let left = seconds;
  btn.disabled = true;
  btn.textContent = `${left}s 后重发`;
  clearInterval(codeCooldownTimer);
  codeCooldownTimer = setInterval(() => {
    left -= 1;
    if (left <= 0) {
      clearInterval(codeCooldownTimer);
      btn.disabled = false;
      btn.textContent = "获取验证码";
      return;
    }
    btn.textContent = `${left}s 后重发`;
  }, 1000);
}

(async function boot() {
  const token = localStorage.getItem(TOKEN_KEY);
  if (token) {
    try {
      const resp = await fetch(apiUrl("/api/auth/me"), {
        headers: { Authorization: `Bearer ${token}` }
      });
      if (resp.ok) {
        location.replace("/app.html");
        return;
      }
    } catch {
      /* stay */
    }
    localStorage.removeItem(TOKEN_KEY);
  }
})();

document.querySelectorAll(".auth-tab").forEach((tab) => {
  tab.addEventListener("click", () => setMode(tab.getAttribute("data-mode") || "login"));
});

document.getElementById("sendCodeBtn")?.addEventListener("click", async () => {
  const hint = document.getElementById("codeHint");
  const target = document.getElementById("regEmail").value.trim();
  if (!target) {
    setAuthStatus("请先填写邮箱", true);
    return;
  }
  try {
    const data = await api("/api/auth/send-code", {
      method: "POST",
      json: { channel: "email", target }
    });
    startCooldown(60);
    let msg = data.message || "验证码已发送";
    if (data.dev_code) {
      msg += `（本地调试码：${data.dev_code}）`;
      const codeInput = document.getElementById("regCode");
      if (codeInput && !codeInput.value) codeInput.value = data.dev_code;
    }
    if (hint) hint.textContent = msg;
    setAuthStatus(data.delivery === "smtp" ? "验证码已发送到邮箱，请查收。" : "验证码已发送。");
  } catch (e) {
    setAuthStatus(e.message, true);
  }
});

document.getElementById("loginForm")?.addEventListener("submit", async (ev) => {
  ev.preventDefault();
  try {
    const data = await api("/api/auth/login", {
      method: "POST",
      json: {
        account: document.getElementById("loginAccount").value.trim(),
        password: document.getElementById("loginPassword").value
      }
    });
    localStorage.setItem(TOKEN_KEY, data.access_token);
    setAuthStatus("登录成功，正在进入…");
    location.href = "/app.html";
  } catch (e) {
    setAuthStatus(e.message, true);
  }
});

document.getElementById("registerForm")?.addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const payload = {
    username: document.getElementById("regUsername").value.trim(),
    password: document.getElementById("regPassword").value,
    code: document.getElementById("regCode").value.trim(),
    channel: "email",
    email: document.getElementById("regEmail").value.trim(),
    phone: null
  };
  try {
    const data = await api("/api/auth/register", { method: "POST", json: payload });
    localStorage.setItem(TOKEN_KEY, data.access_token);
    setAuthStatus("注册成功，正在进入…");
    location.href = "/app.html";
  } catch (e) {
    setAuthStatus(e.message, true);
  }
});

setMode("login");
