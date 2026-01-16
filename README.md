# NewAPI 自動簽到

使用 Playwright 自動執行 NewAPI 服務的每日簽到，支援 GitHub Actions 排程執行。

## 安裝

```bash
pip install playwright
playwright install
```

## 設定

設定檔依以下優先順序載入：
1. `CHECKIN_CONFIG` 環境變數（用於 GitHub Actions）
2. `config.json` 檔案（用於本機開發）

### 設定格式

```json
[
  {
    "name": "帳號 A",
    "domain": "https://example.com",
    "api_user": "your-api-user-value",
    "cookies": "session=xxx",
    "endpoint": "/api/user/sign_in"
  }
]
```

| 欄位 | 必填 | 說明 |
|------|------|------|
| `name` | 否 | 帳號名稱，用於日誌識別 |
| `domain` | 是 | API 網址 |
| `api_user` | 是 | `new-api-user` 標頭值 |
| `cookies` | 是 | Cookie 字串或物件 |
| `endpoint` | 否 | 簽到端點，預設 `/api/user/sign_in` |

## 執行

```bash
python checkin.py
```

指定瀏覽器：

```bash
python checkin.py --channel msedge
```

## GitHub Actions

在 Repository 設定 `CHECKIN_CONFIG` Secret，填入 JSON 設定字串即可。
