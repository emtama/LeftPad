/**
 * LEFTPAD WebSocket 共通管理モジュール
 */
const WS_PORT = 8765;

// グローバル状態
let ws = null;
let isAuthed = false;

/**
 * WebSocket接続と認証を行う
 * @param {string} ip 接続先IP
 * @param {string} token 認証トークン
 * @param {Object} callbacks イベントハンドラ (onAuthSuccess, onMessage, onError, onConnChange)
 */
function connectWS(ip, token, callbacks = {}) {
    const wsProtocol = "ws"; 
    const wsUrl = `${wsProtocol}://${ip}:${WS_PORT}`;
    
    console.log(`Connecting to: ${wsUrl}`);
    
    try {
        ws = new WebSocket(wsUrl);
    } catch (err) {
        if (callbacks.onError) callbacks.onError("WebSocketの作成に失敗しました", err);
        return;
    }

    // 接続タイムアウト監視 (5秒)
    const timeout = setTimeout(() => {
        if (ws.readyState !== WebSocket.OPEN) {
            ws.close();
            if (callbacks.onError) callbacks.onError("接続がタイムアウトしました");
        }
    }, 5000);

    ws.onopen = () => {
        clearTimeout(timeout);
        console.log("Connected. Sending auth token...");
        ws.send(JSON.stringify({ type: 'auth', token: token }));
    };

    ws.onmessage = (e) => {

        console.log("on message!", e.data); // JSON.parse前の生の文字列を出す

        try {
            const data = JSON.parse(e.data);
            
            // 認証レスポンスの処理
            if (data.type === 'auth') {
                isAuthed = !!data.ok;
                if (data.ok) {
                    // 認証成功時に情報を保存
                    console.log(`save ip and token: ${ip} ok: ${token}`);
                    localStorage.setItem('server_ip', ip);
                    localStorage.setItem('auth_token', token);
                    
                    if (callbacks.onAuthSuccess) callbacks.onAuthSuccess(token);
                } else {
                    if (callbacks.onError) callbacks.onError("認証に失敗しました。トークンが無効です。");
                }
            } else {                
                // その他のメッセージを個別のページへ渡す
                if (callbacks.onMessage) callbacks.onMessage(data);
            }
            
        } catch (err) {
            console.error("Data parse error", err);
        }
    };

    ws.onerror = (err) => {
        clearTimeout(timeout);
        if (callbacks.onError) callbacks.onError("サーバーに接続できませんでした", err);
    };

    ws.onclose = () => {
        console.log("WebSocket Closed");
        isAuthed = false;
        if (callbacks.onConnChange) callbacks.onConnChange(false, "Disconnected");
        // gesture_input等で再接続が必要な場合のためのフック
        if (callbacks.onClose) callbacks.onClose();
    };
}

/**
 * サーバーにメッセージを送信する
 * @param {Object} obj 送信するオブジェクト
 */
function sendMessage(obj) {
    if (ws && ws.readyState === 1 && isAuthed) {
        ws.send(JSON.stringify(obj));
    }
}