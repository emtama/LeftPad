//=============================================================
//	モーダル
//=============================================================
class ModalWrapper extends HTMLElement {
    connectedCallback() {
        const text = this.getAttribute('text') || '';

        // 内部構造を生成
        this.innerHTML = `
            <dialog>
                <div class="modal-body">
                    <button class="modal-close"></button>
                    <p>${text}</p>
                </div>
            </dialog>
        `;

        this.dialog = this.querySelector('dialog');

        this.bind();
    }

    bind() {
        // =========================
        // 外部トリガー
        // =========================
        document.addEventListener('click', (e) => {
            const btn = e.target.closest('[data-open]');
            if (!btn) return;

            if (btn.dataset.open === this.id) {
                this.dialog.showModal();
            }
        });

        // =========================
        // 内部イベント
        // =========================
        this.dialog.addEventListener('click', (e) => {

            // closeボタン
            if (e.target.closest('.modal-close')) {
                this.dialog.close();
                return;
            }

            // backdropクリック
            if (e.target === this.dialog) {
                const r = this.dialog.getBoundingClientRect();
                const inside =
                    e.clientX >= r.left &&
                    e.clientX <= r.right &&
                    e.clientY >= r.top &&
                    e.clientY <= r.bottom;

                if (!inside) this.dialog.close();
            }
        });

        // Escキー
        this.dialog.addEventListener('cancel', (e) => {
            e.preventDefault();
            this.dialog.close();
        });
    }
}

customElements.define('modal-wrapper', ModalWrapper);