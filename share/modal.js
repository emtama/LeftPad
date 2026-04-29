//=============================================================
//	モーダル
//=============================================================
class ModalManager {
    constructor() {
        this.bind(); // インスタンス生成時にイベントバインドを実行
    }

    bind() {
        // ドキュメント全体にクリックイベントを委譲して監視
        document.addEventListener('click', (e) => {

            //=========================
            // open（モーダルを開く）
            //=========================
            const open = e.target.closest('[data-modal-open]'); // クリックされた要素、またはその親に data-modal-open 属性があるか確認
            if (open) {
                const id = open.dataset.modalOpen; // data-modal-open の値（モーダル識別子）を取得
                const modal = document.querySelector(`[data-modal="${id}"]`); // 対応するモーダル要素を取得
                if (modal) modal.showModal(); // モーダルが存在すれば表示（<dialog>要素のAPI）
            }

            //=========================
            // close button（ボタンで閉じる）
            //=========================
            const close = e.target.closest('[data-modal-close]'); // data-modal-close 属性を持つ要素を検出
            if (close) {
                const modal = close.closest('dialog'); // その要素が属する <dialog> を取得
                if (modal) modal.close(); // モーダルを閉じる
            }

            //=========================
            // backdrop close（外側クリックで閉じる）
            //=========================
            if (e.target.tagName === 'DIALOG') { // クリック対象が <dialog> 自体かどうか判定
                const rect = e.target.getBoundingClientRect(); // モーダルの表示領域（位置とサイズ）を取得
                const inside =
                    e.clientX >= rect.left &&   // クリック位置が左端以上か
                    e.clientX <= rect.right &&  // クリック位置が右端以下か
                    e.clientY >= rect.top &&    // クリック位置が上端以上か
                    e.clientY <= rect.bottom;   // クリック位置が下端以下か

                if (!inside) e.target.close(); // モーダル外（背景）クリックなら閉じる
            }
        });
    }
}

// クラスをインスタンス化して機能を有効化
new ModalManager();