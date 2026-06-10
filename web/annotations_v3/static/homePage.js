import {setUsername} from "./core/datasetContext.js";

const loginView = document.getElementById("loginView");
const appView = document.getElementById("appView");
const topbar = document.querySelector(".topbar");
const loginForm = document.getElementById("loginForm");
const usernameInput = document.getElementById("loginUsernameInput");
const sessionLine = document.getElementById("sessionLine");
const logoutBtn = document.getElementById("logoutBtn");
const refreshBtn = document.getElementById("refreshBtn");

function showApp(username) {
  loginView.classList.add("hidden");
  appView.classList.remove("hidden");
  topbar.classList.remove("hidden");
  sessionLine.textContent = `当前用户：${username}`;
}

function showLogin() {
  loginView.classList.remove("hidden");
  appView.classList.add("hidden");
  topbar.classList.add("hidden");
}

const existing = window.localStorage.getItem("annotations_v3_username");
if (existing) showApp(existing);
else showLogin();

loginForm.addEventListener("submit", (event) => {
  event.preventDefault();
  showApp(setUsername(usernameInput.value));
});

logoutBtn.addEventListener("click", () => {
  window.localStorage.removeItem("annotations_v3_username");
  showLogin();
});

refreshBtn.addEventListener("click", () => window.location.reload());
