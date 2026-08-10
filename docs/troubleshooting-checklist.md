# トラブルシューティング情報収集チェックリスト

収集対象は、合成データまたはマスキング済みデータに限定します。資格情報、トークン、Application Insights の接続文字列、本番環境のプロンプト、個人情報をインシデント記録へ貼り付けないでください。

## リクエストの識別情報

- [ ] UTC と現地時刻のタイムスタンプ
- [ ] 環境名と実行モード（`simulation` または `live`）
- [ ] シナリオ
- [ ] Conversation ID
- [ ] Response ID
- [ ] Trace ID / Application Insights の `operation_Id`
- [ ] ホステッドエージェントの名前とバージョン
- [ ] Web リビジョンとコンテナーイメージのダイジェスト

## 事象

- [ ] 期待する動作
- [ ] 実際の動作
- [ ] HTTP ステータスと構造化エラーコード
- [ ] 問題の分類（品質、遅延、可用性、トークン使用量、認証）
- [ ] 再現回数と発生頻度

## トレースの証跡

- [ ] 受信リクエストの処理時間と成否
- [ ] `invoke_agent` の処理時間、モデル、Response ID、トークン数
- [ ] `retrieve_policy` が選択した文書 ID、版、状態、結果件数
- [ ] `execute_tool` のツール名、処理時間、成否、`error.type`
- [ ] 例外の種類と機密情報を除去したメッセージ
- [ ] 親子スパンの構造
- [ ] メッセージ本文の記録が有効だったか

## Foundry ライブモード

- [ ] `FOUNDRY_PROJECT_ENDPOINT` が対象プロジェクトを指している
- [ ] `FOUNDRY_AGENT_NAME` と任意のバージョン指定がデプロイ内容と一致している
- [ ] `AZURE_AI_MODEL_DEPLOYMENT_NAME` が `azure.yaml` と一致している
- [ ] ローカル実行では開発者資格情報で認証している
- [ ] Azure 上の実行ではマネージド ID を使用している
- [ ] Web の ID にプロジェクトスコープの Foundry User ロールが付与されている
- [ ] エージェントの ID がモデルとツールへアクセスできる
- [ ] パブリック／プライベートネットワーク設定が呼び出し元のネットワークと整合している
- [ ] Application Insights が接続済みで、直近のテレメトリが取り込まれている

## Container Apps

- [ ] リビジョンのプロビジョニング状態とレプリカ数
- [ ] `/healthz` の結果
- [ ] `/readyz?mode=live` の結果
- [ ] イメージ取得、プローブ、スケーリング失敗に関するシステムログ
- [ ] リクエスト発生時刻前後のコンソールログ
- [ ] ACR のプルロールとレジストリ ID の設定
- [ ] スケールゼロからのコールドスタートによる影響

## Azure SRE Agent

- [ ] `SRE_AGENT_NAME` と `SRE_AGENT_ENDPOINT` が対象リソースと一致している
- [ ] Agent の action mode が `ReadOnly` である
- [ ] SRE Agent managed identity に Reader、Monitoring Reader、Log Analytics Reader が付与されている
- [ ] Web managed identity に SRE Agent Standard User が付与されている
- [ ] 調査 thread ID と investigation ID
- [ ] 調査対象の Trace ID、時間範囲、影響リソース
- [ ] 根拠として使用した KQL、メトリック、Activity Log、Resource Graph
- [ ] 除外した仮説と除外根拠
- [ ] 推定原因と confidence
- [ ] 書き込みコマンドや自動承認を使用していない

## Azure Copilot Observability Agent

- [ ] Observability Agent resource と Azure Monitor workspace が同じリージョンにある
- [ ] Application Insights が monitored resource として有効になっている
- [ ] Issue Creation と Investigation の mode
- [ ] Observability Agent managed identity に、Alert と対象リソースを含む subscription scope の Monitoring Reader が付与されている
- [ ] Observability Agent managed identity に、Issue 保存先 Azure Monitor workspace scope の Issue Contributor が付与されている
- [ ] subscription の `Microsoft.Monitor/settings/default` が既存の Azure Monitor workspace を指しているか確認し、別ワークロードの関連付けを無断で上書きしていない
- [ ] `Microsoft.Monitor/settings/default` の参照先resource groupとworkspaceが現存する
- [ ] `monitoringAccountId` と Issue API の照会先 Azure Monitor workspace が一致している
- [ ] 対象の scheduled query alert が Fired で、Application Insights と同じ subscription にある
- [ ] custom instructions に秘密情報、顧客データ、一時的なインシデント情報が含まれていない
- [ ] Azure Monitor Issue ID、title、severity、status
- [ ] 推奨アクションの priority、owner、rationale、risk、validation
- [ ] すべての環境変更が人間承認待ちになっている
- [ ] autonomous deep investigation の課金が確認済み
- [ ] `awaiting_issue` の場合は、生成待ちとして扱い成功結果を捏造していない

Issue が生成されない場合は、Alert、managed identity、monitored resource、Issue APIの照会先、workspace 関連付けの順に確認します。自動生成を待てない場合は、Fired Alertの **Investigate** からオンデマンドDeep Investigationを開始します。subscription の既定 Azure Monitor workspace を変更すると他ワークロードへ影響するため、必ず所有者の承認を得てから変更してください。

## 評価の証跡

- [ ] データセットのバージョンまたは Git コミット
- [ ] 修正前後のシナリオ対応表
- [ ] 評価器の名前とバージョン
- [ ] 集計結果と失敗したケース ID
- [ ] 失敗ケースの Response ID と Conversation ID
- [ ] LLM Judge の制約または期待動作ルーブリックの変更点

## 初動調査の手順

1. `monitoring/kql/01-overview.kql` で全体状況を確認します。
2. `02-failures.kql` で失敗を抽出します。
3. 遅いトレースは `03-latency.kql` で調査します。
4. `06-conversation-detail.kql` で対象の会話を開きます。
5. `05-evaluation-correlation.kql` で評価イベントと応答を関連付けます。
6. `07-sre-investigation-evidence.kql` でSRE調査の証拠を再現します。
7. `08-observability-alert-signals.kql` でIssue相関対象の信号を確認します。
8. ライブ環境の設定を変更する前に、同じケースをシミュレーションモードで再現します。
