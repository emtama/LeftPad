/*
目的：
リソースのキャッシュ、オフライン対応、バックグラウンド同期。
生存期間：
バックエンド処理が実行できる
アクセス：
DOMにはアクセスできない
概要：
フロントエンドとサーバーの間のプロキシサーバー的な感じ
注意：
WebSocketはService Workerでは管理しない: SWはHTTP(S)リクエストを傍受（Fetch）することは得意ですが、WebSocket通信（ws://）を中継することはできません。
*/

// キャッシュ
const CACHE_NAME = 'leftpad-cache-v1';
const ASSETS_TO_CACHE = [
  '/',
  '/index.html',
  '/pairing.html',
  '/smartphone.html',
  '/css/stylesheet.css',
  '/manifest.json'
];

// 1. インストール時：必要なファイルをキャッシュに保存
self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => {
      return cache.addAll(ASSETS_TO_CACHE);
    })
  );
});

// 2. アクティベート時：古いキャッシュの削除
self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) => {
      return Promise.all(
        keys.filter(key => key !== CACHE_NAME).map(key => caches.delete(key))
      );
    })
  );
});

// 3. フェッチ時：ネットワークより先にキャッシュをチェック（オフライン対応）
self.addEventListener('fetch', (event) => {
  event.respondWith(
    caches.match(event.request).then((response) => {
      return response || fetch(event.request);
    })
  );
});