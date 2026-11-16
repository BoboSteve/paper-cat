# 逐脚本执行顺序（run.py 内部做的事）

> 速览模式只有两步：`quick.py search`（arXiv + OpenAlex，按概念组覆盖度生成初筛名单）→ Agent 写 `picks.json` → `quick.py render`。下面是深度模式。
>
> 深度模式新增的环节：brief 的 `concepts` 在 init 时生成一条概念检索（arXiv 字段检索 + OpenAlex 布尔检索，均带时间窗）；`known_papers` 在 search 后给出召回自检；rank 前候选超过 `prefilter_cap`（默认 400）时先按关键词粗筛；没有本地模型时用关键词排序（`pool.lexical_scores`）；`run.py rerank` 应用 Agent 的二次排序；fetch 后 `reading.py plan` 分批；finalize 前自动 `reading.py merge`；之后可选 `snowball.py find/add` 与 `clue_import.py --suggest/--attach`。

平时用 `scripts/run.py` 的五个阶段即可。需要单独重跑某一步、做路径 A、或排查问题时，按下面的顺序手动调用。所有命令在任务目录中运行，`$S` 指 skill 的 `scripts/` 目录，`python` 指工具环境的解释器。

## 1. 计划与预检（run.py init）

```bash
python $S/preflight.py --require tools --allow-degraded --out preflight.json
python $S/plan.py --brief brief.json --queries queries.jsonl --path B --tier quick --out plan.json
```

`plan.py` 要求 brief 含 topic / topic_description / time_window / inclusion / exclusion；`--selected-k` 缺省按档位（quick 20 / standard 50 / deep 100），`--track-policy` 缺省 main_only。

## 2. 召回（run.py search）

```bash
python $S/psm_search.py --queries queries.jsonl --out-dir . --progress-task .
python $S/seed_import.py --input refs.bib --out seeds.jsonl
python $S/citation_expand.py --seeds seeds.jsonl --direction both --max-per-seed 20 --out citation_candidates.jsonl
python $S/clue_import.py --input clues/note.json --out-clues xhs_clues.jsonl --out-seeds xhs_seeds.jsonl --resolve
```

路径 A：`openreview_enumerate.py`、`enumerate.py`（DBLP）、`official_enumerate.py`（ACL/PMLR）、`official_html.py`（NeurIPS/CVF/ECVA/USENIX/AAAI/IJCAI 主会页面），并用 `path_a_audit.py` 对账。官方目录写 `official/*.summary.json`。

## 3. 合并、补摘要、时间窗

```bash
python $S/merge_candidates.py --input psm=psm_candidates.jsonl --input user_seed=seeds.jsonl \
  --input citation=citation_candidates.jsonl --input xhs_clue=xhs_seeds.jsonl \
  --out candidates.jsonl --report merge_report.json
python $S/metadata_repair.py --input candidates.jsonl --out candidates_metadata.jsonl \
  --report metadata_report.json --only-fields abstract
python $S/catalog_match.py --candidate candidates_metadata.jsonl --catalog acl=official/acl.jsonl \
  --out candidates_official.jsonl --report catalog_match_report.json      # 仅当有官方目录
python $S/scope_filter.py --input candidates_metadata.jsonl --out candidates_scoped.jsonl \
  --start 2025-01-01 --end 2026-11-15 --drop-out --report scope_report.json
```

`scope_filter.py` 把日期当区间（日/月/年精度）；跨越窗口边界的记录标 `boundary` 保留，不会被猜测成某一天。近重复只在 `merge_report.json` 中提示，经复核后写 `version_groups.json` 再用 `apply_version_groups.py` 归并。

## 4. 排序、载体与等级（run.py rank）

```bash
python $S/jev_service.py start
python $S/jev_rank.py --candidates candidates_scoped.jsonl --plan plan.json --out jev_scored.jsonl --progress-task .
python $S/jev_service.py stop
python $S/venue_resolve.py --input jev_scored.jsonl --out jev_venue.jsonl --report venue_report.json
python $S/rank.py --candidates jev_venue.jsonl --plan plan.json --out ranked.jsonl
python $S/calibrate_k.py sample --ranked ranked.jsonl --k 20 --out-dir calibration
```

`venue_resolve.py` 恢复正式发表信息：ACL Anthology ID（含轨道）→ OpenAlex 正式载体与 ISSN → arXiv comment/journal-ref（仅"声称"）。`rank.py` 只查表；强轨道证据（ACL ID、官方目录、OpenReview）不会被卷名启发式覆盖；按 `track_policy` 排除的记录排到最后，不占 Top K。

## 5. 全文（run.py fetch）

```bash
python $S/download.py --ranked ranked.jsonl --plan plan.json --out-dir pdfs --skip-existing
python $S/fulltext_verify.py --task . --progress-task .
python $S/judgment_sheet.py --ranked ranked.jsonl --plan plan.json --out-csv judgment_sheet.csv --out-md judgment_sheet.md
```

`download.py` 逐渠道尝试并把每次尝试写入 `pdfs/manifest.json`；全文按页写成 `===== PAGE n =====` 标记的 UTF-8 文本（PyMuPDF 优先，poppler 备选，不用 `-layout`）；无文字层的扫描件标 `no_text_layer`。判断表的 `read_scope` 留空，读完才填。

## 6. 判断、报告与检查（run.py finalize）

```bash
python $S/missing_fulltext_report.py --sheet judgment_sheet.csv --out-md missing_fulltext.md --out-csv missing_fulltext.csv
python $S/judgment_import.py --input judgment_sheet.csv --ranked ranked.jsonl --plan plan.json \
  --judged-by "Claude Code (model)" --out judgments.jsonl --progress-task .
python $S/report.py --task . --out report.html
python $S/regression_check.py --task .
```

`judgment_import.py` 默认 `--quote-check strict`：引句在全文中找不到即拒收并给出最接近的页码；`warn` 只标警告。`regression_check.py` 按路径调整检查项，状态为 pass / warn / skip / fail，只有 fail 使运行失败；"没有核心论文"是 warn 而不是 fail。
