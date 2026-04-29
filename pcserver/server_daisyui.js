// Panel switch
function switchPanel(id) {
  document.querySelectorAll('.panel').forEach(p => p.classList.add('hidden'));
  document.getElementById('panel-' + id).classList.remove('hidden');
}

// Copy URL
async function copyHTTPURL() {
  const el = document.getElementById('http-url');
  await navigator.clipboard.writeText(el.innerText);
}

// Logs
function addLog(msg) {
  const c = document.getElementById('log-container');
  const d = document.createElement('div');
  d.textContent = msg;
  c.appendChild(d);
  c.scrollTop = c.scrollHeight;
}

function clearLogs() {
  document.getElementById('log-container').innerHTML = '';
}

// Gesture UI
function renderGestures(data) {
  const container = document.getElementById('gesture-shortcut-editor');
  container.innerHTML = '';

  Object.keys(data).forEach(id => {
    const row = document.createElement('div');
    row.className = 'flex gap-2 items-center bg-base-200 p-2 rounded-box';

    row.innerHTML = `
      <div class="w-40">${data[id]}</div>
      <div class="flex-1 bg-base-300 p-2 rounded" id="display-${id}"></div>
      <button class="btn btn-primary btn-sm">記録</button>
      <button class="btn btn-error btn-sm">削除</button>
    `;

    container.appendChild(row);
  });
}

// Dummy init
renderGestures({ swipe: "スワイプ", tap: "タップ" });