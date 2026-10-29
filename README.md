# ai-pr-review

個人免費 GitHub 帳號用的 PR AI 審查共用 workflow（Gemini API 免費額度，免自架機器）。

在任一專案加入 `.github/workflows/ai-pr-review.yml`：

```yaml
name: AI PR Review
on:
  pull_request_target:
    types: [opened, synchronize, reopened, ready_for_review]
concurrency:
  group: ai-pr-review-${{ github.event.pull_request.number }}
  cancel-in-progress: true
permissions:
  contents: read
  pull-requests: write
jobs:
  ai-review:
    uses: jhajha924/ai-pr-review/.github/workflows/ai-review.yml@v1
    secrets: inherit
```

再設定金鑰（輸入時不會顯示）：

```bash
gh secret set GEMINI_API_KEY -R <帳號>/<repo>
```

AI 只給建議（request-changes 或總結留言），不擋合併；不會 checkout 或執行 PR 的程式碼。
