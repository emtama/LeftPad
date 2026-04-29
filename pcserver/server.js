// ══════════════════════════════════════════════
// 初期化
// ══════════════════════════════════════════════
let gesture_shortcuts = {};
let gesture_labels = {};
let capturingKey = null;
let pressedKeys = new Set(); // 現在押されているキーを保持

// 初期化処理（サーバーから基本情報を受け取って設定）
async function init() {
    try {
        
        const root = document.documentElement;  // :root 要素（html要素）を取得
        const styles = getComputedStyle(root);  // :root に適用されているすべてのスタイル計算値を取得
        const qr_fillcolor = "#000000";//styles.getPropertyValue('--color-primary').trim();// 特定の変数名（例: --value）の値を抜き出す
        const qr_backcolor = "#ffffff";//styles.getPropertyValue('--color-surface').trim();// 特定の変数名（例: --value）の値を抜き出す
        //
        const data = await pywebview.api.get_init_data(qr_fillcolor, qr_backcolor);
        //
        document.getElementById('qr-img').src = 'data:image/png;base64,' + data.qr_image;
        document.getElementById('http-url').innerText = data.http_url;
        document.getElementById('ws-url').innerText = data.ws_url;
        document.getElementById('vib-toggle').checked = data.vibration;
        gesture_shortcuts = data.gesture_shortcuts;
        gesture_labels = data.gesture_labels;
        renderGestures(gesture_labels);

        // カラーパレットの適用
        if (data.colors) {
            const root = document.documentElement;
            for (const [key, value] of Object.entries(data.colors)) {
                root.style.setProperty(`--${key}`, value);
            }
        }

    } catch (e) {
        console.error("Init error:", e);
    }
}
// 接続ステータスアップデート
function connectionStatsUpdate(stats) {
    try {                    
        // 端末数を更新
        document.getElementById('client-count').innerText = stats.count;
        
        // 端末IPリストを更新
        const listEl = document.getElementById('client-list');
        if (stats.count > 0) {
            listEl.innerText = "接続中: " + stats.ips.join(', ');
        } else {
            listEl.innerText = "接続されている端末はありません";
        }
    } catch (e) {
        console.error("Stats update error:", e);
    }
}

// pywebviewready のタイミングで実行開始
window.addEventListener('pywebviewready', () => {
    init();
});

// ══════════════════════════════════════════════
// 共通
// ══════════════════════════════════════════════
// マウスの位置を追跡
document.addEventListener("mousemove", (e) => {
    document.documentElement.style.setProperty("--mx", e.clientX + "px");
    document.documentElement.style.setProperty("--my", e.clientY + "px");
    });
// パネルを切り替える
function switchPanel(panelId) {
    // 全パネルとナビボタンをリセット
    document.querySelectorAll('.panel').forEach(p => p.classList.remove('active'));
    document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));

    // 選択されたものだけアクティブ化
    document.getElementById(`panel-${panelId}`).classList.add('active');
    event.currentTarget.classList.add('active');
}

// ══════════════════════════════════════════════
// スマホ接続タブ
// ══════════════════════════════════════════════

// 2秒間だけ「Copied!」を表示する視覚演出
function showCopyFeedback(element) {
    const wait_time = 500;
    element.classList.add('copied_tooltip');// "copied_tooltip"の名前の要素を::afterとして追加。
    setTimeout(() => {
        element.classList.remove('copied_tooltip');// "copied_tooltip"の名前の要素を::afterとして追加。
    }, wait_time);
}
// URLをクリップボードにコピーする関数
async function copyHTTPURL() {
    const el = document.getElementById('http-url');
    const success = await pywebview.api.copy_to_clipboard(el.innerText);
    if (success) showCopyFeedback(el);
}        

// ══════════════════════════════════════════════
// ジェスチャー・ショートカットタブ
// ══════════════════════════════════════════════
// ジェスチャーとショートカットの対応を表示する関数
function renderGestures(gesture_labels) {
    const container = document.getElementById('gesture-shortcut-editor');
    container.innerHTML = ''; // 再描画用
    Object.keys(gesture_labels).forEach(id => {
        const row = document.createElement('div');
        row.className = 'gesture-row';
        row.innerHTML = `
            <div class="gesture-label">${gesture_labels[id]}</div>
            <div class="key-display" id="display-${id}">${(gesture_shortcuts[id] || []).join(' + ')}</div>
            <button onclick="startCapture('${id}')" class="btn btn-primary" id="btn-${id}">記録</button>
            <button onclick="deleteGesture('${id}')" class="btn btn-danger"  id="btn-delete-${id}">削除</button>
        `;
        container.appendChild(row);
    });
}
// ジェスチャーに対応するキーの記録を開始する関数
function startCapture(id) {
    if(capturingKey) return;
    capturingKey = id;
    pressedKeys.clear();
    const btn = document.getElementById(`btn-${id}`);
    btn.innerText = "キーを離して確定...";
    btn.classList.add('active');
}
// ジェスチャーに対応するキーの削除
async function deleteGesture(id) {
    gesture_shortcuts[id] = []; // ローカルデータを空に
    document.getElementById(`display-${id}`).innerText = '';
    await pywebview.api.save_gesture_shortcut(id, []); // Python側へ空リストを送信
}
// 複数キー記録のロジック (keydown で蓄積、keyup で送信)
window.addEventListener('keydown', (e) => {
    if(!capturingKey) return;
    e.preventDefault();

    // 修飾キーの正規化
    let key = e.key;
    if (key === "Control") key = "Ctrl";
    if (key === "Meta") key = "Win"; // Windowsキーなど

    pressedKeys.add(key);
    
    // リアルタイム表示用
    const display = Array.from(pressedKeys);
    document.getElementById(`display-${capturingKey}`).innerText = display.join(' + ');
});

window.addEventListener('keyup', (e) => {
    if(!capturingKey) return;
    
    // 全てのキーが離されたら確定して保存
    const finalKeys = Array.from(pressedKeys);
    if (finalKeys.length > 0) {
        saveAndFinish(capturingKey, finalKeys);
    }
    pressedKeys.clear();
});
// ジェスチャーに対応するキーの決定と保存
async function saveAndFinish(id, keys) {
    gesture_shortcuts[id] = keys;
    await pywebview.api.save_gesture_shortcut(id, keys);
    
    const btn = document.getElementById(`btn-${id}`);
    btn.innerText = "記録";
    btn.classList.remove('active');
    capturingKey = null;
}
// ══════════════════════════════════════════════
// 設定タブ
// ══════════════════════════════════════════════
// 振動の設定
async function toggleVib() {
    const val = document.getElementById('vib-toggle').checked;
    await pywebview.api.toggle_vibration(val);
}
// ══════════════════════════════════════════════
// ログタブ
// ══════════════════════════════════════════════
// Python側からログを受け取って表示する関数
function addLog(message) {
    const container = document.getElementById('log-container');
    const entry = document.createElement('div');
    entry.textContent = message;
    container.appendChild(entry);            
    // 常に最新（一番下）にスクロール
    container.scrollTop = container.scrollHeight;
}