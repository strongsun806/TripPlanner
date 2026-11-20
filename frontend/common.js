// TripPlanner 공통 — API 주소, 안내판(탭 · 키 상태), 글자 처리
const API_BASE = "http://localhost:8000";

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const hm = (t) => String(t ?? "").slice(0, 5);

// 표지판 그림 — 공항 안내 표지처럼 선 하나로 그린다
const PICT = {
  home: '<path d="M4 11l8-7 8 7"/><path d="M6 10v10h12V10"/><path d="M10 20v-6h4v6"/>',
  chat: '<path d="M4 5h16v11H9l-5 4z"/><path d="M8 9.5h8M8 12.5h5"/>',
  plans: '<rect x="4" y="5" width="16" height="15" rx="2"/><path d="M4 10h16M9 3v4M15 3v4"/><path d="M8 14h3"/>',
  logs: '<path d="M12 3v4"/><path d="M8 7h8l-1 6H9z"/><path d="M10 13l-2 8M14 13l2 8M7 21h10"/>',
};

// 안내판: 로고 + 표지판(화면 이동) + 키 상태. 각 페이지의 <header id="board" data-page="home|chat|plans|logs"> 에 그린다
function renderBoard() {
  const el = document.getElementById("board");
  const page = el.dataset.page;
  const tab = (href, id, label) =>
    `<a href="${href}" ${page === id ? 'aria-current="page"' : ""}>
       <span class="pict" aria-hidden="true"><svg viewBox="0 0 24 24">${PICT[id]}</svg></span><span class="l">${label}</span></a>`;
  el.className = "board";
  el.innerHTML = `
    <div class="board-inner">
      <a class="brand" href="index.html">
        <span class="brand-mark" aria-hidden="true">
          <svg viewBox="0 0 24 24" fill="#1c2c4c"><path d="M21 15.5v-2l-8-5V3.5a1.5 1.5 0 0 0-3 0V8.5l-8 5v2l8-2.5V18l-2 1.5V21l3.5-1 3.5 1v-1.5L13 18v-5z"/></svg>
        </span><b>TripPlanner</b>
      </a>
      <nav class="tabs" aria-label="화면">
        ${tab("index.html", "home", "홈")}${tab("chat.html", "chat", "대화")}${tab("plans.html", "plans", "내 일정")}${tab("logs.html", "logs", "실행 로그")}
      </nav>
      <div class="board-side"><div class="keys" id="keys" aria-label="API 키 상태"></div><div id="boardExtra"></div></div>
    </div>`;
  loadStatus();
  drawSky();
}

// 옆에서 본 TripPlanner 여객기 — 꼬리 날개는 신호 노랑, 창문 줄과 노랑 띠
const LINER = `<svg class="liner" viewBox="0 0 240 80" aria-hidden="true">
  <path d="M120 34 100 22h10l30 12z" fill="#aeb9cf"/>
  <path d="M24 40 4 32l14-1 30 7z" fill="#cfd7e7"/>
  <path d="M18 44c0-6 8-10 26-10h152c18 0 32 4 38 10-6 6-20 8-38 8H44c-16 0-26-2-26-8z" fill="#f2f5fb"/>
  <path d="M19 46h191c10 1 18 2 22 0-6 4-18 6-36 6H44c-14 0-23-2-25-6z" fill="#c9d2e3"/>
  <path d="M22 40 8 6h20l32 29z" fill="#ffc629"/>
  <path d="M15 14h12l9 8H19z" fill="#1c2c4c"/>
  <rect x="44" y="41.5" width="150" height="2.5" fill="#ffc629"/>
  <line x1="62" y1="38.5" x2="192" y2="38.5" stroke="#1c2c4c" stroke-width="3" stroke-dasharray="3 4"/>
  <path d="M214 38l10 1 3 3-13-1z" fill="#1c2c4c"/>
  <path d="M100 46 64 72h16l60-26z" fill="#dfe5f0"/>
  <rect x="92" y="52" width="36" height="11" rx="5.5" fill="#c3cde0"/>
  <ellipse cx="127" cy="57.5" rx="2" ry="5" fill="#1c2c4c"/>
  <circle class="strobe" cx="8" cy="7" r="2.2" fill="#fff"/>
  <circle class="navlight" cx="66" cy="71" r="2" fill="#ff5a4e"/>
</svg>`;

// 하늘에 여객기 두 대 — 큰 것은 가깝고 빠르게, 작은 것은 멀고 느리게. 지나간 뒤 한동안 쉰다
// 홈은 첫 화면에 따로 이륙 장면이 있어서 그리지 않는다
function drawSky() {
  if (document.querySelector(".sky") || document.getElementById("board").dataset.page === "home") return;
  const jet = (y, dur, delay, rise, size, o) =>
    `<div class="jet" style="--y:${y};--dur:${dur}s;--delay:${delay}s;--rise:${rise};--size:${size}px;--o:${o}">
       <span class="trail"></span><span class="trail"></span>${LINER}</div>`;
  document.body.insertAdjacentHTML("afterbegin", `<div class="sky" aria-hidden="true">
    ${jet("70%", 14, 1, "40vh", 150, .9)}${jet("26%", 24, -9, "12vh", 80, .6)}</div>`);
}

// 키는 값이 아니라 '설정됐는지'만 받아 온다 (NF3002)
let SERVER_STATUS = null;
async function loadStatus() {
  try {
    const res = await fetch(`${API_BASE}/api/status`);
    SERVER_STATUS = await res.json();
    const short = { GMS_KEY: "LLM", GEOAPIFY_KEY: "장소", VISUALCROSSING_KEY: "날씨", KAKAO_REST_KEY: "식당" };
    document.getElementById("keys").innerHTML = SERVER_STATUS.keys.map((k) =>
      `<span class="${k.set ? "" : "off"}" title="${esc(k.label)} — ${k.set ? "키 설정됨" : ".env 에 " + k.name + " 없음"}">
         <i></i>${short[k.name] ?? k.name}</span>`).join("");
    document.dispatchEvent(new CustomEvent("status", { detail: SERVER_STATUS }));
  } catch {
    document.getElementById("keys").innerHTML = `<span class="off"><i></i>서버 꺼짐</span>`;
    document.dispatchEvent(new CustomEvent("status", { detail: null }));
  }
}

// 답변 글자를 안전하게 HTML 로: 먼저 이스케이프한 뒤 **굵게**, 목록, 링크만 살린다
function richText(text) {
  const lines = esc(text).split("\n");
  let html = "", inList = false;
  for (let line of lines) {
    line = line
      .replace(/\*\*(.+?)\*\*/g, "<b>$1</b>")
      .replace(/\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>')
      .replace(/(^|[\s(])(https?:\/\/[^\s<)]+)/g, '$1<a href="$2" target="_blank" rel="noopener">$2</a>');
    const item = line.match(/^\s*(?:[-*•]|\d+\.)\s+(.*)$/);
    if (item) {
      if (!inList) { html += "<ul>"; inList = true; }
      html += `<li>${item[1]}</li>`;
    } else {
      if (inList) { html += "</ul>"; inList = false; }
      html += line.trim() ? `<p>${line.replace(/^#+\s*/, "")}</p>` : "";
    }
  }
  return html + (inList ? "</ul>" : "");
}

function storage(key, value) {
  try {
    if (value === undefined) return localStorage.getItem(key);
    if (value === null) localStorage.removeItem(key); else localStorage.setItem(key, value);
  } catch { return null; }
}
