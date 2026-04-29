/**
 * LEFTPAD Server Logic
 * DaisyUI統合版
 */

let gesture_shortcuts = {};
let gesture_labels = {};
let capturingKey = null;
let pressedKeys = new Set();

// 初期化処理
async function init() {
    try {
        // テーマに合わせてQRコードの色を指定（必要に応じて変更可能）
        const qr_fillcolor = "#1d232a"; // bg-base-100 dark
        const qr_backcolor = "#ffffff";
        
        // Python API呼び出し
        const data = await pywebview.api.get_init_data(qr_fillcolor, qr_backcolor);
        
        // データの反映
        document.getElementById('qr-img').src = 'data:image/png;base64,' + data.qr_image;
        document.getElementById('http-url').innerText = data.http_url;
        document.getElementById('vib-toggle').checked = data.vibration;
        
        gesture_shortcuts = data.gesture_shortcuts;
        gesture_labels = data.gesture_labels;
        
        renderGestures();
        addLog("システムの初期化が完了しました。");
    } catch (e) {
        addLog("エラー: 初期化に失敗しました。 " + e);
    }
}

// パネル切り替え
function switchPanel(id) {
    document.querySelectorAll('.panel').forEach(p => p.classList.remove('active'));
    document.getElementById('panel-' + id).classList.add('active');
}

// クライアントリストの更新 (Pythonから呼ばれる)
function updateClientList(clients) {
    const list = document.getElementById('client-list');
    const count = document.getElementById('client-count');
    list.innerHTML = '';
    count.innerText = clients.length;
    
    clients.forEach(c => {
        const div = document.createElement('div');
        div.className = "flex items-center gap-2";
        div.innerHTML = `<span class="badge badge-xs badge-success"></span> ${c}`;
        list.appendChild(div);
    });
}

// ジェスチャー編集UIの生成
function renderGestures() {
    const container = document.getElementById('gesture-shortcut-editor');
    container.innerHTML = '';

    Object.keys(gesture_labels).forEach(id => {
        const row = document.createElement('div');
        row.className = 'flex gap-4 items-center bg-base-200 p-4 rounded-xl shadow-sm hover:shadow-md transition-shadow';

        const shortcutText = (gesture_shortcuts[id] || []).join(' + ') || '未登録';

        row.innerHTML = `
            <div class="flex-none w-48">
                <div class="text-xs opacity-50 uppercase font-bold">${id}</div>
                <div class="font-bold">${gesture_labels[id]}</div>
            </div>
            <div class="flex-1 bg-base-300 p-3 rounded-lg font-mono text-primary min-h-[3rem] flex items-center" id="display-${id}">
                ${shortcutText}
            </div>
            <div class="flex-none flex gap-2">
                <button id="btn-${id}" class="btn btn-primary btn-sm w-20" onclick="startCapture('${id}')">記録</button>
                <button class="btn btn-ghost btn-sm btn-square text-error" onclick="clearShortcut('${id}')">
                    <span class="material-symbols-outlined">delete</span>
                </button>
            </div>
        `;
        container.appendChild(row);
    });
}

// キー記録の開始
function startCapture(id) {
    if (capturingKey) {
        const prevBtn = document.getElementById(`btn-${capturingKey}`);
        if(prevBtn) prevBtn.innerText = "記録";
    }
    
    capturingKey = id;
    pressedKeys.clear();
    const btn = document.getElementById(`btn-${id}`);
    btn.innerText = "停止...";
    btn.classList.add('btn-secondary');
    
    document.getElementById(`display-${id}`).innerText = "キーを入力してください...";
    addLog(`ジェスチャー [${id}] の記録を開始しました。`);
}

// ショートカットのクリア
async function clearShortcut(id) {
    gesture_shortcuts[id] = [];
    document.getElementById(`display-${id}`).innerText = "未登録";
    await pywebview.api.save_gesture_shortcut(id, []);
}

// グローバルキーイベント
window.addEventListener('keydown', (e) => {
    if (!capturingKey) return;
    e.preventDefault();

    let key = e.key;
    if (key === ' ') key = 'Space';
    if (key === 'Control') key = 'Ctrl';
    
    pressedKeys.add(key);
    
    const display = Array.from(pressedKeys);
    document.getElementById(`display-${capturingKey}`).innerText = display.join(' + ');
});

window.addEventListener('keyup', (e) => {
    if (!capturingKey) return;
    
    const finalKeys = Array.from(pressedKeys);
    if (finalKeys.length > 0) {
        saveAndFinish(capturingKey, finalKeys);
    }
    pressedKeys.clear();
});

async function saveAndFinish(id, keys) {
    gesture_shortcuts[id] = keys;
    await pywebview.api.save_gesture_shortcut(id, keys);
    
    const btn = document.getElementById(`btn-${id}`);
    if(btn) {
        btn.innerText = "記録";
        btn.classList.remove('btn-secondary');
    }
    capturingKey = null;
    addLog(`保存完了: ${keys.join(' + ')}`);
}

// 設定: 振動
async function toggleVib() {
    const val = document.getElementById('vib-toggle').checked;
    await pywebview.api.toggle_vibration(val);
    addLog(`ハプティクスを ${val ? 'ON' : 'OFF'} にしました。`);
}

// ログ管理
function addLog(msg) {
    const container = document.getElementById('log-container');
    const entry = document.createElement('div');
    const now = new Date().toLocaleTimeString();
    entry.innerHTML = `<span class="opacity-40">[${now}]</span> ${msg}`;
    container.appendChild(entry);
    container.scrollTop = container.scrollHeight;
}

function clearLogs() {
    document.getElementById('log-container').innerHTML = '';
}

// テキストのコピー
async function copyText(elementId) {
    const text = document.getElementById(elementId).innerText;
    try {
        await navigator.clipboard.writeText(text);
        addLog("URLをクリップボードにコピーしました。");
    } catch (err) {
        // Fallback for older environments
        const textArea = document.createElement("textarea");
        textArea.value = text;
        document.body.appendChild(textArea);
        textArea.select();
        document.execCommand('copy');
        document.body.removeChild(textArea);
    }
}

// ウィンドウ読み込み時に初期化
window.onload = init;