# NewAPI 自動簽到

使用 Playwright 自動執行 NewAPI 服務的每日簽到，透過 LinuxDo OAuth 自動取得 cookies，支援 GitHub Actions 排程執行。

## 安裝

```bash
pip install playwright requests
playwright install
```

## 設定

設定檔依以下優先順序載入：
1. `CHECKIN_CONFIG` 環境變數（用於 GitHub Actions）
2. `config.json` 檔案（用於本機開發）

### 設定格式

```json
{
  "linuxdo": {
    "email": "user@example.com",
    "password": "password123"
  },
  "accounts": [
    {
      "name": "帳號 A",
      "domain": "https://example.com",
      "client_id": "linuxdo-oauth-id",
      "endpoint": "/api/user/sign_in"
    }
  ],
  "notifications": []
}
```

#### LinuxDo 登入

| 欄位 | 必填 | 說明 |
|------|------|------|
| `email` | 是 | LinuxDo 登入信箱 |
| `password` | 是 | LinuxDo 登入密碼 |

#### 帳號設定

| 欄位 | 必填 | 說明 |
|------|------|------|
| `name` | 否 | 帳號名稱，用於日誌識別 |
| `domain` | 是 | API 網址 |
| `client_id` | 是 | LinuxDo OAuth client ID |
| `endpoint` | 否 | 簽到端點，預設 `/api/user/sign_in` |

`api_user` 和 `cookies` 會透過 OAuth 自動取得，並快取至 `cookies_cache.json`。

## 通知

目前支援以下通知方式，請在設定檔的 `notifications` 列表中設定：

### ntfy

```json
{
  "type": "ntfy",
  "url": "https://ntfy.sh/your-topic"
}
```

當簽到成功且餘額增加，或發生錯誤時，會發送通知。

### 擴充通知方式

若要支援新的通知管道（例如 Telegram、Discord），請依照以下步驟：

1.  在 `utils/notify.py` 中建立新的類別，繼承 `Notifier`：

    ```python
    class MyNotifier(Notifier):
        def __init__(self, token):
            self.token = token

        def send(self, title: str, message: str):
            # Implement sending logic here
            pass
    ```

2.  修改 `utils/notify.py` 中的 `create_notifiers` 函式，加入新的類型判斷：

    ```python
    def create_notifiers(config_list):
        notifiers = []
        for cfg in config_list:
            if cfg.get("type") == "ntfy":
                notifiers.append(NtfyNotifier(cfg.get("url")))
            elif cfg.get("type") == "my_notify":
                notifiers.append(MyNotifier(cfg.get("token")))
        return notifiers
    ```

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

Workflow 會自動快取 `linuxdo_state.json` 和 `cookies_cache.json`，在每次執行間保留 LinuxDo 登入狀態及 OAuth cookies。
