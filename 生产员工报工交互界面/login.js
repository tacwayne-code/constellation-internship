const form = document.querySelector("#loginForm");
const username = document.querySelector("#username");
const password = document.querySelector("#password");
const button = document.querySelector("#loginButton");
const error = document.querySelector("#loginError");

function showError(message) {
  error.textContent = message || "登录失败，请稍后重试";
  error.hidden = false;
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  error.hidden = true;
  if (!username.value || !password.value) {
    showError("请输入登录账号和密码");
    return;
  }
  button.disabled = true;
  try {
    const response = await fetch("/api/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      body: JSON.stringify({ username: username.value, password: password.value }),
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok || !payload.ok) throw new Error(payload.error || "登录失败");
    window.location.replace("/");
  } catch (err) {
    showError(err.message);
    button.disabled = false;
  }
});
