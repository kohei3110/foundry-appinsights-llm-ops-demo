# Microsoft Foundry + Application Insights LLM 運用デモ手順書

## 1. このデモで伝えること

Microsoft Foundry のポリシー回答 Agent に意図的な品質劣化、遅延、ツール障害を発生させ、次の運用ループを実演します。

1. 正常時の基準を確認する
2. 利用者影響を再現する
3. Conversation ID、Response ID、Trace ID で証跡を関連付ける
4. Application Insights で原因箇所を特定する
5. Azure SRE Agent が読み取り専用で証拠、除外仮説、推定原因を整理する
6. Azure Copilot Observability Agent がネクストアクションを提示する
7. 人間が変更を承認し、固定評価ケースで結果を確認する

**標準所要時間:** 20～25分

**短縮版:** 12～15分

## 2. デモ環境

| 項目 | 値 |
|---|---|
| Web UI | <https://ca-llmops-web-sypsod6k.kindpebble-ae273138.japaneast.azurecontainerapps.io/> |
| Resource group | `rg-llmops-validate` |
| Container App | `ca-llmops-web-sypsod6k` |
| Application Insights | `appi-llmops-sypsod6k` |
| Foundry Agent | `policy-agent` version 7 |
| Azure SRE Agent | `sre-llmops-validate-u6meqo` |
| Observability Agent | `obs-llmops-validate-u6meqo` |
| 使用データ | 合成した日本語規程と申請データのみ |

デモで使用する質問:

> 国内出張の精算期限と申請 REQ-2026-0042 の状況を教えてください。

## 3. 必ず守る運用ルール

- `simulation` と `live` を明示して実行し、両者を混同しません。
- `live` の失敗を `simulation` の成功結果で置き換えません。
- `LLMOPS_CAPTURE_CONTENT=false` を維持し、プロンプトや回答本文を監視ログへ記録しません。
- Azure SRE Agent は `ReadOnly` のまま使用し、書き込みコマンドや自動承認を使いません。
- Observability Agent の出力は推奨事項として扱い、環境変更は必ず人間が承認します。
- デモ中に `Microsoft.Monitor/settings/default`、RBAC、Alert、Agent、Container App の設定を変更しません。
- 接続文字列、Access Token、資格情報、個人情報を画面共有しません。

## 4. シナリオと期待結果

| Scenario | 注入する事象 | 画面で確認する結果 | 主な監視信号 |
|---|---|---|---|
| `healthy` | 現行規程、正常なツール | 30日以内、現行版 `2026-07-01` | 正常な `retrieve`、`execute_tool`、`invoke_agent` |
| `stale_policy` | 廃止済み規程を除外 | 30日以内、`current`、版 `2026-07-01` | superseded の `retrieve` がないこと |
| `slow_tool` | request-status を遅延 | 回答は成功するが Elapsed が増加 | 長い `execute_tool` span、p95悪化 |
| `tool_failure` | request-status を失敗 | 申請状況を取得できないことを明示 | `execute_tool` failure、`error.type` |

`tool_failure` のHTTP表現はモードで異なります。

- `simulation`: APIは構造化エラーとHTTP 502を返します。
- `live`: Hosted Agentがツール失敗を処理し、HTTP 200の回答内で「申請状況を取得できない」と明示する場合があります。HTTP成功だけで業務タスク成功と判断せず、ツールspanを確認します。

## 5. デモ開始15分前の準備

### 5.1 ローカル変数

```bash
export DEMO_URL="https://ca-llmops-web-sypsod6k.kindpebble-ae273138.japaneast.azurecontainerapps.io"
export RESOURCE_GROUP="rg-llmops-validate"
export WEB_APP="ca-llmops-web-sypsod6k"
```

### 5.2 公開エンドポイント

```bash
curl -fsS "$DEMO_URL/healthz"
curl -fsS "$DEMO_URL/readyz?mode=live"
curl -fsS "$DEMO_URL/api/operations/readiness?mode=live"
```

期待結果:

- すべてHTTP 200
- `/healthz` は `status=ok`
- 2つのreadinessは `status=ready`、`mode=live`

### 5.3 Container App

```bash
az containerapp show \
  --resource-group "$RESOURCE_GROUP" \
  --name "$WEB_APP" \
  --query '{
    latestRevision:properties.latestRevisionName,
    latestReadyRevision:properties.latestReadyRevisionName,
    provisioningState:properties.provisioningState,
    traffic:properties.configuration.ingress.traffic
  }' \
  -o json
```

確認項目:

- `latestRevision` と `latestReadyRevision` が一致
- `provisioningState=Succeeded`
- 最新Revisionへ100%のTraffic

### 5.4 ブラウザーとAzure Portal

1. Web UIを開きます。
2. Azure Portalへサインインします。
3. Application Insightsの **Logs** を開きます。
4. Azure Monitorの **Alerts** を別タブで開きます。
5. 次のKQLをすぐ実行できるタブに準備します。
   - `monitoring/kql/01-overview.kql`
   - `monitoring/kql/03-latency.kql`
   - `monitoring/kql/06-conversation-detail.kql`
   - `monitoring/kql/07-sre-investigation-evidence.kql`
   - `monitoring/kql/08-observability-alert-signals.kql`

### 5.5 ライブAlertを事前に用意する場合

`stale_policy` は現行規程のみを選択する回帰確認です。既にFiredの stale-policy Alert は人間が調査し、新たな live の stale-policy 実行で再発を作りません。

Alert名:

- `LLM Ops live stale policy retrieval`
- `LLM Ops live request-status tool failure`

Alert評価は5分間隔です。AlertがFiredになったことを確認してからデモを開始します。自動Issue生成はPreview機能のため、Issueが生成されない場合は後述のオンデマンド調査を使用します。

## 6. 標準デモ手順

### Step 1: 運用設計を説明する（1分）

最初に次のメッセージを伝えます。

> LLMアプリケーションでは、HTTPの成否だけでなく、参照したデータの版、ツール呼び出し、回答品質、レイテンシを同じTraceで追う必要があります。このデモでは、検知から原因調査、次アクション、再評価までを一つの運用ループとして確認します。

強調点:

- UI/APIとFoundry AgentをTraceで関連付ける
- 品質、可用性、性能を別々の信号として監視する
- 自動調査と自動変更を分離する

### Step 2: 正常時の基準を確認する（2分）

1. Web UI上部の **Runtime mode** を `simulation` にします。
2. **Scenario** を `healthy` にします。
3. 質問文が既定のデモ質問であることを確認します。
4. **Run scenario** を選択します。

画面で確認する項目:

- 回答: 精算期限は **30日以内**
- Sources: `国内出張旅費規程 / 2026-07-01 / current`
- Scenario: `healthy`
- Mode: `simulation`
- Elapsed
- Conversation ID
- Response ID
- Trace ID

説明:

> この結果を正常時の基準にします。相関IDは利用者から問い合わせを受けたときの調査キーで、同じ画面から会話、応答、分散Traceへ移動できます。

必要に応じて `monitoring/kql/01-overview.kql` を実行し、正常リクエストを表示します。

### Step 3: 現行規程の回帰を確認する（2分）

1. **Scenario** を `stale_policy` に変更します。
2. 同じ質問を送信します。
3. Conversation IDとTrace IDをコピーします。

期待結果:

- 回答: **30日以内**
- Source status: `current`
- Source version: `2026-07-01`
- HTTP処理自体は成功

`monitoring/kql/06-conversation-detail.kql` の先頭を更新します。

```kusto
let targetConversation = "<画面のConversation ID>";
```

確認項目:

- `retrieve_policy` が現行規程を選択
- `llmops.policy.status=current`
- モデル呼び出しは成功

説明:

> `stale_policy` は廃止済み規程を選択しないことを確認する回帰ケースです。既にFiredのAlertは人間が調査し、修正後の再発はAlertの対象外であることを確認します。

### Step 4: 遅延の原因をspanで特定する（2分）

1. **Scenario** を `slow_tool` に変更します。
2. 同じ質問を送信します。
3. `healthy` よりElapsedが増加したことを確認します。
4. `monitoring/kql/03-latency.kql` を実行します。

確認項目:

- `execute_tool request_status` が主要な遅延箇所
- モデル処理ではなく依存ツールがレイテンシ予算を消費
- エンドツーエンド時間と個別span時間の関係

説明:

> 利用者が感じるのは全体の遅さですが、修正対象を決めるにはモデル、検索、ツールを分けたspanが必要です。

### Step 5: ツール障害と相関IDを確認する（3分）

#### 5A. 決定的なエラー表示

1. `simulation` / `tool_failure` を選択します。
2. 同じ質問を送信します。

期待結果:

- HTTP 502
- `request_status_failure`
- Conversation ID、Response ID、Trace IDがエラー時も返る

#### 5B. 実Foundry Agentで信号を生成する

1. **Runtime mode** を `live` に変更します。
2. **Scenario** は `tool_failure` のまま送信します。
3. 表示されたTrace IDをコピーします。

期待結果:

- 回答本文で申請状況の取得失敗を明示
- 規程に基づく期限は回答
- 失敗した申請状況を推測しない
- Application Insightsに失敗した `execute_tool` spanを記録

`monitoring/kql/07-sre-investigation-evidence.kql` の先頭を更新します。

```kusto
let targetTraceId = "<画面のTrace ID>";
```

説明:

> liveではAgentがツール障害を処理してHTTP 200を返す場合があります。業務タスクの一部失敗を検知するため、HTTPだけでなくツールspanと回答状態を監視します。

### Step 6: Azure SRE Agentで証拠ベースの調査を行う（3～5分）

1. UI下部の **SRE / Observability investigation** へ移動します。
2. **Runtime mode** を `live` にします。
3. **Incident scenario** を `tool_failure` にします。
4. 直前のTrace IDを貼り付けます。
5. **Investigate and recommend** を選択します。

調査は通常1～3分かかります。

確認項目:

- SRE Agent evidence
- Ruled-out hypotheses
- Probable root cause
- Confidence
- Investigation ID
- SRE AgentがContainer Appの健全性を確認
- 書き込み操作や自動承認を実施していない

期待する調査内容:

- Application Insightsの対象Traceを確認
- `execute_tool` またはAgent内の依存処理に失敗・遅延が集中
- Container App Revisionは正常
- プラットフォーム停止、認証障害、リソース枯渇などを証拠に基づいて除外

CLIから同じ処理を実行する場合:

```bash
python scripts/run_operations_demo.py \
  --base-url "$DEMO_URL" \
  --mode live \
  --scenario tool_failure \
  --trace-id "<画面のTrace ID>" \
  --timeout 240
```

説明:

> SRE Agentは環境を変更せず、複数の監視信号から仮説を検証します。調査結果には推定原因だけでなく、除外した仮説とその根拠を残します。

### Step 7: Observability Agentでネクストアクションを定める（3～5分）

#### パターンA: Azure Monitor Issueが生成済み

画面の **Issue status** が `issue_ready` の場合:

1. Issue title、severity、summaryを確認します。
2. **Open Azure Monitor issue** を選択します。
3. Investigationの証拠と推奨ネクストアクションを確認します。
4. UIへ戻り、各アクションの次の項目を確認します。
   - Priority
   - Owner
   - Rationale
   - Risk
   - Validation
   - `Approval required`

#### パターンB: Issueが未生成

現在のPreview環境では、このパターンを想定します。

1. UIがHTTP 202相当の `awaiting_issue` を表示することを確認します。
2. `next_actions` が空で、推奨事項を捏造していないことを説明します。
3. **Open fired alerts and run Investigate** を選択します。
4. Azure Monitor Alertsで次のいずれかを開きます。
   - `LLM Ops live request-status tool failure`
   - `LLM Ops live stale policy retrieval`
5. Alertの **Investigate** を選択します。
6. **Start chat** を選択し、オンデマンドDeep Investigationを開始します。
7. 完了したレポートで次を確認します。
   - 影響範囲
   - 相関したログ、メトリック、Trace
   - 除外仮説
   - 推定原因
   - 推奨ネクストアクション

Issueとして保存する場合:

1. 保存先Azure Monitor workspaceを確認します。
2. Subscriptionの既定workspace変更を要求された場合は、その場で変更しません。
3. Workspace Ownerへ判断を依頼します。

説明:

> 自動Issueが未生成でも、実際のFired Alertから公式のオンデマンド調査を開始できます。どちらの経路でも、Observability Agentは環境を変更せず、最終判断と実行は人間が行います。

`monitoring/kql/08-observability-alert-signals.kql` を実行し、Alertの入力信号を確認します。

### Step 8: 修正前後の評価を比較する（3分）

```bash
python scripts/run_evaluation.py \
  --base-url "$DEMO_URL" \
  --mode simulation
```

出力:

```text
artifacts/evaluation-comparison.json
```

比較する指標:

- Success rate
- Policy version accuracy
- Answer accuracy
- Latency compliance
- Average latency
- p95 latency
- Token use

説明:

> インシデント対応は「直ったように見える」で完了させません。同じ固定ケースを再実行し、品質、可用性、性能への副作用がないことを確認します。

### Step 9: 正常状態を再確認する（2分）

1. UI上部へ戻ります。
2. `simulation` / `healthy` を選択します。
3. 最初と同じ質問を送信します。
4. 次を確認します。
   - 30日以内
   - `2026-07-01`
   - `current`
   - 正常なツール応答
5. `monitoring/kql/01-overview.kql` で正常なTraceを確認します。

最後に次を伝えます。

> 再現可能な障害注入、相関ID、分散Trace、SRE Agentの証拠ベース調査、Observability Agentの推奨、人間承認、固定評価を組み合わせることで、LLMアプリケーションを継続的に改善できる運用ループを構築できます。

## 7. 短縮版デモ

| 時間 | 実施内容 |
|---:|---|
| 2分 | `healthy` と `stale_policy` で現行規程を確認 |
| 2分 | `tool_failure` を実行してTrace IDを取得 |
| 3分 | SRE Agentのevidence、除外仮説、推定原因を表示 |
| 3分 | `awaiting_issue` からFired Alertの **Investigate** を開始 |
| 2分 | 修正前後評価と人間承認の考え方を説明 |

`slow_tool` と詳細KQLは質疑応答用に省略します。

## 8. トラブル時の分岐

| 事象 | 対応 |
|---|---|
| `/healthz` が失敗 | Container Appの最新Ready Revision、Probe、System Logを確認。開始しない |
| live readinessが503 | live設定不足として明示。自動でsimulationへ切り替えない |
| live Foundry呼び出しが失敗 | 構造化エラーとTrace IDを示す。必要なら「ここからは明示的なsimulation」と宣言して続行 |
| SRE Agentがtimeout/502 | live証拠を取得できなかったと説明。simulation結果をlive結果として見せない |
| SRE調査に古いTraceを指定 | 新しいliveシナリオを実行してTrace IDを取り直す |
| Alertが見つからない | `08-observability-alert-signals.kql` で信号を確認し、5分のAlert評価を待つ |
| Issueが未生成 | HTTP 202 `awaiting_issue` を正常な非同期状態として示し、Fired Alertの **Investigate** を使用 |
| **Investigate** が表示されない | Azure Copilot利用権限を確認。simulationで画面構成のみ示し、live成功とは説明しない |
| KQLにデータがない | 取込遅延を考慮して数分待ち、検索期間とTrace IDを確認 |
| Subscription既定workspaceの変更を要求された | デモ中は変更せず、Workspace Ownerへ判断を依頼 |

詳細な収集項目は `docs/troubleshooting-checklist.md` を使用します。

## 9. デモ後に保存する証跡

- デモ実施日時
- 実行モードとScenario
- Conversation ID
- Response ID
- Trace ID
- Web Revision
- SRE Investigation IDとThread ID
- Alert名と発火時刻
- Azure Monitor Issue ID（生成された場合）
- 評価結果 `artifacts/evaluation-comparison.json`
- 実施した人間承認と検証結果

資格情報、Access Token、接続文字列、プロンプト本文、回答本文は証跡へ保存しません。
