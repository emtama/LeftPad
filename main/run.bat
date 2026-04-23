:: 冗長なコマンド表示を隠す
@echo off
:: 文字コードをutf-8に変更
chcp 65001 > nul

:: ── カレントディレクトリをbatファイルの場所に固定 ──────────
cd /d "%~dp0"

echo ============================================
echo   LeftPad Server - セットアップと起動
echo ============================================


:: ── ライブラリインストール ─────────────────────────────────
echo.
echo [2/3] ライブラリをインストール中...
pip install -r requirements.txt
if %errorlevel% neq 0 (
    echo.
    echo [ERROR] pip install に失敗した。上のエラーを確認すること。
    pause
    exit /b 1
)


:: ── サーバー起動 ───────────────────────────────────────────
echo.
echo [3/3] サーバーを起動中...
echo.
python "server.py"

echo.
echo サーバーが終了した
pause