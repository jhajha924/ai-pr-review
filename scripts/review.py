"""呼叫 Gemini API（OpenAI 相容介面）審查 PR 差異，輸出審查內容檔與判定結果。

所有輸入都從環境變數讀取（PR 標題、說明不經 shell 展開，避免指令注入）：
  MODEL, FALLBACK_MODEL, RETRY_WAIT, MAX_DIFF_CHARS, PROMPT_FILE, DIFF_FILE, FILES_FILE, OUT_BODY,
  PR_TITLE, PR_BODY, GEMINI_API_KEY, RUN_URL, GITHUB_OUTPUT, MODELS_API_URL（測試用）

判定（寫入 GITHUB_OUTPUT 的 verdict）：
  REQUIRED_CHANGES／PASS：AI 正常回覆
  EMPTY：沒有程式差異，不呼叫 AI
  ERROR：未設定金鑰或呼叫 Gemini API 失敗
  BADJSON：AI 回覆不是約定的 JSON
"""

import json
import os
import time
import urllib.error
import urllib.request

API_URL = os.environ.get(
    "MODELS_API_URL",
    "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
)
VERDICTS = ("REQUIRED_CHANGES", "PASS")
RETRY_CODES = (429, 500, 502, 503, 504)
RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": list(VERDICTS)},
        "body": {"type": "string"},
    },
    "required": ["verdict", "body"],
}


def read(path):
    with open(path, encoding="utf-8", errors="replace") as f:
        return f.read()


def write_output(verdict, body):
    with open(os.environ["OUT_BODY"], "w", encoding="utf-8") as f:
        f.write(body)
    with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as f:
        f.write(f"verdict={verdict}\n")
    print(f"verdict={verdict}")


def strip_fence(text):
    """模型偶爾會把 JSON 包在 ``` 程式碼框裡，去掉後再解析。"""
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else ""
        text = text.rsplit("```", 1)[0]
    return text.strip()


def call_model(model, system_prompt, user_content):
    payload = {
        "model": model,
        "temperature": 0.2,
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": "review", "schema": RESPONSE_SCHEMA},
        },
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
    }
    req = urllib.request.Request(
        API_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {os.environ['GEMINI_API_KEY']}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        raw = resp.read().decode("utf-8", errors="replace")
    try:
        return json.loads(raw)["choices"][0]["message"]["content"]
    except (ValueError, KeyError, IndexError, TypeError):
        raise RuntimeError(f"API 回應無法解析：{raw[:300]!r}")


def call_with_retry(models, system_prompt, user_content, wait):
    """依序嘗試 models；遇到忙碌、額度或逾時錯誤就換下一個，回傳 (使用的模型, 內容)。"""
    last_error = None
    for i, model in enumerate(models):
        if i:
            time.sleep(wait)
        try:
            return model, call_model(model, system_prompt, user_content)
        except urllib.error.HTTPError as e:
            if e.code not in RETRY_CODES:
                raise
            last_error = e
        except (TimeoutError, urllib.error.URLError) as e:
            last_error = e
        print(f"::warning::{model} 失敗（{last_error}），改試下一個")
    raise last_error


def make_header(model):
    return (
        f"## 🤖 AI 審查（Gemini／{model}）\n"
        "> AI 意見僅供參考，是否合併由人決定。\n\n"
    )


def main():
    model = os.environ.get("MODEL", "gemini-3.8-flash")
    fallback = os.environ.get("FALLBACK_MODEL", "gemini-3.5-flash-lite")
    wait = float(os.environ.get("RETRY_WAIT", "15"))
    max_chars = int(os.environ.get("MAX_DIFF_CHARS", "40000"))
    run_url = os.environ.get("RUN_URL", "")
    header = make_header(model)

    diff = read(os.environ["DIFF_FILE"])
    if not diff.strip():
        write_output("EMPTY", header + "這個 PR 沒有程式差異，未執行 AI 審查。")
        return

    if not os.environ.get("GEMINI_API_KEY"):
        write_output(
            "ERROR",
            header
            + "⚠️ 尚未設定 Secret `GEMINI_API_KEY`，無法執行 AI 審查。\n\n"
            + "請在終端機執行：`gh secret set GEMINI_API_KEY -R <帳號>/<repo>`",
        )
        return

    truncated = len(diff) > max_chars
    if truncated:
        diff = diff[:max_chars]

    user_content = (
        f"PR 標題：{os.environ.get('PR_TITLE', '')}\n\n"
        f"PR 說明：\n{os.environ.get('PR_BODY', '') or '（空白）'}\n\n"
        f"變更檔案：\n{read(os.environ['FILES_FILE'])}\n"
        f"差異（diff）：\n{diff}\n"
    )

    try:
        models = [model, model] + ([fallback] if fallback and fallback != model else [])
        used, content = call_with_retry(
            models, read(os.environ["PROMPT_FILE"]), user_content, wait
        )
        header = make_header(used)
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")[:500]
        write_output(
            "ERROR",
            header
            + f"⚠️ AI 審查暫時無法執行（HTTP {e.code}）。\n\n"
            + f"```\n{detail}\n```\n\n執行紀錄：{run_url}",
        )
        return
    except Exception as e:  # 逾時、連線失敗、回應無法解析等
        write_output(
            "ERROR",
            header
            + f"⚠️ AI 審查暫時無法執行（{type(e).__name__}）。\n\n"
            + f"```\n{str(e)[:500]}\n```\n\n執行紀錄：{run_url}",
        )
        return

    try:
        result = json.loads(strip_fence(content))
        verdict = result["verdict"]
        body = result["body"]
        if verdict not in VERDICTS or not isinstance(body, str):
            raise ValueError(verdict)
    except (ValueError, KeyError, TypeError):
        write_output(
            "BADJSON",
            header
            + "⚠️ AI 輸出格式異常，無法判定。以下為原始回覆：\n\n"
            + f"```\n{content[:3000]}\n```\n\n執行紀錄：{run_url}",
        )
        return

    if truncated:
        body += f"\n\n---\n⚠️ 差異過長，僅審查前 {max_chars} 字元。"
    write_output(verdict, header + body)


if __name__ == "__main__":
    main()
