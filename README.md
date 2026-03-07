# NewAPI 自動簽到

使用 Playwright 帶入預先取得的 cookies，自動執行 NewAPI 服務的每日簽到。支援 GitHub Actions 排程執行。

## 安裝

```bash
pip install playwright requests
playwright install
```

## 設定

設定依以下優先順序載入：
1. 環境變數（用於 GitHub Actions）
2. `config.json` 檔案（用於本機開發）

### 設定格式

```json
{
  "accounts": [
    {
      "name": "帳號 A",
      "domain": "https://example.com",
      "client_id": "linuxdo-oauth-id",
      "endpoint": "/api/user/sign_in"
    }
  ],
  "notifications": [
    {
      "type": "ntfy",
      "url": "https://ntfy.sh/your-topic"
    }
  ]
}
```

### 帳號欄位

| 欄位 | 必填 | 說明 |
|------|------|------|
| `name` | 否 | 帳號名稱，用於日誌識別 |
| `domain` | 是 | API 網址 |
| `client_id` | 是 | LinuxDo OAuth client ID（供 `gen_cookies.py` 使用） |
| `endpoint` | 否 | 簽到端點，預設 `/api/user/sign_in` |
| `disabled` | 否 | 設為 `true` 跳過此帳號 |

## 使用方式

### 1. 產生 Cookies

在本機執行，會開啟瀏覽器讓你手動登入 LinuxDo（處理 hCaptcha），登入後自動對每個帳號執行 OAuth 取得 cookies：

```bash
python gen_cookies.py                  # 預設 chromium
python gen_cookies.py --channel chrome # 用本機 Chrome
```

完成後會：
- 儲存 `cookies_cache.json`（本機直接使用）
- 印出 JSON 字串（貼到 GitHub Variable `COOKIES_CACHE`）

### 2. 執行簽到

```bash
python checkin.py                        # 預設 headless + chromium
python checkin.py --no-headless          # 顯示瀏覽器視窗
python checkin.py --channel chrome       # 指定瀏覽器
```

## 通知

簽到成功、已簽到、失敗時都會發送通知。

### ntfy

```json
{
  "type": "ntfy",
  "url": "https://ntfy.sh/your-topic"
}
```

## GitHub Actions

在 Repository 設定以下 Variables：

| 名稱 | 必填 | 說明 |
|------|------|------|
| `CHECKIN_ACCOUNTS` | 是 | accounts JSON 陣列 |
| `COOKIES_CACHE` | 是 | `gen_cookies.py` 產出的 cookies JSON |
| `CHECKIN_NOTIFY` | 否 | notifications JSON 陣列 |

Workflow 每 6 小時自動執行一次，也可手動觸發。

Cookies 過期時，需在本機重新執行 `gen_cookies.py` 並更新 `COOKIES_CACHE` Variable。
