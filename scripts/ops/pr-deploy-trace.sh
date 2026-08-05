#!/usr/bin/env bash
# pr-deploy-trace.sh — 輸入 PR 編號，回答「這個 PR 現在跑在哪些環境」（唯讀）
#
# 用法：
#   pr-deploy-trace.sh 7970                 # 在 repo 目錄內跑，或用 --repo 指定
#   pr-deploy-trace.sh 7970 --repo sosreader/vocus-web-ui
#   pr-deploy-trace.sh 7970 --no-k8s        # 只查 git / GitHub，不打叢集
#   pr-deploy-trace.sh --sha 5170cbf1       # 直接用 commit sha 反查（不經 PR）
#
# 這支腳本全程唯讀，沒有 --apply。列在 ops/ 是因為它取代的是同一串反覆手打的查詢。
#
# 判斷順序（每層都可能跟上一層矛盾，不可跳層）：
#   1. PR merge commit 是什麼 —— merge 後 branch 常被刪，要用 merge commit 的 sha 不是 head sha
#   2. 那顆 sha 進了哪些 branch（`git branch -r --contains`）→ 決定「理論上該在哪些環境」
#   3. build workflow 有沒有真的跑完 → image 有沒有推上 registry
#   4. 叢集實際跑的 image tag 尾巴的 sha → 唯一能定案「真的在跑」的證據
#
# 為什麼不能停在第 3 層：deploy workflow success 只代表 image 推上 ECR。vocus-web-ui 的
# deploy-staging.yaml 自 2026-04-25 起已無 helm upgrade step（job 改名 notify，只發 Slack），
# 換版由 Flux ImageUpdateAutomation 掃 ECR 觸發，實測 workflow 完成到 pod 起來差 18 分鐘。
# 在那段窗口內回答「已部署」是錯的，而 workflow 的 conclusion 完全看不出來。
# 來源：KB wiki/learned/deployed-sha-verify-via-image-tag-not-workflow-green.md
#       KB wiki/learned/deployed-image-tag-vs-git-log-audit.md
#
# ！kubectl 預設 context 是 vocus-prod-3az（正式站）。本腳本所有 kubectl 呼叫一律明帶
#   --context，絕不依賴預設 context。同 account 下 stg/prod 極易混淆，曾差點對錯叢集下結論。
#   來源：memory feedback_kubectl_default_context_is_prod
#
# 退出碼：0 正常 / 1 參數錯 / 2 前置條件不足

set -euo pipefail

PR=""; SHA=""; REPO=""; DO_K8S=1

while [ $# -gt 0 ]; do
  case "$1" in
    --repo) REPO="${2:-}"; [ -n "$REPO" ] || { echo "--repo 需要 owner/name" >&2; exit 1; }; shift ;;
    --sha)  SHA="${2:-}";  [ -n "$SHA" ]  || { echo "--sha 需要 commit sha" >&2; exit 1; }; shift ;;
    --no-k8s) DO_K8S=0 ;;
    -h|--help) sed -n '2,/^set -euo/p' "$0" | sed 's/^#\{1,\} \{0,1\}//'; exit 0 ;;
    [0-9]*) PR="$1" ;;
    *) echo "未知參數：$1" >&2; exit 1 ;;
  esac
  shift
done

[ -n "$PR" ] || [ -n "$SHA" ] || { echo "請給 PR 編號或 --sha" >&2; exit 1; }
command -v git >/dev/null || { echo "需要 git" >&2; exit 2; }
command -v gh  >/dev/null || { echo "需要 gh CLI" >&2; exit 2; }

git rev-parse --git-dir >/dev/null 2>&1 || { echo "請在 git repo 內執行" >&2; exit 2; }
if [ -z "$REPO" ]; then
  REPO=$(gh repo view --json nameWithOwner --jq .nameWithOwner 2>/dev/null) || REPO=""
fi
[ -n "$REPO" ] || { echo "解析不出 repo，請用 --repo owner/name" >&2; exit 2; }

echo "repo: ${REPO}"

# ── 第 1 層：PR → merge commit ────────────────────────────────────────────────────
if [ -n "$PR" ]; then
  meta=$(gh pr view "$PR" --repo "$REPO" \
         --json number,title,state,baseRefName,headRefName,headRefOid,mergeCommit,mergedAt \
         --jq '[.state, .baseRefName, .headRefName, (.mergeCommit.oid // "-"), (.mergedAt // "-"), .title] | @tsv') \
    || { echo "查不到 PR ${PR}" >&2; exit 2; }
  IFS=$'\t' read -r state base head merge_oid merged_at title <<EOF
$meta
EOF
  echo "PR #${PR}  ${state}  ${head} -> ${base}"
  echo "標題: ${title}"
  echo "merged: ${merged_at}   merge commit: ${merge_oid}"
  if [ "$state" != "MERGED" ]; then
    echo
    echo "PR 尚未 merge，沒有部署可追。"
    exit 0
  fi
  # 用 merge commit 而非 head sha：image tag 裡的 sha 來自 GITHUB_SHA，
  # push 到部署分支時那是 merge commit，不是 feature branch 的 tip。
  SHA="$merge_oid"
fi

SHORT=$(printf '%s' "$SHA" | cut -c1-8)
echo
echo "追蹤 commit: ${SHA} (短 ${SHORT})"

# ── 第 2 層：這顆 sha 進了哪些 branch ─────────────────────────────────────────────
echo
echo "── 分支涵蓋（先 fetch 再判斷，stale ref 會給出相反答案）"
git fetch --quiet --prune origin || echo "  ! fetch 失敗，以下判斷可能用到 stale ref" >&2
if git cat-file -e "${SHA}^{commit}" 2>/dev/null; then
  branches=$(git branch -r --contains "$SHA" 2>/dev/null | sed 's/^[ *]*//' | grep -v '\->' || true)
  if [ -n "$branches" ]; then
    printf '%s\n' "$branches" | sed 's/^/  /'
  else
    echo "  （不在任何 remote branch 上 —— 可能是 squash merge，SHA 已被改寫）"
  fi
else
  echo "  本地沒有這顆 commit（squash merge 或尚未 fetch 到）"
fi

# ── 第 3 層：build / deploy workflow ──────────────────────────────────────────────
echo
echo "── 對應的 workflow run（success 只代表 image 推上 registry，不代表叢集換版）"
gh run list --repo "$REPO" --commit "$SHA" --limit 10 \
   --json name,status,conclusion,createdAt,databaseId \
   --jq '.[] | "  \(if (.conclusion // "") == "" then .status else .conclusion end)\t\(.createdAt)\t\(.name)\t\(.databaseId)"' \
  2>/dev/null || echo "  （查不到 run）"

# ── 第 4 層：叢集實際在跑什麼 ─────────────────────────────────────────────────────
# 以下環境對照表 live 驗於 2026-08-05（來源：vocus-web-ui .github/workflows/ 檔頭與
# helm/frontend/values-*.yaml，以及 KB vocus-web-ui-staging-host-to-branch-mapping.md）。
# image tag 格式：<yyyymmddHHMM>-<runNumber>-<sha 前 8 碼>，三段都由 deploy workflow 組出。
#
#   分支          workflow                  ECR repo                    context         deployment
#   develop       deploy-staging.yaml       vocus-web-ui-staging        vocus-stg       frontend-v1
#   develop-v2~v4 deploy-staging.yaml       vocus-web-ui-staging-v2~v4  vocus-stg       frontend-v2~v4
#   hotfix        deploy-hotfix-k8s.yaml    vocus-web-ui-hotfix         vocus-prod-3az  frontend-hotfix
#   master        build-production.yaml     vocus-web-ui-production     vocus-prod-3az  frontend-prod*
#
# master 那條不是自動部署：build 只推 :latest 到 ECR，經 EventBridge rule 觸發 CodePipeline
# vocus-web-ui-production-ecs-cluster(V2)，Deploy stage 卡手動核准 approve-prod-eks-deploy。
# 所以「master 已 merge + image 已 build」與「prod 已換版」中間隔著一個人。
# CodePipeline 的執行狀態本腳本沒查（需要 AWS_PROFILE，且 rollback 執行的 source revision
# 有陷阱）—— TODO：要接的話用 `aws codepipeline list-pipeline-executions`，判讀方式見
# KB wiki/learned/aws-codepipeline-v2-stage-rollback-source-revision.md。
K8S_TARGETS="vocus-stg:vocus vocus-prod-3az:vocus"

if [ "$DO_K8S" -eq 1 ] && command -v kubectl >/dev/null 2>&1; then
  echo
  echo "── 叢集實際跑的 image（唯一能定案的證據）"
  for target in $K8S_TARGETS; do
    ctx="${target%%:*}"; ns="${target##*:}"
    # 一律明帶 --context，永不依賴 current-context（預設是 prod）。
    if ! kubectl --context "$ctx" -n "$ns" get deploy >/dev/null 2>&1; then
      echo "  [${ctx}] 連不上或無權限，略過"
      continue
    fi
    out=$(kubectl --context "$ctx" -n "$ns" get deploy \
          -o 'custom-columns=NAME:.metadata.name,IMAGE:.spec.template.spec.containers[0].image,READY:.status.readyReplicas' \
          --no-headers 2>/dev/null | grep -i 'frontend' || true)
    if [ -z "$out" ]; then
      echo "  [${ctx}] 找不到 frontend deployment"
      continue
    fi
    printf '%s\n' "$out" | while IFS= read -r row; do
      name=$(printf '%s' "$row" | awk '{print $1}')
      image=$(printf '%s' "$row" | awk '{print $2}')
      ready=$(printf '%s' "$row" | awk '{print $3}')
      mark="  "
      case "$image" in *"$SHORT"*) mark="=>" ;; esac
      printf '  %s [%s] %-22s ready=%-4s %s\n' "$mark" "$ctx" "$name" "$ready" "$image"
    done
  done
  echo
  echo "  => 標記代表該 deployment 的 image tag 尾巴含 ${SHORT}，也就是這個 PR 真的在跑。"
  echo "  沒有 => 而 workflow 是綠的，代表還在 build 完成到叢集換版之間的窗口（或卡人工核准）。"
  echo "  pod 是否真的重啟過要另外看：kubectl --context <ctx> -n <ns> get pods -l ... -o wide"
elif [ "$DO_K8S" -eq 1 ]; then
  echo
  echo "── 略過叢集查詢（找不到 kubectl）"
fi

echo
echo "備註：以上叢集資訊反映查詢當下狀態；rolling update 進行中時新舊 pod 會並存。"
