-- アプリの設定(フェーズ12)。キーと値の組で持つ。
-- 端末ごとの好みなので、JSON のエクスポート / インポートには含めない。
CREATE TABLE app_settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
